# 持续 Agent 任务与双机交付

当前运行口径对应飞书 revision **1329**，实现前代码基线仍是 `0d78ed0`，新增代码以交付 SHA/源码清单为准。变更及未落实内容见 [本轮核对](PAPER-ALIGNMENT-1329.md)，前轮建设见 [交付记录](IMPLEMENTATION-20260929.md)。下面所有真实业务、TDX、故障和性能结论均等待双机运行。

## 顺序

1. 并行推进 [E1 可达性审查](E1-REACHABILITY.md) 和 Full 单客户端无故障：初始化 A 规则 → 真实 Agent 查询/写入 B → 归档/非空提取 → 新会话后继任务检索。按 [任务操作](CONTINUOUS-TASK.md) 配置；先核对实际插件工具审计。E1 记录共同检查的实际拒绝阶段，不预设新容器可被旧代理到达。
2. Full 单客户端故障：独立固定时间表执行故障和恢复，保留卷；采集 [完整事实接收](FACT-RECEIPTS.md)、[准入原件](ADMISSION-ARCHIVE.md)、Provider Quote 计数及恢复分段。
3. 三客户端及 Full/native 配对：每个客户端一个独立 Gateway 和普通业务用户；按下面 suite 固定运行顺序。首轮 pilot 可只有 Full、一个种子；正式模板使用两组、故障/无故障、十个结构种子。
4. 用既有 E5/LoCoMo 入口分别测成本和只读记忆负载；不把它们并进持续任务评分。

## 任务协议

每客户端每阶段六个任务，三个阶段共十八个任务。每阶段前三个依赖历史项目规则，后三个依赖该阶段前三个的持久记录。前驱失败仍按原时间释放后继；执行器不提供前驱答案。事务段含参与判断的完整 ASCII 字段，receiver 跨分块识别后只输出摘要/长度/时间。

默认释放间隔 60 秒、deadline 120 秒、并发 1、等待队列 1、自动重试 0。fault 在 360 秒、recovery 在 720 秒；无故障组保留同样时间但 argv 为空。E4 不等待业务里程碑移动故障时间。两轮预实验后冻结参数；超时、排队失败、依赖缺失、未知写入均保留在原分母。

`memory_store` 返回 stored 不是提交成功。工具审计、commit/task 查询、archive、非空提取和后续检索分别留下结果。Agent 每任务新会话，autoCapture/autoRecall 都关闭。初始化规则允许使用已有 API 种子导入；B 事务的 memory_recall/memory_store 必须由 Agent 调用。评分器读取状态但不代写、不给 Agent 注入期望答案。

## 配对 suite

从 [continuous-suite.example.json](examples/continuous-suite.example.json) 复制输入。`continuous_configs[group][seed][condition]` 指向每次运行的受保护配置；完整配置见 [任务说明](CONTINUOUS-TASK.md)。每个配置使用相同逻辑客户端名、结构 seed、模型参数、调度与控制时刻，但各运行的 secret_seed、业务用户和初始数据独立。用户 key 只在已部署 Gateway 的受保护配置中引用。

每份 suite 只声明一种 `fault_kind`，当前连续服务试验复用 `helper-freeze`、`helper-crash`、`target-exit`，默认 helper-freeze，`fault_scope=shared_service`。两种 condition 的每份正式配置都带同值。分析核对实际故障 checkpoint 的 event、run、executed 和控制调用时间区间；缺失或不符保留任务结果，但不进入正式配对均值/差值。客户端局部停止继续用现有 fleet_fault 独立配方；它不提供持续新事实 E4 的局部注入，后者仍为 NOT_RUN。

suite 不创建用户或容器。复用现有 fleet/deploy/variants 生成组内独立身份、Entry、配置、目录；每次运行前安装其业务用户配置并重新登记变化的实例。不能把本轮新用户配置只填进实验 JSON 而不实际部署到 Gateway。组间、运行间不共用记忆；同一次故障恢复保留该次卷。

```sh
# 从 cczoo/agent-cc 执行，全部路径换成实际受保护路径
python3 experiments/argus/suite.py --config /secure/continuous-suite.json --output /secure/generated-continuous
python3 experiments/argus/runner.py prepare --config /secure/generated-continuous/suite.json --output /secure/evidence/continuous01
```

