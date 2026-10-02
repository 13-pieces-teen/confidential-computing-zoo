# 按实验执行：IP1/IP2 的先后顺序

本表执行范围对齐论文 revision 2095；前序代码补齐见 [实现记录](IMPLEMENTATION-1935.md)，本轮最小输入检查见 [WORK-ITEM.md](WORK-ITEM.md)。建议顺序为 **P0 准备与管理域隔离核验 → E1 准入及生命周期崩溃 → E2 失效交付 → E3 复用恢复 → E4 单客户端六步工作项 → E5 成本 → 总报告**。E5 的单客户端 pilot 也可在 E3 后先做；首次启动/准入的计时在 P0/E1 顺手采集，E5 引用同一份原件，不重复当成独立样本。真实运行尚未完成的项目保留 NOT_RUN。

IP1 包括 SPIRE Server/Trustee 所在控制侧及客户端 TDVM；IP2 是服务 TDVM。以实际进程位置为准，不能从 IP1 读取 IP2 的本地 socket。两侧使用同一交付提交，保留正常节点身份与现行政策。

## 怎么发送 prompt

分别把 [IP1](../../adapters/OpenClaw/spiffe_client/PROMPT-IP1.md)、[IP2](../../adapters/OpenClaw/spiffe_client/PROMPT-IP2.md) 发给对应主机的 Codex。两份文件都按 P0、E1—E5 分节。**每次指定一个实验、一个场景/实验组和当前步骤**；完成当前步骤后输出交接信息，等待需要的另一侧结果，不自行把后续实验全跑掉。

两份旧 prompt 中的多客户端和 LoCoMo 描述不是当前主实验要求，以本页和 [WORK-ITEM.md](WORK-ITEM.md) 为准。E4 使用 [四条件配置](examples/work-item-paper-suite.example.json) 的 `argus-single-work-item-v1` profile；各轮显式设置 `scenario: work-item-v1`，避免兼容入口默认运行旧 18 步场景。

例如先给 IP2：

```text
按 PROMPT-IP2.md 执行 E2 的准备步骤。本轮为 full_argus / helper-freeze。
run_id：本轮实际唯一 ID；交付 SHA：两机共同版本。
完成后保存准备记录，告诉我交给 IP1 的路径和参数。先不手动注入故障。
```

把 IP2 的交接信息贴给 IP1：

```text
按 PROMPT-IP1.md 执行同一轮 E2，使用下面的 IP2 准备结果。
完成采集和判定后保存数据，告诉我 IP2 现在应做的收尾/恢复步骤。
```

交接由操作者转贴信息和传输文件即可，不增加协调服务。已配置的 SSH 只供实验工具执行预先明确的远端命令。`READY` 等交接词是人的进度说明，不代替工具输出的真实就绪/接收证据。

## P0：先锁定两机版本，再验收一条真实链路

两机在已有仓库中取回本次交付，`DELIVERY_SHA` 替换为本次推送的完整提交号；记录工作区差异，不能覆盖已有本地修改。后续所有实验固定这个提交，不随分支更新漂移。

```sh
git fetch origin feat/argus-spiffe-v2-val
git switch --detach DELIVERY_SHA
git rev-parse HEAD
cd cczoo/agent-cc
python3 experiments/argus/delivery.py verify --manifest experiments/argus/source-manifest.json
```

IP1 执行 `bash experiments/argus/remote-software-checks.sh client`。IP2 先执行 `cargo test --locked --manifest-path core/argus/Cargo.toml --bin argus-spire-evidence-provider`，再执行 `bash experiments/argus/remote-software-checks.sh server`（其中包含正常 Linux 构建）。Provider 的本地 Windows 交付没有编译结果，这一步不能省略。两侧使用已有锁定依赖，归档命令输出及实际构件摘要；软件检查不计作真实 TDX 样本。

随后按 [REMOTE-RUNBOOK.md](REMOTE-RUNBOOK.md) 完成组生成、实际构件部署、IP1 Entries/Trustee 与 IP2 注册/准入的交接。先只启用 Full、一名业务客户端及一个 Gateway，验收输入 → 工具 → 传输 → 后端完整读取 → 持久提交 → 新会话回读。六阶段业务 smoke 与 E4 六个计划步骤是不同检查。普通业务身份不能访问管理接口。P0 不通时先修复这条链路，不启动故障或性能测量。

## 总顺序

