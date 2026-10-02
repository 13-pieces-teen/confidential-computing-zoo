# Argus 双机结果（revision 1935 精简实验）

- 交付 SHA / 工作区补丁 / source-manifest：
- IP1/IP2 构建与实际镜像、插件、二进制摘要：
- 实际策略文件与摘要 / 例外范围 / CanReattest：
- 服务/客户端身份与实例、Node 认证路径：
- 模型、可核验采样配置/提供方默认项、fixture checksum、样本列表、预算、时钟误差：
- 软件检查：NOT_RUN

| 项目 | 结果 | 原件与 run ID | 失败/缺口 |
|---|---|---|---|
| Full 单客户端随机事实六阶段和权限负例 | NOT_RUN | | |
| E1 合法/待决/替换/重新准入、第一拒绝层与历史可达性 | NOT_RUN | | |
| 生命周期操作进程崩溃、未决准入与恢复提交 | NOT_RUN | | |
| E2 后端独立健康、新/旧/在途连接与实际读取 | NOT_RUN | | |
| E3 Node 续期、Workload 轮换、Quote 生成 | NOT_RUN | | |
| E3 已知启动恢复与重复创建检查 | NOT_RUN | | |
| E3 同实例新订阅、新受控 launch 及新身份业务恢复 | NOT_RUN | | |
| E4 六步持续行程工作项、Full/native 配对 | NOT_RUN | | |
| E5 控制面、连接、记忆 API 与资源成本 | NOT_RUN | | |
| E5 三个冻结历史长度点、失败/容量拒绝 | NOT_RUN | | |
| E5 两服务同链待决：B 新订阅与原连接流量 | NOT_RUN | | |

E2 分列故障、readiness、入口、最后读取及覆盖区间；缺失不能填零。E3 分列证书更新、证明接收、生成尝试/成功/失败、Provider 启动 ID与计时口径。E4 分列全计划题数、attempted、完成、期限内注入有效完成、请求拒绝/失败/超时/未知、延迟样本数、实际并发和恢复；F1 为辅助。共享故障不称局部连带损失。E5 各层各连接模式独立，尾延迟附样本数。

E2 另列故障前释放但故障后读取、故障后首次释放并读取、时序不确定三类；实际释放与接收不能混用，缺少实例资格证据时“未获准实例读取”保持 UNKNOWN。E4 记录稳定 `work_item_id`、六个计划步骤及每个提案的 CONFIRMED/REJECTED/UNKNOWN/NOT_DISPATCHED/INVALID_PERSISTED_INPUT，原始 Proposal 与 Decision 分开。空审计不证明未调用 store；当前没有完整审计结束证明，不产生 NOT_ATTEMPTED 判定。计划、实际派发和工具执行阶段分列。已恢复访问、已确认状态上继续、独立证据支持的合法继续和完整任务正确分开报告；必要更新未知时不能记完整任务 PASS。小样本只报告实际独立运行数，不写成泛化能力结果。

E5 使用实际链记录数、引用字节与 Trustee 拉取字节；在线细分时间按同一个 accepted nonce 关联，缺导出为 UNKNOWN。共享待决保留 A 的真实 mutation 原件，B 的既有连接必须在健康 before 窗口已有成功请求；新订阅超时不直接记为政策拒绝。LoCoMo、静态 mTLS 和多客户端扫描只作为可选补充，另表列出。

部署前提核验：普通业务与外部客户端能否访问管理 API、Docktap/Docker socket；真实权限、挂载和网络原件：

生命周期恢复范围：同 guest boot 内进程重启 / guest 重启；操作效果、持久意图、结果、实际 CONFIRMED 历史关联原件：

配对结果只用完整且协议/模型匹配的独立运行块，保留未配对/失败/UNKNOWN/NOT_RUN。原始 JSONL、运行清单、statistics.csv、analysis.json、report.md 及可重算图表路径：

本轮结论与仍未测试范围：

## 每轮完成后更新的索引

每个子场景/组/重复完成后追加一行，链接按 [RESULTS-RUN.template.md](RESULTS-RUN.template.md) 写好的两侧记录及本轮总结；各实验的原件种类见 [TWO-HOST-SEQUENCE.md](TWO-HOST-SEQUENCE.md)。未配对轮次先记录，待另一半完成再重算汇总，不覆盖已有原始结果。

| run_id | 实验/场景/组/重复 | 原始结果与范围 | IP1 记录 | IP2 记录 | 本轮 SUMMARY / 原件目录 |
|---|---|---|---|---|---|
| 待执行 | | NOT_RUN | | | |
