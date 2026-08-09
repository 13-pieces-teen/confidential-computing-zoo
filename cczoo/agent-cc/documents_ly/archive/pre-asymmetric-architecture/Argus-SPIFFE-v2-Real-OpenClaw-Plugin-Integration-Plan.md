# Argus-SPIFFE v2 真实 OpenClaw 插件接入计划

## 1. 目标

本轮将真实 OpenClaw Gateway 产生的 OpenViking context-engine 请求导入已经
跑通的 SPIFFE mTLS 通道，形成以下业务路径：

```text
OpenClaw Gateway
  -> OpenViking OpenClaw plugin
  -> restricted HTTP egress
  -> OpenClaw SPIFFE mTLS client
  -> OpenViking SPIFFE mTLS server in TDVM
  -> real OpenViking HTTP API on 127.0.0.1:1933
```

完成后，验收不再只请求 `/health`，而是通过真实 OpenClaw agent turn 触发：

1. `assemble()` 在回复前从 OpenViking 检索上下文；
2. `afterTurn()` 在回复后将 user/assistant 消息追加到 OpenViking session；
3. 显式 commit 将 session 归档，并触发后续记忆处理。

## 2. 本轮边界

### 2.1 纳入本轮

- 真实 OpenClaw 容器到 mTLS client proxy 的受限网络入口；
- OpenViking 插件 `baseUrl` 切换到该入口；
- 业务路径、方法、状态码和 request ID 的无敏感信息日志；
- 从真实 OpenClaw agent turn 到 OpenViking session capture 的远程验收脚本；
- commit 和 archive 处理的远程验收；
- SPIFFE mTLS 现有正向、明文、无客户端 SVID 和错误服务端 ID 验收兼容。

### 2.2 不纳入本轮

- 真实 Quote/QGS；
- production Evidence Provider 或 production Trustee；
- Envoy 或完整服务网格；
- Guard 与业务请求的不可绕过同请求门控；
- 将 Workload API socket 暴露给真实 OpenClaw 进程；
- 让 OpenClaw 插件直接处理 X.509-SVID；
- M5 正式身份切换验收；
- 对 OpenViking 的记忆抽取质量做模型效果承诺。

Argus Guard 继续运行真实进程并显式使用 `mock_allow`，但本轮不把它描述为已经
内联到每个业务请求。

## 3. 当前缺口

当前 `openclaw-mtls-client`：

- 使用 host network；
- 监听宿主机 `127.0.0.1:1934`；
- 通过宿主机 `127.0.0.1:1943` 到达 TDVM；
- 持有 `spiffe://argus.local/agent/openclaw` SVID。

真实 OpenClaw 运行在普通 Docker network 中。OpenClaw 容器内的
`127.0.0.1` 指向自身，无法访问宿主机 loopback 上的 1934。

不能只把 mTLS client 改成共享 OpenClaw network namespace。这样做后，当前
上游 `https://127.0.0.1:1943` 也会指向 OpenClaw namespace，不再是宿主机
到 TDVM 的转发端口。

## 4. 本轮网络设计

本轮保留 mTLS client 的 host network，并新增专用 Docker bridge：

```text
network: argus-openclaw-egress
subnet: 172.31.44.0/28
gateway / proxy listen: 172.31.44.1:1934
real OpenClaw: 172.31.44.2
```

所有地址必须可通过环境变量覆盖，默认值只用于单机远程验收。

mTLS proxy 增加 `-allow-source-ip`：

- 只允许真实 OpenClaw 的固定 bridge IP；
- 其他容器即使能够路由到 1934，也收到 HTTP 403；
- proxy 继续通过 host loopback 访问 TDVM 1943；
- SPIFFE Workload API 只挂载给 mTLS proxy，不挂载给 OpenClaw。

该方案提供当前阶段的软件隔离边界，但不替代后续 Envoy、网络策略或同请求
Guard 强制门控。OpenClaw Gateway 当前还持有用于 sandbox 的 Docker socket，
因此 source IP 白名单主要防止普通兄弟 workload 借用身份，不构成对已经控制
Docker daemon 的攻击者的安全边界。

## 5. 实施模块

### 5.1 专用 egress network

