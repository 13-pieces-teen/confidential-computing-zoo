# 第 4—7 项交付验证记录（2026-09-26）

基线：`8be8afe688ee92956f4f4d036beeb4d4616ee38d`。本记录描述本地代码与软件测试；真实硬件、远程应用和性能数据尚未产生。下方保留多轮验证记录，当前发布状态以 Git 与最新 review 交付为准；原有无关论文及 `sigstore_baseline.py` 修改保留。

## 最新目录整理与整体 review

本次 review 修复了续查状态丢失、INCOMPLETE 问答统计遗漏、时间线缺口分类、接收时钟域比较、明确连接失败分类及运行产物混入源码清单的问题。结果及发布边界见 [REVIEW-20260926.md](REVIEW-20260926.md)。

最新检查：实验 **186 PASS / 19 SKIP**；Workload/TC API profile/receiver 合约 **97 PASS / 29 SKIP**；客户端 Python **26 PASS / 3 SKIP**；重建插件后的 Node **31 PASS**；Matplotlib **4 PASS**。AuthZ Go 两包通过；Helper Windows 信号差异在当前源码重新编译的 Linux config 测试中通过。Linux Python 与远程硬件仍未补跑。以下为之前各轮建设时的记录，不把其“未提交”状态当作当前 Git 发布状态。

## 最新 AAMAS 改造验证

本次完成 LoCoMo 业务执行、E1 固定真实配方、E2 时间线/分块响应、E3 连续业务关联、E4 固定故障、E5 new/reuse/非空记忆查询及分层统计。完整改动、命令和边界见 [AAMAS 改造交付](AAMAS-IMPLEMENTATION-20260926.md)。

| 当前检查 | 结果 |
|---|---:|
| 完整实验工具（锁定 Python 环境） | **172 PASS / 19 SKIP**：17 项 Linux/root，2 项 Matplotlib 条件 |
| Workload Python | **83 PASS / 11 SKIP**：8 项 Linux，3 项官方 SPIRE/Provider 条件 |
| OpenClaw Python | **23 PASS / 4 SKIP**：上游包/Bash/Linux/root/官方 SPIRE 条件 |
| Node recall 审计 | **3 PASS**，LoCoMo Gateway 语法检查通过 |
| 已有 Matplotlib 3.9.2 环境 | **4 PASS**：包括全套中跳过的真实 SVG/PDF 导出 |

各集合有重叠，不能相加。默认 WSL Python 3.8 的额外尝试不满足测试版本要求而失败，不计为 Linux 验证通过。以下较早 Linux/Go 结果保留为历史记录；本次未重新宣称这些结果代表新增全部代码。远程接受状态继续 NOT_RUN。

## 已交付

- 独立 Gateway 配置、身份、选择器、文件权限和生命周期；精确服务端身份集合；旧单实例配置兼容。
- 多用户六阶段业务验证、实际 API 权限负例、任务续查及未知写入不重放。
- 固定镜像派生构建、ASGI 读取包装、独立非阻塞 Unix datagram 采集、批量持久化与覆盖区间 UNKNOWN；审计不控制业务。
- 五组隔离部署及生效配置检查；静态组双端凭据；实验专用弱化实现不进入普通部署配置。
- 配对实验清单、准备/预检/运行/恢复/收集/分析，真实 mTLS 负载、资源采样、统计 CSV/报告和可重生成 SVG/PDF。
- E1 签名历史诊断，E2 双连接及受限故障工具，E3 轮换/创建事件证据，LoCoMo 派生任务转换器。
- 双机运行步骤、场景清单和论文主张—实验—证据表。

## 较早收缩与回归（历史）

依据[代码收缩计划](../ARGUS-SCOPE-REDUCTION-PLAN-20260926.md)，保留 Node/Workload、身份隔离、watchdog/readiness/入口关闭，简化实验路径：

- 默认 full Argus 单种子功能验证；功能不依赖冻结性能负载，预检只覆盖选中项。
- 安全 GET 测量可以显式新开 attempt；旧窗口不覆盖、不拼接，未知写入/创建/故障不重放。
- 测量日志缓冲写，正常完成后写摘要标记；操作意图仍持久化。错误诊断有上限并脱敏。
- 已安装实验包改用独立 CLI 进程的普通 Python 导入；去掉私有导入图及每次采样全树校验。
- receiver v2 不等待 ACK，缺采集器/队列满不阻断业务；缺口按区间 UNKNOWN。新增真实分块 mTLS，在途首块门禁，以及故障后实际读取判定。

