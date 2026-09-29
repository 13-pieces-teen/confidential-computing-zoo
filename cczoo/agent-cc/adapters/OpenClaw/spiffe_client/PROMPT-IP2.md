# IP2 执行 Prompt：实例准入、关闭、证明与服务成本

请在 IP2 服务 TDVM 完成本轮 Argus 框架实验，与 IP1 使用相同完整交付 SHA。依据论文 revision 1373，主线为 E1 实例准入、E2 实际交付停止、E3 身份复用/重新准入、E5 成本；E4 用典型 LoCoMo Agent 负载评价接入、性能和恢复。不要将旧累计金额/事务依赖当默认业务，不修改记忆算法。

先读取适用 AGENTS.md，检查工作区，fetch origin 并使用操作者指定 SHA，保留已有改动和持久数据。命令从 cczoo/agent-cc 执行。阅读 experiments/argus/PAPER-ALIGNMENT-1373.md、REMOTE-RUNBOOK.md、VARIANTS.md、E1-REAL-RUNS.md、E1-REACHABILITY.md、ADMISSION-ARCHIVE.md、examples/E3-LIFECYCLE-RECIPE.md 和 adapters/OpenViking/receiver_audit/README.md。实际软件、镜像、政策及摘要以本机检查为准，不照抄旧报告 PASS。

1. 记录 Provider、SPIRE Agent、Helper、NGINX/AuthZ、OpenViking 的 unit/PID、launch/container、监听、镜像/config digest、SVID 和当前策略。保留 CanReattest=false、Node 数据、信任根和批准例外；本项目不要求 TCB UpToDate，不新增门禁。重建本轮 Provider、WorkloadAttestor、Helper 和实验包，运行 bash experiments/argus/remote-software-checks.sh server。Provider 新计时代码本地 Windows 未完成 Rust 构建，本机必须构建并保存输出。
2. 提供 IP1 接入表：可达 HTTPS origin、目标 ID、精确 allowed_client_ids、各普通业务用户/key 安全取用路径、部署/证据目录、源码与政策摘要。多客户端配置不同时保留 client_id。OpenViking 继续用有效 key 决定业务权限；不声称 SPIFFE 身份与 key 一一绑定。先配合 Full 单客户端随机事实和权限负例，再三客户端。
3. 按原批准/TC API launch/register/start/verify 流程准备 Full 隔离部署和锁定审计派生镜像，使用实际镜像摘要。按 receiver 文档为 listener PID 创建受保护实例映射，独立启动 collector，使其位于被测 Helper/NGINX/容器故障范围外。每次与 IP1 同 run_id。审计仍被动，不等 ACK、不参与准入；丢包/崩溃尾部 UNKNOWN，已观测的读取保留。E2 无需收集 LoCoMo 明文或旧 B 类事务清单。
4. E1：开启所需原件导出，保存 nonce 对应 Evidence、Trustee 请求、签名 EAR、实际政策、历史与时间，和 IP1 的 Trustee 导出合并。导出失败不改变准入结果。执行合法准入、停止旧实例、同镜像替换对旧绑定、独立重新准入、无关活动及配置变化。区分本地 checker 拒绝和远端 Verifier 判断。核查真实受控轨迹能否通过共有当前事实检查；Full/native 同时拒绝报告共同保护，离线材料只作诊断，不为制造差异加生产跳过开关。
5. E2：按 IP1 实际 fault-trial 配置提供组内 remote_acceptance、timeline、collector 和部署路径。默认 readiness=transport，新/旧连接各至少三次成功且旧连接未重建；在途实验先确认应用首块读取。真实时钟误差与 Δ 阈值单列，Δ 不是已证明 SLA。依次测 Helper 冻结、退出、目标退出/替换；按需要再测配置/监听实例变化。故障执行前核验 actual variant，暂停自动恢复后保存窗口；显式 release 仅解除本次恢复控制，再按原流程恢复、重新登记/准入。分别保留故障、检测代理、入口状态、socket 和实际应用读取。缺采集不得填零，未准入替换与监督故障后的迟读分别解释。
6. E3：普通 Node SVID 续期保留有效身份，配合 IP1 Server observer，在其观察开始前和结束后采 Provider，留时钟误差余量：

```sh
python3 experiments/argus/lifecycle_evidence.py quote-snapshot   --provider-socket /run/argus/evidence-provider.sock --run-id RUN_ID   --output /secure/evidence/RUN_ID/provider-before.json
# IP1 完成同 run_id 的 Node 观察后，再采 provider-after.json。
```

替换真实 socket 路径。快照的 Agent 必须是 IP1 选定节点，Provider 启动 ID 必须连续；转移两个原件给 IP1 collect --provider-before/--provider-after，不转发 UDS，不把身份更新当 Quote。计数窗口避免混入其他准入。Workload 轮换另用 examples/e3-workload-rotation.example.json 配本地 provider_socket。首次加入仅用真正独立的新节点实例，不删除当前 Node 数据。已知 launch_id 的 resume-launch 配独立 Docker creates observer，确认没有重复创建；首次提交未知不自动再创建。

7. E5 控制面：保存 Provider generation_elapsed_ns 与成功/失败/丢弃生成计数；WorkloadAttestor trustee_request_elapsed_ns / capture 元数据为含网络和响应读取的 RTT，不能叫纯验证 CPU。按 E3 配方用 time-ready-command 包装一次明确的首次启动/重新准入命令，记录命令到随后新 Helper 有效 readiness 的保守时间；未知结果不重放。IP1 做 steady memory API 的 new/reuse 和 1/3 客户端测量，你同步采 CPU/RSS。采集开销用相同派生镜像 audit on/off 独立对照。
8. E4 服务准备：为每个 run 创建全新普通用户和初始空数据，提供 IP1 专属 key；使用同一数据/模型/记忆配置和预算。每次恢复保留本 run 的持久卷。先单客户端无故障，再三个独立 Gateway，最后 Full/native × fault/no_fault。IP1 使用原 LoCoMo 题，不再发送累计金额或路由任务。按共同 QA 起点的固定时间执行已有故障及显式恢复包装；脚本必须保存 run_id、实际故障和恢复/准入/就绪证据。恢复命令结束不代表任务已恢复，IP1 会另记首次成功访问和任务。
9. 对照按目的启用：Full/native 用于准入、E4、成本；−Watchdog/−Close 仅相应 E2；static mTLS 仅成本。每次先停止前组，确认旧入口与注册项不形成旁路，再使用独立组内配置/身份/目录启动。共享 SPIRE Agent 数据继续保留。不得在普通生产配置中加弱化开关。Full/native 保留相同本地监测、入口控制及应用权限，不假称原生基线没有这些保护。

收集源码/构建与运行 manifest、实际政策、准入原件、Provider 快照、fault/timeline/receiver、资源原文件及完成标记、恢复与身份材料。E4 的问答分数或完成不能填 E2 的接收安全结论；共享服务故障不能作为局部连带损失。填写 RESULTS-FRAMEWORK.template.md，列实测 PASS/FAIL/UNKNOWN/NOT_RUN、窗口、样本数、未执行原因和待 IP1 关联项。没有真实运行的 TDX、模型和性能数据保持 NOT_RUN。必要修复限本轮，不自动推送；将 run_id、原件路径和摘要交操作者同步 IP1，不输出秘密。
