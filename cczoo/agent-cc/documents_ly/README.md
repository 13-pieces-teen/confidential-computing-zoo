# documents_ly 文档索引

索引整理日期：2026-09-16。架构、运行步骤与执行结果分别维护；各验证记录中的提交、版本、环境和日期是其结论边界。

本目录顶层包含通用 Agent/Agent Service 通信设计，以及当前 Node Attestation 与 OpenViking Workload Attestation 说明。通用设计同时包含 **Go/Java 等原生 SDK + Workload API** 与 **Helper 兼容接入**，两端可独立选择。当前具体 OpenViking Workload 实现采用 **真实取证 Provider + Trustee + SPIRE Broker API + SPIFFE Helper + NGINX**。设计目标、已有代码和执行结果分别标明。

## 当前文档与阅读顺序

| 顺序 | 文档 | 内容 |
|---|---|---|
| 1 | [当前代码总览](./Argus-SPIFFE-Current-Code-Architecture-CN.md) | 组件拓扑、端到端流程、代码导航及文档分工 |
| 2 | [通用 Agent 与 Agent Service 可信通信](./Argus-Agent-Agent-Service-Trusted-Communication-CN.md) | 背景价值、原生 SDK 与 Helper 接入及设计目标 |
| 3 | [Node Attestation](./Argus-TDX-Node-Attestation-CN.md) | 节点身份配置、proof key、Quote/REPORTDATA、Trustee EAR 与 Agent SVID |
| 4 | [OpenViking Workload Attestation](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md) | 实际服务进程取证、目标身份交付、NGINX mTLS、AuthZ 与失效处理 |
| 5 | [配置参考](../core/argus/docs/configuration.md) | 实际环境变量、SPIRE Provider CLI、插件输入与 Guard 策略 YAML |
| 6 | [Workload 运行手册](../core/spire/workload/README.md) | 构建安装、Node 升级、policy/Entry、TC API 启动、登记与生命周期操作 |
| 7 | [OpenClaw 客户端接入](../adapters/OpenClaw/spiffe_client/README.md) | Gateway PID 登记、凭据交付、原生 HTTPS 及失效合同 |
| 8 | [OpenClaw 客户端部署手册](../adapters/OpenClaw/spiffe_client/DEPLOY-IP1-TDVM.md) | IP1 / Guest / IP2 部署顺序、产物、运行与验收步骤 |
| 9 | [E2E 演示操作手册](../../../documents_ly/argus-e2e-demo-plan-20260909.md) | 环境准备顺序、WebUI 操作与认证日志观察 |

## 验证记录

| 记录 | 范围 |
|---|---|
| [Workload 首轮验证](../core/spire/workload/VALIDATION.md)、[客户端软件验证](../adapters/OpenClaw/spiffe_client/VALIDATION.md) | 本地及 Linux 容器测试、已知失败和各轮待验清单 |
| [IP2 Node Enrollment](./Argus-IP2-TDVM-Node-Attestation-Real-Enrollment-Report-CN.md) | 2026-09-03 至 09-04 的 Node-only 真实取证与签发记录 |
| [公司 Workload 状态](../../../documents_ly/argus-openviking-workload-attestation-status-20260909.md) | 2026-09-09：固定实例 PoC 通过，严格 UpToDate TCB 验收阻塞 |
| [公司 OpenClaw 客户端验收](../../../documents_ly/argus-openclaw-ip1-tdvm-client-acceptance-20260909.md) | 2026-09-09：mTLS、写入读回及生命周期已有通过项；长期记忆召回失败，联合验收未通过 |

表中的结论属于报告时点，不代表本次整理已复验现场。后续报告补充对应阶段的结果，首轮文件中的旧待验清单保留其原始含义。

代码入口见 [SPIRE README](../core/spire/README.md)。运行步骤和验证记录随代码放置，本目录通过链接引用，避免维护重复副本。

## 论文研究与写作

统一入口为[论文文档导航](./论文/README.md)，集中说明 10 份研究文档的职责、阅读顺序、引用依据和维护位置。

- **当前主稿与章节：** [09-16 Story 与中文 Introduction](./论文/Argus-EMAS-Story与Introduction-2026-09-16.md)以服务化记忆展开可信服务组合；[论文框架](./论文/Argus-AAMAS2027-EMAS-论文框架.md)维护方法、候选贡献、章节与图表。
- **实验：** [任务与性能实验细化](./论文/Argus-EMAS-Agent场景实验设计与论文借鉴-2026-09-14.md)维护 τ²-bench、LoCoMo/OpenClaw 和 Server 混合负载；[总方案](./论文/框架评估研究-优势对比与实验方案-2026-09-07.md)维护基线、E1–E7 与统计规则；[Review](./论文/评估方案Review-CCF-AB-2026-09-07.md)维护运行前核对项。正式结果尚待实测。
- **文献与研究记录：** [Related Work 汇总](./论文/Argus-Related-Work-汇总表-2026-09-15.md)维护来源与比较；[贡献重叠审查](./论文/Argus-EMAS-相关工作与贡献重叠审查-2026-09-11.md)维护论证问题。整体思路、跨企业场景和早期候选方向通过论文导航保留，不作为当前已实现能力。

## 技术博客

面向大众及非本领域读者的 [Argus–SPIFFE 内部技术博客](./技术博客/Argus-SPIFFE-内部技术博客-2026-09-13.md)参考四篇指定 Intel 技术博客的 PDF 正文，从 AI 助手保存项目记录的场景介绍方案和商用价值；[飞书正文](https://lcnaletyynmt.feishu.cn/docx/MtC8dXKzhoS0kNxtYi8cdSi7nJe)与本地稿同步。[风格提炼与事实核验](./技术博客/Intel参考文章-风格提炼与事实核验-2026-09-13.md)单独保留逐篇分析、写作取舍和项目验证边界。

## 实现边界

- Node 与 Workload 使用独立的绑定合同和 policy。Workload 绑定本次 OpenViking 服务进程实例，Helper 代表该实例获取身份，NGINX 使用目标 SVID 终止 mTLS。
- Go/Java 原生 SDK 已有上游支持；当前项目的自定义 WorkloadAttestor 仅在 `AttestReference` 返回证明 selectors，普通 `Attest` 返回空结果。原生接入同等 TDX 准入仍需插件与启动 profile 适配，不能把上游支持视为本项目已完成验收。
- TC API 原日志上传保持；当前身份门禁不依赖 Rekor 验证。普通 SVID 轮换不生成新 Quote；Helper 重连会重新认证，Agent 独立周期重证明尚未实现。

## 历史材料

首阶段 OpenClaw → OpenViking 实施计划、旧重构计划、双 Broker 架构和旧版本验证报告统一放在 [archive](./archive/README.md)，用于追溯设计与证据。现行部署及交付包使用上表的运行手册。

## 维护规则

1. 总览维护拓扑、流程和导航；绑定合同、EAR 校验与服务端失效细节更新 Node/Workload 专题，客户端失效合同更新客户端说明；部署命令更新运行手册；执行结果更新验证记录。
2. 被替代的方案移入 archive，明确失效范围并修复链接；没有追溯价值的草稿直接删除。
3. 验证结论注明提交、版本、环境和日期，区分代码实现、本地测试与真实环境验收。
