# Argus 双机论文实验材料包

本包将已确认的实验收敛方案与图表模板合并交付。两台机器使用同一 Git 提交，分别执行 `PROMPT-IP1.md` 和 `PROMPT-IP2.md`。本文档中的“当前环境”由远端执行者实时核验，不能从包的交付推断已就绪。

## 获取与使用

1. 按 `GIT-GET.md` 获取用户指定的完整提交，只导出本目录。
2. 核验 `SHA256SUMS`，读 `EXPERIMENT-PLAN.md`、自己的角色 prompt 和 `figures/data-contract.md`。
3. IP1/IP2 可同时开始独立准备。IP2 负责串行调度；两边通过既有受保护交接路径互换原件。
4. 正式执行顺序：部署就绪 → E5 健康负载 + E3 自然轮换 → E2 两项消融 → E4 恢复任务 → E1 在线等待/确认 → 汇总。
5. IP2 合并唯一结果文件，IP1 核对客户端原件。模板 `figures/results.json` 保持空白，在证据输出目录中保存工作副本。

## 文件用途

| 文件 | 用途 |
|---|---|
| EXPERIMENT-PLAN.md | 实验问题、组数、样本数、执行顺序和最小代码适配 |
| PROMPT-IP1.md | 客户端、控制端、独立观测的完整执行指令 |
| PROMPT-IP2.md | 服务端、执行器适配、调度与汇总的完整执行指令 |
| figures/results.json | 尚未填写的计划槽位 |
| figures/data-contract.md | 指标与原件对应关系 |
| figures/argus-experiment-templates.pdf | 已检查的六页空白预览 |
| figures/render.py | 可在 Linux/Windows 运行的离线重绘入口 |
| figures/latex、figures/assets | 表格 LaTeX、图的 SVG/PNG |
| SHA256SUMS | 本材料目录的文件摘要，不包含摘要文件本身 |

## 本次交付改变什么

本 Git 提交新增材料目录，不修改运行时源码、批准策略、source-manifest、Node 身份或部署文件。E4 两条件 profile、E5 三轮配置是否已部署，由 IP2 核对；尚缺的适配在 IP2 最新运行代码之上完成，并单独交付版本。不得将本材料分支中的旧基线源码部署到实验主机。

计划与执行数量以本目录为准，覆盖旧材料中与它冲突的实验安排；核心安全判据、已有修复和运行状态以实际部署版本为准。本目录不把已有配置缺失或未知状态认定为通过。

图表采用主文两表两图、附录两表：Table A 准入；Figure A 失效后实际接收；Table B 六轮任务；Figure B 正常成本；Table S1 身份复用；Table S2 分段成本。沿用既定轮次，没有额外的图表专用在线实验。

## 离线生成

核验空模板只需 Python 标准库：

```bash
python3 figures/validate_results.py figures/results.json
python3 figures/test_validation.py
```

在单独分析环境中安装 `figures/requirements-render.txt`，再执行：

```bash
python3 figures/render.py --data /absolute/path/to/filled-results.json --out /absolute/path/to/rendered
```

绘图不要与被测主机的正式测量争用资源。已有两个 Python 环境时，可以用 `--table-python /path/to/python` 指定表格渲染环境。生成目录与填写的数据均留在证据/分析目录，不覆盖本材料包。
