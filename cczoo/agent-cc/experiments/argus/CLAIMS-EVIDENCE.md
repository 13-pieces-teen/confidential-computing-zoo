# 论文主张—实验—证据

当前核对版本为飞书 revision **1329**，差异见 [核对记录](PAPER-ALIGNMENT-1329.md)。真实测试统一等待双机结果。当前持续任务和原件导出见 [CONTINUOUS.md](CONTINUOUS.md)、[ADMISSION-ARCHIVE.md](ADMISSION-ARCHIVE.md)。此前本地回归不覆盖新增实现。

| 初稿主张 | 实验和工具 | 需要保留的原始证据 | 当前边界 |
|---|---|---|---|
| 实例级身份和准入架构 | E1、E4；fleet + exact AuthZ + business | 注册选择器、当前 target、证书 URI、实际 API 有效用户、跨用户读/搜/问结果 | 本地真实 mTLS/固定版本源代码测试；跨机业务 NOT_RUN |
| 历史与当前实例联合核验 | E1；production Trustee/Verifier + admission_trial + admission_evidence + history_diagnostics | nonce 对应 Evidence/Trustee 请求/签名 EAR、验证时间、实际策略/历史材料及真实准入观测 | 导出默认关闭；复核采集时硬件判断，不冒充新的离线 DCAP/实时准入；真实可达性待测试 |
| 入口与实例生命周期联动 | E2；fault_trial + timeline + remote_acceptance + receiver_audit | 故障前双连接、在途首块读取、业务里程碑、readiness/入口观测区间、应用请求读取与客户端响应分块、覆盖区间 | 检测使用明确代理观测；本地实际分块 mTLS 不推导远程 systemd 窗口 |
| 已有效身份的复用和重新准入区别 | E3；Provider quote-counters + lifecycle_trial + node_attestation_observe + lifecycle_evidence + resume-launch | 真正 Quote 生成尝试/成功/失败/重试、Provider 启动 ID、SVID serial、launch ID、创建事件及连续业务 trace | CanReattest=false；跨 Provider 重启不相减，缺 counter 为 UNKNOWN；真实 TDX 待测 |
| 持续任务中的交付与业务恢复 | E4；continuous + fact_receipts + continuous_analysis | 固定任务/故障/恢复表、真实工具审计、完整事务摘要、提交和后继检索、当前实例归属及重新准入证据 | 全计划任务分母；接收与任务双轴，未调用不能算拦截；实际结果 NOT_RUN |
| 共享故障对任务的影响 | E4；continuous_suite + runner + continuous_plot | Full/native 各自 fault/no_fault，独立业务用户/秘密、同 seed 结构、各客户端结果与完整配对块 | 当前故障范围为 shared_service；全部依赖客户端受到作用，不称局部连带损失 |
| 局部故障的连带损失 | 后续局部 continuous 注入；独立配对统计 | 事前轮换的注入对象和未注入集合，同组无故障对照；损失=(no_fault−fault)×100 pp | 当前 continuous 无局部注入路径，NOT_RUN；旧 fleet_fault 仅是另一套可用性实验 |
| 多 Agent 使用可信共享服务 | E4；fleet_business + fleet_fault + locomo_run | 六阶段随机事实、实际注入、跨用户 API 负例、局部/共享故障、显式恢复的新会话回答；LoCoMo 另存任务成绩/覆盖 | 合成机制与 LoCoMo-derived QA 分开；COMPLETE 不是安全 PASS；不是直接 Agent–Agent 协作 |
| 增量机制的成本和必要条件 | E5；五组 variants、load_fleet、resources、analysis | 生效配置/二进制、首次就绪、稳态 HTTP、真实业务任务、原始请求/资源序列、采集 on/off | 对照需要相同模型、数据、权限、预算、负载；NOT_RUN 不得当实测 |

每条论文结果需同时指向源码/构建 manifest、运行 manifest 和对应原始证据；运行版本不能只写分支名。配对表保留缺失组、失败组与容量不足的原因。

历史消融先保存事前批准度量参考并执行离线诊断，再核查受控停止/启动候选能否通过两组共有检查。没有可达差异时不增加 current-facts-only 线上组，不在 E4 强行比较。停止后同镜像替换不天然等于“历史机制独有的拒绝”。

E1 的允许接口、共同检查、路由及接收证据按 [E1-REACHABILITY.md](E1-REACHABILITY.md) 记录；离线诊断不能证明在线攻击可达。论文的 current-facts-only、Attest-on-connect、Invocation lease 均为 proposed_not_run，现有 TLS new/reuse 不实现连接时证明，现有 client credential lease 不实现调用时证明租约。

不从这组实验单独推出“第二份 Workload Quote 必不可少”“优于同等保证的内嵌实现”，也不直接比较 aDNS/ACLE-MCP 论文公布的延迟。静态 mTLS 是成本参照；原生 SPIRE 组合基线保留 Node、官方 unix/Docker、Broker、相同本地监测、关闭入口和应用权限。

接收审计的观察点是 ASGI 应用读取；无法扩展为模型消费、存储撤权或已交付明文擦除。UNKNOWN 需要解释证据缺口，不能折算为安全拒绝或零接收。

“未准入替换实例读取”与“监督失效后超过 Δ 的读取”分别报告。当前 E1 的 ADMITTED/UNKNOWN 观测不能单凭缺失准入材料证明新实例从未准入；该项保留 UNKNOWN 和实际已归属的候选读取，不从 Helper 冻结结果填充这一摘要槽。新鲜度也不扩展为应用存储防回滚、全局唯一实例或全局防分叉。

审计只观测，不参与 Argus 准入或业务授权；采集器不可用不停止业务。v2 接收判定包含故障前开始、停止界限后仍读取的请求体。若正常代理缓冲使在途场景无法建立，则该项为 NOT_RUN，不能推广双连接结果。默认单种子功能运行只用于 smoke；性能和正式配对统计按需启用。
