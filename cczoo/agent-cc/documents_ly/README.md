# documents_ly 文档索引

索引整理日期：2026-09-10。架构、运行步骤与执行结果分别维护；各验证记录中的提交、版本、环境和日期是其结论边界。

本目录顶层包含通用 Agent/Agent Service 通信设计，以及当前 Node Attestation 与 OpenViking Workload Attestation 说明。通用设计同时包含 **Go/Java 等原生 SDK + Workload API** 与 **Helper 兼容接入**，两端可独立选择。当前具体 OpenViking Workload 实现采用 **真实取证 Provider + Trustee + SPIRE Broker API + SPIFFE Helper + NGINX**。设计目标、已有代码和执行结果分别标明。

## 当前文档与阅读顺序

| 顺序 | 文档 | 内容 |
|---|---|---|
| 1 | [通用 Agent 与 Agent Service 可信通信](./Argus-Agent-Agent-Service-Trusted-Communication-CN.md) | 背景价值、原生 SDK 与 Helper 接入、准入与 mTLS、上下文交付、跨语言互通及验收 |
| 2 | [Node Attestation](./Argus-TDX-Node-Attestation-CN.md) | 节点准入、proof key、Quote/REPORTDATA、Trustee EAR 与 Agent SVID |
| 3 | [OpenViking Workload Attestation](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md) | 实际服务进程取证、目标身份交付、NGINX mTLS、AuthZ 与失效处理 |
| 4 | [Workload 运行手册](../core/spire/workload/README.md) | 构建安装、Node 升级、policy/Entry、TC API 启动、登记与生命周期操作 |
| 5 | [OpenClaw 客户端接入](../adapters/OpenClaw/spiffe_client/README.md) | Gateway PID 登记、客户端凭据交付与原生 HTTPS 接入 |
| 6 | [IP1 OpenClaw TDVM → IP2 OpenViking 实施计划](./Argus-IP1-OpenClaw-IP2-OpenViking-Implementation-Plan-CN.md) | 首阶段设计与交付范围；OpenClaw 远程证明延期，执行结果见下表 |
| 7 | [E2E 演示操作手册](../../../documents_ly/argus-e2e-demo-plan-20260909.md) | 环境准备顺序、WebUI 操作与认证日志观察 |

## 验证记录

| 记录 | 范围 |
|---|---|
| [Workload 首轮验证](../core/spire/workload/VALIDATION.md)、[客户端软件验证](../adapters/OpenClaw/spiffe_client/VALIDATION.md) | 本地及 Linux 容器测试、已知失败和各轮待验清单 |
| [IP2 Node Enrollment](./Argus-IP2-TDVM-Node-Attestation-Real-Enrollment-Report-CN.md) | 2026-09-03 至 09-04 的 Node-only 真实取证与签发记录 |
| [公司 Workload 状态](../../../documents_ly/argus-openviking-workload-attestation-status-20260909.md) | 2026-09-09：固定实例 PoC 通过，严格 UpToDate TCB 验收阻塞 |
| [公司 OpenClaw 客户端验收](../../../documents_ly/argus-openclaw-ip1-tdvm-client-acceptance-20260909.md) | 2026-09-09：mTLS、写入读回及生命周期已有通过项；长期记忆召回失败，联合验收未通过 |

表中的结论属于报告时点，不代表本次整理已复验现场。后续报告补充对应阶段的结果，首轮文件中的旧待验清单保留其原始含义。

代码入口见 [SPIRE README](../core/spire/README.md)。运行步骤和验证记录随代码放置，本目录通过链接引用，避免维护重复副本。

项目背景和候选研究贡献见 [框架价值与核心贡献研究](./论文/框架价值与核心贡献研究-2026-09-06.md)。其中的跨域政策互认、动态工具准入和可核验交付等方向不作为当前已实现能力。

## 实现边界

- Node 与 Workload 使用独立的绑定合同和 policy。Workload 绑定本次 OpenViking 服务进程实例，Helper 代表该实例获取身份，NGINX 使用目标 SVID 终止 mTLS。
- Go/Java 原生 SDK 已有上游支持；当前项目的自定义 WorkloadAttestor 仅在 `AttestReference` 返回证明 selectors，普通 `Attest` 返回空结果。原生接入同等 TDX 准入仍需插件与启动 profile 适配，不能把上游支持视为本项目已完成验收。
- TC API 原日志上传保持；当前身份门禁不依赖 Rekor 验证。普通 SVID 轮换不生成新 Quote；Helper 重连会重新认证，Agent 独立周期重证明尚未实现。

## 历史材料

旧重构计划、双 Broker 架构和旧版本验证报告统一放在 [archive](./archive/README.md)，用于追溯设计与证据。

## 维护规则

1. 架构和协议变化更新对应阶段说明；部署命令更新运行手册；执行结果更新验证记录。
2. 被替代的方案移入 archive，明确失效范围并修复链接；没有追溯价值的草稿直接删除。
3. 验证结论注明提交、版本、环境和日期，区分代码实现、本地测试与真实环境验收。
