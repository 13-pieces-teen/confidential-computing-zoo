# 双机交付和验收顺序

本文件是待远程运行的步骤。代码、本地测试、镜像构建、真实准入、真实业务结果分别记录；不能把脚本已生成写成远程 PASS。`IP1` 指原有 SPIRE Server/客户端控制侧及客户端 TDVM，`IP2` 指服务 TDVM/TC API/OpenViking 侧；沿用已有连接配置和信任根。

## 两侧先准备

拉取同一提交，记录 `git rev-parse HEAD`、工作区补丁摘要、当前批准策略/镜像摘要及构建输出。保留既有 Agent 数据、Node 身份和现行策略；不得为方便复现而清空 Node 数据。构建时从仓库根进入 `cczoo/agent-cc/core/spire/workload` 执行 `bash scripts/build.sh`。原有第 1—3 项的 [交付说明](../../core/spire/workload/IMPLEMENTATION-20260925.md) 继续适用。

OpenClaw 定制插件也需从该提交重新构建，并通过正常部署/登记安装到 Gateway。相同 `argus.3` 标签的旧包不具备新增的请求失败关联观测，仅会保守返回 UNKNOWN；以实际包和镜像摘要核对版本。目录整理后的示例位于 `experiments/argus/examples/`，现有 CLI 路径保持不变。

使用 [variants.py](variants.py) 生成各组，只将已校验的完整 payload 装到实验位置。每次换组先停止前组，确认 unit、旧入口和 Broker 不再运行，再激活下一组；Node 状态继续保留。使用生成组内的 `server_script`、`server_config` 及服务名，不能混用生产 `workload.py`。`inspect --live` 在正式故障注入之前运行；之后人为设置的 `Restart=no` 实验 drop-in 要单独保存，不伪装成正常生效配置。

首轮只准备完整 Argus，按三条路径收集证据：合法实例与未重新准入替换实例的区别；实例/Helper 失效后的交付窗口；单客户端通过后扩到三个客户端的私有记忆。其他对照组、性能 pilot、LoCoMo 和图表均按需启用，不是功能验证的前置条件。`runner preflight/run/resume` 可用 `--run-id`、`--case`、`--group` 选择本次范围。

## IP2：共享服务与接收审计

1. 先使用完整 Argus 完成旧单客户端的 `launch/resume-launch → register → start → verify`，将运行与构建 manifest 保存到专用 evidence 目录。
2. 依 [receiver_audit/README.md](../../adapters/OpenViking/receiver_audit/README.md) 构建锁定派生镜像，核对其实际 image config digest，走现有镜像批准和 TC API 发布流程。原基础镜像的批准值不能冒用到派生镜像。
3. 在该次隔离部署配置中声明 `receiver_audit.run_id/mode/image_config_digest`，创建 root 控制的 data/control/evidence 目录，正常 TC API launch。只有 data socket 目录进入目标容器；collector/control/evidence 不进入容器。
4. 对实际 listener PID 完成 register，使用 `collector bind` 生成受保护的实例关联记录，然后独立运行 collector。采集器不得放在 Helper/NGINX/目标容器的 systemd 依赖停止范围内。
5. 使用原有效 API 管理凭据创建三个普通业务用户及各自 key。正常业务配置中不使用 root key。把用户 key 通过现有秘密分发渠道交给相应客户端的受保护文件；不要写入实验清单、命令行、日志或 Git。
6. 配置精确 `allowed_client_ids`；删除配置中的旧 `client_id` 字段，避免两者并存。每组从生成的 `environment.json` 获取目标和客户端 ID。

镜像批准仍核对实际摘要。审计本身不参与准入：采集器未启动、退出或队列满不阻断业务。可以继续业务验收，但 E2 相应接收区间为 UNKNOWN，不能关闭审计后记为零接收。audit off 只用于单独、明确标识的开销对照。

## IP1 / 客户端 TDVM：独立 Gateway 与私有记忆

