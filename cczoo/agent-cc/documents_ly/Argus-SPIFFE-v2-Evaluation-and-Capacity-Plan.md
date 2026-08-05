# Argus-SPIFFE v2 可量化评测与容量测试计划

## 1. 文档目的

本文定义 Argus-SPIFFE v2 当前阶段可以落地的性能评测、容量搜索和结果留痕方案，通过可复现测试回答以下问题：

1. 单个 OpenViking 在 `argus_tdx` RATS 链路下的成功率、P95 和 P99 是多少；
2. OpenViking 完成 Attestation 后，首个正确 workload X.509-SVID 需要多久；
3. 多个 OpenClaw 部署实例访问单个 OpenViking 时，系统容量边界在哪里；
4. OpenClaw 数量和业务并发增长时，具体影响哪些组件和指标；
5. OpenClaw 侧的首个容量拐点和最终瓶颈分别是什么；
6. 如果未来扩展到多个 OpenViking/TDVM，应如何单独测量 `argus_tdx`
   NodeAttestor 的并发容量。

本文是评测实施计划，不包含尚未执行的性能结论。所有最终数字必须来自 Linux
服务器上的真实运行产物，不得使用配置值、目标值或单次成功日志代替测量结果。

本文与
[Argus-SPIFFE-v2-Pre-RA-Hardening-Plan.md](Argus-SPIFFE-v2-Pre-RA-Hardening-Plan.md)
配套使用。Pre-RA 计划定义被测系统在 mock RA 条件下必须满足的安全、隔离、
生命周期和审计门槛；本文定义在这些门槛之上如何采集性能、容量和稳定性数据。
安全加固验收不能替代性能评测，性能通过也不能替代安全正确性验收。

## 2. 当前评测边界

### 2.1 本阶段纳入

- TDVM 内的 `argus_tdx` NodeAttestor 协议与插件链路；
- mock Evidence Provider；
- mock Trustee；
- 单个 OpenViking 的多轮独立冷启动 RATS；
- OpenViking Agent 准入、registration entry 和 workload SVID 获取；
- 多个独立 OpenClaw 身份域的批量启动和 workload SVID 获取；
- 多个 OpenClaw 部署单元经 Guard-gated egress 到单个 OpenViking 的
  SPIFFE mTLS 请求；
- `mock_allow` 条件下 Guard 决策与同一业务请求的强制绑定和 fail-closed 行为；
- OpenClaw 到 OpenViking 已知明文、直连和跨身份旁路的拒绝验证；
- SVID 轮换、到期、Agent/Server 重启、Workload API 故障、entry 删除、
  Agent ban/delete 和旧连接排空的收敛测量；
- binding store 并发、重启、损坏和 clone conflict 的正确性验证；
- run 级 runtime 隔离与 run 内 OpenClaw Deployment Unit 隔离；
- 从 OpenClaw 请求、Guard、mTLS egress 到 OpenViking marker 的结构化审计关联；
- SPIRE、Provider、Trustee、OpenViking 和 Linux 主机资源观测；
- 冷启动、突发启动、限速启动和稳定性测试；
- 原始样本、Prometheus 指标和汇总报告。

### 2.2 本阶段暂缓

- 真实 TDX Quote/QGS 性能；
- production Trustee 和真实 TCB 验证性能；
- Quote、TCB collateral 和远程证明网络抖动；
- Guard `spiffe_identity` 和 `fresh_evidence` 生产模式；
- Guard 基于真实 Quote、TCB、measurement、RTMR 或正式 Trustee 的决策真实性；
- 面向生产 profile 的 Envoy/service mesh 强制执行面；
- 周期 re-attestation；
- 多 OpenViking 服务实例和跨区域部署；
- 多 SPIRE Server 副本和共享事务 binding store；
- 生产环境容量承诺。

因此，本阶段结果必须标记为：

```text
Runtime environment: Linux Host + OpenViking TDVM
TD Guest device: required (/dev/tdx_guest)
Attestation profile: mock evidence / mock Quote verification
Evidence Provider: mock
Trustee: mock
Business path: Guard-gated OpenClaw egress -> SPIFFE mTLS -> OpenViking
Guard verification mode: mock_allow
Pre-RA hardening gates: required for formal B/C business results
Real Quote/QGS: deferred
Production capacity acceptance: not established
```

`mock` 只表示 Evidence Provider、Quote 内容和 Trustee 判定尚未接入生产实现，
不表示 A 类实验可以脱离 TDVM 在任意普通 Linux 容器中运行。

### 2.3 当前仓库实现基线

截至 2026-08-05，当前 `feat/argus-spiffe-v2`、HEAD `2f4c171` 已提供：

- 单 OpenViking `argus_tdx` Agent/Server NodeAttestor 链路；
- TDVM 内 mock Evidence Provider；
- 中心侧独立 mock Trustee；
- 单 OpenClaw `x509pop` Agent；
- 单 OpenClaw Guard-gated mTLS egress proxy；
- 固定 OpenClaw/OpenViking workload registration entries；
- SVID 正向、身份隔离和 mTLS 负向验证脚本；
- NodeAttestor attempts、duration、evidence bytes 和 Trustee request 基础埋点；
- 真实 OpenClaw 插件在 mock RA 条件下写入真实 OpenViking 的 E2E 证据；
- Guard `authorization_context`、独立业务请求摘要、精确 target 绑定、
  `decision_id` 和有界决策有效期的源码实现；
- Guard DENY、503、timeout、malformed、missing receipt、digest mismatch 和
  expired receipt 的远程故障矩阵脚本。

当前未提供：

- WP1 当前源码在 Linux/TDVM 上的完整重新编译和远程验收结论；
- WP2 到 WP8 的完整实施与远程验收；
- RATS benchmark controller；
- run-scoped OpenViking 冷启动生命周期；
- N 个 OpenClaw Unit 的配置、证书和容器生成器；
- 高精度 T0-T3/C0-C3 receipt；
- Provider/Trustee duration 与 in-flight 指标；
- mTLS 并发/QPS load generator；
- `core/spire/benchmarks/` 目录和正式结果 schema。

因此，本计划的目标是复用现有协议、插件和单实例验证链，在其上新增评测层；
不得把当前固定 Compose 直接视为已经支持容量测试。

### 2.4 Linux/TDVM 环境前置条件

A 类 OpenViking RATS 实验要求：

- Linux Host 可运行 Docker；
- 已启动并可通过 SSH 访问 OpenViking TDVM；
- TDVM 内存在 `/dev/tdx_guest`；
- TDVM 内可运行 Docker；
- SPIRE Server、mock Trustee 和 mock Evidence Provider 已完成预热；
- 所有测试镜像在正式计时前完成构建和传输；
- Host 与 TDVM 完成时间同步检查。

B/C 类轻量 OpenClaw 容量实验主要运行在 Linux Host；OpenViking 仍位于已经完成
RATS 的 TDVM 内。若缺少 TDVM，只能执行单元测试和软件协议夹具，不能生成本计划
定义的正式 A/B/C 运行结果。

### 2.5 Pre-RA 加固门槛与评测依赖

Pre-RA 工作包不是一律阻塞全部评测，而是按被测对象形成以下依赖：

| 评测对象 | 正式样本前置门槛 | 说明 |
| --- | --- | --- |
| A 类单 OpenViking RATS | WP4 中与 binding/合法轮换相关的语义冻结；WP5 run-scoped 隔离；WP6 结构化事件与产物 | 不依赖 Guard 数据面门控，但不能通过清空 binding store 制造样本 |
| B 类 OpenClaw 身份启动 | WP5 run/runtime 隔离；WP6 统一 receipt 和指标 | 只测 x509pop 准入和 SVID 就绪时不经过业务 Guard |
| C 类业务请求容量 | WP1 同请求 Guard 门控；WP2 唯一受控路径；WP5 隔离；WP6 审计 | 正式业务样本必须经过 Guard-gated egress；纯 mTLS 只能作为诊断基准 |
| 生命周期与拒绝收敛 | WP3 生命周期实现与 SLA 冻结 | rotation、ban、entry 删除和旧连接排空单独成组 |
| canary、切换和回滚 | WP7 可重复脚本和版本留痕 | 形成独立运维验收报告，不混入容量基线 |
| future real RA | WP8 接口边界加真实 Quote/QGS/Trustee | 使用独立 profile 和结果集，不与 mock 样本混合 |

WP5 的“多 runtime”指多个完整 run/runtime 之间的隔离；B 类的
`N_openclaw` 指同一个 benchmark run 内的 N 个 OpenClaw Deployment Unit。
两层都使用 `RUN_ID`/`unit_id` 分区，但不能把二者计数互换。

## 3. 必须分开的评测对象

当前架构中，自定义 `argus_tdx` NodeAttestor 只用于 OpenViking 服务侧。
OpenClaw 调用侧不经过该 RATS 链路。因此：

```text
增加 OpenClaw 数量
  != 增加 argus_tdx Node Attestation 数量
  != 增加 OpenViking RATS 并发数
```

“OpenViking RATS 性能”“OpenClaw 部署数量”和“业务请求并发数”必须分开。
否则容易出现启动了 100 个 OpenClaw，却错误声称触发了 100 次自定义 Node
Attestation 的结论。

### 3.1 A 类：单 OpenViking RATS 性能与稳定性

目标是测量一个 OpenViking SPIRE Agent 通过 `argus_tdx` 完成 RATS 准入和
服务 SVID 获取的性能。

当前使用：

```text
M_openviking = 1
R = 多轮独立冷启动
```

每轮必须拥有：

- run-scoped OpenViking SPIRE Agent `data_dir`；
- run-scoped Workload API socket；
- 新生成的 Attestation Key；
- 本轮唯一且可审计的 Agent ID；
- 不复用上次 Agent SVID 或 workload SVID；
- run-scoped Agent container、workload container 和 registration entry；
- 独立的事件记录和 SVID probe；
- 显式清理或撤销本轮 Server 侧 Agent/entry。

