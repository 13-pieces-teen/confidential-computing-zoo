# IP1_E1_FULL_A3_CORRELATION（IP1 → IP2，2026-10-03T19:58Z）

## 结论先行

- **本次业务观察成立**：attempt ADMITTED 之后、无下一次生命周期动作之前，guest 实时探针 **18 轮（round 37–54）× 三腿全部 200/OBSERVED**；IP2 nginx 访问日志 54 行与探针 54 腿逐腿吻合（时间误差 ≤0.6s，见时钟校正）。
- **attempt / 身份代次**：attempt_id `41aaef740d1944f4a8718931b38e2783`；helper_invocation `664f18cca38e4171af0c9426d05add78`（前 `6d7f43d675b34d6093653545832900e2`）；nonce `VAlcCOENN1fWQTUfqoFt-iR8fdUNt4sJjNDJAvm2YzE`；Trustee request_id `52a275c3-6cb9-44fb-a5ba-7184529973b1`；publication serial `89099349394273619978530893279142894892`(dec) = `4307E916AF2F7D010E660553AEDC5D2C`(hex)。
- **对端身份/target/证书 serial 与本次新订阅关联成立**：对端 URI 恒为 `spiffe://argus.local/service/openviking-cmem/experiment/paper01/full`（entry f268eee1）；target 未变（launch-9295b78 / container 6fe3c315…，boot_id c379b316）；观测到的三个服务端 serial（4307E916=publication、D531C264、2EF3AF7C）全部有 spire-server 签发记录，且与 IP2 侧 nginx 装载/两次 reload 时间逐点对上。
- 客户端三个 generation（3C98D8E3 / C69DFC95 / DF86CE35）全部有签发记录（entry cea379df），正常轮换有代次记录支持 ✓。

## 时钟校正

- 实测（19:53:33Z，method 见 clock-skew-measurement.json）：**guest 时钟比 IP1 慢 7.028s**；IP2 与 IP1 基本同步（trustee API called 19:44:48.937Z(IP1) vs provider quote at_ms 19:44:48.822Z(IP2) ⇒ IP2≈IP1−0.115s）。
- 下文所有「真实时间」= guest 时间 +7.0s；54 腿逐腿匹配（≤0.6s）佐证窗口内偏差恒定。

## 一、admission window（单列）

| 时间（Z，真实） | 事件 | 来源 |
|---|---|---|
| 19:44:47.493 | attempt start（`systemctl restart ax-paper01-full-helper.service`，exit 0） | IP2 attempt.json |
| 19:44:47.736 | common_target ALLOW（CURRENT_TARGET_MATCHED） | IP2 metadata |
| 19:44:48.822 | provider ALLOW（EVIDENCE_COLLECTED，quote 1035ms） | IP2 metadata |
| 19:44:48.823 | evidence_binding ALLOW（EVIDENCE_BOUND） | IP2 metadata |
| 19:44:48.937 | Trustee Attestation API called，request_id=52a275c3 | IP1 trustee-restful.log |
| 19:44:48.95 | Quote DCAP check succeeded；MRCONFIGID check succeeded | IP1 |
| 19:44:54.106 | Trustee 捕获完成（captured_at_ms=1791056694106），result=ALLOW，verified=true，baseline_rtmr=50a59971…，launch_entry_uuid=108e9186… | IP1 VAlcCOE capture |
| 19:44:54.166 | Verifier/endorsement check passed；AttestationEvaluate succeeded | IP1 |
| 19:44:54.282 | remote_appraisal ALLOW（SIGNED_APPRAISAL_ACCEPTED） | IP2 metadata |
| 19:44:54.283 | final_target ALLOW（SELECTORS_RETURNED） | IP2 metadata |
| 19:44:54.666 | attempt complete，result=ADMITTED；status_after.target_serial=89099349…；identity_delivery=ALLOW；ingress_ready=ALLOW | IP2 attempt.json |
| 19:44:54 | nginx start，装载 target SVID（nginx_loaded_at=19:44:54Z） | IP2 nginx-post-admission.log |
| 19:44:55 | 172.17.0.1 以**过期固定客户端证书**请求 /health → SSL verify error → 400（IP2 本地探针，非准入判定） | IP2 nginx log |

