# Argus 4—7：实现与实验入口

目录职责与模块关系见 [STRUCTURE.md](STRUCTURE.md)，完整生产组件索引见 [ARGUS.md](../../ARGUS.md)。配置示例统一位于 [examples/](examples/)，运行配置片段位于 [config/](config/)。

最新整体 review、修复及发布范围见 [REVIEW-20260926.md](REVIEW-20260926.md)。

基线为 `8be8afe`。本轮扩展独立 Gateway、多用户私有记忆、应用读取审计、隔离的对照组和可恢复实验工具。真实 TDX、systemd 关闭时间、模型记忆效果、镜像运行及跨机性能在远程运行前为 **NOT_RUN**。本地测试不替代远程验收。

2026-09-26 AAMAS 方案的新增实现、最新验证和交付边界见 [改造交付说明](AAMAS-IMPLEMENTATION-20260926.md)。

## 入口与责任

| 内容 | 代码/说明 |
|---|---|
| 多实例部署、单实例兼容 | [deploy.py](../../adapters/OpenClaw/spiffe_client/deploy.py)、[fleet.py](../../adapters/OpenClaw/spiffe_client/fleet.py) |
| 独立用户业务、实际 API 隔离负例 | [fleet_business.py](../../adapters/OpenClaw/spiffe_client/fleet_business.py)、[验收说明](../../adapters/OpenClaw/spiffe_client/FLEET-ACCEPTANCE.md) |
| 固定版 OpenViking 身份语义验证 | [verify_openviking_contract.py](../../adapters/OpenClaw/spiffe_client/verify_openviking_contract.py) |
| 真实应用读取与独立采集 | [receiver_audit](../../adapters/OpenViking/receiver_audit/README.md) |
| 五个隔离实验部署 | [variants.py](variants.py)、[VARIANTS.md](VARIANTS.md) |
| 静态组的专用客户端凭据发布 | [static_clients.py](static_clients.py)；单独构建，不给生产部署添加弱化开关 |
| 配对运行、恢复与收集 | [suite.py](suite.py)、[runner.py](runner.py)、[step.py](step.py) |
| 双连接故障、远程收集 | [fault_trial.py](fault_trial.py)、[remote_acceptance.py](../../core/spire/workload/scripts/remote_acceptance.py) |
| 配置变化与进程/监听实例替换 | [fault_fixture.py](fault_fixture.py)、[受限故障说明](FAULT-FIXTURES.md) |
| 从实际业务证据派生故障里程碑 | [milestone.py](milestone.py)；手写 reached 标记不能通过门禁 |
| 历史规则离线诊断 | [history_diagnostics.py](history_diagnostics.py)、[admission_cases.py](admission_cases.py) |
| 实时准入的固定配方与证据比较 | [admission_trial.py](admission_trial.py)、[E1-REAL-RUNS.md](E1-REAL-RUNS.md) |
| 轮换/恢复及连续业务关联 | [lifecycle_trial.py](lifecycle_trial.py)、[E3 双机配方](examples/E3-LIFECYCLE-RECIPE.md)、[lifecycle_evidence.py](lifecycle_evidence.py) |
| 故障、检测代理、入口停止、读取时间线 | [timeline.py](timeline.py)；由 `fault_trial.py` 自动采样和收集 |
| 多客户端局部/共享故障与显式恢复 | [fleet_fault.py](fleet_fault.py)、[FLEET-FAULT.md](examples/FLEET-FAULT.md) |
| API 负载、资源与统计 | [load_fleet.py](load_fleet.py)、[resources.py](resources.py)、[analysis.py](analysis.py) |
| LoCoMo 导入、续查、新会话 QA 与评分 | [locomo_run.py](locomo_run.py)、[LOCOMO.md](LOCOMO.md)；使用本地已有数据，不冒称官方协议 |
| 双机顺序、证据边界 | [REMOTE-RUNBOOK.md](REMOTE-RUNBOOK.md)、[CLAIMS-EVIDENCE.md](CLAIMS-EVIDENCE.md) |
| 本地验证与源码交付清单 | [VALIDATION.md](VALIDATION.md)、[source-manifest.json](source-manifest.json) |

默认拓扑是客户端 TDVM 内一个共享 SPIRE Agent、三个独立 OpenClaw Gateway，以及服务 TDVM 的共享 OpenViking。Gateway 的身份、UID/GID、凭据组、配置、数据、容器 label、登记和发布 unit 分开；共享 Node/可信 publisher 是公共信任边界。停止一个实例不停止 Node。