当前回归结果如下；各集合重叠，不合计总数。跳过项不计通过。

| 检查 | 结果 | 范围与限制 |
|---|---:|---|
| 实验工具 Python 全套 | **78 PASS / 18 SKIP** | Windows Python 3.12.7；按场景预检、attempt 恢复、诊断、缓冲日志、在途读取门禁。17 项需 Linux/root，1 项 Matplotlib 未配置 |
| Linux 已安装实验包/故障/生命周期 | **24 PASS** | 普通导入、实际隔离包、保护路径、准确目标；Docker/systemd 使用 fixture |
| Linux receiver | **17 IPC PASS + 2 合同集成 PASS** | 真实内核凭据/队列饱和/控制 socket；Collector 实际输出接入判定器。采集器故障不改变交付；时间窗口使用 fixture，不是派生镜像业务验收 |
| Linux Workload Python 全套 | **84 PASS / 3 SKIP** | 原恢复/readiness 回归、真实本地 mTLS 分块、接收区间判定；官方 SPIRE/Provider 依赖未配置 |

接收判定覆盖了延迟到达的采集记录、落盘序号缺失、未归属进程、时钟回拨、旧实例与替代实例覆盖不能相互抵消。缺口不会被写成零接收；来源明确的越界读取不因其他缺口丢失。真实本地分块测试验证同一 TLS 上多块传输与字节一致，不推导远程 NGINX 缓冲或关闭时间。

源码清单已随本轮重新生成，`delivery.py verify` 与作用范围内 whitespace 检查通过。没有提交、推送或远程部署。

## 收缩前验证记录（历史）

下表保留第 4—7 项上一轮的验证出处。receiver 同步 ACK 和私有导入图已由本轮实现取代，相关旧测试不作为当前行为的验证。

以下集合存在重叠，不合计为一个总数。PASS 是相应软件检查通过，不是远程验收通过。

| 检查 | 结果 | 范围与限制 |
|---|---:|---|
| 实验工具完整 Python 测试，Windows Python 3.12.7 | **67 PASS / 17 SKIP** | runner、配对配置、真实 loopback mTLS、业务回执、离线签名、变体、统计、交付哈希。16 项要求 Linux/root；1 项隔离环境缺 Matplotlib。 |
| 最后回执移植性、采样与源码校验回归 | **18 PASS** | 回执引用相对路径，复制证据目录后仍能验证；原始结果丢失/变化拒绝 PASS。属于上述测试集合。 |
| Linux 已安装实验包加载、受限故障和生命周期 | **23 PASS** | 实际生成 full/no-close/static 包、保护权限和私有导入图；Unix peer credentials、创建事件流、inode/锁。Docker/systemd 故障动作使用受控 fixture。 |
| Linux receiver | **13 PASS** | 真实 Unix IPC/采集子进程、分块、断流、采集故障和未决读取。不是 Docker 镜像业务验收。 |
| Linux Workload Python 全套 | **71 PASS / 3 SKIP** | 包含第 1—3 项恢复/readiness/入口判断回归；未提供构建后的 Provider 与官方 SPIRE 集成依赖。 |
| Linux TC API profile | **10 PASS** | 审计镜像摘要、挂载、环境及签名启动投影；没有启动真实 TDVM。 |
| Linux OpenClaw 客户端全套 | **26 PASS / 1 SKIP** | 固定上游包构建验证；官方 SPIRE 集成未配置。本次 fleet 子集 **12 PASS**，包括真实 UID/GID 降权后拒读另一实例凭据。 |
| Node.js 插件回归 | **27 PASS** | 实际锁定发布包、原生 mTLS、业务流程与注入审计。此处 Node.js 不表示 SPIRE Node attestation。 |
| Linux Go Helper 相关包 | **54 PASS / 1 SKIP** | 配置、AuthZ、Broker、发布、磁盘、watchdog/sidecar；真实本地 mTLS 已运行。缺 NGINX 可执行文件的集成测试跳过。 |
| 科学绘图（独立 Anaconda/Matplotlib 3.9.2 环境） | **2 PASS** | 真实导出 SVG/PDF；无测量时 NOT_RUN，不填示意数字。 |
| 固定 OpenViking 身份契约 | **PASS** | 源码 v0.4.8 / `07113f81e0edaebaacdd23ab138087b06fe871ab`；执行原解析/私有 owner 判断，使用受控 key store/URI fixture，非 HTTP/存储集成。 |
| 静态客户端专用构建 | **PASS** | 独立 Go overlay 的 Linux 二进制交叉编译；生产二进制没有静态开关。 |
| 编译/语法与 Git whitespace | **PASS** | 仅代码静态检查，不扩大为系统行为保证。 |

