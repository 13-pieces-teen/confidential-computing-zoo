# OpenClaw TDVM 阶段代码验证记录

## 2026-09-25：argus.3 业务阶段与续查增量

本段是当前本地增量验证；以下 2026-09-09 记录作为历史保留。上游包仍为 `2026.6.18`，未修改提取／检索算法。

- Node v22.17.0：完整客户端测试 **27/27 PASS，0 skipped**，包括实际固定发布包的本地 mTLS、租户／actor 请求头、原生叶级读取和空召回审计。随后针对修改的 GET 重试优先拒绝 TLS/身份错误链，6 项业务流程测试复测通过。
- Python 3.12.7：**13 项适用测试 PASS**；官方 Linux SPIRE 与 Linux Bash 进程 fixture 共 2 项因环境条件跳过。新增离线汇总检查：即使传入 exit=0，缺少处理、身份、实际 Gateway 写入或召回证据仍返回 FAIL；完整匹配材料才 PASS。
- checksum 固定上游重构建及字节可重复性通过；Bash 验收入口在 WSL 中 `bash -n` 通过；新增 Node CLI 语法检查通过。WSL 有既有网络模式回退提示，本轮未改变机器配置。
- 最终测试插件 SHA-256：`a50ef97a357fd24f6f3051928cd30f2c49ae5d3ea7fce2cdb5b81c1622825f2e`。完整交付包应重新由 `build_delivery.py` 构建，并以最终交付清单为准。
- 真实 Gateway／模型／OpenViking 提取与新会话召回、远程部署及 TDX 均未在本轮执行。空提取的远程根因尚未确定，不能把上述本地通过写成真实业务修复通过。下一步见 [业务闭环验收](BUSINESS-ACCEPTANCE.md)。

## 2026-09-09 历史记录

执行日期：2026-09-09。对应 `argus.2` 插件与本轮 deployment/lifecycle 代码。下列测试在提交前的工作区执行，部署时须记录实际检出的完整提交 SHA；公司 IP1/IP2 上的部署和业务验收由操作者执行。

## 已执行的软件检查

| 检查 | 实际结果与边界 |
| --- | --- |
| 原生 TLS 与发布包 | **19/19 PASS，0 skipped**；Node v22.17.0，真实 HTTPS/mTLS 测试服务，实际 npm 发布包的 OpenVikingClient/setup，覆盖精确身份、用途/链/过期、轮换、租约失效、活动连接中断、HTTP/跨 origin/重定向拒绝、错误客户端 403；新增并发召回审计关联。测试 CA 不是公司凭据。 |
| 交付/部署/验收判断 | **9/9 PASS**；Python 3.12.7。固定上游摘要及可重复包、生成配置、宽松或冲突 Entry 拒绝、连续轮换/失效轨迹、第二会话答案泄漏与跨 span 审计拒绝。 |
| Workload 策略及现有运行工具 | **6/6 PASS（历史）**；验证当时内置模板与显式批准原文件/摘要的选择、换行变化拒绝及运行工具。当前部署沿用已批准策略，不由此历史测试新增 TCB UpToDate 条件。 |
| Go 凭据交付/Broker | Windows `go test -mod=readonly -count=1 ./pkg/clientcredentials ./pkg/broker` 与 `go vet` PASS；Go 1.26.5。新增真实进程内 gRPC 首快照、空身份和 deadline 测试；Broker 不响应时不再无限续租缓存凭据。 |
| Linux 凭据测试 | Linux amd64 交叉编译后，在 WSL2 内核 6.6.87.2 实际执行全部 clientcredentials 测试 PASS，包括真实 pidfd/实例变更检测、Broker deadline 和清理；不是仅编译通过。 |
| 官方 SPIRE 集成 | **1/1 PASS**，WSL 中实际运行官方 SPIRE 1.15.3。完整交付 Agent 配置 validate、增量 x509pop Server 片段 validate、真实 x509pop 节点注册、指纹 Agent ID、两个 Entry 创建/JSON 回读与审核均通过。运行阶段不加载 Docker attestor，因此不代表 Docker workload SVID 签发已通过。 |
| TDVM profile | **1/1 PASS（含两种 profile）**，Linux stub QEMU argv 回归。OpenClaw 不依赖 Host Docker，不增加 1933/1943 转发；原 OpenViking profile 保留转发。没有启动真实 TDVM。 |
| 脚本 | Bash 语法及内嵌 Node 语法检查通过；Git diff whitespace 检查通过。 |

