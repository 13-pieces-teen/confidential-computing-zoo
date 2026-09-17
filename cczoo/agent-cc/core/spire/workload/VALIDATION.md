# Workload Attestation 首轮验证记录

执行日期：2026-09-05 至 2026-09-06（Asia/Shanghai）。实现和 review 的基线为分支 `feat/argus-spiffe-v2-val`、提交 `9ced0e3`；本轮按模块分开提交，没有部署到公司环境。开始时已 fetch，远端与本地基线无差异。

本文保留首轮测试及当时的待验清单。后续公司执行结果见 [2026-09-09 Workload 状态报告](../../../../../documents_ly/argus-openviking-workload-attestation-status-20260909.md)和 [OpenClaw 客户端验收报告](../../../../../documents_ly/argus-openclaw-ip1-tdvm-client-acceptance-20260909.md)；各记录仅对应其注明的提交、环境与时间。

## 本地已执行

本机为 Windows；涉及 UDS、pidfd、POSIX signal 和 NGINX 的测试在 Docker Linux 容器执行。Go 使用 1.26.5，Rust 使用 1.88，TC API 测试使用 Python 3.12 与仓库 requirements。下表中的 PASS 不代表真实硬件证明已验收。

| 范围 | 结果与证据边界 |
|---|---|
| Node Attestor | SDK v1.15.3；全部 Go 测试通过，Linux Agent/Server 插件构建通过。原 challenge、PoP、REPORTDATA 向量及 EAR 拒绝测试保留。 |
| Workload Attestor | 全部 Go 测试通过；Linux 插件构建通过。覆盖 PID reference、未知协议、nonce/实例/镜像/配置/policy 不一致，以及伪造、过期、错误 policy/状态/binding 的 EAR。测试使用本地测试签名密钥与 HTTPS 测试服务。 |
| 共享合同与目标监测 | Go 向量、Linux 受保护登记文件和真实子进程 pidfd 退出测试通过。 |
| Rust Provider | `cargo test --locked --bin argus-tdx-evidence-provider`：11 passed；Linux Provider 二进制构建通过。包含 6 项原 Node 测试、跨语言向量/字段约束、正确 Workload 绑定、请求拒绝、取证中实例替换、TSM 错误。硬件 Quote Source 与运行观察在 handler 测试中被替换。 |
| Trustee 合同 | 2 passed；运行真实 `serde_json_canonicalizer` 与 `regorus 0.10.1`，验证 Go/Rust/Trustee 共用 JCS/SHA-384/64-byte REPORTDATA 向量及 Rego 正负例。未启动生产 Trustee 或 DCAP verifier。 |
| 官方 Helper 与 Broker | 官方 v0.11.0 导入源码的 Linux Go 回归通过；定制身份隔离、完整快照移除、PEM 发布失败、reload 失败、订阅断开、自身/目标失效测试通过。Review 新增 readiness 代次读取、发布期间过期、首次配置合并边界回归通过。Windows 上原上游 2 项 POSIX signal 配置测试失败；Linux 对应用例通过，本轮 Broker 仅支持 Linux。 |
| 实际 NGINX | 在 `nginx:1.28.2` 中运行实际 NGINX、UDS AuthZ 和 TLS 客户端：正确 mTLS 业务请求通过，错误客户端 SPIFFE ID/伪造请求头被拒绝，服务端证书按新代次轮换，非法 reload 配置被拒绝。上游业务是测试 HTTP 服务，证书来自测试 CA。 |
| 官方 SPIRE CLI | 官方 v1.15.3 Agent/Server `-version` 均通过；包 SHA-256 为 `ca1a4d1155317bdd2afc7f36663828a10410c7c840e54725b90b4064b0a301c7`。真实 Server 临时实例执行 Entry create/show；实际返回 JSON 通过严格 selectors、parent ID 和关闭预取审计。生成 Agent 配置通过官方 validate，并装载两个自定义插件的配置验证。该测试未执行 Node join。 |
| 部署脚本 | Python 批准基线、同身份全部 Entry 审计、拒绝 TC API 重定向、readiness 清理/内容检查 5 项通过；官方 CLI 集成 1 项通过。Shell 脚本 `bash -n` 通过。Helper 构建输出版本为 `0.11.0-argus.1`。 |
| TC API 新启动流程 | `test_workload_profile.py` 与 `test_workload_launch_flow.py`：3 passed。真实 workflow/service 保留实际实例结果和既有日志提交调用；外部 Docker、registry、日志传输使用测试替身。 |

