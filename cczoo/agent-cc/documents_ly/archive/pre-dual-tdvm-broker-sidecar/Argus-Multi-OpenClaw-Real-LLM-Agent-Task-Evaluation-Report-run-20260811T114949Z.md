# E8：多 OpenClaw 对单 OpenViking 的真实 LLM Agent 任务评估报告（run-20260811T114949Z）

> **历史运行快照：** 本次 run 基于旧 OpenViking Python TLS/materializer
> Profile，不验证当前 Broker Sidecar 链路。
>
> 评估对象：`multi_openclaw_real_llm_shared_x509pop_agent`（单 OpenViking、多 OpenClaw、共享 x509pop SPIRE Agent、Mock Evidence Provider + Mock Trustee）。
> 本报告是当前配置下的探索性快照，不代表生产容量或真实 Quote/QGS 性能。

- 报告日期（本地）：2026-08-11
- 执行主机：`cwf-bkc`（远程 Linux 验证机）
- 评估计划：`documents_ly/Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Plan.md`
- 对应自动产物：`/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/{manifest,summary,report}.{json,json,md}`

---

## 1. 版本与配置摘要

| 项 | 值 |
|---|---|
| 分支 | `feat/argus-spiffe-v2-val` |
| 完整提交 SHA | `0cebc86e420da7c0014afee28bd6d0680672bcee` |
| Git tree state | **clean**（拉取前后均无未提交改动；`git pull --ff-only upstream` 由 `1b5c8a2` 快进至 `0cebc86`） |
| 运行 profile | `multi_openclaw_real_llm_shared_x509pop_agent` |
| 证明 Profile | `mock_ra_mock_trustee`（Mock RA + Mock Trustee，非真实 Quote/QGS） |
| OpenClaw 模型 | `aidemo/minimax-m2.7`（provider `aidemo`；`maxTokens`/`temperature` 未配置，`null`） |
| OpenViking 插件 | `base_url=https://openviking.argus.local:1943`，`mode=remote` |
| OpenViking 归档 VLM（配置观测） | `vlm.api_base=https://gateway.aichina.intel.com/v1`，`model=minimax-m2.7`（`ov.conf`，非本轮基准测试量） |
| Guard 证据模式 | `case_level`（caller-local SPIFFE 授权） |
| 任务超时 | agent 180s / capture T1+60s / commit 300s / archive T3+300s（默认值，未调整） |
| 每任务格式门 | 摘要 150–400 字符 + `1./2./3.` 结论行 + 独占末行 marker |
| 源 OpenClaw 容器 | `agentcc-openclaw-sbx-gateway`（未停止/未修改） |
| 源配置卷 | `openclaw-config`（按 allowlist 克隆，不修改源） |

> 本轮使用 harness 更新提交 `0cebc86`：新增 `provenance.py` Git clean gate（正式动作要求干净提交树）、`test_provenance.py`、`test_report.py`，manifest/receipt 升级为 v2 schema，`report.py` 同时输出 completed-only E2E 与全任务最终化耗时。`run.sh` 的 `all` 仅运行 C1/C2/C4/C8。

---

## 2. 各阶段执行状态

| 阶段 | 结果 | 说明 |
|---|---|---|
| `unit` | ✅ 通过 | `task-worker.test.mjs` 10/10；`test_report.py` + `test_provenance.py` 6/6 |
| `preflight` | ✅ 通过 | `check_remote_environment`、`verify-svid.sh`、`verify-openclaw-plugin-e2e.sh` 全部通过；真实 OpenClaw→OpenViking 插件 E2E 捕获/提交/归档 `archive:true`；Guard ALLOW、SPIFFE mTLS 正常 |
| `pilot` | ✅ 完成 | P0 2、P1 6、P2 12，共 20 条最终收据；全部 receipt 结构校验通过；生成 manifest / source-revision / config-profile / summary / report / SHA256SUMS |
| `all`（C1/C2/C4/C8） | ⛔ **未运行** | P0 单 OpenClaw 即出现 `openviking_archive` timeout（2/2），按执行规则阻断，不运行正式矩阵 |

---

## 3. Pilot 逐 case 指标

运行目录：`/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z`

### 3.1 Case 汇总

| Case | OpenClaw 数 | 启动/完成 | 成功率 % | Tasks/min | Completed E2E P50/P95/max ms | Agent turn P50 ms | Archive(commit→ready) P50 ms | 公平性 | 测量窗口 min |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P0 | 1 | 2/0 | 0.0 | 0.00 | N/A | 18.5 | N/A | N/A | 10.90 |
| P1 | 2 | 6/0 | 0.0 | 0.00 | N/A | — | N/A | N/A | 16.69 |
| P2 | 4 | 12/3 | 25.0 | 0.18 | 134 148 / 316 354 / 316 354 | 25 162 | 108 426 | 0.00 | 16.73 |

