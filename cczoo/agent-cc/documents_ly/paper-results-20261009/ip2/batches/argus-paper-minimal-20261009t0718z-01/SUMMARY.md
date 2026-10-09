# 联合结果摘要 — Argus 最小补充实验 (batch argus-paper-minimal-20261009t0718z-01)

日期：2026-10-09。协调者：IP2（OpenViking 侧）。协议版本：docs/argus-minimal-experiments-20261009，
材料 commit `b471a34b73e4f8490fa633b7ea3bcfb510756d39`（SHA256SUMS 全部核对通过）。
实际运行版本：部署树 HEAD `12b365ed8d3c73965e69c8fbe3fcb46075fefa6c`（材料提交与实际运行版本分别记录）。
本批未触发任何故障、新准入、组件重启、TTL 变更，未重跑 P0/A3/E2/E4，未升级 Provider。

## 一、旧材料补齐（S0，全部完成，无重跑）

- **E2 r4 接收窗口原件**（IP2 侧职责）：本地 `/var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/`
  26 个文件已建本地 SHA256 索引（`s0/e2-r4-local-sha256.txt`），并已导出到结果分支
  `cczoo/agent-cc/documents_ly/paper-results-20261009/ip2/e2/`。epoch 文件 binding.json
  sha `1c1fba89…`、receiver.jsonl（122493797 字节）sha `142d0e1d…`。
- **历史规则七用例**：已全部在结果分支
  `ip2/batches/20261009-repair-and-rerun/preexisting/e1-offline-history-fixtures/cases/`
  （legal、unrelated_activity、same_image_new_instance、hidden_stop、config_mismatch、
  old_evidence、instance_mismatch），分析器版本记录 git `3cf25a87`，private_keys_exported=false。
- **P0 权限负例**（IP1 侧找回）：`p0d-six-stage-20261003/negative-probes-20261003T1000Z.json`
  sha `3af191ef…`，7/7 负例（invalid/missing key 401、wrong_identity 403、cross_user 3/3、
  direct_backend 拒绝/超时）。
- **P0 真实 Agent 召回**（IP1 侧找回）：`ip1-p0d-sf-recall-exactness-20261003/` 报告 sha
  `17f6c651…`，3 轮全新真实网关召回会话，正例 4 样本 fact 完整出现、逐轮审计通过，负例记录齐备。

## 二、S1 三轴结果（run_id: argus-paper-minimal-20261009t0718z-01-rotation）

观察窗口：started_at_ms `1791531867266` → completed_at_ms `1791532468115`（600 s，complete=true，
Helper 日志窗口闭合完整）。客户端测量：IP1 侧 900 s（warmup 30 s + measurement 900 s，
0.2 req/s、concurrency 2、timeout 10 s、reuse、固定非空查询 POST /api/v1/search/find）。
时钟界：统一采用 IP1 直接 guest↔IP2 测量（guest 落后 IP2 平均 15.43 s，n=10），
collect `--clock-uncertainty-ms 20000`。

### 轴 1：Workload SVID 轮换 — 观察达成

- before→after 凭据 serial 变化：`245456514439120629732028939970102485636` →
  `117168169887379234192897520659204972313`（可信快照，两侧同 Helper 订阅
  `76bc43416ab64049871e818f3bcd15d3`、同 launch-a4ba2ba）。
- 窗口内 Helper 日志精确计数：**3 次发布，3 个不同 serial**（journal-anchor 取窗 + journal-window 解析）。

### 轴 2：业务访问（轮换期间）— FAIL，覆盖率 UNKNOWN（如实标注）

- 计划 180 请求 = 实际发出 180 请求（requests.jsonl 落盘计数一致，无捏造请求；
  此前两次中止的启动尝试发出 0 请求）。
- 全程 180 中：**成功 155（全部非空）**、**UNKNOWN 25**（error_class=ValueError、
  http_status=None，SVID generation 目录换代的客户端侧竞态，未重放）、rejected 0、
  timeout 0、overload 0。连接复用 144/180（80%），17 次重连 = 5 credentials_rotated
  + 12 previous_error。
- 观察窗口内（±20 s 界）：104 请求中成功非空 85、UNKNOWN 19；7 个中断片段（6 个单请求
  毛刺 5 s 内恢复 + 末段 13 请求连续 UNKNOWN 直至客户端测量结束）。
- **覆盖率 UNKNOWN（如实声明）**：客户端测量冻结结束于 guest `1791532367347`，而我方
  completed_at_ms+20000 = `1791532488115` 超出客户端结束点约 120 s；最大探测间隔
  125768 ms > 10000 ms 阈值。原因是 CLIENT_ACTIVE 送达延迟与一次执行工具临时不可用
  导致观察启动晚于计划（07:44:27Z 启动）。时间戳全部冻结、未做任何延长，正式窗口唯一，
  未重跑。UNKNOWN 不填零、不阻塞其他轴交付。
- 辅助时延（非 Argus 相对开销，仅供参考）：load-result api_ms n=161 p50 111.3 ms /
  p95 194.9 ms；IP1 侧仅成功子集 n=155 p50 112.0 ms / p95 257.7 ms（样本口径差异如实记录）。

### 轴 3：Quote 计数 — 已观察（区间内零新增 Workload Quote 调用）

- 既有 Provider 计数接口可用（无升级），前后快照均取到：同一 Provider 实例
  `c379b316-287e-4c6b-821b-63ae5a51c45e:1280687:1791524628548924645`（进程/套接字/Agent 未变）。
- 计数区间：`2026-10-09T07:44:27.654Z` → `2026-10-09T07:54:27.934Z`（600.28 s，
  嵌套于观察窗口内；本地 Provider 前后快照在各自 workload 快照之后取得，实际起止如上）。
- 区间内增量：Workload attempted/generated/failed = **0/0/0**，Node = **0/0/0**。
  三项 workload 增量全零支持结论：**该计数区间没有新的 Workload Quote 调用**。

## 三、S2 / S3 — SKIP（已有原件充分，引用不重做）

- **S2 权限矩阵**：SKIP。P0 负例 7/7 原件（sha `3af191ef…`）已支持原型配置权限声明，
  按文档条件不再发起任何新准入请求。
- **S3 单次 Agent 召回**：SKIP。P0 真实召回原件（报告 sha `17f6c651…`，含真实会话、
  逐轮审计与 transport 关联）已充分，按文档条件不再发起新模型调用。

## 四、交付

- 本批非秘密证据：`/root/argus-paper-minimal-20261009t0718z-01/`（server/、client/、
  s0/、materials/、config/、results/、RESULTS.csv、SUMMARY.md、README.md、MANIFEST.json）。
- 结果分支：`codex/argus-results-ip2-20261009`，路径
  `cczoo/agent-cc/documents_ly/paper-results-20261009/ip2/batches/argus-paper-minimal-20261009t0718z-01/`，
  提交与全量 SHA256SUMS 见该目录（不写回 docs 分支，不强推、不 reset、不混入运行代码）。

## 五、批次结束时 Full 服务状态（只读确认，2026-10-09T16:09 CST）

- 五个 ax-paper02-full 单元全部 active，ActiveEnterTimestamp 与批次开始时一致（无重启）：
  agent/authz/provider 13:43:48 CST、helper 13:44:43 CST、nginx 13:45:27 CST。
- 容器 `fb2a2294…`：running、healthy、RestartCount=0，StartedAt 2026-10-09T05:43:16Z（未变）。
- 本批全程未触发故障、新准入或组件重启；IP2 采样已结束（OBSERVATION_FINISHED 已发）。
