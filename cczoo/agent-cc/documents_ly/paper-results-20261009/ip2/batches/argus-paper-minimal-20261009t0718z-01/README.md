# Argus 最小补充实验 — batch argus-paper-minimal-20261009t0718z-01（IP2 侧交付包）

协议材料：仓库 confidential-computing-zoo，分支 docs/argus-minimal-experiments-20261009，
提交 `b471a34b73e4f8490fa633b7ea3bcfb510756d39`，目录
cczoo/agent-cc/experiments/argus/paper-minimal-20261009/（材料副本在 `materials/`，
SHA256SUMS 已核对）。实际运行版本与材料提交分别记录：部署树 HEAD
`12b365ed8d3c73965e69c8fbe3fcb46075fefa6c`，工具 SHA 见 MANIFEST.json。

## 目录

- `SUMMARY.md` — 联合结果摘要（三轴分列、S2/S3 跳过理由、旧材料补齐、覆盖率不足如实声明）
- `RESULTS.csv` — 逐行指标（run_id、指标、值/单位、分母、状态、证据路径、限制）
- `MANIFEST.json` — 材料 SHA 与实际运行工具/二进制哈希（分列）
- `run-plan-manifest.json` — 已发布的共同 RUN_PLAN（服务 ID、PID、冻结参数、交付目录）
- `config/` — S1 观察配置（rotation-config.json）
- `server/` — IP2 观察原件（observation.json、workload/provider 前后快照、Helper 日志窗口）
- `client/` — IP1 的 S1 客户端交付原件（requests.jsonl、load-result.json、c1/ 等，SHA 已核验）
- `s0/` — S0 预检与旧材料索引（E2 r4 本地哈希、Provider 探针、时钟、既有证据索引）
- `results/` — collect 输出（collect-result.json）与 RESULTS.csv
- `SHA256SUMS` — 本交付包全部文件的 SHA256 清单

## 关键结论（详见 SUMMARY.md / RESULTS.csv）

1. 轮换：600 s 窗口内 3 次精确发布、3 个不同 serial，Helper 订阅稳定 — 观察达成。
2. 业务访问：180/180 计划请求实际发出（无捏造）；155 成功全非空、25 UNKNOWN
   （客户端 generation 目录换代竞态，未重放）、0 rejected/timeout/overload；因客户端
   测量冻结结束点早于我方 completed_at_ms+20000 ms 界约 120 s，端侧覆盖率不足，
   整体覆盖率 UNKNOWN、判定 FAIL —— 如实标注，不填零、不重跑。
3. Quote：既有 Provider 计数接口（未升级）区间
   2026-10-09T07:44:27.654Z→07:54:27.934Z 内 Workload 增量 0/0/0、Node 0/0/0 ——
   区间内无新的 Workload Quote 调用。
4. S2/S3 SKIP：P0 负例 7/7（sha 3af191ef…）与 P0 真实召回（sha 17f6c651…）原件充分，
   引用不重做。
5. 服务状态（批次结束，只读确认）：五个 ax-paper02-full 单元 active 无重启、
   容器 running/healthy/RestartCount=0。
