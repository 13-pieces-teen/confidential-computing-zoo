# E5：分开连接、真实记忆查询和 Agent 任务

从 `cczoo/agent-cc` 执行。先通过随机事实业务验收，以实际已提取且可查询的记忆准备固定查询。请求文件位于受保护目录，不把 API key 或私有正文写入仓库。以下对象是固定查询格式，`query` 使用已写入事实的项目标记，问题不包含预期答案：

```json
{"query":"已写入的项目标记","target_uri":"viking://user/memories","limit":5}
```

复制 `examples/suite.performance.example.json`，保留各组已经隔离的身份和用户，设置：

```json
{
  "frozen": false,
  "connection_mode": "reuse",
  "workload_kind": "memory_query",
  "path": "/api/v1/search/find",
  "body_files": {
    "alice": "/secure/fixtures/alice-memory-query.json"
  },
  "rate": 1,
  "concurrency_per_client": 8,
  "warmup_seconds": 30,
  "measurement_seconds": 120,
  "timeout_seconds": 10
}
```

先做预实验确认每个用户返回叶级记忆，再固定相同到达负载，将 `frozen` 改为 true，生成并 prepare 正式套件。`new` 和 `reuse` 使用不同实验目录、相同模型/数据/权限/负载/种子；不要把它们放在一个汇总分母中。统计按接口种类和连接模式分层。

```sh
python3 experiments/argus/suite.py --config /secure/cost-memory-reuse.json --output /secure/generated-cost-reuse
python3 experiments/argus/runner.py prepare --config /secure/generated-cost-reuse/suite.json --output /secure/evidence/cost-reuse
# 按生成 fleet 部署，依次激活对应组，再按 manifest 运行具体 run_id。
python3 experiments/argus/runner.py preflight --output /secure/evidence/cost-reuse --role client --run-id RUN_ID
python3 experiments/argus/runner.py run --output /secure/evidence/cost-reuse --role client --run-id RUN_ID
python3 experiments/argus/runner.py collect --output /secure/evidence/cost-reuse
python3 experiments/argus/runner.py analyze --output /secure/evidence/cost-reuse
python3 experiments/argus/plot.py --output /secure/evidence/cost-reuse
```

负载工具每个客户端/worker 持有独立连接，记录实际 connection ID、是否复用、重连原因、TCP/TLS/API 时间。服务端关闭连接或凭据 generation 变化会使下一次请求重新建连；失败的 POST 不自动重放。`reuse` 是请求策略，不保证服务器实际保持每条连接；报告必须列实际复用数。

`memory_nonempty_goodput_rps` 只计测量窗口内成功且返回叶级记忆的查询。HTTP 成功但空召回作为该负载失败单列，不能用状态 API 的高吞吐替代记忆业务。它仍不表示回答正确：完整模型任务由 E4 六步工作项另测。

`examples/suite.performance.example.json` 当前默认是 Full/native、`reuse/memory_query` 和单客户端。状态 API 可另行单列探活开销。控制面 Quote/Trustee/命令至就绪计时见 [E3 配方](examples/E3-LIFECYCLE-RECIPE.md#small-control-plane-cost-measurements)，不得将 Trustee RTT 当成纯证明 CPU 耗时。三个历史长度点与两个服务共享待决观测使用 [cost_trials.py 配方](E5-COST-TRIALS.md)。远程性能当前为 NOT_RUN。
