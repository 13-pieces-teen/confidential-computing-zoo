# 六步行程工作项：持续读写、中断与继续

此场景复用 `continuous.py`、真实 OpenClaw `memory_recall` / `memory_store`、原始任务 journal 和独立 OpenViking receiver。配置仍为 `argus.continuous.v1`，增加 `scenario: "work-item-v1"`。省略 scenario 或使用 `ledger-v1` 保留旧 18 步协议。它没有新调度服务或新部署模式；故障与服务替换由受信管理员实施合法维护 / 故障操作。

每个客户端只有一个稳定 `work_item_id`，业务目标是把持续到来的私有行程约束保存在外部记忆，再从候选路线中选择满足全部已生效约束的最低成本行程；如果无可行路线，明确报告 `NO_FEASIBLE_ITINERARY`。每次使用新的 OpenClaw session。这里验证跨新会话的持久业务状态继续，不声称恢复同一模型推理进程。

## 固定协议

| 步骤 | 阶段 | 新输入 | 依赖与处理 |
|---|---|---|---|
| s00 | normal | 预算上限 | 回读外部记忆中的初始规则与路线候选，首次提交行程状态 |
| s01 | normal | 步行分钟上限 | 回读 s00，形成故障前已确认检查点 |
| s02 | pause | 新预算提案 | 保留已确认正常输入，独立尝试真实读写；不重试写入 |
| s03 | pause | 新步行上限提案 | 纳入此前所有已确认输入；s02 未确认时不提交新写入，保留未派发及截止时间 |
| s04 | recovery | 通行分钟上限 | 恢复命令完成及实际入口预检后，查询原 session / task / archive；任何未知写入未解决则不派发新写入 |
| s05 | recovery | 室内停靠数量要求 | 回读全部已持久化事实并重算所有约束，完成或明确报告无可行行程 |

每步发布一个唯一完整 `ARGUS_FACT_V1` 接收帧，以及独立的 `ARGUS_PROPOSAL_V1` 业务封套。Proposal JSON 明确包含 `schema=argus.proposal.v1`、`work_item_id`、`fact_id`、`step_index`、`constraint_key`、`unit` 和 `value`：预算单位 `cent`，步行 / 通行 `minute`，室内停靠 `count`。封套原文与接收帧、派生 `Decision` 一起保存；后续按原始 Proposal 类型和顺序重算，不能从错误旧 Decision 猜测。Proposal 必须与同一记录内接收帧的 fact_id 和数值一致。规范化 JSON 的哈希用于观测关联，不是密码学认证。

`prepare` 按计划步骤检查约束状态；提案与该约束的前值相同时直接报错，且不发布 fixture、manifest 或运行状态。预实验先校验计划种子，再冻结四条件共用的种子与生成规则；正式样本不按运行结果筛选。该检查保证每步改变约束值，不要求每步都改变最优路线。

成功步骤恰好一次 `memory_store`，使用唯一的原 session ID，不把模型答案写回作为控制器补偿。controller 初始只写路线规则；用户更新全部由真实 Agent 工具处理。任一前序写入结果未知时，所有阶段均禁止提交新的写入，只查询原 session / task；不通过重发消除未知。此规则也覆盖 s02 未知后的 s03。后继步骤保留原计划及截止时间，未确认时记录 `PRIOR_WRITE_UNRESOLVED`。

调度每阶段 2 步，故障命令位于 `2 × release_interval_s`，恢复命令位于 `4 × release_interval_s`。pause 长度至少覆盖三个实验 stop budget。固定计划不因回答速度移动，仍是一客户端一个活动步骤及一个排队步骤，deadline / 队列失败保留在六步分母。

故障期明确拒绝的提案**不会自动补写**。因此完整任务可能失败，同时可以在已确认状态上部分继续。所有已确认持久化的完整 Proposal 都保留，包括当步 Decision 算错的输入；后续根据真实回读的原始提案重算约束，不沿用错误旧答案。后续查询失败只追加观测，不抹掉之前已确认的提交；真实 recall 缺少必要事实帧或 Proposal 时仍不能通过业务评分。

