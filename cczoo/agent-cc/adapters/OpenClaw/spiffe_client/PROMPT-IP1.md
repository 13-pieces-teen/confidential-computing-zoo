# IP1 Prompt：一次执行一个实验

请在 IP1（SPIRE Server/Trustee 控制侧及客户端 TDVM）工作，依据论文 revision 1373 和本轮交付代码。阅读本文件及 `experiments/argus/TWO-HOST-SEQUENCE.md`。本次只执行操作者指定的 **P0/E1/E2/E3/E4/E5、场景、组、run_id 和步骤**；没有指定时先做 P0，不自动串行跑完所有实验。

从 `cczoo/agent-cc` 运行命令。与 IP2 固定同一完整提交，读取适用 AGENTS.md，保留已有改动、节点数据、信任根和当前政策（CanReattest=false，不加 TCB UpToDate）。使用现有部署和 CLI，必要修复限定本轮，记录 diff，不自动推送。参数以实际主机/Guest/容器位置为准；IP2 socket 不能在本机直接访问。

每个步骤结束把结果写入本轮 `IP1-summary.md`，模板为 `experiments/argus/RESULTS-RUN.template.md`。每个实验完成后接收 IP2 原件，写本轮 `SUMMARY.md`，更新整批 `RESULTS-FRAMEWORK.template.md` 对应行。给操作者一段简短交接：run ID、已完成步骤、原始结果、证据目录/摘要、当前服务状态、下一台主机的具体动作。交接由操作者转贴，不自动消息联系另一个 Agent。缺少对侧材料时先完成独立准备，再列出所缺材料；已有真实材料就继续，不反复请求批准。

## P0：共同准备和单客户端 smoke

1. 与 IP2 并行核对版本、构建。按现有部署步骤重建本轮插件/Helper；运行 `bash experiments/argus/remote-software-checks.sh client`，保存输出和实际产物摘要。复用健康 TDVM、Node/Broker 与 Gateway；客户端原为 x509pop 时如实标明，不擅自切 TDX 节点路径。
2. 等 IP2 提供实际镜像/Helper 摘要、所需身份、部署配置和政策摘要后，在 Server/Trustee 侧按现有流程设置对应 Entries/策略与信任配置。需要匹配 Helper 路径/哈希的选择器使用真实产物；完成后把实际就绪证据交 IP2，供其启动服务。
3. 等 IP2 正常服务及普通用户就绪，取得 HTTPS origin、精确服务/客户端 ID、业务 key 受保护文件位置。配置并登记一个 Gateway，以原有随机事实链路执行写入—归档—提取—检索—注入—新会话回答，以及实际可运行的身份/权限负例。用现有 `fleet_business.py` 与 `FLEET-ACCEPTANCE.md`，不复用旧上下文答案，不把根 key 用于业务。
4. 记录代码/插件/镜像/政策摘要、运行身份、六阶段原件、失败及未测项。将 smoke 结果交 IP2，双方说明当前服务是否健康。完成 P0 后停在交接处；不自动开始 E1。

## E1：实例准入与历史核验

前置：P0 可用；已选定一个 E1 场景和组。阅读 `experiments/argus/E1-REAL-RUNS.md`、`E1-REACHABILITY.md`、`ADMISSION-ARCHIVE.md`。

1. **IP1 先准备**：固定 run ID/组，准备当前 Server/Trustee 日志及本机可选原件导出，保存实际政策及摘要。把准备结果交 IP2。IP2 按真实部署配方执行实例操作；本机不重复 launch/stop。
2. **IP2 分阶段交接后，IP1 核对**：分别在合法实例、未登记的新实例、独立重新准入后，以已有受保护业务配置执行同一固定查询/连接检查，记录当前 peer/SVID、请求结果与阶段。IP2 留出这些检查点；不要在它还需记录未登记阶段时要求提前 register。
3. 按 nonce/实例/时间关联 Trustee 原件和 IP2 observation。访问失败要区分连接不可达、本地旧绑定拒绝、服务端准入拒绝和业务授权拒绝。Full/native 都拒绝是共有机制的结果；历史增量按真实可达性材料判断。
4. 收 IP2 `before`、`unregistered-new`（适用）、`after`、`comparison.json` 和原件目录；保留文件结构与摘要。写本轮 SUMMARY：旧/新实例、真实判定层、对照、共同当前事实检查、历史差异是否可达。未实现在线回放的旧 Quote/错 nonce 场景标 NOT_RUN，离线诊断另列。
5. 确认 IP2 已恢复合法服务，结束这一场景/组。下一场景新 run ID；不要为了结果更好覆盖本轮。