遵循 [FLEET-ACCEPTANCE.md](../../adapters/OpenClaw/spiffe_client/FLEET-ACCEPTANCE.md)。先部署一个 Gateway，通过原随机事实跨会话验收，再使用三实例 fleet 配置。节点公共安装/重启单独执行，实例命令都带 `--instance`。

```sh
python3 adapters/OpenClaw/spiffe_client/deploy.py render-node --config /secure/fleet.json --output /secure/rendered-node
python3 adapters/OpenClaw/spiffe_client/deploy.py render --config /secure/fleet.json --instance alice --output /secure/rendered-alice
# 按 fleet 文档完成 IP1 Entries 与 Guest 安装/登记；同样处理 bob、carol。
python3 adapters/OpenClaw/spiffe_client/fleet_business.py run --config /secure/business.json --output /secure/evidence/three-clients
python3 adapters/OpenClaw/spiffe_client/fleet_business.py resume --config /secure/business.json --output /secure/evidence/three-clients
```

验收必须检查六阶段结果、实际 API 用户、私有范围、注入证据和新会话回答。包含跨用户直接读/搜索/问答、有效身份+无效 key、错误身份+有效 key、伪造请求头、直接后端入口负例。没有配置 wrong-identity 或 direct-backend 负例时对应项目为 NOT_RUN，不能省略后称完整通过。

## E2：客户端协调双连接，服务端执行故障

配置 SSH host alias 指向服务 TDVM，并保持默认主机密钥校验。从 [fault-trial.example.json](examples/fault-trial.example.json) 创建实际配置。`run_id` 必须与审计部署、collector、业务里程碑一致；`clock_uncertainty_ms` 来自实际同步测量，示例 20 ms 不代表当前环境。`bound_ms` 是待检验参数，示例 10 s 不是已证明的 SLA。

用 `milestone.py` 从实际业务 journal 派生里程碑，不能手写 `reached:true`。例如另一个终端执行：

```sh
python3 experiments/argus/milestone.py --business-dir /secure/evidence/three-clients \
  --instance alice --milestone nonempty-extraction --run-id RUN_ID \
  --output /secure/evidence/fault-trial/milestone.json --wait-seconds 120
```

工具核对实际运行、实例、任务、mTLS 写入和阶段原始文件的快照摘要；故障门禁再次验证这些关联。协调器等待两条 TLS lane 各至少三次成功、旧连接未重连，以及派生里程碑。默认另发一个在途分块 POST，并通过 collector `status` 确认应用已读取首块后才注入故障。NGINX 缓冲等原因使该条件不可达时记 `BASELINE_OR_INFLIGHT_READ_UNREACHABLE / NOT_RUN`，不发故障；显式设 `probe.inflight=false` 时只验证原双连接范围。业务里程碑证明阶段已完成，不是模型内部同步断点。

```sh
python3 experiments/argus/fault_trial.py run --config /secure/fault-trial.json --output /secure/evidence/fault-trial
# 中断后只收集既有证据，绝不重发故障：
python3 experiments/argus/fault_trial.py resume --config /secure/fault-trial.json --output /secure/evidence/fault-trial
```

支持 Helper 冻结、Helper 退出、目标退出。目标替换在完成旧目标的观测窗口后使用正常 launch/register 流程，先保存旧证据，再由 `remote_acceptance.py replacement` 记录新 launch/container。配置变化与同容器进程/网络/监听实例替换使用 [fault_fixture.py](FAULT-FIXTURES.md)：只允许 `/srv/argus-experiments` 内受保护的配置、明确摘要和实际 target。协调器新增 `server_fault_fixture` 指向该工具，并在配置变更时填入 `fixture.original/replacement/original_sha256/replacement_sha256`。

配置故障原地修改已绑定 inode，防止原子换文件后容器仍读取旧 inode 而产生假实验。恢复是显式命令，保留单次修改意图，遇到第三方修改或实例变化拒绝覆盖。同容器 restart 会同时替换进程和网络/监听实例；它不是纯端口变化消融。同容器新 PID 可通过内核映射观测；新容器使用 collector `add-target --control … --target … --run-id …` 增加观测关联，不授予 Argus 身份。无法归属、丢包或崩溃尾部保持 UNKNOWN。