| 阶段 | 先做什么 | 接下来 | 最后做什么 |
|---|---|---|---|
| P0 准备 | 两边并行核对版本、构建；IP2 给实际镜像/Helper 摘要、实例配置和所需身份 | IP1 配好对应 Entries/Trustee 并回交；IP2 正常启动服务、准备普通用户和审计 | IP1 单客户端六阶段 smoke；两边记录部署信息和结果 |
| E1 准入 | IP1 准备 Trustee/Server 记录；IP2 准备 Full 的试验配置与合法实例 | IP2 按配方逐步改变实例，在 before/未登记新实例/重新准入阶段保存观察；IP1 按交接核对访问及关联原件 | IP2 compare、恢复合法实例；IP1 合并报告；之后才开始下一个场景/组 |
| E2 失效交付 | IP2 准备合法实例、独立 collector、路径和 run ID，不执行故障 | IP1 独占协调：新/旧连接基线 → 在途首块 → SSH 故障 → 观测 → 收集 | IP1 通知窗口结束；IP2 保存日志、解除 hold 并显式恢复；IP1 复查访问及汇总 |
| E3-A 普通 Agent 续期 | IP1 启动持续非空记忆 API probe，并确认已进入测量；IP2 采 Provider before | IP1 开 Node observer，等待自然续期并正常结束；IP2 随后采 Provider after | IP1 在快照结束后让 probe 正常完成，导入双机原件 collect；不重启 Agent |
| E3-B Workload 轮换 | IP1 先启动 probe；IP2 在本机启动带 provider_socket 的 Workload observer | 保持目标/Helper 正常，等待自然证书轮换，observer 正常结束 | IP1 probe 覆盖整个窗口，取回 IP2 原件 collect |
| E3-C 已知启动恢复 | 准备真实已知且未完成的 launch ID；IP1 先启动 probe，IP2 先启动 creates observer | IP2 lifecycle observer 取得 before 后，只执行一次 resume-launch，再记录 after | creates/probe 均覆盖完整过程后正常结束，IP1 合并；没有适用未完成操作则记录 NOT_RUN |
| E3-D/E 新订阅与合法替换 | 分别冻结同实例新订阅、新受控 launch 案例；保留实例与旧订阅 | 用各自生命周期命令实施，保存新准入/订阅/Quote/入口原件 | 关联新 SVID 的首次非空业务响应；不能用 resume-launch 代替替换 |
| E4 持续工作项 | IP1 冻结六步行程任务、模型、协议、规模；IP2 按本 run 准备新用户、组和恢复包装 | IP1 配置并登记 Gateway，preflight 后运行单个 run；约束逐步释放，固定时点触发故障与显式恢复；未知写入只查询 | IP2 保存控制/准入/就绪原件；IP1 保存工作项、提案状态、实际释放、真实读写、最终正确性及收集分析；再切下一 run |
| E5 成本 | 两边确认组、规模、连接模式、负载及真实 PID；IP2 先启资源采样 | IP1 在采样已开始后运行负载，完成预热和测量；IP2 保持环境不变 | IP2 采样覆盖负载结束并自然写完成标记；IP1 收集每台主机的资源记录及请求结果，逐轮汇总 |
| E5 小型补充 | IP2 按 [成本配方](E5-COST-TRIALS.md) 冻结三个历史点或 A/B 同链待决；IP1 保留对应原件 | 历史点显式新订阅；共享场景只取证探针不停止 B 原 Helper，IP1 旧连接负载覆盖全过程 | 传输 Trustee 原始计时及负载文件，只读关联；共享影响复用 E1 原 run ID |

P0 不需要为每个实验从零重装。只更新确实变化的组件、组和实例；修改配置后按正常登记/准入流程启用。一个失败结果也可以完整归档；若后续依赖健康基线，先恢复该基线，而不是重跑到 PASS 或丢掉失败轮次。

### 每阶段怎样收尾