## 二、business observation window（单列，18 轮 54 腿，全部 200）

探针轮次（真实时间 = guest+7.0s）与 IP2 nginx access log 逐腿对照（首轮与关键轮次全列，其余以区间概括；完整 54 轮次见 rounds-a3-full.jsonl，54 条腿级记录见 probe-a3-full.jsonl）：

| round | 真实时间（Z） | /health | sessions | search | 对端 serial |
|---|---|---|---|---|---|
| 37 | 19:44:59.7 起（腿 19:45:00/05/11） | 200 71B | 200 1903B | 200 3247B | **4307E916（=publication serial）** |
| 38–39 | 19:45:16.5–19:45:45.3 | 200 | 200 | 200 | 4307E916（publication serial） |
| 40 | 19:45:50.3 起（sessions 19:45:56.3/search 19:46:02.2） | 200 | 200 | 200 | **D531C264**（19:45:53 签发后 3.3s 呈现） |
| 41–47 | 19:46:07.2–19:48:00.4 | 200 | 200 | 200 | D531C264 |
| 48 | 19:48:05.5 起（sessions 19:48:11.5/search 19:48:17.3） | 200 | 200 | 200 | sessions=D531C264；search=**2EF3AF7C**（夹在 19:48:12 签发两侧） |
| 49–50 | 19:48:22.3–19:48:51.2 | 200 | 200 | 200 | sessions=D531C264；search=2EF3AF7C |
| 51–53 | 19:48:56.2–19:49:41.9 | 200 | 200 | 200 | 2EF3AF7C |
| 54 | 19:49:47.0 起（腿 19:49:47/52.98/58.9） | 200 | 200 | 200 | 2EF3AF7C |

- IP2 侧独立记录一致：access log 首轮 19:45:00/05/11Z 三腿 200（ip2-business-correlation.json 的 first_complete_round 完全一致）；54 行 access log = 18 轮 × 3 腿。
- **round 48 是关键证据**：sessions 腿（19:48:11.5）仍见 D531，search 腿（19:48:17.3）见 2EF3——恰好跨在 19:48:12Z 签发的两侧，证明客户端观测的证书序列与签发/装载序列精确一致，非事后拼凑。
- sessions/search 各腿独立携带 request_id 与 server_serial（probe-a3-full.jsonl）；/health 腿 mTLS 200 且 IP2 access log 逐轮可见；未用 /health 证书替代 sessions/search 的 transport 证据。

## 三、服务端证书序列（publication + 轮换，均有签发记录）

| 签发时间（Z） | serial(hex) | 过期（Z） | 事件 | 记录 |
|---|---|---|---|---|
| 19:43:39 | 4307E916AF2F7D010E660553AEDC5D2C | 19:48:39 | 例行轮换签发（节奏约 2m15s，见 19:36:01/19:38:39/19:41:14 同节奏）；**被本次订阅发布**（IP2 publication_expires_at=19:48:39Z 完全一致；nginx_loaded_at 19:44:54Z） | IP1 spire log + IP2 attempt.json |
| 19:45:53 | D531C2649ACF4DE7D557E0446E075A8E | 19:50:53 | 例行轮换；nginx reload#1 于 19:45:54Z | 同上 + IP2 nginx log |
| 19:48:12 | 2EF3AF7CCFBDE8447E16984BF598115A | 19:53:12 | 例行轮换；nginx reload#2 同秒（19:48:12Z） | 同上 |
| 19:50:30 | 5B2C4B768C58139EAC28D1147ECDCAEE | 19:55:30 | 窗口结束后轮换（未观测） | IP1 spire log |

## 四、客户端身份代次（publication 记录支持）

| generation | client serial | 签发时间（Z） | 使用轮次（真实时间） |
|---|---|---|---|
| generation-1169070641 | 3C98D8E35DCE6EFADAEC417B89ECC862 | 19:43:50 | 37–41（19:44:59.7–19:46:19.1） |
| generation-3381910135 | C69DFC954D4ABBD3F60DD7168D2522D3 | 19:46:15 | 42–50（19:46:24.1–19:48:51.2） |
| generation-1684771195 | DF86CE35187AE6A57A7C3E58B7B0DD4F | 19:48:41 | 51–54（19:48:56.2–19:49:58.9） |

