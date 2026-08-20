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

仓库已经分别具备双 TDVM 部署骨架和 Broker Sidecar 组件，但二者尚未合并成一个可运行
Profile：现有 `runtime/dual-tdvm` 仍让 OpenViking 直接挂载 Workload API。因此当前
状态是“目标架构已确定，代码集成与远程验收待完成”，不能引用旧报告中的 PASS 作为
最新方案证据。

## 推荐阅读顺序

1. [双 TDVM + OpenViking Broker Sidecar 架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
2. [实施与验证计划](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md)
3. [OpenViking Broker Sidecar 详细设计](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)

## 顶层文档职责

| 文档 | 职责 | 状态 |
|---|---|---|
| [Argus-Dual-TDVM-Broker-Sidecar-Architecture.md](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md) | 当前架构事实源：组件、身份、A-F 时序和边界 | 已确定 |
| [Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md) | 双 TDVM 骨架与 Broker 组件的合并步骤、测试和完成条件 | 待实施 |
| [OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md) | Broker API、PID reference、Sidecar 生命周期和取舍 | 组件已实现，待接入双 TDVM |

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
5. 新的远程验证完成后，应新增双 TDVM Broker 专用报告，不覆盖历史报告。