服务准入采用精确 SPIFFE ID 集合；`identity.allowed_client_ids` 与旧 `identity.client_id` 互斥。业务权限继续由 OpenViking 从有效 API key 解析。请求头、`actor` 或声称的用户不是安全身份。没有新增 SPIFFE 与 API key 强制一一绑定；持有另一份有效 key 的获准客户端可使用该 key 本身的权限。

## 实验流程

所有命令从 `cczoo/agent-cc` 执行；Linux 运行建议 Python 3.12。先完成构建和一个真实单客户端路径，再扩到三客户端。每个实验组顺序占用服务监听端口；先停止前组，检查已停止，再激活下一组。共享 Node 数据不得删除。

1. 用 `variants.py render` 生成每组隔离服务配置及实际源代码，`inspect` 检查文件和声明机制。没有完整构建时 `payload_ready=false`，工具拒绝安装或正式执行。
2. 为各组准备实际非 root 用户 key 文件，使用相同模型、数据、权限与预算。每组使用独立业务用户，防止上一组的持久记忆污染下一组。
3. 首轮复制 [suite.example.json](examples/suite.example.json)，填入已有 fleet、完整 Argus 的 business 配置及 variant 目录。默认只运行 full Argus、私有记忆、一个种子，不需要性能负载配置。性能测量另外使用 [suite.performance.example.json](examples/suite.performance.example.json)，先完成预实验，再冻结总到达负载；该示例的 `frozen=false` 需要在实际 pilot 后填写。
4. 生成配置并准备实验：

```sh
python3 experiments/argus/suite.py --config /secure/suite-input.json --output /secure/generated-suite
python3 experiments/argus/runner.py prepare --config /secure/generated-suite/suite.json --output /secure/evidence/paper01
python3 experiments/argus/runner.py preflight --output /secure/evidence/paper01 --role client
```

`suite.py` 为每组生成独立客户端名、UID/GID、目标/客户端 SPIFFE ID、配置和负载输入，核对服务端 allowlist。必须按照生成文件部署对应客户端。源文件、配置、策略/二进制 manifest 引用被冻结；不要在 `prepare` 后改实验配置。

`preflight`、`run` 和 `resume` 可用 `--run-id`、`--case` 或 `--group` 缩小范围，只检查选中项的依赖。一个功能场景无需先构建其他对照组。首轮按[双机步骤](REMOTE-RUNBOOK.md)完成实例替换准入、失效交付、私有记忆三条路径；这些步骤复用现有工具，没有新增通用编排服务。

静态 mTLS 组两端都使用专用静态实验凭据。用 `static_clients.py build` 生成独立客户端二进制，再按照 `static-clients.json` 安装和登记；不能让该组客户端悄悄继续使用普通 SPIRE 动态签发。静态发布仍保留当前实例绑定和短期读取 lease，以复用相同 Gateway 适配器。发布目录是版本化 generation；负载工具从 `ready.json` 解析并检查当前 lease，不能硬写不存在的目录根部 `svid.pem`。

运行清单 `manifest.json.runs` 给出固定随机化顺序、配对块、seed、实例数和短 `run_id`。按顺序激活该组并运行指定项，避免在同一个监听端口同时启动多个组：

```sh
python3 experiments/argus/runner.py run --output /secure/evidence/paper01 --role client --run-id RUN_ID
python3 experiments/argus/runner.py resume --output /secure/evidence/paper01 --role client --run-id RUN_ID
python3 experiments/argus/runner.py collect --output /secure/evidence/paper01
python3 experiments/argus/runner.py analyze --output /secure/evidence/paper01
python3 experiments/argus/plot.py --output /secure/evidence/paper01
```

执行器的操作是显式 argv 数组，不执行拼接的 shell。子进程返回码不足以产生 PASS：证据必须绑定当前运行和操作，且为本次新写入。`step.py` 将原业务工具的持久状态与执行器的独立结果回执分开；恢复查询不会移走业务 journal。

对已知业务任务继续调用原 `resume`；结果未知的首次写入或创建不会重放。执行器超时留下 `submission_unknown`。若操作完成但执行器中断，只有持有准确结果哈希并核对运行/操作的操作者可以 `reconcile --operation RUN_ID/OP_ID --sha256 HASH`；该命令不执行原操作。负例 FAIL 与 UNKNOWN 保留，不覆盖为后续成功。

中断的安全 GET 测量可用 `resume --run-id RUN_ID --new-attempt` 显式新开一轮：先前窗口留在 `runs/RUN_ID/attempts/N`，新一轮从预热开始。不能拼接窗口；完整 FAIL/UNKNOWN 不能用此选项反复刷结果，未知 POST/创建/故障也不会重放。统计保留中断次数，新 attempt 沿用原配对块。