## 证据与结果 contract

- `manifest.facts`：`task_id=step_id`、`fact_index`、稳定 `work_item_id`、`constraint_key`、`proposal_sha256`、完整事实长度 / hash 与计划 offset。私有规则 / 取值 / 标准答案保留在受保护 fixture / state。
- `result.steps`：计划 `planned_at_ms`、排队 `offered_at_ms`、调用 intent `dispatch_started_at_ms` 和独立 `released_at_ms`。实际释放来自 Gateway 的 `release_receipt`，其 source 为 `gateway_agent_dispatch`、boundary 为 `openclaw_cli_input`，包含 task / fact / work-item / session 关联及 prompt hash。缺少该 receipt 时实际释放为 null；计划 / offered / intent 都不能冒充实际释放。这是 CLI 输入边界，不是网络接收、模型 provider 接收或持久提交。
- `proposal_persisted` / `committed`：完整事实帧、类型/顺序/单位正确的原始 Proposal 与真实 session 提交、非空抽取、archive 的持久证据；与 Decision 正确性分开。只存帧或 Decision 不算 Proposal 已确认。每条提案报告 `CONFIRMED` / `REJECTED` / `UNKNOWN` / `NOT_DISPATCHED` / `INVALID_PERSISTED_INPUT`。`NOT_DISPATCHED` 表示控制器尚未提交 Agent 请求。已派发后，空白 / 部分工具日志即使配合已完成答复，也不能证明未调用 store；目前 Docker 日志没有完整审计结束证明，因此这种情况保持 `UNKNOWN`，阻止所有后继提交。`NOT_ATTEMPTED` 保留给未来能够独立证明完整审计且确实未调用 store 的情况，目前采集器不输出该判定。`INVALID_PERSISTED_INPUT` 表示已经写入但原始输入契约不满足。原 task ID 可查询但不可重发。
- 工具审计增加 `input_proposals` / `output_proposals`，只包含关联 ID、步骤索引和规范化哈希，不导出私有数值。Gateway 预检要求部署带此能力的适配器，旧构建不能静默进入正式任务。
- `joint-result.json` 与 `continuous-tasks.csv` 同时报告 `planned_phase`、`actual_dispatch_phase`、`actual_tool_phases`；`continuous-tool-events.csv` 保留实际工具事件。依据真实 fault checkpoint 与 recovery command 分类，时钟不确定区间重叠记为 `boundary`，缺钟差范围记为 `UNKNOWN`。`after_recovery_command` 不等于已重新准入或恢复完成。
- 步骤 `task_result`：独立核验完整期望约束 / 路线 / 状态、deadline、真实工具、必要前驱事实出现在 recall 输出、完整当前事实输入、Decision archive。正确模型回答不能替代 receiver oracle，也不能替代实际前驱 recall。
- `work_items[].complete_task_result`：所有六个必需提案均已生效、全部当步业务结果及最终约束正确才 PASS。未生效 / 未派发提案显式列在 `unapplied_required_proposals`；未解决 UNKNOWN 或不完整窗口不能 PASS。`work_items[].result` 采用此完整任务口径。
- `work_items[].continuation_result`：在全部已确认输入上正确继续的结果。它可能 PASS，而完整任务 FAIL / UNKNOWN；两者分开报告，不把部分继续称为完成全部任务。
- `receipt_result` 与 `legal_recovery_result`：原始任务结果未接独立证据时保持 UNKNOWN。联合分析复用 `fact_receipts` 的准入原件、已核验读取实例与独立任务评分，在同一 run / client / step / fact 上确认首次正确合法继续，写入 `joint-result.json` 的工作项 `legal_recovery_result`、`legal_recovery_evidence` 和 `recovery_intervals`。记录准入→首次合法读取、首次读取→正确继续及恢复命令→正确继续的时间区间；缺任一关联保持 UNKNOWN，不把命令 exit 0 或 `/health` 当作准入。首次合法继续也不会改变六步完整完成判定。
- 恢复继续须由当前步骤实际 `request_ids` 中的请求取得合法读取，且 `answer_at_ms` 本身晚于该读取；缺少关联或答案产生在恢复前时，较晚的 archive 确认不能将其升级成恢复后继续。跨源事件顺序与区间均采用每端钟差界限 `u` 合成的保守 `2u` 门限，重叠或缺少钟差保持 `UNKNOWN`。通过这些条件后，正确继续时间使用 `goal_confirmed_at_ms`：答案返回与独立 archive 确认两个观测中的较晚时间，包含确认探测开销，是保守完成观测。`answer_at_ms` 独立保留，确认时间不能早于答案时间。联合结果另导出 `continuous-work-items.csv`，完整完成、最终继续、首次合法继续及恢复区间分别列示。

