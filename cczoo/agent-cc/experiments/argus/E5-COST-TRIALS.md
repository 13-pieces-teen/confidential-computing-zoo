# E5：三个历史长度点与共享待决窗口

这是实验工具使用说明，不是已完成结果。正常 API 成本继续使用 [E5-MEMORY-LOAD.md](E5-MEMORY-LOAD.md)；首次建立及恢复沿用 E3/E4 原件。这里的记录均不独立证明真实 TDX。

## 三个历史点

复制 [cost.history.example.json](examples/cost.history.example.json)。工具在服务 TDVM 上运行，只接受已构建并核验的 Full/native 实验部署。

`small/medium/large` 必须顺序固定。中、大两档的 `growth_argv_file` 是操作者冻结的 argv 数组列表，只能采用批准的无关工作负载及受支持生命周期路径；不要用原始 Docker socket 绕过记录，也不要操作被测目标。示例格式：

```json
[["python3", "/opt/approved-runtime/scripts/workload.py", "--config", "/secure/unrelated-workload.json", "launch"]]
```

具体参数以该安装版本 CLI 为准。增长活动可以包含已审核的受控清理，避免额外业务实例长期运行而混入资源开销。不要向 argv 写入 token；使用保护好的环境或凭据文件。三个点及所有命令在第一次操作前冻结；每条命令先保存意图，超时或结果未知立即停止，后继点保留 NOT_RUN，不自动重放。

```sh
python3 experiments/argus/cost_trials.py history --config /secure/history-full.json --output /secure/evidence/history-full-01
```

每个点执行：增长活动 → 有限等待全部记录确认 → 读取实际记录数 → 显式重建同一实例的 Helper 订阅 → 保存新准入阶段和就绪耗时 → 再读历史，确认测量期间目标及历史未变。增长前、准入前后重新读取部署、批准政策原件和已安装政策文件，与首次冻结值比较。准入结果未知时停止后继增长和订阅，保留后继点 NOT_RUN。这里重建订阅会影响入口，因此不同时作为正常访问成本或旧连接持续性样本。

pilot 使用 `expected_records: null` 确认实际记录数。正式运行设置 `mode: formal`、`frozen: true` 和三个严格递增的预定记录数；长度偏离时保留结果，不能继续凑到期望数字。`HISTORY_CAPACITY`、未知增长和超时都有原件，不删除最大档失败。

`series.json` 分别记录采集状态、准入判定、记录数/引用字节数、Provider 分段时间、准入至新入口就绪时间。Provider 分段时间必须唯一匹配本次 `accepted_nonce`，歧义或缺失为 UNKNOWN，不能借用另一轮重试的最后一条 ALLOW。引用字节数不等于完整历史字节数；Trustee 的 `fetched_bytes` 是实际拉取响应字节。`OBSERVED` 指观测完成，不能代替字段中的准入结果。

Trustee 在已有异步 evidence capture 中增加 `history-timing.json`，计时不进入证明政策或签名判定。`verify_ms` 包含拉取与验证，`verify_excluding_fetch_ms` 是扣除已计拉取后的墙钟耗时，不称纯 CPU 时间。把 IP1 的原始 nonce 目录传回后只读关联：

```sh
python3 experiments/argus/cost_trials.py history-collect --series-directory /secure/evidence/history-full-01 --trustee-captures /secure/transferred/trustee --output /secure/evidence/history-full-01/collected.json
```

此命令核对原准入文件、nonce 和 capture 哈希，不重新执行证明或离线计时来代替线上成本。native 没有自定义 Workload 历史评估，此项为 NOT_APPLICABLE；丢失导出为 UNKNOWN。

## 两服务同链待决

复制 [cost.shared.example.json](examples/cost.shared.example.json)。`deployment` 为 B，`other_deployment` 为 A，必须是同一 TC API/TruCon 控制路径上的不同服务实例。`pending_config` 指向 E1 的真实 `record_pending` 配置与已到达的 barrier 回执，不能手填 `pending=true`。

1. IP1 启动 B 的确定性非空记忆查询，`connection_mode=reuse`，使用同一个 run ID，保证窗口前已有成功请求，覆盖以下三个阶段。
2. IP2 对健康状态采 `before`；通过 E1 的实验屏障使 A 的实际操作保持待决，再采 `pending`；释放屏障并确认记录后采 `confirmed`。
3. 每次采样运行同一已批准 Helper 二进制的只取证 Broker 探针。探针不发布文件、不调用 hook、不停止原 Helper/NGINX。超时不能单独解释为政策拒绝，原因需要 E1 阶段日志。

```sh
python3 experiments/argus/cost_trials.py shared-snapshot --config /secure/shared.json --phase before --output /secure/evidence/before.json
# 通过 E1 让 A 的真实操作到达屏障。
python3 experiments/argus/cost_trials.py shared-snapshot --config /secure/shared.json --phase pending --output /secure/evidence/pending.json
# 释放屏障，等待确认。
python3 experiments/argus/cost_trials.py shared-snapshot --config /secure/shared.json --phase confirmed --output /secure/evidence/confirmed.json
```

共享窗口不重启 B 的原 Helper。pending 阶段在探针前后查询 A 的同一个真实 mutation；没有这条关联，不声称 A 导致共享阻塞。需要保留 E1 原件。工具保存 B 的进程绑定、Helper invocation、链快照和探针元数据。

IP1 正常结束负载后，传输单客户端 `load-result.json` 与 `requests.jsonl` 并核对哈希，再收集：

```sh
python3 experiments/argus/cost_trials.py shared-collect --before /secure/evidence/before.json --pending /secure/evidence/pending.json --confirmed /secure/evidence/confirmed.json --load-directory /secure/transferred/b-load --clock-uncertainty-ms 5 --output /secure/evidence/shared-collected.json
```

`5` 仅表示参数格式，必须替换为本次测得的时钟误差。统计只把已核验正常的 `before` 窗口内完成成功非空请求的 connection ID 算作旧连接；在 pending 开始后、首次 mutation 查询前才建立的连接也排除。窗口内新建连接另计；Helper 或目标变化、负载未完整结束、时钟边界重叠、mutation 关联缺失均保留 UNKNOWN。新订阅与既有流量分列，不能把 pending 自动解释为旧入口关闭，也不能由无读取推出未经准入接收量。

结果复用 E1 的待决窗口，保持原 run ID，不作为新的独立重复或多智能体协作实验。
