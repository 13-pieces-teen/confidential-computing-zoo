# IP2 Prompt：一次执行一个实验

请在 IP2 服务 TDVM 工作，依据论文 revision 1373 和本轮交付代码。阅读本文件及 `experiments/argus/TWO-HOST-SEQUENCE.md`。本次只执行操作者指定的 **P0/E1/E2/E3/E4/E5、场景、组、run_id 和步骤**；没有指定时先做 P0，不自动串行跑完所有实验。

命令从 `cczoo/agent-cc` 执行。两机固定同一完整提交，读取适用 AGENTS.md，保留已有改动和持久身份/数据。保持 CanReattest=false 和当前批准政策，不加 TCB UpToDate。沿用现有部署、实例准入及实验工具；必要修复限本轮，记录 diff，不自动推送。使用实际路径和 process/unit，不凭旧报告推测运行态。

每步结束用 `experiments/argus/RESULTS-RUN.template.md` 写本轮 `IP2-summary.md`，由操作者交接给 IP1。内容包括 run ID、已完成步骤、工具原始结果、证据目录/摘要、当前服务状态及下一步。原始证据按目录整体交给 IP1，不把日志结论手填成工具 JSON，不输出 key/正文。实际依赖未满足时保留当前状态、列明所缺材料；已有真实信息就继续，不反复请求批准。

## P0：共同准备和单客户端 smoke

1. 与 IP1 并行核对版本、构建。按已有步骤重建 Provider、WorkloadAttestor、Helper、NGINX/AuthZ 与需要的审计派生镜像；运行 `bash experiments/argus/remote-software-checks.sh server` 并保存日志。新增 Provider Rust 计时需要在本机实际构建，不能用本地 Windows 检查替代。
2. **IP2 先交配置材料**：实际镜像/Helper 摘要、路径、服务及客户端身份、所需 Entries、当前批准政策摘要，交 IP1 配 Server/Trustee。保持健康 Node 数据/CA，不重复 chain 初始化。
3. **等 IP1 Entries/Trustee 就绪**，使用实际组内 workload.py 沿原 launch/resume-launch→register→start→verify 流程启动服务。若本轮确实发生首次启动/重新准入，可按 E3 配方记录 Quote/Trustee 计时及 time-ready-command 观测；已健康服务不冒充冷启动。
4. 按 receiver_audit/README.md，为实际 listener PID 建立受保护映射、独立运行 collector；创建普通业务用户，配置精确 allowed_client_ids。交 IP1 实际 HTTPS origin、目标身份、key 安全取用路径、部署/证据目录、版本与政策摘要。客户端不用 root key；业务 key 权限与实例身份分开。
5. 等 IP1 完成单客户端六阶段与权限 smoke，协助定位实际服务/API问题。记录本机状态与原件，并将当前服务健康情况交 IP1 汇总。结束 P0，暂不执行故障。

## E1：实例准入与历史核验

前置：IP1 已准备本轮 Server/Trustee 记录，场景和组明确。阅读 `experiments/argus/E1-REAL-RUNS.md`、`E1-REACHABILITY.md`、`ADMISSION-ARCHIVE.md`。

1. 建立本轮不可变配置、新目录，先 Full；运行 admission_trial preflight。保存实际政策和需要的原件导出配置；与 IP1 核对组、身份、run ID、版本。
2. **IP2 执行实例动作**：按对应配方在合法实例记录 before；需要同镜像替换时先停止旧目标、受控 launch 新实例，记录未登记新实例；最后正常 register/start 并记录 after。每个阶段把当前目标与证据交 IP1，待它记录相应访问结果后再改变下一阶段，防止中间状态被覆盖。
3. 使用真实 before/after 运行 admission_trial compare，保存 observation、target、comparison、准入 nonce/Evidence/EAR/历史/政策原件。无关活动场景须重新准入同一目标；配置错配使用现有绑定 inode 变更配方；不要临时修改批准策略迎合错配。
4. 历史核验记录共有当前事实检查和真实可达轨迹。未登记实例不可达、本地 checker 拒绝、远端 Verifier 拒绝分别写；Full/native 都拒绝不是“历史独有优势”。Full 预期原件缺失时记录 UNKNOWN；native 本来不产生 Workload EAR，记该机制不适用，不据此判失败。未实现在线证据回放的子项为 NOT_RUN。
5. **收尾**：原始窗口及比较材料保存后恢复正常批准配置和合法实例，保存恢复/verify 结果。将完整证据目录、nonce关联、当前健康状态交 IP1，它完成本轮 SUMMARY；下一场景/组使用新 run ID。

