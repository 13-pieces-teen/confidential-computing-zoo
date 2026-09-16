# 历史文档归档

本目录只保存设计演进和对应版本的验证证据，不定义当前架构、部署步骤或待办。文中的“当前”“下一步”和 PASS 均属于原记录日期与提交。

当前入口：[文档索引](../README.md)、[Node Attestation](../Argus-TDX-Node-Attestation-CN.md)、[Workload Attestation](../Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)。部署与验收以 [运行手册](../../core/spire/workload/README.md) 和 [验证记录](../../core/spire/workload/VALIDATION.md) 为准。

| 归档 | 内容 |
|---|---|
| [OpenClaw 客户端首阶段计划](./openclaw-client-stage1/Argus-IP1-OpenClaw-IP2-OpenViking-Implementation-Plan-CN.md) | 2026-09-09 的 IP1 → IP2 首阶段设计与交付快照；2026-09-16 归档，部署步骤由现行客户端手册维护，不再进入交付包 |
| [pre-workload-implementation](./pre-workload-implementation/README.md) | 本次移入的 Node 重构计划及旧图、双 Broker 架构/实施/远程报告、OpenViking Broker Sidecar 详细方案 |
| [pre-dual-tdvm-broker-sidecar](./pre-dual-tdvm-broker-sidecar/README.md) | 更早的非对称方案、Python 直连 Workload API、会议记录与性能报告 |
| [pre-asymmetric-architecture](./pre-asymmetric-architecture/README.md) | 早期 SPIFFE 集成设计与验证材料 |
| [argus-spiffe-v2](./argus-spiffe-v2/README.md) | 早期 v2 执行、Pre-RA 和容量计划 |

引用历史测试时必须带上原提交、版本、Profile、环境、日期与 Mock/Real 边界。已删除的 runtime 源码路径只保留为历史标识，不作为可执行入口。
