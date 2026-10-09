# IP2 E1 Full A3 closure summary

| 项目 | 结论 | 原件 |
|---|---|---|
| 新准入 | ESTABLISHED / ADMITTED | A3 attempt `41aaef740d1944f4a8718931b38e2783` |
| 合法客户端业务访问 | ESTABLISHED / OBSERVED | IP1 rounds 37–54；54/54 legs 200 |
| 双机 A3 | CLOSED / PASS | `offline-correlation.json` |
| 旧 UNKNOWN | 保留，未修改 | 原 A3 目录及原 SHA |
| 当前 Full | ready / healthy | `readiness-snapshot.json` |
| B/C 阻断 | Docktap delegation 已过期 | `del-13a8012059de` |

19:02:31Z 的联动订阅仍为诊断。本次没有新订阅、服务重启或实验执行。
B/C 前需按正常流程续办 delegation，并在 node policy 有效窗口内为隔离实验进程启用新的
`before_confirm` barrier。
