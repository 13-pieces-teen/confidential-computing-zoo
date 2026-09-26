# 论文主张—实验—证据

| 初稿主张 | 实验和工具 | 需要保留的原始证据 | 当前边界 |
|---|---|---|---|
| 实例级身份和准入架构 | E1、E4；fleet + exact AuthZ + business | 注册选择器、当前 target、证书 URI、实际 API 有效用户、跨用户读/搜/问结果 | 本地真实 mTLS/固定版本源代码测试；跨机业务 NOT_RUN |
| 历史与当前实例联合核验 | E1；production Trustee/Verifier + admission_trial + history_diagnostics | 接受日志与 nonce、策略/构建摘要、Rekor 签名历史、目标 launch/container/image、当前运行上下文；完整外层审计另需 Quote/REPORTDATA/EAR 原件 | 区分真实 ADMITTED、本地绑定拒绝、UNKNOWN；现有 CLI 未完整导出外层原件，相关实时负例保留 NOT_RUN |
| 入口与实例生命周期联动 | E2；fault_trial + timeline + remote_acceptance + receiver_audit | 故障前双连接、在途首块读取、业务里程碑、readiness/入口观测区间、应用请求读取与客户端响应分块、覆盖区间 | 检测使用明确代理观测；本地实际分块 mTLS 不推导远程 systemd 窗口 |
| 已有效身份的复用和重新准入区别 | E3；lifecycle_trial + node_attestation_observe + lifecycle_evidence + resume-launch | 分开的 Node/Workload Quote 计数来源、SVID serial、进程起点、launch ID、独立创建事件、连续非空业务 trace | CanReattest=false；只读等待自然轮换。Workload Quote 无实际 counter 时 UNKNOWN，不能由证书变化推算 |
| 多 Agent 使用可信共享服务 | E4；fleet_business + fleet_fault + locomo_run | 六阶段随机事实、实际注入、跨用户 API 负例、局部/共享故障、显式恢复的新会话回答；LoCoMo 另存任务成绩/覆盖 | 合成机制与 LoCoMo-derived QA 分开；COMPLETE 不是安全 PASS；不是直接 Agent–Agent 协作 |
| 增量机制的成本和必要条件 | E5；五组 variants、load_fleet、resources、analysis | 生效配置/二进制、首次就绪、稳态 HTTP、真实业务任务、原始请求/资源序列、采集 on/off | 对照需要相同模型、数据、权限、预算、负载；NOT_RUN 不得当实测 |

每条论文结果需同时指向源码/构建 manifest、运行 manifest 和对应原始证据；运行版本不能只写分支名。配对表保留缺失组、失败组与容量不足的原因。

不从这组实验单独推出“第二份 Workload Quote 必不可少”“优于同等保证的内嵌实现”，也不直接比较 aDNS/ACLE-MCP 论文公布的延迟。静态 mTLS 是成本参照；原生 SPIRE 组合基线保留 Node、官方 unix/Docker、Broker、相同本地监测、关闭入口和应用权限。

接收审计的观察点是 ASGI 应用读取；无法扩展为模型消费、存储撤权或已交付明文擦除。UNKNOWN 需要解释证据缺口，不能折算为安全拒绝或零接收。

审计只观测，不参与 Argus 准入或业务授权；采集器不可用不停止业务。v2 接收判定包含故障前开始、停止界限后仍读取的请求体。若正常代理缓冲使在途场景无法建立，则该项为 NOT_RUN，不能推广双连接结果。默认单种子功能运行只用于 smoke；性能和正式配对统计按需启用。
