# OpenViking Workload Attestation：公司环境运行手册

2026-09-09 补充：已批准的 `OutOfDate` PoC 原始策略可通过 `approved_policy_artifact.path` 和 `approved_policy_artifact.sha256` 显式输入；本地文件与 Trustee 回读必须逐字节匹配。未配置该项时严格模板仍要求 `UpToDate`，不会根据策略名称自动放宽。客户端部署及使用方式见 [IP1 OpenClaw TDVM 操作手册](../../../adapters/OpenClaw/spiffe_client/DEPLOY-IP1-TDVM.md)。

本目录交付首轮真实取证链的代码、配置和验收入口。SPIRE Server/Agent 与两个 Attestor SDK 使用 **v1.15.3**；Helper 基于官方 **v0.11.0**，定制构建版本为 **0.11.0-argus.1**。Trustee 接口基线为 **v0.21.0**。

完整流程和信任边界见 [当前方案](../../../documents_ly/Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)。本轮不验证 Rekor；TC API 原有日志上传保持。普通 SVID 轮换不会生成新 Quote；Helper 重连才重新订阅、重新认证。真实 TDX 验收需要公司 TDVM。

## 统一部署配置（方案 A）

唯一输入是 [environment.example.json](config/environment.example.json) 对应的 `schema_version: 1` 配置；缺少字段、未知字段和旧 `previous_*` 字段直接拒绝。示例中的域名、身份、端口和目录仅为一套部署值，下面命令使用这套示例路径。部署仍限定 OpenViking、Docker、一个 Node slot 和一个目标监听进程；服务 unit 名与 `argus-nginx` 账号固定。

| 配置段 | 内容及消费端 |
|---|---|
| `identity` | trust domain 与完整 Agent/Helper/target/client SPIFFE ID；贯通 Provider、Attestor、Helper、Broker allowlist、Entry、Rego、AuthZ 和验收探针。 |
| `paths` | 安装、配置、记录、SPIRE 二进制目录与 `run_name`；渲染全部 unit、socket、凭据、NGINX 日志/临时目录和清理 hook。 |
| `workload` | workload ID、宿主机/容器内配置与数据路径、内部监听端口、TLS 端口、宿主发布端口；贯通 TC API、登记、Provider、Rego 和 NGINX。 |
| `approved` 与信任字段 | 显式批准的镜像/配置/平台摘要、policy 及 TLS/EAR 材料；不会从当前观察值自动批准。 |

`run_name=argus` 派生 `/run/argus-workload/target.json`、`/run/argus/evidence-provider.sock`、`/run/argus-credentials/`、`/run/argus-nginx/`、`/run/argus-authz/authz.sock`。Workload API 与 Broker 分别使用 `/run/argus-spire-agent/agent.sock`、`/run/argus-spire-broker/broker.sock`，符合 SPIRE 对独立目录的要求。Linux 路径只接受干净的绝对路径；配置和可执行文件不能落入可写数据目录或 `/tmp`。

安装与 `render` 生成 Helper/NGINX 配置、systemd units、hook、TC API 最小配置；`render` 还生成 Agent HCL 和批准策略。安装、配置、记录与 SPIRE 二进制目录不能与派生的运行目录重叠，避免停服清理删除配置或程序。两者更新 unit 后执行 `daemon-reload`，不自动启动服务。应用新配置之前必须先停止现有认证栈；活跃服务会导致拒绝安装/渲染，防止旧实例的清理 hook 读到新目录。应编辑单独的输入文件，停止旧实例后再应用，不直接覆盖运行中的 `environment.json`。

## 1. 运行前提供的材料

准备已批准的 Node Agent/Server 配置、SPIRE bootstrap bundle、proof key、Trustee TLS CA、固定 EAR P-256 公钥及 issuer/profile，以及 Node policy 与对应信任材料。

同时提供：

- 已批准的 OpenViking 实际 image config digest（Docker `.Image` / image inspect `.Id`，格式为 `sha256:...`）、实际配置文件 SHA-256、服务进程 executable 路径。
- 已批准 TDVM 的 `mr_td`、`rtmr_0/1/2`，均为 96 位小写十六进制；运行时日志可改变 RTMR3，本 policy 不固定 RTMR3。
- OpenClaw 客户端的有效 SVID、私钥、bundle，以及可返回 2xx 的 OpenViking 业务 URL。业务 API key 如需要，通过 `OPENVIKING_API_KEY` 环境变量提供。
- TDVM 上可以直连 Trustee 的 HTTPS 地址；可通过已配置 SSH alias 查询 SPIRE Server。SSH 必须使用已有 host key 校验，目标账号需能读取 Server socket 和运行中进程信息。

