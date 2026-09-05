# documents_ly 文档索引

本目录按 Node Attestation、OpenViking Workload Attestation 和历史设计组织文档。
[archive](./archive/) 保存更早的架构材料。

## 当前方案

Node 阶段的 Evidence Provider、TDX Quote、Trustee 评估与 SPIRE 节点准入，见
[Node Attestation 方案](./Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md)。

[NGINX + Broker-aware SPIFFE Helper Workload 认证方案](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)
以 Node Attestation 已完成为前提，描述 TC API 启动、目标进程真实取证（本轮不依赖 Rekor 验证）、
Trustee workload 评估、Broker 身份交付，以及 NGINX 服务入口与失效处理。
这是当前 Workload 设计与验收依据，已经原位替换此前同名方案。

构建、部署和公司执行入口见 [Workload 运行手册](../core/spire/workload/README.md)，
本地测试、提交前 review 修复及公司待验项目见 [验证记录](../core/spire/workload/VALIDATION.md)。

运行验证状态以对应环境、版本和日期的验证记录为准；设计文档不能替代实际验收。
其余 Broker 顶层文档保留历史设计和验证结果，不定义当前业务方案。

## 推荐阅读顺序

1. [真实 TDX Node Evidence 与 Trustee 改造方案及执行状态](./Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md)
2. [OpenViking Workload 认证方案：NGINX + Broker-aware SPIFFE Helper](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)
3. [双 TDVM + Egress/Ingress Broker 历史架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
4. [历史实施与验证计划](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md)
5. [已被当前 Helper + NGINX 方案取代的 OpenViking Broker Sidecar历史设计](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)
6. [历史软件链远程验证报告](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md)

## 顶层文档职责

| 文档 | 职责 | 状态 |
|---|---|---|
| [Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md](./Argus-TDX-Node-Attestation-Real-Evidence-Trustee-Refactor-Plan-CN.md) | Node Evidence、Trustee 与 SPIRE 准入方案及验收门槛 | Node 设计与执行记录；运行状态见对应环境验证记录 |
| [Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md) | TC API、真实 Quote/Trustee、Helper 身份交付、NGINX mTLS 与生命周期 | 首轮实现与验收依据；SPIRE 1.15.3 / Helper 0.11.0 |
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
