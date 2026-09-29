# IP1 执行 Prompt：框架实验、客户端与汇总

请在 IP1（SPIRE Server/客户端控制侧）及现有客户端 TDVM 验证 Argus，与 IP2 使用本轮同一完整提交 SHA。按论文 revision 1373：重点是实例准入、交付停止、身份复用和成本，LoCoMo 用于真实 Agent 接入及性能/故障影响。不要运行默认累计金额/前驱事务实验，不修改记忆提取或排序算法。

先读取适用 AGENTS.md，检查 git status，fetch origin 后使用操作者指定的交付 SHA，保留现有改动及节点数据。命令从 cczoo/agent-cc 执行。阅读 experiments/argus/PAPER-ALIGNMENT-1373.md、REMOTE-RUNBOOK.md、LOCOMO.md、E5-MEMORY-LOAD.md、examples/E3-LIFECYCLE-RECIPE.md 和 CLAIMS-EVIDENCE.md。记录实际源码、patch、source-manifest、构建、镜像与插件摘要；包版本相同不能代替摘要。

1. 复用已有客户端 TDVM、SPIRE Agent/Broker 和独立 Gateway；必要时沿用 DEPLOY-IP1-TDVM.md。保持当前节点证明路径、CanReattest=false、CA 和 Agent 数据。客户端若仍为 x509pop，明确其 Node TDX 未测，不因为物理机支持 TDX 就改认证路径。重建插件及本轮组件，运行 bash experiments/argus/remote-software-checks.sh client 并保存结果。
2. 与 IP2 核对 Guest/容器可达 HTTPS origin、服务 SPIFFE ID、精确 allowlist、普通业务用户及 key 的受保护路径、部署与证据目录、实际政策摘要。不要使用远端 localhost 或 root key。先 Full 单客户端随机事实新会话链路及现有身份/权限负例，检查六阶段和真实注入；再扩三个独立 Gateway，保持私有用户隔离。
3. E1 与 IP2 共同按 E1-REAL-RUNS.md、E1-REACHABILITY.md、ADMISSION-ARCHIVE.md 保存合法实例、旧绑定替换拒绝和重新准入证据。Trustee AS 在 IP1 时，按文档在这里启用可选原件导出并按 nonce 合并 IP2 材料。保存当次实际政策。Full/native 都拒绝属于共同保护；历史机制增量须有通过共有检查的真实可达轨迹，离线删日志不是在线攻击。
4. E2 独立执行：复制 examples/fault-trial.example.json，保持 readiness=transport。配置 IP2 的 SSH alias、实际组内工具路径、run_id、collector 与时钟误差。工具等待新/旧 lane 各三次成功、旧连接未重建；在途请求须已被应用读取首块。不能建立在途场景则该项 NOT_RUN。先 Full 的 Helper 冻结/退出和目标退出，再按问题需要运行 −Watchdog/−Close；不要增加完整交叉矩阵。结果分列实际接收、检测/入口区间、新旧连接、在途与 UNKNOWN。
5. E3 按双机配方运行。Node observer 在持有 SPIRE Server API 的主机；IP2 Provider 在 Node 观察开始前、结束后采相同 run_id 的快照，覆盖窗口并留时钟误差余量。IP1 同时保持非空记忆 API probe，最后 collect 显式传 --provider-before/--provider-after。普通续期不删 Node 数据或强制重注册；没观察到 serial 更新不能宣称续期通过。Workload SVID 轮换另测。身份更新、证明接收样本、真实 Quote 生成分别报告。已知 launch_id 恢复由 IP2执行，保留独立创建事件。
6. E5 先完成单客户端 pilot，复制 examples/suite.performance.example.json（reuse/memory_query，1/3 实例），为每个用户准备真实非空私有记忆查询。冻结到达负载，再做 30 秒预热、120 秒测量、五轮；new/reuse 分开配置。输出实际 TCP/TLS/API、复用比例、HTTP 与非空记忆 Goodput、失败/超时及 CPU/RSS。Quote 生成、Trustee RTT、命令至就绪的控制面计时另列，不用状态 API 替代业务，也不把这些数当 Agent 任务延迟。
7. E4 先单客户端 LoCoMo 无故障，再三个 Gateway：使用固定本地数据 checksum、样本、模型和预算，每轮新普通用户，登记前应用 config/locomo-readonly.fragment.json（autoCapture=false、autoRecall=true、tools.deny=["*"]）。历史会话显式导入；所有初始化完成后才启动 QA 时钟；每题新会话，问题无参考答案。使用 examples/locomo.example.json 的并发/固定释放配置，实际并发由记录验证。
8. 功能跑通后做 Full/native × fault/no_fault，复制 examples/suite.locomo-paired.example.json。locomo_configs[group][seed][condition] 引用每次运行的配置；每次新业务用户，组间固定题目、模型、预算和控制时刻。fault/recovery 使用预先检查过的现有命令包装，按 run_id 写服务端回执；no_fault 使用相同时间、空 argv。正常/故障/恢复阶段都应有计划题，查看生成的 locomo_phase_counts；先小样本 pilot 再冻结正式配对块。共享服务故障影响所有客户端，不称局部连带损失。

统一套件命令（实际配置路径由你填写）：

```sh
python3 experiments/argus/suite.py --config /secure/locomo-paired.json --output /secure/generated-locomo
python3 experiments/argus/runner.py prepare --config /secure/generated-locomo/suite.json --output /secure/evidence/locomo01
# 按 run-order 先部署本 run 的 Gateway 配置并正常准入，再运行：
python3 experiments/argus/runner.py preflight --output /secure/evidence/locomo01 --role client --run-id RUN_ID
python3 experiments/argus/runner.py run --output /secure/evidence/locomo01 --role client --run-id RUN_ID
# 中断只续查既有操作/补审计；不能补发问题、故障或平移时间窗：
python3 experiments/argus/runner.py resume --output /secure/evidence/locomo01 --role client --run-id RUN_ID
python3 experiments/argus/runner.py collect --output /secure/evidence/locomo01
python3 experiments/argus/runner.py analyze --output /secure/evidence/locomo01
python3 experiments/argus/plot.py --output /secure/evidence/locomo01
```

E4 主要报告所有计划题、attempted、完成与期限内注入有效完成、请求耗时和端到端耗时、拒绝/失败/超时/未知、实际调用重叠与恢复后首次访问/任务。F1 只作功能一致性辅助；完成窗口不等于所有题成功，也不等于接收安全。未知初始化写入不重复提交，已知提取任务续查；QA开始后的resume不重放任务。控制失败或模型不匹配保留原结果，但不进入配对估计。

最终填 RESULTS-FRAMEWORK.template.md，保存原始证据、操作/实例关联、软件检查、统计和图表，分别标 PASS/FAIL/UNKNOWN/NOT_RUN。维持现行批准政策，不增加 TCB UpToDate，不加入新历史跳过或调用租约机制。真实 TDX、关闭时间、模型效果和性能以本轮结果为准。环境缺口先完成独立准备，再列具体缺口。必要修复限本轮，不自动推送；把 run_id、摘要和证据路径交操作者同步 IP2，不输出 key 或私密正文。