这里的“冷启动”定义为 identity cold start：

```text
TDVM 不重启
SPIRE Server 不重启
Trustee/Provider 不重启
镜像不重新传输

只重建：
  OpenViking Agent data
  Attestation Key
  Agent SVID
  Workload API socket
  registration entry
  workload SVID
```

这样能够隔离 RATS 与身份签发开销，避免把 TDVM boot、镜像传输和 Provider 启动
时间混入 Node Attestation。

该实验回答：

- 单 OpenViking RATS 准入成功率；
- `argus_tdx` Node Attestation P50/P95/P99；
- Attestation 到 OpenViking workload SVID 的 P50/P95/P99；
- Provider、Trustee 和 SPIRE Server 的分段耗时；
- 多轮冷启动下的 retry、timeout 和稳定性。

该实验不回答“同时支持多少个 `argus_tdx` Agent”。R 是重复采样次数，不是
并发 OpenViking 数量，也不是 NodeAttestor 容量。

### 3.2 B 类：OpenClaw 部署容量

目标是模拟企业中“多个 OpenClaw 调用一个 OpenViking”的部署形态。

一个 OpenClaw Deployment Unit 至少表示：

- 一个独立 OpenClaw 调用实例或轻量等价客户端；
- 一份独立 x509pop 叶证书和私钥；
- 一个独立 SPIRE Agent 身份域和唯一 Agent ID；
- 独立 Agent 状态目录和 Workload API socket；
- 一个持有 OpenClaw 侧 workload SVID 的独立 Guard-gated mTLS egress proxy；
- 一个经 caller-side Guard ALLOW 后到 OpenViking 的 SPIFFE mTLS 路径。

当前仓库中，SVID 的实际持有者是 `openclaw-mtls-client` egress proxy，
OpenClaw 应用容器本身不挂载 Workload API socket。因此本计划统一使用：

```text
OpenClaw-side workload SVID
  = OpenClaw mTLS egress proxy workload SVID
```

不得将其写成“OpenClaw 应用进程直接持有 SVID”。

本阶段冻结为“独立 Agent、共享业务 workload identity”模型：

```text
N unique x509pop Agent IDs
N run-scoped registration entries
N isolated Workload API sockets
N mTLS egress proxies

shared workload SPIFFE ID:
  spiffe://argus.local/agent/openclaw
```

共享 workload SPIFFE ID 表示这些 Unit 是同一种 OpenClaw 调用方的多个部署副本。
每个 Unit 通过 `unit_id`、Agent ID、entry ID、容器名和 receipt 区分。简历和报告
只能声称“N 个独立 OpenClaw Agent 身份域”，不能声称“N 个不同业务 SPIFFE
身份”。

采用该模型可以保留 OpenViking 当前对
`spiffe://argus.local/agent/openclaw` 的精确客户端 ID 授权。如果未来要给每个
Unit 分配唯一 workload SPIFFE ID，必须另行修改 registration、SVID 校验和
OpenViking allowlist；不与本轮基线数据混合。

当前 v2 设计中 OpenClaw Agent 使用 `x509pop`，OpenViking Agent 使用
`argus_tdx`。因此该实验能够衡量整个部署单元的身份启动和调用容量，但不能把
OpenClaw 侧的 `x509pop` 准入结果写成 TDX RATS 性能。

该实验回答：

- 单台 Linux 服务器能够并行运行多少个独立 OpenClaw 部署单元；
- 多个 OpenClaw-side egress proxy 首次获取 SVID 的完成速度；
- 多个 OpenClaw 同时建立 mTLS 连接时的成功率和延迟；
- 单个 OpenViking 的连接数、QPS 和资源瓶颈。

### 3.3 C 类：业务请求容量

目标是测量已经完成身份启动的 OpenClaw workload 对单个 OpenViking 发起请求时
的运行容量。

第一阶段只使用确定性的 session、最小业务写入或等价请求，避免模型推理延迟、
第三方 API 限流和 token 成本污染身份基础设施结果。正式 C 类请求必须经过
Guard-gated egress，并产生 Guard decision、SPIFFE mTLS 和 OpenViking marker
的同请求证据。纯 health 或绕过 Guard 的 mTLS 请求只能作为协议诊断，不进入正式
C 类容量结论。完整 OpenClaw/LLM 请求只在代表性规模点补测。

该实验回答：

- 已就绪身份下的最大稳定并发连接数；
- 单个 OpenViking 的稳定 QPS；
- Guard 决策、mTLS 握手和 OpenViking 服务各自增加的延迟；
- Guard 在并发增长时的吞吐、错误、timeout 和排队边界；
- mTLS 握手和复用连接的成本差异；
- OpenViking P95/P99、错误率和资源水位随并发增长的变化。

### 3.4 D 类：多 OpenViking RATS 控制面容量（后续扩展）

只有未来存在多个独立 OpenViking/TDVM 时，自定义 NodeAttestor 的并发容量测试
才成立：

```text
M independent OpenViking/TDVM instances
  -> M independent argus_tdx SPIRE Agents
  -> one SPIRE Server
  -> one Trustee
```

届时每个 OpenViking/TDVM 必须拥有独立 Agent 状态、证明密钥、Agent ID、
Workload API 和服务 SVID，再对 M 做指数扩容。

该实验可回答：

- `argus_tdx` 最大稳定并发准入数；
- Trustee 和 SPIRE Server 的 RATS 控制面吞吐；
- 多 OpenViking 批量准入的 P95/P99 和容量拐点；
- `M_max_stable` 及其资源瓶颈。

D 类不属于当前“多 OpenClaw、单 OpenViking”的主验收范围，不得用 OpenClaw
实例模拟 OpenViking RATS 节点。

## 4. 评测拓扑

### 4.1 A 类实验拓扑

```text
one OpenViking/TDVM
  -> one argus_tdx SPIRE Agent
  -> mock Evidence Provider
  -> one SPIRE Server
  -> one mock Trustee
  -> Workload API
  -> OpenViking service SVID probe

Repeat R independent cold-start runs
```

### 4.2 B/C 类实验拓扑

```text
N OpenClaw Deployment Units
  -> N unique x509pop Agent IDs
  -> N isolated Workload API sockets
  -> N Guard-gated mTLS egress proxies
  -> shared OpenClaw workload SPIFFE ID
  -> configured caller-side Argus Guard instance set
  -> Guard ALLOW bound to the same business request
  -> SPIFFE mTLS requests
  -> one OpenViking service
  -> one OpenViking argus_tdx identity

Identity center
  -> one SPIRE Server
  -> one mock Trustee
```

默认先使用轻量 Deployment Unit 搜索容量边界，再选择少量规模点替换为完整
OpenClaw 进程。这样能够区分身份基础设施瓶颈与 OpenClaw/模型运行开销。

当前固定 `compose.center.yaml` 只保留为单实例功能基线，不使用
`docker compose --scale`。Benchmark launcher 必须为每个 Unit 生成：

- 唯一 x509pop 叶证书和私钥，共享现有 x509pop CA；
- 唯一 Agent 配置、data directory、socket、容器名和 metrics 端口；
- 唯一 `entry_id`，父节点指向该 Unit 的 Agent ID；
- `argus.workload=openclaw` 和 `argus.benchmark.unit=<unit_id>` selectors；
- 唯一 mTLS proxy 容器名、监听端口和允许的 OpenClaw source IP；
- Guard endpoint、Guard instance count、请求绑定合同版本和 timeout；
- run-scoped 网络地址和原始 receipt。

完整 OpenClaw 多实例阶段使用一个 run-scoped bridge network、唯一 OpenClaw IP
和每 Unit 唯一 proxy listen port。不能复用当前单实例的固定容器名、
`172.31.44.2` source IP 或 `1934` proxy port。

Guard 的实例基数必须在正式测试前冻结。当前单实例共享 Guard 可以作为首版基线；
若后续改为每 Unit 一个 Guard 或 Guard pool，必须建立新的拓扑 profile，不与共享
Guard 结果合并。所有正式 C 类业务请求都必须经同步 Guard 决策；仅用于诊断的
Guard-bypass/mTLS-only 测试必须使用独立 scenario 名称和结果集。

### 4.3 D 类后续扩展拓扑

```text
M independent OpenViking/TDVM instances
  -> M independent argus_tdx identities
  -> one SPIRE Server
  -> one mock or production Trustee
```

该拓扑只在多 OpenViking/TDVM 场景进入范围后实施。

## 5. 指标合同

### 5.1 OpenViking RATS 主指标

#### RATS 冷启动成功率

当前 A 类实验同时报告 RATS 协议成功率和最终身份就绪率：

```text
openviking_rats_success_rate
  = runs_returning_fresh_agent_attributes / started_identity_cold_starts

openviking_identity_ready_rate
  = runs_with_valid_agent_and_expected_workload_svid / started_identity_cold_starts
```

二者不能合并。NodeAttestor 已返回 AgentAttributes，但 Agent SVID、registration 或
workload SVID 后续失败时，RATS 可以计为成功，identity ready 必须计为失败。

不得直接用“成功 attempt 数 / 总 attempt 数”代替。单轮 OpenViking 冷启动中，
SPIRE Agent 自动重试可能触发多次 Node Attestation attempt，但最终只对应一个
冷启动结果。

每组 RATS 冷启动至少记录：

```text
R_started
R_attested
R_identity_ready
R_timeout
R_failed
R_retried
R_stale_identity
R_wrong_identity
```

辅助指标：

```text
attempt_success_rate
  = successful_attestation_attempts / all_attestation_attempts
```

`openviking_rats_success_rate` 反映 RATS 协议是否完成；
`openviking_identity_ready_rate` 反映完整身份链是否就绪；辅助指标反映内部重试
和瞬时失败情况。

#### NodeAttestor 插件耗时

```text
plugin_attestation_ms
  = Server plugin 开始处理 AgentHello
  到返回 AgentAttributes
```

输出：

