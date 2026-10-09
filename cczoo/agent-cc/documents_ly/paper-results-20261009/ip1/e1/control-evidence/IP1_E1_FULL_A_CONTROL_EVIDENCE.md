# IP1_E1_FULL_A_CONTROL_EVIDENCE（IP1 → IP2，2026-10-04）

范围：原 A 窗口（18:47:00.632–18:47:07.789Z）控制端原件 + 19:07:51Z 当前诊断（明确标注，非 A 窗口证据，不回填）。IP1 未重启任何服务端 Helper/Provider/Agent，未执行 Docker/barrier 操作；P0 证据未动。

## 1. 原 A 窗口：不存在 IP1 实时探针（已确认）

- probe.jsonl 全部 5 条 observed_at：15:55:26 / 15:55:40 / 18:09:11 / 18:40:55(preflight) / 18:43:01(smoke)——无 18:47 窗口记录；rotation-watch 持续活跃（窗口内凭据轮换正常）。
- nginx 侧同一入口的窗口内记录仅 1 条：`POST /attestation → 200`（18:47:07Z），无任何业务探针请求。
- 结论：**原 A 窗口无 IP1 探针原件**；19:07:51Z 补做结果标记为「当前诊断」，不得作为 A 窗口证据。

## 2. 原 A 窗口控制端原件（本次交付，与 attempt 关联）

关联键：attempt_id `897feeb2e92a4f79aa97fe62878360b1` / nonce `jcpRsiLGeFAP9pZS2XT2JrDWMMoBKJwuvmKtvL9COzQ` / helper_invocation `db01eae91b884ae4bca808028e3a0f58`（前 `426b2d5089fe4f16aff5d546bf407096`）/ target launch-9295b78 + container 6fe3c315… / 窗口 18:47:00.6–07.8Z。

时间线（IP2 attempt.json 阶段与 IP1 控制端原件互证）：

| 时间 (Z) | 事件 | 来源 |
|---|---|---|
| 18:47:00.632 | attempt start（helper restart 命令，exit 0） | IP2 attempt.json |
| 18:47:00.886 | common_target ALLOW（CURRENT_TARGET_MATCHED） | IP2 |
| 18:47:01.941 | evidence_binding ALLOW（EVIDENCE_BOUND） | IP2 |
| 18:47:02.056 | Trustee Attestation API called，request_id=540b6596-dd45-4b72-b53d-6d43d4320a90 | IP1 trustee-restful.log |
| 18:47:02.071 | Quote DCAP check succeeded；MRCONFIGID check succeeded | IP1 |
| 18:47:07（nginx 完成时刻） | POST /attestation → 200（6287B；经 IP2→IP1 反隧道 -R 18443→8444，故源地址 127.0.0.1） | IP1 nginx access log |
| 18:47:07.364 | Trustee 捕获完成 captured_at_ms=1791053227364，coverage=COMPLETE，nonce=jcpRsi… | IP1 capture.json |
| 18:47:07.418 | Verifier/endorsement check passed；AttestationEvaluate succeeded | IP1 |
| 18:47:07.533 | remote_appraisal ALLOW（SIGNED_APPRAISAL_ACCEPTED） | IP2 |
| 18:47:07.536 | final_target ALLOW（SELECTORS_RETURNED） | IP2 |
| 18:47:07.731 | target SVID 发布 serial=168278850950390833660795343270905335788，expires 18:50:39Z，subscription=db01eae9 | IP2 |
| 18:47:07.789 | attempt complete | IP2 |
| 18:47:13 | IP1 快照循环首次捕获 jcpRsi 目录 8 文件（其后 39 个周期 sha256 不变，末次 19:06:44Z） | IP1 files-snap |

- **Trustee 判定原件**：`history-result.json` = `{"error_class":null,"result":"ALLOW","verdict":{"verified":true,"baseline_rtmr":"50a59971…","launch_entry_uuid":"108e9186…"}}`；DCAP、MRCONFIGID、策略检查全部通过，无拒绝。
- 附带文件：trustee-restful 窗口 7 行；nginx 窗口 1 行；spire-server 窗口 9 行（18:47:01/04 为生产 openclaw 与 native helper 的常规轮换，仅作上下文；Full helper SVID 系 18:42:13Z 签发、TTL 300，窗口内仍有效，无窗口内重签）；trustee 8 文件副本 + 快照首捕记录。

## 3. Provider receipt 缺失的定位（协助 IP2 定级）

