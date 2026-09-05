# OpenViking Workload Attestation：公司环境运行手册

本目录交付首轮真实取证链的代码、配置和验收入口。SPIRE Server/Agent 与两个 Attestor SDK 使用 **v1.15.3**；Helper 基于官方 **v0.11.0**，定制构建版本为 **0.11.0-argus.1**。Trustee 接口基线为 **v0.21.0**。

完整流程和信任边界见 [当前方案](../../../documents_ly/Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)。本轮不验证 Rekor；TC API 原有日志上传保持。普通 SVID 轮换不会生成新 Quote；Helper 重连才重新订阅、重新认证。真实 TDX 验收需要公司 TDVM。

## 1. 运行前提供的材料

准备原来已调通的 Node Agent/Server 配置、SPIRE bootstrap bundle、原 proof key、Trustee TLS CA、固定 EAR P-256 公钥及 issuer/profile。保留原 Node policy 与对应信任材料。

同时提供：

- 已批准的 OpenViking 实际 image config digest（Docker `.Image` / image inspect `.Id`，格式为 `sha256:...`）、实际配置文件 SHA-256、服务进程 executable 路径。
- 已批准 TDVM 的 `mr_td`、`rtmr_0/1/2`，均为 96 位小写十六进制；运行时日志可改变 RTMR3，本 policy 不固定 RTMR3。
- OpenClaw 客户端的有效 SVID、私钥、bundle，以及可返回 2xx 的 OpenViking 业务 URL。业务 API key 如需要，通过 `OPENVIKING_API_KEY` 环境变量提供。
- TDVM 上可以直连 Trustee 的 HTTPS 地址；可通过已配置 SSH alias 查询 SPIRE Server。SSH 必须使用已有 host key 校验，目标账号需能读取 Server socket 和运行中进程信息。

镜像 ID、配置摘要必须经过审批后填写基线。运行工具不会把当前观察值自动写成批准值。TC API 旧 `image_digest` 日志字段可能采用旧摘要格式；本轮授权只使用新 `runtime_image_config_digest` 与 Provider 实际观察的 SHA-256 内容 ID。

## 2. 构建与安装

在 Linux x86_64 构建机运行，安装 Go 1.25.3 或更新版本、Rust 1.88 或更新版本、OpenSSL 开发包、pkg-config、Python 3.11+、NGINX（带 `http_auth_request_module`）、curl。本地已使用 Go 1.26.5 和 Rust 1.88 验证。Python 构建环境需安装 TC API 依赖及 pytest：

```bash
cd cczoo/agent-cc/core/spire/workload
python3 -m pip install -r ../../tc_api/requirements.txt pytest pytest-asyncio
bash scripts/build.sh
sudo bash scripts/install.sh
```

`build.sh` 执行 Node、Workload、官方 Helper、NGINX、TC API 启动和 Trustee 合同测试，并用官方 SPIRE 校验生成配置与真实 Entry JSON，下载官方 SPIRE v1.15.3 二进制并检查固定 SHA-256。它不编译或修改 SPIRE Core。产物位于 `build/`，包含插件、Helper、Provider、辅助工具及哈希清单。

把相同构建产物安装到 TDVM 与 Server 主机。Server 只使用其中的 Server/Node 插件和 Entry 工具。安装不会启动或启用新服务；现有配置不会被覆盖。

参考 `config/environment.example.json` 建立两台主机各自的 `/etc/argus-workload/environment.json`，设为 root 所有、0600。填写真实路径和批准基线；示例占位值会被拒绝。两台机器的批准基线及 Helper 二进制必须一致。

## 3. 保留 Node 合同，升级到官方 v1.15.3

先在 Server 上生成升级配置：

```bash
sudo /opt/argus-workload/bin/argus-agent-config -role server \
  -source /etc/spire/argus-poc/server.conf \
  -node-binary /opt/argus-workload/bin/argus-tdx-nodeattestor-server \
  -output /etc/argus-workload/server.conf
```