- count；
- P50；
- P95；
- P99；
- max；
- 按 `side/result/reason` 聚合的成功和失败分布。

成功和失败耗时必须分开，避免超时失败样本抬高“成功 Attestation 延迟”。
主报告的 P50/P95/P99 只基于成功 run；失败和 timeout 单独报告数量、比例、
失败阶段与耗时分布，不得静默丢弃。

#### Agent 准入耗时

```text
openviking_agent_admission_ms
  = 调度器发出 OpenViking Agent 启动命令
  到控制器首次从 SPIRE Server 观察到该 OpenViking Agent 为 valid
```

该指标包含进程启动、插件通信、Provider/Trustee 调用和 SPIRE Server 准入开销，
更接近 OpenViking 服务部署侧的用户感知。

### 5.2 Attestation 到 SVID 的端到端指标

A 类 OpenViking RATS 实验统一定义四个控制点：

```text
T0 = 调度器发出 OpenViking Agent 启动命令
T1 = 控制器首次观察到 OpenViking Agent 已 valid
T2 = OpenViking workload registration entry 已可用
T3 = probe 首次获取到预期 OpenViking SPIFFE ID 的有效 X.509-SVID
```

由此计算：

```text
openviking_agent_admission_ms     = T1 - T0
openviking_registration_ms        = T2 - T1
openviking_svid_delivery_ms       = T3 - T2
openviking_attestation_to_svid_ms = T3 - T1
openviking_bootstrap_to_svid_ms   = T3 - T0
```

`openviking_attestation_to_svid_ms` 是 RATS 主报告使用的端到端指标。
`openviking_registration_ms` 和 `openviking_svid_delivery_ms` 用于判断延迟来自
动态注册、CA 签名、Workload API 还是客户端轮询。

SVID 成功必须同时满足：

- SPIFFE ID 与该轮 OpenViking 的预期服务 ID 完全一致；
- 证书仍在有效期内；
- 证书不是上一次运行残留；
- trust bundle 可用；
- probe 只读取本轮 OpenViking Agent 的 Workload API socket。

### 5.3 批量就绪指标

批量就绪指标主要用于 B 类多 OpenClaw 实验：

```text
openclaw_units_ready_per_second
time_to_50_percent_ready
time_to_95_percent_ready
time_to_all_ready
```

如果部分 OpenClaw Deployment Unit 永远未就绪，`time_to_all_ready` 记为
timeout，不得只对成功子集计算后宣称整批完成。

D 类多 OpenViking 扩展实验可以使用独立指标：

```text
openviking_nodes_ready_per_second
```

不能将 B 类 `openclaw_units_ready_per_second` 写成 `argus_tdx` NodeAttestor
就绪吞吐。

### 5.4 OpenClaw 到 OpenViking 指标

身份启动阶段：

- OpenClaw Deployment Unit 启动成功率；
- OpenClaw `x509pop` Agent valid 成功率；
- OpenClaw-side egress proxy workload SVID 成功率；
- `openclaw_bootstrap_to_svid_ms` P50/P95/P99；
- 单位时间内完成身份就绪的 Deployment Unit 数。

OpenClaw 使用独立时间点，避免与 OpenViking RATS 的 T0-T3 混淆：

```text
C0 = 调度器发出 OpenClaw Deployment Unit 启动命令
C1 = 控制器首次观察到该 OpenClaw x509pop Agent 已 valid
C2 = 对应 OpenClaw workload registration entry 已可用
C3 = probe 首次获取到该 OpenClaw 的预期 X.509-SVID

openclaw_agent_admission_ms  = C1 - C0
openclaw_registration_ms     = C2 - C1
openclaw_svid_delivery_ms    = C3 - C2
openclaw_admission_to_svid_ms = C3 - C1
openclaw_bootstrap_to_svid_ms = C3 - C0
```

这些指标属于 OpenClaw 身份启动，不属于 `argus_tdx` RATS 指标。

mTLS/业务阶段：

- 连接建立成功率；
- TLS handshake P50/P95/P99；
- Guard ALLOW/DENY/error/timeout 数量和比例；
- `guard_decision_ms` P50/P95/P99；
- `guard_to_forward_ms` P50/P95/P99；
- 请求成功率；
- `guarded_request_e2e_ms` P50/P95/P99；
- stable QPS；
- 活跃连接数；
- peer SPIFFE ID 错误数；
- 证书或 bundle 获取失败数；
- OpenViking 5xx、timeout 和 connection reset 数。

时间口径冻结为：

```text
G0 = egress 完成请求冻结并开始调用 Guard
G1 = egress 收到并校验完整 Guard decision receipt
G2 = egress 开始发送同一个被冻结请求的 mTLS 转发
G3 = egress 收到 OpenViking 最终响应

guard_decision_ms      = G1 - G0
guard_to_forward_ms    = G2 - G1
guarded_request_e2e_ms = G3 - G0
```

`mtls_handshake_ms` 继续作为协议分段指标；它不能替代
`guarded_request_e2e_ms`。如需测量不经过 Guard 的 mTLS 极限，只能使用
`diagnostic_mtls_only` scenario，不进入正式 C 类业务容量结论。

连接模式必须拆分：

1. 每个请求新建 mTLS 连接；
2. 复用长连接；
3. 固定 QPS；
4. 固定并发数。

### 5.5 身份和门控正确性硬指标

以下指标必须为零，任何一项非零都不能判为评测通过：

```text
duplicate_agent_id
wrong_agent_parent
wrong_workload_svid
cross_agent_socket_access
unexpected_peer_spiffe_id
stale_svid_accepted
plaintext_request_accepted
guard_bypass_request_accepted
guard_failure_forwarded
guard_digest_mismatch_forwarded
guard_expired_decision_forwarded
direct_openviking_request_accepted
unauthorized_openviking_marker_written
audit_correlation_missing
```

性能下降可以形成容量拐点；身份串用、Guard fail-open、旁路成功或同请求审计链
缺失属于正确性失败，不能用提高超时、重试次数或删除失败样本掩盖。

`audit_correlation_missing` 的判定对象是正式 C 类成功请求：每个成功请求必须能以
同一个 `request_id` 关联 Guard `decision_id`、`request_digest`、mTLS 转发和
OpenViking session/message/commit marker。拒绝请求则必须关联到明确拒绝阶段，且
OpenViking 不存在对应 marker。

### 5.6 生命周期与拒绝收敛指标

生命周期场景与 bootstrap、普通 steady-state 分开，至少记录：

```text
svid_rotation_interruption_ms
agent_restart_recovery_ms
workload_api_failure_to_deny_ms
agent_ban_to_new_connection_deny_ms
entry_delete_to_new_connection_deny_ms
old_connection_drain_ms
bundle_update_convergence_ms
```

每个场景必须同时记录正向、负向和恢复结果。Agent ban、entry 删除或 SVID 到期后，
只检查新进程或新连接不够；还必须检查测试开始前已建立的 keep-alive/TLS 连接。
连接最大生命周期、idle timeout、重试上限和 SVID TTL 必须进入 manifest。

这些收敛指标是 mock-stage 身份生命周期与执行面指标，不代表发生了新的
`argus_tdx` Node Attestation，也不代表真实 Quote 被重新验证。

### 5.7 资源和瓶颈指标

SPIRE Server：

- CPU、RSS、goroutine、线程数和文件描述符；
- gRPC 请求速率和错误码；
- A 类中的 NodeAttestor 单链路分段耗时；
- D 类扩展中的 NodeAttestor 并发数和排队时间；
- datastore 写入耗时、锁等待、数据库大小；
- CA/SVID 签名速率和延迟；
- 进程重启和 OOM。

mock Evidence Provider / Trustee：

- 请求速率；
- in-flight 请求数；
- P50/P95/P99；
- timeout 和 5xx；
- CPU、RSS、文件描述符；
- 排队长度。

Argus Guard / Guard-gated egress：

- Guard 请求速率、in-flight 和队列长度；
- ALLOW、DENY、error、timeout 和 malformed receipt；
- decision P50/P95/P99；
- request digest mismatch 和 expired receipt；
- egress body buffer、活动连接、goroutine/线程、CPU、RSS 和文件描述符；
- Guard 或 egress 重启、OOM 和 fail-closed 数量。

SPIRE Agent / OpenClaw：

- 单实例和总 CPU/RSS；
- 启动失败和重试数；
- Workload API 请求速率和延迟；
- SVID 获取失败；
- 容器/进程重启；
- socket 和文件描述符数量。

OpenViking：

- 请求 QPS、in-flight 和活跃连接；
- 请求 P50/P95/P99；
- 4xx/5xx/timeout；
- CPU、RSS、线程和文件描述符；
- mTLS 握手失败；
- 连接接受队列和端口耗尽。

Linux 主机：

- CPU 使用率和 load average；
- 可用内存、swap 和 OOM；
- 磁盘 IOPS、吞吐和空间；
- 网络吞吐、丢包、重传；
- PID、文件描述符和 conntrack 使用量。

## 6. 时间和样本记录规则

### 6.1 时间来源

跨进程端到端时间优先由同一个 benchmark controller 记录，避免不同容器时钟
偏差。插件内部耗时使用同一进程的 monotonic time。

如果必须合并不同主机的事件：

- 测试前完成时间同步检查；
- 在 manifest 中记录时钟偏差；
- 偏差超过阈值时不计算跨主机端到端延迟。

### 6.2 冷启动和热启动分开

A 类 RATS 主报告使用 OpenViking 独立冷启动；B 类身份容量报告使用独立
OpenClaw Deployment Unit 冷启动：

- run-scoped data directory；
- run-scoped Workload API socket；
- 新 Attestation Key 或 x509pop 叶证书；
- 不复用上次 Agent SVID；
- 不复用上次 workload SVID；
- 使用 run-scoped registration entry；
- 测试后按精确 Agent ID/entry ID 清理 Server 状态。

