# S1 身份复用结论 — 复核确认与汇总更正（IP2）

## 复核命令

```bash
python3 /root/argus-paper-final-evidence-20261009t0841z/s1/verify-s1-identity-reuse.py
# 输出: s1-recheck.json（全部核查项 true，原文见 provider/helper 原件）
```

输入为最小批次服务器原件（只读，sha256 已记录并与 observation.json 中的记录值一致）。

## 可引用结论（已确认，可直接引用）

> **600.281 秒内三次 Workload SVID 发布（三个不同 serial），Workload Quote 增量 0/0/0。**

支撑链条（全部来自原件，见 s1-recheck.json）：

1. **计数区间**：Provider 计数接口 before `captured_at_ms=1791531867654` →
   after `captured_at_ms=1791532467935`，时长 600281 ms = **600.281 s**；
   该区间嵌套在 Helper journal 覆盖窗口 [1791531867336, 1791532468051] 内。
2. **同一 Provider**：前后快照 `provider_instance_id` 完全一致
   `c379b316-287e-4c6b-821b-63ae5a51c45e:1280687:1791524628548924645`；
   socket 路径、agent SPIFFE ID 均一致；instance 的 boot 段与 Helper anchor
   `boot_id=c379b316287e4c6b821b63ae5a51c45e` 一致（同一次启动）。
3. **同一 Helper 订阅与目标**：三条发布记录 `_SYSTEMD_INVOCATION_ID` 均为
   `76bc43416ab64049871e818f3bcd15d3`（= 订阅 ID），同一 unit
   `ax-paper02-full-helper.service`、同一 Helper PID、同一 boot；
   `launch_id=launch-a4ba2ba`、container、pid、start_time、policy 全部一致。
4. **三次不同发布**：serial 分别为 80937547914853678949157948885751517620 /
   326027069949880564519896745254572331883 /
   117168169887379234192897520659204972313（三个不同值）；
   实时戳 15:49:04.796Z / 15:51:29.562Z / 15:53:41.584Z，全部落在计数区间内。
5. **Workload Quote 增量 0/0/0**：before/after 均为 attempted=2, generated=2,
   failed=0（generation_elapsed_ns 同为 2054669555）→ 增量 0/0/0；
   Node 增量同样 0/0/0。区间内没有新的 Workload Quote 调用。

## 与业务连续性分开（不合并、不改判）

- 业务侧 180 条计划请求实际全部发出：**155 成功 / 25 UNKNOWN**
  （= 窗内 19 + 窗外 6），0 rejected/timeout/overload。
- 业务连续性覆盖率 **UNKNOWN**、判定 **FAIL** 维持原状：
  客户端测量冻结 1791532367347 早于我方 completed_at_ms+20000=1791532488115，
  端侧尾部未覆盖（IP1 更正为 **120768 ms** 覆盖不足）。
- 身份复用结论是**服务端结论**（Helper journal + Provider 计数接口），
  不依赖客户端覆盖；业务 FAIL/UNKNOWN 不影响其成立，两轴分别引用。

## 第三次发布的客户端覆盖（不夸大）

第三次发布（15:53:41.584Z）发生在客户端测量冻结（15:52:47.347Z）**之后**，
没有端到端客户端覆盖记录。结论表述为"服务端观测到三次发布"，**不**描述第三次
发布具有完整客户端覆盖。

## IP1 更正（追加，来源：IP1-FINAL-RECEIPT）

- 覆盖不足量更正为 **120768 ms**（原批次 RESULTS.csv 记为约 120 s，见
  business_max_probe_gap 行，evidence 字段已含冻结时间戳）。
- IP1 客户端侧 900 s 内观测到 **8 个 client-side serial**——这是客户端观测到的
  SVID 代次变化，与上表服务端 journal 的 3 次发布是**不同测量面**，分别引用，
  不合并为一个数。

## 输出

- verify-s1-identity-reuse.py — 复算脚本（只读）
- s1-recheck.json — 机器可读复核结果（全部核查项、原件 sha256、结论与更正）
