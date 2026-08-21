# Argus 双 TDVM + OpenViking Broker Sidecar 实施与验证计划

> 对应架构：[双 TDVM + OpenViking Broker Sidecar 架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
>
> 状态：代码实施与本地静态验证完成；统一 Profile 的远程双 TDVM 验收待执行

## 1. 实施目标

本次实施只做一件事：把现有双 TDVM 部署骨架与已经实现的 OpenViking Broker
Sidecar 链路合并到 `core/spire/runtime/dual-tdvm`。

不重新设计 Broker，不恢复 OpenViking Python 直接获取 SVID，也不增加新的安全边界。

## 2. 实施结果

| 范围 | 实施前 | 当前结果 |
|---|---|---|
| 双 TDVM | 两个 `argus_tdx` Agent、独立 Parent 和直接 workload SVID 已有骨架 | 保留两个独立 TDVM 和 Agent |
| OpenClaw | 使用自己 TDVM 的 Workload API 和 SPIFFE mTLS | 保持现状 |
| OpenViking | 直接挂载 Workload API | TC-API 启动原生服务；无 SPIRE mount |
| Broker | 只在 asymmetric Profile 实现 | 已接入 OpenViking TDVM，引用 TC-API 返回 PID |
| Registration | 两个普通 Entry | OpenClaw、Broker、OpenViking target 三个强 Entry |
| 验证 | 两条软件链分别验证 | 已提供统一 ALLOW/DENY、错误客户端、pidfd 与跨 TDVM mTLS 脚本；远程待执行 |

## 3. 实施步骤

### W1. 拆分两端 Agent 配置

- OpenClaw Agent 保留当前 Docker WorkloadAttestor；
- OpenViking Agent 增加 Broker Endpoint 和只允许
  `WorkloadPIDReference` 的配置；
- OpenViking Agent 加载外部 `argus_tdx_workload`；
- OpenViking TDVM 的 Evidence Provider 同时提供 Node 和 workload Mock evidence；
- Agent 保持 root，Sidecar 保持 `1000:1000`；Broker 目录使用
  `1000:1000/2770`，Agent 创建的 UDS 验证为 `root:1000/0770`。

### W2. 构建与部署 Broker 组件

- 在 dual-tdvm prepare 阶段构建 WorkloadAttestor 和 Broker Sidecar；
- SPIRE Server 与两个 Agent 统一到 `1.15.2`；
- NodeAttestor、WorkloadAttestor 构建前自动下载只读模块依赖；
- 把 WorkloadAttestor 二进制和校验值写入 OpenViking Agent 配置；
- 把 Broker Sidecar 镜像传入 OpenViking TDVM；
- 不向 OpenViking Python 镜像重新加入 materializer、TLS wrapper 或 SPIRE SDK。

### W3. 收敛 Registration Entry

- OpenClaw Entry：绑定 OpenClaw TDVM Agent Parent 和现有强 Docker selectors；
- Broker Entry：绑定 OpenViking TDVM Agent Parent、Broker label/image/digest；
- OpenViking target Entry：绑定同一 OpenViking Parent、OpenViking
  label/image/digest 和 `argus_tdx_workload` 可信 selectors；
- target Entry 禁用 X.509-SVID prefetch；
- 不保留相同 OpenViking SPIFFE ID 的弱 Entry。

### W4. 修改 OpenViking TDVM 启动流程

- 复用 OpenViking TDVM 既有 TC-API 和 Registry，不由该 Profile 重建；
- launch-only 非交互启动必须显式提供 identity token 或 bearer token；
- 从启动结果解析唯一 container ID 和实际宿主机 PID；
- OpenViking 只监听回环 HTTP 1933；
- Sidecar 使用 `--pid host` 和 pidfd 引用该 PID；
- Sidecar 挂载 Broker 自身 Workload API socket 和 Broker API socket；
- Sidecar 在 1943 终止 mTLS，转发到 OpenViking 回环地址。

### W5. 修改跨 TDVM 调用

- `DUAL_OPENVIKING_HOST_ADDRESS` 指向 OpenViking TDVM 可达地址；
- 1943 对应 Broker Sidecar，不再是 Python ASGI TLS listener；
- OpenClaw 保持 caller-local Guard ALLOW 后直接发起 mTLS；
- OpenViking 明文 1933 不对 OpenClaw TDVM 开放。

### W6. 更新验证脚本

验证脚本至少检查：

1. 两个独立 Agent 和 Parent ID；
2. OpenClaw SVID 正常；
3. OpenViking 容器无 SPIRE socket；
4. Sidecar 的目标 PID 与 OpenViking 实际 PID 一致；
5. ALLOW 后才出现 mTLS listener ready；
6. DENY 时 Trustee metric 记录拒绝，Sidecar 无目标 SVID、无 ready、不监听 1943，
   并保持无身份等待状态；
7. OpenClaw 到 Sidecar 的跨 TDVM mTLS 成功；
8. 无证书、错误客户端 ID 和明文 1933 访问失败；
9. OpenViking 退出后 Sidecar 退出且 1943 关闭；
10. `/health=200` 是安全链路硬验收；`/ready` 只记录 Application Readiness，
    本 profile 未部署 Ollama/bge-m3 时允许明确记录为 `503 / NOT READY`。

## 4. 验证顺序

### 4.1 本机验证

- WorkloadAttestor：`go test ./...`、`go vet ./...`；
- Broker Sidecar：`go test ./...`、`go vet ./...`；
- NodeAttestor Mock workload endpoints 的定向测试；
- Linux amd64 交叉编译；
- 修改脚本 `bash -n`；
- dual-tdvm Compose 和 SPIRE 配置渲染检查；
- 所有 Registration Entry 的静态 selector 审计。

M3 隔离测试默认使用 `39988/39989`，避免与 dual-tdvm 的 `29988` 冲突；
prepare 会在测试证书缺失或不足 24 小时有效期时重建整套证书。

### 4.2 远程软件链验证

先运行 Broker 隔离 ALLOW/DENY，确认组件本身未回归；再部署两个 TDVM 的统一
Profile，验证完整 A-F 时序。

远程结果必须分别记录：

- 两个 TDVM/Agent 的身份和 Parent；
- Mock Evidence Provider/Trustee 请求计数；
- Broker 自身身份与 OpenViking 目标身份；
- PID/container 关联；
- source、TC-API runtime、运行容器与 Registration Entry 的 image config digest；
- mTLS 正向和负向结果；
- OpenViking/Sidecar 退出行为。

当前允许使用 `DUAL_OPENVIKING_IMAGE_CONFIG_DIGEST` 传入实测 runtime digest。
后续工程项是让 Registration Entry 直接基于 Attestor 实际观察到的 runtime
measurement，而不是未经 TC-API 转换的 source artifact measurement。

## 5. 完成定义

只有以下条件全部满足，才能把最新方案标记为“远程软件链已通过”：

- `runtime/dual-tdvm` 不再给 OpenViking Python 挂载 Workload API；
- OpenViking 目标 SVID 只通过 Broker PID-reference 获得；
- ALLOW、DENY、进程退出和跨 TDVM mTLS 均在同一 Profile 通过；
- 旧的 direct OpenViking SVID Entry 不存在；
- 结果明确标记 Mock Evidence Provider + Mock Trustee。

真实 Quote/QGS、TC-API/Rekor 证据和生产 Trustee 替换不属于本轮完成条件。

## 6. 主要复用路径

| 路径 | 用途 |
|---|---|
| `core/spire/runtime/dual-tdvm` | 最新 Profile 的部署入口 |
| `core/spire/runtime/asymmetric/config/openviking-agent.conf.tmpl` | Broker Endpoint 与外部 WorkloadAttestor 配置参考 |
| `core/spire/runtime/asymmetric/scripts/register-workloads.sh` | Broker/target Entry 参考 |
| `adapters/OpenViking/scripts/launch_openviking.sh` | TC-API、container ID、PID 和 Sidecar 启动参考 |
| `adapters/OpenViking/broker_sidecar` | Broker SVID、目标订阅、mTLS、pidfd |
| `core/spire/plugins/argus-tdx-workloadattestor` | workload evidence、Trustee 和 selectors |
| `core/spire/tests/nodeattestor-mock` | Broker ALLOW/DENY 隔离测试 |

## 7. 不在本轮新增

- 第三个 Gateway 或 service mesh；
- OpenViking Python SPIFFE SDK；
- 每请求 Quote、正文哈希或 TLS exporter 绑定；
- 新的回退 Profile；
- 为满足本轮 `/ready` 观察项而部署 Ollama/bge-m3；
- 对已失陷 TDVM、Docker 管理员或 SPIRE 管理员的额外防护。