不得通过删除整个 SPIRE Server data directory 制造“冷启动”。SPIRE Server、
Trustee、Provider、TDVM、镜像和信任根在同一批次中保持稳定；只重建被测身份单元。

不得通过清空或替换 binding store 绕过 clone 检测。A 类正式运行前必须冻结：

- 每轮是否使用唯一且可审计的 `instance_id`；
- attestation key 轮换何时属于合法轮换；
- `idempotent`、`clone_conflict`、`state_corrupt` 和 `storage_error` 的分类；
- Agent/entry teardown 与 binding 保留、备份和恢复的关系。

如果新 Attestation Key 因复用旧 `instance_id` 被正确判定为 clone conflict，该轮是
失败样本，不能删除 binding 后重跑并只保留成功结果。

热启动、缓存命中和连接复用单独成组，不能与冷启动样本混合。

### 6.3 原始样本优先

Prometheus 用于运行观察、告警和趋势分析；最终 P50/P95/P99 以逐 RATS run、
逐 OpenClaw Deployment Unit 或逐请求的原始样本离线计算为准。

原因：

- 自动重试可能改变 counter 含义；
- 聚合后的 summary quantile 不能安全地跨实例再次聚合；
- 原始样本能够审计 timeout、失败原因和异常值；
- 简历数字需要能够追溯到具体 run。

P99 的正式结论建议至少基于 1000 个成功样本。样本不足时仍可输出探索性 P99，
但必须标记 `exploratory` 并同时报告样本数和 max。

### 6.4 Benchmark controller 合同

现有 `start-*.sh`、`deploy-v2-guest.sh`、`verify-svid.sh` 和 `verify-mtls.sh`
继续作为功能验收 fixture，不直接充当正式计时器。Controller 必须直接编排底层
步骤并使用同一个 monotonic clock：

```text
setup phase, excluded from timing:
  build/load images
  start SPIRE Server and Trustee
  start/warm Evidence Provider
  verify TDVM and clock synchronization

measured OpenViking phase:
  T0: immediately before docker run of fresh OpenViking Agent
  T1: SPIRE Server agent list first shows expected fresh Agent ID as valid
  T2: run-scoped registration entry is visible from SPIRE Server API
  T3: SVID probe first returns expected service ID and a fresh certificate

measured OpenClaw phase:
  C0: immediately before starting the Unit's fresh x509pop Agent
  C1: SPIRE Server first shows the expected Unit Agent ID as valid
  C2: the Unit's run-scoped registration entry is visible
  C3: the Unit's assigned egress proxy first obtains the expected SVID

measured guarded business phase:
  G0: after request freeze, immediately before calling Guard
  G1: complete Guard decision receipt is received and validated
  G2: the same frozen request begins mTLS forwarding
  G3: the final OpenViking response is received
```

Controller 要求：

- 启动前已知或可确定预期 Agent ID；
- 轮询 SPIRE Server，而不是用 Agent healthcheck 代替 T1/C1；
- 轮询间隔可配置并写入 manifest，默认不高于 200ms；
- 不把 SSH 建连、镜像传输、Provider 启动或脚本解释器启动计入
  `plugin_attestation_ms`；
- 记录每个事件的 monotonic offset 和 UTC wall time；
- SVID receipt 记录 SPIFFE ID、证书 serial、NotBefore、NotAfter 和首次观察时间；
- 正式 C 类请求记录 `request_id`、`decision_id`、`request_digest`、
  `verification_mode`、G0-G3 和 OpenViking marker；
- Guard DENY/error/timeout/malformed/digest mismatch/expired receipt 均产生失败
  receipt，并证明 OpenViking 无对应 marker；
- timeout 也必须产生最终 receipt；
- teardown 使用精确 run-scoped 名称，不清理其他运行或生产数据。

`plugin_attestation_ms` 由插件进程内部计时；T0-T3/C0-C3 由 controller 计时。
两类时间不能互相替代。Controller 轮询带来的观测误差不从样本中人为扣除，
而是通过 `controller_poll_interval_ms` 一并披露。

G0-G3 由 egress 结构化事件或与 controller 使用同一 monotonic 时间域的专用探针
记录。若不能证明时间源关系，则报告分段耗时和 controller 观察到的端到端耗时，
不得把不同主机 wall clock 直接相减。

## 7. 埋点与数据产物设计

### 7.1 Prometheus 低基数指标

在现有 NodeAttestor telemetry 基础上补充或校准逻辑指标：

```text
argus_nodeattestor_attempts_total{
  side,
  result,
  reason
}

argus_nodeattestor_duration_ms{
  side,
  result,
  reason
}

argus_nodeattestor_evidence_bytes{
  side
}

argus_nodeattestor_trustee_requests_total{
  result,
  reason
}
```

这些 `argus_nodeattestor_*` 指标只描述 OpenViking 侧 `argus_tdx` 链路。
OpenClaw `x509pop` 准入和 workload SVID 指标使用独立命名，不合并到
`argus_nodeattestor_*`。

`attestation_profile` 是 run-level 属性，保存在 manifest 和 report，不进入
插件 Prometheus label。这样无需为 mock/real profile 修改 NodeAttestor 配置
schema，也避免同一 run 中出现不一致标签。

mock Evidence Provider 和 mock Trustee 必须新增：

```text
argus_mock_evidence_requests_total{result,reason}
argus_mock_evidence_in_flight
argus_mock_evidence_request_duration_seconds

argus_mock_trustee_requests_total{result,reason}
argus_mock_trustee_in_flight
argus_mock_trustee_request_duration_seconds
```

Guard 和 Guard-gated egress 至少提供以下逻辑指标：

```text
argus_guard_decisions_total{decision,result,reason}
argus_guard_decision_duration_seconds{result}
argus_guard_in_flight

argus_egress_requests_total{decision,result,reason}
argus_egress_guard_to_forward_duration_seconds{result}
argus_egress_guarded_request_duration_seconds{result}
argus_egress_in_flight

argus_identity_lifecycle_convergence_seconds{scenario,result}
```

`reason` 只能使用冻结的低基数枚举，例如 `deny`、`timeout`、`unavailable`、
`malformed`、`missing_receipt`、`digest_mismatch`、`expired_receipt`、
`peer_id_mismatch`。完整错误文本进入结构化事件，不进入 label。

现有 `argus_m4_fake_requests_total` 只作为兼容指标保留，不作为新报告的主指标。
duration 使用可聚合 histogram 或保留原始请求 receipt；不得跨实例平均 summary
quantile。

采集路径冻结为：

- SPIRE Server：Host loopback 暴露端口；
- OpenViking Agent：controller 通过 SSH 抓取 TDVM loopback `9991`；
- mock Evidence Provider：controller 通过 SSH 抓取 TDVM loopback `18080/metrics`；
- OpenClaw Agent：每 Unit 映射唯一 Host loopback metrics 端口；
- mock Trustee：Prometheus 使用配置的 mTLS client credentials 抓取中心侧
  `/metrics`；
- Guard：每个配置的 Guard 实例使用受限 metrics endpoint；
- Guard-gated egress：每 Unit 或每个共享 egress 使用唯一、受限 metrics endpoint；
- Linux/容器资源：controller 采集 `docker stats`、进程和主机资源快照。

最终 Prometheus 暴露名称以 SPIRE MetricsService 实际输出为准，并在实现阶段通过
抓取 `/metrics` 固化。

禁止把以下高基数字段放入 Prometheus label：

- `run_id`；
- `agent_id`；
- `deployment_unit_id`；
- `container_id`；
- 完整错误消息。

这些字段只进入结构化事件和原始样本。

### 7.2 原始 receipt

A 类每轮 OpenViking RATS 冷启动至少生成一条 JSONL 最终记录：

```json
{
  "run_id": "20260804T120000Z-openviking-rats-r017",
  "scenario": "single_openviking_rats_cold_start",
  "attestation_profile": "mock-evidence-mock-quote",
  "openviking_count": 1,
  "sample_index": 17,
  "attestor": "argus_tdx",
  "rats_status": "success",
  "identity_ready_status": "success",
  "attempts": 1,
  "agent_id": "spiffe://argus.local/spire/agent/...",
  "expected_workload_id": "spiffe://argus.local/service/openviking-cmem",
  "openviking_agent_admission_ms": 812,
  "openviking_registration_ms": 37,
  "openviking_svid_delivery_ms": 121,
  "openviking_attestation_to_svid_ms": 158,
  "openviking_bootstrap_to_svid_ms": 970,
  "svid_serial": "01A2...",
  "svid_not_before": "2026-08-05T12:00:00Z",
  "svid_not_after": "2026-08-05T12:10:00Z",
  "failure_reason": null
}
```

B/C 类每个 OpenClaw Deployment Unit 和每个请求分别生成 receipt，并使用：

```text
unit_id
openclaw_unit_index
attestor=x509pop
agent_id
entry_id
expected_workload_id=spiffe://argus.local/agent/openclaw
openclaw_agent_admission_ms
openclaw_registration_ms
openclaw_svid_delivery_ms
openclaw_admission_to_svid_ms
openclaw_bootstrap_to_svid_ms
mtls_handshake_ms
svid_serial
svid_not_before
svid_not_after
```

正式 C 类每个业务请求还必须记录：

```text
request_id
guard_decision_id
request_digest
verification_mode
guard_result
guard_failure_reason
guard_decision_ms
guard_to_forward_ms
guarded_request_e2e_ms
client_spiffe_id
server_spiffe_id
openviking_session_id
openviking_marker_id
http_status
forwarded_mtls
```

身份启动 receipt 不要求不存在的 Guard 字段；业务 request receipt 不得省略这些字段
后仍标记为正式 C 类成功样本。`request_id`、`guard_decision_id`、
`request_digest` 和 marker 属于原始 receipt/日志字段，不得进入 Prometheus label。

不得在 OpenClaw receipt 中把 `attestor` 标记为 `argus_tdx`。
所有 Unit 的 `expected_workload_id` 可以相同，但 `unit_id`、Agent ID、entry ID、
socket 和容器名必须不同。

