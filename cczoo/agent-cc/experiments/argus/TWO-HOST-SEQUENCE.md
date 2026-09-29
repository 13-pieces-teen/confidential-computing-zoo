# 按实验执行：IP1/IP2 的先后顺序

本表对应论文 revision 1373，只调整执行和记录方式，不改变实验定义。建议顺序为 **P0 准备 → E1 准入 → E2 失效交付 → E3 复用恢复 → E4 Agent 接入 → E5 成本**。E5 的单客户端 pilot 也可在 E3 后先做；首次启动/准入的计时在 P0/E1 顺手采集，E5 引用同一份原件，不重复当成独立样本。

IP1 包括 SPIRE Server/Trustee 所在控制侧及客户端 TDVM；IP2 是服务 TDVM。以实际进程位置为准，不能从 IP1 读取 IP2 的本地 socket。两侧使用同一交付提交，保留正常节点身份与现行政策。

## 怎么发送 prompt

分别把 [IP1](../../adapters/OpenClaw/spiffe_client/PROMPT-IP1.md)、[IP2](../../adapters/OpenClaw/spiffe_client/PROMPT-IP2.md) 发给对应主机的 Codex。两份文件都按 P0、E1—E5 分节。**每次指定一个实验、一个场景/实验组和当前步骤**；完成当前步骤后输出交接信息，等待需要的另一侧结果，不自行把后续实验全跑掉。

例如先给 IP2：

```text
按 PROMPT-IP2.md 执行 E2 的准备步骤。本轮为 full_argus / helper-freeze。
run_id：本轮实际唯一 ID；交付 SHA：两机共同版本。
完成后保存准备记录，告诉我交给 IP1 的路径和参数。先不手动注入故障。
```

把 IP2 的交接信息贴给 IP1：

```text
按 PROMPT-IP1.md 执行同一轮 E2，使用下面的 IP2 准备结果。
完成采集和判定后保存数据，告诉我 IP2 现在应做的收尾/恢复步骤。
```

交接由操作者转贴信息和传输文件即可，不增加协调服务。已配置的 SSH 只供实验工具执行预先明确的远端命令。`READY` 等交接词是人的进度说明，不代替工具输出的真实就绪/接收证据。

## 总顺序

| 阶段 | 先做什么 | 接下来 | 最后做什么 |
|---|---|---|---|
| P0 准备 | 两边并行核对版本、构建；IP2 给实际镜像/Helper 摘要、实例配置和所需身份 | IP1 配好对应 Entries/Trustee 并回交；IP2 正常启动服务、准备普通用户和审计 | IP1 单客户端六阶段 smoke；两边记录部署信息和结果 |
| E1 准入 | IP1 准备 Trustee/Server 记录；IP2 准备 Full 的试验配置与合法实例 | IP2 按配方逐步改变实例，在 before/未登记新实例/重新准入阶段保存观察；IP1 按交接核对访问及关联原件 | IP2 compare、恢复合法实例；IP1 合并报告；之后才开始下一个场景/组 |
| E2 失效交付 | IP2 准备合法实例、独立 collector、路径和 run ID，不执行故障 | IP1 独占协调：新/旧连接基线 → 在途首块 → SSH 故障 → 观测 → 收集 | IP1 通知窗口结束；IP2 保存日志、解除 hold 并显式恢复；IP1 复查访问及汇总 |
| E3-A 普通 Agent 续期 | IP1 启动持续非空记忆 API probe，并确认已进入测量；IP2 采 Provider before | IP1 开 Node observer，等待自然续期并正常结束；IP2 随后采 Provider after | IP1 在快照结束后让 probe 正常完成，导入双机原件 collect；不重启 Agent |
| E3-B Workload 轮换 | IP1 先启动 probe；IP2 在本机启动带 provider_socket 的 Workload observer | 保持目标/Helper 正常，等待自然证书轮换，observer 正常结束 | IP1 probe 覆盖整个窗口，取回 IP2 原件 collect |
| E3-C 已知启动恢复 | 准备真实已知且未完成的 launch ID；IP1 先启动 probe，IP2 先启动 creates observer | IP2 lifecycle observer 取得 before 后，只执行一次 resume-launch，再记录 after | creates/probe 均覆盖完整过程后正常结束，IP1 合并；没有适用未完成操作则记录 NOT_RUN |
| E4 Agent 接入 | IP1 先冻结样本/模型/计划；IP2 按本 run 准备新用户、组和恢复包装 | IP1 配置并登记 Gateway，preflight 后运行单个 run；全部历史初始化结束才进入 QA，控制器按固定时间调用 IP2 包装 | IP2 保存控制/准入/就绪原件；IP1 保存 QA/请求/注入、collect/analyze；再切下一 run |
| E5 成本 | 两边确认组、规模、连接模式、负载及真实 PID；IP2 先启资源采样 | IP1 在采样已开始后运行负载，完成预热和测量；IP2 保持环境不变 | IP2 采样覆盖负载结束并自然写完成标记；IP1 收集每台主机的资源记录及请求结果，逐轮汇总 |