采集窗口结束后，协调器额外留出一秒让周期水位覆盖最后样本，然后 finalize；该等待不是持久化时限保证。判定按停止界限后的实际读取时间和 v2 覆盖区间，不要求整轮 `complete=true`。使用该组安装目录内的 `remote_acceptance.py release --config … --fault …` 移除本次唯一拥有的重启控制，此操作不启动服务。明确恢复时再运行该组 stop/登记/start/verify。分别保留旧/新连接、在途请求和 receiver 结果；接收记录确定违规时保留 FAIL，缺失记录不推导零接收。

## E1 / E3：准入与恢复

E1 七种情形见 [scenarios.json](scenarios.json)。合成离线诊断可先运行：

使用含生产 Trustee 锁定依赖 `sigstore==3.6.7` 和 `pytest` 的隔离环境；合成工具复用仓库现有签名 fixture。

```sh
PYTHONPATH=core/tlog python3 experiments/argus/admission_cases.py --output /secure/evidence/e1-signed-fixtures
```

真实准入保留服务器 nonce、Quote/REPORTDATA 核验、批准策略、完整历史、当前实例、Entry 及实际 TLS/接收证据。配置错配/旧证据由外层 Trustee/协议验证，离线 LogVerifier 的 ALLOW 不能覆盖这一层。

真实路径使用新增 [E1-REAL-RUNS.md](E1-REAL-RUNS.md) 的固定配方及 `admission_trial.py preflight/observe/compare`：合法准入→旧实例停止→同镜像新实例的旧绑定拒绝→独立准入成功，另跑无关受控活动与配置变化。生产 verify 成功关联到实际 accepted nonce；本地 target checker 拒绝标为 `LOCAL_BINDING_REJECTED`，不能直接写成远端 Verifier DENY。当前 CLI 没有完整导出原 Quote、request 与签名 EAR 的接口，对缺原件的外层负例保留 NOT_RUN。

E3 用 [node_attestation_observe.py](../../core/spire/workload/scripts/node_attestation_observe.py) 在首次加入/普通 Agent SVID 续期前后采样，独立记录 Workload SVID 和 Workload Quote。用 `lifecycle_evidence.py` 检查同实例轮换和恢复；缺少创建事件完整覆盖时，不把前后相同容器快照写成“没有创建过重复容器”。

在 IP2 独立启动 `lifecycle_evidence.py observe-creates --workload-id WORKLOAD --duration 120 --output creates.jsonl`，等待输出 `CREATE_OBSERVER_READY`，再采集已知未完成启动的 before snapshot、执行 resume-launch、采集 after snapshot，最后等事件订阅自然到期。该 observer 仅记录元数据，丢失、提前 EOF、PID/目标不一致均使结论为 UNKNOWN。时间窗口必须覆盖整个恢复过程；同主机时钟下用 `lifecycle_evidence.py resume --before … --after … --resume-result … --creates … --output …` 生成判定。

已知 launch ID 时运行：

```sh
python3 /opt/argus-experiments/paper01/full_argus/payload/scripts/workload.py resume-launch \
  --config /etc/argus-experiments/paper01/full_argus/environment.json --launch-id KNOWN_LAUNCH_ID
```

未知首次创建不自动再 POST；需要刷新签名身份时只使用既有 commit 接口。记录恢复前后已知操作、target、独立 Docker 创建事件、容器数和中断窗口。

新增 [E3-LIFECYCLE-RECIPE.md](examples/E3-LIFECYCLE-RECIPE.md) 将上述快照与同 run ID 的连续非空记忆 API trace 关联：SPIRE Server API/metrics 所在主机采 Node，OpenViking 主机采 Workload，客户端独立运行 `load.py` 或 `load_fleet.py`。`lifecycle_trial.py observe` 只读等待自然轮换；`collect` 核对时间覆盖、身份和原文件哈希，分开输出身份变化、Quote 计数与业务中断。缺 Workload Quote counter 时输出 UNKNOWN，不把它填成零。