镜像 ID、配置摘要必须经过审批后填写基线。运行工具不会把当前观察值自动写成批准值。授权使用 `runtime_image_config_digest` 与 Provider 实际观察的 SHA-256 内容 ID。

## 2. 构建与安装

在 Linux x86_64 构建机运行，安装 Go 1.25.3 或更新版本、Rust 1.88 或更新版本、OpenSSL 开发包、pkg-config、Python 3.11+、NGINX（带 `http_auth_request_module`）、curl。本地已使用 Go 1.26.5 和 Rust 1.88 验证。Python 构建环境需安装 TC API 依赖及 pytest：

```bash
cd cczoo/agent-cc/core/spire/workload
python3 -m pip install -r ../../tc_api/requirements.txt pytest pytest-asyncio
bash scripts/build.sh
# 先复制示例、填写两台主机各自的路径与批准值，并保存为 root:root 0600。
sudo bash scripts/install.sh --config /root/workload-environment.json
```

`build.sh` 执行 Node、Workload、官方 Helper、NGINX、TC API 启动和 Trustee 合同测试，并用官方 SPIRE 校验生成配置与真实 Entry JSON，下载官方 SPIRE v1.15.3 二进制并检查固定 SHA-256。它不编译或修改 SPIRE Core。产物位于 `build/`，包含插件、Helper、Provider、辅助工具及哈希清单。

构建开始时会使旧的 `SHA256SUMS` 失效，全部检查成功后才原子发布新清单。
安装前检查 [完整可执行产物列表](scripts/build-artifacts.sh) 中的 12 个文件及其哈希，
包括 Provider 和官方 SPIRE Agent/Server；只安装这些文件。
部署使用完整构建产物，缺失清单、缺失产物或被修改的二进制都会在安装前被拒绝。

把相同构建产物安装到 TDVM 与 Server 主机。Server 只使用其中的 Server/Node 插件和 Entry 工具。安装不会启动或启用服务；会将指定输入写入 `paths.config_dir/environment.json` 并重新渲染本栈配置。原 Node 源配置保持原样。

Evidence Provider 的 UDS 路由为 `POST /ra/v1/node-evidence` 和 `POST /ra/v1/workload-evidence`（后者需配置 workload 登记文件）。Provider 与 NodeAttestor 插件来自同一次完整构建；`workload.py render` 将插件路径与 SHA-256 写入 Agent 配置，`workload.py start` 自动执行 `render`。配置只指定 UDS socket，无需填写 HTTP 路径。

Provider 二进制为 `argus-spire-evidence-provider`，源码为 `core/argus/src/bin/spire_evidence_provider.rs`。安装脚本提供 `argus-tdx-provider.service`，其 `ExecStart` 指向该二进制，并从统一配置显式传入 Agent ID、socket、登记文件及 `--workload-data-path`。

参考 `config/environment.example.json` 准备两台主机各自的输入配置，安装后保存至 `paths.config_dir/environment.json`，设为 root 所有、0600。填写真实路径和批准基线；示例占位值会被拒绝。两台机器的 Agent/Helper/目标身份、批准基线及 Helper 二进制必须一致；`paths.install_dir` 也须相同，因为 Server 使用本地同路径的 Helper 副本生成目标主机的 `unix:path` 与 `unix:sha256` selectors。TDVM 预检会逐项比较 Server 返回的 Entry 合同和本地预期，任一不一致直接拒绝。

## 3. 配置官方 SPIRE v1.15.3 与 Node 插件

先在 Server 上用已批准的 Node 配置生成部署配置：

```bash
sudo /opt/argus-workload/bin/argus-agent-config -role server \
  -source /etc/spire/argus-poc/server.conf \
  -node-binary /opt/argus-workload/bin/argus-tdx-nodeattestor-server \
  -trust-domain argus.local \
  -agent-id spiffe://argus.local/spire/agent/argus_tdx/openviking-node \
  -output /etc/argus-workload/server.conf
```

该工具要求显式指定 `-node-binary`，写入插件命令和校验摘要，保留 Server Node `plugin_data`、challenge、PoP、REPORTDATA、Trustee EAR 信任配置及 CA 配置。Server systemd unit 的 `ExecStart` 配置为：

```ini
[Service]
ExecStart=
ExecStart=/opt/spire-1.15.3/bin/spire-server run -config /etc/argus-workload/server.conf
```

重载 systemd 并重启该 Server unit。在 TDVM 执行：

```bash
sudo python3 /opt/argus-workload/scripts/workload.py render --config /etc/argus-workload/environment.json
```

