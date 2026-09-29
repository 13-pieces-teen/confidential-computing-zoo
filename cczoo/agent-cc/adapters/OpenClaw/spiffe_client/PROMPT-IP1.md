# IP1 执行 Prompt：持续任务客户端与结果汇总

请在 IP1 及其客户端 TDVM 完成本次 Argus 验证。与 IP2 使用同一交付提交，依据飞书论文 revision 1329，先并行推进 E1 历史判定可达性审查和单客户端持续任务，再做共享故障恢复、三客户端及 Full/native 配对。真实测试由本次远程执行产生，不照抄历史 PASS。

先读取适用 AGENTS.md，检查工作区、fetch 后使用操作者指定的完整 SHA；保留既有改动和部署目录。记录 git SHA、patch 摘要、source-manifest、实际构建和模型配置。阅读 cczoo/agent-cc/experiments/argus/PAPER-ALIGNMENT-1329.md、CONTINUOUS.md、CONTINUOUS-TASK.md、FACT-RECEIPTS.md、ADMISSION-ARCHIVE.md、E1-REACHABILITY.md 和 REMOTE-RUNBOOK.md。命令从 cczoo/agent-cc 运行。0d78ed0 是实现前基线，不代表本轮新增代码；两机以操作者指定的交付版本及实际摘要为准。

1. 核对已有客户端 TDVM、共享 SPIRE Agent/Broker、独立 Gateway 和 publisher。够用时复用；首次创建沿用 DEPLOY-IP1-TDVM.md。原有 x509pop 客户端仍用当前方式，标明客户端节点 TDX 证明 NOT_RUN，不仅凭主机支持 TDX 就改认证路径。保留 Node 数据、信任根和 CanReattest=false。
2. 重建本次定制插件及客户端 Helper，按 deploy/fleet 正常安装和重新登记变化的实例。执行 bash experiments/argus/remote-software-checks.sh client，保存输出、跳过原因和产物摘要。包版本相同不代表代码相同，以摘要判断。
3. 从 IP2 取得实际 HTTPS origin、服务身份、精确客户端 allowlist、普通业务用户和 key 的安全取用位置、策略摘要及 collector/evidence 路径。按实际 Guest/容器网络核对，不把 IP2 localhost 当远端地址。业务不用 root key，秘密不放命令行、Git 或报告。

若 Trustee AS 实际运行在 IP1，按 ADMISSION-ARCHIVE.md 在这里重建/安装本次 AS hook 与 verify_trucon.py，启用受保护的 ARGUS_TRUCON_EVIDENCE_DIR。它与 IP2 插件的 ARGUS_ADMISSION_EVIDENCE_DIR 按 nonce 合并；不要要求 IP2 从不运行 Trustee 的主机获取不存在的导出。实验准备期间按既有流程应用配置，不清理共享节点身份。

与 IP2 并行核查 E1：保存实际批准政策文件、摘要及例外范围；协助原件验证和固定批准度量对照。对每条候选轨迹记录操作者权限、允许接口、共同当前事实检查、历史判断和接收结果。Full 与 guarded native 同时拒绝时，报告共同保护；只有共同检查允许且历史政策不同的真实可达轨迹才支持历史增量。离线删改日志/签名夹具只作政策诊断。

