# IP1 -> IP2: E4 六轮正式结果解读与汇总(结果整理阶段交付)

schema: argus.e4.ip1-formal-results-analysis.v1
status: E4_RESULTS_EXPLAINED_AWAITING_IP2_REVIEW
date: 2026-10-09
scope: 解释既有冻结结果,不新增实验、不重跑 E4、不改判定

## 1. 验证器版本与原件冻结

- 实际运行版本: git HEAD `3cf25a87a2ba1ab4f6ff4ac0821ca515570fa5c7` +
  工作区 as-run 状态(执行时即此工作树,未再变更)。关键验证器文件
  sha256(工作区实际状态):

| 文件 | sha256 |
|---|---|
| continuous.py | `eff14fb46cc3f7851402c25593620264e1f4b2fdd75c1139bcc4a47abf477720` |
| analysis.py | `7540e1e47d8042b0d81b6e9b9ec81aa1af0cfc79941c222230ae3179a3c21850` |
| continuous_analysis.py | `9d52107dfdf4b836bc40ad2bedfaba5570c4134523159b00b8929dfa7d49fd63` |
| continuous_gateway.mjs | `d09b3a62c64939528cd90911f978bcd7b49e74e64b7eea58fe6270c83155d254` |
| continuous_work_item.py | `cea1608bfd3bc59fb3a930490b15b66e6ce655fd741cb098290dff3c2d4f196a` |
| continuous_proposal.py | `8d43895bfa7f1c3806c53a5d6869fffe5f8c95c761bc5b33a6a989afe16a2503` |
| locomo_run.py | `a52ece66e0c74023ae50b6bc2b722c9d2399aae93d693bdfd398530ab97d08b4` |
| step.py | `570e1108dc44afd4c271fef6b292fb0d185360f6a78d66061ce81abdd37aeead` |

- 六轮冻结原件(仅读,未改动): `/secure/e4-formal/runs/<run_id>/` 下
  `continuous/result.json`、`continuous/state.json`、
  `continuous/events.jsonl`、`continuous/manifest.json`、
  `continuous/private-fixture.json`、根 `step-result.json`、
  `operation-id.json`;配置文件 `/secure/e4-formal/full-{101,102,103}-{recovery,healthy}.json`。
- 六份已交付证据 JSON 与冻结原件逐字段交叉校验 **全 PASS**(result /
  counts / protocol_digest / manifest_sha256 / completed_at / step_outcomes /
  controls argv 七项全匹配),IP1 侧未发现可证实的解析或关联缺陷。
- 原始结果、模型、提示词、验收规则均保持冻结;本文件只做解释与汇总。

## 2. 回执中 PASS / FAIL5·UNKNOWN1 的字段、对象与分母

判定代码: `continuous.py` `result_for()`(工作区版本,下同)。

### 2.1 `result: PASS` —— 执行窗口测量判据,非任务正确性

```python
verdict = 'PASS' if complete and controls_ok and len(state['controls']) == 2
          and not model_mismatches else 'UNKNOWN' if start is not None else 'NOT_RUN'
```

- 对象与字段: ① `state.phase == 'complete'`(窗口走完);
  ② `state.controls` 恰有 2 条(fault/recovery 双槽),每条
  `status == 'completed'`(fault 轮)或 `'no_fault'`(healthy 轮)且
  `returncode == 0`;③ `result.json.model_mismatches` 为空
  (逐步 `model`/`provider` 与配置 `model_settings.model` 无差异)。
- 分母: 无比例分母,是四个条件的合取。
- 范围字段 `evidence_scope: "execution_window_not_task_or_receipt_success"`
  明确: **PASS 不表示任务成功,也不表示回执/接收成功**。

### 2.2 `counts: FAIL 5 / UNKNOWN 1` —— 六步任务结果,分母 6

- 分母 `planned_tasks = 6`(每 run 单客户端 6 个计划步骤
  s00–s05);`counts` = 各 `task_result` 值的计数。
- 六轮逐步骤完全同构:

| step | task_result | reason | 已派发 | committed | 前置条件 |
|---|---|---|---|---|---|
| s00 | **FAIL** | ANSWER_INCORRECT | 是(agent 有回答) | UNKNOWN | —(首步) |
| s01 | **UNKNOWN** | CHECKPOINT_UNCONFIRMED | 否 | False | prerequisite_unknown |
| s02 | FAIL | CHECKPOINT_UNCONFIRMED | 否 | False | prerequisite_uncommitted |
| s03 | FAIL | CHECKPOINT_UNCONFIRMED | 否 | False | prerequisite_uncommitted |
| s04 | FAIL | CHECKPOINT_UNCONFIRMED | 否 | False | prerequisite_uncommitted |
| s05 | FAIL | CHECKPOINT_UNCONFIRMED | 否 | False | prerequisite_uncommitted |

- s00: agent 回答了(dispatch_attempted=True),但答案是
  **memory_store 失败报告文本**而非决策 JSON,`parse_answer(answer) != expected`
  → FAIL / ANSWER_INCORRECT(`score_step`);无 store 证据 → `committed=UNKNOWN`
  → s00 提案状态 UNKNOWN。
- s01: checkpoint 指向 s00 提案,状态 UNKNOWN →
  `finish_without_dispatch(..., 'UNKNOWN' if status==UNKNOWN else 'FAIL')`
  → UNKNOWN。
- s02–s05: checkpoint 指向前驱提案,未确认(not True)→ FAIL。
- 所以 FAIL5 = s00 + s02–s05,UNKNOWN1 = s01。没有 NOT_RUN。
- `attempted_tasks: 0` 是保守正向计数口径:只有正向运输证据
  (request_body 记录)才计 attempted=True;s00 的 attempted 记录为
  UNKNOWN(events.jsonl `task_observed`),不计入。这不是解析缺陷。

### 2.3 work_items 三轴(每 run 一工作项,六轮同值)

| 轴 | 值 | 依据(代码) |
|---|---|---|
| complete_task_result | **UNKNOWN** | `unknown or not complete or any step UNKNOWN` → UNKNOWN;`unresolved_proposals=['s00']`(s00 提案状态 UNKNOWN 且 s01 task UNKNOWN),全部 6 提案在 `unapplied_required_proposals` |
| continuation_result | **UNKNOWN** | `unknown` 非空 → UNKNOWN(存在未解决提案,无法判定正确继续) |
| legal_recovery_result | **UNKNOWN** | runner 不填此轴;需联合分析(fact_receipts 准入原件 + 已核验读取实例 + 独立任务评分)在同 run/client/step/fact 上确认才可写 PASS,缺任一关联保持 UNKNOWN |
| receipt_result | **UNKNOWN** | 同上,接收侧独立证据未接入 |

WORK-ITEM.md 口径:complete_task_result 仅当全部六个必需提案生效且每步
业务结果与最终约束正确才 PASS;continuation_result 可与完整任务分开报告;
命令 exit 0 与 `/health` 不等价于准入。以上均为运行器冻结判定,未被本文件改动。

## 3. 六轮汇总表

