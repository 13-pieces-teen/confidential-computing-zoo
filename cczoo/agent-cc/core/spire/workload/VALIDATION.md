# Workload Attestation 首轮验证记录

执行日期：2026-09-05 至 2026-09-06（Asia/Shanghai）。实现和 review 的基线为分支 `feat/argus-spiffe-v2-val`、提交 `9ced0e3`；本轮按模块分开提交，没有部署到公司环境。开始时已 fetch，远端与本地基线无差异。

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