## E2：实例失效后实际停止交付

前置：IP2 已完成本轮 collector/实例准备，并交来实际路径。阅读 `experiments/argus/REMOTE-RUNBOOK.md` 的 E2 和 `examples/fault-trial.example.json`。

1. 取得 IP2 提供的 run ID、组内工具/部署路径、collector control、receiver/fault/lifecycle 文件位置和时钟误差。配置真实 mTLS 凭据和合成业务查询，保留 `readiness=transport`。每次一个故障、一个组，先 Full。
2. **IP1 是唯一故障协调者**，只运行一次：

```sh
python3 experiments/argus/fault_trial.py run --config /secure/e2-trial.json --output /secure/evidence/E2/RUN_ID/ip1
```

工具负责新/旧 lane 各三次成功、旧连接未重建、在途首块读取、SSH 注入、观测与收集。IP2 不手工再注入，也不提前恢复。在途首块不可达则按工具记录 NOT_RUN；不改代理缓冲来制造通过。
3. 程序正常收集结束后，把“观测窗口结束、可以收尾恢复”及实际结果交 IP2。中断用原 run ID 和输出目录 `fault_trial.py resume` 仅收既有证据，不重发故障；收集未完成前不让 IP2提前恢复。
4. 保存 `trace.jsonl`、state/result、assessment、timeline 和 collection 原件；接收 IP2 fault/receiver/lifecycle、覆盖及恢复原件。IP2 完成显式恢复后，本机只做恢复访问确认，不把恢复后流量拼回原关闭窗口。
5. SUMMARY 分列故障时间、readiness/入口观测区间、最后应用读取、新/旧/在途结果、界限后读取、时钟误差及 UNKNOWN。客户端断连不等于应用零接收。完成这一个场景后结束；−Watchdog/−Close 仅在后续指定的相应机制轮次运行。

## E3：有效身份复用、轮换及已知启动恢复

每个子场景独立 run ID。阅读 `experiments/argus/examples/E3-LIFECYCLE-RECIPE.md`，先协商足够覆盖身份 TTL 的 observer/probe 时长和实际时钟误差。

### E3-A 普通 Agent SVID 续期（默认先做）

1. **IP1 先起非空私有记忆 API probe**（现有 load_fleet 配方），确认预热结束、测量请求实际成功，告诉 IP2 可以采 before。Probe 需覆盖整个 Node observer，并留交接余量。
2. 等 IP2 Provider before 原件已保存，在持有 SPIRE Server API 的本机运行 Node observer：

```sh
python3 experiments/argus/lifecycle_trial.py observe --config /secure/e3-agent-renewal.json --output /secure/evidence/E3/RUN_ID/node
```

等待自然续期，不删状态、不重注册或主动重启。Observer 正常结束后立即交接 IP2 采 after；此时 probe 继续。
3. IP2 确认 after 后，让 probe 按预定时长正常结束；不要提前杀进程丢失 load-result。取回快照原件，按配方 collect，明确传 `--provider-before` / `--provider-after`。两份快照必须是同 Agent、同 Provider 启动，包住 Node observer 窗口及误差余量。
4. 记录 serial 更新、Node 证明接收计数、真实 Node/Workload Quote 生成计数、业务连续性各自结果。窗口没观察到续期就是该问题 UNKNOWN，不写成通过。

### E3-B Workload SVID 轮换

IP1 先起新的独立 probe并确认进入测量 → IP2 在服务机启动带本机 provider_socket 的 Workload observer → 等它正常结束 → IP1 probe 正常结束并收 IP2 目录 collect。目标和 Helper 保持同一实例；分别报告证书变化与 Quote 生成，不能用 SVID 更新次数替代 Quote 数。

### E3-C 已知 launch ID 恢复

先由 IP2 确认已有真实未完成且 ID 已知的启动操作，本机起独立 probe → IP2 creates observer 就绪、lifecycle before 后只执行一次 resume-launch → observer/creates 正常结束 → 本机 probe 正常结束并合并结果。保留创建事件覆盖、launch/容器关联和中断；没有适用操作则本子项 NOT_RUN，不再造一次未知创建。

