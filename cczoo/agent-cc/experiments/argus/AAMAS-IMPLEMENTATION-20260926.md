# Argus AAMAS 方案改造交付

日期：2026-09-26。基线 `8be8afe688ee92956f4f4d036beeb4d4616ee38d`，当前工作区包含此前尚未提交的第 4—7 项实现及本次改造。没有提交、推送或远程部署，原有无关改动保留。

本轮落实最小实验工具、统计和代码交付，沿用现有生产准入、身份、应用和批准策略。`CanReattest=false` 保持；没有增加 `TCB UpToDate` 条件，没有重新引入审计 ACK/业务阻塞、任务授权或通用多机编排服务。

## 代码由什么改成什么

| 项目 | 改造前 | 现在的实现和边界 |
|---|---|---|
| LoCoMo | 只有输入转换，默认顺序取题 | 固定种子按对话/类别取样，真实 Gateway/native SPIFFE 导入、归档/任务续查、逐题新会话、评分；已知任务续查，未知写入与 QA 不重复 |
| 问答污染与注入观测 | 合成事实哈希审计 | 专用评测 Gateway 在登记前关闭自动捕获和模型工具；自然召回块记录哈希、长度与实际输出包含关系，不保存提示原文；语义支持不由哈希证明 |
| E1 | 离线历史规则与散落命令 | `admission_trial.py` 与固定真实配方关联生产 verify、当前 target、nonce、配置/构建与新旧实例；明确 ADMITTED、本地绑定拒绝、UNKNOWN |
| E2 | 指定 bound 的接收判定 | `timeline.py` 汇总故障、readiness 检测代理、systemd 入口停止区间及应用最后读取；客户端保留断流前响应分块；每次 collect 保留输入快照，失败不遗留旧 PASS |
| E3 | 分开的 Node/Workload 快照与恢复检查 | `lifecycle_trial.py` 只读等待自然轮换，关联连续非空业务 trace、身份与时间覆盖；操作者显式执行恢复，复用创建事件流 |
| E4 | 私有记忆正常/权限负例 | 固定 `fleet_fault.py` 观察单 Gateway 停止及共享故障影响；每客户端成功基线、跨故障查询、显式恢复后新会话回答，恢复不覆盖旧失败 |
| E5 | 每请求新建 TLS，默认状态 API | `new/reuse` 实际连接模式、独立 worker/client 连接、TCP/TLS/API 时间、真实非空叶级记忆查询与 Goodput；失败 POST 不自动重放 |
| 实验集成 | private-memory / steady-api | 可选 LoCoMo case 接入现有 runner/step/resume；固定题目与预算、真实业务绑定、输入摘要；不同模式/负载/选题分组和配对，独立业务评分与安全结果 |

真实 Gateway、TDX、systemd、模型与跨机结果尚未运行。这里的“真实”接口说明工具调用路径；本地测试中的外部 Gateway/Docker/systemd fixture 不计远程实测。

## 本地验证

| 检查 | 本次结果 | 说明 |
|---|---:|---|
| 实验工具完整 Python 套件 | **172 PASS / 19 SKIP** | 锁定 sigstore 环境；包含真实 loopback mTLS new/reuse、连接隔离、断流、runner→step→LoCoMo 子进程续查、统计/证据关联；17 项 Linux/root 条件，2 项绘图依赖 |
| Workload Python 回归 | **83 PASS / 11 SKIP** | 恢复/readiness/远程接收判定等；8 项 Linux 条件，3 项 Provider/官方 SPIRE 依赖 |
| OpenClaw Python 回归 | **23 PASS / 4 SKIP** | 未配置上游 tarball、Linux/root、官方 SPIRE/Bash 依赖 |
| Node.js recall 审计 | **3 PASS** | 并发关联、空/失败输出、自然语言实际注入；`locomo_gateway.mjs` 语法检查通过 |
| Matplotlib 科学图 | **4 PASS** | 已有 Anaconda/Matplotlib 3.9.2 环境导出 SVG/PDF，缺数据不画假性能 |

上述套件存在重叠，不合计为单个测试总数。一次额外 WSL 检查使用了默认 Python 3.8，与本次 Python 3.12 测试要求不符而失败；未将其计为 Linux 通过，也未为此更改系统解释器。前次 Linux 验证保留在 [VALIDATION.md](VALIDATION.md) 的历史区，不冒充本次执行。

从仓库根复现本次主要检查（Windows 的临时目录需使用可写 workspace 路径）：

```powershell
.\tmp\argus-extensions-venv\Scripts\python.exe -m pytest cczoo/agent-cc/experiments/argus/tests -q -rs --basetemp tmp/argus-aamas-experiments-final2
python -m pytest cczoo/agent-cc/core/spire/workload/tests -q -rs --basetemp tmp/argus-aamas-workload-final
python -m pytest cczoo/agent-cc/adapters/OpenClaw/spiffe_client/test -q -rs --basetemp tmp/argus-aamas-client-final
node --test cczoo/agent-cc/adapters/OpenClaw/spiffe_client/test/recall.test.mjs
```

`tmp` 中虚拟环境是本机测试环境，不作为源码交付依赖；正式复现环境及锁定依赖见 VALIDATION。源码清单在全部修改完成后重建，远程实际 Linux 二进制和派生镜像仍需各自 build manifest。

## 远程顺序与剩余证据

总入口：[REMOTE-RUNBOOK.md](REMOTE-RUNBOOK.md)。按单客户端随机事实→三个私有客户端→E1/E2→E3/E4/LoCoMo→E5 顺序运行，基础失败时先定位，不展开所有组的笛卡尔积。

- E1：[真实配方](E1-REAL-RUNS.md)。当前 CLI 尚未完整导出原始 Quote、证明请求及签名 EAR；需这些原件的完整实时负例仍未闭环，不能仅凭本地拒绝或离线签名测试称其通过。
- E2：配置 [fault-trial.example.json](examples/fault-trial.example.json) 的真实 SSH、受测组、run ID、时钟不确定性及 timeline 路径；重算使用结果引用的 `collection-*` 输入目录。应用请求读取与客户端响应接收是两个观测方向，不扩大为模型消费或明文擦除。
- E3：[轮换/恢复配方](examples/E3-LIFECYCLE-RECIPE.md)。Node Quote 使用实际 metrics 来源；Workload Quote 缺可靠计数来源时仍为 UNKNOWN。它是采集能力缺口，不能由 SVID serial 推导。
- E4：[固定故障](examples/FLEET-FAULT.md)、[LoCoMo](LOCOMO.md)。每轮采用独立初始用户，未知提交不重放；私有问答验证共享服务，不声称直接协作规划。
- E5：[记忆负载](E5-MEMORY-LOAD.md)。冻结 pilot 后分别测 new/reuse 与 status/memory，再做规模。P1 历史长度/检测参数敏感性待主结果稳定后选代表点。

实时 TDX、远程镜像运行、实际 systemd/NGINX 关闭时间、模型记忆质量与跨机性能均为 **NOT_RUN**。代码和本地检查完成不等于论文证据闭环完成。