P0 不需要为每个实验从零重装。只更新确实变化的组件、组和实例；修改配置后按正常登记/准入流程启用。一个失败结果也可以完整归档；若后续依赖健康基线，先恢复该基线，而不是重跑到 PASS 或丢掉失败轮次。

## 每个实验完成后保存什么

每个子场景/实验组/重复使用唯一 `run_id` 和新目录；runner 管理的 E4/E5 以 `manifest.json.runs` 中实际生成的 ID 为准，不能另起一个不匹配的 ID。两台主机各有自己的本地证据目录，路径可以不同。

| 实验 | IP1 原件 | IP2 原件 | 汇总要写的数字/判定 |
|---|---|---|---|
| P0 | 版本/构建、客户端配置摘要、六阶段业务结果与权限负例 | 版本/构建、政策/镜像/Helper 摘要、launch/目标/身份/就绪 | 哪条路径已通、哪个阶段失败、未执行项 |
| E1 | 实际 Server/Trustee 材料、按 nonce 关联的导出、阶段访问记录 | before/unregistered-new/after 的 observation、target、comparison、准入原件 | 各案例真实准入/拒绝层、实例变更、共同检查与历史差异可达性 |
| E2 | trace、state、result、assessment、collection 快照和 timeline | 原始 fault JSONL、receiver JSONL、lifecycle、collector 覆盖和恢复记录 | 故障/检测/入口/最后读取时间，新/旧/在途结果；界限后读取与 UNKNOWN |
| E3 | 连续 requests/load-result、Node observer（A）、最终 collect 结果 | Provider before/after；Workload observer（B）；creates/resume 原件（C） | SVID 更新、Quote 尝试/生成/失败、Provider 启动、覆盖与中断、是否重复创建 |
| E4 | fixture checksum、模型/配置、state/result/predictions、请求/注入、时间线 CSV、分析 | 本 run 用户作用域信息、控制回执、实际故障及恢复/准入/就绪日志 | 全计划/尝试/完成/有效完成、失败/超时/未知、请求和任务耗时、实际并发、恢复；F1 辅助 |
| E5 | requests/load-result、客户端资源、冻结负载、逐轮统计 | 服务端资源及 complete 标记、Quote/Trustee/就绪计时原件 | 分层延迟、样本数、Goodput、实际连接复用、错误率、CPU/RSS、独立重复数 |

每个 run 在原始工具输出旁各写一份 `IP1-summary.md` / `IP2-summary.md`，使用 [单轮模板](RESULTS-RUN.template.md)；**不用另造工具结果 JSON**。IP1 接收 IP2 原件后写本轮 `SUMMARY.md`，再更新整批 [RESULTS-FRAMEWORK.template.md](RESULTS-FRAMEWORK.template.md) 对应行。保留原始文件名和相对引用，转移整个工具证据目录，使用 SHA256 核对文件。预测文本留在受保护目录，交接信息不含 key/正文。

每次交接只需这几项：

```text
实验/场景/组/重复/run_id：
当前主机、已完成步骤、实际 UTC 起止时间：
结果：工具原始结果 + 简短解释（未运行项 NOT_RUN，证据缺口 UNKNOWN）
证据目录/需转移的文件及摘要：
当前服务状态：健康 / 故障保持中 / 已恢复 / 未知
下一步由 IP1 或 IP2 执行什么：
```

## 跨机取证的三点细节

1. E2/E4 在测量期间只有 IP1 控制器触发故障。IP2 不再人工执行同一条命令，也不提前恢复；E2 窗口结束后的恢复由明确交接开始，E4 恢复已在固定时间表内。
2. E3 的 Provider before 要早于 observer 的第一份快照，after 要晚于最后一份，均留出实际测得的时钟误差。API 测量还要包住 observer 窗口。提前协商足够的时长，让工具正常结束写出完整结果；窗口不够就保留 UNKNOWN，不拼接多个窗口。
3. E5 两机资源文件分别保存为 `resources-ip1.jsonl`、`resources-ip2.jsonl` 及各自 `.complete.json`，不要拼接：两机 PID 可能同号。当前 analysis 的 `resources.jsonl` 只代表指定的一侧/进程集合；在报告标清范围，另一侧原件和单独统计保留。不能把服务端 CPU 当成整个系统 CPU。资源默认按整个实际采样窗口汇总，写明窗口长度；不把包含预热和收尾的总 CPU 冒称仅测量期 CPU。

完整 CLI 参数仍以 [REMOTE-RUNBOOK.md](REMOTE-RUNBOOK.md) 和各实验配方为准；本页负责顺序和交接。没有执行的真实 TDX、模型、关闭时间及性能保持 NOT_RUN。`CanReattest=false` 和当前批准政策继续沿用。
