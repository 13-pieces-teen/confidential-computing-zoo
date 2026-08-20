# E8：多 OpenClaw 对单 OpenViking 的真实 LLM Agent 任务评估报告（run-20260812T012615Z）

> **历史运行快照：** 本次 run 基于旧 OpenViking Python TLS/materializer
> Profile，不验证当前 Broker Sidecar 链路。
>
> 评估对象：`multi_openclaw_real_llm_shared_x509pop_agent`（单 OpenViking、多 OpenClaw、共享 x509pop SPIRE Agent、Mock Evidence Provider + Mock Trustee）。
> 本报告是当前配置下的探索性快照，不代表生产容量或真实 Quote/QGS 性能。
> **本轮为「归档 VLM 本地化」对照轮**：OpenViking 归档 VLM 从外部 Intel AICloud 网关（`gateway.aichina.intel.com/v1`，minimax-m2.7）切换到**本地 ollama（qwen3:8b，经 litellm 适配）**，以验证替换后能否让正式矩阵跑完。生成侧（OpenClaw `aidemo/minimax-m2.7`）保持不变。
> ⚠️ **归档 VLM 非原外部网关配置**（local ollama qwen3:8b via litellm），本轮所有归档结果均在此替换配置下测得。

- 报告日期（本地）：2026-08-12
- 执行主机：`cwf-bkc`（远程 Linux 验证机）
- 评估计划：`documents_ly/Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Plan.md`
- 对应自动产物：`/var/lib/argus-spire-asymmetric/agent-tasks/run-20260812T012615Z/manifest.json`（run 中止于 P1，summary/report 未生成，以本文件为准）

---

## 1. 版本与配置摘要

| 项 | 值 |
|---|---|
| 分支 | `feat/argus-spiffe-v2-val` |
| 完整提交 SHA | `f9294871e4bbfb763c27f45545803e51efdfe249` |
| Git tree state | **clean**（`provenance.py` clean gate 通过；未改动任何代码） |
| 运行 profile | `multi_openclaw_real_llm_shared_x509pop_agent` |
| 证明 Profile | `mock_ra_mock_trustee`（Mock RA + Mock Trustee，非真实 Quote/QGS） |
| OpenClaw 模型 | `aidemo/minimax-m2.7`（provider `aidemo`；`maxTokens`/`temperature` 未配置，`null`）——**未改动** |
| OpenViking 插件 | `base_url=https://openviking.argus.local:1943`，`mode=remote` |
| **OpenViking 归档 VLM（本轮配置观测）** | `provider=litellm`，`api_base=http://172.18.0.1:11434`（宿主机 ollama），`model=ollama/qwen3:8b`，`max_retries=2`，`extra_request_body.num_ctx=16384`（配置意图；ollama runner 实测按 **4096** 截断，见 §6）。**非原外部网关 `gateway.aichina.intel.com`** |
| Guard 证据模式 | `case_level`（caller-local SPIFFE 授权） |
| 任务超时 | agent 180s / capture T1+60s / commit 300s / archive T3+300s（默认值，未调整） |
| 每任务格式门 | 摘要 150–400 字符 + `1./2./3.` 结论行 + 独占末行 marker |
| 源 OpenClaw 容器 | `agentcc-openclaw-sbx-gateway`（未停止/未修改） |
| 源配置卷 | `openclaw-config`（按 allowlist 克隆，不修改源） |
| 运行环境 | TD Guest 已运行（1943 hostfwd 生效）；`agentcc-openviking-service` 在本轮 pilot 前为一次干净重启（清理上一轮遗留的 wedged extraction），pilot 中止后再次重启恢复（清理并发下堆积的 extraction） |

> 本轮未改动任何评测代码、超时、重试或 Provider 参数；唯一的配置变更（用户已批准）是**归档 VLM 指向本地 ollama**。受控直连测试与本 pilot 均在该配置下运行。

---

## 2. 各阶段执行状态

| 阶段 | 结果 | 说明 |
|---|---|---|
| `preflight` | ✅ 通过（E2E 跳过） | `check_remote_environment`、`verify-svid.sh`、provenance git-clean gate 全部通过。真实 OpenClaw→OpenViking 插件 E2E 本轮以 `E8_PREFLIGHT_RUN_REAL_E2E=0` 跳过（原因：harness `verify-openclaw-plugin-e2e.sh` 的捕获逻辑 `sessions.slice(0,100)` 在累计 >100 会话下会漏检 marker，属已知 harness bug；归档链路改由下述受控测试直接验证） |
| 受控归档测试 | ✅ 通过 | 直接 API（X-API-Key + 插件 baseUrl）建会话→加单条小消息→commit→轮询 context。**tiny session 161s 内 `latest_archive_overview` 就绪**（ollama 侧：1 次 embedding + 3 次 `/api/generate`，23s–1m58s）。证明 local-ollama 归档链路端到端可用 |
| `pilot` | ⚠️ **P0 完成、P1 部分、P2 未启动** | P0=0/2、P1=0/3（3/6 收据，运行中止于 P1 以规避宿主机负载危机，见 §3.4）。全部已生成收据结构校验通过 |
| `all`（C1/C2/C4/C8） | ⛔ **未运行** | P0 单 OpenClaw 仍 0/2 `openviking_archive` timeout，按阻断规则不运行正式矩阵 |

