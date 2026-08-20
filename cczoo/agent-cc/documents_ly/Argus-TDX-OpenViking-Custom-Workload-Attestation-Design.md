# Argus TDX OpenViking Workload Attestation 架构入口

> 状态：已收敛为 Broker Sidecar 主方案

OpenViking Python 不直接调用 SPIRE Workload API，不持有 SVID 或私钥，也不挂载
SPIRE socket。Broker Sidecar 使用 OpenViking 的实际宿主机 PID，通过 SPIFFE Broker
API 请求目标身份；SPIRE Agent 调用自定义 WorkloadAttestor，并在 Mock Evidence
Provider 和 Mock Trustee 返回 ALLOW 后匹配静态 Registration Entry。

当前架构与完整 A-F 时序见：

- [Argus 非对称 Attestation-backed SPIFFE 架构](./Argus-Asymmetric-Attestation-SPIFFE-Architecture.md)
- [早期架构逻辑与时序（历史背景）](./OpenViking-Workload-Attestation-Architecture-Workflow-CN.md)
- [Broker Sidecar 详细方案](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)

TDVM Gateway 只作为详细方案中的后续备注，不属于当前实现范围。旧的 OpenViking
Python 直连 Workload API 方案不再保留为设计或回退路径。