> Tasks/min 为「完成数 / 该 case 墙钟时长」；Completed E2E 仅统计成功任务；失败任务进入全任务最终化与失败阶段耗时统计。

### 3.2 每单元吞吐与公平性

| Case | 单元 | 启动/完成 | 单元成功率 % | Tasks/min | E2E P95 ms |
|---|---|---:|---:|---:|---:|
| P0 | openclaw-01 | 2/0 | 0.0 | 0.00 | N/A |
| P1 | openclaw-01 | 3/0 | 0.0 | 0.00 | N/A |
| P1 | openclaw-02 | 3/0 | 0.0 | 0.00 | N/A |
| P2 | openclaw-01 | 3/1 | 33.3 | 0.06 | 316 354 |
| P2 | openclaw-02 | 3/0 | 0.0 | 0.00 | N/A |
| P2 | openclaw-03 | 3/1 | 33.3 | 0.12 | 83 834 |
| P2 | openclaw-04 | 3/1 | 33.3 | 0.08 | 134 148 |

### 3.3 各阶段时延（已完成任务，ms）

| Case | agent_turn P50 | capture_first_observed P50 | commit→archive P50 | E2E P50 |
|---|---:|---:|---:|---:|
| P2 | 25 162 | 25 639 | 108 426 | 134 148 |

> 3 个成功任务的 commit→archive 为 64.2s / 108.4s / 272.8s，即归档即使成功也耗时较长，与归档 VLM 外部网关时延一致。

### 3.4 资源与连接峰值（collector 采样）

| Case | 组件 | CPU 峰值 % | RSS 峰值 MiB | 主机 TCP 连接 max（→1943 端口） | 客机 TCP 连接 max（→1943 端口） |
|---|---|---:|---:|---:|---:|
| P0 | openclaw-01 | 130.66 | 1429.5 | 208（3） | 24（3） |
| P0 | openviking | 12.89 | 597.4 | — | — |
| P1 | openclaw-01 / openclaw-02 | 236.58 / 139.42 | 1465.3 / 1423.4 | 207（5） | 29（5） |
| P1 | openviking | 29.65 | 599.2 | — | — |
| P2 | openclaw-01..04 | 157.97 / 159.29 / 191.07 / 168.24 | 1147.9–1453.1 | 213（9） | 36（9） |
| P2 | openviking | 43.27 | 601.4 | — | — |
| 各 case | guard / spire-server / openclaw-agent / mock-trustee / mock-evidence-provider | ≤5.24 | ≤135.6 | — | — |

> collector_errors=0；Guard/SPIRE/Mock 组件负载可忽略，控制面与身份链路不是瓶颈。内存充裕（host 可用 700+ GiB）。

---

## 4. 按阶段失败汇总（pilot，共 20 个实测任务）

| 失败阶段 | 错误类 | 次数 | 所属 | 耗时 P50/P95/max ms | 说明 |
|---|---|---|---|---|---|
| `openviking_archive` | `timeout` | 16 | P0×2、P1×6、P2×8 | 328 316 / 340 916 / 340 916 | 归档在 T3+300s 内未就绪；归档 VLM 外部请求超时（见 §6） |
| `openviking_capture` | `timeout` | 1 | P2 | 99 058 | OpenClaw 已产出 marker，但 OpenViking session context 未在 T1+60s 窗口内暴露该消息 |

- error_class 汇总：`timeout`×17、成功（`None`）×3。
- 无 `session_isolation`、`duplicate_marker`、`marker_in_input`、任务串线；无重复 `task_id`/marker。
- 其余链路（agent 生成、marker 捕获、commit、Guard ALLOW、SPIFFE mTLS、会话隔离）未出现失败。

---

## 5. 失败类别明确区分

| 类别 | 计数 | 证据 |
|---|---:|---|
| harness 问题 | 0 | collector_errors=0；无 `setup`/`unknown`；无重复收据 |
| 模型输出格式失败 | 0 | 成功捕获的响应均通过格式门（成功任务 `response_summary_chars` 325/330 等、`response_conclusion_count=3`） |
| OpenViking capture 失败 | 1 | P2 openclaw-03-001，capture 超时（marker 未在窗口内出现在 session context） |
| OpenViking archive 失败 | 16 | 全部 `openviking_archive` 300s 超时 |
| 外部 Provider 超时 | 16 | 即上述 archive 超时：客机日志 `_run_long_term_memory_extraction` → `openai.APITimeoutError: Request timed out.` / `httpx.ReadTimeout`；`ov.conf` `vlm.api_base=gateway.aichina.intel.com/v1` |
| SPIRE/Guard/mTLS 问题 | 0 | Guard `decision="deny"`=0；SVID 校验、SPIFFE mTLS 插件 E2E 通过 |

