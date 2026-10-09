# S1 更正说明(追加, 不改原判定) — run_id argus-paper-minimal-20261009t0718z-01-rotation

日期: 2026-10-09。依据: 冻结原件
`rotation/argus-paper-minimal-20261009t0718z-01-rotation/requests.jsonl`
(sha256 f490611c2c8ab75dce4dc093188dc3d8db7faa5fffd2f2f0908145b429a4c6c0,
186 行 = 6 warmup + 180 measurement), `c1/requests.jsonl` (e07bb770…, 同内容),
`load-result.json` (8fb37fce…)。重算脚本与完整输出见本目录
`s1_recompute.py` / `s1-recompute-output.txt`。本文件为追加更正说明,
不修改任何原始行、不改原 FAIL/UNKNOWN 判定, 不覆盖 IP1-FINAL-RECEIPT.md。

## 1. 更正后的 180 条构成(逐行重算)

- **180 条是计划采样调用**(900 s × 0.2 req/s, 冻结计划), 文件内实际行数 186 = 180 measurement + 6 warmup(全部成功)。
- **155 条成功**: 全部 HTTP 200、memory_result=nonempty、每响应 2 片 leaf 记忆。另有 6 条 warmup 成功(aux 时延 n=161 = 155+6 的来源)。
- **19 条 ValueError**: 在 HTTP 提交前中止(submission_state=not_attempted, http_status=None), 确未发出。分布: 3 条在观察窗外早期(row 8/39/70, started 1791531472347/1791531627347/1791531782347), 3 条在窗口内单条毛刺(row 100/131/161), 13 条为末尾连续片段。
- **6 条 RemoteDisconnected**: 交付结果未知(submission_state=unknown, http_status=None)——连接中途断开, 请求可能已到达服务端。分布: 3 条在观察窗外早期(row 22/54/82), 3 条在窗口内单条毛刺(row 112/140/169, started 1791531992347/1791532132347/1791532277347)。服务端是否实际收到, 由 IP2 的 NGINX/应用日志关联, 属服务端侧职责; 客户端侧结论为"交付结果未知"。
- **末尾 13 条中止无窗口内恢复证据**: row 174–186, 全部 ValueError(not_attempted), started 1791532302347→1791532362347, 止于客户端测量自然结束 1791532367347, 之后无任何成功行。IP2 collect 的"7 个中断片段 = 6 个单请求毛刺(5s 内恢复)+ 末段 13 条"与本重算一致(窗口内 6 毛刺 = 3 ValueError + 3 RemoteDisconnected)。
- **不统一归因"轮换竞态"**: 两类错误机制不同——ValueError 为 SVID generation 目录换代竞态(发送前, 未触及网络); RemoteDisconnected 为传输中断(触及网络, 交付未知)。原始判定 FAIL/UNKNOWN 保持不变。

## 2. 观察窗口内数字(collect 口径 ±20 s)

- 窗口 [1791531847266, 1791532488115] 内非成功行共 19 条 = **16 ValueError + 3 RemoteDisconnected**, 与 IP2 RESULTS.csv 的 in-window unknown=19 一致(该行未细分 error_class, 本文件给出细分)。
- 窗口外 6 条非成功行 = 3 ValueError + 3 RemoteDisconnected(全部位于观察开始前)。

## 3. 对端 serial 更正: 8 → 7

- 冻结 requests.jsonl 全 186 行去重后 **7 个对端 serial**(first_seen):
  1. 198035542019463705454557261293993331640 @1791531437347(warmup)
  2. 180910845701925942513390270946919128630 @1791531547347
  3. 232694896824432079261123141675733596784 @1791531707347
  4. 245456514439120629732028939970102485636 @1791531847347(= IP2 before 快照 serial ✓)
  5. 230961490919735595310653232097052148985 @1791531997347
  6. 80937547914853678949157948885751517620 @1791532137347
  7. 326027069949880564519896745254572331883 @1791532282347
- S1 客户端交付包 README 中"8 个 serial"系交付时统计口径误差; 以文件重算的 7 个为准。
  轮换轴结论不受影响(快照间 ≥1 次 serial 变化成立; IP2 before=24545651… 出现在客户端行, after=11716816… 发布于客户端最后请求之后, 不在客户端文件内, 两者一致)。

## 4. 其它确认(与已交付数字一致, 无变化)

- 连接复用 144/180(80%), 重连 17 = credentials_rotated 5 + previous_error 12; load-result connection_attempts=17, observed_connection_ids=18。
- 覆盖不足(collect 120768 ms > 10000 ms 阈值)、时间戳冻结、正式窗口唯一、未重跑——维持 IP1-FINAL-RECEIPT.md 与 IP2 联合 SUMMARY 的原判定。

## 5. 结论

180 条为计划采样; 155 成功非空; 19 条 ValueError 确未提交; 6 条 RemoteDisconnected 交付未知(其中 3 条在观察窗口内); 末尾 13 条中止且无窗口内恢复证据。原始 FAIL/UNKNOWN 与冻结时间戳不变。
