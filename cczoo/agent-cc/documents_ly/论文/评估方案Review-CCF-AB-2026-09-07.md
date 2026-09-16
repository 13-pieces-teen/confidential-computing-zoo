# Agent-CC 评估方案 Review

审查对象：[整系统评估方案](框架评估研究-优势对比与实验方案-2026-09-07.md)。日期：2026-09-07。范围：方案自审，未执行正式实验。

文档整理：2026-09-16。本文件集中列出正式运行前需要核对的事项，不另定义基线或实验结果。统一规则以[整系统评估方案](./框架评估研究-优势对比与实验方案-2026-09-07.md)为准；当前 τ²-bench、LoCoMo/OpenClaw 与 Server 混合负载的执行细节见[Agent 场景实验补充](./Argus-EMAS-Agent场景实验设计与论文借鉴-2026-09-14.md)。文档分工见[论文目录说明](./README.md)。保留原检查项并补充当前 benchmark 核对点；本次未执行或确认完成这些检查，完成状态须由对应版本的运行证据更新。

方案已覆盖 Node Attestation、Workload Attestation、真实 OpenClaw–OpenViking 交互，以及跨层故障、性能和资源成本。强基线、机制消融、合法控制、配对统计和复现材料均有对应设计。

## 正式运行前需完成

| 项目 | 待完成内容 | 关联实验 |
|---|---|---|
| 两端实际保证 | 核对客户端 Node/Workload 配置及目标 Entry；双端同级 TDX 组需各自具备对应证据 | E1、E2、E7 |
| 基线可运行性 | 验证 B3a 的 Broker/原生插件组合；实现 B3b 的认证取证通道；核对 F01 固定 parent/profile 适配 | E1、E2、E3 |
| 冷启动与业务来源 | 从两端/整栈启动计时；补充真实 Gateway 的读取、检索与上下文使用记录；区分验证进程的读回/commit | E2、E7 |
| 数据路径与恢复 | 补客户端/后端持续观测、最后数据交付、任务丢失/重复/未知状态；单列服务端冻结与远端策略变化边界 | E1、E4、E7 |
| 实验参数 | 预实验后锁定数据、SLO、到达率、样本量、观测窗口和分析方法 | E2–E5、E7 |
| 复用与复现 | 验证第二调用程序/服务及同等准入；在干净环境运行材料并重建核心结果 | E6、整体交付 |

### 当前 benchmark 的补充核对点（2026-09-16）

以下均为运行前待核对项，沿用总方案的基线、统计与证据规则。

| 项目 | 待核对内容 | 关联实验 |
|---|---|---|
| 三类 transport | 分别验证 τ² 的记忆 adapter、LoCoMo/OpenClaw 的真实 Gateway 插件、Server 发压用 SDK/HTTP 客户端实际使用 Argus 身份及精确对端校验；关联到获准的真实 OpenViking 实例，不能只替换 URL 或证明外层代理 | E1、E3、E7 |
| 请求来源与实收 | 将 benchmark 的 task/question/session 标识关联到记忆请求、入口及后端实收记录；区分 Agent、发压程序与验证进程。τ² 的其他业务工具不默认纳入保护范围，问答正确也不代替实际记忆使用证据 | E1、E4、E7 |
| 任务与请求计时 | τ² 的训练记忆生成、在线任务与评分分开；LoCoMo 的导入、索引及缓存条件固定，整次问答、记忆请求与 Server 处理分别计时。首次可信就绪与稳态复用分列，保留失败、超时、重试及等待 | E2、E3、E7 |
| 混合负载与后台完成 | 核对闭环 worker 与开放到达率适配；分别记录计划发送、实际发送、实收、完成及成功请求。总 QPS 不替代成功吞吐，commit/导入接受不替代后台完成；记录队列、积压和排空，CLI 启动与模型限流成本单列 | E3、E5 |

## 关键核对依据

- [客户端接入手册](../../adapters/OpenClaw/spiffe_client/README.md)：Gateway 使用自己的目标身份，客户端证明强度由本机配置决定。
- [WorkloadAttestor](../../core/spire/plugins/argus-tdx-workloadattestor/internal/workloadattestor/plugin.go)：当前自定义证明主要走 `AttestReference`，原生 SDK 的同等证明路径需先适配。
- [E2E 脚本](../../adapters/OpenClaw/scripts/verify_openclaw_plugin_e2e.sh)：实际 Gateway POST 可验证；部分读取/commit 由验证进程完成，业务计时开始于健康检查之后。
- [生命周期脚本](../../core/spire/workload/scripts/verify-lifecycle.py)：现有停止检查主要观察 systemd/readiness/PEM，需要补实际连接与业务数据观测。
- [Workload 验证记录](../../core/spire/workload/VALIDATION.md)：正式硬件结果须对应本次固定版本。

复核重点是：组件拒绝能否落实到实际业务、基线是否具有可比保证、失败是否进入统计、原始数据是否足以重建结果。上述执行项完成后，才能据实判断系统优势。