## E4：多客户端故障与 LoCoMo

完成三客户端随机事实链路后，按 [FLEET-FAULT.md](examples/FLEET-FAULT.md) 的固定命令运行 `client-stop` 和 `shared-service-fault`。局部故障只停止选中容器，连续查询其他 Gateway；共享故障复用已有远程注入工具。每次先保存完整业务验收证据和至少三轮成功基线，再故障、观测，最后由操作者显式按正常部署恢复。`observe-recovery` 只做安全查询和各客户端新会话问答，不自动重建或重新故障。它评价业务影响，零交付仍由 E2 的实际接收证据回答。

LoCoMo 遵循 [LOCOMO.md](LOCOMO.md)，先从本地锁定数据做固定样本转换。每个对话绑定独立普通用户和已登记的评测 Gateway；登记前设置 `autoCapture=false`、`autoRecall=true`、禁用模型工具写回。原始历史显式导入，QA 每题新会话，答案只留在本地评分器。不同组/重复使用不同初始空用户。可直接运行 `locomo_run.py`，或用 `suite.py` 可选 `locomo` case 纳入 prepare/run/resume/collect/analyze。

LoCoMo 的 COMPLETE 表示题目执行完成，不意味着答对、非空提取或安全通过。报告包括 all-task 与 answered-only F1、按对话/类别分层、UNKNOWN/NOT_RUN、注入观测；category 5 的弃答独立统计。结果采用声明的派生词汇评分，不标作官方 LoCoMo 成绩。

## 对照与性能

按问题选择组后，再按 runner manifest 顺序激活：准入比较用完整 Argus/原生 SPIRE；关闭机制用完整 Argus/−Watchdog/−Close；基础成本按需加入静态 mTLS。默认不运行完整交叉矩阵。弱化组使用专用生成物、身份、Entry 和目录。−Close 经 `inspect` 核实主动停止来源移除；−Watchdog 保留其余检查和停止路径。

静态组先 `static_clients.py build --output /secure/static-build`，记录实际 binary SHA，将其安装至配置声明的路径。为每个静态客户端和静态服务器签发专用实验 URI SAN 证书，使用同一专用测试 CA。根据生成 `static-clients.json` 对每实例执行 `install`、`register`，并 `inspect` 核对实际 unit；该组使用静态 register，不能启动普通动态 publisher。结束后按工具 `restore` 恢复原 unit，或停用整个隔离实例。

三客户端功能完成后，运行 1/2/4/8 规模。没有四/八个独立客户端时保留容量停止。先做 pilot 冻结总到达负载，各组采用同样配置，再执行 30 s 预热、120 s 测量、五次独立重复。真实 Agent 任务单独评价，不和普通 API 延迟混合。使用 `resources.py` 同步记录受测进程 CPU/RSS，审计开销采用同镜像 on/off 配对。

`new` 与 `reuse`、`status_api` 与 `memory_query` 分别配置和汇总，见 [E5-MEMORY-LOAD.md](E5-MEMORY-LOAD.md)。预实验必须为每个用户准备实际非空记忆，报告实际建连/复用数、TCP/TLS/API 时间、非空 Goodput；不能只测健康检查。固定 POST 搜索的中断测量不自动重放，新实验需保留旧窗口并显式准备。

性能配置见 `examples/suite.performance.example.json`。安全 GET 测量中断后可 `runner.py resume --output … --role client --run-id … --new-attempt`，保留旧 attempt，从预热重新开始；未知业务写入和故障不重放。每行测量不再同步刷盘，正常结束才写完成标记，分析时排除未完成窗口并报告中断次数。资源日志必须连同 `resources.jsonl.complete.json` 一起收集。

最后运行 `collect` / `analyze`，保存原始 JSONL、运行清单、判定、CSV、Markdown 和 SVG。先报告合成机制任务，再报告 LoCoMo 派生任务，所有未执行项保留 NOT_RUN。不能把本地 source/fixture 测试计入真实远程实验样本。
