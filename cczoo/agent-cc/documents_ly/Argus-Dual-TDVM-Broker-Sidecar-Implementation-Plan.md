# Argus 双 TDVM + OpenViking Broker Sidecar 实施与验证计划

> 对应架构：[双 TDVM + OpenViking Broker Sidecar 架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
>
> 状态：待实施；本文不表示统一 Profile 已经运行或远程验收

## 1. 实施目标

本次实施只做一件事：把现有双 TDVM 部署骨架与已经实现的 OpenViking Broker
Sidecar 链路合并到 `core/spire/runtime/dual-tdvm`。

不重新设计 Broker，不恢复 OpenViking Python 直接获取 SVID，也不增加新的安全边界。

## 2. 当前代码差距

| 范围 | 当前代码 | 目标 |
|---|---|---|
| 双 TDVM | 两个 `argus_tdx` Agent、独立 Parent 和直接 workload SVID 已有骨架 | 保留两个独立 TDVM 和 Agent |
| OpenClaw | 使用自己 TDVM 的 Workload API 和 SPIFFE mTLS | 保持现状 |
| OpenViking | `runtime/dual-tdvm` 仍把 Workload API 直接挂给 OpenViking | 移除 OpenViking SPIRE mount |
| Broker | Broker Sidecar 和 `argus_tdx_workload` 已在 asymmetric Profile 实现 | 接入 OpenViking TDVM |
| Registration | 双 TDVM 当前只有 OpenClaw/OpenViking 两个普通 Entry | 改为 OpenClaw、Broker、OpenViking target 三个 Entry |
| 验证 | 双 TDVM 骨架与 Broker 软件链分别验证 | 新增统一 ALLOW/DENY 与跨 TDVM mTLS 验证 |

## 3. 实施步骤

### W1. 拆分两端 Agent 配置

- OpenClaw Agent 保留当前 Docker WorkloadAttestor；
- OpenViking Agent 增加 Broker Endpoint 和只允许
  `WorkloadPIDReference` 的配置；
- OpenViking Agent 加载外部 `argus_tdx_workload`；
- OpenViking TDVM 的 Evidence Provider 同时提供 Node 和 workload Mock evidence。

### W2. 构建与部署 Broker 组件

- 在 dual-tdvm prepare 阶段构建 WorkloadAttestor 和 Broker Sidecar；
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

- 使用 TC-API 启动原生 OpenViking；
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
6. DENY 时 Trustee、Broker 和无目标 SVID 三项同时成立；
7. OpenClaw 到 Sidecar 的跨 TDVM mTLS 成功；
8. 无证书、错误客户端 ID 和明文 1933 访问失败；
9. OpenViking 退出后 Sidecar 退出。

## 4. 验证顺序

### 4.1 本机验证

- WorkloadAttestor：`go test ./...`、`go vet ./...`；
- Broker Sidecar：`go test ./...`、`go vet ./...`；
- NodeAttestor Mock workload endpoints 的定向测试；
- Linux amd64 交叉编译；
- 修改脚本 `bash -n`；
- dual-tdvm Compose 和 SPIRE 配置渲染检查；
- 所有 Registration Entry 的静态 selector 审计。

### 4.2 远程软件链验证

先运行 Broker 隔离 ALLOW/DENY，确认组件本身未回归；再部署两个 TDVM 的统一
Profile，验证完整 A-F 时序。

远程结果必须分别记录：

- 两个 TDVM/Agent 的身份和 Parent；
- Mock Evidence Provider/Trustee 请求计数；
- Broker 自身身份与 OpenViking 目标身份；
- PID/container 关联；
- mTLS 正向和负向结果；
- OpenViking/Sidecar 退出行为。

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
- 对已失陷 TDVM、Docker 管理员或 SPIRE 管理员的额外防护。
