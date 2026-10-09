# 给 IP2：Argus 论文最小补充实验

## 先读取固定 Git 材料

从 `https://github.com/13-pieces-teen/confidential-computing-zoo.git` 的 `docs/argus-minimal-experiments-20261009` 分支，按用户转交的完整提交 SHA 读取 `cczoo/agent-cc/experiments/argus/paper-minimal-20261009/`。在独立临时目录浅拉并导出材料，先核对提交与 SHA256SUMS，再读 PLAN.md 和本文件；具体步骤见 GIT-GET.md。不得在部署目录执行 pull、checkout、reset、merge 或 cherry-pick；不得为对齐材料提交而部署该分支的代码。

该提交只是实验方案版本，运行代码/工具/镜像仍记录当前实测摘要。用本文件替代旧 E4 补跑 prompt；不要执行仓库其他旧 controlled-v2、pilot 或六轮指令。你的结果仍交付到既有 IP2 results 分支（当前已知为 `codex/argus-results-ip2-20261009`，先核对其用途），不写回本 docs 分支。

请按这份新指令执行，替代此前“修复 E4、健康/故障 pilot、新六轮任务”的后续安排。你是本批唯一协调者，目标为复用已有材料、完成一次健康Workload轮换观察及汇总。IP1负责客户端请求与核验，双方通过已有受保护通道直接协调，不需每一步等我转贴。

## 范围

保留P0/A3/E2/E4与旧INFLIGHT原件。不新建VM/epoch/chain、不升级内核或模型、不改TCB/准入策略、不部署native、不做Docker生命周期、Helper/Provider/Agent重启、故障、新订阅或新准入。不为收集一个缺失指标重新部署组件。允许独立目录配置、有界采集、只读日志/快照和本方案请求；小型调用包装可做，生产组件与旧验收判据不改。

保持当前健康Full实例、业务用户、凭据及轮换。旧独立采样器在确认无业务依赖后才正常停用并封存。秘密、owner material和在线DB不入公开包/Git。forced-command server-check不能用作任意shell/scp，权限拒绝照实报告。

## 1. S0：补旧原件与只读预检，限时约15分钟

与IP1合并已有材料索引，不重跑旧实验。优先导出：

- E2 r4相关receiver连续窗口、binding、coverage/watermark及终结边界、时间校正；分别记录源文件与裁剪文件摘要，保留已有缺口。
- 七个历史规则构造用例的可公开输入、公开验签材料、验证器版本和结果；不导出生产密钥，不把它们称为在线攻击。
- P0权限/Agent召回原件由IP1优先找，你补服务端关联；不存在就列缺项。

确认当前Full target/launch/container/PID+starttime、Helper invocation、Provider startup ID、入口、业务数据作用域、collector及实际工具版本。不凭旧回执认定当前健康。只读Provider counter接口存在才使用；缺失允许只将Quote计数标UNKNOWN/UNAVAILABLE，不阻塞其他可执行观察。

## 2. 发布唯一 RUN_PLAN

生成一次 `batch_id=argus-paper-minimal-<UTC时间>-01`；S1统一run_id为加`-rotation`。用小manifest发送真实target SPIFFE ID、当前绑定/版本/配置摘要、动态端点、目录及以下冻结参数。IP1加入客户端身份、查询摘要、动态credentials位置及业务作用域；秘密不写入manifest。核对相关工具实际接口和源码摘要，不强求整机HEAD相同，不盲目合并/覆盖工作区。

S1参数：workload-rotation；当前Full；一个合法客户端；固定search/find；reuse；0.2req/s；客户端并发2；单请求10秒；预热30秒+测量900秒；IP2观察600秒；collect最大采样间隔10000ms。只跑1个正式窗口。不运行Node enrollment、Node renewal或resume-launch分支，不改变TTL制造轮换。

先与IP1确认三次小查询实测非空。没有现成记忆则跳过相应测试，不新增commit/extraction；业务路径不健康则交具体阻塞和现有原件，不开启环境重建。自然轮换周期若超窗口，照实说明本窗口未观察到轮换，不临时延长或主动刷新。

## 3. S1：收到 CLIENT_ACTIVE 后启动只读观察

IP1先启动负载并完成预热，CLIENT_ACTIVE包含实际measurement起点及预计结束。收到后尽快（原则上120秒内）启动：

`cczoo/agent-cc/experiments/argus/lifecycle_trial.py observe --config <本批配置> --output <全新目录>`