Agent 配置从已批准的 Node 配置合并生成，保留 proof key 路径等协议设置，设置 Node 插件二进制、Provider socket、Workload API socket，加入 WorkloadAttestor 与本机 Broker。生成结果可在 `/etc/argus-workload/agent.conf` 审查。

NodeAttestor 支持配置 Agent ID，Provider 的 `--agent-id` 是必填项，必须等于 Server `plugin_data.agent_id`；该 ID 的 trust domain 必须与 SPIRE `trust_domain` 一致。Workload 合同保留 v1，只检查合法 Agent ID 格式；Provider、WorkloadAttestor、Helper、Entry 与策略进一步核对本次配置中的批准身份。更换域名/身份时还需同步现有 Node 配置与客户端身份材料；Server 工具会拒绝 Node 配置与部署值不一致。仍只支持单个已固定 proof key 的 Node slot，不新增多节点注册；完整格式见 [身份配置合同](../../argus/docs/configuration.md#spire-node-attestation)。

Provider 将配置中的 Agent ID、Server nonce 和 proof public key 绑定到 `REPORTDATA`；expiry 和 Quote digest 由 PoP transcript 签名覆盖。Trustee 负责 Quote/TCB/policy 评估，Server NodeAttestor 验证 PoP 与签名 EAR 后才返回 `AgentAttributes`，最终由 SPIRE Server CA 签发 Agent SVID。业务服务的 SVID 仍由后续 Workload 证明和静态 Entry 独立控制。

Node 运行脚本 `core/spire/scripts/argus-node-attestation.sh` 使用 v1.15.3 路径并检查 Agent/Server 二进制版本；Workload preflight 通过远端 `server-check` 核对 **正在运行** 的 Server executable。Node 加入需要公司环境验收。

## 4. 安装固定 workload policy 与静态 Entry

`render` 生成 `/etc/argus-workload/argus-workload-openviking-v1_cpu.rego`（文件名前缀随批准 policy ID）。先审查内容，再通过已有 Trustee 管理通道安装。

Trustee v0.21 REST 的 `POST /policy` 接受 `policy_id` 与无补位 base64url 的 `policy`。例如生成可审查请求文件：

```bash
sudo python3 - <<'PY'
import base64,json,pathlib
p=pathlib.Path("/etc/argus-workload/argus-workload-openviking-v1_cpu.rego")
out=p.with_suffix(".request.json")
out.write_text(json.dumps({"policy_id":p.stem,"policy":base64.urlsafe_b64encode(p.read_bytes()).decode().rstrip("=")}))
out.chmod(0o600)
PY
```

将请求提交到公司现有、受控的 Trustee 管理入口。不要把 policy 写权限开放给 workload。评估请求使用不带 `_cpu` 的 policy ID；Trustee 为 CPU 选择带该后缀的存储 policy，EAR 中返回请求的原 ID。preflight 会直接读取 Trustee `GET /policy/<id>_cpu`，确认实际内容与批准的渲染结果一致。

在 SPIRE Server 主机执行：

```bash
sudo python3 /opt/argus-workload/scripts/workload.py apply-entries --config /etc/argus-workload/environment.json
sudo python3 /opt/argus-workload/scripts/workload.py server-check --config /etc/argus-workload/environment.json
```

Helper Entry 同时要求 root UID、实际二进制路径和 SHA-256。目标 Entry 同时要求 `argus_tdx:verified:true`、workload、policy、Agent ID、实际镜像和配置摘要，并关闭 X.509-SVID 预取（`disableX509SVIDPrefetch=true`）。

工具检查相同身份的 **全部 Entry**；发现旧的宽泛 Entry 会拒绝继续，输出 Entry ID。必须先审查并通过 Server 的 `entry delete -entryID ...` 移除绕过证明的旧授权，再运行。工具不会悄悄保留同身份旁路，也不会自行删除已有授权。

## 5. TC API 启动与目标登记

在 TDVM 部署本分支的 TC API。给 TC API 容器传入：

```text
ARGUS_WORKLOAD_CONFIG=/etc/argus-workload/tc-api-workload.json
```

该文件由统一配置导出，仅包含 schema 版本和 `workload` 段，以 root 所有且不可被组/其他用户写入的只读文件挂载给 TC API。`config_host_path` 与 `data_host_path` 必须同时在 Docker 宿主机存在，并以相同路径挂载给 TC API。OpenViking 配置中保持 `server.host=127.0.0.1`，`server.port` 等于 `workload.listen_port`、`storage.workspace` 等于 `workload.data_path`。基于现有 OpenViking 配置补齐模型、API key 等业务参数。

专用 profile 固定非特权容器、只读 rootfs、独立 bridge network namespace、单个监听进程、仅发布 `published_port:tls_port`、只读配置 bind mount 与单独可写数据目录。无 TDX 设备、SPIRE socket 或私钥挂入业务容器。镜像如果声明额外 volume、服务启动多个共享监听 worker、或实际读取不同配置，Provider 会拒绝；先修正运行配置再登记。

```bash
# 使用公司已有 OIDC 登录流程取得 token；不关闭日志上传。
export TC_API_IDENTITY_TOKEN=...
# 查询结果默认复用 identity token 作为 Bearer；若网关另有要求，设置 TC_API_BEARER_TOKEN。
sudo --preserve-env=TC_API_IDENTITY_TOKEN,TC_API_BEARER_TOKEN \
  python3 /opt/argus-workload/scripts/workload.py launch --config /etc/argus-workload/environment.json
sudo python3 /opt/argus-workload/scripts/workload.py register --config /etc/argus-workload/environment.json
```

`launch` 使用 TC API 的 `nginx-spiffe-helper-v1` profile，保留 container/launch/image 内容关联。Docker argv 与启动日志的 mounts、ports、环境摘要使用同一份已校验配置快照；请求不能覆盖挂载或运行命令。请求拒绝 HTTP 重定向，需填写可以直接处理请求的 TC API 地址。`register` 在宿主机解析实际监听 `workload.listen_port` 的进程，而非直接采用 container init PID；生成 root 保护的 `/run/argus-workload/target.json`。

首次登记不覆盖已有登记。替换实例需要先 `stop`，再启动/登记新实例；不支持原地切换成另一个进程。`stop` 停止认证栈并撤下入口；旧 OpenViking 容器仍由 TC API/Docker 管理，启动替换容器前需按旧 container ID 停止它，释放 `workload.published_port` 指定的端口。业务容器不能修改登记文件。

## 6. 预检、启动、状态、验证与停止

```bash
sudo python3 /opt/argus-workload/scripts/workload.py preflight --config /etc/argus-workload/environment.json
sudo python3 /opt/argus-workload/scripts/workload.py start --config /etc/argus-workload/environment.json
sudo python3 /opt/argus-workload/scripts/workload.py status --config /etc/argus-workload/environment.json
sudo --preserve-env=OPENVIKING_API_KEY \
  python3 /opt/argus-workload/scripts/workload.py verify --config /etc/argus-workload/environment.json
sudo python3 /opt/argus-workload/scripts/workload.py stop --config /etc/argus-workload/environment.json
```

preflight 检查批准基线、真实版本、同身份 Entry、Trustee 直连 HTTPS/REST/policy、固定 EAR 公钥、TSM、目标当前实例与客户端材料。缺项直接停止并报告，不转向 mock、代理或旧 `allow` JSON。

start 在通过预检并生成配置后启动 systemd 栈。如果 SPIRE Agent 或 Provider 进程已运行，启动检查会报告 PID 并停止执行；需先停止占用进程。首次目标凭据通过链、身份和密钥检查，完整代次切换，再经 NGINX `-t` 与实际 TLS 加载检查后，才原子发布 readiness；发布期间过期会清理凭据并停服。

NGINX 进入 OpenViking 的 network namespace，对外终止 mTLS；OpenViking 内部端口只监听该 namespace 的回环。NGINX 先验证客户端证书链，再把实际 TLS 证书及验证状态覆盖写入受控 UDS 请求。AuthZ 检查唯一 SPIFFE URI、用途、有效期与配置的 `identity.client_id`。客户端工具同时核对服务端目标 ID。

OpenClaw 的实际插件调用按 [原生 SPIFFE mTLS 接入手册](../../../adapters/OpenClaw/spiffe_client/README.md)部署。新增 `spiffe-client-credentials` 通过 OpenClaw 自己的 Agent/Broker 引用真实 Gateway PID，将目标凭据交付给插件内的 HTTPS 客户端；连接脚本区分安装、PID 登记和配置生效，业务验收检查 Gateway 的实际 mTLS 写入日志。这里的 `verify` 探针仍用于 OpenViking 服务端验收，不能替代完整的 OpenClaw 插件业务验收。

PEM 位于 root 所有的 0700 tmpfs 目录；每代包含证书、PKCS#8 私钥、bundle。每次变更创建新文件和目录，避免 NGINX 因文件缓存沿用旧证书。TLS session cache/tickets/early data 关闭。

Helper 持有目标 pidfd，约每 500 ms 复核实例。60 秒启动预算覆盖等待 Helper 自身 SVID、建立 Broker 订阅、等待目标 SVID 和首次完整发布；首次发布成功后解除启动计时，继续检查凭据有效期。初始化期间目标退出也会取消正在等待的调用。目标退出、身份移除、订阅断开、凭据过期、PEM 或 reload 失败会清除 readiness/PEM 并停止 NGINX。Helper 被 SIGKILL 时，由 systemd BindsTo、ExecStopPost 和 RuntimeDirectory 清理兜底。NGINX 停服后连接清理上限为 5 秒；实例检测另有轮询调度时间。重连会重新证明；Agent 独立周期重证明尚未实现。

## 7. 公司验收与记录

### 终端查看 Workload 认证状态

在 IP2 打开一个终端，运行只读日志入口：

```bash
sudo python3 /opt/argus-workload/scripts/watch-attestation.py
```

它先打印最近一小时的已有事件，再持续显示新事件；按 `Ctrl+C` 只退出查看器。每行保留 journal 的原始 UTC 时间和 unit，使用 `[INSTANCE]`、`[QUOTE]`、`[EAR]`、`[SVID]` 标记订阅实例、Quote 生成、EAR 校验接受、目标 SVID 发布；已知认证/凭据错误显示 `[ERROR]`，其他进程错误显示 `[PROCESS-ERROR]` 并保留原日志。原日志字段中的 `launch_id`、PID、nonce、policy、EAR 摘要和 serial 可直接用于关联。SVID 行额外输出 `serial_hex`，便于与 OpenClaw 请求的十六进制序列号比较。

查看更早的一次准入、打印后退出：

```bash
sudo python3 /opt/argus-workload/scripts/watch-attestation.py \
  --since '2026-09-07 00:00:00 UTC' --no-follow
```

已有部署可以直接从更新后的仓库运行 `scripts/watch-attestation.py`，不必为查看日志重跑安装、重启服务或重新证明。脚本仅使用 Python 标准库和 `journalctl`。它不生成 Quote，不接触密钥，不修改策略，不把无日志当作通过；`[EAR]` 后仍需结合实例检查和目标 SVID 判断完整准入，`[SVID]` 轮换不是一次新认证。它显示原事件中的策略 ID，不根据名称推断 `tcb_status`；真实 TCB 结论仍以对应 EAR/验收记录为准。

### 联合验证

`verify` 同时检查实例、实际业务 2xx、客户端/服务端 SPIFFE ID、NGINX 当前证书序列号与固定 Entry。它读取 JSON journal，只接受当前 boot 和 Helper systemd invocation 内按订阅、EAR 接受、SVID 发布顺序关联的事件，逐字段精确比较 launch、container、PID、start time、policy 和完整证书序列号；验证期间 Helper 重启或就绪状态变化会拒绝通过。当前构建的 Helper 与 WorkloadAttestor 必须一并部署，缺少关联字段会拒绝验收。记录保存到 `/var/log/argus-workload/`，原始 JSON journal 保存为 `last-verify-journal.jsonl`；不写出私钥、原始 Quote 或 EAR token，保存 nonce、实例、policy、EAR 摘要与 SVID 序列号的运行日志关联。

以下入口会故意中断指定测试工作负载，只在公司验收实例运行：

```bash
sudo --preserve-env=OPENVIKING_API_KEY python3 /opt/argus-workload/scripts/verify-lifecycle.py rotation --config /etc/argus-workload/environment.json
sudo --preserve-env=OPENVIKING_API_KEY python3 /opt/argus-workload/scripts/verify-lifecycle.py wrong-client --config /etc/argus-workload/environment.json \
  --wrong-client-cert /approved-test-client/svid.pem --wrong-client-key /approved-test-client/key.pem
sudo --preserve-env=OPENVIKING_API_KEY python3 /opt/argus-workload/scripts/verify-lifecycle.py helper-crash --config /etc/argus-workload/environment.json
# 重新 register/start/verify 后执行目标退出用例：
sudo --preserve-env=OPENVIKING_API_KEY python3 /opt/argus-workload/scripts/verify-lifecycle.py target-exit --config /etc/argus-workload/environment.json
```

普通轮换必须保持就绪、证书序列号改变且没有新的 appraisal；Helper crash/target exit 必须撤下入口并清理凭据。负向操作之后工具将栈停下，恢复需要重新登记。对 nonce、镜像、配置、policy、伪造/过期 EAR 的负向测试由插件/Provider 合同测试覆盖；公司还须验证真实 DCAP/Quote 拒绝行为。

本地与公司执行结果分开记录，见 [本轮验证记录](VALIDATION.md)。没有公司真实验收记录时，不能把本地软件测试写成真实 TDX 全链路已跑通。