完整 Linux 构建与复跑入口为 [scripts/build.sh](scripts/build.sh)。此轮按组件构建和测试，没有把完整安装脚本执行到宿主机或公司主机。

## 提交前 review 与修复

检查范围包括 Node 合同保留、Quote/运行实例绑定、Trustee EAR 校验、静态 Entry 授权、Helper 自身/目标身份隔离、NGINX/AuthZ、TC API 启动及部署交付。

| 级别 | 已修复的问题 | 验证 |
|---|---|---|
| P1 | TC API HTTP 客户端默认跟随重定向，可能向其他地址转发 Bearer token；改为拒绝所有重定向。 | 两个真实本机 HTTP 服务测试：请求收到 302 后失败，重定向目标没有收到认证 header。 |
| P1 | 上层 `target/` 忽略规则排除了新 Go 进程检查包，工作区可构建但默认提交会遗漏源码。 | 加入目录级例外，确认全部 4 个 Go 文件和 Trustee 合同 lockfile 已进入 Git。 |
| P2 | readiness 文件原地覆盖会被读取到中间状态；停服清理时 `is_file` 与读取之间也存在竞态。 | 完整临时文件 rename 发布；旧文件读者保持旧代次，Python 对删除/空内容返回未就绪。 |
| P2 | 证书在 NGINX 发布 hook 运行期间过期后，仍可能被标记为就绪。 | 发布使用凭据有效期作为 deadline，hook 返回后再次检查；过期测试确认 readiness 与 PEM 均清除。 |
| P2 | 外部设置 `CARGO_TARGET_DIR` 时，构建命令与 Provider 打包路径不一致，可能打包旧二进制。 | Provider 和 Trustee 合同各使用构建输出内的固定 Cargo 目录；复制路径对应 Provider 的同一目录。Shell 语法检查通过，完整 release 构建留给 Linux 构建入口。 |
| P2 | 生命周期验收在 kill 命令返回后才计时，低估实际停服延迟。 | 将计时移到触发操作之前；实际 systemd 停服时限仍由公司环境验收。 |
| P2 | HCL 合并在首次增加 experimental 块时未限制字段，可能误改 Broker 之外的配置。 | 首次与重复配置共用限制；两种原始配置都拒绝无关 experimental 字段的测试通过。 |
| P2 | Windows `core.autocrlf` 使 Git 归档中的 shell 脚本变成 CRLF，Linux 因 shebang 包含 CR 而无法执行。 | 在 SPIRE 目录明确 `*.sh text eol=lf`；Git blob、Windows 归档和 Linux 可执行脚本保持相同 LF。 |

从 Git 已提交源码生成归档，在临时 Linux 容器使用 Go 1.26.5 重新构建并执行四个模块的 `go test -mod=readonly -count=1 ./...` 与 `go vet -mod=readonly ./...`，全部通过。该步骤先复现 Helper shell 的 CRLF 失败，固定 LF 后再次从 Git 归档执行 Helper 全量测试与静态检查通过。改动过的 Broker、HCL 合并和 Python 工具分别复跑回归，官方 SPIRE 配置与 Entry 集成再次通过。所有提交通过 `git diff --cached --check`；导入上游文件的空白规范化记录在 Helper `UPSTREAM.md`，未把开发机二进制或测试凭据加入提交。

### 启动流程