该工具只更新 Node 插件命令和校验摘要，保留 Server Node `plugin_data`、challenge、PoP、REPORTDATA、Trustee EAR 信任配置及 CA 配置。将现有 Server systemd unit 的 `ExecStart` 改为：

```ini
[Service]
ExecStart=
ExecStart=/opt/spire-1.15.3/bin/spire-server run -config /etc/argus-workload/server.conf
```

重载 systemd 并重启该 Server unit。在 TDVM 执行：

```bash
sudo python3 /opt/argus-workload/scripts/workload.py render
```

Agent 配置从原 Node 配置合并生成，保留 proof key 路径等协议设置，更新 Node 插件二进制、Provider socket、Workload API socket，加入 WorkloadAttestor 与本机 Broker。生成结果可在 `/etc/argus-workload/agent.conf` 审查。

原 Node 运行脚本 `core/spire/scripts/argus-node-attestation.sh` 已改为 v1.15.3 路径并检查 Agent/Server 二进制版本；新 Workload preflight 进一步通过远端 `server-check` 核对 **正在运行** 的 Server executable。Node 加入必须在公司环境重新验收；历史 v1.15.2 报告仍表示当时的真实版本。

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
sudo python3 /opt/argus-workload/scripts/workload.py apply-entries
sudo python3 /opt/argus-workload/scripts/workload.py server-check
```

Helper Entry 同时要求 root UID、实际二进制路径和 SHA-256。目标 Entry 同时要求 `argus_tdx:verified:true`、workload、policy、Agent ID、实际镜像和配置摘要，并关闭 X.509-SVID 预取（`disableX509SVIDPrefetch=true`）。

工具检查相同身份的 **全部 Entry**；发现旧的宽泛 Entry 会拒绝继续，输出 Entry ID。必须先审查并通过 Server 的 `entry delete -entryID ...` 移除绕过证明的旧授权，再运行。工具不会悄悄保留同身份旁路，也不会自行删除已有授权。

## 5. TC API 启动与目标登记

升级 TDVM 的 TC API 至本分支代码。给 TC API 容器传入：

```text
ARGUS_OPENVIKING_CONFIG_PATH=/srv/openviking/ov.conf
ARGUS_OPENVIKING_DATA_PATH=/srv/openviking/data
```

上述路径必须同时在 Docker 宿主机存在，并以相同路径挂载给 TC API。配置文件只读挂载；配置中设定 `server.host=127.0.0.1`、`server.port=1933`、`storage.workspace=/var/lib/openviking`。基于现有 OpenViking 配置补齐模型、API key 等业务参数。

专用 profile 固定非特权容器、只读 rootfs、独立 bridge network namespace、单个监听进程、仅发布 1943、只读配置 bind mount 与单独可写数据目录。无 TDX 设备、SPIRE socket 或私钥挂入业务容器。镜像如果声明额外 volume、服务启动多个共享监听 worker、或实际读取不同配置，Provider 会拒绝；先修正运行配置再登记。

```bash
# 使用公司已有 OIDC 登录流程取得 token；不关闭日志上传。
export TC_API_IDENTITY_TOKEN=...
# 查询结果默认复用 identity token 作为 Bearer；若网关另有要求，设置 TC_API_BEARER_TOKEN。
sudo --preserve-env=TC_API_IDENTITY_TOKEN,TC_API_BEARER_TOKEN \
  python3 /opt/argus-workload/scripts/workload.py launch
sudo python3 /opt/argus-workload/scripts/workload.py register
```

`launch` 使用 TC API 的 `nginx-spiffe-helper-v1` profile，保留 container/launch/image 内容关联。请求拒绝 HTTP 重定向，需填写可以直接处理请求的 TC API 地址。`register` 在宿主机解析实际监听 1933 的进程，而非直接采用 container init PID；生成 root 保护的 `/run/argus-workload/target.json`。

首次登记不覆盖已有登记。替换实例需要先 `stop`，再启动/登记新实例；不支持原地切换成另一个进程。`stop` 停止认证栈并撤下入口；旧 OpenViking 容器仍由 TC API/Docker 管理，启动替换容器前需按旧 container ID 停止它，释放宿主机 1943 端口。业务容器不能修改登记文件。

## 6. 预检、启动、状态、验证与停止

```bash
sudo python3 /opt/argus-workload/scripts/workload.py preflight
sudo python3 /opt/argus-workload/scripts/workload.py start
sudo python3 /opt/argus-workload/scripts/workload.py status
sudo --preserve-env=OPENVIKING_API_KEY \
  python3 /opt/argus-workload/scripts/workload.py verify