首次 Node 加入只在另行指定且有独立新节点身份时验证，沿用现有加入流程；不要删工作节点数据制造“首次”。E3 结束后归档各子项并输出汇总，不把独立窗口的计数相减。

## E4：典型 LoCoMo 任务的接入、性能与恢复

阅读 `experiments/argus/LOCOMO.md`。先单客户端无故障，再三客户端，最后才做指定的 Full/native × fault/no_fault 配对；每次只执行一个 manifest run。

1. **IP1 先准备计划**：固定本地数据 checksum、原始样本/类别/题数、模型和预算；将本 run 所需业务用户/身份和组交 IP2 准备。沿用原题，不生成累计金额/路由/前驱任务。正式配对的正常/故障/恢复阶段都需有计划题，检查 `locomo_phase_counts`。
2. 等 IP2 新普通用户、服务组和控制包装就绪，使用对应 key 文件配置/登记 Gateway：autoCapture=false、autoRecall=true、tools.deny=["*"]。每题新会话，答案只在本地评分器。修改后的实际 Gateway 需正常重新登记，不能只改磁盘配置后当运行态已更新。
3. 单例按 locomo_run 的 preflight/run；配对按 suite/runner（生成/prepare 只做一次），选定实际 RUN_ID：

```sh
python3 experiments/argus/runner.py preflight --output /secure/evidence/locomo01 --role client --run-id RUN_ID
python3 experiments/argus/runner.py run --output /secure/evidence/locomo01 --role client --run-id RUN_ID
```

本机 controller 先完成所有历史初始化，再开始 QA 固定时钟，并在计划时刻调用 IP2 的现有故障/恢复包装。IP2 不另启人工计时器、不重复操作。No-fault 使用相同控制时刻/预算和空 argv。整轮结束前不因某题失败平移时间或补发题目。
4. 初始化阶段已知提取任务可续查，未知写入不重放；QA 开始后的 resume 只补已知审计，不补发题或故障。完整窗口中部分题失败是有效测量记录，不必反复 resume 直到全答对。
5. 结束后取得 IP2 控制、故障、重新准入和就绪原件，在本 run 目录保留；然后 collect/analyze/plot。保留全部计划题/attempted/完成/注入有效完成、请求与任务耗时、拒绝/超时/未知、实际并发和恢复，F1 为功能辅助。单个 run 先落盘总结；配对不齐时保留 unpaired，待对应 run 完成再汇总。
6. 输出交接与下一轮所需组/用户信息，停在本轮结束。共享服务故障不称局部连带损失，问答成功不当作 E2 接收安全。

## E5：控制面、连接、记忆 API 和资源成本

阅读 `experiments/argus/E5-MEMORY-LOAD.md`；先 pilot 冻结负载，再正式重复。默认 1/3 实例、30 秒预热/120 秒测量/五轮，new/reuse 使用独立配置与结果。若此前已有数据，只复用明确绑定的原件，不重复计样本。

1. **两侧共同准备**：本机给 IP2 实验组/规模/实际 run ID、计划时长及 PID 范围；为每用户固定非空私有记忆查询，记录模型/数据/权限/负载。两边先装好对应组，不在测量中换组。
2. **IP2 先起服务端 resources.py**，确认首批样本已写入，再通知 IP1。本机起自己的资源采样，然后运行选定负载 run。双方采样时长留出交接和收尾余量，覆盖完整预热/测量。
3. 负载正常结束，通知 IP2 等采样自然结束；两侧保留资源 `.complete.json`。请求 trace 与 load-result 放原位置，资源分别保留主机标签；不能合并两台主机可能重复的 PID。当前分析入口 `resources.jsonl` 只代表明确选择的一侧范围，另一侧单独保留与统计。
4. 收 IP2 资源和控制面计时原件后 collect/analyze/plot。本轮写完 SUMMARY，才按 manifest 切下一 run；每轮独立保存，五轮完成后才给独立运行/配对区间。
5. 分别记录 Quote 生成均值和次数、Trustee 请求 RTT、命令至就绪、TCP/TLS/API、复用数、HTTP/非空 Goodput、失败/超时/未知、CPU/RSS。控制面首次/重新准入的原件来自 P0/E1 或本轮显式测量，不把健康服务的现成 readiness 当冷启动。任务延迟引用独立 E4，不用 API 延迟代替。

实际执行中所有未跑项仍为 NOT_RUN，未知与失败如实保存；不修改政策或记忆算法来满足实验结果。
