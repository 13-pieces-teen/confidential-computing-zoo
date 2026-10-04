# 填数口径

`results.json` 是唯一入口。它是已有原件的展示索引，保留 `run_id`、`evidence_ref`，不替代双机原件。不向其中写入 token、私钥、API key、真实业务私密上下文。

## 状态与缺失

`pending` 是尚未填写的计划槽位；`recorded` 表示原件已经核对；`unknown` 表示有原件但结论未知，允许保留其中明确观测到的数值；`not_run` 表示未执行，填写说明来源。`null` 为缺失，不得填零。渲染器不预设 Full 胜出，也不自动把失败/未知改为通过。

每一正式槽位实际对应同版本、同策略和配对条件下的一轮。`pair` 仅是配对编号；`run_id` 才是原件标识。历史 A3 的 54 条 HTTP 腿不填成 54 轮或三轮新正式样本。若实际采用的冻结种子或组数变化，应新建一份设计文件，不用已看结果挑选槽位。

## admission / history

`admission` 的 A/B/C 是合法准入、成功结果已保存而记录尚未确认、原操作确认后的独立准入。`decision` 为 ALLOW/DENY/UNKNOWN，`first_layer` 用 common/provider/evidence/remote/final 或实际明确层；未到达的后继阶段不标为 DENY。`business_access` 为 PASS/FAIL/UNKNOWN，`current_checks` 和 `record_state` 记录实测事实。

Table A 汇总每阶段各组的 A/D/U 计数和实际 k/3；未运行/未填写仍列出。`history` 是独立离线区，`source_kind` 明确 archive 或 signed_fixture；`verdict` 是该验证器的真实判定。更大系统的 Quote、新鲜度等外层检查不由一个离线历史案例代言。

## receiver

`fault` 为 config/freeze；相应对照为 full/no_close 或 full/no_watchdog。`backend_alive`、`coverage` 来自独立观测。正文图计数为故障后首次释放且读取的去重合成事实，其他类型填 `late_pre_fault_facts`、`ambiguous_facts`。

时间以每轮故障的同一对齐时刻为零，单位秒。`detected_s`、`closed_s` 来自事件原件；`closure_observation` 为 CLOSED / RIGHT_CENSORED / UNKNOWN。未在窗口内观察到关闭时，`closed_s=null`，填写实际 `window_end_s` 和 RIGHT_CENSORED。有限窗口内未关闭不画成 0 秒。

`trace` 的每个元素为：`{"t_s": <秒>, "new_facts": <累计整数或null>, "coverage": "COMPLETE或GAP"}`。时间递增；覆盖段内累计量单调。缺口用 GAP＋null 断开曲线。完整窗口的 `new_facts_read=0` 需要 COMPLETE 覆盖；不完整窗口中明确发生的读取仍保留。

故障前的实测零读取可填 0；不允许因为没有日志而自动补零。图中的轨迹是原始独立运行，不平滑、不插值弥补缺口。

## tasks

Full 的 healthy/recovery 两条件，每条件三个完整六步任务。`steps` 各项 `outcome` 为 CORRECT / INCORRECT / REJECTED / TIMEOUT / UNKNOWN / NOT_DISPATCHED，实际 `phase` 为 normal/fault/recovery；`proposal_state` 使用 CONFIRMED / REJECTED / UNKNOWN / NOT_DISPATCHED。同一列的计划步骤不意味着工具必在同一阶段执行。

`confirmed_proposals` 为全部已确认原始 Proposal 数，`confirmed_readback` 为真实回读数量；`readback_status=VERIFIED` 要有独立核对原件。`continuation` 与 `complete_task` 分别填 PASS/FAIL/UNKNOWN/N/A。所有已确认提案真实回读只是继续成功的必要条件，仍须由任务 oracle 验证约束与实际后继决策。六步完整成功要求全部必需更新及判断达成。

`read_s` 是重新准入至首次合法记忆访问，`continue_s` 是同一重新准入至正确继续。两项共用起点而非可相加的阶段；正常条件 N/A，未知保持 null。不能用 health 就绪时间代替真实 memory 工具结果。

## cost / reuse / stage_costs

`cost` 连接模式 new/reuse 由真实 transport 验证。`p50_ms`、`p95_ms` 在每轮正常且业务有效的允许请求中计算；每轮有 `attempted = valid + rejected + failed + timed_out + invalid`，分类互斥，排队截止时的未完成请求按冻结协议归类。分位数和失败统计共用同一测量窗口；预热不混入。

资源 `cpu_one_core_pct` 以单核 100% 归一（可以超过 100%），`rss_mib` 为明确组件集合的实际 RSS 口径；`component_scope` 写清主机、进程/实例分段与聚合规则，避免把两机 PID 或共享页无解释地相加。完整样本之外不外推资源开销。

`reuse` 填每组实际健康窗口长度和实际计数：node_quotes、workload_quotes、subscriptions、node_svids、workload_svids、new_admissions。SVID 字段是窗口内不同证书序列数；新 Quote 与换证分别计数。native 中没有定制 Workload Quote 是可记录的实测零，未采到数据则是 null。

`stage_costs` 初始为空，有原件后追加：

```json
{
  "status": "recorded",
  "run_id": "原始运行标识",
  "evidence_ref": "原件索引与摘要",
  "phase": "admission",
  "arm": "full",
  "stage": "quote",
  "duration_ms": null,
  "environment": "实际主机、构建、操作标识"
}
```

该示例展示字段，不是一条有效数值样本。`phase` 为 admission/recovery，`stage` 为 quote/appraisal/identity/ingress/first_access/deliberate_hold。同一阶段的一组直接实测耗时作中位数与范围展示；不用分段 p95 相加。`deliberate_hold` 独立成行。离线历史耗时在 history 中填写 `verify_ms` 并提供条数/字节与执行主机；图表不将离线核验等同于在线准入总耗时。