- **E1：**先合法新准入，再做 `record_pending`（实际 start 已生效、记录尚未确认），最后恢复确认并独立新准入；Full/native 使用相同本地控制。以 [E1-STAGE-RECEIPTS.md](E1-STAGE-RECEIPTS.md) 的 `pending → attempt --new-subscription → observe → compare` 为入口，保留第一拒绝层、目标、待决 mutation 及 before/after。随后按现有配方补齐替换、错配和声明的崩溃点。两组都拒绝或场景不可达也要报告；这不证明完整历史的独立优势。可能使 RTMR/数据库不一致的切点留到最后，在单独试验环境执行，不能在后续实验要复用的健康节点上制造未决损坏。
- **E2：**先做 Full/helper-freeze 的一轮 pilot，确认新/旧连接与在途接收证据齐全，再做既定故障和关闭消融。看 `result.json`、`assessment-*.json` 和 `timeline-*.json`；后端仍存活、入口关闭及实际接收分别判定。仅由 IP1 协调器注入，IP2 保存窗口后显式恢复。
- **E3：**依次观察 Node 自然续期、Workload 自然轮换、已知启动恢复、同实例新订阅及新受控替换。按 [配方](examples/E3-LIFECYCLE-RECIPE.md) 用 `lifecycle_trial.py collect` 合并两侧原件，得到每个子场景自己的结果 JSON；身份变化、Quote 数量、API 中断与恢复分别查看。没有真实已知未完成 launch 时该项保留 NOT_RUN。
- **E4：**先跑一条无故障和一条故障/恢复工程 pilot，验收原始 Proposal 可回读、错误旧 Decision 可纠正、缺必要回读不能判成功。先用 `prepare` 校验计划种子，拒绝同值提案，再冻结模型、种子列表、时间表、期限和样本数。正式按 manifest 的配对顺序运行 Full/native × 正常/故障；每个配对块 4 轮，N 个块共 4N 轮，每轮 6 个步骤。pilot 与正式目录分开，每轮使用新用户和独立 secret seed；同一轮恢复保留原 journal。查看完整任务成功、已确认状态上的继续、合法恢复三个结果，不能把 6 个步骤当成 6 次独立重复。
- **E5：**先固定单客户端负载，再比较 Full/native 的非空记忆 API、连接及资源成本；沿用示例的 30 s 预热、120 s 测量、5 次独立重复，若 pilot 后需变动须在正式运行前冻结。新连接/复用连接分开测量。三个历史长度点由 `cost_trials.py history` 输出 `series.json`；共享待决由 `shared-collect` 输出独立 JSON，复用 E1 的原 run ID，不新增独立样本。健康性能窗口与故障窗口分开。

以上数字是执行配置，不是已测结果或充分统计功效的保证。每阶段先确认原件能重算、结果范围明确、服务恢复到下一轮要求，再进入下一项；科学结果为 FAIL 本身不构成删除该轮的理由。

## 每个实验完成后保存什么

每个子场景/实验组/重复使用唯一 `run_id` 和新目录；runner 管理的 E4/E5 以 `manifest.json.runs` 中实际生成的 ID 为准，不能另起一个不匹配的 ID。两台主机各有自己的本地证据目录，路径可以不同。

| 实验 | IP1 原件 | IP2 原件 | 汇总要写的数字/判定 |
|---|---|---|---|
| P0 | 版本/构建、客户端配置摘要、六阶段业务结果与权限负例 | 版本/构建、政策/镜像/Helper 摘要、launch/目标/身份/就绪 | 哪条路径已通、哪个阶段失败、未执行项 |
| E1 | 实际 Server/Trustee 材料、按 nonce 关联的导出、阶段访问记录 | before/unregistered-new/after 的 observation、target、comparison、准入原件 | 各案例真实准入/拒绝层、实例变更、共同检查与历史差异可达性 |
| E2 | trace、实际释放、state、result、assessment、collection 快照和 timeline | 原始 fault JSONL、receiver JSONL、lifecycle、collector 覆盖和恢复记录 | 故障/检测/入口/最后读取时间，新/旧/在途结果；故障前释放的延迟读取、故障后新事实读取与 UNKNOWN |
| E3 | 连续 requests/load-result、Node observer（A）、最终 collect 结果 | Provider before/after；Workload observer（B）；creates/resume 原件（C） | SVID 更新、Quote 尝试/生成/失败、Provider 启动、覆盖与中断、是否重复创建 |
| E4 | fixture checksum、模型/配置、稳定工作项、提案状态、state/result、请求/实际释放、时间线 CSV、分析 | 本 run 用户作用域信息、控制回执、实际故障及恢复/准入/就绪日志 | 六步全计划/尝试/确认/拒绝/未知、最终约束正确性、实际读取、完整任务/正确继续/合法恢复及耗时 |
| E5 | requests/load-result、客户端资源、冻结负载、逐轮统计 | 服务端资源及 complete 标记、Quote/Trustee/就绪计时原件 | 分层延迟、样本数、Goodput、实际连接复用、错误率、CPU/RSS、独立重复数 |

每个 run 在原始工具输出旁各写一份 `IP1-summary.md` / `IP2-summary.md`，使用 [单轮模板](RESULTS-RUN.template.md)；**不用另造工具结果 JSON**。IP1 接收 IP2 原件后写本轮 `SUMMARY.md`，再更新整批 [RESULTS-FRAMEWORK.template.md](RESULTS-FRAMEWORK.template.md) 对应行。保留原始文件名和相对引用，转移整个工具证据目录，使用 SHA256 核对文件。预测文本留在受保护目录，交接信息不含 key/正文。

