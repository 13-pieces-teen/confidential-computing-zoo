# 飞书 revision 1329：代码与双机 prompt 核对

2026-09-29 读取[飞书论文](https://lcnaletyynmt.feishu.cn/docx/Epqmdt7zHo9tFNxbQ25cCkMenrd)确认 `revision_id=1329`。本次修改代码、实验说明和运行 prompt；没有改写飞书正文。实现前 Git 基线仍为 `0d78ed0c0bf466f0371fd099e1341272daf2dfb7`，本轮及前轮新增内容以实际交付 SHA、工作区补丁和 [source-manifest.json](source-manifest.json) 为准。

审查依据是本轮实际抓取的正文，涵盖威胁模型、JointAdmit、复用条件、E1–E5、摘要结果槽和内部实现状态。文本快照 SHA256：`826095e71165458a9671a0442534123d653e86fd08070da480924a3b2d2f1072`；快照位于本地临时证据目录，不作为远程执行依赖。

## 结论与修改

现有实例准入及入口架构与新版主要描述一致。本次需要修正的是实验归因、结果命名和操作顺序，无须增加新的生产授权层、持续 Quote 或审计门禁。

| 原先情况 | 本轮修改 | 对应文件 |
|---|---|---|
| 共享服务故障的每客户端差值被描述成局部连带损失，fault−control 与论文损失方向相反 | 显式记录 shared_service；保留原始差值，另定义仅适用于事前声明局部未注入客户端的 `(no_fault−fault)×100 pp`；当前局部 continuous 状态仍为 NOT_RUN | continuous、suite、runner、continuous_analysis、analysis |
| 图表把 UNKNOWN 与 NOT_RUN 合并，缺少配对块散点 | 拆分图例并在报告保留两轴未运行数；保存每个配对块差值，展示原始点 | continuous_plot、continuous_analysis、analysis |
| 超 Δ 读取只有笼统 forbidden 名称，容易与未准入替换混同 | 增加停止原因、具体接收实例、事实/字节及最后读取；监督失效和实例条件失效分开，未知事件不直接判违反 | fact_receipts |
| 缺 admission 关联易被解释成确定未准入 | 显式输出 admission_unestablished 候选与 UNKNOWN；完整覆盖及所有读取正面关联准入时才能报该运行的零值，不能据此称已验证替换攻击 | fact_receipts、continuous_analysis、analysis |
| 恢复里程碑仅要求晚于故障，没有统一命令起点 | 以原始 control 的实际恢复命令为起点；排除命令前观测，给出时钟误差区间；恢复后的合法在途任务可以计入首次完成 | fact_receipts、continuous_plot |
| 缺故障前准入对照也可能把旧身份视作恢复，存储探测未关联恢复实例 | 同实例要求可比较的旧 nonce/Helper 记录和实际变化；存储探测核对恢复实例和实际内容摘要；中断运行保留已知接收违反及未知尾部 | fact_receipts |
| 发现实际模型不匹配后，任务成绩仍可进入配对均值 | 保留该次任务原始结果，排除比较估计并记录原因 | continuous_analysis、analysis |
| 固定度量参考仅核对目标/政策 ID | 对原件对照增加实际批准政策字节摘要一致性，防止同 ID 换政策后仍视为公平对照 | history_diagnostics |
| E1 说明还称无原件导出，prompt 仍面向 revision 1251 | 更新独立归档状态；新增允许接口、执行者权限、共同检查、真实 namespace/路由和拒绝阶段记录；E1 与单客户端并行推进 | admission_trial、E1-REAL-RUNS、E1-REACHABILITY、IP1/IP2 prompt |

新判据继续使用实际证据，不新增请求 ACK、任务重放、实例发现服务或通用跨机编排。生产认证、批准政策和入口机制没有因本轮论文对齐而改变。

## 论文中仍未落实的范围

1. **局部持续任务与连带损失。**当前 continuous 支持的三种故障均在共享服务端。旧 fleet_fault 可以验证单 Gateway 停止后的可用性，但使用预写事实，不能代替持续新事实任务。局部 E4 注入、事前轮换对象和相应主结果尚未实现/运行；不得用共享故障填摘要的未注入客户端损失槽。
2. **历史在线增量。**已有生产联合核验、原件导出、离线诊断和真实准入观测。尚未证明共同当前事实检查仍允许而历史政策应拒绝的在线轨迹。先执行 [E1-REACHABILITY.md](E1-REACHABILITY.md)；若共同检查或网络路径已阻断，报告共同保护或离线政策覆盖。current-facts-only 研究组仍是 proposed_not_run。
3. **连接时评估与调用时租约。**新版论文明确列为 proposed_not_run。现有 TLS new/reuse 负载不是 Attest-on-connect，本地凭据 lease 不是 Invocation lease。本轮不将这些模式加入 variants，也不声称复现完整 aDNS/ACLE-MCP。
4. **确定未准入替换实例的非零接收量。**当前 E1 正面观测可确认已准入；ADMITTED/UNKNOWN 或旧绑定拒绝不足以证明另一个实例整个区间未准入。缺此证据时保留候选读取和 UNKNOWN，需要真实拒绝/准入区间材料才能升级结论，不接受手填未准入标志。
5. **同版本实测。**TDX、真实 Agent 工具/记忆效果、关闭时间、恢复、性能和配对结果仍为 NOT_RUN。论文已保留 TBD；本轮不填结果方向或数字。

这些缺口作为明确后续事项保留。本轮 review 不把论文中的计划模式自动扩为新的生产功能。

## 执行与验证

- [IP1 完整 prompt](../../adapters/OpenClaw/spiffe_client/PROMPT-IP1.md)：客户端、Trustee 所在角色、真实任务和证据汇总。
- [IP2 完整 prompt](../../adapters/OpenClaw/spiffe_client/PROMPT-IP2.md)：E1 可达性、服务准入、接收、故障及恢复。
- [结果模板](RESULTS-CONTINUOUS.template.md)：分开摘要结果槽、停止窗口、未准入候选和局部损失。
- [主张—证据表](CLAIMS-EVIDENCE.md)：当前支持范围和待实施范围。

两机先拉取操作者指定的同一交付提交并核对实际摘要。E1 可达性审查与单客户端无故障闭环并行，然后共享故障恢复、三客户端 Full/native × fault/no_fault；E2 消融、E5 和 LoCoMo 独立执行。恢复保留本次卷，跨运行隔离用户和初始数据；未知提交不重放。

本地按用户分工仅做语法/静态检查，并补充远程回归测试材料；不执行单元、集成或硬件测试。最新检查记录写在本节末。远程入口仍为 `remote-software-checks.sh client|server|analysis`，server 已加入 E1 observation 回归。旧交付中的 Go/插件构建结果见 [原建设记录](IMPLEMENTATION-20260929.md)，不能作为本轮行为测试通过。

本轮静态检查：实验目录 69 个 Python 文件 AST、2 份 JSON、远程入口 Bash `-n` 和 Git whitespace 检查通过；11 份操作文档的 46 个本地链接均存在。源码清单在最后刷新并由 `delivery.py verify` 核验。没有重跑此前的 Go/Rust/插件构建，本轮修改集中在 Python 实验分析和文档。

保留 CanReattest=false 及现行批准策略，不增加 TCB UpToDate 准入条件。客户端仍按实际 x509pop 路径报告，不能因两台主机支持 TDX 就写成双方均已完成硬件证明。无关工作区改动保持原样；代码交付版本以 Git 提交及源码清单为准，远程主机测试仍未执行。
