# documents_ly 文档索引

本目录顶层只保留当前方案。此前的架构、会议记录、评估计划和运行报告均放在
[archive](./archive/) 下。

## 当前方案

当前唯一主方案是：

> OpenClaw 与 OpenViking 分别运行在独立 TDVM 中；两个 TDVM 各自完成 Node
> Attestation。OpenClaw 使用自己的 workload identity 发起调用，OpenViking Python
> 保持无侵入，由同一 OpenViking TDVM 内的 Broker Sidecar 代表实际 Python PID
> 请求目标 SVID，并终止入站 mTLS。

当前 Evidence Provider 和 Trustee 均为 Mock。

`runtime/dual-tdvm` 已完成 Broker Endpoint、PID-reference WorkloadAttestor、TC-API
launch-only 启动、三个 Registration Entry 和 Sidecar mTLS 验证脚本的代码集成；
OpenViking 不再挂载 Workload API。当前状态是“本地实现与静态验证完成，远程双 TDVM
ALLOW/DENY 验收待执行”，不能引用旧报告中的 PASS 作为最新方案证据。

## 推荐阅读顺序

1. [双 TDVM + OpenViking Broker Sidecar 架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
2. [实施与验证计划](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md)
3. [OpenViking Broker Sidecar 详细设计](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)
4. [远程验证报告（待执行）](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md)

## 顶层文档职责

| 文档 | 职责 | 状态 |
|---|---|---|
| [Argus-Dual-TDVM-Broker-Sidecar-Architecture.md](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md) | 当前架构事实源：组件、身份、A-F 时序和边界 | 已确定 |
| [Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md) | 双 TDVM 骨架与 Broker 组件的合并步骤、测试和完成条件 | 代码已实施，远程待验收 |
| [OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md) | Broker API、PID reference、Sidecar 生命周期和取舍 | 已接入双 TDVM，远程待验收 |
| [Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md) | 记录 Parent ID、Entry、PID、UDS 权限、Trustee metrics 和 mTLS 远程证据 | 待远程填写 |

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
5. 双 TDVM Broker 专用报告保留“待验证”状态；远程执行后在该报告补入实测证据，
   不覆盖历史报告。
