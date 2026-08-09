# Argus-SPIFFE v2 执行完成度报告

> **归档状态（2026-08-07）**：本文是早期完成度快照，不代表当前默认架构或当前验收状态。
> 当前主方案见
> [Argus-SPIFFE-v2-Threat-Model-Realignment-Plan.md](../../Argus-SPIFFE-v2-Threat-Model-Realignment-Plan.md)。

## 1. 报告信息

- 报告日期：2026-07-29
- 实施分支：`dev/openviking`
- 实施规格：`documents_ly/Argus-SPIFFE-v2-Implementation.md`
- 验证范围：Argus TDX NodeAttestor v2、SPIRE 1.15.1 集成、软件故障矩阵、TD VM 内真实 OpenViking、OpenClaw 业务链路与回滚

## 2. 总体结论

Argus-SPIFFE v2 的协议、Agent 插件、Server 插件和无硬件集成已经完成。真实 OpenClaw 到真实 OpenViking 的业务链路已在 TD VM 部署形态下通过，remote attestation 在当前 v2 架构验收中使用 mock Evidence Provider 与 mock Trustee，replay、HTTP 503、timeout 和 metrics 分类均已验证。

当前版本可以认定为“v2 软件架构与 TD VM 业务部署验证完成”，不能认定为“TDX 硬件安全验收完成”或“生产远程认证上线”。Host 尚未提供 QGS 及 QEMU QGS socket 连接，真实 Quote、v2 REPORTDATA 独立验证和 production Trustee 仍是阻塞项。

## 3. 里程碑完成度

状态定义：

- **完成**：实现和对应验收均已通过；
- **部分完成**：已有可运行实现和子项验证，但未满足该里程碑全部规范性门槛；
- **未开始**：未进入本轮交付。

| 里程碑 | 状态 | 已完成内容 | 剩余内容 |
| --- | --- | --- | --- |
| M0：协议冻结 | 完成 | deterministic protobuf、RFC 8785/JCS、SHA-384 REPORTDATA、Ed25519 transcript、Agent ID 与 selector 合同 | 无 |
| M1：Agent 插件 | 完成 | initial payload、challenge、Evidence Provider 调用、证明密钥持久化、transcript 签名、错误与 evidence bytes metrics | 无 |
| M2：Server 插件 | 完成 | challenge、Trustee adapter、claims 校验、AgentAttributes、稳定失败分类与 metrics | 无 |
| M3：无硬件联调 | 完成 | SPIRE 1.15.1 + fake Provider/Trustee、SVID 签发、selector 组合矩阵、负向测试与独立测试数据目录 | 无 |
| M4：TDX 验收 | 部分完成 | 软件故障矩阵、metrics、TD Guest 检查、真实 OpenViking TD VM 部署、mock attestation 架构 profile | QGS、真实 Quote、严格 v2 Evidence Provider、独立 production Trustee 与硬件安全矩阵 |
| M5：切换与回滚 | 部分完成 | OpenClaw `2933 -> 1934 -> 2933` 业务端点回滚、Guest 状态回滚、可重复部署脚本 | SPIRE 身份平面 canary、正式切换、观察窗口、旧 SVID deny/失效收敛 |
| M6：生命周期增强 | 未开始 | 无 | `can_reattest=true`、周期触发、eviction 收敛 |

## 4. 已执行操作

### 4.1 协议与插件实现

1. 固化 Argus TDX NodeAttestor v2 wire protocol 和密码学绑定规则。
2. 实现 Agent 侧 challenge-response、Evidence Provider 访问、证明密钥和 transcript 签名。
3. 实现 Server 侧 nonce challenge、Trustee 验证适配、claims/policy 校验和 AgentAttributes 输出。
4. 为 Agent 与 Server 接入 SPIRE Metrics host service，记录 attempts、duration、evidence bytes 和稳定失败原因。
5. 扩展 fake services，支持 evidence replay、Provider 503、Trustee 503 与 Trustee delay 注入。

主要实现目录：

- `core/spire/plugins/argus-tdx-nodeattestor/`
- `core/spire/m3/`

### 4.2 M4 软件故障与可观测性验证

执行 `core/spire/m4/test-failures.sh`，覆盖：

- 首次 fresh attestation 成功；
- 新 Agent key/challenge 收到旧 evidence 时 replay fail-closed；
- Evidence Provider HTTP 503 fail-closed；
- Trustee HTTP 503 fail-closed；
- Trustee 超时 fail-closed；
- Prometheus 正确分类 replay、HTTP 503 和 timeout。

最终结果：软件故障矩阵通过，基准 Agent 数为 0，fresh control 后为 1；Server 记录 1 次成功和 4 次拒绝。

### 4.3 TDX Host 与 Guest 验证

1. 在 TDX Host 检查 KVM TDX、QEMU `tdx-guest` 与 TDVF。
2. 启动 Ubuntu TD VM，Guest 内核报告 `tdx: Guest detected`。
3. 在 Guest 内确认 `/dev/tdx_guest`、`tdx_guest` module 与 TSM configfs report root。
4. 新增 `core/tdx-quote/src/bin/tdx-quote-smoke.rs`，用于以 48-byte SHA-384 digest 触发生产 TSM Quote backend。
5. Quote smoke 到达 Host 后因缺少 QGS/QEMU QGS socket 连接触发 `KVM: unknown exit reason 40`，据此将 QGS 明确记录为硬件验收阻塞项。

### 4.4 真实 OpenViking TD VM 部署