`test_workload_profile.py` 检查实际 image config digest、launch/container 关联、隔离网络与只读配置。`test_workload_launch_flow.py` 执行真实 TC API launch workflow 和 launch service，仅替换 Docker、registry、日志提交等外部边界；检查 profile 进入最终启动命令、实例结果保留实际摘要、原日志提交被调用、拒绝 dockercmd 覆盖。它不表示真实 Rekor 上传或 OpenViking 容器启动已验证。

### 既有 TC API 回归失败

扩大回归运行 `test_subprocess_unit.py`、`test_tdx_mr_adapter.py`、新增 profile 测试，得到 **7 failed、25 passed**。随后通过 `git archive HEAD` 提取未修改的 `9ced0e3`，用相同 Python 3.12/依赖重跑前两个文件，得到 **相同 7 failed、23 passed**。新增 profile 两项均通过。

原始提交已经失败的测试：

- `test_generate_sbom_missing_syft`、`test_generate_sbom_timeout`：旧调用缺少 `luks_path` 参数。
- `test_build_result_shows_failed_when_build_step_fails`：结果查询返回 422，原测试期待 200。
- `test_encrypt_image_prefers_docker_daemon_transport`、`test_encrypt_image_falls_back_to_docker_archive_on_transport_error`、`test_encrypt_image_logs_invalid_public_key_before_skopeo`：旧调用缺少 `luks_path`。
- `test_get_pubkey_from_kbs_derives_public_key_from_key_pem`：旧调用将 DummyTlog 传入路径位置，返回 False。

这些现有 build/encryption/KBS API 与测试的偏差未纳入本次 Workload 改造，也未把整个 TC API 测试集标为通过。

## Evidence Provider 接口命名统一（2026-09-06）

两个 UDS 接口统一为 `POST /ra/v1/node-evidence` 与 `POST /ra/v1/workload-evidence`，不保留无版本别名。Provider 路由、NodeAttestor 客户端、现有测试及当前运行文档已同步；部署脚本只配置 socket，构建入口同时构建 Provider 与 Node 插件。

复核确认，旧 Node 路径仅保留在 404 拒绝测试和明确标注的历史归档中。构建、安装、配置合并和 systemd 链路无旧路径引用；运行手册补充了安装新 Node 插件后通过 `render` 更新 `plugin_checksum` 的要求。`start` 已在启动服务前调用 `render`，本次未修改部署脚本。

本次在 Windows / Go 1.26.5 下，NodeAttestor 与 WorkloadAttestor 两个模块的 `go test -mod=readonly -count=1 ./...`、`go vet -mod=readonly ./...` 均通过。Node 请求测试通过 `NewClient` 创建客户端，检查实际使用的版本化路径及原请求内容。

Rust Provider 测试已更新为使用新 Node 路径，并检查旧 Node 路径及未配置的 Workload 路径返回 404；本次未执行这些 Rust 测试：本机无可用 Cargo，Docker daemon 无法连接，WSL 不可访问。上方此前的 Rust/Linux 通过记录不代表本次路由改动已复验。Linux 环境需运行 `cargo test --locked --bin argus-tdx-evidence-provider` 或完整构建入口后，再执行公司环境验收。

## Node Attestation PR #364 同步（2026-09-13）

