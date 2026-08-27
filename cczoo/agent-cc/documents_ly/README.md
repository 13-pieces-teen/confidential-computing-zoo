# documents_ly 文档索引

本目录顶层只保留当前方案。此前的架构、会议记录、评估计划和运行报告均放在
[archive](./archive/) 下。

## 当前方案

当前唯一主方案是：

> OpenClaw 与 OpenViking 分别运行在独立 TDVM 中；两个 TDVM 各自完成 Node
> Attestation。OpenClaw 和 OpenViking 均保持源码无侵入；OpenClaw 插件调用本地
> Egress Broker，后者执行 Guard 授权并代表真实 OpenClaw PID 发起 mTLS；OpenViking
> Ingress Broker 代表真实 Python PID 终止 mTLS。

当前 Evidence Provider 和 Trustee 均为 Mock。

`runtime/dual-tdvm` 已完成双 Broker Endpoint、两个 PID reference、四个 Registration
Entry、TC-API launch-only 和统一验证脚本集成；两个业务容器均不挂载 Workload API。
`ea15713` 的远程结果早于 OpenClaw Egress 改造，不能作为当前代码的验收结论；当前
方案仍需重新执行远程双 TDVM ALLOW/DENY 和真实插件 E2E。

## 推荐阅读顺序

1. [真实 TDX Node Evidence 与 Trustee 改造方案及执行状态](./Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md)
2. [双 TDVM + Egress/Ingress Broker 架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
3. [实施与验证计划](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md)
4. [OpenViking Broker Sidecar 详细设计](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)
5. [远程验证报告（`ea15713`）](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md)

## 顶层文档职责

| 文档 | 职责 | 状态 |
|---|---|---|
| [Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md](./Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md) | 自定义 Node 流程精简、真实 Evidence Provider/Trustee 改造、当前完成度与验收门槛 | 合同收敛与实施准备；真实路径未完成 |
| [Argus-Dual-TDVM-Broker-Sidecar-Architecture.md](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md) | 当前架构事实源：组件、身份、请求时序和边界 | 已确定 |
| [Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md) | 当前双 Broker 代码、测试和完成条件 | 本地验证中；远程待复验 |
| [OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md) | OpenViking Broker API、PID reference 与生命周期详细设计 | 当前组件设计参考 |
| [Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md) | 历史远程证据；记录 Parent、Entry、PID、UDS 和 mTLS | 早于当前 Egress 改造，不能复用为当前 PASS |

## 历史归档

- [pre-dual-tdvm-broker-sidecar](./archive/pre-dual-tdvm-broker-sidecar/README.md)：
  当前组合方案形成前的非对称架构、旧双 TDVM 直连方案、会议记录及历史评估报告。
- [pre-asymmetric-architecture](./archive/pre-asymmetric-architecture/README.md)：
  非对称架构形成前的设计和验证材料。
- [argus-spiffe-v2](./archive/argus-spiffe-v2/README.md)：
  更早的 v2 执行、Pre-RA 强化和容量计划。

归档文档只用于追溯对应提交、Profile、日期和决策过程，不定义当前架构。

## 文档维护规则

1. 当前架构变化先更新架构文档，再更新实施计划。
2. 代码骨架、Mock 测试和旧 Profile PASS 不得写成当前双 TDVM Broker Profile 已验收。
3. Registration Entry 是静态匹配规则；WorkloadAttestor 在验证成功后返回 selector。
4. Mock Evidence Provider/Trustee 只证明软件链路，不代表真实 Quote、QGS、
   TC-API/Rekor 或生产 Trustee 验证。
5. 每轮远程执行都记录对应 commit；新改动通过前不得复用旧 commit 的运行结论。
