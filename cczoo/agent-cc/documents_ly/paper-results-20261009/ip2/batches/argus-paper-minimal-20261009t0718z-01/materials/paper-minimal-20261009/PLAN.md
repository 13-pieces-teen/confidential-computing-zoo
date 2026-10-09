# Argus：现有两机最小补充实验方案

日期：2026-10-09。状态：执行方案，未在服务器执行，所有新增结果均为 NOT_RUN。

## Git 材料入口

仓库：`https://github.com/13-pieces-teen/confidential-computing-zoo.git`；材料分支：`docs/argus-minimal-experiments-20261009`；仓库内材料目录：`cczoo/agent-cc/experiments/argus/paper-minimal-20261009/`。按用户转交的完整提交 SHA 固定读取，校验 SHA256SUMS；不要只相信不断变化的分支 HEAD。获取方式见同目录 GIT-GET.md。

该分支是文档分发分支。只读取本目录，不 checkout/pull/merge/cherry-pick 到部署工作区，不安装其祖先版本代码；当前二进制、镜像和实验工具继续按实际运行摘要记录。它替代旧补实验指令，不是 controlled-v2 的部署通知。实验结果继续交付到各自既有 results 分支，不写回本 docs 分支。

本方案替代此前“修复 E4 → 健康/故障 pilot → 新六轮任务”的后续安排。旧 P0、A3、E2、E4 原件及判定保留。论文以实例准入、受监督的复用、入口关闭及真实服务集成为主线。

## 1. 工作范围与顺序

| 阶段 | 工作 | IP1 | IP2 | 完成条件 |
|---|---|---|---|---|
| S0 | 复用已有材料、核对当前健康状态 | 找 P0 授权/真实 Agent 召回原件；客户端小探针 | 找 E2 完整窗口、七例离线输入；确认当前目标与只读接口 | 已有材料给路径/摘要，缺失给具体项；当前路径能否测量有明确结论 |
| S1（唯一默认新实验） | 健康状态下正常 Workload SVID 轮换与固定记忆查询 | 900 秒低频查询，保留每次请求 | 600 秒只读 workload-rotation 观察，附已有 Provider 计数 | 分别报告轮换、访问采样、Quote 计数，不以单个 PASS 代替全部结果 |
| S2（条件执行） | 小型 API 授权矩阵 | 仅在 P0 原件不可用时发送固定请求 | 关联现有 NGINX/AuthZ/应用日志 | 有效 key、缺 key、无效 key、伪造用户头四条件，各 3 次 |
| S3（可选，不阻塞封稿） | 一次真实 OpenClaw 新会话召回 | 有已确认合成事实且缺少可用旧案例时，最多一次模型调用 | 关联实际业务请求 | 答案、工具调用、真实服务访问分别报告；不等待“跑到成功” |
| S4 | 合并与交付 | 核对客户端结果及联合总结 | 汇总派生结果与证据索引 | 一套带来源、分母、SHA256 的小型材料包 |

先回收旧原件，不重跑旧实验。S0 材料整理与 S1 准备可并行；S1、S2、S3 的请求窗口依次执行。

环境健康、既有工具兼容时：S0/准备约 10–20 分钟，S1 在线约 16–20 分钟，条件项约 5–15 分钟，整理交付约 10–20 分钟。目标为一次 45–75 分钟工作时段；旧材料查找或权限异常可能增加时间，达到下面的时间上限后交付已取得结果，不展开新排障工程。

## 2. 当前部署与授权边界

- 复用现在健康的 Full 实例、Gateway、业务用户、身份和凭据轮换。先实测当前配置，不能把历史 paper01/full 路径或短证书照搬到 paper02。
- 保留现有模型、Embedding、业务 key 和正常网络设置；本轮不换模型、不重新配置代理、不暴露任何 key。记忆检索仍可能调用现有 Embedding 服务。
- 不新建 VM/epoch/chain，不升级内核，不清状态，不重放旧 INFLIGHT，不做 Docker 生命周期、Helper/Provider/Agent 重启、故障注入、新订阅或新准入。
- 不部署 native，不做 Full/native 比较，不做 new/reuse 双模式、规模测试或历史增长。S1 只使用 reuse。
- 允许在独立实验目录写配置、创建有界采集进程、读取既有日志和计数、发送本方案的低频业务请求。允许小型调用/解析包装，但不得修改生产组件、部署版本或旧验收判据。
- 旧独立采样器仅在确认不被业务依赖后正常停止并封存其产物；不停止身份续期、控制面或现有业务服务。新采集器均设定结束时间，结束后仅清理本批进程。
- 只使用已有获准的文件传输通道；forced-command server-check 不能当作任意 shell/scp。只读访问被拒时记录具体操作和拒绝信息，不绕过。

## 3. 双机协调：一个批次，三个信号

