# Revision 1373：框架实验与双机交付

本轮依据飞书论文 revision 1373，将实验主线调整为实例准入、交付停止、身份复用和成本。以 `93b9ed13` 为修改前基线；远程执行使用包含本文的实际交付提交，并核对 `source-manifest.json`。代码、本地软件检查与远程验收分别报告。

| 实验 | 本轮实现与配置 | 主要结果 |
|---|---|---|
| E1 联合准入 | 复用 admission_trial、原件导出、E1-REACHABILITY | 真实允许/拒绝、共同当前事实检查、历史差异是否可达 |
| E2 失效与交付 | fault-trial 示例使用 `readiness=transport`，无需模型提取里程碑；保留新/旧连接各三次成功、旧连接未重建、在途首块读取条件 | 实际 ASGI 读取、关闭窗口、缺口 UNKNOWN；正常证书更新单列 |
| E3 复用与恢复 | IP1 Node 观测可显式导入 IP2 Provider 前后快照；Workload 观测直接使用本机 Provider | SVID、接收的证明样本与真实 Quote 生成分开；跨启动不相减 |
| E4 应用接入 | LoCoMo 历史导入后，新会话只读 QA；独立 Gateway 并发、固定释放/deadline、独立故障/恢复时钟 | 请求和端到端耗时、完成与注入、实际并发、恢复；F1 仅功能一致性辅助 |
| E5 分层成本 | E5 默认示例改为非空私有记忆查询、连接复用、1/3 客户端；控制面增加小范围计时 | Quote 生成均值、Trustee 请求 RTT、命令至就绪、TCP/TLS/API 与真实 Agent 任务分别报告 |

默认 E4 不再使用累计金额、路由和前驱事务。旧 `continuous*`、完整合成事实匹配和旧结果文件仍可重放、分析，属于可选历史机制负载，不是论文默认实验。

## 执行顺序

1. 同提交重建生产组件和 OpenClaw 插件；Full 单客户端随机事实与权限负例作 smoke。
2. 独立完成 E1/E2/E3，E2 不等待记忆 benchmark。先 Full，再只执行问题所需的隔离对照。
3. E5：单客户端 pilot 后冻结负载，先 1/3 实例；有容量再另加规模，new/reuse 分开。
4. E4：先单客户端 LoCoMo 无故障，再三个 Gateway，最后 Full/native × fault/no_fault。新用户、相同数据/模型/预算；先小样本 pilot，再冻结正式配对块。

E4 的固定时间从**全部历史初始化完成后的 QA 起点**计。每客户端单个 QA worker，不同 Gateway 可并发。超期任务保留在分母；中断后只查询已知操作，不补跑漏掉的题、不重新注入故障、不平移时间窗。完整题数来自固定 fixture，不能沿用旧 continuous 的每客户端 18 题。

配对入口 [suite.locomo-paired.example.json](examples/suite.locomo-paired.example.json) 引用各 run 的现有配置。它不另建部署平台、不创建业务用户；按 `run-order.json`，先安装该 run 声明的 Gateway 配置并正常准入，再运行选定 `run_id`。常规无故障套件 [suite.locomo.example.json](examples/suite.locomo.example.json) 保留。

## 观测边界

- 普通自动召回也记录 request ID、session/span、响应头与完整 body 耗时、失败阶段；不记录正文、提示词或密钥。HTTP 200 后截断属于请求失败。
- E4 的有效完成表示期限内有回答且观察到上下文注入，不代表回答正确或安全交付。接收安全仍使用 E2 的实例映射与实际读取。
- Quote 计时在真实生成调用边界，含失败及后来丢弃的调用；累计计数只给均值，不给单次 p95。Trustee 计时是含网络的请求 RTT，非纯验证 CPU。
- `time-ready-command` 只执行一次显式命令；命令后检查新 Helper 的有效 readiness，是保守的就绪观测成本，不是签发精确时刻。
- 生产认证、Helper/watchdog、实例绑定及应用权限不改变。保留 `CanReattest=false` 和当前批准策略，不加 `TCB UpToDate` 门禁。
- 不新增 current-facts-only、Attest-on-connect、Invocation lease。历史增量需要真实共同检查可达的反例；TLS new/reuse 不代表连接时重新证明。

双机详细步骤：[REMOTE-RUNBOOK.md](REMOTE-RUNBOOK.md)；可直接交给远程 Codex 的 [IP1 prompt](../../adapters/OpenClaw/spiffe_client/PROMPT-IP1.md)、[IP2 prompt](../../adapters/OpenClaw/spiffe_client/PROMPT-IP2.md)。结果填写 [RESULTS-FRAMEWORK.template.md](RESULTS-FRAMEWORK.template.md)。真实 TDX、模型、关闭时间和性能均待远程测试。
