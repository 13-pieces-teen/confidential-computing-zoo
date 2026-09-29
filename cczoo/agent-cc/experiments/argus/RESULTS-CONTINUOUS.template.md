# Argus 双机结果（未运行模板）

状态：NOT_RUN。请只用实际记录填表，保留失败/未知/未运行。

| 版本或条件 | 实际值 / 证据 |
|---|---|
| 两侧完整 Git SHA / patch 摘要 | NOT_RUN |
| Source manifest / build manifest / 实际二进制与镜像 | NOT_RUN |
| 论文口径 / 实现前代码基线 | revision 1329 / 0d78ed0（实际交付 SHA 另填） |
| 实际 policy ID / 原文件摘要 | NOT_RUN |
| 模型 / 模型参数 /插件摘要 | NOT_RUN |
| 实际客户端数 / 各用户 / SPIFFE ID | NOT_RUN |
| 两轮 pilot / 固定调度 / 预算 | NOT_RUN |
| 故障种类、fault_scope、作用实例 / Δ / 时钟不确定度 | NOT_RUN |

| 检查 | 状态 | 原始证据路径 | 结论范围 |
|---|---|---|---|
| 软件测试与回归 | NOT_RUN | | 测试环境，不能替代真实链路 |
| 单客户端真实两工具、初始化与持久依赖 | NOT_RUN | | 无自动召回/捕获，独立新会话 |
| 完整事实读取 / 缺口覆盖 | NOT_RUN | | ASGI 读取 |
| 故障新/旧 socket / 在途载荷 | NOT_RUN | | 实际生命周期 |
| 重新准入/原件/策略/历史复核 | NOT_RUN | | 采集时 Trustee 硬件判断 |
| Node/Workload Quote 与身份更新 | NOT_RUN | | 重启代次分开 |
| 三客户端故障/无故障配对 | NOT_RUN | | 全计划任务分母 |
| 局部 continuous 注入及未注入客户端损失 | NOT_RUN | | 尚未接入；共享服务故障不能替代 |
| 历史共同检查可达性 | NOT_RUN | | 不预设线上消融可比较 |
| current-facts-only / Attest-on-connect / Invocation lease | proposed_not_run | | 尚无在线研究组；不声称复现参考系统 |
| E5 / LoCoMo | NOT_RUN | | 独立协议和统计 |

逐 run 附 offered/attempted/received/committed/recalled、deadline miss、双轴四格与 UNKNOWN/NOT_RUN、重复/唯一完整事实及字节。恢复分段：命令、后端/存储可用、新准入、入口就绪、首次合规读取、首次任务完成。附配对差值、有效块数和缺块原因，不能用一次成功代替正式统计。

| 论文结果槽 | 数值 / 状态 | 所需证据及解释 |
|---|---|---|
| 未准入替换实例读取的唯一新事实 | NOT_RUN | 实际替换/路由、接收者归属、准入区间；缺 admission 只能为 UNKNOWN，不使用停止条件违反次数代填 |
| 超过 Δ 的停止场景读取 / 最后读取 | NOT_RUN | supervision_stop 与 instance_condition_stop 分列；完整窗口、Δ 内数据、时钟误差和覆盖一并提供 |
| 联合任务达成率 | NOT_RUN | 接收 PASS 且 deadline 内任务 PASS / 全部计划任务，未知比例另列 |
| 恢复时间 | NOT_RUN | 从恢复命令至首次合规读取、首次完成任务；未完成/缺证据保留 |
| 局部连带完成率损失（pp） | NOT_RUN | 预声明未注入客户端；(no_fault−fault)×100，共享故障不适用 |
| 稳态任务延迟变化 / 有效任务吞吐 | NOT_RUN | 声明 Full 相对的基线与统计量，同时保留失败、超时和未知 |

E1 另附 [可达性表](E1-REACHABILITY.md)：允许接口与执行者权限、共同检查、实际 namespace/监听路由、拒绝阶段和原件。等效结果和不可达轨迹同样保留；离线诊断不当作在线防御。