配置以现有 `examples/e3-workload-rotation.example.json` 为基础，填本批run_id、真实target_id、当前安装的workload_config，duration_seconds=600。有现成计数接口才设置provider_socket。不加入node配置；observe已负责本机before/after Provider快照，collect时不能再导入另一套before/after。

保留所有工具生成的原件和窗口内Helper/入口记录。快照/journal各自可能耗时，IP1查询窗口必须覆盖整个started_at_ms到completed_at_ms及跨机时钟保守界，不能只对齐中间600秒。测当前客户端Guest与IP2的时钟偏移；collect没有有符号offset参数，传绝对偏移加测量误差，不改原件或照抄100ms。完成立即发OBSERVATION_FINISHED并停止本批采样，不等对方PASS。接收IP1完整load目录后，用共同run_id和该时钟保守界collect一次，`--max-probe-gap-ms 10000`。以实际时间复核覆盖，缺口保留。

某信号10分钟未到则检查一次真实传输落点并交状态，不无限等待，也不另开新准入“试一下”。若窗口已开始，按期限结束并保留部分数据；不为凑PASS重跑。

## 4. 三个独立结果，禁止相互代替

1. **rotation**：同target、同Helper，至少一个有效服务SVID serial变化。两端快照给变化次数下界；有完整日志才给精确数。
2. **business_continuity**：客户端每个请求的身份、结果、非空记忆、覆盖、最大间隔。collect顶层result只是生命周期判据；load-result PASS/rc0也不代表所有请求成功。必须报告实际分母、失败及空/未知结果。采样全成功不推出采样间零中断。
3. **quote_generated**：同一Provider startup/身份/socket、计数单调、边界无未完成生成，分别报告Workload/Node attempted/generated/failed增量。读取 `quote_generated.workload`，不要误用按工具设计仍UNKNOWN的 `workload_quote_samples`。只有Workload三项都是0才可写该计数区间无新Workload Quote调用；不要用缺日志/证书轮换推导0。本地Provider快照在相应workload快照后采集，实际计数区间不是全部900秒负载或服务器快照区间，须分别列起止；具体轮换事件和请求在共同区间内才可联合表述复用结论。计数缺失、Provider变化或调用未完成时只标该轴UNKNOWN，其他轴照常报告。有额外调用时如实保留并依据已有日志归属，不重启重测。

辅助耗时直接来自同窗口请求，p50/p95附成功样本N，失败保留总体分母；包含API/网络/可能的Embedding，不称系统吞吐上限或Argus相对开销。不新增CPU采样或历史增长任务。

## 5. 条件项，主窗口后依次执行

- S2：P0权限原件够用就引用。否则与IP1执行受保护sessions端点的有效/缺失/合成无效key各3次，以及身份上下文接口的伪造user头3次。已有合法有效负例SPIFFE身份才附wrong-identity；无则NOT_RUN，不新建身份。你关联现有NGINX/AuthZ/应用结果，TLS失败不冒充403拒绝。不要运行带LLM的完整isolation套件。
- S3：缺少可用旧真实Agent案例时，且当前作用域有已确认合成fact，IP1才执行一次180秒上限的新会话召回；提示不含答案。你保留相应真实请求关联。无fact则SKIPPED，失败/限流/审计缺失保留，不新写入、不反复重试。该案例不改写旧E4结论。

## 6. 一次合并交付后停止

保留当前服务健康，结束本批采集。输出README、MANIFEST、SHA256SUMS、RESULTS.csv、SUMMARY及每个已执行阶段的小型完整原件。S1须包含observation、workload/provider before-after、实际引用的Helper journal、客户端requests/load-result/子目录及最终collect结果；不要只交汇总。大原件给必要连续裁剪和覆盖边界，附源/裁剪双摘要。

RESULTS每行记录run_id、指标、值/单位、分母、状态、来源与范围；不同epoch和旧/新实验分开。UNKNOWN不填0，SKIPPED不算失败率。与IP1核对一次联合SUMMARY即可收尾，不需要等待每条辅助指标完美。

沿此前已授权IP2 results分支显式提交本批非秘密文件，不强推、不reset、不混入运行代码/无关修改；推送不可用则先交包和摘要并说明。最终只回：旧材料补齐情况，S1三轴结果，S2/S3执行或跳过，Git分支/commit/包摘要，当前服务状态。不得自动进入E4或其他实验。
