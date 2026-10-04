# Argus 实验图表模板

设计依据：[ACLE-MCP](https://arxiv.org/pdf/2609.02690) 第 5–7 页的 Table 1–3、Figure 2–4；Argus 采用 2026-10-04 收敛方案（飞书 revision 2356）。本包不增加实验组、故障类型或正式轮次。

## 主文只放两表两图

| 本包标签 | 内容 | 读者应从中看出什么 | 放置 |
|---|---|---|---|
| Table A | 准入状态与判定；离线历史规则单列 | 当前事实、记录就绪与历史资格分别参与什么判定 | 6.2，接 E1 案例描述 |
| Figure A | 两类失效的累计新增接收曲线＋逐轮关闭时延 | 主动关闭和外部监督如何改变真实交付 | 6.3，双栏宽主图 |
| Table B | 六轮智能体任务的六步结果、确认提案回读及恢复耗时 | 任务在服务恢复后能否遵守完整确认状态 | 6.4，双栏宽 |
| Figure B | 新建/复用 TLS 的逐轮 p50、p95 | 正常访问的成本与连接模式的关系 | 6.5，双栏宽紧凑图 |
| Table S1 | Quote、订阅与 SVID 的实际计数 | 身份轮换和新证明的分工 | 附录 |
| Table S2 | 有效完成量/失败、资源、关键路径和离线核验成本 | 成本来源及指标口径 | 附录 |

标签由 LaTeX 自动编号，与现有架构图、时序图和基线表一起排版。当前正文的“成本指标”表是测量清单，可在结果齐备后由 Figure B 和 Table S2 取代；基线表保留，写清共同控制与各消融改变的机制。

## 从 ACLE-MCP 借鉴什么

其 Table 1 概括正常业务、安全结果和延迟，Figure 2 用场景矩阵展开差异；Table 2 将移除的机制与受影响场景对应；Figure 4 分开正常耗时和人为等待。Argus 的两张结果表、定向消融曲线和正常访问成本图沿用这些表达职责。[来源：第 5–7 页](https://arxiv.org/pdf/2609.02690#page=5)

Argus 的重点是服务实例的准入、持续交付和恢复。Figure A 因而展示实际读取时间线；不照搬每调用授权的攻击类别或 freshness 扫描。图表不填入 ACLE-MCP 的数值，也不把其不同实现环境的延迟放入同一性能排名。

## 数据填入与重绘

唯一数据入口是 `results.json`。已列出计划槽位，所有实验观察为空；初始模板中的 TBD 是待填写，N/A 表示设计上不适用，两者均不是零或失败。

1. IP2 填 E1、E2 服务端原件及阶段成本，IP1 填任务、业务负载与身份观察；按既有 run/attempt 引用关联。无需额外在线实验。
2. 将槽位的 `status` 改为 `recorded`、`unknown` 或 `not_run`，并填写实际 `run_id`、`evidence_ref` 及已观测字段。`pending` 槽位不得填观测数值。状态与判据详见 `data-contract.md`。
3. 运行 `python validate_results.py results.json`。
4. 运行 `python plot_figures.py --data results.json --out build`（需 matplotlib）。
5. 运行 `python build_tables.py --data results.json --plots build --out .`（需 reportlab、pypdf）。

统一入口为 `python3 render.py --data /path/to/filled-results.json --out /path/to/rendered`，Linux/Windows 均可使用。依赖见 `requirements-render.txt`，应装在独立分析环境。也可用 `--table-python` 指定另一 Python 环境。输出放在本材料目录以外，保留空白模板；生成过程不连接实验主机，也不改原始证据。

输出包括一份六页 PDF、两张图的 SVG/PNG、四张 LaTeX 表、图注和接入片段。PDF 每页是一幅图或一张表，可直接引用指定页；正式表格优先用 LaTeX 源码。`paper-includes.tex` 给出插入宏。

## 展示规则

- E1 的在线三阶段与离线规则核验分开。Full/native 展示实测判定计数和首次拒绝层；预期策略不预填为实测通过。
- E2 计数对象是故障后首次释放且被应用读取的不同合成事实。观测缺口断开曲线；已确认的读取仍显示。关闭未在窗口内发生时显示右删失符号，并列实际观察窗口。
- E4 每行是一轮完整任务。六个步骤用 C/X/R/T/U/N 区分正确、错误、拒绝、超时、未知、未派发；恢复继续与完整任务成功分列。正常条件的重新准入耗时为 N/A。
- E5 每个点是一轮的 p50 或 p95；短横线是同条件的描述性中位数。三轮样本逐点显示，不做平滑分布或置信带。有效返回、拒绝、失败与超时在 Table S2 同时报告。
- 人为 barrier 保持时间在阶段成本中独立成行。离线验证器耗时单列环境。历史 P0/A3 不混入新版本的三轮配对统计。
- 所有数值轴随实测自动取范围；待填模板没有模拟数据线、假柱高、预填百分比或结果排名。

本包是图表与数据接口设计。未修改远端代码或运行实验，未更新飞书正文及既有英文整篇导出包。

PDF 由绘图与排版程序直接生成并逐页检查。LaTeX 表格源码与接入片段已提供；本次内置 LaTeX 编译器报告运行环境缺失（`Unable to find standard directories for platform`），因此未将其标为已编译通过。