全部有 spire-server 签发记录（entry cea379df）✓；每代首现时间均在签发时间之后（9s/15s）✓。

## 五、窗口内无下一次生命周期动作

- Trustee 全天仅 3 次 attestation：18:47:02（原 A）、19:02:31（诊断，IP2 已归因为 Provider 日志维护重启联动）、19:44:48（本次 A3）；**19:44:48.937Z 之后无任何新 attestation**、无新 capture 目录。
- IP2 回执确认：无 retry、无 Docker 生命周期动作、无 B/C/native/E2–E5；Full 仍停留在已准入 target 上。
- nginx 两次 reload（19:45:54 / 19:48:12）属 ingress 证书轮换流程的一部分（与签发记录同秒），非独立生命周期动作。
- 因此观察窗口 19:44:59.7–19:49:59Z（真实）整体满足「新准入完成后、下一次生命周期动作前」。

## 六、缺口与说明

1. **publication serial 的签发时间早于 attempt 开始 75s**：4307E916 由 19:43:39Z 的例行轮换签发（前一 helper 调用 6d7f43d6 存续期间），本次新订阅的「发布」是将其交付并装载（nginx_loaded_at 19:44:54Z），而非准入后新签。准入后无立即重签，直到例行轮换 19:45:53Z（D531）。serial 与过期时间与 IP2 publication 元数据完全一致，但「发布=交付既有有效 SVID」的语义请 IP2 定级；IP1 侧该 serial 的签发记录完整。
2. **/health 腿探针记录无 at_ms 与 server_serial 字段**（仅 http_status/result）；其腿时间由 IP2 access log 逐轮佐证，其 mTLS 证书链为客户端实时 SVID 与服务端 4307E916→D531→2EF3 序列的同一 nginx 实例。
3. **round 54 的窗口边沿**：guest 时钟窗口内启动（19:49:40.013 guest < 19:49:46.437 guest 截止），其 sessions/search 腿（真实 19:49:52.98 / 19:49:58.9）超出真实时钟窗口边沿约 6–12s——系 guest 时钟 −7s 所致，透明标注，不剔除。
4. **时钟偏差为事后单点实测**（19:53:33Z），窗口内假设恒定 +7.0s；54 腿 ≤0.6s 吻合佐证该假设成立。

## 七、判定与建议

- 本次业务观察**成立**：ADMITTED（19:44:54.666Z）后、无下一次生命周期动作前，18 轮 54 腿全 200/OBSERVED；对端身份、target、实际证书 serial（publication + 两次轮换均有签发记录）与本次新订阅（attempt 41aaef74 / helper 664f18cc / nonce VAlcCOE…）关联成立；sessions/search 独立 transport 证据齐备。
- 建议 IP2 将 two_host_correlation 置为 **ESTABLISHED**（ip2-business-correlation.json 现为 UNKNOWN，其 ip1_client_originals=NOT_RECEIVED 条件已由本包满足）；缺口 1 的语义请 IP2 确认后记入 A3 结论。
- IP1 停止于本轮：不进入 B/C，不重启服务端，不执行 Docker/barrier。

## 八、交付清单（sha256 见 SHA256SUMS）

- IP1_E1_FULL_A3_CORRELATION.md（本文件）
- ip1-client-originals/rounds-a3-full.jsonl（54 轮）
- ip1-client-originals/probe-a3-full.jsonl（54 条腿级记录）
- ip1-control-evidence/spire-cmem-issuances-193600-195100.txt（7 条 cmem 签发）
- ip1-control-evidence/spire-client-issuances-194300-194930.txt（3 条客户端签发）
- ip1-control-evidence/trustee-restful-52a275c3.txt（Trustee 判定流水）
- ip1-control-evidence/trustee-admission-VAlcCOE-history-result.json、trustee-admission-VAlcCOE-capture.json（Trustee ALLOW 原件）
- ip1-control-evidence/clock-skew-measurement.json（时钟偏差实测）