## E2：实例失效后实际停止交付

本轮一个故障/一个组。阅读 `experiments/argus/REMOTE-RUNBOOK.md` 的 E2、receiver README 和 `examples/fault-trial.example.json`。

1. **IP2 先准备**：确认本轮合法目标、生产绑定、实际 variant；collector 独立于 Helper/NGINX/容器故障范围，run ID 与 IP1 相同。给 IP1 实际 SSH alias、组内 remote_acceptance/部署路径、collector control、fault/receiver/lifecycle 输出路径、时钟误差和健康证据。
2. 回复“本轮准备完成，可由 IP1 启动 fault_trial”。**不要手工注入，也不要另设恢复计时器。** IP1 协调器会先建立新/旧连接和在途读取条件，再通过 SSH 执行一次故障并观测；不满足基线时它不会注入。
3. 观测期间保留故障状态及原始日志，不提前重启/恢复受测服务。collector 继续采集；若采集失败保留 UNKNOWN，不能停采后称零接收。若脚本中断，由 IP1 resume 收既有证据，不再注入第二次。
4. **等 IP1 明确通知观测与收集结束**，保存本轮 fault JSONL、receiver/lifecycle、collector 覆盖和服务日志。按原工具 release 解除本轮 hold（release 本身不启动），再执行需要的 stop/配置恢复/登记/start/verify。目标已退出或替换时走正常新实例准入，不能复用旧绑定。
5. 将原始目录与摘要、实际恢复动作/结果交 IP1 做恢复访问检查和最终统计。报告故障时刻、入口/实例状态与应用读取边界；缺口 UNKNOWN，超过 Δ 的读取与未准入替换读取分开。结束这一轮后才换场景/对照组。

## E3：有效身份复用、轮换及已知启动恢复

阅读 `experiments/argus/examples/E3-LIFECYCLE-RECIPE.md`。每子项独立 run ID，Provider 计数窗口避免混入无关准入。

### E3-A 普通 Agent SVID 续期

1. 等 IP1 确认持续非空记忆 probe 已进入测量，再采本机 Provider before：

```sh
python3 experiments/argus/lifecycle_evidence.py quote-snapshot \
  --provider-socket /run/argus/evidence-provider.sock --run-id RUN_ID \
  --output /secure/evidence/E3/RUN_ID/ip2/provider-before.json
```

替换实际 socket，核对返回 Agent ID 就是 IP1 选中的服务节点。把已保存的 before 时间/路径交 IP1；留时钟误差余量，让它随后开始 Node observer。
2. 保持 Agent/Provider 正常，不重启或删身份。等 IP1 通知 **observer 已正常结束**，留误差余量再用同命令采 after（改输出文件名），此时 IP1 probe仍继续。
3. 将两份原件和摘要交 IP1，再通知可以完成 probe。两次必须同 Provider 启动 ID且包住完整 observer；重启或错窗保留 UNKNOWN，不用证书变化推算 Quote。IP1 collect 后回交结果，本机记录自己的计数与实例状态。

### E3-B Workload SVID 轮换

等 IP1 新 run 的 probe进入测量，再在本机运行配置好实际 provider_socket 的 Workload observer：

```sh
python3 experiments/argus/lifecycle_trial.py observe --config /secure/e3-workload-rotation.json --output /secure/evidence/E3/RUN_ID/workload
```

让同一目标/Helper 自然轮换，不停服务、不主动重登记。Observer正常结束后交 IP1完整目录，它的probe需继续覆盖收尾。分别记录SVID和真正Quote次数。

### E3-C 已知 launch ID 恢复

1. 先确认真实已知且未完成的 launch ID，未知首次创建不能再 POST；没有适用状态则记 NOT_RUN。
2. 等 IP1 probe 已开始，在本机先运行独立 `lifecycle_evidence.py observe-creates`，看到 CREATE_OBSERVER_READY，再启动 case=resume-launch 的 lifecycle observer。
3. 看到 E3_OBSERVER_READY（before已尝试）后，按已有组内命令只执行一次 resume-launch，保存原始结果。等待 lifecycle和creates自然结束，覆盖恢复前后，再通知 IP1结束probe。
4. 交 IP1 observer、creates、resume 原件与摘要，关联 launch/容器数、创建事件和业务中断。没有完整创建事件覆盖不称“未重复创建”。