Go 各包结果与日志位于本工作区 `tmp/argus-extensions-go/`。其他检查的输出在本次执行记录，客户端另有 [验证摘要](../../adapters/OpenClaw/spiffe_client/VALIDATION.md)；源码/包缓存不是测试日志。临时目录不作为提交内容。

## 复现入口

建议 Linux Python 3.12，在隔离环境安装本次测试使用的 `pytest==9.1.1`、`sigstore==3.6.7`。绘图另需 Matplotlib（本次验证 3.9.2）。保留项目自身锁定依赖；不要因此升级生产环境。

下列命令从仓库根运行；Linux root/保护目录测试应在本地测试 VM 或 WSL 执行。Windows 可运行适用子集，Linux/root 项跳过必须另外核对。

```sh
python -m pytest cczoo/agent-cc/experiments/argus/tests -q -rs
python -m unittest discover -s cczoo/agent-cc/adapters/OpenViking/receiver_audit/tests -v
python -m unittest discover -s cczoo/agent-cc/core/spire/workload/tests -v
python -m pytest cczoo/agent-cc/core/tc_api/tests/test_workload_profile.py -q
python -m unittest discover -s cczoo/agent-cc/adapters/OpenClaw/spiffe_client/test -p 'test_*.py' -v
```

客户端完整入口为 `adapters/OpenClaw/spiffe_client/test-client.sh`，会取得锁定上游包并设置插件测试环境。已有缓存可设置 `ARGUS_TEST_UPSTREAM` 和 `ARGUS_TEST_PLUGIN_DIR`。固定 OpenViking 契约入口见 [多客户端验收说明](../../adapters/OpenClaw/spiffe_client/FLEET-ACCEPTANCE.md)。

Go 在 `cczoo/agent-cc/core/spire/helpers/spiffe-helper` 下执行：

```sh
go test -mod=readonly -count=1 ./cmd/argus-agent-config ./cmd/spiffe-authz ./cmd/spiffe-helper/config ./pkg/authz ./pkg/broker ./pkg/clientcredentials ./pkg/disk ./pkg/util ./pkg/sidecar
```

本次 Windows 上交叉编译 Linux 测试二进制后，放在相应包工作目录内由 WSL 执行。官方 SPIRE、Provider 和 NGINX 的跳过项需在依赖齐全的环境另行运行。

## 源码与实际构建分开记录

[source-manifest.json](source-manifest.json) 记录本次交付范围内的源码清单、每文件 SHA 和原始工作区字节 SHA。可移植源码校验仅将 CRLF 转为 LF，其他内容变化会失败；这用于 Windows/Linux Git checkout 的换行差异，不能替代二进制、配置或策略的原始字节校验。

从 `cczoo/agent-cc` 运行：

```sh
python3 experiments/argus/delivery.py verify --manifest experiments/argus/source-manifest.json
```

远程必须由 `core/spire/workload/scripts/build.sh` 生成实际 Linux 构建清单，由 `receiver_audit/build.py` 生成实际派生镜像清单。源代码验证不能令 `payload_ready=false` 的实验包变成可安装包。

## 待远程验收

以下统一为 **NOT_RUN**：完整 Linux 发布构建、审计派生镜像构建/启动、真实 TDX 准入、普通 Agent 续期 Quote 计数、systemd/NGINX 关闭窗口、真实多 Gateway 模型记忆与业务权限、跨机旧/新连接接收量、五组对照及规模/性能测量。按 [双机步骤](REMOTE-RUNBOOK.md) 的单客户端→三客户端→负例/故障→对照→规模顺序运行。

E1 离线诊断仅验证历史子协议，保留外层 Quote/nonce/配置核验。ASGI 审计仅证明应用读取。故障里程碑来自落盘业务证据，不是模型内部同步断点。同容器 restart 同时改变进程/网络/监听，不能标作纯端口消融。直接 Agent–Agent 协作、任务授权和已交付明文撤销不属于本轮。

保留 `CanReattest=false` 及批准策略，不新增 `TCB UpToDate` 准入要求。
