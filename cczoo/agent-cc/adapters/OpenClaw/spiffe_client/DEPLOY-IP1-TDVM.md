# IP1 TDVM 中部署 OpenClaw，连接 IP2 OpenViking

本轮交付对应已确认范围：**IP1 Host 创建新 TDVM，OpenClaw 在 Guest 内独立容器运行；OpenClaw 先通过普通 `x509pop` 节点准入及 unix/Docker workload selectors 获取 SVID。OpenClaw 的 TDX 远程证明为 NOT_RUN。** IP2 保留已有 OpenViking TDX PoC 链及显式 `OutOfDate` 策略例外。双向认证指同一条 mTLS 连接的客户端/服务端验证，业务是 OpenClaw 请求、OpenViking 返回结果。

以下是交付给操作者的命令，尚未在公司主机执行。所有 `REPLACE_*`、镜像、地址、证书和进程 PID 都需使用本轮实测值。建议三处均保存 stdout、stderr、操作时间和服务日志；不得把预检结果写成业务或远程证明 PASS。

## 交付内容与工具

`dist/openclaw-ip1-tdvm-stage2.tar.gz` 包含源码、Linux amd64 `spiffe-client-credentials`、`argus.3` 插件包、操作文档和 SHA-256 清单。可选附带官方 SPIRE 1.15.3 归档。解压到一个新目录，先执行 `sha256sum -c SHA256SUMS`，不要直接覆盖当前 IP2 实例。

从工作区重建（Python >= 3.10、Go >= 1.25.3、已校验的 npm 上游包）：

```bash
cd cczoo/agent-cc/adapters/OpenClaw/spiffe_client
python3 build_delivery.py --upstream /path/to/openclaw-plugin-2026.6.18.tgz \
  --spire-archive /path/to/spire-1.15.3-linux-amd64-musl.tar.gz
```

该命令单独构建客户端 Helper，不触发 IP2 的 Rust/TDX 栈重建。包内 `MANIFEST.json` 记录源码基线、工作区改动状态、每个文件摘要、Go 版本、插件及 Helper 摘要。上游依赖版本固定；在 Guest 安装插件时仍需 npm 依赖下载能力或公司准备的包缓存。OpenClaw 镜像、模型配置及现有 OpenViking 用户 API key 不包含在交付包中。

后文假设解压目录为 `/root/argus-stage2`，其下有 `agent-cc/`。执行终端统一设置：

```bash
export KIT=/root/argus-stage2
export OC="$KIT/agent-cc/adapters/OpenClaw/spiffe_client"
export SCRIPTS="$KIT/agent-cc/adapters/OpenClaw/scripts"
umask 077
```

## 1. IP1 Host：启动新的 TDVM

先记录既有 Server/Trustee 状态、CA/bundle、IP2 当前实例信息和 1943 可达性。新 VM 用独立 overlay、名称、端口和资源预算，不复用 IP2 Guest 的数据盘。

```bash
export TDVM_BASE_IMAGE=/path/to/company-approved-tdx-base.qcow2
export TDVM_OVERLAY_IMAGE=/var/lib/argus-openclaw-vm/openclaw.qcow2
export TDVM_FIRMWARE=/path/to/OVMF.inteltdx.fd
export TDVM_SSH_PUBLIC_KEY=/root/.ssh/id_ed25519.pub
export TDVM_GUEST_USER=tdx
export TDVM_GUEST_UID=1000
export TDVM_GUEST_GID=1000
export TDVM_CPUS=8
export TDVM_MEMORY=8G
export TDVM_SSH_PORT=2223
bash "$SCRIPTS/openclaw_tdvm.sh" preflight
bash "$SCRIPTS/openclaw_tdvm.sh" prepare
bash "$SCRIPTS/openclaw_tdvm.sh" start
bash "$SCRIPTS/openclaw_tdvm.sh" status
ssh -p 2223 tdx@127.0.0.1
```