修改 `core/spire/v2/start-openclaw-workload.sh`：

1. 检查真实 OpenClaw 容器存在且正在运行；
2. 创建或验证专用 bridge network；
3. 将真实 OpenClaw 以固定 IP 接入 network；
4. 校验现有 network 的 driver、subnet、gateway 和容器 IP；
5. 导出 proxy bind address 和 allowed source IP；
6. 启动 mTLS client workload；
7. 验证 mTLS client 已取得预期 SVID。

不得静默接受同名但 subnet/gateway 不同的 Docker network。

### 5.2 mTLS client proxy

修改 `core/spire/v2/mtls-smoke/main.go`：

- 增加可选 `-allow-source-ip`；
- 对不匹配的来源 fail closed，返回 403；
- 复用或生成 `X-Argus-Request-ID`；
- 记录 method、path、status、duration、source IP 和 request ID；
- 不记录 API key、Authorization、cookie、请求正文或 SVID 私钥；
- 上游 mTLS 失败继续返回 502；
- 精确服务端 SPIFFE ID 校验保持不变。

### 5.3 Compose 配置

修改 `core/spire/v2/compose.center.yaml`：

```text
-listen=${V2_OPENCLAW_PROXY_BIND}:${V2_OPENCLAW_PROXY_PORT}
-allow-source-ip=${V2_OPENCLAW_EGRESS_IP}
```

默认 bind 为专用 bridge gateway，不再监听 host loopback。

### 5.4 插件配置切换

增加 `core/spire/v2/connect-openclaw-plugin.sh`，统一：

- 校验专用 network 和 mTLS proxy 已经就绪；
- 调用仓库现有 OpenViking 插件安装/配置入口；
- 将插件设置为 remote mode；
- 将 `baseUrl` 设置为专用 gateway 上的 1934；
- 保留非 root OpenViking user API key；
- 重启 OpenClaw Gateway；
- 检查 `plugins.slots.contextEngine=openviking`；
- 检查配置中的 `baseUrl` 与本轮目标一致。

同时修正 `adapters/OpenClaw/scripts/connect_openclaw_openviking.sh`：health 和
readiness 必须从真实 OpenClaw 容器内部检查，不能由宿主机代替插件检查网络。

### 5.5 现有 mTLS 验收调整

修改 `core/spire/v2/verify-mtls.sh`：

- 通过真实 OpenClaw 容器访问 proxy `/health`；
- 保留从 mTLS workload 内执行错误服务端 SPIFFE ID 的负向 probe；
- 保留宿主机对 TDVM 1943 的明文和无客户端 SVID 负向检查；
- 在负向检查后重新从真实 OpenClaw 请求 `/health`。

### 5.6 真实消息 E2E

增加 `core/spire/v2/verify-openclaw-plugin-e2e.sh`。

默认流程：

1. 检查真实 OpenClaw 容器、插件 slot、remote mode 和 `baseUrl`；
2. 通过 `openclaw agent` 和唯一 session key 发起真实 agent turn；
3. 消息包含本次运行唯一 marker；
4. 等待插件 `afterTurn()` 完成 capture；
5. 通过插件配置的 `baseUrl`，经 mTLS proxy 查询 OpenViking sessions；
6. 扫描 session context，定位包含 marker 的 session；
7. 对该 session 发起 commit；
8. 轮询 context，确认 `commit_count > 0` 且出现 archive overview；
9. 输出 marker、OpenClaw session key、OpenViking session ID 和 proxy 日志提示。

记忆抽取依赖 OpenViking 的 LLM/embedding 配置，默认只要求 capture 和 archive。
通过显式环境变量可以把 `memories_extracted > 0` 升级为强制验收项。

脚本不自动删除真实用户数据，也不尝试模糊匹配后删除 memory。

