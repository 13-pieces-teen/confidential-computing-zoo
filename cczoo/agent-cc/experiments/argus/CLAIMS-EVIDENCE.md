# 论文主张—实验—证据（revision 1373）

当前实现与双机步骤见 [PAPER-ALIGNMENT-1373.md](PAPER-ALIGNMENT-1373.md)。下表描述所需证据，真实 TDX、模型效果、停止窗口及性能结果均待双机测试。

| 主张/问题 | 实验与工具 | 原始证据 | 解释边界 |
|---|---|---|---|
| 实例级准入与身份绑定 | E1；admission_trial、注册、精确 AuthZ | nonce/Evidence/EAR、实际策略、当前 target/Entry、TLS 身份与准入判定 | 本地绑定拒绝与远端 Verifier 拒绝区分 |
| 历史与当前实例联合核验 | E1；原件复核、历史诊断、可达性记录 | 采集时真实批准材料、共有检查、操作者允许接口、历史差异 | 只有共同检查允许的真实可达反例才能说明历史增量；离线规则不冒充新鲜在线准入 |
| 生命周期失效后入口停止交付 | E2；fault_trial、timeline、receiver | 真实新/旧 socket、在途首块、故障、readiness/入口区间、应用读取和覆盖 | 默认 transport 就绪无需模型；ASGI 读取不扩大为模型消费、擦除或存储撤权 |
| 续期复用与重新准入的区别 | E3；lifecycle_trial/evidence、Node observer | SVID、Provider 原件、启动 ID、真实 Quote 尝试/成功/失败、实例/launch、创建事件 | 跨 Provider 启动不相减；样本接收与 Quote 生成不同；CanReattest=false 不等于持续新鲜 TDX 证明 |
| 多 Agent 使用可信共享服务 | E4；fleet_business、LoCoMo | 私有范围 API 负例、独立用户/Gateway、历史导入、新会话、请求/注入审计 | 多客户端访问一个服务，不是直接 Agent–Agent 编排；业务 key 权限独立于实例准入 |
| Agent 接入性能和故障影响 | E4；locomo_execution/suite、analysis/plot | 全计划题、固定时间表、完整原始任务与请求记录、实际模型、实际并发、服务端控制回执 | 完成/有效完成/正确率/接收安全分开；F1 仅功能辅助，Argus 不主张提高记忆质量 |
| 共享服务恢复对任务的影响 | E4；Full/native 各自 fault/no_fault | 相同题目/模型/预算、独立用户、控制时刻与结果、恢复后请求及任务 | 缺对照或控制未知不进配对；共享故障影响所有客户端，不称局部连带损失 |
| 分层成本 | E5；Quote/Trustee 计时、time-ready-command、load、resources | 真实生成计数/累计耗时、Trustee RTT、命令至新就绪、TCP/TLS/API、非空 Goodput、CPU/RSS | Trustee RTT 含网络；Quote 累计只得均值；API 与 Agent 端到端分开；new/reuse 分层 |

每条结果关联完整源码 SHA、工作区与产物摘要、运行清单和原始证据。原始 JSONL、统计 CSV、报告与图表可以重算。所有计划题保留，缺观测记 UNKNOWN/NOT_RUN；置信区间按独立运行或完整配对块，不把题目和请求当独立运行。

核心组保留 Full/native。Guarded native 保留 TDX Node、官方 unix/Docker、Broker、相同本地监测与入口关闭、应用权限；差别为 Argus Workload 证明与历史门禁。−Watchdog 和 −Close 只说明对应机制；静态 mTLS 是成本参照，不承担同等安全保证。没有复现 aDNS/ACLE-MCP 全系统，不直接比较其公布延迟。

[E1-REACHABILITY.md](E1-REACHABILITY.md) 先核查历史差异可达性，没有差异就报告共同保护/诊断。current-facts-only、Attest-on-connect、Invocation lease 未实现在线组。TLS new/reuse 不是每连接证明，客户端凭据 lease 也不是每调用证明租约。不单独宣称第二份 Workload Quote 必不可少或优于同等保证内嵌方案。

监督失效后超过 Δ 的读取与未准入替换实例读取分开。缺准入材料不能证明实例从未准入；缺采集不能推导零读取；明确读取不会因另一处覆盖缺口被抹掉。被动审计不参与生产准入或业务授权。现行策略不增加 TCB UpToDate 条件。

旧 continuous/完整合成事务的四格输出保留为历史可选机制诊断，见旧文档；它不再是默认论文 E4。默认 E4 使用典型 LoCoMo 任务，接收停止由独立 E2 验证。