IP2 为唯一协调者，生成一次 batch_id，例如 `argus-paper-minimal-<UTC时间>-01`，S1 run_id 为其加 `-rotation`。双方使用同一个 run_id，公开文件只包含非秘密信息。

1. **RUN_PLAN**：IP2 提供当前 target SPIFFE ID、launch/container/PID/starttime、Helper invocation、Provider startup、实际配置/工具版本、S1 参数及交付目录。IP1 加入客户端身份、业务作用域、端点、动态 credentials_dir 和查询摘要；秘密只留原受保护位置。双方检查相同 run_id/摘要。
2. **CLIENT_ACTIVE**：IP1 先启动有界负载，完成 30 秒预热并确认测量期请求已经写入后，发送实际测量起点和客户端预计终点。IP2 收到后尽快启动观察，原则上在客户端测量开始后 120 秒内启动；依据实际快照耗时确认是否还有完整覆盖余量。
3. **OBSERVATION_FINISHED**：IP2 完成前后快照、600 秒观察及日志收尾后立即发送实际 completed_at_ms。IP1 保持原 900 秒测量至结束，再交付请求原件。若客户端已结束而服务器尚未收尾，照实标记覆盖不足，不改变时间戳或偷偷延长已冻结窗口。

保存一次当前客户端 Guest 与 IP2 的时钟偏差/不确定度测量。现有 collect 没有有符号时钟校正参数，因此传入“绝对偏移加测量误差”的实测保守界，不改原始时间戳，也不照抄示例100ms。客户端窗口须覆盖服务器整个 started_at_ms 到 completed_at_ms，再加该保守界；不是只覆盖中间 600 秒。双方直接交换信号和文件，不需要用户逐步转贴。某个信号超出 10 分钟未到时，检查一次实际落点，然后交付明确状态；不设无限等待循环。

## 4. S1 冻结参数与工具

默认参数是本轮稀疏采样协议，不沿用旧 E4 或旧高频故障观察阈值：

| 参数 | 值 |
|---|---|
| case | workload-rotation（不附加 Node enrollment/renewal 流程） |
| 服务臂/客户端 | 当前 Full / 1 个既有合法客户端 |
| API | 当前已验证的 `/api/v1/search/find`，固定非空查询 |
| 连接模式 | reuse；实际建连、重连及代次照实记录 |
| 请求率 | 0.2 请求/秒，即计划每 5 秒一条 |
| 客户端并发上限 | 2 |
| 请求超时 | 10 秒 |
| 预热/测量 | 30 秒 / 900 秒 |
| IP2 观察期 | 600 秒，前后快照及日志耗时另计 |
| 覆盖判据 | max_probe_gap_ms=10000；记录实际间隔 |
| 正式窗口 | 1 次；不因结果不理想重复到 PASS |

S1 前用相同查询做 3 次小探针，确认合法身份、接口/返回结构与非空结果。没有现成非空记忆时不新增写入/提取流程；跳过 S1 的记忆连续性主张，交付缺项。若部署的证书轮换周期明显超出窗口，也不改 TTL 或主动刷新证书，报告本窗口没有观测到轮换。

可复用工具：

- `cczoo/agent-cc/experiments/argus/load_fleet.py`
- `cczoo/agent-cc/experiments/argus/lifecycle_trial.py`
- `cczoo/agent-cc/experiments/argus/lifecycle_evidence.py`
- `cczoo/agent-cc/experiments/argus/examples/e3-workload-rotation.example.json`

先核对本机实际工具接口。`load_fleet.py` 接收顶层 `instances`/`rate` 等字段；不能把 suite.performance 的嵌套 load/body_files 示例直接当作其配置。实例项应引用现有动态 `credentials_dir`、合法 `client_id`/`server_id`、protected `api_key_file` 和固定 `body_file`，避免使用旧的静态 SVID 文件。

工具入口（参数路径必须由本次 RUN_PLAN 解析，不复制示例部署路径）：

```text
IP1: load_fleet.py --config <本批load配置> --output <全新client目录> --run-id <共同run_id> --clients 1
IP2: lifecycle_trial.py observe --config <本批rotation配置> --output <全新server目录>
IP2: lifecycle_trial.py collect --observation <server目录> --load-result <收到的client/load-result.json> --clock-uncertainty-ms <实测值> --max-probe-gap-ms 10000 --output <本批result.json>
```

IP2 的配置只设置实际 `workload_config` 和 `target_id`，现有 Provider 计数接口可用时加入实际 `provider_socket`。本地快照由 observe 采集，不再同时向 collect 提供另一套 imported Provider 快照。接口缺失则 Quote 指标 UNAVAILABLE/UNKNOWN，其余观察继续；不为采指标重启或升级 Provider。

### S1 必须分别报告的结果

