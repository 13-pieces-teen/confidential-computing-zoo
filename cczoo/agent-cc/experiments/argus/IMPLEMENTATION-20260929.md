# Argus 持续任务交付记录（2026-09-29）

后续已按飞书 revision 1329 完成代码与 prompt 核对，当前运行口径见 [PAPER-ALIGNMENT-1329.md](PAPER-ALIGNMENT-1329.md)。本文件保留最初 revision 1251 建设及检查记录；局部 continuous 连带损失、在线历史消融和连接/调用模式未据此完成。

依据用户确认的论文 revision 1251 与基线提交 `0d78ed0c0bf466f0371fd099e1341272daf2dfb7`。本轮代码在 `feat/argus-spiffe-v2-val` 工作区完成，未提交、推送或远程执行。源码范围和逐文件摘要由 [source-manifest.json](source-manifest.json) 标识；它记录基线加当前文件，不能只用基线 SHA 指代新增实现。

## 已建设

| 部分 | 当前实现 |
|---|---|
| 持续任务 | 真实 OpenClaw 两工具；A 规则初始化，B 定时释放；三阶段、固定 DAG、新会话；并发/队列/deadline；已知操作续查，未知写入不重放 |
| 工具和业务证据 | AsyncLocalStorage 隔离调用关联、每 HTTP 请求 ID、原始 commit/task/archive、完整事实摘要；stored 不直接当成功；后继从持久记忆取前驱 |
| 接收与连接 | 被动 ASGI 完整 ASCII 帧匹配、部分帧候选、重复/唯一读取、进程归属和覆盖 UNKNOWN；socket 生命周期及新/旧/在途独立载荷 |
| 原件复核 | 默认关闭的插件/Trustee 导出；生产 EAR/历史/实际政策复核；实际准入 observation 关联；固定事前批准度量参考 |
| 生命周期观测 | Provider 每次真实 Quote 尝试/成功/失败计数及启动代次；订阅/SVID 独立元数据；恢复命令、存储、准入、入口、读取、任务分段 |
| 对照与论文输出 | 原 runner 接入 continuous；Full/native × fault/no_fault、同结构独立秘密；全任务分母、双轴四格、同组故障差值、各客户端损失、CSV/Markdown/SVG/PDF |
| 操作交付 | [主运行说明](CONTINUOUS.md)、[任务配置](CONTINUOUS-TASK.md)、[接收说明](FACT-RECEIPTS.md)、[原件说明](ADMISSION-ARCHIVE.md)、[结果模板](RESULTS-CONTINUOUS.template.md)、双机完整 prompt |

独立配置排列不再改变每客户端的随机任务结构；随机值/种子不进入跨运行统计分层协议摘要。故障条件必须和真实 checkpoint 的 event/run/time 关联，缺失/错配不会按无故障给出接收 PASS，也不会进入正式配对统计。所有计划运行仍出现在报告中；没有原生证据的运行保留计划任务数，不伪造事实 ID 或时间。

## 本地检查与远程状态

| 检查 | 状态与范围 |
|---|---|
| Python AST / JSON / Node `--check` / Bash `-n` | 已进行静态语法检查，不执行业务和测试 |
| WorkloadAttestor Go 包与新 EAR 复核 CLI | `go build ./...` 通过；测试源仅编译，未执行 |
| Helper / client credentials Go 构建 | `go build -mod=readonly ./cmd/spiffe-helper ./cmd/spiffe-client-credentials` 通过 |
| 锁定 OpenClaw 上游插件打包 | 离线构建通过；插件 SHA256 `633ec342c89b715d530782c61eac1e2161a863c6f922c1902b9d69dc31a4aaf5`，实际部署仍需重建/核对 |
| Rust Provider / regorus CLI / Trustee AS 构建 | 本机 Windows/默认 WSL 无 Cargo，留待远程构建；没有安装额外工具链 |
| 新测试及原有回归 | **NOT_RUN**；按用户要求由双机执行 |
| 真实 TDX / 实际工具调用 / 模型提取与后继召回 | **NOT_RUN** |
| systemd 停止窗口 / 重新准入 / socket 接收 / 跨机性能 | **NOT_RUN** |

没有用上一版本 PASS 数字证明这次新增代码正确。没有生成模拟论文结果或填充性能图。

## 双机入口

- [IP1 完整 prompt](../../adapters/OpenClaw/spiffe_client/PROMPT-IP1.md)：客户端 TDVM、模型/工具、固定任务、接收证据合并和统计；Trustee 实际在 IP1 时也在该主机开启原件导出。
- [IP2 完整 prompt](../../adapters/OpenClaw/spiffe_client/PROMPT-IP2.md)：服务实例、collector、故障/恢复、真实准入及 Quote 计数。

按单客户端 → 故障恢复 → 三客户端及配对 → E5/LoCoMo 顺序运行。软件检查入口从 `cczoo/agent-cc` 执行：

```sh
bash experiments/argus/remote-software-checks.sh client
bash experiments/argus/remote-software-checks.sh server
bash experiments/argus/remote-software-checks.sh analysis
```

这些脚本会实际运行测试，需在相应远程环境执行。server 入口复用原 build.sh，包含已有回归、二进制构建和官方 SPIRE 校验；analysis 入口包含完整实验测试。依赖沿用仓库锁定版本，缺依赖/跳过项单独报告。

## 保留的边界

没有添加生产跳过历史开关、任务级授权、通用多机编排器或审计 ACK 门禁。current-facts-only 线上研究组要等远程共同检查可达性证据；当前交付诊断及验证路线。硬件原件复核引用采集时生产 Trustee 判断，`offline_dcap` 与 `fresh_admission` 保持 NOT_RUN。

连续主模板一次选择一个现有服务故障；客户端局部故障继续使用 fleet_fault。LoCoMo 保留独立只读方案。接收观察点仍为应用读取。保留 CanReattest=false 及既有批准策略，不增加 TCB UpToDate；无关论文和 sigstore_baseline.py 改动保留。