失败样本同样必须落盘，并保留：

- 所在阶段；
- timeout 阈值；
- gRPC/HTTP 状态；
- 最近一次重试原因；
- 相关组件是否重启；
- 是否观察到 Agent ID；
- 是否取得错误 SVID；
- Guard decision 是否存在以及失败原因；
- 请求是否被转发；
- OpenViking 是否出现对应 marker。

### 7.3 Run manifest

每次测试必须生成不可变 `manifest.json`，至少包含：

```text
run_id
git_commit
branch
dirty_worktree
test_start_time
Linux distribution and kernel
CPU model and core count
memory
storage type
tdvm_required
tdvm_device_present
tdvm_instance_id
host_tdvm_clock_offset_ms
spire_version
container_runtime_version
attestation_profile
provider_type
trustee_type
pre_ra_hardening_gate_version
pre_ra_hardening_gate_status
guard_mode
guard_contract_version
guard_instance_count
guard_timeout_ms
guard_max_body_bytes
egress_profile
business_request_digest_algorithm
binding_store_backend
binding_store_schema_version
scenario
openviking_count
openviking_rats_repetitions
openclaw_unit_count
launch_pattern
launch_rate
request_concurrency
request_rate
timeouts
controller_poll_interval_ms
server_x509_svid_ttl
workload_x509_svid_ttl
max_connection_lifetime_ms
connection_idle_timeout_ms
retry_policy_version
SLO version
```

缺少 commit、环境规格或 mock/real 标记的运行，不进入最终简历数据集。正式 C 类
业务结果若缺少 Guard mode、合同版本、hardening gate 状态或连接生命周期配置，
同样不能进入正式报告。

### 7.4 建议产物目录

实施阶段建议新增：

```text
core/spire/benchmarks/
  README.md
  schemas/
    manifest.schema.json
    sample.schema.json
    summary.schema.json
  configs/
    prometheus.yaml
    scenarios.yaml
    capacity-slo.yaml
    hardening-gates.yaml
  load/
    openviking-rats-runner/
    openclaw-unit-runner/
    svid-probe/
    guarded-load-client/
    diagnostic-mtls-client/
  scripts/
    verify-hardening-gates.sh
    run-openviking-rats.sh
    run-openclaw-capacity.sh
    run-identity-lifecycle.sh
    collect-resources.sh
    summarize.sh
  results/
    .gitignore
    <run-id>/
      manifest.json
      hardening-gate.json
      samples.jsonl
      prometheus-snapshot/
      container-stats.csv
      summary.json
      report.md
```

大体积原始结果默认不直接提交 Git；仓库保留 schema、汇总报告、代表性样本和结果
校验摘要。`results/.gitignore` 默认忽略 run 目录，只保留 `.gitignore` 和经过人工
确认的脱敏汇总。

## 8. 容量搜索方法

### 8.1 当前容量搜索对象

当前主容量搜索对象是：

```text
N_openclaw
  = 独立 OpenClaw Deployment Unit 数量

request_concurrency / request_rate
  = 已就绪 OpenClaw 经 Guard-gated egress 对单 OpenViking 的业务负载
```

A 类保持 `M_openviking=1`，通过增加 R 获取延迟和成功率样本，不对单
OpenViking 做 `N_openclaw_max_stable` 或 `M_max_stable` 搜索。

D 类未来启用后，才对独立 OpenViking/TDVM 数量 `M_openviking` 做自定义
NodeAttestor 容量搜索。

### 8.2 OpenClaw 数量不预设 100

容量测试采用指数搜索：

```text
N_openclaw = 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, ...
```

100 可以作为业务关注的补充规模点，但不是测试终点。测试持续到首次出现稳定性、
正确性、延迟或资源 SLO 失败。

增加 `N_openclaw` 不会增加 `argus_tdx` Node Attestation 数量。在当前拓扑中，
OpenViking 仍为一个已完成 RATS 的服务节点。

### 8.3 启动模型

每个规模至少覆盖：

1. `burst`：N 个 OpenClaw Deployment Unit 尽可能同时启动；
2. `ramp-1`：每秒启动 1 个；
3. `ramp-5`：每秒启动 5 个；
4. `ramp-10`：每秒启动 10 个；
5. 更高 launch rate：根据前一轮结果继续增加；
6. `bootstrap-steady`：全部就绪后保持到首次 SVID serial 变化之前；
7. `rotation-steady`：持续到每个代表性 Unit 至少观察到两次 SVID serial 变化；
8. `churn`：按固定比例停止并补充新实例。

第一轮优先完成 `burst` 和一个可控 `ramp`。`bootstrap-steady`、
`rotation-steady` 和 `churn` 在基础容量可重复后执行。

当前 workload SVID TTL 为 600 秒。`bootstrap-steady` 不允许混入证书轮换；
`rotation-steady` 必须显式报告 SVID renewal 次数和 renewal 期间的请求错误。
SVID rotation 不计为新的 `argus_tdx` Node Attestation。

### 8.4 首次失败后的边界细化

假设：

```text
N_openclaw_pass = 128
N_openclaw_fail = 256
```

则在两者之间进行二分式细化，例如测试 192、160、176，直到得到满足精度要求的
最大稳定规模。

最终输出两个不同的容量点：

```text
N_openclaw_knee
  = 延迟明显抬升或吞吐增益显著下降的首个规模点

N_openclaw_max_stable
  = 连续 K 个批次满足所有正确性门槛、成功率、延迟和资源 SLO 的最大 N
```

不能只报告能够启动的最大容器数。

### 8.5 容量拐点建议判定

对相邻规模记录：

```text
p95_inflation(N_openclaw)
  = p95(N_openclaw) / p95(1)

throughput_gain
  = throughput(N_openclaw) / throughput(previous_N_openclaw)

resource_headroom
```

以下现象出现时标记候选拐点：

- 扩容后 ready throughput 增益很小；
- P95/P99 开始非线性增长；
- OpenClaw `x509pop` 身份准入、SPIRE Server 或 CA/SVID 签发开始排队；
- 共享 Guard 或 Guard-gated egress 开始排队、timeout 或消耗主要 CPU；
- datastore lock/write latency 明显增长；
- CPU、内存、FD 或网络接近资源上限；
- OpenClaw Agent 重试率显著增加；
- OpenViking 请求延迟在同一 QPS 下持续抬升。

当前 B/C 类出现上述现象时，不能归因于 `argus_tdx` NodeAttestor；需要从
OpenClaw `x509pop` 身份平面、SVID 签发、Guard/egress、mTLS 或 OpenViking
服务面定位。

拐点算法和阈值必须在正式测试前写入 `capacity-slo.yaml`，不能看到结果后再修改
规则以美化结论。

## 9. 通过条件与停止条件

### 9.1 硬性正确性门槛

- A 类每个成功 run 只产生本轮预期的 OpenViking Agent 和服务 SVID；
- A 类不得通过清空 binding store 绕过 clone conflict；binding 失败必须保留并分类；
- B 类唯一 OpenClaw Agent ID 数与成功 Deployment Unit 数一致；
- B 类每个成功 Unit 使用唯一 Agent ID、entry、socket 和 egress proxy；
- B 类所有成功 egress proxy 获得精确的共享业务 ID
  `spiffe://argus.local/agent/openclaw`；
- OpenClaw 应用容器未挂载 Workload API socket，且只能经分配的 egress proxy
  访问 OpenViking；
- 正式 C 类请求全部经过当前 run 声明的 Guard-gated egress；
- 每个正式 C 类成功请求都能关联唯一 request、Guard decision、request digest、
  mTLS 转发和 OpenViking marker；
- Guard DENY、503、timeout、malformed、missing receipt、digest mismatch 和
  expired receipt 均不得产生 OpenViking marker；
- 直连 OpenViking、绕过 Guard 或绕过分配 egress 的请求均被拒绝；
- 错误 SVID、跨 Agent socket 和错误 peer SPIFFE ID 均为零；
- plaintext 不能访问 mTLS 端口；
- 无 OOM、无关键组件 crash loop；
- 每个样本能够关联到唯一 run 和 Agent/Deployment Unit。

正式 C 类运行前，WP1、WP2、WP5 和 WP6 对应的 hardening gate 必须在同一 Git
commit/profile 上有远程 PASS 证据。若 hardening gate 不完整，只能产生
`diagnostic` 或 `exploratory` 结果。

### 9.2 性能 SLO

正式运行前，A 类先完成单 OpenViking 冷启动基线；B/C 类使用
`N_openclaw=1` 和小规模运行建立基线，再分别冻结：

- 最低 OpenViking RATS 冷启动成功率；
- 最低 OpenViking identity ready rate；
- `argus_tdx` Node Attestation P95/P99 上限；
- `openviking_attestation_to_svid_ms` P95/P99 上限；
- 最低 OpenClaw Deployment Unit 就绪率；
- `openclaw_bootstrap_to_svid_ms` P95/P99 上限；
- OpenClaw 整批 `time_to_95_percent_ready` 上限；
- Guard decision P95/P99 和 timeout 上限；
- `guarded_request_e2e_ms` P95/P99 上限；
- OpenViking 请求成功率和 P95/P99 上限；
- SVID 轮换业务中断、Agent ban 新连接拒绝和旧连接排空上限；
- 最大 CPU、内存、FD 和重启次数；
- 单批 timeout。

若项目暂时没有业务 SLO，可以先使用内部工程阈值，但必须在报告中标记
`engineering threshold`，不能写成客户或生产 SLA。

### 9.3 停止扩容条件

在 B/C 类实验中，出现任一情况即停止继续扩大 `N_openclaw`、请求并发或 QPS，
先保留现场并定位：

