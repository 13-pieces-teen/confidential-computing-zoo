# IP1_E1_FULL_A3_ORIGINALS_SUPPLEMENT（IP1 → IP2，2026-10-04T03:34Z）

目的：补交 E1 Full A3 客户端原件中**未随主关联包交付**的部分。主包
`e1-20261004-full-a3-correlation`（IP1_E1_FULL_A3_CORRELATION.md sha256
933ebf3aadf99589dffde88c714f9f237fa72470e966ae0fd486cb93b4059526）已被 IP2 核验并在
A3-CLOSURE-RECEIPT（03:27:56Z）中闭环（双机 A3 CLOSED/PASS）。本包不构成新结论、
不重跑 A、不启动 A4、不补造、不重采现在的业务探针；P0 不重跑；未重启 Helper/Provider/Agent，
未执行 B/C 或 Docker 操作。

## 一、原件对照表（任务清单 → 原件 → 状态）

| 任务要求 | 原件 | 交付位置 | 状态 |
|---|---|---|---|
| probe JSONL | 54 条 A3 记录（schema argus.e1-business-probe.v1） | 主包 ip1-client-originals/probe-a3-full.jsonl | 已交 ✓ |
| 响应文件 | 每条记录内 body_sha256 / bytes / body_head / items（sessions 10 items、search 3247B contains_fact） | 主包（同上） | 已交 ✓；完整响应体未另存文件（见缺口 2） |
| transport journal | rounds-a3-full.jsonl（54 轮 started_at/ended_at/probe_rc/每腿状态） | 主包 | 已交 ✓ |
| Gateway 日志 | IP2 nginx access log 54 行（IP2 侧原件，已 54/54 逐腿互证 ≤610ms） | IP2 侧 | 已互证 ✓；guest 侧 gateway-follow 仅 stub（本包，缺口 1） |
| 凭据代次记录 | rotation-watch-a3-window.jsonl（358 条，5s 级） | 本包 | 补交 ✓ |
| 采样 unit 输出 | loop-status-final.jsonl（loop_end seq=54）、loop.pid | 本包 | 补交 ✓（rounds 已在主包） |

## 二、成功 / 失败 / 缺口全景（162 腿 = 161×200 + 1×ECONNRESET）

- **成功**：rounds 1–54 共 162 腿中 161 腿 HTTP 200（含准入后 rounds 37–54 的 54/54 腿）。
- **唯一失败**：**round 36 的 sessions 腿**（request_id 8e66081f-f0e1-40ad-9709-2ab4485794b8）：
  `result=UNAVAILABLE, code=CONNECTION_UNAVAILABLE, network_error=ECONNRESET, phase=https_request`。
  发生时机（真实时间 ≈ 19:44:5x）恰为本次准入的 ingress 重启瞬间（nginx start 19:44:54Z，
  IP2 nginx-post-admission.log 同秒记录旧 worker 退出）→ 属准入流程自身的连接重置，非政策拒绝；
  同轮 search 腿 200（对端 4307E916）与下一轮起 18 轮全 200 佐证。该腿完整原件保留于
  probe-a3-full.jsonl（主包已交），未改写、未删除。
- **缺口 1（gateway 容器日志）**：guest 侧 gateway-follow.log 仅有两次 "collector started"
  stub（15:56:59Z / 18:42:42Z），窗口内无容器 stdout 行——该采集器的 docker logs follow
  未产出内容。Gateway 侧请求事实由 IP2 nginx access log（54 行）与探针记录互证覆盖。
- **缺口 2（响应体）**：探针仅存响应摘要（body_sha256/bytes/body_head/items），完整响应体
  未另落盘；如需，可由 IP2 nginx 侧或容器侧复核，但本轮不重采。
- **缺口 3（/health 腿字段）**：mtls_health 腿无结构化 at_ms/server_serial，但 peer_cert 文本
  含对端 serial 与 SAN URI——本包 health-peer-serials-a3.json 已逐轮提取（54 行），
  与 sessions/search 的 server_serial 逐轮对照，轮内跨轮换的 7 个 MIXED 轮（5/14/23/32/36/40/48）
  均与签发/装载序列吻合。
