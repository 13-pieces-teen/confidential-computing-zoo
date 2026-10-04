# 图注与正文衔接

下列英文图注可随模板进入投稿稿；中文用于飞书编辑。主文保持原有 6.2–6.5 顺序。实际图号和表号交排版系统编号，A/B 是本包临时标签。

## Table A / 6.2

**English.** Admission decisions across the instance lifecycle. The online comparison holds local controls fixed and reports legal admission, unconfirmed records, and admission after confirmation. Offline cases separately identify the effect of authenticated history rules. A/D/U denotes observed allow, deny, and unknown outcomes.

**中文。** 实例生命周期中的准入判定。在线比较保持相同本地控制，分别报告合法准入、记录未确认和确认后准入；离线案例单独核验经认证历史的政策规则。A/D/U 分别表示实测允许、拒绝与未知。

**正文承接。** 在原有 E1 方法段后引用此表，解释实际出现差异的阶段及其拒绝层。正常 A/C 的业务访问列同时说明受保护路径的可用性。不要把在线门禁差异直接表述成所有历史规则均已证明不可替代。

## Figure A / 6.3

**English.** Receipt control after binding and supervision failures. Panels (a) and (b) show per-run cumulative receipt of distinct facts first released after the fault. Panels (c) and (d) show ingress-closure delay and receipt at the observation end. Each dot is one run; hollow triangles denote right-censored closure times. Gaps indicate incomplete observation coverage.

**中文。** 绑定与监督失效后的接收控制。(a)(b) 为各轮在故障后首次释放的新事实的累计读取量；(c)(d) 为入口关闭时延和观测结束时的读取量。每个点对应一轮，空心三角表示窗口结束时尚未观察到关闭，曲线缺口表示观测覆盖不完整。

**正文承接。** 先说明两项消融分别改变何种机制，再用曲线解释“持续发送—检测—入口关闭—读取停止”的关系。后端存活与观测覆盖由原件支持；以实际窗口中的差异写结论，不把 HTTP 失败直接替代零读取。

## Table B / 6.4

**English.** Cross-session task continuation with Full Argus. Each row is a six-step task. The readback column covers all confirmed original proposals; correct continuation and complete task success are separate outcomes. Access and continuation times share re-admission as their origin.

**中文。** Full Argus 的跨会话任务继续。每行对应一个六步任务，回读列覆盖全部已确认原始提案，分别报告正确继续和完整任务成功。首次合法访问与正确继续耗时均以重新准入为起点。

**正文承接。** 用正常与恢复两条件解释系统接入智能体持续任务的价值。六步逐项展示，让读者看到恢复前后哪些更新已经生效、后续决策如何使用记忆。本表的目标是该场景下的恢复正确性。

## Figure B / 6.5

**English.** Normal-access cost under new and reused TLS connections. Dots are per-run p50 and p95 latencies for expected-allow requests with valid business responses; short horizontal lines show medians across runs. Both modes use the same API, data, permissions, and offered load. The supplementary cost table reports all request outcomes and resource measurements.

**中文。** 新建和复用 TLS 连接下的正常访问成本。每个点为一轮业务有效的正常允许请求的 p50 或 p95，短横线为跨轮中位数。两组使用相同 API、数据、权限和到达负载；请求结果与资源开销见补充成本表。

**正文承接。** 在原有“E5 比较增量成本”段后引用图 B。取得数据后填写每种连接模式的绝对耗时与配对增量，保持端到端请求耗时、准入耗时和模型耗时的来源清晰。

## 两张附录表

S1 保留完整窗口及 Quote/SVID/订阅计数，正文用一句话说明复用行为并引用。S2 保留请求结果、资源和分段成本，人工保持窗口及离线处理各自成行。两表复用现有记录，不要求新增实验或补成整齐的数字矩阵。

## 版面安排

主文 A/B 两表和 A/B 两图均按约 7.05 英寸双栏宽制作，图中实字号约 7–9 pt。图 A 承担核心机制的展示，可保留四面板；图 B 为紧凑双面板。现有基线定义表保持简短；当前成本指标清单改由结果图和附录表承担，避免与正文并排重复。

写作时先点明每幅图解释的机制，再给真实条件、观测和数值。图注保持读图所需的定义；逐次交接、版本排错和工具缺陷留在复现包。