- 身份正确性失败；
- Guard fail-open、旁路成功、digest/decision 绑定失败或审计链缺失；
- 成功率低于冻结 SLO；
- P99 超过冻结 SLO；
- 批次 timeout；
- SPIRE、Provider、Trustee 或 OpenViking OOM/重启；
- Guard 或 Guard-gated egress OOM、重启或持续排队；
- CPU 长时间高于安全水位；
- 文件描述符、端口、PID 或 conntrack 接近上限；
- datastore 持续锁等待；
- ready throughput 已平台化；
- 主机失去稳定观测或原始样本不完整。

### 9.4 重复次数

- A 类执行足够的独立 OpenViking 冷启动 run；
- B/C 类每个规模和启动模型至少执行 5 个独立批次；
- warm-up 批次不进入正式统计；
- 候选 `N_openclaw_knee` 和 `N_openclaw_max_stable` 增加重复次数；
- P99 正式结论尽量累计至少 1000 个成功样本；
- 所有失败批次都保留，不得只选择成功运行。

## 10. 实验矩阵

### 10.1 A 类：单 OpenViking RATS 性能与稳定性

| 场景 | OpenViking 数 | 重复方式 | 主要输出 |
| --- | ---: | --- | --- |
| 链路基线 | 1 | 单次 cold start | 埋点、receipt 和 SVID 正确性 |
| 延迟采样 | 1 | R 次 identity cold start | RATS/identity-ready 成功率、P50/P95/P99 |
| 失败重试观察 | 1 | R 次独立 cold start | attempts、retry 和失败原因 |
| 连续稳定性 | 1 | 顺序重建 | 资源泄漏、残留身份和长期错误 |

R 只用于增加统计样本，不代表并发 OpenViking 数量。

### 10.2 B/C 类：多 OpenClaw 到单 OpenViking

| 场景 | OpenClaw 单元数 | 请求模式 | 主要输出 |
| --- | ---: | --- | --- |
| 身份启动基线 | 1 | 无业务请求 | bootstrap 到 SVID |
| 批量身份启动 | 指数增长 | 无业务请求 | 就绪率和身份平面容量 |
| diagnostic mTLS-only | 代表性 N | 新连接/复用 | 纯握手和 mTLS 分段基线，不进入正式 C 类 |
| Guard decision baseline | 1 | 固定最小业务请求 | Guard decision 与 gated E2E 分段 |
| Guard-gated burst | 代表性 N | 每请求新连接 | Guard、握手和业务容量 |
| Guard-gated keep-alive | 代表性 N | 连接复用 | 稳定 QPS 与旧连接行为 |
| Guard-gated 固定 QPS | 代表性 N | 逐级加压 | Guard/OpenViking 延迟和错误率 |
| Guard-gated 固定并发 | 代表性 N | 逐级加压 | 最大稳定并发 |
| bootstrap steady | 代表性 N | 首次 SVID 轮换前 | 不含轮换的稳定性 |
| rotation steady | 代表性 N | 至少两次 SVID serial 变化 | 轮换期间错误率和资源 |
| 完整 OpenClaw | 1、拐点前、拐点附近 | 真实 Guard-gated 请求 | 应用开销和完整审计链 |

完整 OpenClaw 测试不必覆盖所有 N。先用轻量身份和 mTLS 客户端找到基础设施边界，
再在少量规模点验证完整应用，避免 LLM 时延和费用主导结果。

### 10.3 生命周期与拒绝收敛

| 场景 | 连接状态 | 主要输出 |
| --- | --- | --- |
| SVID 自动轮换 | 新连接和已有连接 | 中断时间、错误率、serial 变化 |
| Workload API 短时不可用 | 新请求 | fail-closed 时间和恢复时间 |
| SPIRE Agent 重启 | 新连接和已有连接 | 恢复时间和隔离完整性 |
| workload entry 删除 | 新连接和已有连接 | 新连接拒绝与旧连接排空 |
| Agent ban/delete | 新连接和已有连接 | 拒绝收敛、最大残留连接时间 |
| trust bundle 更新/旧 bundle | 新连接 | 更新收敛和旧 bundle 拒绝 |
| egress/Guard 重启 | 业务请求 | fail-closed、恢复和重复请求行为 |

生命周期场景使用冻结的连接最大生命周期、idle timeout 和 retry policy。任何
fail-open、明文/API key-only fallback 或无限存活的旧连接都属于正确性失败。

### 10.4 D 类：多 OpenViking RATS 容量（后续）

| 场景 | OpenViking/TDVM 数 | 启动模型 | 主要输出 |
| --- | ---: | --- | --- |
| 多节点基线 | 2 | cold burst | 独立身份和链路正确性 |
| 指数扩容 | 2 到首次失败点 | burst | `argus_tdx` 并发准入容量 |
| 限速扩容 | 逐步增加 | ramp | 可持续 RATS 准入速率 |
| 边界细化 | pass 与 fail 之间 | burst/ramp | `M_knee`、`M_max_stable` |

该矩阵只有在项目真实引入多个 OpenViking/TDVM 后执行。

## 11. 实施阶段

### Phase 0：冻结口径

- [ ] 冻结单 OpenViking RATS、多 OpenClaw 身份和业务请求三类主评测定义；
- [ ] 明确多 OpenViking RATS 容量属于后续 D 类；
- [ ] 冻结 OpenViking identity cold start，不包含 TDVM boot 和镜像传输；
- [ ] 冻结 OpenClaw 为独立 x509pop Agent、共享 workload SPIFFE ID；
- [ ] 明确 SVID 实际持有者为 mTLS egress proxy；
- [ ] 冻结 run 级 runtime 隔离与 run 内 Unit 隔离的边界；
- [ ] 冻结共享 Guard、每 Unit Guard 或 Guard pool 的实例基数；
- [ ] 冻结 Guard mode、Guard/authorization contract 版本和请求摘要算法；
- [ ] 冻结正式 C 类必须经过 Guard-gated egress，mTLS-only 仅为诊断场景；
- [ ] 冻结 T0-T3、C0-C3、G0-G3 时间点；
- [ ] 冻结成功、失败、timeout 和 retry 定义；
- [ ] 冻结 binding store、`instance_id`、合法 key rotation 和 clone conflict 语义；
- [ ] 冻结连接最大生命周期、idle timeout 和生命周期收敛 SLO；
- [ ] 定义 manifest、receipt 和 summary schema；
- [ ] 明确 TDVM required、mock evidence/Trustee 和 real Quote deferred 边界；
- [ ] 将 WP1、WP2、WP5、WP6 的验收状态定义为正式 C 类 hardening gate；
- [ ] 固化 `mtls-smoke` 依赖锁定方式，提交可复现的 `go.sum`。

完成标准：同一份原始数据能够由不同人计算出相同成功率和百分位。

### Phase 1：建立 hardening gate、埋点和 controller 垂直切片

- [ ] 在目标 Linux/TDVM commit/profile 上重新验证 WP1 Guard 同请求门控；
- [ ] 验证 WP2 唯一受控路径和所有旁路拒绝；
- [ ] 记录 WP5 run/runtime 隔离与 WP6 审计字段的 gate 版本；
- [ ] NodeAttestor duration 加入 `result/reason` 维度；
- [ ] 保留 attempts、evidence bytes 和 Trustee requests；
- [ ] 输出结构化 attestation 完成事件；
- [ ] 增加 mock Provider/Trustee duration、in-flight 和 reason 指标；
- [ ] 增加 Guard/egress decision、duration、in-flight 和 reason 指标；
- [ ] 实现统一 monotonic benchmark controller；
- [ ] 实现 OpenViking service SVID probe；
- [ ] SVID receipt 输出 serial、NotBefore 和 NotAfter；
- [ ] 实现 Server Agent/entry 轮询，不用 healthcheck 代替 T1/T2；
- [ ] 实现正式请求的 request/decision/digest/mTLS/marker 关联 receipt；
- [ ] 验证每轮冷启动不会复用旧 Agent/SVID；
- [ ] 生成第一份完整 run 目录。

完成标准：一个 OpenViking 冷启动 run 能够生成 manifest、原始 receipt、
Prometheus 快照和 report；一个最小 Guard-gated 请求能够生成完整因果链 receipt。

### Phase 2：实现 OpenViking RATS 重复采样

- [ ] 计时前预加载镜像并预热 Provider/Trustee；
- [ ] 参数化 run-scoped Guest root、`data_dir`、socket、容器名和证明密钥；
- [ ] 每个 run 使用独立 `RUN_ID`，清理脚本只能处理当前 run；
- [ ] 从新 Attestation Key 确定本轮预期 Agent ID；
- [ ] 按冻结合同生成唯一 `instance_id` 或执行显式合法 key rotation；
- [ ] 实现顺序 identity cold start、状态轮询和 timeout；
- [ ] 实现 run-scoped OpenViking workload entry 并显式使用本轮 parent ID；
- [ ] 实现 OpenViking 服务 SVID 精确校验；
- [ ] 按精确 Agent ID/entry ID 执行安全 teardown；
- [ ] 不清空 binding store，并保留 clone/state/storage 失败样本；
- [ ] 确认旧 Agent 不会使“恰好一个 Agent”的单实例 fixture 误判；
- [ ] 采集各组件和主机资源；
- [ ] 分别汇总 RATS success rate、identity ready rate、P50/P95/P99 和 attempts。

完成标准：R 次独立冷启动结果可重复，不存在身份残留或跨轮复用。

### Phase 3：实现多 OpenClaw Deployment Unit

- [ ] 新增 benchmark 专用 Unit launcher，不使用 `docker compose --scale`；
- [ ] 共享 x509pop CA，为每个 Unit 签发唯一叶证书和私钥；
- [ ] 参数化 OpenClaw 独立 `x509pop` Agent ID、配置和容器名；
- [ ] 为每个 OpenClaw 使用独立 Agent 状态和 Workload API；
- [ ] 为每个 Unit 创建唯一 entry ID 和 `argus.benchmark.unit` selector；
- [ ] 每 Unit 建立独立 Guard-gated mTLS egress proxy、metrics 端口和 source IP；
- [ ] 按冻结拓扑生成 Guard endpoint，并记录 Guard 实例基数；
- [ ] 验证每 Unit 只能访问其分配的 egress，所有 egress 只能走 Guard ALLOW 路径；
- [ ] 批量获取并精确校验共享 ID 的 OpenClaw-side egress proxy SVID；
- [ ] 支持 burst、ramp 和 bootstrap-steady；rotation-steady/churn 在 Phase 5 启用；
- [ ] 输出每个 Deployment Unit 的原始 receipt。