默认功能配置是一个种子的 smoke 验证，不是论文统计样本。正式 E4 的种子数按实验设计另行设置；可选 E5 示例为 1/2/4/8 客户端、五轮、预热 30 秒和测量 120 秒。容量不够时保留 `CAPACITY_STOP`。E1/E2/E3 的部署相关检查和里程碑在 [scenarios.json](scenarios.json) 列出，未知环境参数必须填写，不假造硬件动作或业务里程碑。LoCoMo 与图表不在首轮功能验收的必经路径中。

LoCoMo 使用 `suite.py` 的可选 `locomo` case，或直接运行 `locomo_run.py`。它需要独立的初始空用户及登记前配置好的只读评测 Gateway；不能与 `private-memory` case 共用本批用户。每个新重复使用新用户/部署，一次中断继续原 journal。`step` 的 PASS 只表示 LoCoMo 工作负载 COMPLETE；QA 分数、注入观测、应用读取分别报告。

## 证据与统计

`events.jsonl`、每项原始证据、构建/运行清单、`verdicts.json`、`statistics.csv`、`analysis.json`、`report.md` 和 `verdicts.svg` 可保留和重算。报告区分 PASS/FAIL/UNKNOWN/NOT_RUN，延迟尾部分位数带样本数。CI 重采样独立运行或完整配对块，不把同一运行中的 HTTP 请求当独立实验。没有第二个独立运行时不输出 CI。

操作意图与恢复检查点继续原子持久化；普通压测和资源 JSONL 改为缓冲写，正常结束才 flush/fsync 并写完成标记。资源统计要求 `resources.jsonl.complete.json` 与原文件摘要匹配。runner/step/fault_trial 保留有大小上限、脱敏的错误诊断，不保存业务 stdout 或请求正文。

接收审计 v2 通过非阻塞 datagram 上报，不等持久 ACK，采集器故障不阻断业务。覆盖区间由来源水位和丢失计数确定，缺口及崩溃尾部为 UNKNOWN；明确的越界读取仍为 FAIL。`fault_trial` 默认增加跨故障的分块请求，先确认应用读取首块再注入；NGINX 缓冲导致无法形成该场景时记 NOT_RUN，不修改正常代理设置来制造通过。

`plot.py` 使用 Matplotlib 从同一统计文件导出带独立运行数和 CI 的 SVG/PDF 性能图；环境需安装 Matplotlib。缺少完整定量观测时返回 NOT_RUN，不画示意数字。原始科学图不与状态计数图混用。资源文件放在各运行的 `resources.jsonl`；RSS 指标标为“各进程峰值之和（上界）”，不冒称同一时刻的系统峰值。

`load_fleet.py` 使用实际 mTLS、精确服务身份和各客户端独立业务 key，采用有界并发的开放到达负载。API Goodput 指测量窗口内成功完成的期望 HTTP 请求；它不等于 Agent 回答正确率。正常成功、授权拒绝、超时、未知及本地过载分别输出。CPU/RSS 用 `resources.py` 独立采样，PID 重用停止该序列。采集开销需用相同派生镜像的 audit on/off 单独配对测量。

E5 的 `connection_mode=new|reuse` 分开统计建连与 API 成本。复用连接限于同一客户端、同一 worker；每次请求重新检查当前凭据读取 lease，身份轮换后重建连接。`workload_kind=status_api|memory_query|custom_api` 区分探活与业务请求；`memory_query` 只接受声明的搜索接口及非空查询，报告实际叶级记忆数量、空结果和非空 Goodput。见 [E5-MEMORY-LOAD.md](E5-MEMORY-LOAD.md)。

E2 时间线的 detected 是 readiness 撤销/过期的轮询区间，entry_stopped 是 systemd inactive 且无主 PID 的观测；它们不能替代实际应用最后读取。请求读取与客户端成功响应分块分别统计，401/403 错误正文不会计作业务数据泄露。每次 collect 保存独立原始输入快照及摘要，支持中断后重算。

`admission_cases.py` 产生七类带真实 Merkle/DSSE 签名的合成历史，完整规则调用生产 `LogVerifier`。`history_diagnostics.py capture/replay` 归档真实记录时保留当时上下文及信任材料哈希。它们只评价历史子验证器：配置摘要、Quote、REPORTDATA、nonce 新鲜度属于外层验证，不能据离线输出写成“实时准入通过”。

保留 `CanReattest=false` 和现行批准策略；没有增加 `TCB UpToDate` 条件。多目标路由、协作者发现、任务级授权、直接 Agent–Agent 编排，以及对已交付明文的撤销都不在本轮保证之内。