| run | 证据关联/接收判据(IP2 侧) | complete_task_result | continuation_result | legal_recovery_result | 已确认输入(五腿)与 s00 实际回读 | FAIL/UNKNOWN 原因与原件 |
|---|---|---|---|---|---|---|
| R1 seed-101 fault `eeacfeaf` | 34/34 请求,2507 COMPLETE,corr `ddfdd1ad…`,collector intervals_only | UNKNOWN | UNKNOWN | UNKNOWN | 五腿: session `e74eb5f0-ed93…` commit_count=1 task_status=completed memory_write=4 find 4 记忆 collective_four_value_hit=true (project 67cc1bdcc876); s00 回读: recalled=false, tool_call_count=0, transport_evidence=[], attempted=UNKNOWN, committed=UNKNOWN, decision_committed=false, deadline_missed=false | s00 FAIL ANSWER_INCORRECT;s01 UNKNOWN CHECKPOINT_UNCONFIRMED;s02–s05 FAIL CHECKPOINT_UNCONFIRMED。原件: `…/eeacfeaf/continuous/result.json` steps[0..5] + `continuous/events.jsonl` task_observed s00(派发期观测措辞 ①,见 §3 注) |
| R2 seed-101 healthy `861c0d0a` | 38/38 请求,2507 COMPLETE,corr `e4d98575…`,collector intervals_only | UNKNOWN | UNKNOWN | UNKNOWN | 五腿: session `b978b125-7532…` commit_count=1 task_status=completed memory_write=2 find 2 记忆 collective_four_value_hit=true (project 941952b7ed97); s00 回读同 R1(派发期观测措辞 ②) | s00 FAIL ANSWER_INCORRECT;s01 UNKNOWN CHECKPOINT_UNCONFIRMED;s02–s05 FAIL CHECKPOINT_UNCONFIRMED。原件: `…/861c0d0a/continuous/result.json` steps[0..5] + `events.jsonl` task_observed s00 |
| R3 seed-102 healthy `94e9cd04` | 39/39 请求,2507 COMPLETE,corr `e050dc6f…`,collector intervals_only | UNKNOWN | UNKNOWN | UNKNOWN | 五腿: session `9876d228-8122…` commit_count=1 task_status=completed memory_write=2 find 2 记忆 collective_four_value_hit=true (project b01f1f46ff85); s00 回读同 R1(派发期观测措辞 ③) | s00 FAIL ANSWER_INCORRECT;s01 UNKNOWN CHECKPOINT_UNCONFIRMED;s02–s05 FAIL CHECKPOINT_UNCONFIRMED。原件: `…/94e9cd04/continuous/result.json` steps[0..5] + `events.jsonl` task_observed s00 |
| R4 seed-102 fault `cd119c89` | 34/34 请求,2507 COMPLETE,corr `d28e82f4…`,collector intervals_only | UNKNOWN | UNKNOWN | UNKNOWN | 五腿: session `ee7c3e42-0733…` commit_count=1 task_status=completed memory_write=3 find 3 记忆 collective_four_value_hit=true (project 44b819f90ef0); s00 回读同 R1(派发期观测措辞 ④) | s00 FAIL ANSWER_INCORRECT;s01 UNKNOWN CHECKPOINT_UNCONFIRMED;s02–s05 FAIL CHECKPOINT_UNCONFIRMED。原件: `…/cd119c89/continuous/result.json` steps[0..5] + `events.jsonl` task_observed s00 |
| R5 seed-103 fault `7998d974` | 34/34 请求,2507 COMPLETE,corr `5e4bda26…`,collector intervals_only | UNKNOWN | UNKNOWN | UNKNOWN | 五腿: session `89b0b7eb-0a73…` commit_count=1 task_status=completed memory_write=3 find 3 记忆 collective_four_value_hit=true (project fe96eba346f1); s00 回读同 R1(派发期观测措辞 ⑤) | s00 FAIL ANSWER_INCORRECT;s01 UNKNOWN CHECKPOINT_UNCONFIRMED;s02–s05 FAIL CHECKPOINT_UNCONFIRMED。原件: `…/7998d974/continuous/result.json` steps[0..5] + `events.jsonl` task_observed s00 |
| R6 seed-103 healthy `a7193641` | 37/37 请求,2507 COMPLETE,corr `07ca0269…`,collector intervals_only | UNKNOWN | UNKNOWN | UNKNOWN | 五腿: session `5df1d492-7238…` commit_count=1 task_status=completed memory_write=2 find 2 记忆 collective_four_value_hit=true (project 9d062d36d62b); s00 回读同 R1(派发期观测措辞 ⑥) | s00 FAIL ANSWER_INCORRECT;s01 UNKNOWN CHECKPOINT_UNCONFIRMED;s02–s05 FAIL CHECKPOINT_UNCONFIRMED。原件: `…/a7193641/continuous/result.json` steps[0..5] + `events.jsonl` task_observed s00 |

说明:
- 「证据关联/接收判据」列来自 IP2 终版 closure 回执(请求 enter/end 全命中、
  COMPLETE 覆盖区间、correlation SHA、collector finalize)。**请求全关联与
  collector COMPLETE 是接收/覆盖判据,不替代任务正确性**;任务正确性看
  complete_task_result(六轮均 UNKNOWN,未达 PASS 口径)。
- 「已确认输入」= 控制侧五腿初始化(六轮全部 confirmed)——控制器 seed 写入
  成功;而 s00 失败发生在**派发业务 agent 的 memory_store 环节**(agent 侧),
  与控制侧初始化是不同链路。五腿字段名即冻结原件用名:
  `ov_session_id` / `commit_count` / `task_status` / `memories_extracted.
  memory_write` / `find.collective_four_value_hit`。
