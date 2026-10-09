# A3 准入与单次成本 — 来源索引（不重复采集）

本文件只索引既有结果分支原件，不重新采集、不重跑 A3。

## A3 准入原件（ip2/e1/online/）

| 文件 | 摘要（sha256 前 16） | 内容 |
|---|---|---|
| A3-CLOSURE-RECEIPT.md | c5f109bb9b9bfabc | A3 关闭收据（IP2 侧） |
| A3_FINAL_STATUS.txt | a7db975d512cfe65 | A3 最终状态：new_admission=ESTABLISHED_ADMITTED，two_host_a3=CLOSED_PASS，old_unknown=PRESERVED_UNCHANGED，new_subscription_triggered=NO，provider_restarted_or_redeployed=NO |
| IP2-summary.md | 0e3fe0785e6ba880 | IP2 A3 摘要：新准入 ESTABLISHED/ADMITTED（attempt 41aaef740d1944f4a8718931b38e2783）；合法客户端业务访问 54/54 legs 200；双机 A3 CLOSED/PASS |
| attempt.json | 93869bf834d9eb0b | A3 attempt 原始记录 |
| observation.json | 34d65af504c32cd0 | A3 观察输出 |
| offline-correlation.json | eb8a50b3fb981295 | 双机 A3 离线关联（CLOSED_PASS 依据） |
| SOURCE-VERIFICATION.txt | ad371bfd7a4df634 | 来源核对记录 |
| IP1_E1_FULL_A3_CORRELATION.md | 933ebf3aadf99589 | IP1 侧 E1 Full A3 关联（54/54 legs、IP1 rounds 37–54） |

注：A3 状态中的 bc_authorization=BLOCKED_DELEGATION_EXPIRED 是业务连续性 B/C 阻断的
delegation 状态，与 A3 准入结论本身无关，引用时按原字段区分。

## 单次准入成本（ip2/tables/FIGURE-DATA.json，sha ca4dabc70887fc72）

schema: argus.paper02.figure-data.v1
environment: 原始 IP2 TDX guest，boot c379b316-287e-4c6b-821b-63ae5a51c45e，
SPIRE 1.15.3；e4 model siliconflow/deepseek-ai/DeepSeek-V3.2；controlled_v2_backend 未部署。

| 指标 | 数值 | 单位 |
|---|---|---|
| e1_admission_cost_ms.history_snapshot | 6.338805 | ms |
| e1_admission_cost_ms.quote | 1034.778383 | ms |
| e1_admission_cost_ms.provider_total | 1086.17503 | ms |
| e1_admission_cost_ms.ready_elapsed | 7171.385087 | ms |
| e1_stage_elapsed_ms.common_target | 243 | ms |
| e1_stage_elapsed_ms.provider | 1330 | ms |
| e1_stage_elapsed_ms.evidence_binding | 1330 | ms |
| e1_stage_elapsed_ms.remote_appraisal | 6789 | ms |
| e1_stage_elapsed_ms.final_target | 6791 | ms |

缺失在线 E1 负例（missing_online_e1，维持 UNKNOWN 策略，不补零）：
wrong_nonce、old_evidence_fresh_challenge、hidden_stop_submission、
mismatched_quote_or_ear、current_facts_only_backend。
missing_value_policy: UNKNOWN 和 NOT_RUN 保持分类值，永不替换为零。

## 相关旁证（本批次已复核材料）

- E1 七例历史规则离线复核：见本批次 e1-cases/（规则级比较，非完整 Native SPIRE 对照）。
- E2 入口关闭区间：见本批次 e2/（离线复算）。
- S1 身份复用：见本批次 s1/（服务端 600.281 s 三次发布 + Quote 增量 0/0/0）。