---

## 6. 阻断判定：不运行正式矩阵 `all`

- **P0 单 OpenClaw（1 单元、2 任务）仍出现 `openviking_archive` timeout（2/2）** → 触发阻断规则：**不运行 `all`**（C1/C2/C4/C8 未执行）。
- 保留全部证据，结论写为 **“单并发归档基线不稳定”**，**不能归因成多 OpenClaw 并发容量问题**。
- 已确认是外部 Provider 问题（OpenViking archive VLM → Intel AICloud 网关超时），故**未修改测试代码、未增加兜底/自动重试/Fallback/超时放宽**，如实保留结果。
- 补充证据：本 Pilot 中 P0(1 单元) 0/2 成功、P2(4 单元) 3/12 成功，archive 成败呈与并发无关的外部间歇性；因此**不能因 P1/P2 失败直接断言并发容量上限**。

---

## 7. Pilot 检查点核对（7 项全部通过）

1. `source-revision.json`：`clean=true`，commit `0cebc86e420da7c0014afee28bd6d0680672bcee` 与当前 HEAD 完全一致 ✅
2. manifest `schema_version=argus-e8-agent-task-run-v2` ✅
3. task receipt `schema_version=argus-e8-agent-task-v2`（20/20）✅
4. P0/P1/P2 每个计划任务均有且仅有一条最终收据（2/6/12）✅
5. 无 `session_isolation`、`duplicate_marker`、`marker_in_input`、任务串线 ✅
6. 报告同时包含 Completed E2E、全任务最终耗时、失败任务耗时、按失败阶段耗时 ✅
7. `formal_matrix.complete=false`（missing C1/C2/C4/C8）为预期行为，非正式容量结果 ✅

---

## 8. 身份与容量边界（明确声明）

- 本评估中所有 OpenClaw 单元 **共享同一个 x509pop SPIRE Agent**（`spiffe://argus.local/agent/openclaw`）、**同一 Workload API**、**同一 caller SPIFFE ID**、**同一 Guard**（`spiffe_identity` 模式、caller-local 授权）。
- 多 OpenClaw **不是**多个独立可信节点，也不代表独立身份域或生产容量。
- RA/Trustee 为 **Mock Profile**（`mock_ra_mock_trustee`），不涉及真实 Quote/QGS 性能。
- 控制面观测：右侧新增 Node Attestation=0、Trustee 请求=0；左侧 x509pop active-agent/attestation 计数器当前 Prometheus profile 未暴露（`unavailable_reason` 记录于 summary）。

---

## 9. 结论

在远程主机现有模型配置（`aidemo/minimax-m2.7`）、单 OpenViking、共享 x509pop Agent、Mock RA + Mock Trustee 条件下，测得 N 个真实 OpenClaw 的 agent turn→capture→校验→commit→Guard ALLOW→SPIFFE mTLS 链路正常；瓶颈唯一集中在 OpenViking **archive 阶段的对外部模型网关调用**（Intel AICloud 网关超时），且该不稳定在单并发（P0）即已出现，因此**不构成 C1/C2/C4/C8 正式容量或扩展效率结论**。不得缩写为“系统支持 N 个独立可信 Agent”或“生产 Agent 容量”。

---

## 10. 证据绝对路径

| 项 | 路径 |
|---|---|
| Run 目录 | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/` |
| manifest.json | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/manifest.json` |
| source-revision.json | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/source-revision.json` |
| config-profile.json | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/config-profile.json` |
| summary.json | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/summary.json` |
| report.md | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/report.md` |
| tasks.jsonl | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/cases/{P0,P1,P2}/tasks.jsonl`（及 `units/*/tasks.jsonl`） |
| SHA256SUMS.txt | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T114949Z/SHA256SUMS.txt`（71 项全部校验通过） |
| 关键日志 | 各 case `units/*/worker.stderr.log`、`container.log`、`collector.{stdout,stderr}.log` |
| 归档根因日志 | TD Guest `agentcc-openviking-service` 日志（`openai.APITimeoutError: Request timed out.`，`_run_long_term_memory_extraction`；经 `ssh -p 2222 tdx@127.0.0.1`） |
| 运行日志 | `/tmp/argus-e8-pilot-run.log` |
| 代码版本 | 分支 `feat/argus-spiffe-v2-val`，`0cebc86`（provenance gate + v2 report schema） |

---

*报告按用户既定约束生成：未修改模型/Provider/温度/最大输出/提示词/超时参数以美化结果；未引入通用适配层、自动重试、Fallback 或清理框架；未重置/隐藏未提交改动；未停止/修改源 OpenClaw；保留全部失败收据；未把外部 Provider 限制描述为系统容量。*
