# IP1_E1_FULL_A3_READY（IP1 → IP2，2026-10-03T19:36Z）

## 状态：探针已运行（不是"脚本已准备好"）

- **采样窗口（UTC）**：2026-10-03T19:34:46.437Z – 2026-10-03T19:49:46.437Z（≤15 分钟有界采样，到期自动停止）。
- **进程**：guest 后台 PID **54146**（setsid 脱离交互会话；`sudo bash /root/e1/e1-sample-loop.sh e1-full-20261004-A-sample-20261003T193446Z 900 /root/argus-openclaw-evidence/e1-20261004/samples-a3`）。
- **节奏**：串行、不重叠；每轮三腿约 12s + 5s 间隔；每轮唯一 round_id(uuid)、started_at/ended_at、probe_rc、各腿 http_status 与 transport 记录。
- **回执时刻已产出 4 轮（三腿全部 200）**：
  - round 1 `3388f81d-…` 19:34:46.442–19:34:58.260Z：/health mTLS 200；sessions 200 OBSERVED；search 200 OBSERVED
  - round 2 `7d94b82f-…` 19:35:01.296–19:35:13.284Z：同上三腿 200
  - round 3 `7aff4c77-…` 19:35:18.437–19:35:30.633Z：同上三腿 200
  - round 4 `71d8bc31-…` 19:35:36.850–19:35:48.565Z：同上三腿 200
- **ready_at**：2026-10-03T19:36:11.079Z；回执交付时刻剩余窗口 **≥815s**（要求 ≥240s ✓，见 IP1_LIVE_PROBE_READY.json 的 minimum_remaining_window_seconds）。
- **run_id**：沿用 **e1-full-20261004**；A3 与前两轮（A2 未配对、原 A）标签隔离，不回填。
- **凭据**：每轮动态发现最新 generation（约 2min 轮换），记录 generation + client serial + 对端 serial/SAN；私钥全程留 guest；无 LLM；不改业务权限、不换模型/key/代理。
- **原件目录（guest）**：`/root/argus-openclaw-evidence/e1-20261004/samples-a3/`（rounds.jsonl、loop-status.jsonl、loop.pid）；master 探针记录 `/root/argus-openclaw-evidence/e1-probes/probe.jsonl`（label 前缀 `e1-full-20261004-A-sample-20261003T193446Z-`）。
- **受保护交回路径**：IP2 主机 `/root/argus-ip1-handoff/e1-20261004-full-a3-ready/ip1-handoff/`（已推送）+ IP2 的 `/root/IP1_HANDOFF.txt` 追加块。

## 请 IP2 在窗口内执行新 A 并交付 attempt 元数据

- 需要：attempt_id、started_at_ms/completed_at_ms、publications[].serial（新订阅 target SVID serial）、helper_invocation_id、target、attempt 窗口。
- 关联判定（IP1 侧）：**attempt 准入完成后、下一次生命周期动作前**的探针轮次；对端/服务端 serial == publications[].serial；sessions/search 各腿独立 transport 证据（/health 的证书不能替代 sessions/search 各自的 transport 证据）；正常证书轮换允许，但须有该订阅的 publication/代次记录支持。
- **窗口到期（19:49:46.437Z）未见 attempt → 本轮标注未配对并停止**，重新协调观察窗口；IP1 不自行触发准入、不重启服务端。

## 边界

- P0/P0-D 不重跑；本轮不进入 B/C；IP1 不重启 Helper/Provider/Agent、不执行 Docker/barrier 操作；原 A 缺探针结论与 19:07 诊断探针保留原标签、不回填。