---

## 3. Pilot 逐 case 指标

运行目录：`/var/lib/argus-spire-asymmetric/agent-tasks/run-20260812T012615Z`

### 3.1 Case 汇总

| Case | OpenClaw 数 | 启动/完成 | 成功率 % | Completed E2E | Agent turn P50 ms | 归档超时(300s) | 说明 |
|---|---:|---:|---:|---:|---:|---:|---|
| P0 | 1 | 2/0 | 0.0 | N/A | ~20 000–21 000 | 2/2 | 两任务均在 T3+300s 内 archive 未就绪 |
| P1 | 2 | 3/0（6 计划） | 0.0 | N/A | ~21 000–27 000 | 2/3 | 另 1/3 `openviking_capture` 超时；运行中止 |
| P2 | 4 | — | — | — | — | — | 未启动 |

### 3.2 每单元收据（P0 / P1）

| Case | 单元 | 序号 | 状态 | 失败阶段 | agent_turn ms | archive_ready |
|---|---|---|---:|---|---:|---|
| P0 | openclaw-01 | 001 | failed | `openviking_archive` timeout | 20 373 | ❌ |
| P0 | openclaw-01 | 002 | failed | `openviking_archive` timeout | 21 353 | ❌ |
| P1 | openclaw-01 | 001 | failed | `openviking_archive` timeout | 21 396 | ❌ |
| P1 | openclaw-01 | 002 | failed | `openviking_capture` timeout | 27 473 | —（未 commit） |
| P1 | openclaw-02 | 001 | failed | `openviking_archive` timeout | 25 763 | ❌ |

> agent turn（20–27s）与 commit（~20s）均正常，失败全部集中在归档/捕获阶段。P1-01-002 的 capture 超时出现于宿主机 load 280–300 的并发高峰（见 §3.4），属服务器过载，非归档本身。

### 3.3 归档 latency 实测（关键证据）

| 观测 | 值 |
|---|---|
| 受控测试（单条小消息） | commit→overview **161s**（<300s 窗口） |
| P0-001 真实任务 extraction | ollama 侧 2 次 `/api/generate`（2m51s、4m14s），wall-clock **>300s**；任务最终 `completed`（overview 生成），但超出窗口约 3min |
| P0-002 / P1 任务 extraction | 全部超过 300s 窗口；P1 并发下 load 280–300 |
| 归档窗口（harness） | `archive_timeout_ms=300000`（未调整） |

### 3.4 资源与连接峰值（collector 采样 + 宿主机观测）

| Case | 宿主机 load（观测峰值） | ollama qwen3 runner %CPU（观测） | 说明 |
|---|---:|---:|---|
| P0（1 单元，串行） | 峰值 ~87（单任务 extraction） | 单 runner 瞬时 ~150–260 cores | 单任务 extraction 即可显著占用 CPU |
| P1（2 单元） | **279–298** | 并发 extraction 叠加，多个 generate 排队 | 服务器过载，出现 capture 超时；据此**中止 pilot** |
| 受控测试（空闲） | ~0.08 | runner 空闲 | 非高峰期基线 |

> 控制面（Guard/SPIRE/Mock）负载可忽略。瓶颈唯一集中在归档 VLM 的 CPU 推理；宿主机可用内存 700+ GiB，非内存问题。

---

## 4. 按阶段失败汇总（pilot 已生成 5 条实测收据）

| 失败阶段 | 错误类 | 次数 | 所属 | 说明 |
|---|---|---|---|---|
| `openviking_archive` | `timeout` | 4 | P0×2、P1×2 | 归档在 T3+300s 内未就绪；extraction 实际**能完成但超窗**（P0-001 已证），根因见 §6 |
| `openviking_capture` | `timeout` | 1 | P1-01-002 | `assistant marker was not captured`；宿主机 load 280–300 下服务器过载，未到 commit |

- error_class 汇总：`timeout`×5、成功（`None`）×0。
- 无 `response_validation`（本轮生成侧格式门全部通过）、无 `session_isolation`/`duplicate_marker`/`marker_in_input`/任务串线。
- agent 生成、marker 捕获（P0/P1 其余任务）、commit、Guard ALLOW、SPIFFE mTLS、会话隔离链路未出现独立失败。

