# E8：多 OpenClaw 对单 OpenViking 的真实 LLM Agent 任务评估报告

> 评估对象：`multi_openclaw_real_llm_shared_x509pop_agent`（单 OpenViking、多 OpenClaw、共享 x509pop SPIRE Agent、Mock Evidence Provider + Mock Trustee）。
> 本报告是当前配置下的探索性快照，不代表生产容量或真实 Quote/QGS 性能。

- 报告日期（本地）：2026-08-11
- 执行主机：`cwf-bkc`（远程 Linux 验证机）
- 评估计划：`documents_ly/Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Plan.md`

---

## 1. 版本与配置摘要

| 项 | 值 |
|---|---|
| 分支 | `feat/argus-spiffe-v2-val` |
| 评估运行时的 HEAD | `e03a48d`（+ 未提交的 E8 校准修改，见下） |
| 报告提交后的 HEAD | `7528add`（校准修改已提交并推送） |
| 运行 profile | `multi_openclaw_real_llm_shared_x509pop_agent` |
| 证明 Profile | `mock_ra_mock_trustee`（Mock RA + Mock Trustee，非真实 Quote/QGS） |
| OpenClaw 模型 | `aidemo/minimax-m2.7`（provider `aidemo`，maxTokens/temperature 未配置，无覆盖） |
| OpenViking 插件 | `base_url=https://openviking.argus.local:1943`，`mode=remote` |
| OpenViking 归档 VLM（直接配置观测） | `vlm.api_base=gateway.aichina.intel.com/v1`，`model=minimax-m2.7`（ov.conf，非本轮实测） |
| Guard 证据模式 | `case_level`（caller-local SPIFFE 授权） |
| 任务超时 | agent 180s / capture 60s / commit 300s / archive 300s（默认值，未调整） |
| 每任务格式门 | 摘要 150–400 字符 + 1./2./3. 结论行 + 独占末行 marker |
| 源 OpenClaw 容器 | `agentcc-openclaw-sbx-gateway`（未停止/未修改） |

> E8 校准修改（提交 `7528add`，已在运行前生效于工作区）：
> `task-worker.mjs` 的 `validateAssistant` 改为容忍换行被传输折叠（marker 按末个空白分隔 token 校验），长度门限定在摘要正文并按真实模型输出区间校准为 150–400 字符；`run.sh` 的 pilot/all 对 P0/P1/P2 使用 `require_success=0`（报告实测成功率，而非要求 100% 完成）；多单元启动时每个单元注入独立 `OPENCLAW_GATEWAY_TOKEN` 与模型 CA bundle，并排除源工作区 attestation 标记以允许重新播种。

---

## 2. 各阶段完成状态

| 阶段 | 结果 | 说明 |
|---|---|---|
| `unit` | ✅ 通过 | `task-worker.test.mjs` 8/8、`test_report.py` 4/4 全部通过（含校准后的 4 个新增/修改用例） |
| `preflight` | ✅ 通过 | `check_remote_environment`、`verify-svid.sh`、`verify-openclaw-plugin-e2e.sh`（`E8_PREFLIGHT_RUN_REAL_E2E=1`）全部通过；真实 E2E 归档 `archive:true` |
| `pilot` | ✅ 完成 | P0 2/2、P1 3/6、P2 3/12 完成；全部 receipt 结构校验通过（数量、时序、状态合法）；生成了 summary.json / report.md / SHA256SUMS.txt |
| `all`（C1/C2/C4/C8） | ⏹ 已启动、按用户决策中止 | 复跑 P0 2/2 失败（`openviking_archive` 超时）、P1 部分写入（task-003 末行不完整，worker 被杀中止）。未跑 C1/C2/C4/C8 |

> 说明：pilot 通过即按既定指令直接启动 `all`。由于已确认归档阶段外部模型网关在并发下持续超时（见 §6），继续跑完 C1–C8 预计还需约 4 小时且几乎全是同类归档失败收据，用户选择「现在停止，用现有数据出报告」。C1/C2/C4/C8 未执行，扩展效率以 P0→P1→P2（1→2→4 OpenClaw）为可用数据。

---

## 3. 逐层指标（pilot 运行实测）

运行目录：`/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T082311Z`

### 3.1 Case 汇总

| Case | OpenClaw 数 | 启动/完成 | 成功率 % | Tasks/min | E2E P50/P95/max ms | Agent turn P50 ms | Archive(commit→ready) P50 ms | 公平性（min/max 单元速率） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| P0 | 1 | 2/2 | 100.0 | 1.02 | 42 641 / 74 808 / 74 808 | 17 700 | 24 112 | 1.00 |
| P1 | 2 | 6/3 | 50.0 | 0.24 | 90 283 / 125 228 / 125 228 | 20 832 | 70 265 | 0.36 |
| P2 | 4 | 12/3 | 25.0 | 0.18 | 132 059 / 255 566 / 255 566 | 29 728 | 104 319 | 0.00 |

