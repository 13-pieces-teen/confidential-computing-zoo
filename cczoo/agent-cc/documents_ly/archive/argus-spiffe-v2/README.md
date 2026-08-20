# Argus-SPIFFE v2 历史方案归档

本目录保存已经完成历史使命、但仍可用于追溯实现与远程验证过程的方案和报告。
这些文件不再定义当前默认架构、威胁模型或后续实施优先级。

当前主方案：

- [双 TDVM + OpenViking Broker Sidecar 架构](../../Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
- [documents_ly 文档索引](../../README.md)

归档内容：

| 文件 | 归档原因 |
| --- | --- |
| `Argus-SPIFFE-v2-Execution-Plan.md` | 早期双Agent与Mock阶段执行基线，里程碑已经推进 |
| `Argus-SPIFFE-v2-Execution-Status.md` | 早期完成度快照，不代表当前状态 |
| `Argus-SPIFFE-v2-Pre-RA-Hardening-Plan.md` | hostile-caller增强防护计划，不再作为默认Argus核心门槛 |
| `Argus-SPIFFE-v2-Pre-RA-Hardening-Remote-Verification-Report.md` | 对应旧Pre-RA计划的历史远程验证证据 |
| `Argus-SPIFFE-v2-Evaluation-and-Capacity-Plan.md` | 基于代理链路的旧评测合同，需按direct Profile重写后才能重新启用 |

归档文件仍保留历史结论和证据边界。引用它们时应注明对应提交、Profile、Mock/Real
状态和验证日期，不得用历史 PASS 替代当前 Broker Sidecar Profile 的远程验收。