首次 Node 加入仅在另行指定的独立新节点上沿现有加入流程采集，不删除运行节点数据。各子项完成后保存summary并交IP1汇总；E3不是通过每次重新生成Quote来保活。

## E4：典型 LoCoMo 任务的接入、性能与恢复

阅读 `experiments/argus/LOCOMO.md`。只处理 IP1指定的这一个run，先无故障单客户端，再三客户端和指定配对组。

1. 等 IP1 给出本run计划/组/用户映射；本机准备对应隔离服务组和**本run全新普通用户**，提供专属key安全路径、精确身份、健康证据。No-fault和不同重复也用新用户；不清空其他人的数据。
2. 对故障run，准备并在预实验确认现有故障/release/正常恢复命令包装。包装接收实际run ID并将控制、故障、重新准入、入口就绪原件保存到本机证据目录；IP1 controller会丢弃包装stdout，必须由包装自行写回执。同一run恢复保留持久卷；跨run用户/初始数据隔离。
3. 将“本run服务和控制包装已准备”交 IP1。**等它部署/登记Gateway并启动runner；故障和恢复只由其QA固定时间表调用本机包装。** QA时钟在全部历史初始化后开始，不从本机准备完成时计时；不人工重复操作，也不因任务慢而挪动故障时刻。
4. 运行期间只做约定的观测，保留实际命令起止、故障状态、恢复/准入/就绪及错误；无故障组不调用故障命令。包装返回0只表示命令结束，真实恢复仍看服务证据和IP1后继请求。
5. IP1通知窗口结束后，归档本轮资料，报告当前服务状态；中断/未知恢复需先交接确认原操作状态，不盲目重复创建。将证据交IP1，它汇总请求/注入/任务和性能。共享服务故障影响全部依赖客户端，不称局部连带损失。
6. 完成本轮 summary 后等下一run具体计划；不要主动切组或清空本轮用户，以便补收原件。

## E5：控制面、连接、记忆 API 和资源成本

阅读 `experiments/argus/E5-MEMORY-LOAD.md`。以IP1给定的组/规模/new或reuse/负载/run ID为准；先pilot冻结参数，正式每次一轮。

1. 两侧确认实际PID、采样时长和开始顺序。记录当前组、镜像、政策、CPU/内存环境；稳态测量中不主动重启/更新。为每用户保证已经存在实际可检索的私有记忆。
2. **IP2先开服务端资源采样**，时长涵盖交接余量、预热与测量：

```sh
python3 experiments/argus/resources.py --pid ACTUAL_PID --run-id RUN_ID \
  --duration ACTUAL_DURATION --output /secure/evidence/E5/RUN_ID/ip2/resources-ip2.jsonl
```

多个明确受测进程可重复传 --pid。使用独立终端保持前台采样，确认首批记录后交IP1开始负载；示例占位符必须替换，不把相同PID误当同一进程。
3. 等IP1报告负载已正常结束，采样仍按约定时长自然结束，保留JSONL及其 `.complete.json`。不要提前kill导致完整性标记缺失。若覆盖不足如实记录，不拼接另一轮资源窗口。
4. 将服务端资源文件和摘要、组配置及原件交IP1；与IP1资源分开保存，不能把跨机PID日志简单合并。注明CPU/RSS覆盖的是哪些进程。
5. 控制面成本：提供本次或此前明确关联的Provider generation_elapsed_ns、成功/失败次数、Trustee请求RTT及响应是否完整、命令到新Helper有效readiness的原件。需要新测首次/重新准入时单独一轮，用现有time-ready-command一次包装明确操作，不在稳态压测中混入。已有健康readiness不算冷启动，Trustee RTT不叫纯验证CPU。
6. 保存本轮summary交IP1，等它收集/汇总后再切下一组/重复。Static mTLS仅成本参照；审计on/off为单独明确轮次，不在同轮中途切换。实际未跑的控制面或规模项保留NOT_RUN。

每个实验结束都要说明当前服务是否可供下一实验使用。P0/E1/E2/E3/E4/E5均保留真实失败和UNKNOWN；不根据客户端HTTP成功或模型回答替代应用接收证据。
