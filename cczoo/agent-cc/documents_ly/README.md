# documents_ly 文档索引

本目录记录 Argus、SPIFFE/SPIRE、TDX、OpenClaw 和 OpenViking 的架构决策、
实施方案、会议纪要与评估证据。

## 当前结论

当前唯一实施主线是 Broker Sidecar：

1. OpenViking Python 源码保持不变，不调用 Workload API，也不持有 SVID；
2. Broker Sidecar 使用实际 OpenViking 宿主机 PID 提交 WorkloadPIDReference；
3. 自定义 WorkloadAttestor 调用 Mock Evidence Provider 和 Mock Trustee；
4. ALLOW 后返回可信 selectors，SPIRE 匹配静态 Registration Entry 并签发目标 SVID；
5. Sidecar 在内存中使用目标 SVID，为 OpenClaw 提供 mTLS 入口。

当前代码、本机单元测试和静态检查已经完成。Broker Sidecar 在远程 Linux/TDVM
上的 ALLOW、DENY、PID namespace、pidfd 和完整 mTLS 链路仍待验证。现有 2026-08-10
和 2026-08-11 的远程报告属于此前的 Python TLS/materializer Profile，不能作为当前
Broker Sidecar 的远程验收结果。

## 推荐阅读顺序

1. [当前架构](./Argus-Asymmetric-Attestation-SPIFFE-Architecture.md)
2. [当前实施与远程验证方案](./Argus-Asymmetric-Attestation-SPIFFE-Implementation-Plan.md)
3. [Broker Sidecar 详细设计记录](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)
4. [当前评估方案](./Argus-Asymmetric-Attestation-SPIFFE-Evaluation-Plan.md)

需要了解决策背景时，再阅读会议纪要和历史架构逻辑。历史运行报告只用于追溯对应
提交、Profile 和测试结果。

## 一、当前主线

| 文档 | 定位 | 当前状态 |
|---|---|---|
| [Argus-Asymmetric-Attestation-SPIFFE-Architecture.md](./Argus-Asymmetric-Attestation-SPIFFE-Architecture.md) | 当前架构事实源，说明边界、组件和 A-F 时序 | Broker Sidecar 已实现；远程验证待执行 |
| [Argus-Asymmetric-Attestation-SPIFFE-Implementation-Plan.md](./Argus-Asymmetric-Attestation-SPIFFE-Implementation-Plan.md) | 当前代码路径、部署顺序和验收命令 | 本机验证完成；远程验证待执行 |
| [OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md) | Broker API 选择、PID 引用、状态机和取舍的详细设计记录 | 设计已落地；实施状态以当前架构和实施文档为准 |
| [Argus-Asymmetric-Attestation-SPIFFE-Evaluation-Plan.md](./Argus-Asymmetric-Attestation-SPIFFE-Evaluation-Plan.md) | Broker Sidecar 功能验收后的性能与容量评估合同 | 评估适配待完成 |
| [Argus-TDX-OpenViking-Custom-Workload-Attestation-Design.md](./Argus-TDX-OpenViking-Custom-Workload-Attestation-Design.md) | 兼容旧文件名的架构入口 | 指向当前主线 |
| [Argus-TDX-OpenViking-Custom-Workload-Attestation-Implementation.md](./Argus-TDX-OpenViking-Custom-Workload-Attestation-Implementation.md) | 兼容旧文件名的实施入口 | 指向当前主线 |

## 二、会议与决策背景

| 文档 | 定位 | 使用方式 |
|---|---|---|
| [Custom-Workload-Attestor-TC-API-Meeting-Notes-CN.md](./Custom-Workload-Attestor-TC-API-Meeting-Notes-CN.md) | 原始语音转写整理后的会议纪要 | 用于追溯问题、争议和未冻结事项 |
| [OpenViking-Workload-Attestation-Architecture-Workflow-CN.md](./OpenViking-Workload-Attestation-Architecture-Workflow-CN.md) | Broker 决策前的概念架构与触发顺序 | 保留整体逻辑；其中“Python 直接请求身份”已被 Broker Sidecar 取代 |

## 三、备选架构

| 文档 | 定位 | 当前关系 |
|---|---|---|
| [Argus-Dual-TDVM-SPIFFE-Design.md](./Argus-Dual-TDVM-SPIFFE-Design.md) | OpenClaw 与 OpenViking 分别进入独立 TDVM 的备选架构 | 不属于当前 Broker Sidecar 主线，也不作为回退路径 |

## 四、历史远程验证与性能证据

以下文件绑定到各自记录的分支、提交、日期和旧运行 Profile。它们仍是有效历史证据，
但不验证当前 Broker Sidecar 代码。

| 文档 | 对应内容 |
|---|---|
| [Argus-Asymmetric-Attestation-SPIFFE-Remote-Validation-Report.md](./Argus-Asymmetric-Attestation-SPIFFE-Remote-Validation-Report.md) | 2026-08-10 旧 Python ASGI TLS/materializer Profile 的远程功能验证 |
| [Argus-Asymmetric-Attestation-SPIFFE-Benchmark-Report.md](./Argus-Asymmetric-Attestation-SPIFFE-Benchmark-Report.md) | 旧 Profile 的 E3-E7 性能报告 |
| [Argus-Asymmetric-Attestation-SPIFFE-Evidence-Manifest.md](./Argus-Asymmetric-Attestation-SPIFFE-Evidence-Manifest.md) | 上述 E3-E7 原始证据路径与校验摘要 |
| [Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Plan.md](./Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Plan.md) | 旧 Profile 的 E8 评估合同 |
| [Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Report.md](./Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Report.md) | 2026-08-11 E8 汇总快照 |
| [run-20260811T114949Z](./Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Report-run-20260811T114949Z.md) | E8 单次运行记录 |
| [run-20260811T145408Z](./Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Report-run-20260811T145408Z.md) | E8 TD Guest 恢复后的运行记录 |
| [run-20260812T012615Z](./Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Report-run-20260812T012615Z.md) | E8 本地归档 VLM 对照运行记录 |

当前 Broker Sidecar 远程验证完成后，应新增独立报告，避免覆盖以上历史报告。

## 五、归档目录

- [archive/pre-asymmetric-architecture](./archive/pre-asymmetric-architecture/README.md)：
  当前非对称架构形成前的设计、实现和验证材料。
- [archive/argus-spiffe-v2](./archive/argus-spiffe-v2/README.md)：
  更早的 v2 执行、Pre-RA 强化和容量计划。

归档文件不定义当前架构，也不能用其中的 PASS 替代当前代码的验收。

## 文档维护规则

1. 架构变化先更新“当前架构”，代码和命令变化再更新“实施方案”。
2. 计划只定义方法和完成条件，不写成已经完成的证据。
3. 报告必须保留分支、提交、Profile、日期以及 Mock/Real 边界。
4. Registration Entry 是静态匹配规则；验证成功由 WorkloadAttestor 返回 selector，
   不能表述为 Entry 中的 verified 状态被动态改写。
5. Mock Evidence Provider/Trustee 的通过只证明软件链路，不能升级为真实 Quote、
   QGS、TC-API/Rekor 或生产 Trustee 结论。
