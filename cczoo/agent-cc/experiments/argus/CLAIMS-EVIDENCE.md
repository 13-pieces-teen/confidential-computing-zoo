# 论文主张—实验—证据（revision 1935 精简实验）

当前实现与执行入口见 [IMPLEMENTATION-1935.md](IMPLEMENTATION-1935.md)，前序部署边界见 [PAPER-ALIGNMENT-1806.md](PAPER-ALIGNMENT-1806.md)。管理接口仅供受信管理员使用，须核验实际隔离；不以恶意部署者为默认攻击者。下表描述所需证据，真实 TDX、模型效果、停止窗口及性能结果均待双机测试。

| 主张/问题 | 实验与工具 | 原始证据 | 解释边界 |
|---|---|---|---|
| 实例级准入与身份绑定 | E1；admission_trial attempt、admission_stages、注册、精确 AuthZ | 新订阅、nonce/Evidence/EAR、分阶段回执、实际策略、当前 target/Entry | 本地目标拒绝、记录待决、远端政策拒绝分开；依赖异常不记政策拒绝 |
| 历史与当前实例联合核验 | E1；原件复核、历史诊断、可达性记录 | 采集时真实批准材料、共有检查、操作者允许接口、历史差异 | 只有共同检查允许的真实可达反例才能说明历史增量；离线规则不冒充新鲜在线准入 |
| 实际操作与历史之间的崩溃一致性 | E1；Docktap/TruCon 持久 mutation 与故障点测试 | 转发前意图、Docker 结果、恢复提交、关联 CONFIRMED 记录、待决时准入拒绝 | 默认 tmpfs 覆盖同一 guest boot 内进程重启；未知操作不重放；未决历史拒绝不等于主动停止已有入口 |
| 生命周期失效后入口停止交付 | E2；fault_trial、timeline、receiver | 真实新/旧 socket、在途首块、故障、readiness/入口区间、应用读取和覆盖 | 默认 transport 就绪无需模型；ASGI 读取不扩大为模型消费、擦除或存储撤权 |
| 续期复用与重新准入的区别 | E3；lifecycle_trial/evidence、Node observer | SVID、Provider 原件、启动 ID、真实 Quote 尝试/成功/失败、实例/launch、创建事件 | 跨 Provider 启动不相减；样本接收与 Quote 生成不同；CanReattest=false 不等于持续新鲜 TDX 证明 |
| Agent 使用可信共享服务 | E4；continuous，fleet_business 为权限辅助检查 | 私有范围 API 负例、单客户端稳定工作项、真实 memory_store/recall | 主实验不扫描多客户端；业务 key 权限独立于实例准入，不声称直接 Agent–Agent 编排 |
| 持续任务中新增披露与中断的影响 | E4；work-item-v1、continuous_analysis、fact_receipts | 六步行程任务、带类型/顺序的原始 Proposal、实际释放、完整回读与写入确认、独立接收记录 | 原始提案与派生答案分开；未知写入阻止全部后继写入；未提交、明确拒绝、UNKNOWN 分列；空审计不证明未调用 |
| 共享服务恢复对任务的影响 | E4；Full/native 各自 fault/no_fault、continuous_observations | 相同任务结构/模型/预算、全部已确认提案、原操作核查、独立准入与实际读取、保守完成时间 | 未解决的必要状态不能算完整成功；缺独立准入/读取则合法恢复 UNKNOWN；新会话继续不是模型进程恢复 |
| 分层成本 | E5；Quote/Trustee 计时、time-ready-command、load、resources | 真实生成计数/累计耗时、Trustee RTT、命令至新就绪、TCP/TLS/API、非空 Goodput、CPU/RSS | Trustee RTT 含网络；Quote 累计只得均值；API 与 Agent 端到端分开；new/reuse 分层 |
| 历史长度与共享门禁成本 | E5；cost_trials | 三个冻结历史长度、同 nonce 的 Provider/Trustee 原始时间、A 待决原件、B 新订阅与原连接流量 | 引用字节与拉取字节分列；失败/容量拒绝保留；B 原 Helper 不重启，pending 不等于旧入口关闭 |

每条结果关联完整源码 SHA、工作区与产物摘要、运行清单和原始证据。原始 JSONL、统计 CSV、报告与图表可以重算。所有计划题保留，缺观测记 UNKNOWN/NOT_RUN；置信区间按独立运行或完整配对块，不把题目和请求当独立运行。

核心组保留 Full/native。Guarded native 保留 TDX Node、官方 unix/Docker、Broker、相同本地监测与入口关闭、应用权限；差别为 Argus Workload 证明与历史门禁。−Watchdog 和 −Close 只说明对应机制；静态 mTLS 是成本参照，不承担同等安全保证。没有复现 aDNS/ACLE-MCP 全系统，不直接比较其公布延迟。

[E1-REACHABILITY.md](E1-REACHABILITY.md) 先核查历史差异可达性，没有差异就报告共同保护/诊断。current-facts-only、Attest-on-connect、Invocation lease 未实现在线组。TLS new/reuse 不是每连接证明，客户端凭据 lease 也不是每调用证明租约。不单独宣称第二份 Workload Quote 必不可少或优于同等保证内嵌方案。

监督失效后超过 Δ 的读取与未准入替换实例读取分开。缺准入材料不能证明实例从未准入；缺采集不能推导零读取；明确读取不会因另一处覆盖缺口被抹掉。被动审计不参与生产准入或业务授权。现行策略不增加 TCB UpToDate 条件。

`continuous` 旧配置仍默认 `ledger-v1` 以保持兼容；当前 E4 必须显式选择 `work-item-v1`。只读 LoCoMo 为辅助。E2 的传输层实际释放与 E4 的 Gateway 输入交付采用不同边界，不能把两类时间混为同一指标。小型任务用于展示持续交互中的机制效果，不证明该风险为智能体独有。