1. **轮换**：target 与 Helper 稳定；至少一个有效 SVID serial 改变。两端快照只能证明至少一次变化，不能假装精确次数。
2. **访问**：实际请求总数、各 HTTP/传输结果、nonempty/empty/unknown、窗口覆盖和最大间隔。`load-result.result`、脚本退出码或 rotation PASS 不能代替 `business_continuity` 及请求原件。
3. **Quote**：同一 Provider 进程/身份/socket的 before/after，计数单调且边界没有未完成生成；分别列 Workload 与 Node 的 attempted/generated/failed 增量。只有 Workload 三项增量均为零，才写“该计数区间没有新的 Workload Quote 调用”。本地Provider前后快照在各自workload快照之后取得，须列出其实际起止时间，不能扩展成覆盖整个900秒负载或全部服务器快照。只有具体轮换事件与请求确实位于共同覆盖区间，才联合表述“轮换和访问复用准入且没有新Quote”；否则保留三项独立观察。缺接口、进程变化、未完成调用或不完整快照都不能填零。
4. **辅助成本**：同一窗口成功请求的 p50/p95 可附样本数，错误单列且保留在总体分母。它是当前固定查询的端到端耗时，包含网络、服务和可能的 Embedding 工作；不是 Argus 相对开销或系统吞吐上限。无需新增 CPU/RSS 采集服务。

零失败只表示有覆盖的计划采样中未失败，不能推断采样间无中断。不把约 180 个计划请求提前填成实测请求数。

## 5. S2 与 S3 的固定条件

S2 仅在 P0 授权原件不能支撑原型配置的相应说明时执行，使用同一合法 SVID，对受保护 `/api/v1/sessions` 验证有效 key 200、缺 key 401、合成无效 key 401，各 3 次；用已验证的身份上下文接口再做 3 次伪造用户头，检查实际 caller context 未变。不得用 `/health` 200 替代业务授权。已有当前有效但不在 allowlist 的实验身份时，可再做 3 次 wrong-SPIFFE；没有则 NOT_RUN，不新增身份或借生产私钥。TLS 握手失败与 AuthZ 403 分开记录。不要调用带 LLM 流程的整套 `fleet_business.py isolation`。

S3 仅当现有真实 Agent 案例不足、当前业务作用域内已存在已确认的合成 fact、现有 OpenClaw CLI/工具路径可用时执行。IP1 先冻结 marker、fact 哈希、独立 session key 和一次调用预算，提示只给 marker、不含答案。按现有召回入口调用真实 OpenClaw，超时上限 180 秒，只调用一次；回答包含完整 fact 即为答案命中，前导句不影响。保存最终用户回答、真实工具调用、transport/server 请求关联。答案命中而工具/路径原件缺失时分开报告，不能据此完整证明受保护召回。

已有整套 `verify_openclaw_plugin_e2e.sh` 会写入/commit、负例并执行旧六阶段验证，不能整套执行来代替 recall-only。允许小型包装既有召回调用，不改旧脚本判据或升级模型。没有已确认事实则 SKIPPED；失败、限流或审计不足就归档，不重新生成事实或反复问到成功。会话自身正常记录属于本次调用，不作为新的记忆提交实验。此结果属于新单次案例，不覆盖旧 E4 的任何判定。

## 6. 原件与最终交付

S0 查找限时 15 分钟并可与准备并行。优先补：P0 的权限负例与真实 Agent 召回；E2 r4 的完整关联窗口、binding、coverage/watermark 和源/裁剪摘要；七例历史规则的可公开构造输入、公开验签材料、验证器版本。找不到就列缺项，不重做对应实验。

每端输出本批目录：`README.md`、`MANIFEST.json`、`SHA256SUMS`、`rotation/`、条件执行的 `authorization/`/`agent-recall/`、`existing-evidence-index.json`。IP2 最后产出 `RESULTS.csv` 和 `SUMMARY.md`，每行含 run_id、指标、值/单位、分母、状态、证据路径、限制。

交付完整小型负载与观察文件（含 requests.jsonl、load-result.json、observation.json、前后快照、所引用的 Helper journal、最终 result），不能只交 PASS 截图或汇总。大日志导出必要连续窗口及覆盖边界，注明原文件和裁剪文件各自摘要，不能裁掉缺口。旧原件按原版本归档，不把不同 epoch 的结果混为一个运行。

秘密留原受保护目录：不上传私钥、API key、OIDC token、owner material、在线数据库；公开配置只保留非秘密引用。沿此前已批准的两机 results 分支提交本批非秘密目录，显式 git add 文件清单，不强推、不 reset、不混入生产代码或全部未提交变更。若远端写入不可用，交付可下载包与 commit/摘要，照实说明未推送。

本地窗口结束即停止本批采样并交付，不以收到对方PASS为停止条件。双方原件齐备后只做一次离线合并，最后双方各回复一次：哪些旧材料已补齐；S1 三项独立结论；S2/S3 执行或跳过原因；数据/分支/commit/摘要；当前服务健康。不再自动进入 E4 或其他实验。