## 6. 配置变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `V2_REAL_OPENCLAW_CONTAINER` | `agentcc-openclaw-sbx-gateway` | 真实 OpenClaw Gateway 容器 |
| `V2_OPENCLAW_EGRESS_NETWORK` | `argus-openclaw-egress` | 专用 bridge network |
| `V2_OPENCLAW_EGRESS_SUBNET` | `172.31.44.0/28` | 专用 subnet |
| `V2_OPENCLAW_PROXY_BIND` | `172.31.44.1` | bridge gateway 与 proxy bind |
| `V2_OPENCLAW_EGRESS_IP` | `172.31.44.2` | 真实 OpenClaw 固定 IP |
| `V2_OPENCLAW_PROXY_PORT` | `1934` | OpenClaw 插件 HTTP 入口 |
| `OPENVIKING_API_KEY` | 无 | 非 root OpenViking user key |
| `V2_E2E_CAPTURE_ATTEMPTS` | `30` | session capture 轮询次数 |
| `V2_E2E_CAPTURE_INTERVAL` | `2` | capture 轮询间隔秒数 |
| `V2_E2E_SESSION_SCAN_LIMIT` | `100` | 每轮扫描的最近 session 数量 |
| `V2_E2E_COMMIT_ATTEMPTS` | `60` | commit/archive 轮询次数 |
| `V2_E2E_COMMIT_INTERVAL` | `5` | commit 轮询间隔秒数 |
| `V2_E2E_REQUIRE_MEMORY` | `0` | 是否强制要求记忆抽取结果 |

## 7. 远程执行顺序

远程主机在已有双 Agent v2 正向环境上执行：

```bash
export TDVM_SSH_IDENTITY=/root/.ssh/id_rsa
export OPENVIKING_API_KEY='<non-root-user-key>'

core/spire/v2/start-openclaw-workload.sh
core/spire/v2/start-openviking-workload.sh
bash core/spire/v2/connect-openclaw-plugin.sh
core/spire/v2/verify-architecture.sh
bash core/spire/v2/verify-openclaw-plugin-e2e.sh
```

如果远程 OpenClaw 已经安装了预期插件版本，设置
`OPENCLAW_INSTALL_PLUGIN=0`，只更新 remote endpoint。

如果默认 subnet 与远程主机冲突，必须在启动前覆盖整组 network 参数，不能只
修改 OpenClaw IP。

## 8. 远程验收标准

### 8.1 必须通过

- 真实 OpenClaw 容器连接专用 egress network；
- mTLS proxy 只接受配置的 OpenClaw source IP；
- 插件 slot 为 `openviking`，且 `baseUrl` 指向 1934；
- 真实 OpenClaw agent turn 成功；
- OpenViking session context 中出现唯一 marker；
- commit 完成且生成 archive overview；
- proxy 日志出现对应业务路径和 request ID；
- mTLS 双向身份与精确 peer ID 检查继续通过；
- 明文、无客户端 SVID、错误服务端 ID 继续被拒绝。

### 8.2 负向验收

- 普通兄弟容器访问 1934 返回 403 或网络不可达；
- 停止 mTLS proxy 后，marker 不得写入 OpenViking；
- OpenClaw workload entry 无效时，业务请求不得到达 OpenViking；
- 错误服务端 SPIFFE ID 时，业务请求失败；
- 恢复正确 runtime 后，新 marker 能再次完成 capture 和 archive。

OpenViking 插件部分路径可能采取 best-effort 策略。负向测试应断言消息没有进入
OpenViking，而不是断言 OpenClaw 整体无法生成回复。

## 9. 本地代码建设约束

本次在当前电脑只完成：

- 文档与代码修改；
- shell 语法检查；
- Go 格式检查；
- `git diff --check`；
- 人工逻辑审查。

不在当前电脑启动 Docker、SPIRE、OpenClaw、OpenViking 或 TDVM，不运行远程
架构测试，也不把静态检查结果描述为运行态通过。

## 10. 完成定义

仓库代码完成后，本轮状态应描述为：

> 真实 OpenClaw 插件接入代码已完成，能够将 agent turn 的 context-engine
> HTTP 请求导入受 source-IP 限制的 SPIFFE mTLS client，并提供 session capture
> 与 commit/archive 的远程验收脚本；仍待远程 Linux/TDX 主机运行验证。

只有远程验收全部通过后，才能描述为：

> 真实 OpenClaw agent turn 已通过 SPIFFE mTLS 到达 TDVM 内 OpenViking，并完成
> session capture 与 archive 处理。
