# 给 IP1：Argus 论文最小补充实验

## 先读取固定 Git 材料

从 `https://github.com/13-pieces-teen/confidential-computing-zoo.git` 的 `docs/argus-minimal-experiments-20261009` 分支，按用户转交的完整提交 SHA 读取 `cczoo/agent-cc/experiments/argus/paper-minimal-20261009/`。在独立临时目录浅拉并导出材料，先核对提交与 SHA256SUMS，再读 PLAN.md 和本文件；具体步骤见 GIT-GET.md。不得在部署目录执行 pull、checkout、reset、merge 或 cherry-pick；不得为对齐材料提交而部署该分支的代码。

该提交只是实验方案版本，运行代码/工具/镜像仍记录当前实测摘要。用本文件替代旧 E4 补跑 prompt；不要执行仓库其他旧 controlled-v2、pilot 或六轮指令。你的结果仍交付到既有 IP1 results 分支（当前已知为 `codex/argus-results-ip1-20261009`，先核对其用途），不写回本 docs 分支。

请按这份新指令执行，替代此前“修复 E4、健康/故障 pilot、新六轮任务”的后续安排。目标是用当前两机完成一段健康运行观察，补齐已有原件，直接供论文使用。你负责客户端请求、已有 P0 材料和独立核验；IP2 统一协调 run_id、窗口与汇总。双方直接通过既有受保护通道交接，不需每一步等我转贴。

## 范围

保留 P0/A3/E2/E4 原件；不重跑这些阶段，不换 VM/内核/模型，不部署 native，不改策略、身份或业务 key，不清旧链/INFLIGHT。不重启 Agent/Helper/Provider/Gateway，不产生新订阅、新准入或 Docker 生命周期。允许独立目录配置、有界采集和下列低频请求；小型调用包装可做，生产组件及旧验收判据不改。

旧采样器只有确认独立无业务依赖后才正常停止并封存。现有凭据续期、控制面和业务服务保持运行。既有 forced-command 通道不能用于任意命令。秘密不出 Guest、不出现在日志正文、argv 或 Git。

## 1. 先完成两个独立准备

1. 限时约 15 分钟找回 P0 权限负例原件及真实 OpenClaw 召回材料，给出原 run/版本/用户作用域、结果、路径和 SHA256；有材料先复用，不重跑六阶段。和 IP2 对应 E2 原件及旧控制端关联。有缺项列缺项，不无限找日志。
2. 核对当前真正运行的 Gateway、动态 credentials_dir、client/server SPIFFE ID、业务作用域、入口和已确认查询。旧 paper01/full 的短证书与硬编码路径不能照抄。做 3 次固定 memory query 小探针，要求实际 TLS 身份正确、API 结构正确且非空；查询答案不需模型生成。

当前路径不健康时交付具体失败阶段和原件；本方案不扩展为新一轮环境恢复。找旧材料可继续。

## 2. 唯一默认正式窗口：S1 健康轮换

收到 IP2 RUN_PLAN 后对齐一个 batch_id / `-rotation` run_id。双方协议写入同一个小 manifest，记录当前真实版本和配置摘要，不要求 Git HEAD 完全相同，更不盲目 git pull 覆盖运行区。工具字段不兼容时先说明，不自动重新部署。

负载冻结为：1 个既有合法客户端，`connection_mode=reuse`，`workload_kind=memory_query`，固定 `/api/v1/search/find` 查询；rate=0.2 请求/秒，concurrency_per_client=2，timeout_seconds=10，warmup_seconds=30，measurement_seconds=900，frozen=true。计划每 5 秒一条，实际分母从日志统计。不要切 new 模式或加第二组负载。

使用现有 `cczoo/agent-cc/experiments/argus/load_fleet.py`，配置必须符合它的实际顶层 schema（instances/rate 等），不能直接传 suite.performance 的嵌套配置。引用 Guest 内实时动态凭据与 protected API-key 文件，使用现成凭据轮换路径；不导出私钥。只查已存在的记忆，不新增写入/commit/extraction。

你先启动有界采样/负载（独立进程或 transient unit，不能依赖会话持续在线）。30 秒预热结束、测量请求确实落盘后，向 IP2 发送 CLIENT_ACTIVE：run_id、measurement 起点、预计终点、trace 路径/首个请求ID。IP2 在此期间执行600秒观察。你保持原900秒测量到结束，记录 IP2 的 OBSERVATION_FINISHED；不事后更改开始/结束时间。

保存当前客户端Guest与IP2的实测时钟偏差和误差。collect没有有符号offset参数，使用绝对偏移加误差的保守界，不改原件、不照抄100ms。负载需要覆盖 IP2 整个 started_at_ms→completed_at_ms 及该界，含其前后快照和日志耗时。信号超过10分钟未到，检查一次通道实际落点并给出状态，不设无限等待；已运行负载到预定时间自行结束，不等IP2的PASS才停止采集。

## 3. 按需小验证，与 S1 分开

- **S2 授权**：若 P0 原件可用，跳过并引用。否则在与 IP2 确认后，对受保护 sessions API 做正常 key、缺 key、合成无效 key各3次；再在已验证的身份上下文接口用伪造 user header做3次，检查实际 caller仍为本人。已有有效负例SPIFFE身份才附wrong-identity3次，没有则NOT_RUN。不要新建身份，不借生产私钥，不使用整个 `fleet_business.py isolation`，它会连带 LLM。保存状态码、上下文结果和请求关联；TLS失败不等于AuthZ拒绝。
- **S3 真实 Agent 单次召回**：已有可用 P0 真实案例时跳过。否则，只有当前账户下已存在已确认合成fact才执行一次；冻结marker、fact哈希、新session key，提示仅含marker而无答案。调用现有OpenClaw真实agent CLI，timeout上限180秒，一次后停止；答案包含完整fact即命中，允许前导句。保留工具调用与transport/server关联，缺项独立标注。没有现成事实则SKIPPED，不新增写入或等待提取。不要整套执行 `verify_openclaw_plugin_e2e.sh`，允许只包装其中既有召回调用。不改模型、不反复调用直到PASS。

## 4. 判定与交付

将完整 `requests.jsonl`、`load-result.json`、每客户端子目录/连接日志、配置非秘密副本、时钟材料和S2/S3原件交IP2，路径与SHA核验。不能只交汇总。查询可能依赖现有Embedding，不能称完全不依赖模型。

必须看实际请求：总数、success/rejected/timeout/unknown/overload、nonempty/empty/unknown、实际复用/重连。脚本rc0或load-result PASS不等于每个请求都成功。IP2会以 `max_probe_gap_ms=10000` 收集，分别给rotation、business_continuity、Quote结果。Provider计数窗口与900秒负载不是同一窗口；只在实际共同覆盖区间关联请求和具体轮换事件，不能把局部Quote=0扩展到全部请求。辅助p50/p95仅对本窗口成功请求统计并附N，错误保留在总体分母；不是Argus相对开销。

沿此前已批准的IP1 results分支只提交本批非秘密证据目录，显式add清单，不强推、不混入无关修改。没有可用推送通道则先交包并说明。最终与IP2核对一次联合SUMMARY，分别列旧材料补齐、S1三项结果、S2/S3是否执行、Git分支/commit/包摘要、服务健康；结束本批采集，不自动进入E4。