完成标准：N=1、10、32 的 OpenClaw 身份启动和最小 Guard-gated 请求结果可重复，
且不会触发额外 `argus_tdx` Node Attestation。

### Phase 4：搜索多 OpenClaw 身份和 Guard-gated 业务容量

- [ ] 按指数序列扩大 `N_openclaw`；
- [ ] 找到首次失败点并细化边界；
- [ ] 重复验证 `N_openclaw_knee` 和 `N_openclaw_max_stable`；
- [ ] 实现输出逐请求 JSONL 的 Guard-gated 轻量 load client；
- [ ] 对单个 OpenViking 执行 Guard-gated 新连接和 keep-alive 测试；
- [ ] 单独执行 `diagnostic_mtls_only` 分段基线，不混入正式 C 类；
- [ ] 分开统计 Guard、egress、mTLS 和 OpenViking 延迟与资源；
- [ ] 搜索最大稳定请求并发和 QPS；
- [ ] 分开报告身份 bootstrap 和 Guard-gated 业务请求阶段；
- [ ] 记录 Guard、OpenViking 和身份平面资源；
- [ ] 每个正式成功请求校验完整因果链，每个拒绝请求校验无 marker；
- [ ] 输出 B/C 类正式报告。

完成标准：能够回答“多少个 OpenClaw 部署单元在什么请求模型下稳定工作，以及
继续增加时首先影响 Guard、身份平面、mTLS 还是 OpenViking”。hardening gate
未通过时不得输出正式 C 类结论。

### Phase 5：生命周期和拒绝收敛

- [ ] 完成 SVID rotation、Workload API 故障和 Agent/Server 重启测试；
- [ ] 完成 entry 删除、Agent ban/delete、SVID 到期和 bundle 更新测试；
- [ ] 同时观察已有 keep-alive/TLS 连接和新连接；
- [ ] 记录拒绝时间、恢复时间、业务中断和旧连接排空时间；
- [ ] 验证所有故障均不回退到明文、API key-only 或未授权直连；
- [ ] 将 `bootstrap-steady`、`rotation-steady` 和 lifecycle fault 场景分开。

完成标准：WP3 定义的每个场景都有正向、负向和恢复 receipt，并满足冻结的
工程 SLO。

### Phase 6：完整 OpenClaw 和运维代表性验证

- [ ] 选择单实例、拐点前和拐点附近规模；
- [ ] 替换轻量客户端为真实 OpenClaw Guard-gated 调用；
- [ ] 使用 run-scoped bridge、唯一 OpenClaw IP 和唯一 proxy listen port；
- [ ] 验证每个 OpenClaw 只能访问分配给自己的 egress proxy；
- [ ] 固定模型、prompt、响应上限和外部依赖；
- [ ] 分开报告身份、Guard/mTLS 与模型业务延迟；
- [ ] 使用真实 OpenViking marker 验证完整审计链；
- [ ] 对比轻量与完整进程的资源增量。
- [ ] 执行 WP7 canary、切换和回滚脚本并归档版本、身份和流量证据；

完成标准：确认轻量压测结论能够解释真实应用部署，但不把模型 API 容量归因给
Argus/SPIRE；canary/回滚形成独立运维验收报告，不混入容量百分位。

### Phase 7：多 OpenViking RATS 容量（条件触发）

- [ ] 确认项目已进入多个独立 OpenViking/TDVM 场景；
- [ ] 为每个 OpenViking 建立独立 `argus_tdx` Agent 身份域；
- [ ] 支持多 OpenViking 的 burst 和 ramp；
- [ ] 搜索 `M_knee` 和 `M_max_stable`；
- [ ] 输出 D 类 RATS 控制面容量报告。

完成标准：只有实际创建 M 个独立 OpenViking/TDVM 并触发 M 条 RATS 链路后，
才形成自定义 NodeAttestor 容量结论。

## 12. 汇总报告格式

A 类单 OpenViking RATS 报告至少输出：

| OpenViking 数 | RATS runs | RATS 成功率 | Identity ready 率 | Attempt 重试率 | Attestation P95 | Attestation P99 | Attestation-to-SVID P95 | Attestation-to-SVID P99 | CPU 峰值 | RSS 峰值 | 结果 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | PASS/FAIL |

B 类多 OpenClaw 身份容量报告至少输出：

| N OpenClaw | 批次 | Unit 就绪率 | SVID P95 | SVID P99 | Ready/s | CPU 峰值 | RSS 峰值 | 结果 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | PASS/FAIL |

C 类 Guard-gated 业务容量报告至少输出：

| N OpenClaw | Guard profile | 请求数 | 成功率 | Guard P95/P99 | mTLS P95/P99 | Gated E2E P95/P99 | stable QPS | 审计关联缺失 | 未授权 marker | 结果 |
| ---: | --- | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | --- |
| 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 0 | 0 | PASS/FAIL |

生命周期报告至少输出场景、连接状态、拒绝时间、恢复时间、业务中断、旧连接排空、
fallback 观察和 PASS/FAIL。生命周期结果不得并入普通 steady-state 延迟分布。

报告必须同时写明：

```text
Environment:
Commit:
OpenViking attestor:
OpenViking attestation profile:
OpenClaw attestor:
OpenClaw workload identity model:
OpenClaw SVID holder:
Pre-RA hardening gate version/status:
Guard mode/contract:
Guard instance topology:
Egress profile:
Binding store backend/schema:
Connection lifetime/idle timeout:
OpenViking count:
OpenViking RATS repetitions:
OpenClaw unit count:
Launch pattern:
N_openclaw_knee:
N_openclaw_max_stable:
Primary bottleneck:
Secondary bottleneck:
Failure mode after boundary:
Sample count:
Run IDs:
```

不得只保留截图或手工抄录的最终数字。

## 13. 对“数量增加会影响什么”的分析框架

| 数量增长后的现象 | 可能受影响组件 | 需要检查的证据 |
| --- | --- | --- |
| 单 OpenViking RATS P95/P99 波动 | NodeAttestor、Trustee、Provider | 分段耗时、CPU、timeout、attempts |
| OpenViking admitted 但服务 SVID 慢 | registration、CA、Workload API | OpenViking registration/SVID delivery |
| OpenClaw 数量增加但 RATS 指标不变 | 符合当前架构预期 | 确认 OpenViking 仍只有一个 Agent |
| OpenClaw SVID 变慢 | x509pop、registration、CA、Workload API | OpenClaw admission/SVID 分段耗时 |
| OpenClaw ready/s 平台化 | SPIRE Server、CA 或 datastore | CPU、DB write/lock、gRPC throughput |
| 只有 OpenClaw burst 失败 | 启动风暴和瞬时资源上限 | burst 与 ramp 对比 |
| Guard decision P95/P99 抬升 | Guard、egress 或 Guard 网络路径 | decision duration、in-flight、timeout、CPU/RSS |
| Guard 正常但 Gated E2E 变慢 | mTLS、OpenViking 或连接管理 | G0-G3 分段、handshake、服务延迟 |
| Agent 正常但 OpenViking 请求慢 | OpenViking 服务面 | QPS、连接数、服务 CPU/RSS |
| 新连接慢、keep-alive 正常 | TLS/证书/FD/端口 | handshake、FD、conntrack、重传 |
| ban 后新连接拒绝但旧连接仍工作 | 连接生命周期和排空策略 | SVID NotAfter、连接年龄、max lifetime、drain receipt |
| 容器数继续增加但业务吞吐不增 | 主机或 OpenViking 饱和 | throughput gain、resource headroom |
| 出现错误身份或错误 SVID | 隔离/注册逻辑错误 | parent、selector、socket、原始 receipt |
| Guard 故障仍出现 marker | egress fail-open 或旁路 | request/decision/digest/marker 因果链和网络路径 |

最终报告必须给出证据链，而不是仅写“性能下降”。
除非执行 D 类多 OpenViking 实验，否则 OpenClaw 扩容中的退化不得归因于
`argus_tdx` NodeAttestor 容量。

## 14. 简历数据生成规则

简历数字只能来自：

- 冻结后的指标合同；
- 记录明确 commit 和 Linux 环境的正式 run；
- 完整保留失败样本的汇总；
- A 类拥有足够的独立 OpenViking 冷启动 runs；
- B/C 类每个正式规模至少 5 个独立批次；
- 有足够样本支撑的 P95/P99；
- 明确标记 TDVM required、mock evidence/Trustee 和 real Quote deferred。

OpenViking RATS 性能模板：

```text
在 Linux TDVM、mock evidence/Trustee 环境下构建 OpenViking `argus_tdx`
RATS 可观测评测链，
通过 [R] 次独立冷启动测得 Node Attestation 成功率 [X%]、P95/P99
[A/B ms]，Attestation 到 OpenViking workload SVID 的 P95/P99 为
[C/D ms]，并通过 Provider、Trustee 和 SPIRE 分段指标定位主要延迟来源。
```

多 OpenClaw 场景模板：

```text
构建 [N] 个独立 x509pop Agent 身份域、共享 OpenClaw workload SPIFFE ID 到单
OpenViking 的 Guard-gated SPIFFE mTLS 压测模型，
在 [并发/QPS 模型] 下实现 [成功率]、Guard decision P95/P99 [A/B ms] 和业务
端到端 P95/P99 [C/D ms]，结合 Guard、SPIRE、OpenViking 与 Linux 资源指标定位
容量拐点；测试使用 `mock_allow` 和 mock Trustee，只证明门控执行正确性，
未包含真实 Quote/QGS 或生产 RA 决策开销。
```