`continuous_analysis.py` / `analysis.py` 输出 `continuous_work_item_completion_rate`、`continuous_work_item_continuation_rate` 和 `continuous_step_success_rate`。一客户端是一业务任务、六个步骤；旧 `continuous_task_success_rate` 在该场景仍仅表示步骤率，不能引用为完整业务任务完成率。统计单位为独立运行或完整配对块。Full/native、fault/no-fault 必须使用同一 scenario、结构 seed、模型、权限、时序与预算；配对 digest 含 scenario，混用旧 ledger 与新 work item 被拒绝。

## 运行入口与验证状态

从 [examples/work-item.example.json](examples/work-item.example.json) 复制配置，填写真实 Gateway、普通私有用户、固定模型及准确 SPIFFE 身份。先单客户端 no-fault pilot，使用全新 protected 输出目录：

```bash
python3 continuous.py prepare --config /protected/trip.json --output /protected/trip-run
python3 continuous.py preflight --config /protected/trip.json --output /protected/trip-run
python3 continuous.py run --config /protected/trip.json --output /protected/trip-run
python3 continuous.py analyze --config /protected/trip.json --output /protected/trip-run
```

正式 fault 配置由管理员填写实际 fault / legal recovery 命令 argv，保留运行内卷和原 journal。`resume` 只对原操作查询 / 审计，不重新派发 Agent、写入、故障控制或移动时间窗口。当前论文配置使用 [work-item-paper-suite.example.json](examples/work-item-paper-suite.example.json)，其 profile 强制单客户端、`work-item-v1` 和 Full/native × fault/no-fault 四条件。复制单任务示例形成四个独立配置，每个运行使用全新私有用户与 secret_seed；同一配对块固定 structure_seed、模型、时序、权限与预算。配置引用的部署和真实控制命令需要先完成，示例不是运行结果。

使用现有 `suite.py --config ... --output ...` 生成并随机排列四条件；`planned_tasks` 为六步，缺结果仍保留正确分母。先完成正常单客户端真实轨迹及故障恢复预实验，再冻结配置后正式运行。旧 ledger / 多客户端支持保留，论文默认不展开这些维度。

模型预检记录选择的模型、模型配置哈希及能读取到的配置采样字段。配置字段不代表提供方实际应用了参数：`sampling_observation` 区分 `CONFIGURED_NOT_EFFECTIVE_VERIFIED` 和 `PROVIDER_DEFAULT_UNVERIFIED`，`effective_parameters` 仍为 UNKNOWN。当前不声称已锁定或核验提供方采样；正式报告保留这一限制。每步仍记录实际返回的 provider / model 并检测名称不符。

本地 focused fixture 覆盖准备阶段的同值提案拒绝、原始 Proposal 存取与类型校验、从实际存储文本重算且不读取私有 fixture 的类型/答案、所有阶段未知写入阻止后继提交、完整完成与正确继续分离、缺少 typed recall 不通过、计划与实际事件阶段分离、合法恢复证据错配保持 UNKNOWN。真实模型、远端 TDX、合法重新准入、停止时序与 receiver 联合结果仍为 **NOT_RUN**。正式运行前完成一条真实输入→工具→传输→应用完整读取→持久提交→后继回读链，并验收错误旧 Decision 可由原始提案纠正、缺失必要回读不能判为正确继续。
