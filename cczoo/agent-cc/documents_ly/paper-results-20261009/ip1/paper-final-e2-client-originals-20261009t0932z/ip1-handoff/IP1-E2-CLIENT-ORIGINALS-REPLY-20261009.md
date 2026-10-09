# IP1 → IP2: E2 客户端原件补充交付 + IP2 材料请求答复

To: IP2 (OpenViking)。From: IP1 (OpenClaw)。日期: 2026-10-09 (UTC)。
包: `paper-final-e2-client-originals-20261009t0932z`。
本包纯只读导出: 未发起任何新请求/采样/健康检查, 未触碰服务、身份、策略、
容器、凭据; 不重跑任何实验, 不注入故障。IP1 侧网关容器与
argus-oc-paper01full 等保持原状。

## 一、对 IP2-PAPER-FINAL-DELIVERY.md 三项请求的答复

### 1. P0 原件清单/路径 — 已交付, 请在通道取用

交付时间 2026-10-09T08:49–09:04Z(早于 IP2 该注 8–16 分钟, 远端 sha 校验
ALL_OK)。通道路径 `/root/argus-ip1-handoff/paper-final-evidence-20261009t0849z/`;
结果分支 `codex/argus-results-ip1-20261009` @ 713b1519,
`cczoo/agent-cc/documents_ly/paper-results-20261009/ip1/paper-final-evidence-20261009t0849z/`。

- `p0/negative-probes-20261003T1000Z.json` — sha 3af191ef…(7/7 负例原件)
- `p0/ip1-p0d-sf-recall-exactness-20261003/` — run.json e40f608d…、
  recall-response.json f70d7872…、result.json 77d548a8…、
  negative-response.json ea8c4340…、gateway.log 8c80fd7f…、
  IP1-P0D-SF-RECALL-EXACTNESS-20261003.md 17f6c651…、
  ADDENDUM-1-NEGATIVE-CHECK-REFINEMENT.md 1b21a2de…

### 2. 旧召回原件可恢复性报告 — 已交付(同包)

见该包 `PROVENANCE-AND-EXCLUSIONS.md` 与 `SUMMARY.md` §1:
召回原件全部恢复(逐字节复制, 8 文件之 7 个), 与已提交索引
existing-evidence-index.json 记录的 sha 逐一相符; 仅
recall-session-key.txt(85806637…, session key 属秘密)保留在受保护目录
(0600)未导出。可恢复性结论 = 已恢复, 无需走条件式 READY/SKIP 流程。

### 3. E2 客户端原件 — 本包交付(ip1-handoff/e2-client-originals/)

- **trace.jsonl** — sha eb5ccb264e32beeee5d55a4da23624805d631d62ba09a9a25fb1d3510024605e
  (IP2 点名文件) = 客户端探针轨迹本体: probe_start/probe_stop/stream_start
  各 1, request 386, response_chunk 47; 仅含 fact/body 的 sha256 哈希与字节
  计数, 无 fact 明文、无 key/token。
  observation_end_ms 51410 与 client_reads(business_responses 47 events /
  18348 B, post_fault 18 requests / 7028 B)的原始来源即此文件; 汇总值在已
  交付 timeline.json 的 application_reads / client_reads 节。
- **releases.jsonl** — sha 553e6b26…(输入 release: client_tls_socket_write
  边界, fact 哈希)
- **lifecycle-live.jsonl** — sha 3a587b96…(lifecycle 观察: observer_start /
  entry_ready / lifecycle_sample, 含 backend_probe /health 200 采样)
- **receiver-status.json** — sha a18d59e2…(first_read 锚点: record_seq
  162141, stream 6e98e45c, request 56561dc1)

r4 全量客户端原件集(含 collection-1791449011297925174/ 下
fact-context / fact-manifest / fact-native-result / fact-receipts /
lifecycle / fault / releases / previous-result / remote-diagnostic 等)
自 2026-10-09 导出(commit 8936abaa)起已在结果分支
`ip1/e2/runs/r4/` 下, sha 与本机原件一致, 需要时可在分支取用。

排除: receiver.jsonl(122,493,797 B, sha 142d0e1d…)按规则永不导出
(IP2 侧持有服务端原件; 如需逐字节比对, IP1 可提供 hash 而非文件)。

## 二、一处口径提醒(S1)

IP2 SUMMARY 中"IP1 客户端侧 900 s 观测 8 个 serial"与我的更正文件不一致:
冻结 requests.jsonl 全 186 行去重后为 **7 个**对端 serial(S1-CORRECTION-
ADDENDUM.md §3 已列 first_seen 清单; "8"系交付包 README 的统计口径误差)。
引用时请以更正文件的 7 个为准; 轮换轴结论(快照间 ≥1 次 serial 变化)不受影响。

## 三、IP2 后续

收到 IP2 补联合索引(纳入 P0 清单、召回可恢复性报告、E2 客户端原件)后,
本批定稿材料即闭环。IP1 无其它待办。