8 CPU/8G 是可调整的起始配置，不是容量实测。profile 只开放 Host loopback SSH 转发；不需要 Host Docker bridge，也不占用 OpenViking 的 1933/1943。QEMU 启动不等于 Guest 启动成功。该 profile 的 Host/Guest `boot` 预检不要求 Quote/QGS/TSM 报告通路；若公司 QEMU/TDVF 启动参数不同，沿用已验证的 TDVM 创建流程，再接下列 Guest 部分。

## 2. Guest 与 IP1：准备普通节点身份及网络

Guest 内安装 Docker、Python >= 3.10、OpenSSL、官方 SPIRE 1.15.3，并复制交付包。若使用附带归档，解压 `vendor/spire-1.15.3-linux-amd64-musl.tar.gz` 至 `/opt`。在 Guest 检查：

```bash
bash "$KIT/agent-cc/core/spire/tests/tdvm/check-tdx-guest.sh" boot
/opt/spire-1.15.3/bin/spire-agent -version
docker info
install -d -m 0755 /etc/argus-openclaw
install -d -m 0700 /etc/argus-openclaw/node
install -d -m 0755 /opt/argus-workload/bin
install -m 0755 "$OC/dist/spiffe-client-credentials" /opt/argus-workload/bin/
# 新节点首次生成；不得覆盖正在使用的私钥。
test ! -e /etc/argus-openclaw/node/key.pem
openssl req -new -newkey rsa:3072 -nodes \
  -keyout /etc/argus-openclaw/node/key.pem -out /etc/argus-openclaw/node/openclaw.csr \
  -subj /CN=argus-openclaw-ip1-tdvm
chmod 0600 /etc/argus-openclaw/node/key.pem
```

将 CSR 传至 IP1，用公司批准的独立 x509pop CA 签发。CA 私钥留在 IP1，Guest 只接收节点证书和公开信任材料。如果本次新建专用实验 CA，可在 IP1 执行一次：

```bash
install -d -m 0700 /etc/argus-openclaw/pki
test ! -e /etc/argus-openclaw/pki/ca.key
openssl req -x509 -newkey rsa:3072 -nodes -days 30 \
  -keyout /etc/argus-openclaw/pki/ca.key -out /etc/argus-openclaw/x509pop-ca.pem \
  -subj /CN=Argus-OpenClaw-PoC-Node-CA \
  -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign
printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n' > /etc/argus-openclaw/pki/node.ext
openssl x509 -req -in /path/to/openclaw.csr \
  -CA /etc/argus-openclaw/x509pop-ca.pem -CAkey /etc/argus-openclaw/pki/ca.key \
  -CAcreateserial -days 7 -extfile /etc/argus-openclaw/pki/node.ext -out /path/to/openclaw-cert.pem
```

把节点证书装到 Guest `/etc/argus-openclaw/node/cert.pem`。从 **当前 IP1 SPIRE Server** 导出公开 bundle，通过已认证管理通道传到 Guest `/etc/argus-openclaw/bootstrap-bundle.pem`；这不是上述 x509pop CA：

```bash
/opt/spire-1.15.3/bin/spire-server bundle show \
  -socketPath REPLACE_WITH_RUNNING_SERVER_SOCKET -format pem > /path/to/bootstrap-bundle.pem
```

Guest 必须能连接 IP1 Server 的实际 TLS/gRPC 地址。QEMU user networking 下 `10.0.2.2` 可作为 Host 地址的候选，但必须实测监听地址、路由和端口；不要直接照抄 IP2 的 loopback 隧道。容器还必须能访问 IP2 NGINX 的 HTTPS origin。若走 TCP 隧道，终点需要对 Guest 的 Docker 容器可达，TLS 始终终止于 IP2 NGINX。`127.0.0.1` 在 Host、Guest、容器中含义不同；本轮 OpenClaw 不需要 Trustee 地址。

## 3. 生成配置并安装 Guest 服务

固定 OpenClaw **镜像 manifest 摘要**和独立的 **image config 摘要**。确认所用镜像中有 `openclaw` 命令，`gateway_command` 与该镜像的正式启动方式一致；默认采用 `openclaw gateway --bind loopback --allow-unconfigured`，不暴露 Gateway 管理端口。

