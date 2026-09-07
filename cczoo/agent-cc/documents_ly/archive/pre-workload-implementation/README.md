# Workload 首轮实现之前的设计归档

归档日期：2026-09-06；来源提交：`9f493f8`。

这些文档此前仍位于顶层，但其 Node-only 限制或独立 Go Broker 数据面已被当前 **SPIRE v1.15.3 + WorkloadAttestor + Broker-aware Helper + NGINX** 实现替代。保留原决策和验证结果，不再跟随当前代码更新。

| 文档 | 归档原因 |
|---|---|
| [Node 真实取证与 Trustee 重构计划](./Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md) | 混有早期 Node 里程碑、环境快照和 Workload 尚未实现的限制；配套旧图一并放入本目录 assets |
| [双 TDVM Broker 架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md) | Egress/Ingress Go Broker 已不定义当前身份与 mTLS 路径 |
| [双 TDVM Broker 实施计划](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md) | 旧代码接线和执行入口 |
| [双 TDVM Broker 远程验证报告](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md) | SPIRE v1.15.2、Mock Provider/Trustee 的历史软件链证据，不能作为当前真实 Workload 验收 |
| [OpenViking Broker Sidecar 详细方案](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md) | 旧 PID reference 与内存 TLS 代理设计，已由 Helper/NGINX 实现替代 |

当前说明见 [Node](../../Argus-TDX-Node-Attestation-CN.md)、[Workload](../../Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)、[运行手册](../../../core/spire/workload/README.md) 和 [验证记录](../../../core/spire/workload/VALIDATION.md)。