在 `feat/argus-spiffe-v2-val` 的 `1e2247d` 基础上，迁入已合并
[PR #364](https://github.com/intel/confidential-computing-zoo/pull/364) 的 review 修订。
上游 `5e51ac9` 与原 PR 最终提交 `59e9c37` 的文件树一致。

同步范围包括配置化 Agent ID、Rust/Go 共享身份和 REPORTDATA 向量、Server
trust domain 校验、appraisal 与 AgentAttributes 的身份一致性、Provider 更名、
Node 插件 Intel module/Proto 路径和许可证，以及配置与部署文档。
SPIRE SDK 已是 v1.15.3；本轮仅清除 go.sum 中旧 v1.15.2 的两项记录。

Provider 现在使用 `argus-spire-evidence-provider`，必须传入 `--agent-id`。
保留 `/ra/v1/node-evidence`、`/ra/v1/workload-evidence`、Workload 取证及 32 KiB
请求体限制。现有 Workload 部署显式使用
`spiffe://argus.local/spire/agent/argus_tdx/openviking-node`；启用 Workload 登记时，
Provider 在启动前拒绝另一 Node 身份。测试也确认原身份的 REPORTDATA 字节不变。
systemd unit 仍为 `argus-tdx-provider.service`，启动检查同时识别旧、新 Provider 进程。

| 状态 | 本轮检查 | 结果与边界 |
|------|----------|------------|
| PASS | NodeAttestor Go | Windows Go 1.26.5：`go test -mod=readonly -count=1 ./...`、`go vet -mod=readonly ./...`、`go mod verify` 通过；6 个测试包。 |
| PASS | WorkloadAttestor 与 Workload Go | 两个模块的 `go test -mod=readonly -count=1 ./...` 和 `go vet -mod=readonly ./...` 通过；Windows 不执行 Linux `/proc` 测试。 |
| PASS | Linux Go 平台回归 | Windows 交叉编译后在 WSL Ubuntu-20.04 实际执行 6 个包、22 个顶层测试，0 skipped：Node Agent proof-key 权限、Workload protocol/target 的 pidfd 退出与 root-owned registration 检查，以及 WorkloadAttestor evidence/Trustee/plugin 回归。 |
| PASS | Rust Provider | Rust 1.88.0 / Windows GNU：`cargo test --locked --bin argus-spire-evidence-provider`，14 passed；包括共享身份向量、原部署绑定不变、配置不一致拒绝和既有 Workload handler 回归。Quote Source 与运行观察由测试替身提供。 |
| PASS | Argus 完整目标编译检查 | `cargo check --locked --all-targets` 通过，Cargo.lock 未改变；包含既有未使用代码警告。该 Windows 检查不编译 `cfg(unix)` 路径。 |
| PASS | Python 部署回归 | `python -m unittest discover -s cczoo/agent-cc/core/spire/workload/tests -v`：8 passed、2 skipped；覆盖仅安装旧 Provider 的拒绝及两种残留进程阻止启动。 |
| PASS | Shell、格式和文档 | build/install/Node operator 脚本 `bash -n` 通过；Go/Rust 格式、文档本地链接及 SVG XML 检查通过。 |
| NOT_RUN | Linux Rust Provider UDS 集成 | 已加入使用实际 systemd `ExecStart` 的测试，验证必填 Agent ID、两条版本化路由及旧路由 404，并接入 `scripts/build.sh`；本机 WSL 无 Rust/C 工具链，本轮未执行。 |
| NOT_RUN | 官方 SPIRE CLI 集成及真实 TDX 验收 | Python 官方 SPIRE 用例本轮缺少构建产物而跳过；未执行真实 Quote、Trustee appraisal、Node join/re-attestation、Agent/Workload SVID 签发或公司部署。历史验证记录不替代本轮验收。 |

升级时同时安装新 Provider 与 Node 插件，更新 unit 并重新执行 `workload.py render`
以刷新插件校验摘要。Linux 构建与集成验证入口仍为 [scripts/build.sh](scripts/build.sh)；
真实环境验收沿用下列步骤。

## 迁移后链路 review 与部署修复（2026-09-13）

在迁移提交 `b7ff67d2940fd18b1f420a73b4ac76c01a295b32` 上复核身份绑定、
Node/Workload 接口和部署衔接。未发现迁移引入的协议回归；Rust/Go 身份校验、
LP16/SHA-384 REPORTDATA、Trustee appraisal 输入与最终 AgentAttributes 使用同一身份，
原 Workload 身份保持不变。PoP、expiry、EAR 和请求大小限制保留。

发现并修复一项 P2 升级安装问题：构建失败后旧 `SHA256SUMS` 仍可能有效，
安装脚本会复制旧 Provider，却安装引用新 Provider 名称的 systemd unit，
最终输出 `INSTALL=PASS`，而 `ExecStart` 指向不存在的文件。
通过实际 build/install 脚本和隔离的安装目录复现了这一组合。

修复后，构建开始即废弃旧成功清单，所有检查成功后原子发布新清单；安装在任何
主机修改前，校验 [完整产物列表](scripts/build-artifacts.sh) 中的 12 个可执行文件
及其哈希，包括新 Provider 和官方 SPIRE Agent/Server，并只安装列表中的文件。
升级需要完整重建；旧清单、缺失产物和被修改的二进制均在安装前被拒绝。

| 状态 | 本轮检查 | 结果与边界 |
|------|----------|------------|
| PASS | Node 定向回归 | `go test -count=1 ./internal/protocol ./internal/server ./internal/trustee` 三个包通过；覆盖身份和绑定、错误 PoP、过期挑战、其他身份 EAR 及 appraisal 失败拒绝。 |
| PASS | Linux 隔离安装回归 | `test_build_install` 4 项通过：失败重建使旧清单失效、旧 Provider 包拒绝、未列入新 Provider 或被修改的 SPIRE 拒绝、完整包只安装已校验文件。账号和服务命令为测试替身，安装目标重定向至临时目录，未安装到宿主机。 |
| PASS | Linux Python 合并回归 | WSL Ubuntu-20.04、独立 Python 3.11.16：`test_build_install` 4 项、`test_runtime` 8 项、`test_spire_cli.OfficialSPIRETests` 2 项，共 14 passed、0 skipped。系统 Python 3.8 的旧用例曾因不支持的语法报错，换用项目支持的 Python 后通过，未修改系统 Python。 |
| PASS | 官方 SPIRE 配置与 Entry 集成 | 使用校验固定 SHA-256 的官方 SPIRE v1.15.3；四个 Go 工具从本轮提交源码重新交叉编译为 Linux 二进制。临时 Server 实际执行 Entry create/show/audit，生成 Agent 配置通过官方 validate。 |
| PASS | 实际 Node Server 插件 Configure | 新增测试启动官方 SPIRE Server 和当前 NodeAttestor 插件：原 Agent ID 与 `argus.local` 配置通过 healthcheck；Agent ID 的 trust domain 不一致时，实际 Configure 报错并阻止 Server 启动。测试未请求 Trustee 或执行 Node join。 |
| PASS | 静态检查 | Shell `bash -n`、Rust Provider 格式与 Git 差异空白检查通过。 |
| NOT_RUN | Linux Rust Provider UDS 与完整 Linux 构建 | 本轮未执行。WSL 缺少 Rust/C 工具链，Docker 因本机 socket 错误未能启动；未将 Windows Rust handler 测试视为 Linux UDS 验收。 |
| NOT_RUN | 真实 TDX/Trustee/SVID 与 systemd 部署 | 未执行硬件 Quote、真实 Trustee appraisal、Node join/re-attestation、Agent/Workload SVID 签发及公司主机安装。 |

本节补齐上节尚未执行的官方 SPIRE CLI 集成，保留此前测试的时间与环境边界。
开发机日志、二进制哈希与环境来源记录位于 `.git/pr364-chain-review/spire-cli/`，
其中 `linux-python311-combined.log` 记录 14 项合并回归结果。该目录不属于源码交付。
临时 Server 数据已清理，未留下运行中的测试 Server。

## 2026-09-17 初步修复：日志关联、Helper 初始化和当前部署流程

`verify` 改用 JSON journal，根据 boot、当前 Helper systemd invocation 与订阅顺序，
逐字段核对 launch、container、PID、start time、policy 和完整 SVID 序列号。
Helper 与 WorkloadAttestor 日志补齐实例字段，缺少字段直接拒绝验收。
这些字段用于本机受信任日志的运行关联，不构成 EAR 到 SVID 的新增密码学证明。

Helper 的 60 秒启动预算覆盖 Workload API 初始身份、Broker 建连和首次凭据完整发布。
目标 watcher 从初始化阶段就能取消等待；首次发布成功后关闭启动计时器，保留目标监控
和凭据过期检查。失败仍进入既有清理与停止 hook。

正式部署脚本和示例配置移除旧 Provider 名、`previous_*` 停服设置及升级分支；
Node 配置工具必须指定本次部署的插件二进制。产物白名单、哈希、失败构建清单失效、
现有进程冲突检查和 Node 信任配置保持。本文件上方保留各日期的历史记录。

| 状态 | 检查 | 结果与边界 |
|------|------|------------|
| PASS | Python 运行合同 | `test_runtime` 11 项通过；包括 launch/serial 前缀、错误 policy/container/start time、旧 invocation、错误 unit/boot、缺失或乱序事件、重复字段、验证期间重启和正常轮换。外部命令使用替身。 |
| PASS | Linux Helper 生命周期 | Go 1.26.5 交叉编译后在 WSL Linux 执行 `pkg/broker`，9 项顶层测试全部通过、0 skipped。真实 go-spiffe SDK 的初始身份等待可被超时和目标退出取消；首次目标等待/发布受预算限制，成功发布后可继续运行并响应目标退出。目标 watcher 的错误通道使用测试替身，未执行公司 systemd 栈。 |
| PASS | Helper 与配置工具 | Windows 下 `go test -mod=readonly -count=1 ./pkg/broker ./cmd/argus-agent-config` 及对应 `go vet` 通过；Linux 专属行为以上一行实际执行为准。 |
| PASS | WorkloadAttestor | `go test -mod=readonly -count=1 ./...` 和 `go vet -mod=readonly ./...` 通过。 |
| PASS | Linux 构建/安装合同 | Python 3.11.16 执行 `test_build_install` 4 项通过、0 skipped；使用伪构建产物、服务命令替身和临时安装根目录。 |
| NOT_RUN | 实际服务验收 | 本次未执行真实 NGINX、systemd 部署、硬件 Quote、Trustee、SVID 签发及 OpenViking 业务验收；未改动 Rust Provider 和路由。 |

## 公司环境待执行

按 [运行手册](README.md) 提供批准的镜像/配置/平台基线、现有 Node 配置、Trustee TLS/EAR 信任材料及 OpenClaw 客户端 SVID，执行：

1. 使用官方 SPIRE v1.15.3 重新验证原 Node 加入；保留原 proof key、Node policy 与信任合同。
2. 从 TDVM 直接 HTTPS 访问 Trustee `/attestation`，核对实际固定 workload policy；完成真实 TSM Quote、DCAP、签名 EAR、目标 SVID 和 OpenViking 业务 2xx。
3. 执行真实错误镜像/配置/平台、Quote/binding 和 policy 拒绝用例，确认取不到目标身份。
4. 使用 `verify-lifecycle.py` 验证正常轮换、错误客户端、目标退出及 Helper SIGKILL；确认 readiness/PEM 清理、NGINX 停服与现有连接清理时限。
5. 在 systemd 环境补验 Agent/Broker 断连、完整快照身份移除、凭据过期和 reload 故障。该部分的逻辑测试已通过，实际 systemd/网络故障验收尚待执行。

公司执行记录由工具保存到 `/var/log/argus-workload/`，关联版本、boot/PID/start time、launch/container、nonce、policy、EAR 摘要、SVID 序列号和业务结果。未取得这些记录前，真实 TDX 全链路状态为 **待验收**。

本轮不依赖 Rekor 验证门禁；TC API 原日志提交链保持。普通 SVID 轮换不计为重新证明；Agent 侧独立周期重新证明未实现。

开发机清理说明：自动审批审核以 `blocked by policy` 拒绝删除本轮临时构建目录和 Helper 的 Windows 编译产物。这些文件暂留在开发机，已由 Git ignore 排除，不属于源码交付。