```bash
docker pull REPLACE_WITH_IMAGE_AT_SHA256
docker image inspect REPLACE_WITH_IMAGE_AT_SHA256 --format '{{.Id}}'
openssl x509 -in /etc/argus-openclaw/node/cert.pem -fingerprint -sha1 -noout
sha256sum /opt/argus-workload/bin/spiffe-client-credentials
cp "$OC/config/deployment.example.json" /root/openclaw-deployment.json
# 编辑 /root/openclaw-deployment.json，填写上面的实测值和地址；证书 SHA1 为去掉冒号的小写 40 位。
python3 "$OC/deploy.py" render --config /root/openclaw-deployment.json --output /root/openclaw-rendered
```

生成目录含独立 Agent/Broker 配置、Compose JSON、两个 systemd unit、客户端配置、目标/Helper Entries 和校验清单。Agent ID 根据 x509pop 默认的证书 DER SHA1 路径产生，不复用 IP2 的 argus_tdx Agent ID。协议依据：[SPIRE x509pop](https://github.com/spiffe/spire/blob/v1.15.3/doc/plugin_agent_nodeattestor_x509pop.md)、[Docker selectors](https://github.com/spiffe/spire/blob/v1.15.3/doc/plugin_agent_workloadattestor_docker.md)。

在 IP1 把生成的 `server-x509pop.fragment.hcl` 合并到既有 Server 的 `plugins` 块，保留原 argus_tdx 插件、CA、数据库和监听参数；已有 x509pop 时合并受信 CA 列表，不添加重复块。按现有流程 `spire-server validate -config ACTUAL_CONFIG` 后应用配置。不要把片段直接覆盖整份 Server 配置。

Guest 准备 root-only `/etc/argus-openclaw/gateway.env`，放置实际模型/Gateway 所需环境变量；模型、权限和 OpenClaw 主配置由操作者按所选版本准备。没有环境变量时可用空文件，但不能冒充模型已可用。

```bash
test -e /etc/argus-openclaw/gateway.env || install -m 0600 /dev/null /etc/argus-openclaw/gateway.env
python3 "$OC/deploy.py" guest-install --config /root/openclaw-deployment.json --source /root/openclaw-rendered
/opt/spire-1.15.3/bin/spire-agent validate -config /etc/argus-openclaw/agent.conf
docker compose -f /etc/argus-openclaw/compose.json config --quiet
systemctl start argus-openclaw-agent
journalctl -u argus-openclaw-agent -n 50 --no-pager
```

安装动作不自动启动 Agent 或 Gateway。此 profile 使用 rootful Docker，不支持 user namespace remapping；Gateway 为非 root 用户，仅获得专用 reader GID，不挂载 SPIRE/Broker sockets。Broker socket 与 Workload API socket 分别位于 `/run/spire/openclaw-broker`、`/run/spire/openclaw`，这是官方 SPIRE 的目录隔离要求。

## 4. IP1：核对节点、创建 Entries

把同一份 deployment JSON 复制到 IP1 的 root-owned 路径，核对 `server_socket`、`server_unit`。Agent 接入后执行：

```bash
python3 "$OC/deploy.py" apply-entries --config /root/openclaw-deployment.json
python3 "$OC/deploy.py" server-check --config /root/openclaw-deployment.json > /root/openclaw-entries-check.json
```

工具先读当前 Server 的真实可执行文件版本和 Agent 列表，再检查全部同身份 Entries。只创建缺失项；旧 parent、宽松 selectors、特权或预取设置不符均报错，由操作者明确处理，不自动删除或放宽。Helper 绑定 root UID、路径和二进制 SHA256；OpenClaw 绑定 Guest UID、Node 可执行文件路径、镜像 config 摘要和专用 Docker label。读取结果必须确认为 x509pop 普通身份，不能记为 OpenClaw TDX 证明。

## 5. Guest：启动 Gateway、安装插件、登记真实 PID

```bash
docker compose -f /etc/argus-openclaw/compose.json up -d
export OPENVIKING_API_KEY=REPLACE_WITH_EXISTING_NON_ROOT_USER_KEY
bash "$SCRIPTS/connect_openclaw_openviking.sh" install
docker restart agentcc-openclaw-sbx-gateway
docker top agentcc-openclaw-sbx-gateway -eo pid,args
python3 "$OC/deploy.py" guest-register --pid REPLACE_WITH_ACTUAL_GATEWAY_GUEST_HOST_PID
bash "$SCRIPTS/connect_openclaw_openviking.sh" connect
```

`guest-register` 检查容器、镜像、label、实际 Gateway 命令行、UID、可执行路径，随后登记 PID/start time/boot ID/PID namespace。已有登记不覆盖。`connect` 配置插件后，若需要 Gateway 重启加载配置，按以下顺序重新登记，再做业务验收：

```bash
python3 "$OC/deploy.py" guest-stop
docker restart agentcc-openclaw-sbx-gateway
docker top agentcc-openclaw-sbx-gateway -eo pid,args
python3 "$OC/deploy.py" guest-register --pid REPLACE_WITH_NEW_GATEWAY_PID
python3 "$OC/deploy.py" guest-status
bash "$SCRIPTS/verify_openclaw_plugin_e2e.sh"
```

`guest-stop` 只停止凭据交付、清理凭据和登记，不停止业务容器、Agent 或 VM。Guest 重启后 `/run` 需要重建：先 `install -d -o root -g argus-openclaw -m 0750 /run/argus-openclaw /run/argus-openclaw/credentials`，再启动 Agent/Gateway，并登记新 PID。首次联调期间 Compose 的 restart 为 `no`，避免进程自动替换后误用旧登记。

验收脚本创建独立证据目录：真实 Gateway 写入随机项目校验码 → 原生客户端读回 → commit/archive 并等待提取结束 → 新会话只给项目名检索校验码 → 随机不存在项目回答 `UNKNOWN`。`argus.3` 在 ContextEngine assemble 返回处记录合成校验码的摘要，关联同一次 assemble 的 Gateway mTLS 请求 ID；不把预期答案放入第二次问题，也不修改模型输入。缺少召回、模型回答、Gateway 写入或 mTLS 证据时返回 FAIL。该证据证明插件将召回内容交给 ContextEngine，LLM 网络请求的完整抓包不属于本轮记录。

`argus.3` 分开记录归档完成、提取计数、检索候选及输入边界，空提取明确返回 `EXTRACTION_EMPTY`。异步任务超时后用 `verify_openclaw_plugin_e2e.sh --resume EVIDENCE_DIR` 续查同一 task，不重发 POST；新旧身份作用域不一致时拒绝恢复。完整阶段码、参数和受保护证据说明见 [业务闭环验收](BUSINESS-ACCEPTANCE.md)。

保留 `evidence/<run>/result.json`、`SHA256SUMS`、两个会话/负例响应、写入及召回 mTLS 记录、Gateway 日志。它们含测试对话，目录默认仅操作者可读。

## 6. Guest：连续观察轮换及失效

先从通过的 `processing.json` 取 OpenViking session ID 和 `marker.txt`。在终端 A 持续读该 session；URI 中的 session ID 需做 URL encoding：

```bash
bash "$SCRIPTS/observe_openclaw_lifecycle.sh" \
  '/api/v1/sessions/REPLACE_URLENCODED_SESSION/context?token_budget=128000' 360 'REPLACE_MARKER' \
  > /root/rotation.jsonl 2> /root/rotation-transport.log
python3 "$OC/lifecycle.py" check --trace /root/rotation.jsonl --mode rotation
```

轮换必须观察到 client 或 server serial 变化，并且全程 readiness 和业务读成功。轮换不是重新远程证明。读取归档后 context 不再包含 marker 时，选用经实际 API 验证可稳定读回该 marker 的归档读取路径；不能把断言改成仅 `/health` 成功。

失效测试为每一项新开一份 trace/events 文件。终端 B 在**故障前** `mark --event fault`，执行并保留实际命令结果；故障保持至少 15 秒，再在恢复前 `mark --event recover`，执行恢复操作。观测窗口仍保持相同只读请求：

```bash
python3 "$OC/lifecycle.py" mark --events /root/pause-events.json --event fault
systemctl kill --kill-whom=main --signal=SIGSTOP argus-openclaw-credentials
# 保持故障至少 15 秒，终端 A 持续观察。
python3 "$OC/lifecycle.py" mark --events /root/pause-events.json --event recover
systemctl kill --kill-whom=main --signal=SIGCONT argus-openclaw-credentials
python3 "$OC/lifecycle.py" check --trace /root/pause.jsonl --mode publisher --events /root/pause-events.json
```

| 场景 | 注入与恢复 | 判定 |
| --- | --- | --- |
| 停止 / SIGKILL / SIGSTOP Helper | stop/start、kill/restart、pause/continue；SIGKILL 测试前临时将该 unit 的 Restart 改为 no，完成后撤销本次 drop-in | `--mode publisher`：故障后 3.5 秒窗口内持续停止服务，恢复后成功 |
| Broker 无响应 | SIGSTOP/SIGCONT **Guest 的 argus-openclaw-agent**；不操作 IP1 Server 或 IP2 Agent | `--mode broker`：5 秒探测周期 + 3 秒 deadline + 2.5 秒 lease + 250 ms 客户端检查，预留调度后验收上限 12 秒 |
| Gateway 实例替换 | 保持旧登记，重启 Gateway，证明凭据失效；再 guest-stop、登记新 PID、启动 | 旧实例不能继续取身份，新实例明确重登记后恢复 |
| 业务 TCP 故障 | 暂停本轮专用 TCP 隧道，或用明确限定 IP2:1943 的临时网络规则；恢复后查同一已存在资源 | `--mode network`；只读请求恢复，不能盲目重试不确定写入 |
| 错误客户端 ID | 使用公司已有错误身份负例证书，对 IP2 NGINX 1943 发起请求 | 记录真实 403；TCP timeout 和无证书 TLS 失败均不能替代此项 |
| Entry 删除 | IP1 记录删除的具体 Entry ID、时间与恢复参数，Guest 持续观察 | 记录 Server → Agent 的实际传播时延；不能把删除时刻等同于远端证书瞬时撤销 |

时间标记只是辅助记录，不证明故障确已执行；须附实际命令、`systemctl show` 和 journal。Broker 探测不使用侵入性 HTTP/2 keepalive，而是定期建立有 deadline 的新订阅、验证首个目标 SVID 快照；失败即取消主订阅并清理本地凭据。12 秒是本地 Broker 停顿后的验收目标，不包括控制面 Entry 更新传播延迟。

## 7. IP2：保持既有 PoC 基线，收集服务端证据

只需核对当前 OpenViking 容器/launch ID、NGINX origin、允许的客户端 SPIFFE ID、实际 Workload API socket、身份 serial 和当前策略。收集对应 request ID 的 NGINX/AuthZ 日志及当前 Node/Workload 证明证据；本轮不重建现有容器或重置 SPIRE CA/Agent 数据。

已有 `OutOfDate` PoC 策略若与本地严格模板不同，更新后的 `workload.py` 可显式指定审批后的原始文件及摘要：

```json
"approved_policy_artifact": {
  "path": "/etc/argus-workload/reviewed-poc-policy.rego",
  "sha256": "REPLACE_WITH_SHA256_OF_EXACT_REVIEWED_BYTES"
}
```

这段合入已有 environment JSON；`approved.policy_id` 同样必须指向该已发布策略。`preflight` 仍要求 Trustee GET 回读逐字节一致，文件换行变化也会失败。不给这个字段时仍使用要求 `UpToDate` 的严格模板，策略 ID 含 `poc` 不会自动放行。不得从报告摘要猜测原始 Rego 文件。此功能只使既有批准策略可复现，不自动发布策略或触发 IP2 重启。

最后按 `PASS / FAIL / BLOCKED / NOT_RUN` 汇总：TDVM boot、x509pop Agent、workload SVID、mTLS、真实写入/归档/新会话召回、轮换、失效/恢复。OpenClaw Quote/Trustee appraisal 固定记 NOT_RUN；IP2 按实际证据记录 tcb_status 和批准策略摘要，当前批准策略不要求 TCB UpToDate。