1. 将真实 OpenViking v0.4.8 镜像离线导入 TD VM。
2. 迁移完整 OpenViking persistent state，保留 User API Key 与系统数据。
3. 状态复制期间短暂 pause Host 源容器，通过只读 helper 获取一致快照，并设置 EXIT trap 确保失败时自动 unpause。
4. 仅删除复制状态中的 `data/.openviking.pid`，避免 stale PID 导致 data lock。
5. 在 Guest 使用 host network 启动 OpenViking，并配置 `unless-stopped`。
6. 通过 QEMU loopback forward 建立 `127.0.0.1:2933 -> TD VM:1933`，不向外部网卡暴露 API。
7. 验证 `/health`、`/ready` 和带 OpenViking User API Key 的 sessions 请求。

新增运维脚本：

- `core/spire/m4/tdvm.sh`：overlay 准备、TD VM 启动、状态和停止；
- `core/spire/m4/deploy-openviking-tdvm.sh`：Docker bootstrap、镜像/状态部署、状态备份与 Guest 回滚；
- `core/spire/m4/switch-openclaw-openviking.sh`：OpenClaw 目标切换和认证验证；
- `core/spire/m4/test-architecture.sh`：真实业务路径与 mock v2 的组合验收。

### 4.5 回滚演练

完成两类回滚：

1. **业务端点回滚**：OpenClaw 从 TD VM forward `2933` 切到保留的 Host OpenViking `1934`，认证 sessions 返回 HTTP 200；随后切回 `2933`，再次返回 HTTP 200。
2. **Guest 状态回滚**：将 TD VM OpenViking 恢复到最近 `.backup-*` 状态，保留被替换状态为 `.replaced-*`，健康检查和认证业务请求通过；之后重新部署 Host 当前状态作为最终运行态。

切换脚本以 OpenClaw `node` 用户修改持久配置，复用现有 User API Key，不输出 key，并为每次切换保留 JSON 配置备份。

## 5. 验证结果

| 验证项 | 结果 |
| --- | --- |
| Agent/Server plugin contract tests | 通过 |
| fake services handler tests | 通过 |
| telemetry recorder tests | 通过 |
| M3 SPIRE 1.15.1 集成与 selector 负向矩阵 | 通过 |
| M4 replay/503/timeout/metrics 软件矩阵 | 通过 |
| TDX Guest device/module/TSM preflight | 通过 |
| `tdx-quote-smoke` binary tests | 通过，3 个测试 |
| `cargo test --locked` 全量测试 | 被既有 `tests/integration.rs` 缺失阻断；该 Cargo 配置来自初始提交，不是本轮回归 |
| TD VM OpenViking `/health` 与 `/ready` | HTTP 200 |
| OpenClaw 到 TD VM OpenViking sessions | HTTP 200 |
| Host `1934` 回滚目标 | 健康且认证请求通过 |
| Guest 状态 rollback/redeploy | 通过 |
| M4 shell scripts `bash -n` | 通过 |
| `git diff --check` | 通过 |
| 真实 Quote + QGS + production Trustee | 未通过，deferred |

最终组合验收命令：

```bash
TDVM_SSH_IDENTITY=/path/to/tdvm-key core/spire/m4/test-architecture.sh
```

最终组合验收输出确认：

- 真实 OpenClaw 到 `http://127.0.0.1:2933` 的认证请求为 HTTP 200；
- TD Guest placement 检查通过；
- mock Evidence Provider/Trustee attestation path 通过；
- replay、Provider 503、Trustee 503、Trustee timeout 和 metrics 分类通过；
- Real Quote/QGS 状态为 `DEFERRED`。

## 6. 最终运行态

截至本报告日期：

- TD VM 正在运行；
- TD VM OpenViking 正在运行，restart policy 为 `unless-stopped`；
- OpenClaw 最终目标为 `http://127.0.0.1:2933`；
- OpenClaw API key 已配置但未输出到日志或报告；
- Host OpenViking `http://127.0.0.1:1934` 保持健康，作为业务回滚目标；
- Host 源 OpenViking 容器未处于 paused 状态；
- 原 Host 服务、TD VM overlay 和历史状态备份均未删除。

## 7. 未完成项与风险

### 7.1 M4 硬件安全验收

以下项目完成前，不得宣称真实 TDX remote attestation 已通过：

1. 在 Host 安装并启动 QGS；
2. 将 QEMU `tdx-guest` 的 `quote-generation-socket` 连接到 QGS；
3. 实现严格的 `POST /ra/v1/evidence` v2 Evidence Provider；
4. 实现独立验证 Quote、REPORTDATA、policy、measurement 和 TCB 的 production Trustee；
5. 重跑真实 Quote 正向、tamper、replay、TCB/policy 拒绝和 metrics 矩阵。

### 7.2 M5 正式切换

当前只完成业务端点和 Guest 状态回滚，尚需：

1. 制定 SPIRE 身份平面 canary 范围；
2. 验证正式 registration entries 与 Guard allow/deny；
3. 执行观察窗口；
4. 验证旧 v2 SVID 到期、deny 和 eviction 收敛；
5. 完成正式身份平面回滚演练。

## 8. 后续建议顺序

1. 配置 QGS 和 QEMU QGS socket，先让 `tdx-quote-smoke` 成功返回真实 Quote；
2. 完成严格 v2 Evidence Provider 与 production Trustee；
3. 执行规范性 M4 硬件安全矩阵；
4. 进入 M5 SPIRE 身份平面 canary、切换、观察和回滚；
5. M4/M5 完成后再评估 M6 re-attestation 生命周期增强。