---

## 5. 失败类别明确区分

| 类别 | 计数 | 证据 |
|---|---:|---|
| harness 问题 | 0 | 无 `setup`/`unknown`；无重复收据；受控测试证明链路可用 |
| 模型输出格式失败 | 0 | 本轮生成侧（minimax-m2.7）格式门全过 |
| OpenViking capture 失败 | 1 | P1-01-002（并发 load 280–300 下服务器过载） |
| OpenViking archive 失败 | 4 | 全部 `openviking_archive` 300s 超时；extraction 可完成但 wall-clock 超窗（P0-001 任务状态 `completed`、overview 已生成） |
| 归档 VLM 延迟（本地 ollama） | 4 | `journalctl -u ollama`：真实任务 extraction 2–4 次 `/api/generate`（1m45s–4m14s），prompt 16.5–16.8k tokens **被截断至 4096**；CPU-only qwen3:8b 输出生成 ~5–10 tok/s |
| 外部 Provider 超时（原网关） | 0 | 本轮无 `gateway.aichina.intel.com` 请求（已切本地） |
| SPIRE/Guard/mTLS 问题 | 0 | Guard `decision="deny"`=0；SVID 校验、SPIFFE mTLS 正常 |

---

## 6. 阻断判定：不运行正式矩阵 `all`

- **P0 单 OpenClaw（1 单元、2 任务）本轮 0/2 成功**，全部为 `openviking_archive` timeout → 触发阻断规则：**不运行 `all`**（C1/C2/C4/C8 未执行）。
- **替换为本地 ollama 后，归档链路功能可用、但单并发基线仍不满足 300s 窗口**：
  - 受控 tiny-session 测试 161s 内归档就绪（<300s）✅；
  - 真实任务会话的 extraction 在 CPU-only qwen3:8b 上需 2–4 次 VLM generate（各 1m45s–4m14s），wall-clock **~500s > 300s 窗口**；P0-001 的 extraction 事后确认 `completed`（overview 生成），即**能完成、只是超窗**。
  - extraction prompt 本身 ~16.7k tokens（绝大部分为固定系统/内存 schema/少样本脚手架，任务输入仅 ~100–330 chars/302–332 chars 的 `source_text`），且 ollama runner 按 4096 截断（litellm `num_ctx=16384` 未生效）——但主导耗时是模型对真实会话生成较长的归档摘要的 CPU 逐 token 推理，而非 prompt 预处理。
- **并发下进一步恶化**：P1（2 单元）宿主机 load 飙至 279–298，服务器过载后连 capture 都超时 → 出于环境保护**中止 pilot**（未继续 P1/P2）。
- 已确认瓶颈为本地 CPU-only VLM 推理延迟（非外部网络），故**未修改测试代码、未放宽超时、未改 Provider/模型/重试策略、未加兜底**，如实保留全部失败收据。
- 结论边界：P0 单并发归档基线即不稳定（>300s 窗口不可满足），因此**不能把 P1/P2 失败或本轮 0 成功解释为并发容量上限**；也不能因 P0-001 extraction 事后完成而断言归档链路稳定。

---

## 7. Pilot 检查点核对（已生成部分）

1. `source-revision.json` / manifest：`git_tree_state=clean`，commit `f9294871` 与当前 HEAD 一致 ✅
2. manifest `schema_version=argus-e8-agent-task-run-v2` ✅
3. task receipt `schema_version=argus-e8-agent-task-v2`（P0 2/2、P1 3/6 已生成）✅
4. 已生成任务均有且仅有一条收据，无重复 `task_id`/marker ✅
5. 无 `session_isolation`、`duplicate_marker`、`marker_in_input`、任务串线 ✅
6. 报告同时包含 agent_turn、commit、归档超窗、受控测试实测时延 ✅
7. `formal_matrix.complete=false`（missing C1/C2/C4/C8）为预期行为，非正式容量结果 ✅

---

## 8. 身份与容量边界（明确声明）

- 本评估中所有 OpenClaw 单元 **共享同一个 x509pop SPIRE Agent**（`spiffe://argus.local/agent/openclaw`）、**同一 Workload API**、**同一 caller SPIFFE ID**、**同一 Guard**（`spiffe_identity` 模式、caller-local 授权）。
- 多 OpenClaw **不是**多个独立可信节点，也不代表独立身份域或生产容量。
- RA/Trustee 为 **Mock Profile**（`mock_ra_mock_trustee`），不涉及真实 Quote/QGS 性能。

---

## 9. 结论