> Tasks/min 为「完成数 / 该 case 墙钟时长」；E2E 为已完成任务的 `agent_task_e2e_ms`。

### 3.2 每单元吞吐与公平性

| Case | 单元 | 启动/完成 | 单元成功率 % | Tasks/min | E2E P95 ms |
|---|---|---:|---:|---:|---:|
| P0 | openclaw-01 | 2/2 | 100.0 | 1.02 | 74 808 |
| P1 | openclaw-01 | 3/2 | 66.7 | 0.22 | 125 228 |
| P1 | openclaw-02 | 3/1 | 33.3 | 0.08 | 89 513 |
| P2 | openclaw-01 | 3/1 | 33.3 | 0.11 | 125 278 |
| P2 | openclaw-02 | 3/1 | 33.3 | 0.07 | 255 566 |
| P2 | openclaw-03 | 3/1 | 33.3 | 0.07 | 132 059 |
| P2 | openclaw-04 | 3/0 | 0.0 | 0.00 | N/A |

公平性指标（报告 `fairness_ratio = min(单元速率)/max(单元速率)`）：P0=1.00、P1=0.36、P2=0.00。各 case 单元完成数分布：P0 `[2]`、P1 `[2,1]`（变异系数 0.33）、P2 `[1,1,1,0]`（变异系数 0.58）——随 OpenClaw 数上升，单元间完成率与速率差距显著拉大。

### 3.3 各阶段时延（已完成任务，ms）

| Case | agent_turn P50 | capture→observed P50 | commit→archive P50 | E2E P50 |
|---|---:|---:|---:|---:|
| P0 | 17 700 | 18 453 | 24 112 | 42 641 |
| P1 | 20 832 | 20 738 | 70 265 | 90 283 |
| P2 | 29 728 | 30 207 | 104 319 | 132 059 |

> 时延增长主要由「commit→archive」段贡献（P0→P2 从 24s 增至 104s，约占 E2E 增长的 80%）。Agent 生成段（agent_turn）增长温和（17.7s→29.7s）。

### 3.4 资源峰值（collector 采样）

| Case | 组件 | CPU 峰值 % | RSS 峰值 | 1943 端口连接峰值（host / guest） |
|---|---|---|---|---|
| P0 | openclaw-01 | 137.6 | ~1.43 GiB | 201 / 29 |
| P0 | openviking | 8.5 | ~0.57 GiB | — |
| P1 | openclaw-01 / openclaw-02 | 251.2 / 157.4 | 各 ~1.43 GiB | 227 / 48 |
| P1 | openviking | 22.8 | ~0.58 GiB | — |
| P2 | openclaw-01..04 | 164.8 / 176.9 / 216.2 / 297.0 | 1.10–1.45 GiB | 225 / 47 |
| P2 | openviking | 40.1 | ~0.59 GiB | — |
| 各 case | guard / spire-server / openclaw-agent / mock-trustee | <1.0 | ≤0.15 GiB | — |

> 内存充裕（host 可用 700+ GiB）；CPU 峰值集中在 OpenClaw 单元容器（1–3 核）与归档 VLM 后端（见 §6）。Guard/SPIRE/Mock 组件负载可忽略，说明控制面与身份链路不是瓶颈。

---

## 4. 按阶段失败汇总（pilot，共 20 个实测任务）

| 失败阶段 | 错误类 | 次数 | 所属 | 说明 |
|---|---|---|---|---|
| `openviking_archive` | `timeout`（archive did not complete） | 11 | P1×3、P2×8 | 归档在 300s 内未就绪；归档 VLM 请求超时（见 §6） |
| `response_validation` | `invalid_conclusions` | 1 | P2 | 模型输出缺少 1./2./3. 结论行（真实模型行为，非工具缺陷） |

其余链路（agent 生成、marker 捕获、commit、Guard ALLOW、SPIFFE mTLS、会话隔离）未出现失败。P0 两任务均完成。

---

## 5. 扩展效率与首次退化层

C1/C2/C4/C8 **未执行**（`all` 中止，见 §2），因此无法给出相对 C1 的扩展效率。以 P0→P1→P2（1→2→4 OpenClaw）为可用扩展数据：

| 指标 | P0 (1) | P1 (2) | P2 (4) | 首次退化层 |
|---|---:|---:|---:|---|
| 成功率 | 100% | 50% | 25% | **P1（2 OpenClaw）** |
| Tasks/min（总） | 1.02 | 0.24 | 0.18 | P1（吞吐不升反降） |
| E2E P50 | 42.6s | 90.3s | 132.1s | P1 |
| commit→archive P50 | 24.1s | 70.3s | 104.3s | P1 |
| 公平性 | 1.00 | 0.36 | 0.00 | P1 |

结论：**首次退化层为 P1（2 个并发 OpenClaw）**。退化集中在归档阶段（外部模型网关的 memory_extraction 请求在并发下超时），而非 agent 生成或身份链路。总吞吐未随 OpenClaw 数提升，因为完成任务的速率受归档吞吐钳制。

---

## 6. 失败根因与修复建议

