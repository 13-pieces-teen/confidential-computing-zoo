# Argus revision 1373 双机结果

- 交付 SHA / 工作区补丁 / source-manifest：
- IP1/IP2 构建与实际镜像、插件、二进制摘要：
- 实际策略文件与摘要 / 例外范围 / CanReattest：
- 服务/客户端身份与实例、Node 认证路径：
- 模型、fixture checksum、样本列表、预算、时钟误差：
- 软件检查：NOT_RUN

| 项目 | 结果 | 原件与 run ID | 失败/缺口 |
|---|---|---|---|
| Full 单客户端随机事实六阶段和权限负例 | NOT_RUN | | |
| E1 合法/替换/重新准入、历史可达性 | NOT_RUN | | |
| E2 新/旧/在途连接与实际读取 | NOT_RUN | | |
| E3 Node 续期、Workload 轮换、Quote 生成 | NOT_RUN | | |
| E3 已知启动恢复与重复创建检查 | NOT_RUN | | |
| E4 LoCoMo 单/三客户端与 Full/native 配对 | NOT_RUN | | |
| E5 控制面、连接、记忆 API 与资源成本 | NOT_RUN | | |

E2 分列故障、readiness、入口、最后读取及覆盖区间；缺失不能填零。E3 分列证书更新、证明接收、生成尝试/成功/失败、Provider 启动 ID与计时口径。E4 分列全计划题数、attempted、完成、期限内注入有效完成、请求拒绝/失败/超时/未知、延迟样本数、实际并发和恢复；F1 为辅助。共享故障不称局部连带损失。E5 各层各连接模式独立，尾延迟附样本数。

配对结果只用完整且协议/模型匹配的独立运行块，保留未配对/失败/UNKNOWN/NOT_RUN。原始 JSONL、运行清单、statistics.csv、analysis.json、report.md 及可重算图表路径：

本轮结论与仍未测试范围：

## 每轮完成后更新的索引

每个子场景/组/重复完成后追加一行，链接按 [RESULTS-RUN.template.md](RESULTS-RUN.template.md) 写好的两侧记录及本轮总结；各实验的原件种类见 [TWO-HOST-SEQUENCE.md](TWO-HOST-SEQUENCE.md)。未配对轮次先记录，待另一半完成再重算汇总，不覆盖已有原始结果。

| run_id | 实验/场景/组/重复 | 原始结果与范围 | IP1 记录 | IP2 记录 | 本轮 SUMMARY / 原件目录 |
|---|---|---|---|---|---|
| 待执行 | | NOT_RUN | | | |