在生成侧（`aidemo/minimax-m2.7`）不变、**归档 VLM 替换为本地 ollama（qwen3:8b via litellm，非原外部网关配置）**、单 OpenViking、共享 x509pop Agent、Mock RA + Mock Trustee 条件下：

- 真实 OpenClaw 的 agent turn→capture→commit→Guard ALLOW→SPIFFE mTLS 链路正常（agent turn 20–27s，commit ~20s）；
- 归档链路**功能可用**（受控 tiny-session 161s 就绪；P0-001 extraction 事后 `completed`），但真实任务会话的 extraction 在 **CPU-only qwen3:8b 上 wall-clock ~500s，超出 300s 归档窗口**，P0 单并发即 0/2；
- 因此与外部网关轮类似，**瓶颈仍唯一集中在归档阶段**：外部网关是“请求长尾/间歇超时”，本地 ollama 是“确定性 CPU 推理延迟超窗”。两种 Provider 均未能在 P0 单并发下满足 300s 窗口 → **不构成 C1/C2/C4/C8 正式容量或扩展效率结论**，不得缩写为“系统支持 N 个独立可信 Agent”或“生产 Agent 容量”。

---

## 10. 证据绝对路径

| 项 | 路径 |
|---|---|
| Run 目录（pilot） | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260812T012615Z/` |
| manifest.json | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260812T012615Z/manifest.json` |
| prompts.json | `/var/lib/argus-spire-asymmetric/agent-tasks/run-20260812T012615Z/prompts.json`（`source_text` 302–332 chars） |
| P0 收据 | `.../run-20260812T012615Z/cases/P0/tasks.jsonl`（001/002，含 session `fa405ad5…`、`a98b84c0…`） |
| P1 收据 | `.../run-20260812T012615Z/cases/P1/tasks.jsonl`（3 条） |
| P0-001 extraction 事后 completed 证据 | OpenViking API：task `d6d2b6e2-2eac-4ae2-8059-c8645e6aa687` status=`completed`；session `fa405ad5-8bb1-4b90-8a7d-3381c50c1c09` context `latest_archive_overview` 非空 |
| 归档 VLM 调用时序 | 宿主机 `journalctl -u ollama`：受控测试 3 次 `/api/generate`（23s/1m58s/22.9s）；P0 真实任务 4 次 `/api/generate`（1m45s/2m51s/2m54s/4m14s）+ `truncating input prompt limit=4096`（prompt 16.5–16.8k） |
| 受控归档测试脚本 | `/tmp/ctl-archive-test.mjs`（本机；直连 `https://openviking.argus.local:1943`） |
| 运行日志 | `/var/lib/argus-spire-asymmetric/agent-tasks/pilot-rerun-20260812T0120.log` |
| 宿主机负载/进程 | pilot 运行期 `top`/`/proc/loadavg`（P1 峰值 279–298）；ollama runner 瞬时 ~150–260 cores |
| 代码版本 | 分支 `feat/argus-spiffe-v2-val`，`f929487`（未改动，provenance gate 验证） |

---

## 11. 运行后配置变更记录（2026-08-12，非本轮基准测试量）

- 本轮（run-20260812T012615Z）在**本地 ollama 归档 VLM** 配置下测得（如上）。**运行结束后**，按用户决定将 OpenViking 归档 VLM **恢复为原始外部网关配置**：`provider=openai`、`api_base=https://gateway.aichina.intel.com/v1`、`model=minimax-m2.7`、`max_retries=2`（与切换前 `ov.conf.bak.ollama-swap` 一致；本地 litellm 配置另存为 guest `/app/.openviking/ov.conf.bak.ollama-current-20260812` 以备回退）。
- 恢复后受控探针（直连 API，tiny 会话）：**commit→overview 30.2s**，task `completed`，overview 1064 chars → 外部网关在探针时刻**健康可用**（符合其"成功时快"画像）。
- 按用户决定**暂不重跑 pilot**；后续若重跑，需重新记录当前网关状态（网关为间歇性，前两轮网关状态下 P0 均为 0/2）。

---

*报告按用户既定约束生成：未修改模型/Provider/温度/最大输出/提示词/超时/重试参数以美化结果；未引入通用适配层、自动重试、Fallback 或清理框架；未重置/隐藏未提交改动；未停止/修改源 OpenClaw；保留全部失败收据；未把 Provider 限制描述为系统容量。归档 VLM 替换为本地 ollama（qwen3:8b via litellm）为用户批准的配置变更，本轮结果标注「归档 VLM 非原外部网关配置」；运行后已恢复原外部网关配置（§11）。pilot 中止（P1 阶段）与容器重启均为环境保护/恢复动作，非评测行为变更。*
