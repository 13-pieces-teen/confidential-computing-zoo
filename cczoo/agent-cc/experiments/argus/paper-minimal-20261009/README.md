# Argus 两机最小实验材料包

日期：2026-10-09。材料版本：paper-minimal-v1。所有新增实验仍是计划，发布材料不表示远端已执行。

## 读取入口

- 仓库：`https://github.com/13-pieces-teen/confidential-computing-zoo.git`
- 分支：`docs/argus-minimal-experiments-20261009`
- 材料目录：`cczoo/agent-cc/experiments/argus/paper-minimal-20261009/`
- 固定版本：使用用户转交的完整 commit SHA；提交不内嵌自身哈希。按 [GIT-GET.md](GIT-GET.md) 在独立目录读取并校验。

先读 [PLAN.md](PLAN.md)，然后 IP1 读 [IP1-PROMPT.md](IP1-PROMPT.md)，IP2 读 [IP2-PROMPT.md](IP2-PROMPT.md)。两份指令可同时交给两机，IP2 为唯一窗口协调者。

## 本轮范围

1. 优先补取已有 P0、E2、历史规则用例原件。
2. 默认新增一次健康 Workload SVID 轮换与低频记忆访问窗口：IP1 30秒预热+900秒测量，每5秒一次查询；IP2 600秒只读观察。
3. 授权矩阵和一次真实 Agent 召回按已有材料与现成前提决定是否执行。
4. 合并小型证据与独立指标后停止，不自动进入 E4。

该包替代旧 E4 修复/pilot/六轮安排及 controlled-v2 执行指令。其他旧文档作为历史资料保留，不与本包叠加执行。不重跑P0/A3/E2，不新建环境、部署native、注入故障或主动触发准入。

## Git 与版本边界

本次新增内容全部位于本材料目录，只有文档、换行规则与校验清单。分支祖先只是 Git 历史，不是部署建议；不要在运行目录拉取、切换、合并或安装该分支的代码。

材料 SHA 与实际工具/二进制/镜像摘要分别记录。结果继续提交到两机各自既有 results 分支；本 docs 分支不接收实验原件、秘密或运行代码。

`SHA256SUMS` 覆盖其余六个文件。校验成功后即按角色指令继续，无需将普通 read/测量步骤交回用户逐次确认。