4. 先跑已有单客户端随机事实业务及错误身份/权限负例，检查六阶段原始结果。然后配置 continuous：autoCapture=false、autoRecall=false，允许真实 Agent 的 memory_recall/memory_store；不复用 LoCoMo 只读配置。preflight 核对实际运行配置、用户、服务身份和工具审计。每任务新会话，问题不含期望答案。
5. 首轮 Full 单客户端 no_fault，按 CONTINUOUS-TASK.md prepare/run。核对 A 初始化 archive、非空提取与检索；B 的真实工具调用、request ID、完整事务段、commit/task ID、后继检索及确定性评分。未调用、空提取、改写、超时、未知提交均保留；不能补答案、代替 Agent 调工具或重发不确定写入。
6. 再做 Full 共享服务故障闭环。与 IP2 固定 run_id、一项故障、fault_scope=shared_service、控制 argv、恢复脚本、卷保留及时间同步误差。任务释放、故障、恢复按独立固定时间表执行，不等待任务成功再平移。默认每客户端 18 任务，间隔 60 秒，deadline 120 秒，并发 1、队列 1、重试 0；fault/recovery 第 360/720 秒，Δ=10 秒为研究阈值。中断仅 resume 续查，不重放写入/故障，不重置原点。
7. 单客户端通过后扩到三个独立 Gateway。使用 continuous-suite.example.json 和现有 variants/fleet；先两轮 pilot 再冻结参数。正式用 Full/native × fault/no_fault、十个配对结构 seed；各次运行独立普通用户、初始数据和 secret_seed，同次恢复保留卷。suite 不代为创建用户/部署 Gateway。按 run-order 安装本轮实际配置并登记变化实例后，再执行选定 run_id。

统一入口：

~~~sh
python3 experiments/argus/suite.py --config /secure/continuous-suite.json --output /secure/generated-continuous
python3 experiments/argus/runner.py prepare --config /secure/generated-continuous/suite.json --output /secure/evidence/continuous01
python3 experiments/argus/runner.py preflight --output /secure/evidence/continuous01 --role client --run-id RUN_ID
python3 experiments/argus/runner.py run --output /secure/evidence/continuous01 --role client --run-id RUN_ID
# 中断后只查询原操作：
python3 experiments/argus/runner.py resume --output /secure/evidence/continuous01 --role client --run-id RUN_ID
~~~

把 IP2 collector 原始记录及 fault/admission/recovery 材料按 FACT-RECEIPTS.md 收进 runs/RUN_ID/continuous/，核对 run/实例/hash 后 collect/analyze/plot：

~~~sh
python3 experiments/argus/runner.py collect --output /secure/evidence/continuous01
python3 experiments/argus/runner.py analyze --output /secure/evidence/continuous01
python3 experiments/argus/plot.py --output /secure/evidence/continuous01
~~~

联合评分保留全部计划任务分母、接收×任务四格、两轴 UNKNOWN/NOT_RUN、deadline miss、唯一事实、重复传输和部分帧候选字节。未调用工具不能算成功拦截。分别报告 Full-native、同组 fault-control、共享故障下各客户端变化及恢复分段。论文的局部连带完成率损失定义为 (no_fault−fault)×100 个百分点，仅针对采集前声明的未注入客户端；当前 continuous 服务故障组不能填这一结果。旧 fleet_fault 可单独验证局部可用性，不能冒充持续新事实的局部 E4。恢复从实际恢复命令计时，不能从服务已经就绪时起算。

E2 新/旧 socket 和在途载荷按 fault_trial 独立执行，区分必要监督失效后的停止阈值与未准入替换实例读取。正常缓冲读取、缺准入记录和已证明未准入分别报告；不能将 Helper 冻结称为历史资格失效。E5 与 LoCoMo 后续单独跑，不把 HTTP Goodput 当 Agent 成功率。保留现行批准策略，不增加 TCB UpToDate。current-facts-only、Attest-on-connect、Invocation lease 尚未实现在线组，标 proposed_not_run；普通 new/reuse TLS 测量不等于连接时重新证明，也不称完整 aDNS/ACLE-MCP 复现。没有可达历史差异时保留诊断和等效结果，不临时加入跳过历史开关。

输出 RESULTS-CONTINUOUS.template.md 对应报告、原始证据路径、两机版本、软件检查与远程测量分别的 PASS/FAIL/UNKNOWN/NOT_RUN，以及必要修复 diff。缺 IP2/模型信息先完成独立准备，再列具体缺口。修复限本轮，不放宽判据、不自动推送。将 run_id、关联 ID 和证据路径交操作者同步 IP2，不输出私密事实或密钥。
