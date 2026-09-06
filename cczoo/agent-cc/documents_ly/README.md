# documents_ly 文档索引

更新日期：2026-09-06；代码核对基线：`feat/argus-spiffe-v2-val` / `9f493f8`。

本目录顶层只保留当前 Node Attestation 与 OpenViking Workload Attestation 说明。两阶段的代码和部署工具均已实现；当前 Workload 路径采用 **真实取证 Provider + Trustee + SPIRE Broker API + SPIFFE Helper + NGINX**。

## 当前文档与阅读顺序

| 顺序 | 文档 | 内容 |
|---|---|---|
| 1 | [Node Attestation](./Argus-TDX-Node-Attestation-CN.md) | 节点准入、proof key、Quote/REPORTDATA、Trustee EAR 与 Agent SVID |
| 2 | [OpenViking Workload Attestation](./Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md) | 实际服务进程取证、目标身份交付、NGINX mTLS、AuthZ 与失效处理 |
| 3 | [Workload 运行手册](../core/spire/workload/README.md) | 构建安装、Node 升级、policy/Entry、TC API 启动、登记与生命周期操作 |
| 4 | [验证记录](../core/spire/workload/VALIDATION.md) | 已执行测试、证据边界、既有失败和公司环境待验项目 |

代码入口见 [SPIRE README](../core/spire/README.md)。运行步骤和验证记录随代码放置，本目录通过链接引用，避免维护重复副本。

## 当前实现与验证状态

- SPIRE Server/Agent 与两个 Attestor SDK 为 **v1.15.3**；Helper 基于 **v0.11.0**，定制版本为 **0.11.0-argus.1**；Trustee 接口基线为 **v0.21.0**。
- Node 与 Workload 使用独立的绑定合同和 policy。Workload 绑定本次 OpenViking 服务进程实例，Helper 代表该实例获取身份，NGINX 使用目标 SVID 终止 mTLS。
- TC API 原日志上传保持；当前身份门禁不依赖 Rekor 验证。普通 SVID 轮换不生成新 Quote；Helper 重连会重新认证，Agent 独立周期重证明尚未实现。
- 最新验证记录覆盖本地软件与 Linux 容器集成测试。公司环境的 v1.15.3 Node 复验、真实 Quote/DCAP、Trustee、OpenViking 业务和生命周期验收仍待执行，不能用旧报告代替。

## 历史材料

旧重构计划、双 Broker 架构和旧版本验证报告统一放在 [archive](./archive/README.md)，不进入当前阅读顺序或部署步骤。包含 Envoy + SDS 的旧代理部署方案及绘图风格草稿已删除。

## 维护规则

1. 架构和协议变化更新对应阶段说明；部署命令更新运行手册；执行结果更新验证记录。
2. 被替代的方案移入 archive，明确失效范围并修复链接；没有追溯价值的草稿直接删除。
3. 验证结论注明提交、版本、环境和日期，区分代码实现、本地测试与真实环境验收。