sudo python3 /opt/argus-workload/scripts/workload.py stop
```

preflight 检查批准基线、真实版本、同身份 Entry、Trustee 直连 HTTPS/REST/policy、固定 EAR 公钥、TSM、目标当前实例与客户端材料。缺项直接停止并报告，不转向 mock、代理或旧 `allow` JSON。

start 在通过预检后停止配置中指定的旧 Agent/Provider unit，启动新 systemd 栈。手工启动的旧进程不会被自动杀掉；需先按 PID 停止。首次目标凭据通过链、身份和密钥检查，完整代次切换，再经 NGINX `-t` 与实际 TLS 加载检查后，才原子发布 readiness；发布期间过期会清理凭据并停服。

NGINX 进入 OpenViking 的 network namespace，对外终止 mTLS；OpenViking 内部端口只监听该 namespace 的回环。NGINX 先验证客户端证书链，再把实际 TLS 证书及验证状态覆盖写入受控 UDS 请求。AuthZ 检查唯一 SPIFFE URI、用途、有效期与固定 OpenClaw ID。客户端工具同时核对服务端目标 ID。

PEM 位于 root 所有的 0700 tmpfs 目录；每代包含证书、PKCS#8 私钥、bundle。每次变更创建新文件和目录，避免 NGINX 因文件缓存沿用旧证书。TLS session cache/tickets/early data 关闭。

Helper 持有目标 pidfd，约每 500 ms 复核实例。目标退出、身份移除、订阅断开、凭据过期、PEM 或 reload 失败会清除 readiness/PEM 并停止 NGINX。Helper 被 SIGKILL 时，由 systemd BindsTo、ExecStopPost 和 RuntimeDirectory 清理兜底。NGINX 停服后连接清理上限为 5 秒；实例检测另有轮询调度时间。重连会重新证明；Agent 独立周期重证明尚未实现。

## 7. 公司验收与记录

`verify` 同时检查实例、实际业务 2xx、客户端/服务端 SPIFFE ID、NGINX 当前证书序列号、固定 Entry、与本次启动关联的 EAR 接受日志。记录保存到 `/var/log/argus-workload/`，不写出私钥、原始 Quote 或 EAR token；保存 nonce、launch、policy、EAR 摘要与 SVID 序列号关联。

以下入口会故意中断指定测试工作负载，只在公司验收实例运行：

```bash
sudo --preserve-env=OPENVIKING_API_KEY python3 /opt/argus-workload/scripts/verify-lifecycle.py rotation
sudo --preserve-env=OPENVIKING_API_KEY python3 /opt/argus-workload/scripts/verify-lifecycle.py wrong-client \
  --wrong-client-cert /approved-test-client/svid.pem --wrong-client-key /approved-test-client/key.pem
sudo --preserve-env=OPENVIKING_API_KEY python3 /opt/argus-workload/scripts/verify-lifecycle.py helper-crash
# 重新 register/start/verify 后执行目标退出用例：
sudo --preserve-env=OPENVIKING_API_KEY python3 /opt/argus-workload/scripts/verify-lifecycle.py target-exit
```

普通轮换必须保持就绪、证书序列号改变且没有新的 appraisal；Helper crash/target exit 必须撤下入口并清理凭据。负向操作之后工具将栈停下，恢复需要重新登记。对 nonce、镜像、配置、policy、伪造/过期 EAR 的负向测试由插件/Provider 合同测试覆盖；公司还须验证真实 DCAP/Quote 拒绝行为。

本地与公司执行结果分开记录，见 [本轮验证记录](VALIDATION.md)。没有公司真实验收记录时，不能把本地软件测试写成真实 TDX 全链路已跑通。