读取 `run-order.json`，将该项 run_id 用于 IP2 审计部署、collector、admission/fault 证据及 IP1 执行。按其固定随机顺序逐项部署；一个服务端口每次运行一个变体。不要一次执行未准备好用户和服务状态的全部 runs。

```sh
python3 experiments/argus/runner.py preflight --output /secure/evidence/continuous01 --role client --run-id RUN_ID
python3 experiments/argus/runner.py run --output /secure/evidence/continuous01 --role client --run-id RUN_ID
# 中断后：保留时间原点，只查询已有操作和收集证据，不继续释放任务或重做故障
python3 experiments/argus/runner.py resume --output /secure/evidence/continuous01 --role client --run-id RUN_ID
```

控制命令填写现有 `remote_acceptance.py` 故障、release，以及该变体 workload.py 恢复/登记/verify 等明确 argv。需要多步恢复时写一份固定操作脚本，保存其摘要，复用 `resume-launch`；不增加通用恢复服务。故障提交未知时不重发。重启命令返回 0 不等于重新准入完成。

## 收集与评分

每 run 原生目录是 `runs/RUN_ID/continuous/`。把 IP2 原始 collector 输出复制为其中 `receiver.jsonl`，按 [接收协议](FACT-RECEIPTS.md) 放入 `receipt-context.json` 及引用的 fault/admission 原件。原始应用读取和身份关联来自内核/受保护部署记录，请求头仅作运行关联。保存双方时钟偏差的测量；不能凭默认 0 假定跨机无误差。

```sh
python3 experiments/argus/runner.py collect --output /secure/evidence/continuous01
python3 experiments/argus/runner.py analyze --output /secure/evidence/continuous01
python3 experiments/argus/plot.py --output /secure/evidence/continuous01
```

输出 `statistics.csv`、`continuous-tasks.csv`、`analysis.json`、`report.md`、每项 `joint-result.json` 及可重生成 SVG/PDF。四格顺序是接收判据/任务结果；两个轴的 UNKNOWN、NOT_RUN 另列。所有计划任务进入任务分母；缺接收证据不会覆盖明确任务失败，未调用工具不计成功拦截。唯一事实、重复读取、完整故障窗口和 Δ 后读取在 receiver 子结果保留。Δ 默认 10 秒，是研究阈值。

`paired_full_minus_other` 在相同 condition 比组；`paired_fault_minus_control` 在同组同结构 seed 给出原始 fault−control 差值；`paired_client_fault_minus_control` 展示各客户端变化。共享服务故障的这张表不能称为局部连带损失。论文定义的完成率损失是 `(no_fault−fault)×100` 个百分点，仅对事前声明的局部未注入客户端计算；当前服务模板不产生此类样本。CI 使用完整独立配对块；缺配对保留数量。图表不填示意时间；恢复阶段缺证据时留空/UNKNOWN，恢复时长从实际命令开始计。

## 边界

审计是被动测量，不等待 ACK，也不阻塞业务。观察点是应用读取，不是模型消费、持久存储撤权或明文擦除。EAR 复核引用采集时生产 Trustee 的硬件判断，不等于今日重新 DCAP 评价。历史消融先做固定批准参考与可达性验证；没有可比较远程结果，不提供线上跳过历史开关，也不纳入 E4。

接收结果区分监督失效后的停止阈值、实例绑定失效和未准入替换实例读取。缺少某实例的 admission 材料只能说明资格未建立，不能单凭缺记录证明它未准入；对应指标保留 UNKNOWN。Helper 冻结后的超阈值读取是停止场景违反，不自动等于历史资格失效。Δ 内读取仍展示，实测最大延迟不当作理论上界。

论文中的 Attest-on-connect、Invocation lease 和在线 current-facts-only 均为 `proposed_not_run`，现有 `variants.py` 未提供这些组。TLS `new/reuse` 仅为连接负载模式。后续模式比较应保持同证据、政策和本地控制，分别改变评估时机与监督；本轮不实现这些扩展。

现有 x509pop 客户端部署可继续使用；其节点 TDX 证明仍是 NOT_RUN。两台主机支持 TDX 不自动表示每个角色都完成 TDX 证明。保留 CanReattest=false 及现行批准策略，不要求 TCB UpToDate。
