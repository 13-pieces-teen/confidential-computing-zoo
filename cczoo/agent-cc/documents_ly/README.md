# documents_ly 文档索引

更新日期：2026-09-07；代码核对基线：`feat/argus-spiffe-v2-val` / `467ad8a`，另有尚未提交的客户端接入改动。

本目录顶层包含通用 Agent/Agent Service 通信设计，以及当前 Node Attestation 与 OpenViking Workload Attestation 说明。通用设计同时包含 **Go/Java 等原生 SDK + Workload API** 与 **Helper 兼容接入**，两端可独立选择。当前具体 OpenViking Workload 实现采用 **真实取证 Provider + Trustee + SPIRE Broker API + SPIFFE Helper + NGINX**。设计目标、已有代码和执行结果分别标明。

## 当前文档与阅读顺序

| 顺序 | 文档 | 内容 |
|---|---|---|
| 1 | [通用 Agent 与 Agent Service 可信通信](./Argus-Agent-Agent-Service-Trusted-Communication-CN.md) | 背景价值、原生 SDK 与 Helper 接入、准入与 mTLS、上下文交付、跨语言互通及验收 |
| 2 | [Node Attestation](./Argus-TDX-Node-Attestation-CN.md) | 节点准入、proof key、Quote/REPORTDATA、Trustee EAR 与 Agent SVID |
| 3 | [OpenViking Workload Attestation](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md) | 实际服务进程取证、目标身份交付、NGINX mTLS、AuthZ 与失效处理 |
| 4 | [Workload 运行手册](../core/spire/workload/README.md) | 构建安装、Node 升级、policy/Entry、TC API 启动、登记与生命周期操作 |
| 5 | [OpenClaw 客户端接入](../adapters/OpenClaw/spiffe_client/README.md) | 当前工作区的文件凭据 TLS 接入与部署步骤；共用 Helper 收敛尚未完成 |
| 6 | [Workload 验证记录](../core/spire/workload/VALIDATION.md)、[客户端验证记录](../adapters/OpenClaw/spiffe_client/VALIDATION.md) | 已执行测试、证据边界、既有失败和公司环境待验项目 |
| 7 | [IP1 OpenClaw TDVM → IP2 OpenViking 实施计划](./Argus-IP1-OpenClaw-IP2-OpenViking-Implementation-Plan-CN.md) | 2026-09-09 修订；先部署新 Guest、普通 SPIRE 身份与 mTLS 业务，OpenClaw 远程证明延期，尚未执行 |

2026-09-09 新增的[公司 Workload 状态报告](../../../documents_ly/argus-openviking-workload-attestation-status-20260909.md)已记录固定实例的真实 PoC 链路通过、严格 TCB 验收阻塞。下方及首轮验证文件中的公司待验说明属于此前基线；当前剩余项目按新报告与下一阶段计划区分，不能把旧的待验状态套用到全部环节。

代码入口见 [SPIRE README](../core/spire/README.md)。运行步骤和验证记录随代码放置，本目录通过链接引用，避免维护重复副本。

项目背景和候选研究贡献见 [框架价值与核心贡献研究](./论文/框架价值与核心贡献研究-2026-09-06.md)。其中的跨域政策互认、动态工具准入和可核验交付等方向不作为当前已实现能力。

## 当前实现与验证状态

- SPIRE Server/Agent 与两个 Attestor SDK 为 **v1.15.3**；Helper 基于 **v0.11.0**，定制版本为 **0.11.0-argus.1**；Trustee 接口基线为 **v0.21.0**。
- Node 与 Workload 使用独立的绑定合同和 policy。Workload 绑定本次 OpenViking 服务进程实例，Helper 代表该实例获取身份，NGINX 使用目标 SVID 终止 mTLS。
- Go/Java 原生 SDK 已有上游支持；当前项目的自定义 WorkloadAttestor 仅在 `AttestReference` 返回证明 selectors，普通 `Attest` 返回空结果。原生接入同等 TDX 准入仍需插件与启动 profile 适配，不能把上游支持视为本项目已完成验收。
- TC API 原日志上传保持；当前身份门禁不依赖 Rekor 验证。普通 SVID 轮换不生成新 Quote；Helper 重连会重新认证，Agent 独立周期重证明尚未实现。
- 已有验证记录包含本地软件与 Linux 容器集成结果，但不覆盖全部后续改动：Provider 路由调整后的 Rust 复验、当前客户端的 Linux/systemd 与完整业务联调仍有缺口。公司环境的 v1.15.3 Node 复验、真实 Quote/DCAP、Trustee、OpenViking 业务和生命周期验收仍待执行，不能用旧报告代替。

## 历史材料

旧重构计划、双 Broker 架构和旧版本验证报告统一放在 [archive](./archive/README.md)，不进入当前阅读顺序或部署步骤。包含 Envoy + SDS 的旧代理部署方案及绘图风格草稿已删除。

## 维护规则

1. 架构和协议变化更新对应阶段说明；部署命令更新运行手册；执行结果更新验证记录。
2. 被替代的方案移入 archive，明确失效范围并修复链接；没有追溯价值的草稿直接删除。
3. 验证结论注明提交、版本、环境和日期，区分代码实现、本地测试与真实环境验收。