在正式结果产生前，只能写“设计/实现评测体系”，不能填写目标 N 或把 100 写成
已验证容量。

只有完成 D 类多 OpenViking 实验后，才能使用“`argus_tdx` NodeAttestor 支持
[M] 个并发服务节点准入”的容量表述。

## 15. 风险与控制

### 15.1 缓存导致假快

控制：

- 主报告使用新 data directory；
- OpenViking 每轮生成新 Attestation Key；
- OpenClaw 每 Unit 使用唯一 x509pop 叶证书；
- 记录证书序列号和 NotBefore；
- 检查 SVID 是否来自当前 run；
- 冷启动和热启动分组。

### 15.2 重试掩盖失败

控制：

- 同时报告 RATS success、identity ready 和 attempt success；
- 记录每个 Agent 的 attempts；
- timeout Agent 保留原始失败事件。

### 15.3 多容器共享一个 Agent

控制：

- A 类每轮 OpenViking RATS 使用新的 Agent 状态和身份材料；
- B 类每个 OpenClaw Deployment Unit 使用独立 Agent 状态、socket 和 Agent ID；
- B 类 Unit 共享业务 workload SPIFFE ID，但 receipt 必须使用唯一 `unit_id`；
- D 类每个 OpenViking/TDVM 使用独立 `argus_tdx` Agent；
- 共享一个 SPIRE Agent 的多个 workload 只能归入业务并发实验；
- 报告中分别写明 OpenViking Agent 数、OpenClaw Agent 数、workload 数和请求并发数。

### 15.4 单机资源先于软件架构耗尽

控制：

- manifest 记录主机规格；
- 资源耗尽时结论写成“该环境下的容量”；
- 后续可通过扩大主机或拆分 SPIRE/Trustee/OpenViking 复测瓶颈。

### 15.5 mock 结果被误写成真实 Quote 验证

控制：

- manifest、report 和简历模板都显式写 `TDVM required`、
  `mock evidence/Trustee` 和 `real Quote/QGS deferred`；
- 真实 Quote/QGS 接入后建立独立 profile 和结果集；
- 不混合 mock 与 real-attestation 样本。

### 15.6 P99 样本不足

控制：

- 所有百分位同时报告样本数；
- 样本不足时标记 exploratory；
- 在最终容量点累计足够样本；
- 保留 max 和失败分布。

### 15.7 固定 Compose 被误用于扩容

控制：

- `compose.center.yaml` 只用于单实例功能基线；
- benchmark 使用独立 Unit launcher；
- 禁止对包含固定 `container_name`、socket、证书和端口的服务执行
  `docker compose --scale`；
- N=2 必须先通过证书、Agent ID、entry、socket、source IP 和 proxy port
  隔离检查，再扩大到 N=10/32。

### 15.8 SVID 持有者表述错误

控制：

- 当前 SVID 持有者统一记录为 OpenClaw mTLS egress proxy；
- OpenClaw 应用只通过受限 egress 路径消费该身份能力；
- 未将 Workload API socket 挂载给 OpenClaw 应用时，不声称应用进程直接持有 SVID；
- 报告同时记录应用容器、proxy 容器、Agent ID 和 workload SPIFFE ID。

### 15.9 计时窗口被部署开销污染

控制：

- 镜像构建、传输和加载在 setup phase 完成；
- Provider、Trustee、SPIRE Server 和 TDVM 在计时前就绪；
- T0/C0 紧邻被测 Agent 的启动操作；
- 不使用整个 `deploy-v2-guest.sh` 的墙钟时间作为 Node Attestation 延迟；
- controller 轮询分辨率和 SSH/网络路径写入 manifest。

### 15.10 SVID rotation 混入 bootstrap

控制：

- `bootstrap-steady` 在首次 SVID serial 变化前结束；
- `rotation-steady` 明确观察至少两次 serial 变化；
- renewal 期间的延迟和错误单独报告；
- SVID rotation 不计为 `argus_tdx` re-attestation。

### 15.11 Guard-bypass 样本混入正式容量

控制：

- 正式 C 类 scenario 只能调用 Guard-gated egress；
- mTLS-only 使用 `diagnostic_mtls_only` 名称和独立结果目录；
- manifest 记录 hardening gate、Guard mode、合同版本和实例基数；
- 每个正式成功请求必须关联 decision/digest/marker；
- 任一 Guard fail-open、旁路成功或未授权 marker 使整批结果无效。

### 15.12 清空 binding store 制造成功冷启动

控制：

- binding store 在同一批正式 A 类实验中保持持久；
- 每轮按冻结合同使用唯一 `instance_id` 或显式合法 key rotation；
- clone conflict、state corrupt 和 storage error 全部保留；
- 备份恢复单独成组，不与正常 cold start 延迟混合；
- 不删除失败 binding 后重跑并只保留成功样本。

### 15.13 Guard 实例基数变化污染容量对比

控制：

- manifest 记录共享 Guard、每 Unit Guard 或 Guard pool；
- Guard 实例数、资源限额、timeout 和网络路径保持固定；
- 改变 Guard 实例基数时建立新 topology profile；
- 不把扩展 Guard 后的结果与单共享 Guard 基线直接计算同一容量曲线。

## 16. 完成定义

本评测计划完成需要同时满足：

- 单 OpenViking RATS、多 OpenClaw 身份和业务请求三类主评测已经分开执行和报告；
- 指标合同、时间点和成功条件已经冻结；
- 每次运行均生成 manifest、原始 samples 和汇总报告；
- OpenViking identity cold start 不包含 TDVM boot、镜像传输和组件启动；
- 单 OpenViking RATS success rate、identity ready rate、P95 和 P99 可复现；
- Attestation 到 OpenViking workload SVID 的 P95 和 P99 可复现；
- 多 OpenClaw 测试使用唯一 x509pop Agent 身份域、唯一 socket/entry/proxy，
  但共享业务 workload SPIFFE ID；
- OpenClaw SVID 持有者被准确记录为 mTLS egress proxy，而不是应用进程；
- 正式 C 类结果只来自通过 WP1/WP2/WP5/WP6 hardening gate 的 commit/profile；
- 正式 C 类请求全部经过 Guard-gated egress，并拥有完整
  request/decision/digest/mTLS/marker 因果链；
- Guard fail-open、旁路成功、未授权 marker 和审计关联缺失均为零；
- Guard、mTLS 和 OpenViking 分段耗时与 gated E2E 已分开报告；
- mTLS-only 结果已标记为 diagnostic，未混入正式业务容量；
- 生命周期场景已分别记录轮换、拒绝、恢复和旧连接排空时间；
- A 类未通过清空 binding store 绕过 clone 检测；
- benchmark launcher 未使用当前固定 `compose.center.yaml` 或
  `docker compose --scale` 生成多 Unit；
- 已通过指数搜索和边界细化得到 `N_openclaw_knee` 与
  `N_openclaw_max_stable`；
- 已说明规模增长影响的具体组件和证据；
- 已完成多个 OpenClaw Deployment Unit 到单 OpenViking 的代表性测试；
- `bootstrap-steady` 与 `rotation-steady` 分开，SVID renewal 未计为
  `argus_tdx` re-attestation；
- canary、切换和回滚形成独立运维验收报告，未混入容量百分位；
- 未把 OpenClaw 数量写成自定义 NodeAttestor 容量；
- mock、真实实现和 deferred 能力边界在所有报告中一致；
- 简历表述能够追溯到正式 run，而不是目标值或人工估计。

D 类多 OpenViking RATS 容量是条件触发的扩展项，不是当前主计划完成的前置条件。

## 17. 下一步实施顺序

建议按以下顺序进入代码实施：

1. 冻结 hardening gate、Guard topology/contract、binding 生命周期、连接生命周期、
   manifest/sample/summary schema、`capacity-slo.yaml`、身份模型与 SVID TTL；
2. 在目标 Linux/TDVM commit/profile 上完成 WP1 Guard 同请求门控和 WP2 旁路拒绝
   的远程验收，记录 WP5/WP6 gate 版本；
3. 实现 monotonic benchmark controller 的单 OpenViking 垂直切片，包括 T0-T3、
   Server 轮询、SVID/timeout receipt、binding 分类和精确 teardown；
4. 补齐 NodeAttestor、mock Provider/Trustee、Guard/egress 的 duration、in-flight、
   reason、结构化事件和资源采集，并为 `mtls-smoke` 固化可复现依赖；
5. 将 OpenViking Agent data、Attestation Key、socket、容器和 registration
   entry 改为 run-scoped，在不清空 binding store 的条件下完成 identity cold start；
6. 在 Linux Host + TDVM 上执行多轮独立 OpenViking 冷启动，形成 RATS success、
   identity ready、P95/P99 与 Attestation-to-SVID 基线；
7. 实现 benchmark 专用 OpenClaw Unit launcher，先用 N=2 完成证书、Agent ID、
   entry、socket、Guard-gated proxy、source IP 和端口隔离门槛；
8. 完成 `N_openclaw=1、10、32` 的身份启动、`diagnostic_mtls_only` 和最小
   Guard-gated 业务基线；
9. 执行 burst/ramp 指数搜索和边界细化，分别得到 OpenClaw 身份容量和
   Guard-gated 业务的 `N_openclaw_knee`/`N_openclaw_max_stable`；
10. 执行 Guard-gated 新连接、keep-alive、固定并发和固定 QPS 压测，分开报告
    Guard、mTLS、OpenViking 和 gated E2E；
11. 完成 SVID rotation、Agent ban、entry 删除、Workload API 故障和旧连接排空
    的生命周期与拒绝收敛实验；
12. 在单实例、拐点前和拐点附近运行完整 OpenClaw，并单独完成 canary/回滚验收；
13. 冻结首版 benchmark report 和可追溯的简历数据；
14. 仅在多个独立 OpenViking/TDVM 进入范围后实施 D 类 NodeAttestor 容量测试。
