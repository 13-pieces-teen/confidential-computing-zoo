# 双 TDVM + Broker Sidecar 组合方案之前的文档归档

归档日期：2026-08-20。

本目录保存当前“OpenClaw TDVM + OpenViking TDVM + OpenViking Broker Sidecar”
组合方案形成前的架构、实施计划、会议记录和运行证据。

当前方案请参阅：

- [双 TDVM + OpenViking Broker Sidecar 架构](../../Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
- [实施与验证计划](../../Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md)
- [文档总索引](../../README.md)

## 归档内容

### 1. 旧非对称 Broker Profile

- `Argus-Asymmetric-Attestation-SPIFFE-Architecture.md`
- `Argus-Asymmetric-Attestation-SPIFFE-Implementation-Plan.md`
- `Argus-Asymmetric-Attestation-SPIFFE-Evaluation-Plan.md`

该方案只有 OpenViking 位于 TDX/TDVM 证明侧，不能代表当前双方均在独立 TDVM
中的架构。

### 2. 旧双 TDVM 直连方案

- `Argus-Dual-TDVM-SPIFFE-Design.md`

该版本让 OpenViking 直接挂载 Workload API，并通过 materializer/Python TLS 路径
取得和使用 SVID；已被 OpenViking Broker Sidecar 取代。

### 3. 早期 Workload Attestation 讨论

- `Custom-Workload-Attestor-TC-API-Meeting-Notes-CN.md`
- `OpenViking-Workload-Attestation-Architecture-Workflow-CN.md`
- `Argus-TDX-OpenViking-Custom-Workload-Attestation-Design.md`
- `Argus-TDX-OpenViking-Custom-Workload-Attestation-Implementation.md`

这些材料保留了从 Python 直接获取 SVID 到 Broker Sidecar 的决策过程。

### 4. 历史远程验证和性能证据

- `Argus-Asymmetric-Attestation-SPIFFE-Remote-Validation-Report.md`
- `Argus-Asymmetric-Attestation-SPIFFE-Benchmark-Report.md`
- `Argus-Asymmetric-Attestation-SPIFFE-Evidence-Manifest.md`
- `Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Plan.md`
- `Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Report.md`
- 三份带 run 时间戳的 E8 报告

这些报告绑定到各自记录的旧提交、日期和 Python TLS/materializer Profile。报告中的
PASS 仍是有效历史证据，但不能用作当前双 TDVM Broker Profile 的验收结果。

## 使用规则

1. 引用归档报告时必须同时注明提交、Profile、日期和 Mock/Real 边界；
2. 归档中的 direct OpenViking Workload API、materializer 和 Python TLS 路径不再是
   当前方案或回退方案；
3. 归档文件不再随当前代码同步更新。
