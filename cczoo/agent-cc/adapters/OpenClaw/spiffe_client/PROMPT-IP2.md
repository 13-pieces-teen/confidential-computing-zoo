# IP2 执行 Prompt：服务实例、证明与接收证据

请在 IP2 服务 TDVM 完成本次 Argus 验证，与 IP1 使用同一交付提交。目标是历史判定可达性、持续真实 Agent 事务的应用读取、故障停止、显式重新准入和恢复证据。依据飞书论文 revision 1329，全部实测结果由本轮产生，历史结果仅作定位线索。

先读取适用 AGENTS.md，检查工作区、fetch 后使用操作者指定的完整 SHA；保留已有改动和运行目录。阅读 cczoo/agent-cc/experiments/argus/PAPER-ALIGNMENT-1329.md、CONTINUOUS.md、FACT-RECEIPTS.md、ADMISSION-ARCHIVE.md、E1-REACHABILITY.md、REMOTE-RUNBOOK.md 和 VARIANTS.md。命令从 cczoo/agent-cc 运行。0d78ed0 是实现前基线，两机以本轮交付 SHA 和实际文件/产物摘要为准。

1. 记录当前 Provider、SPIRE Agent、Helper、NGINX/AuthZ、OpenViking 的 unit/进程、launch/container ID、监听、镜像/config digest、SVID serial/有效期、实际策略及原文件摘要和例外范围。保留 Node 数据、CA 和 CanReattest=false，不为续期删数据/强制重新注册。记录实际 TCB 状态，当前策略不要求 TCB UpToDate，不增加门禁；不能用默认严格模板替代实测所用政策。
2. 提供 IP1 接入表：从 Guest/容器可达的 HTTPS origin、服务 ID、精确客户端集合、各普通用户/key 安全取用位置、版本、策略摘要及证据目录。多客户端用 allowed_client_ids，不同时保留旧 client_id。实例准入与 OpenViking key 权限分开核对，不声称身份/key 一一绑定。
3. 在隔离目录构建本次生产及 variant payload。执行 bash experiments/argus/remote-software-checks.sh server，生成实际构建 manifest。使用新 receiver 审计派生镜像，按真实摘要走原批准和 launch/register/start/verify；不能借用旧镜像批准值。先 Full，再按 run-order 切 native，每次一组；不遗留弱化 Entry 作为 Full 旁路。
4. 按 FACT-RECEIPTS.md 为实际 listener PID 生成受保护绑定，独立运行 collector，放在 Helper/NGINX/业务容器故障范围外。每轮 run_id 与 IP1 一致。receiver 只记录完整事务摘要、长度、分块、时间及进程实例，不取得 B 类秘密明文清单；请求头只关联。采集缺口保持 UNKNOWN，审计无 ACK 或业务门禁。
5. 首轮单客户端 no_fault 核对真实两项工具 HTTP、归档/提取、后继读取。按 ADMISSION-ARCHIVE.md 启用默认关闭的导出，保留 nonce 对应 Evidence、Trustee 请求、签名 EAR、实际策略/历史、验证时间及实际准入观测。导出失败不改变在线 verdict。生产复核工具验证绑定；硬件判断引用采集时 Trustee，不声称今日重新离线 DCAP。
6. 与 IP1 固定一项故障、fault_scope=shared_service 和恢复 argv。复用 remote_acceptance/fault_fixture，保存故障 checkpoint、暂停自动重启状态及 release 记录。E4 按固定时间执行；E2 双连接/在途另按首块读取门禁，无法形成状态的尝试保留 NOT_RUN。恢复保留本轮卷，依正常后端、登记、准入、入口启动顺序；已知 launch 用 resume-launch，未知首次创建不再 POST。
7. 持续记录恢复命令、后端/存储可用、新实例实际准入、入口就绪、首次合规事实读取、首次任务完成。替换容器后 collector 增加真实目标映射；add-target 不授予 Argus 身份。命令返回 0、有效 EAR 或旧证书存在均不单独等于重新准入完成。将 admission/fault/receiver/context 原件交 IP1 汇总。
8. 从 Provider UDS /ra/v1/quote-counters 采前后快照，保留 provider_instance_id；分别记 Node/Workload attempted/generated/failed、重试生成、SVID 更新和订阅。启动 ID 变化不能直接相减。E3 普通轮换只读观察；首次加入使用独立实验身份，不删除共享节点数据模拟。
9. 单客户端闭环后，为三客户端各次运行提供独立普通用户/初始数据；Full/native 各配 fault/no_fault，十个结构 seed。跨运行隔离，同次恢复保留卷。共享服务/身份服务故障影响所有依赖者，不输出“未注入客户端连带损失”；局部 continuous 注入尚待实现，旧 fleet_fault 的局部可用性单独报告。−Watchdog/−Close 先分别用于对应 E2 机制，有可解释差异再考虑加入 E4。

E1 与首轮单客户端并行准备，按 E1-REACHABILITY.md 保存每一步操作者权限、允许接口和产生的事件。先固定批准度量参考，再核查受控 stop/start 等候选能否通过共同 collector、本地目标绑定及网络隔离；核实旧 NGINX 的目标网络命名空间和实际监听者，新容器不自动继承旧路由。分别记录采集、本地绑定、Trustee、身份签发和入口阶段的拒绝。只有在共同检查接受、历史政策分歧且路由/接收可证实时，才支持额外在线防御；否则保留离线政策覆盖或共同保护结果。当前 current-facts-only、Attest-on-connect、Invocation lease 均为 proposed_not_run，不部署不存在的研究组，不加入生产跳过历史或每请求强制 Quote 开关。

采集结束按 FACT-RECEIPTS.md finalize/收集覆盖区间，输出完整窗口及 Δ=10 秒后读取和最后读取时刻，分别列完整/唯一事实、重复传输、部分帧候选字节、新/旧 socket。明确区分“监督失效后未按研究阈值停止”与“已确认未准入替换实例读取”，不把缺 admission 材料自动判成后者。缺口不算零读取；明确不合规读取不能被另一处缺口抹掉。恢复后重新准入的合法读取与暂停期分开。测量时钟不确定度，不能默认跨机完全同步；实测最大停止延迟不称理论上界。

最终交付版本/构建/配置/策略摘要、身份和业务权限负例、准入与恢复原件、计数快照、collector 原始日志、恢复后服务状态。软件检查与真实 TDX/业务/关闭时间分别写 PASS/FAIL/UNKNOWN/NOT_RUN，使用 RESULTS-CONTINUOUS.template.md；不输出密钥/提示词/事务原文，不自动推送。未取得 IP1 真实业务记录时继续服务准备，不提前宣布端到端通过。
