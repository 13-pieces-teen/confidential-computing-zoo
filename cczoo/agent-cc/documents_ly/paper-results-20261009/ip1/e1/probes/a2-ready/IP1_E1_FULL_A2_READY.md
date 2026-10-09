# IP1_E1_FULL_A2_READY（IP1 → IP2，2026-10-03T19:19Z）

## 状态：探针已运行（不是"脚本已准备好"）

- **采样窗口（UTC）**：2026-10-03T19:18:01.277Z – 2026-10-03T19:33:01Z（≤15 分钟有界采样，到期自动停止）。
- **进程**：guest 后台 PID **40194**（setsid 脱离交互会话；`sudo bash /root/e1/e1-sample-loop.sh e1-full-20261004-A-sample-20261003T1918Z 900 /root/argus-openclaw-evidence/e1-20261004/samples`）。
- **节奏**：串行、不重叠；每轮三腿约 12s + 5s 间隔；每轮唯一 round_id(uuid)、started_at/ended_at、probe_rc、各腿 http_status。
- **回执时刻已产出 3 轮（三腿全部 200）**：
  - round 1 `688285f8-…` 19:18:01.282–19:18:13.437Z：/health mTLS 200；sessions 200 OBSERVED；search 200 OBSERVED
  - round 2 `6d75b825-…` 19:18:18.468–19:18:30.777Z：同上三腿 200
  - round 3 `1fb79683-…` 19:18:35.808–19:18:47.908Z：同上三腿 200
- **run_id**：沿用 **e1-full-20261004**；新 A 建议 attempt 目录 full-attempt-003/stage-a-normal（IP2 可修订）。
- **凭据**：每轮动态发现最新 generation（约 2min 轮换），记录 generation + client serial + 对端 serial/SAN；私钥全程留 guest；无 LLM；不改业务权限、不换模型/key/代理。
- **原件目录（guest）**：`/root/argus-openclaw-evidence/e1-20261004/samples/`（rounds.jsonl、loop-status.jsonl、loop.pid）；master 探针记录 `/root/argus-openclaw-evidence/e1-probes/probe.jsonl`（label 前缀 `e1-full-20261004-A-sample-20261003T1918Z-`）。
- **受保护交回路径**：`/root/argus-ip1-handoff/e1-20261004-full-a2-*/ip1-handoff/` + `/root/IP1_HANDOFF.txt`。

## 请 IP2 在窗口内执行新 A 并交付 attempt 元数据

- 需要：attempt_id、started_at_ms/completed_at_ms、publications[].serial（新订阅 target SVID serial）、helper_invocation_id、target、attempt 窗口。
- 关联判定（IP1 侧）：**attempt 准入完成后、下一次生命周期动作前**的探针轮次；对端/服务端 serial == publications[].serial；sessions/search 各腿独立 transport 证据（/health 的证书不能替代 sessions/search 各自的 transport 证据）；正常证书轮换允许，但须有该订阅的 publication/代次记录支持。
- **窗口到期（19:33:01Z）未见 attempt → 本轮标注未配对并停止**，重新协调观察窗口；IP1 不自行触发准入、不重启服务端。

## 边界

- P0/P0-D 不重跑；本轮不进入 B/C；IP1 不重启 Helper/Provider/Agent、不执行 Docker/barrier 操作；原 A 缺探针结论与 19:07 诊断探针保留原标签、不回填。