**根因（已通过日志与 API 确认）**：
1. 归档阶段 OpenViking 的 `memory_extraction`（`_run_long_term_memory_extraction`）调用**外部模型网关** `gateway.aichina.intel.com/v1`（模型 `minimax-m2.7`，与 agent 同一模型）。在多个 OpenClaw 并发归档时，该外部调用超时：日志为 `openai.APITimeoutError: Request timed out.` / `httpx.ReadTimeout`；超时任务的 OpenViking `session_commit` task 最终 `status=failed`，`error="Request timed out."`。
2. 这是 **Provider 侧外部网关的并发时延/失败**，不是 SPIRE/Guard/OpenClaw/SPIFFE mTLS 链路问题：agent turn、marker 捕获、commit、Guard `decision=Allow` 全部正常，失败任务的模型输出仍通过格式门（如 P2 各 `response_body_chars`=305–386、`conclusion_count`=3）。
3. 观测到的归档时延随并发放大：单单元 24s → 2 单元 70s → 4 单元 104s+（>300s 则超时失败）；host 上 ollama（bge-m3 嵌入后端）CPU 峰值一度达 ~10.7 核。
4. `agentcc-openviking-service` 显示 `unhealthy` 但为陈旧健康检查（其检查 `curl 127.0.0.1:1933` 与 API 端口 1943 不一致），容器实际持续接受 SPIFFE mTLS 并更新 X509-SVID，与本轮失败无关。

**按指令保留的失败收据**：11 条 `openviking_archive` 超时 + 1 条 `invalid_conclusions` 均完整保留在 receipt 中；未为「通过」而重跑、未调大超时、未把 Provider 侧限制描述为系统容量。

**最小修复建议（非美化，不修改测试超时）**：
- Provider 侧：提升 Intel AICloud 网关对 `minimax-m2.7` memory_extraction 的并发吞吐/降低时延；或
- OpenViking 侧：对 memory_extraction 做并发限流/请求批处理，避免并发归档互相超时；
- 测试侧无需改动；若后续在网关恢复后重跑，可直接复用现有 harness（C1–C8 未执行，是当时未跑而非被跳过）。

---

## 7. 身份与容量边界（明确声明）

- 本评估中所有 OpenClaw 单元（`p0/p1/p2-openclaw-*`）**共享同一个 x509pop SPIRE Agent**（`spiffe://argus.local/agent/openclaw`）、**同一 Workload API**、**同一 caller 身份**、**同一 Guard**（`spiffe_identity` 模式、caller-local 授权）。
- 多 OpenClaw **不是**多个独立可信节点，也不代表独立身份域或生产容量。
- RA/Trustee 为 **Mock Profile**（`mock_ra_mock_trustee`），不涉及真实 Quote/QGS 性能。
- 度量反映「单控制面、单 OpenViking、外部模型网关并发受限」下的扩展行为，仅供内部验证参考。

---

## 8. 度量/观测/未确认字段的区分

- **实测（本轮真实测量）**：§3 全部指标、§4 失败计数、§5 扩展趋势、资源峰值。
- **配置观测（直接读配置，非本轮基准测试量）**：OpenViking 归档 VLM 指向 `gateway.aichina.intel.com` / `minimax-m2.7`（`ov.conf`）；OpenClaw `maxTokens/temperature` 未配置（`null`）；Guard 证据模式 `case_level`。
- **无法确认（N/A）**：左 x509pop active-agent/attestation 计数器（当前 Prometheus profile 未暴露，控制面右侧增量不可得）；OpenClaw 生成 Provider 的 token 用量（`input/output_tokens=null`）；`all` 中止运行 P1 的 task-003 末行（worker 被杀，不完整，不计入结论）。

---

## 9. 证据绝对路径

| 项 | 路径 |
|---|---|
| pilot 运行目录 | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T082311Z/` |
| 汇总 JSON | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T082311Z/summary.json` |
| 自动报告 | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T082311Z/report.md` |
| 校验和 | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T082311Z/SHA256SUMS.txt` |
| manifest / prompts / 配置画像 | 同上目录 `manifest.json`、`prompts.json`、`config-profile.json` |
| pilot 运行日志 | `/tmp/e8-pilot2.log` |
| `all`（中止）运行目录 | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260811T085912Z/`（P0 完整、P1 部分） |
| `all`（中止）运行日志 | `/tmp/e8-all.log` |
| 归档根因日志 | 客机 `agentcc-openviking-service` 日志（`openai.APITimeoutError: Request timed out.`，`_run_long_term_memory_extraction`） |
| 代码校准提交 | 分支 `feat/argus-spiffe-v2-val`，`7528add`（已推送 `fork`） |

---

*报告按用户既定约束生成：未修改模型/Provider/温度/最大输出/提示词/超时参数以美化结果；未引入通用适配层、分页、自动重试或清理框架；未做广泛 Docker 清理；未停止/修改源 OpenClaw；保留全部失败收据；未把外部 Provider 限制描述为系统容量。*