## E4/E5 的执行与总结果入口

以下命令从 `cczoo/agent-cc` 在 IP1 执行，路径均为需要准备的实际受保护配置/证据目录。E4 从 `examples/work-item-paper-suite.example.json` 复制配置，并为每组/种子/条件填写独立的 `work-item-v1` 配置；E5 使用 `examples/suite.performance.example.json`，各自生成一批 suite。示例配置不是开箱即跑的远端部署。

```sh
python3 experiments/argus/suite.py --config /secure/e4-suite.json --output /secure/generated-e4
python3 experiments/argus/runner.py prepare --config /secure/generated-e4/suite.json --output /secure/evidence/e4
# 读取 manifest.json.runs，按其顺序先部署本轮对应组、Gateway 和新用户；RUN_ID 替换为实际 ID。
python3 experiments/argus/runner.py preflight --output /secure/evidence/e4 --role client --run-id RUN_ID
python3 experiments/argus/runner.py run --output /secure/evidence/e4 --role client --run-id RUN_ID
# IP2 原件已传回并按该轮配置关联后，每完成一轮就更新统计：
python3 experiments/argus/runner.py collect --output /secure/evidence/e4
python3 experiments/argus/runner.py analyze --output /secure/evidence/e4
python3 experiments/argus/plot.py --output /secure/evidence/e4
```

E5 换用对应配置、generated 和 evidence 目录，保持同样顺序；负载运行前先在两机启动资源采样。中断时使用相同 run ID 的 `runner.py resume`，不再次 `run`、不重发未知写入或故障。每轮结果先看其工具原件；每批看 `verdicts.json`、`statistics.csv`、`analysis.json`、`report.md` 与 `figures.json` 指向的图。E4 另有 `continuous-work-items.csv`、`continuous-tasks.csv`、`continuous-tool-events.csv`，可追到具体工作项和步骤。

**最终总报告由 IP1 维护一份 `RESULTS-FRAMEWORK.md`（复制模板）**：逐行链接 E1 comparison、E2 接收/时序、E3 lifecycle collect、E4 四条件统计、E5 性能及成本结果，记录支持的论文主张和仍未验证的范围。runner 的 collect/analyze 只汇总该 suite 登记的运行，不会自动吸收单独执行的 E1—E3、历史长度或共享待决结果；不要拼接不同格式 JSONL 伪造一个总分。全部预定轮次均有原件或明确缺口、配对数/分母/失败与未知完整列出时，这一份报告就是整批实验的最终入口。

每次交接只需这几项：

```text
实验/场景/组/重复/run_id：
当前主机、已完成步骤、实际 UTC 起止时间：
结果：工具原始结果 + 简短解释（未运行项 NOT_RUN，证据缺口 UNKNOWN）
证据目录/需转移的文件及摘要：
当前服务状态：健康 / 故障保持中 / 已恢复 / 未知
下一步由 IP1 或 IP2 执行什么：
```

## 跨机取证的三点细节

1. E2/E4 在测量期间只有 IP1 控制器触发故障。IP2 不再人工执行同一条命令，也不提前恢复；E2 窗口结束后的恢复由明确交接开始，E4 恢复已在固定时间表内。
2. E3 的 Provider before 要早于 observer 的第一份快照，after 要晚于最后一份，均留出实际测得的时钟误差。API 测量还要包住 observer 窗口。提前协商足够的时长，让工具正常结束写出完整结果；窗口不够就保留 UNKNOWN，不拼接多个窗口。
3. E5 两机资源文件分别保存为 `resources-ip1.jsonl`、`resources-ip2.jsonl` 及各自 `.complete.json`，不要拼接：两机 PID 可能同号。当前 analysis 的 `resources.jsonl` 只代表指定的一侧/进程集合；在报告标清范围，另一侧原件和单独统计保留。不能把服务端 CPU 当成整个系统 CPU。资源默认按整个实际采样窗口汇总，写明窗口长度；不把包含预热和收尾的总 CPU 冒称仅测量期 CPU。

完整 CLI 参数仍以 [REMOTE-RUNBOOK.md](REMOTE-RUNBOOK.md) 和各实验配方为准；本页负责顺序和交接。没有执行的真实 TDX、模型、关闭时间及性能保持 NOT_RUN。`CanReattest=false` 和当前批准政策继续沿用。
