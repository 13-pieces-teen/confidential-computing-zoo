# Argus：生命周期记录与持续 Agent 任务交付

本轮依据作者确认的“服务端管理接口仅供受信管理员使用”边界实施。论文取舍及第四节组织见 [PAPER-ALIGNMENT-1806.md](PAPER-ALIGNMENT-1806.md)。本文件记录本地代码交付，不是论文实验结果；飞书与 Overleaf 未在本轮改写。

## 改动及作用

### 1. 生命周期操作与历史一致性

Docktap 在 `create/start/stop/rm` 转发给 Docker 前，先通过现有受保护的 TruCon 内部通道写入待决操作。完整操作结果必须先保存，才向调用方返回 Docker 响应。后台从保存的结果构造、签名并提交记录，固定操作标识避免重复提交；进程重启只恢复记录提交，不重放 Docker。

未决操作阻止新的 Workload 证明获取可用历史快照，关联记录实际 `CONFIRMED` 后才能解除。查询待决状态与历史快照共享排序锁，避免两次读取之间插入变更。失败 HTTP 响应也记录；不完整响应或未知结果保持待决。

边界：默认 SQLite 位于 `/dev/shm`，覆盖同一 guest boot 内的进程恢复；不保证 guest 重启持久恢复，也未补完 RTMR extend 与数据库提交之间的另一处崩溃窗口。此门禁不阻止直接生成的 Node Quote，不关闭已活跃的代理。未决操作还可能阻塞共享 default chain 上其他实例的新准入，必须报告其可用性成本。

### 2. 首次释放与实际接收

E2 默认探针在现有 TLS 发送路径记录合成事实首次进入传输的时间区间，继续用独立服务端审计判断完整事实是否被实际读取。故障前释放但延迟读取、故障后首次释放并读取、时序缺失或重叠分开统计。重复发送按同一事实去重，发送异常与时钟矛盾保留 UNKNOWN；明确的违规读取不会因其他覆盖缺口被抹去。

E4 使用 Gateway 将输入交给 OpenClaw CLI 的回执，两种释放边界分别标注。计划时刻、排队时刻、发送、服务读取与持久提交不能互换。当前尚无独立完整的实例策略拒绝时间链，“未获准替换实例接收”仍为 UNKNOWN；不得用手填 `eligible=false` 替代证据。

接收/停止条件的 `PASS`、首次释放分类是否确定、实例政策资格是否确定是三个独立结论。即使已有明确读取符合接收条件，首次释放或资格缺证仍可为 UNKNOWN；不得将前者的 PASS 用来替代后两者。

### 3. 小型持续 Agent 工作项

显式选择 `scenario: work-item-v1`，每个客户端运行一个固定行程工作项、六个步骤：预算、步行上限、故障期预算更新、故障期步行更新、恢复后的通行时间上限和室内停靠要求。路线与初始规则存放在私有记忆中，Agent 通过真实 `memory_recall` 与 `memory_store` 回读和更新。配置与执行命令见 [WORK-ITEM.md](WORK-ITEM.md)。

新的推理会话继续同一个业务工作项。恢复先核查原有写入，保留所有已确认事实，即使当步答案错误；未知写入不重发。完整任务成功要求全部必需更新和业务判定满足协议，已确认状态上的部分继续单独报告。未派发、拒绝、未知不能混成“成功阻断”，恢复后的一个成功回答也不能覆盖此前未完成约束。

旧 18 步 `ledger-v1` 保持兼容；配对协议包含场景与步骤数，不能与六步工作项混配。LoCoMo 保留为辅助召回检查，不承担持续任务安全价值的主要证明。

## 验证与执行顺序

本地验证原件位于仓库根 `tmp/argus-implementation-20261002/`：

| 范围 | 结果 | 原件 |
|---|---|---|
| Docktap/TruCon 生命周期与相邻回归 | 141 passed | `delivery-tests.txt`、`lifecycle-delivery.md` |
| 首次释放、默认 E2 收集及相关回归 | 74 passed | `receipt-tests-4.log`、`receipt-delivery.md` |
| 全部实验工具 + remote_acceptance + receiver_audit 合并回归 | 372 passed，37 skipped，8 subtests passed | `combined-tests.log` |
| 六步任务、旧协议、配对分析与 runner 最终回归 | 69 passed | `agent-task-focused.xml`、`agent-task-delivery.md` |

以上测试范围有重叠，不能相加作为独立用例数。Windows 上跳过的平台或工具依赖测试不算通过；包括实际 Linux 文件锁、进程/凭据关联等路径。生命周期测试实际执行 SQLite、HTTP handlers、owner 签名及幂等/确认关联，Docker、RTMR、Rekor 为替身。Agent 测试使用受控 Gateway/记忆替身，未调用真实模型。

最终任务回归还覆盖合并测试启动后完成的迟到确认修复：首次在截止时间后确认的提交不会借用更早的 pending 查询时间冒认按时完成。`node --check continuous_gateway.mjs` 及相关 `git diff --check` 通过。源码清单扩展覆盖 Docktap/TruCon，完成本轮修改后由 `delivery.py create/verify` 重新生成并核验；清单不代表远端二进制已重建。

远端建议依次执行：管理域隔离验收 → 单客户端健康路径 → 生命周期崩溃点 → E2 实际接收 → E3 重新准入 → 六步任务 Full/native 配对 → 成本测量。原始配置、策略、模型、时钟误差和构建摘要一起冻结。

真实 TDX、Docker 进程崩溃、管理面隔离、NGINX 停止时延、模型任务效果与跨机性能均为 **NOT_RUN**。未提交或推送本轮修改。