- **缺口 4（窗口边沿）**：round 54 在 guest 时钟窗口内启动（19:49:40.013 guest < 19:49:46.437
  截止），其 sessions/search 腿超真实时钟边沿 6–12s（guest 时钟 −7.028s 所致），保留并标注。

## 三、serial 口径统一表（hex ↔ dec，观测值全集）

服务端（cmem entry f268eee1，全部有 spire 签发记录）：
- **4307E916AF2F7D010E660553AEDC5D2C** = 89099349394273619978530893279142894892（=publication，签发 19:43:39Z，exp 19:48:39Z）
- D531C2649ACF4DE7D557E0446E075A8E = 283383928406445954388742336530918890126（签发 19:45:53Z）
- 2EF3AF7CCFBDE8447E16984BF598115A = 62409775252995864943823921409586368858（签发 19:48:12Z）
- 37AE2C6B514A5FDEC45E95957282A4A7 = 74011900350149581757179872518762308775（窗口前）
- 988BB711CA9A1E4921F374839EDFDAB5 = 202768097713182074300111320966317988533（19:36:01Z）
- 8AB2A0788E98464B00231C700E204617 = 184360946996184635825974058139407894039（19:38:39Z）
- 210FDA744144699F7E9CA69F69400D40 = 43946839089739925323826755218366795072（19:41:14Z）

客户端（实验客户端 entry cea379df，全部有 spire 签发记录）：
- 3C98D8E35DCE6EFADAEC417B89ECC862 = 80547307883891194392581160082994940002（gen-1169070641，签发 19:43:50Z）
- C69DFC954D4ABBD3F60DD7168D2522D3 = 264007456768340338878386634831559992019（gen-3381910135，签发 19:46:15Z）
- DF86CE35187AE6A57A7C3E58B7B0DD4F = 297117793222127090442424383051909881167（gen-1684771195，签发 19:48:41Z）

## 四、凭据代次记录（rotation-watch，guest 时钟，358 条本包交付）

首见时刻（guest 时钟；+7.0s 为真实时间）与 spire 签发记录对照：
- gen-667304587 @19:34:48（窗口初始）→ gen-2743828406 @19:36:14 → gen-2768907474 @19:38:44 → gen-4180306715 @19:41:11
- gen-1169070641 @19:43:46（真实 19:43:53 ≈ 签发 19:43:50Z ✓）
- gen-3381910135 @19:46:12（真实 19:46:19 ≈ 签发 19:46:15Z ✓）
- gen-1684771195 @19:48:37（真实 19:48:44 ≈ 签发 19:48:41Z ✓）

## 五、当前健康快照（休息后，与 A3 历史结论分开）

见 ip1-current-health-snapshot-20261004T0332Z.json（只读）：IP1 三服务 unit active、
PID 未变（spire-server 4161698 / restful-as 1870180）、e1r2 采集单元 active；
guest 采样器已自停（loop_end seq=54），rotation watcher 仍运行（仅采集用途，未动任何服务）。

## 六、不补造声明

- 未用现在的证书、响应或新探针替代旧原件；未启动 A4；未重新执行 A；未重启任何服务端组件；
  未执行 B/C/native/Docker/barrier 操作。IP2 侧 B/C 阻断（Docktap delegation 过期）不在本轮处理。

## 七、交付清单（sha256 见 SHA256SUMS）

- IP1_E1_FULL_A3_ORIGINALS_SUPPLEMENT.md（本文件）
- rotation-watch-a3-window.jsonl（358 条，A3 窗口 19:34:46–19:49:46Z）
- loop-status-final.jsonl（loop_start + loop_end seq=54）
- loop.pid.txt（54146）
- gateway-follow.log（stub 原件，缺口 1）
- health-peer-serials-a3.json（54 轮 health 腿 peer serial 提取 + 一致性）
- ip1-current-health-snapshot-20261004T0332Z.json（休息后只读快照，独立于 A3 结论）
