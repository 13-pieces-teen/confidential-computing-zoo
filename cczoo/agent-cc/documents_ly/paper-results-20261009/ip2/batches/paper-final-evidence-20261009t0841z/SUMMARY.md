# Argus 论文定稿前补充验证 — IP2 统一交付（paper-final-evidence-20261009t0841z）

全部离线执行。未重跑 P0/A3/E2/E4/S1，未新增故障、准入、订阅或 Docker 生命周期操作，
未修改旧链、生产组件或任何历史原件。所有分析脚本在独立目录，只读既有结果分支材料。

## 一、E2 离线复算（e2/）—— 可以复算

- 先原样运行既有 `verify_e2_r4_windows.py`：PASS（摘要、序列、请求生命周期、覆盖边界）。
- 独立脚本 `e2/e2-recompute.py` 从作物原始事件重算，全部核查项通过：
  入口关闭区间 [4530.462, 5145.519] ms、检测区间 [4831.708, 5145.519] ms、
  故障命令完成 4.5415 ms、10 s 界内 18 请求/19 事件/4795 字节、
  界后 0/0/0（界后 146 对请求但零 body_read，直到 receiver_stop 53633.5 ms）、
  最后应用读取 4612.611 ms（4837.674 ms 为在途 http.disconnect 0 字节，两版均解释）、
  COMPLETE 覆盖至 53419.373 ms、入口关闭后后端健康 163 采样 OBSERVED（±20 ms 界口径）。
- 历史旧值（4.530–5.146 s、4.613 s 等）全部精确复现或解释，两版及原因保留在
  e2-recomputed.json comparison 节。
- 输出：e2-timeline.csv（18 行）、e2-recomputed.json、e2/SOURCES.md（命令与来源索引）。
- 缺项（不注入故障、不补造）：客户端 trace.jsonl（sha eb5ccb26…）与客户端探针 jsonl
  未随作物导出——observation_end_ms 51410 与 client_reads 历史值保持，待 IP1 客户端原件。

## 二、七个历史规则案例独立复核（e1-cases/）—— 7/7 与历史预期一致，0 运行错误

- 使用归档输入 + 公开信任材料 + 归档 Rekor + 生产 LogVerifier
  （analyzer-source 三文件实测摘要与 VERSIONS.json 逐一对上；tlog 树
  63d41663… 在 3cf25a87 与部署 HEAD 12b365ed 字节一致；sigstore 3.6.7、
  cryptography 46.0.7、rekor_types 齐备）。
- 关键区别：归档 verify_e1_fixtures.py 把任意 Exception 归为 DENY；本脚本区分
  RULE_DENY（规则命中）与 ERROR（缺依赖/路径/归档不全/运行错误）。
  本批 0 例 ERROR，三个预期 DENY 全部为规则命中：
  - same_image_new_instance → "exactly one target launch record is required"
  - hidden_stop → "history does not reproduce Quote RTMR2"
  - instance_mismatch → "launch record does not match current container/image"
- 四个预期 ALLOW 全部 ALLOW。每例 verify 6.2–10.7 ms、3–4 条归档 Rekor。
- 输出：seven-cases-decisions.csv（决策表）、seven-cases-reasons.json（原因表 +
  加载的验证器版本/摘要 + 命令 + timings）、verify-seven-cases.py。
- Fixed/Launch/History 为规则级比较，不称完整 Native SPIRE 在线对照；TDX Quote、
  REPORTDATA、challenge 新鲜度、PID/starttime、配置准入等外层检查不属于历史子验证器，
  已按历史 result.json 的 not_checked_here 字段分别说明。

## 三、S1 可引用结论确认与汇总更正（s1/）—— 可引用

> **600.281 秒内三次 Workload SVID 发布（三个不同 serial），Workload Quote 增量 0/0/0。**

- Provider 计数区间 1791531867654→1791532467935 ms（600.281 s），嵌套在 Helper
  journal 覆盖窗口内；同一 Provider 实例
  c379b316-287e-4c6b-821b-63ae5a51c45e:1280687:1791524628548924645、同一 socket、
  同一 agent；同一 Helper 订阅 76bc43416ab64049871e818f3bcd15d3、同一 unit、
  同一 Helper PID；launch/container/pid/start_time/policy 全一致；三个不同 serial
  全部落在区间内（15:49:04.796Z / 15:51:29.562Z / 15:53:41.584Z）；
  Workload attempted/generated/failed 增量 0/0/0（generation_elapsed_ns 不变），Node 0/0/0。
- 与业务连续性分开：180 实际发出、155 成功、25 UNKNOWN（窗内 19 + 窗外 6）、
  0 rejected/timeout/overload；coverage UNKNOWN、verdict FAIL 维持原判。
- IP1 更正追加：覆盖不足 120768 ms（原约 120 s 精化为该值）；IP1 客户端侧 900 s
  观测 8 个 serial——与服务端 3 次发布为不同测量面，分别引用。
- 第三次发布（15:53:41.584Z）在客户端测量冻结（15:52:47.347Z）之后，
  不描述为有完整客户端覆盖；S1 结论为服务端结论。

## 四、应用案例材料状态 —— 等待 IP1 交付，不阻塞

- 受保护通道当前最新材料为最小批次收据（final-receipt-minimal-20261009）。
- IP1 尚未交付：P0 原件清单/路径、旧召回原件可恢复性报告、客户端探针与
  trace.jsonl（E2 缺项）。
- 按协议不发起新请求、不启动采样窗口、不做健康检查（这些仅在 IP1 明确报告
  旧召回原件无法恢复时按条件执行）。
- 已有服务端关联索引（P0 负例 7/7 sha 3af191ef…、真实召回 sha 17f6c651…、
  A3/成本原件）见 a3-index/ 与最小批次 RESULTS.csv 的 s2/s3 行。
- 收到 IP1 材料后补一次联合索引；对方未交付不阻塞本次提交。

## 五、交付清单

| 路径 | 内容 |
|---|---|
| SUMMARY.md | 本摘要 |
| PAPER-METRICS.csv | 指标/数值/单位/样本窗口/证据类型/来源摘要/适用结论（22 行） |
| e2/ | E2 复算脚本、时间线 CSV、结果 JSON、来源索引 |
| e1-cases/ | 七例独立复核脚本、决策表、原因表、README |
| s1/ | S1 复核脚本、机器可读结果、结论与更正 |
| a3-index/ | A3 准入与单次成本来源索引（不重复采集） |
| SHA256SUMS | 本批次全部文件摘要 |

旧原件保持不变；本批不包含任何运行代码变更。