- IP2 交付的 admission-stage-journal.jsonl 单元覆盖：ax-paper01-full-agent 16 行 + ax-paper01-full-helper 6 行 + init.scope 6 行；**ax-paper01-full-provider 0 行**（该 unit active，MainPID 1800920，InvocationID 13f19839…，未被采集）。
- provider 阶段的 receipt 本应由 provider unit 日志承载，而采集未覆盖该 unit → **NO_RECEIPT 与「采集范围」一致，不能据此判为评估失败**；且证据链显示证据实际已被采集（EVIDENCE_BOUND）并评估成功（Trustee ALLOW、verified=true）。
- 建议下一次 A：journal 采集覆盖 ax-paper01-full-{agent,helper,provider,authz,nginx} 全部 unit。

## 4. IP2 本地探针 HTTP 400 分析

- 拓扑（IP1 侧实测）：guest → 10.0.2.2:1944 = IP1 `ssh -L 127.0.0.1:1944:172.31.28.53:1943` → IP2 业务 nginx；Trustee 入口 127.0.0.1:8444（IP2 经 -R 18443→8444 反隧道访问，access log 源 127.0.0.1 由此解释）。
- 两侧探针为**同一业务入口**（172.31.28.53:1943）；差异仅在客户端身份：allowed_client_ids 仅含 `spiffe://argus.local/agent/openclaw/experiment/paper01/full`；该身份 SVID 私钥仅存在于 IP1 guest（private_key_included=false、must_use_live_credentials=true）→ IP2 本地无法合法呈现该身份 → **400 属身份呈现失败，不是准入被拒的证据**。
- 佐证：同一入口、同一合法身份，IP1 实时探针三腿 200（18:43:01Z smoke；19:07:51Z diag：/health 200 71B、sessions 200 1903B、search 200 3247B contains_fact=true）。
- 剩余疑点：400 的具体层级（TLS 层 vs authz/业务层）需 IP2 提供 spiffe-mtls-probe 的调用参数（URL、证书来源）与 stderr；请在下次 attempt 证据中附上。
- IP1 不使用关闭证书校验、放宽 allowed_client_ids 或借用生产身份等任何方式。

## 5. 当前诊断（2026-10-03T19:07:51.809Z，label=e1-full-20261004-A-diag-20261003T190751Z）

- 实时凭据：generation-1384170031，client serial C2EA629A103A2A34B5EFA1791ED12010（URI=合法客户端身份），not_after 19:11:07Z。
- 三腿：/health 200（peer serial B187E902CCD8250BDBFA1648364EB216，SAN spiffe://…/service/openviking-cmem/experiment/paper01/full）；sessions 200（1903B，10 items）；search 200（3247B，contains_fact=true）。probe_rc=0。
- **当前合法业务路径可用。**

## 6. 下一次 A 的约定（提案，请 IP2 确认或修订）

- IP2 至少提前 10 分钟通告计划开始时间 T 与 attempt_id；IP1 预排 guest 探针于 T+2s 与 T+10s 各一次（label 用调度时间；关联按实际时间戳 + 探针对端 serial == 该 attempt publications[].serial 双重判定，不回填）。
- 探针目标不变（guest→10.0.2.2:1944 三腿）；IP1 控制端采集持续运行（5 单元）。
- 建议 IP2 采集：journal 覆盖全部 5 个 ax-* unit；附 spiffe-mtls-probe 参数与 stderr。
- 窗口内信号无需人工转贴：T 与 attempt_id 提前约定即可，探针自动在窗口内完成。
- 附注：19:02:31Z 出现第二次 Trustee 评估（request_id ba2bcbdf-eca0-4484-9947-e87c2d5472e7，nonce 2b__9D2rNPMrD8s6qkRcIvwkT2A1EF9huzxuXpBu2nw，同样 result=ALLOW verified=true，captured 19:02:36.5Z）——IP1 无对应操作，请 IP2 确认是否其侧计划内动作。

## 7. 交付清单（sha256 见 SHA256SUMS）

- a-normal/ip1/trustee-restful-18h46h30-18h48h30-window.log（7 行）
- a-normal/ip1/nginx-access-18h47-window.log（1 行）
- a-normal/ip1/spire-server-18h46h30-18h48h30-window.log（9 行）
- a-normal/ip1/trustee-admission-jcpRsiLGeFAP9pZS2XT2JrDWMMoBKJwuvmKtvL9COzQ/（8 文件：capture.json、history-config.json、history-request.json、history-result.json、policy-input.json、rekor.json、trust-0.bin、trust-1.bin）
- a-normal/ip1/trustee-files-snap-first-jcpRsi-capture.txt（快照首捕 2 周期）
- diag-20261003T190751Z/diag-probe-record.json、diag-checkpoint.json（当前诊断，非 A 窗口证据）
