# 给 IP1：受控实验 v2

本包controlled-v2取代旧的“等管理员/新TDX就绪后再实验”与cwf-bkc条件provision安排。你负责实验控制端、单客户端及独立观测。

1. 按GIT-GET.md导出用户指定固定提交并核验SHA256SUMS；读EXPERIMENT-PLAN.md、BACKEND-CONTRACT.md、figures/data-contract.md。保留当前运行分支和修复。
2. 停止仅等待管理员或重复汇报的循环；保留控制、凭据续期与必要取证。两个故障现场冻结，不启动新TDX guest或重试内核。
3. 在现有普通Linux资源上准备实验SPIRE控制端和身份。实验根、appraiser、端口与状态隔离；verifier为全局配置时使用独立实验Server/Agent。
4. 现有单实验Gateway可在记录配置后复用；各条件保持单客户端。与IP2冻结软件Node bootstrap，两臂一致，不记为TDX证明。旧Gateway/业务现场需要保留时，用独立实验配置，不能覆盖生产配置。
5. 模型沿用SiliconFlow及已选对话/Embedding，key从受保护配置读取；仅模型请求取消公司代理。
6. 核对P0/P0-D、A3归档来源与版本，回填TDX-ARCHIVE-INDEX.json；使用原有效历史窗口，不因证书现在过期而重采。
7. 收到IP2实验后端后配置精确Entries、凭据发布和路由。私钥留guest，保留forced-command通道，由你执行实时探针。
8. 共同完成S1：合法准入/业务访问→真实Helper冻结后的关闭→解冻/必要重新准入→实际记忆回读，另确认失配拒绝。S1不重跑P0、不计正式样本。
9. S1通过后按E1受控轨迹→E5/E3→E2→E4六任务推进；12b365ed适配核验后复用。按批采样READY后由IP2发START，直接用既有受保护目录交接。
10. 你采请求/响应、transport、证书代次、时钟、负载和任务oracle，IP2独立receiver互证应用读取。允许自然语言前导文本，oracle不加入准入链路。
11. 向IP2交数据分片并复核客户端部分；受控与实机归档分开。首份回执是普通Linux/客户端可用性、隔离身份/采样准备及具体依赖；不能再把原平台管理员确认设为总阻塞。

已授权软件范围内持续推进，无需用户逐阶段转贴或确认；真实的新权限/资源缺口集中报告。