- 注(派发期观测措辞,非冻结原件字段):六轮 s00 模型应答均为
  **memory_store 失败报告文本而非决策 JSON**(冻结原件以
  ANSWER_INCORRECT + tool_call_count=0 + transport_evidence=[] +
  write_outcome=UNKNOWN 记录);具体措辞为执行期容器内观测——
  ① SVID 验证错误、② sessionId 参数问题、③ 按指示不重试、
  ④ sessionId 无效或配置问题、⑤ sessionId 格式不正确、⑥ 返回错误信息。
  执行期容器按契约逐轮轮换重建,模型应答原文不保留在冻结原件中;
  若 IP2 需要,可基于 result.json `steps[0]` 回读字段与 receiver 原件
  另行核对(判定不变)。
- 原件引用路径前缀 `/secure/e4-formal/runs/e4p1-<rid前8位>…/continuous/`。

## 4. 逐步明细与共同首败环节

六轮逐步结果完全一致(见 §2.2 表)。共同首败环节:

**s00 的业务 agent memory_store 调用失败**,六轮无一例外。六轮 s00 的模型
答案均为失败报告文本(而非决策 JSON)——冻结原件判定依据:
`task_result=FAIL, reason=ANSWER_INCORRECT`;回读字段
(events.jsonl `task_observed` s00,六轮同):recalled=false、
tool_call_count=0、transport_evidence=[]、decision_committed=false、
deadline_missed=false、attempted=UNKNOWN、committed=UNKNOWN、
store_tool_failed=false、write_outcome=UNKNOWN。派发期观测的失败报告
措辞分为两类:SVID 验证错误(R1)、sessionId 参数/格式/无效类(R2–R6),
具体见 §3 注(措辞为执行期观测,非冻结原件字段)。

由此形成的确定性链条(healthy 与 fault 完全相同):
1. s00 store 失败 → 答案错误 → ANSWER_INCORRECT(FAIL),s00 提案 UNKNOWN;
2. s01 checkpoint(s00 提案)UNKNOWN → 不派发 → UNKNOWN;
3. s02–s05 checkpoint 前驱提案未确认 → 不派发 → FAIL;
4. 六提案全部未生效 → complete_task_result / continuation_result 均 UNKNOWN。

fault 与 healthy 无差异的原因:s00 回答时点(+95.4s ~ +145.6s,全部在
180s deadline 内)早于 fault 控制时点(+180s)——任务失败链在 fault 注入前
已经闭合,fault 从未与任务流交互。fault 轮的真实控制(R1/R4/R5 rc0、
argv b0f4d271/06123d651、时点精确)与 healthy 轮双槽空 argv(4f53cda1)
均已按契约落地,不影响也不改变任务判定。

## 5. 缺陷排查结论(按本轮约定)

1. IP1 侧:六份证据 JSON 与六轮冻结原件逐字段交叉校验全 PASS
   (result/counts/protocol_digest/manifest_sha256/completed_at/steps/
   controls),未发现可证实的解析或关联缺陷;`attempted_tasks=0` 为保守
   正向证据计数口径(见 §2.2),非缺陷。
2. IP2 侧已知问题(已自愈,无需重判):run 6 关联初稿 ISO-to-epoch 偏移
   产生空窗口,被 IP2 断言拒绝后按严格 UTC 从 receiver 原件重算,接受
   哈希 `07ca0269…`(IP2 closure 已记录)。原判定保留。
3. 若后续发现新的可证实缺陷:原判定保留,交 IP2 核对;修复后必须对
   **全部六轮**统一离线重算,禁止只修失败样本(本轮约定)。
4. 原始结果、模型、提示词与验收规则冻结,本文件未做任何重打分。

## 6. E1 配合状态(不变更项)

- 客户端与控制端保持现状:容器 `argus-oc-paper01full` 运行中,身份
  `e4p1-s103-healthy`;IP2 侧五个 workload unit、1943、exact-run receiver
  均正常(IP2 closure 终态)。
- 不重建 Gateway、不重跑 P0/A3、不主动触发新准入。
- 收到 E1 窗口后,IP1 将以现有实时凭据与探针配合采证;需要时按既有
  trusted-channel 流程推进。