本轮官方 SPIRE 验证发现并修复了 Broker 与 Workload API socket 同目录的问题。现在分别使用 `/run/spire/openclaw-broker/broker.sock` 与 `/run/spire/openclaw/agent.sock`。

本机 Docker daemon 不可用。WSL 启动报告 mirrored network 配置失败并降为无外部网络，但其 Linux 进程、loopback SPIRE/gRPC 和 pidfd 测试正常完成。未据此改动用户的 WSL/Docker 设置。

## 产物与复现

插件仍固定 `@openviking/openclaw-plugin@2026.6.18`，定制修订为 `argus.2`。上游 SHA512、依赖和版本门槛见 `upstream.lock.json`。本次构建插件 SHA256：

```text
48e6d6385f81c83e6d97164ce86399f0a1e872b0c1aa9005276e7f808d5b18ce
```

完整交付包由 `build_delivery.py` 生成，根目录含 `MANIFEST.json` 和 `SHA256SUMS`。Helper 使用 `GOOS=linux GOARCH=amd64 CGO_ENABLED=0 -trimpath -buildvcs=false` 构建；当前二进制摘要以该清单为准。公司机器解压到新目录后先 `sha256sum -c SHA256SUMS`。

软件测试入口为 `bash test-client.sh`。设置 `ARGUS_TEST_SPIRE_BIN_DIR` 可增加官方 SPIRE Linux 集成测试；未设置时的 skip 不等于集成通过。本次分别在 Windows 和 WSL 执行适用的测试，并用固定上游包启用发布包测试。

## 留给公司环境的实际验收

以下均为 **NOT_RUN**：IP1 新 TDVM 启动、Guest Docker/systemd 部署、真实 Gateway PID 登记及其 Docker selectors/SVID、与 IP2 NGINX/OpenViking 的业务连通、新会话记忆召回及 UNKNOWN 负例、公司 SVID 轮换、进程/网络故障注入与恢复。

OpenClaw 的 Quote/Trustee 远程证明按约定不实施，记为 **NOT_RUN**。IP2 继续保留已有 `OutOfDate` PoC 策略例外及其真实证据边界。第二会话的审计位置是 ContextEngine assemble 返回边界，不能表述为已抓取模型供应商的完整请求。

按 [IP1 / Guest / IP2 操作手册](DEPLOY-IP1-TDVM.md)执行并返回真实证据；不能把本记录中的测试结果替代公司环境 PASS。

## 2026-09-26 多客户端扩展本地验证

本次增加一个共享节点上的独立 Gateway 部署和私有记忆验收，见
[多客户端部署与验收](FLEET-ACCEPTANCE.md)。上面的历史数字和远程边界保持原义。

- Linux / WSL Python 3.12.12 客户端全套：27 项，26 PASS、1 项官方 SPIRE 集成未配置而 skip。
  固定 npm 上游包的完整性与可重复构建检查启用；新增 fleet 12/12 PASS。
- 新增 fleet 用例含真实 Linux UID/GID 降权读文件：本实例 reader group 可读自身模拟凭据，
  读取另一实例凭据得到 PermissionError；其余覆盖配置冲突、选择器交叉匹配、独立 mount、
  单实例停止、共享节点与应用身份区分、实际 key 作用域判定和未知提交不重放。
- Node 原有发布包/原生 mTLS/业务与召回审计回归：27/27 PASS，未改动上游插件版本。
- 锁定 OpenViking 0.4.8 源码 revision `07113f81e0edaebaacdd23ab138087b06fe871ab` 的
  `verify_openviking_contract.py` PASS：运行原始 API key 身份解析和私有 owner 准入判断，
  明确使用受控 key store、排除 OAuth、使用已规范化 URI owner fixture。
  此结果不是部署后的 HTTP/存储集成验收。
- 实验采样器与回执检查 9 项 PASS：实际 loopback mTLS 正常请求、403、错误 peer 在 HTTP 发送前失败、
  超时与过载、多客户端线上的 request ID 与原始记录一致、重复身份/业务 key 拒绝、
  容量不足结构化 NOT_RUN、发布代际与过期租约拒绝、持久化失败不能产生完整测量，
  以及恢复回执必须完成且匹配 operation ID。
- Bash 与 Node 语法检查、Python 编译、Git whitespace 检查通过。

多 Gateway 的真实模型记忆、远程业务用户隔离、真实节点/服务故障和 TDX 仍需远程执行。
`CanReattest=false` 和本项目当前批准策略保持，不新增 `TCB UpToDate` 准入条件。
