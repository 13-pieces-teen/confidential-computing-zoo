# documents_ly 文档索引

本目录顶层包含当前 Node 实施事实源、Stage 2 当前目标设计，以及仍需归档整理的
历史 Broker 设计和验证记录。[archive](./archive/) 保存更早的架构材料。

## 当前方案

当前实施范围是第一次 OpenViking Node Attestation：

> OpenViking TDVM 内的独立 Evidence Provider 通过 TSM/QGS 生成真实 Quote；
> Agent NodeAttestor 负责 SPIRE challenge 和 proof-of-possession；Server
> NodeAttestor 调用真实 Trustee，并在 appraisal 通过后返回固定 Agent ID。

仓库当前没有可直接执行的端到端部署入口；Trustee policy、EAR trust、proof pin、
SPIRE bundle 和网络地址由目标环境提供。

Workload Attestation、第二次 Quote、Broker身份和双 TDVM业务 mTLS 不在当前运行
范围内。[NGINX + Broker-aware SPIFFE Helper Workload Attestation](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)
记录当前 Stage 2 目标设计，但尚未实现或验收；其余 Broker 顶层文档保留历史设计
和验证结果，不能作为当前部署入口或真实 Node Attestation验收证据。

## 推荐阅读顺序

1. [真实 TDX Node Evidence 与 Trustee 改造方案及执行状态](./Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md)
2. [NGINX + Broker-aware SPIFFE Helper Workload Attestation 当前设计](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)
3. [双 TDVM + Egress/Ingress Broker 历史架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
4. [历史实施与验证计划](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md)
5. [已被当前 Helper + NGINX 方案取代的 OpenViking Broker Sidecar历史设计](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)
6. [历史软件链远程验证报告](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md)

## 顶层文档职责

| 文档 | 职责 | 状态 |
|---|---|---|
| [Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md](./Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md) | 自定义 Node 流程、真实 Evidence Provider/Trustee改造和验收门槛 | 当前事实源；真实E2E仍受网络和policy阻塞 |
| [Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md) | Stage 2目标PID证明、Broker-aware Helper取证、PEM发布与NGINX mTLS完整流程 | 当前设计基线；尚未实现或真实E2E验收 |
| [Argus-Dual-TDVM-Broker-Sidecar-Architecture.md](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md) | 历史双 Broker组件、身份和请求时序 | 非当前运行架构 |
| [Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md) | 历史双 Broker代码与验证计划 | 不再是执行入口 |
| [OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md) | OpenViking Broker API和PID reference历史设计输入 | 已由当前Helper + NGINX Stage 2设计取代 |
| [Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md) | 历史软件链远程证据 | 不能复用为当前PASS |

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
2. 代码骨架、测试替身和历史 Profile PASS不得写成当前真实Node路径已验收。
3. Registration Entry 是静态匹配规则；WorkloadAttestor 在验证成功后返回 selector。
4. 软件链测试不代表真实Quote、QGS、TC-API/Rekor或生产Trustee验证。
5. 每轮远程执行都记录对应 commit；新改动通过前不得复用旧 commit 的运行结论。
