# E2 r4 离线复算 — 命令与来源索引

## 复算命令

```bash
# 1. 既有窗口完整性验证（原样运行，未修改）
cd <results>/ip2/batches/20261009-repair-and-rerun/preexisting/e2-r4
python3 verify_e2_r4_windows.py
# 结果: PASS: E2 r4 crops, request lifecycles, watermarks, and preserved UNKNOWN tail verified (rc=0)

# 2. 独立复算（本目录脚本，只读工作树材料）
cd /root/argus-paper-final-evidence-20261009t0841z/e2
python3 e2-recompute.py
# 输出: e2-recomputed.json + e2-timeline.csv
```

## 输入原件（全部来自结果分支 preexisting/e2-r4/，只读）

| 文件 | sha256 | 用途 |
|---|---|---|
| receiver/experiment-window.jsonl | a1735650… | 1113 行 record 162130–163242：请求进入/结束、54 个 http.request body_read、1 个 http.disconnect、252 水位、251 COMPLETE + 1 UNKNOWN 覆盖区间、receiver_stop |
| receiver/recovery-window.jsonl | e75ef2e6… | 41 行 record 17580–17620：两条恢复探针完整生命周期 + COMPLETE 覆盖 + 水位 |
| originals/fault.jsonl | 3c9caf27… | helper-freeze 注入（executed=true，started/complete 单调与墙钟） |
| originals/lifecycle.jsonl | 9b83b38c… | 观察者 203 个采样（含 backend_probe）与 detected/entry_stopped 轮询界 |
| originals/timeline.json | 4dc1ad53… | 历史分析输出（旧摘要，比对基准） |
| originals/result.json | 见 ip2 FILES.json | 历史评估输出（whole_window.last_body_read 等） |
| originals/assessment.json / state.json / final-closure.json / recovery-receiver-finalize.json | 见 ip2 FILES.json | 校验与恢复关联 |

## 历史分析器语义来源（只读检查，未修改）

experiments/argus/timeline.py，部署树 HEAD 12b365ed8d3c73965e69c8fbe3fcb46075fefa6c，
sha256 ef0785f8890d6f909d9f6ea2d042f30f5ffe9186ccdb88728eda70805cd6b091。

## 复算口径

- 相对时间基准：fault 命令调用（executed 行 started_monotonic_ns）；同机排序用单调时钟；
  跨机（客户端↔服务端）用墙钟 ±20 ms 界。
- 10 秒界限：fault completed + 10000 ms（单调）；保持不变。
- 读取事件过滤（历史口径）：received/body_read/http.request、kernel_process_and_deployment
  来源、探针已知 request_id、received_body_bytes>0。本作物中该过滤与不过滤结果一致
  （作物只含这类行），已在 e2-recomputed.json 记录。
- UNKNOWN 尾段保留：作物末尾覆盖区间保持 UNKNOWN/uncovered_final_or_crash_tail，
  未覆盖区间不算零读取。
- 后端健康计数按历史分析器 ±20 ms 双界复现：after_fault 180、after_entry_stop 163、
  unknown 0；原始单调计数为 181（边界样本被不确定界排除），两版均保留。

## 历史值对照结论（详见 e2-recomputed.json comparison 节）

- 入口关闭区间 4530.462→5145.519 ms：精确复现。
- 检测（readiness 撤回）区间 4831.708→5145.519 ms：精确复现。
- 关闭相对检测 close_after_detection [-615.057, 313.810] ms：精确复现。
- 最后应用读取 4612.611 ms：精确复现（最后 http.request body_read）；
  result.json 的 4837.674 ms 为在途请求 56561dc1 的 http.disconnect（0 字节，more_body=false），
  两个历史值均已解释并复现，非矛盾。
- 故障后 10 秒界内读取：18 请求 / 19 事件 / 4795 字节：精确复现。
- 10 秒界后读取：0 请求 / 0 事件 / 0 字节：精确复现（界后仍有 146 对请求进入/结束，
  但零 body_read，直到 receiver_stop 53633.5 ms）。
- 有效观察终点：COMPLETE 覆盖至 53419.373 ms（observed_to_ms，精确复现）；
  历史 observation_end_ms 51410 = 客户端最后一个探针请求完成（墙钟相对 51390 ms）+ 20 ms
  不确定界——该值来自客户端探针原件，不在本作物中（见缺项）。
- 故障命令完成 4.5415 ms：精确复现。
- 入口关闭后后端健康：163 个采样 OBSERVED（±20 ms 界口径），live_after_entry_stop=OBSERVED。

## 缺项（不注入故障、不补造）

1. trace.jsonl（客户端 HTTP 读取事件，sha eb5ccb26…）——未随作物导出；
   client_reads（47 事件/18348 字节、post_fault 18 请求/7028 字节、最后读取 4807–4847 ms）
   保持历史值，待 IP1 客户端原件关联。
2. 客户端探针 jsonl（type=request 行）——observation_end_ms 51410 的原始来源；
   待 IP1 原件后重导。
