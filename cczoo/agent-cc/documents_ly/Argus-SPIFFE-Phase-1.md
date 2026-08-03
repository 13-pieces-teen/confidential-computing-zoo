# Argus-SPIFFE Phase 1 本机执行与实现记录

## 1. 文档说明

本文档记录 2026-07-13 在当前 Argus 主机上实际完成的 SPIFFE/SPIRE Phase 1 集成，包括本机环境、与初始方案的差异、真实代码改动、运行时操作、验证结果、回滚方式和已知问题。

本文不是一份仅供参考的部署设想。文档中的路径、容器名称、端口、SPIFFE ID 和验证结果均来自主机：

```text
/home/ying_liu/agent-cc-argus-spiffe
```

本次实施遵循 Phase 1 边界：SPIRE 作为独立 workload identity 平面运行，不修改 Argus TDX Quote 生成、验证或 ALLOW/DENY 逻辑，也不在本阶段启用 OpenClaw 与 OpenViking 之间的 SPIFFE mTLS。

## 2. 最终拓扑

当前 OpenClaw 和 OpenViking 位于同一台 Docker 宿主机，因此使用单 SPIRE Server、单 SPIRE Agent 的 MVP 拓扑：

```text
SPIRE Server 1.15.1
  127.0.0.1:8081
  /tmp/spire-server/private/api.sock
        |
        | join_token NodeAttestor
        v
SPIRE Agent 1.15.1
  /run/spire/sockets/agent.sock
  docker WorkloadAttestor
        |
        +-- docker:label:argus.workload:openclaw
        |      -> spiffe://argus.local/agent/openclaw
        |
        +-- docker:label:argus.workload:openviking-cmem
               -> spiffe://argus.local/service/openviking-cmem
```

SPIRE Server 和 Agent 均直接运行在宿主机，不加入 `core/argus/start_argus.sh`，从而保持身份平面与 Argus 数据面解耦。

## 3. 本机环境基线

执行时确认的主机环境如下：

| 项目 | 实际值 |
|---|---|
| 操作系统 | CentOS Stream 9 |
| 架构 | `x86_64` |
| 执行用户 | `root` |
| Docker Client | `29.3.0` |
| Docker Server | `29.3.0` |
| SPIRE | `1.15.1` |
| SPIRE 安装路径 | `/opt/spire-1.15.1` |
| SPIRE 当前链接 | `/opt/spire -> /opt/spire-1.15.1` |
| Docker socket | `/var/run/docker.sock` |
| Trust domain | `argus.local` |

实施前已有的主要容器：

| 容器 | 镜像 | 运行方式 |
|---|---|---|
| `agentcc-openclaw-sbx-gateway` | `openclaw-sbx:latest` | host network，实际服务端口 `18889` |
| `agentcc-openviking-service` | `localhost:5000/openviking-cmem:latest` | bridge network，发布 `8010:8010` |
| `tc_api-tc-api-1` | `tc_api-tc-api` | 已运行 |
| `tc_api-nginx-1` | 本地镜像 | 已运行 |

## 4. 与初始方案的关键差异

### 4.1 Agent parent ID 不能使用方案中的固定 alias

初始方案尝试通过以下命令指定 Agent ID：

```bash
spire-server token generate \
  -spiffeID spiffe://argus.local/spire/agent/argus-host
```

SPIRE 1.15.1 实际返回：

```text
invalid agent ID: ... path is in the reserved namespace
```

`/spire/agent/...` 是 SPIRE 保留命名空间，不能通过 `-spiffeID` 添加为普通附加 ID。本机改为让 `join_token` NodeAttestor 自动生成 Agent ID：

```text
spiffe://argus.local/spire/agent/join_token/bff746e6-464e-49e0-81bb-ba5f2f8c15d8
```

`register-workloads.sh` 不写死该 UUID，而是通过：

```bash
spire-server agent list -output json
```

结构化读取当前唯一 Agent 的 trust domain 和 path，并动态组成 registration entry 的 `parentID`。如果将来出现多个 Agent，脚本会拒绝自动选择，要求显式设置：

```bash
export ARGUS_AGENT_PARENT='<agent-spiffe-id>'
```

### 4.2 运行时参数以现有容器为准

初始方案中的 Docker 命令是通用示例。本机已有容器的网络、端口、数据卷和 privilege 状态与示例不同，因此重建容器时保留原有实际参数，只追加 Phase 1 所需的四类接入项：

1. 唯一 workload label。
2. `/run/spire/sockets` 挂载。
3. 只读 `spire-agent` CLI 挂载，仅用于 Phase 1 验证。
4. `SPIFFE_ENDPOINT_SOCKET` 环境变量。

没有为了套用示例而改变 OpenClaw/OpenViking 的业务网络、端口和数据目录。

## 5. SPIRE 1.15.1 安装

下载固定版本：

```bash
SPIRE_VERSION=1.15.1
ARCHIVE="spire-${SPIRE_VERSION}-linux-amd64-musl.tar.gz"
URL="https://github.com/spiffe/spire/releases/download/v${SPIRE_VERSION}/${ARCHIVE}"

curl -fL --retry 3 --connect-timeout 20 \
  -o "/tmp/${ARCHIVE}" \
  "$URL"
```

使用 release 中实际存在的 checksum 文件验证：

```bash
curl -fsSL \
  "https://github.com/spiffe/spire/releases/download/v1.15.1/spire-1.15.1-linux-amd64-musl_sha256sum.txt" \
  -o /tmp/spire-1.15.1-linux-amd64-musl_sha256sum.txt

cd /tmp
sha256sum -c spire-1.15.1-linux-amd64-musl_sha256sum.txt
```

实际结果：

```text
spire-1.15.1-linux-amd64-musl.tar.gz: OK
```

安装到 `/opt`：

```bash
mkdir -p /opt/spire-1.15.1.tmp
tar -xzf /tmp/spire-1.15.1-linux-amd64-musl.tar.gz \
  -C /opt/spire-1.15.1.tmp \
  --strip-components=1
mv /opt/spire-1.15.1.tmp /opt/spire-1.15.1
ln -sfn /opt/spire-1.15.1 /opt/spire
```

版本验证：

```text
/opt/spire/bin/spire-server --version -> 1.15.1
/opt/spire/bin/spire-agent --version  -> 1.15.1
```

## 6. 仓库交付物

本次在主机仓库中新增：

```text
core/spire/
├── README.md
├── conf/
│   ├── agent.conf
│   └── server.conf
└── scripts/
    ├── bootstrap-agent.sh
    ├── register-workloads.sh
    └── verify-svid.sh
```

文件权限已确认：

```text
0644 core/spire/README.md
0644 core/spire/conf/agent.conf
0644 core/spire/conf/server.conf
0755 core/spire/scripts/bootstrap-agent.sh
0755 core/spire/scripts/register-workloads.sh
0755 core/spire/scripts/verify-svid.sh
```

另外修改：

```text
adapters/OpenClaw/scripts/run-sbx.sh
adapters/OpenViking/scripts/launch_openviking.sh
```

## 7. SPIRE Server 与 Agent 配置

### 7.1 Server

`core/spire/conf/server.conf` 的实际参数：

| 配置 | 值 |
|---|---|
| bind address | `127.0.0.1` |
| bind port | `8081` |
| private API socket | `/tmp/spire-server/private/api.sock` |
| trust domain | `argus.local` |
| data directory | `/var/lib/spire/server` |
| default X.509-SVID TTL | `10m` |
| DataStore | SQLite3 |
| NodeAttestor | `join_token` |
| KeyManager | `disk` |
| UpstreamAuthority | `disk` |

### 7.2 Agent

`core/spire/conf/agent.conf` 的实际参数：

| 配置 | 值 |
|---|---|
| data directory | `/var/lib/spire/agent` |
| server | `127.0.0.1:8081` |
| Workload API socket | `/run/spire/sockets/agent.sock` |
| bootstrap bundle | `/etc/spire/bootstrap.crt` |
| trust domain | `argus.local` |
| NodeAttestor | `join_token` |
| KeyManager | `disk` |
| WorkloadAttestor | `docker`、`unix` |
| Docker socket | `unix:///var/run/docker.sock` |

配置已使用 SPIRE 自带命令校验：

```bash
/opt/spire/bin/spire-server validate \
  -config core/spire/conf/server.conf
/opt/spire/bin/spire-agent validate \
  -config core/spire/conf/agent.conf
```

实际结果：

```text
SPIRE server configuration file is valid.
SPIRE agent configuration file is valid.
```

## 8. Bootstrap 实现与执行

`core/spire/scripts/bootstrap-agent.sh` 负责以下操作：

1. 检查必须以 root 执行。
2. 检查 SPIRE、OpenSSL 和 Python 3 可用。
3. 创建 Server、Agent、socket 和日志目录。
4. 首次执行时生成开发用 EC P-256 Root CA。
5. 校验 Server 和 Agent 配置。
6. 启动 Server 并等待健康。
7. 从 Server 导出 bootstrap bundle 到 `/etc/spire/bootstrap.crt`。
8. Agent 首次启动时生成一次性 join token。
9. 启动 Agent 并等待健康。
10. 输出已鉴别 Agent 列表。

运行时目录：

```text
/var/lib/spire/server
/var/lib/spire/agent
/etc/spire/bootstrap.crt
/run/spire/sockets/agent.sock
/var/log/spire/server.log
/var/log/spire/agent.log
/run/spire/server.pid
/run/spire/agent.pid
```

开发 CA 在首次 bootstrap 时生成：

```text
/var/lib/spire/server/upstream-ca.key
/var/lib/spire/server/upstream-ca.crt
```

私钥权限为 `0600`。CA 私钥、join token、datastore、Agent key 和签发的 workload 私钥均未写入 Git 仓库。

执行命令：

```bash
cd /home/ying_liu/agent-cc-argus-spiffe
core/spire/scripts/bootstrap-agent.sh
```

最终结果：

```text
SPIRE Server and Agent are healthy.
Found 1 attested agent
Attestation type: join_token
Agent version: 1.15.1
```

脚本已做幂等验证。再次运行时不会重新创建 CA、Server 状态或 Agent 身份，而是验证配置和健康状态。

当前实现使用 `nohup` 启动 Server 和 Agent，没有创建 systemd unit。主机重启后需要重新运行：

```bash
core/spire/scripts/bootstrap-agent.sh
```

Agent 已有持久状态时不再使用新的 join token，而是使用 `/var/lib/spire/agent` 中的已有身份恢复连接。

## 9. Workload registration entries

执行：

```bash
core/spire/scripts/register-workloads.sh
```

脚本先读取 Agent JSON：

```bash
/opt/spire/bin/spire-server agent list \
  -socketPath /tmp/spire-server/private/api.sock \
  -output json
```

本机最终 parent ID：

```text
spiffe://argus.local/spire/agent/join_token/bff746e6-464e-49e0-81bb-ba5f2f8c15d8
```

创建的 entries：

| Workload | SPIFFE ID | Selector | X.509-SVID TTL |
|---|---|---|---|
| OpenClaw gateway | `spiffe://argus.local/agent/openclaw` | `docker:label:argus.workload:openclaw` | 600 秒 |
| OpenViking CMEM | `spiffe://argus.local/service/openviking-cmem` | `docker:label:argus.workload:openviking-cmem` | 600 秒 |

当前 entry IDs：

```text
OpenClaw:   e5fa9050-9832-4c7c-816f-e09022d8a399
OpenViking: ca8b02b6-febc-4960-81a0-f15e7e986cb8
```

注册脚本同样幂等。再次执行时输出：

```text
Registration entry already exists: spiffe://argus.local/agent/openclaw
Registration entry already exists: spiffe://argus.local/service/openviking-cmem
Found 2 entries
```

## 10. OpenClaw 接入实现

修改文件：

```text
adapters/OpenClaw/scripts/run-sbx.sh
```

新增可覆盖变量：

```bash
SPIRE_ENABLED="${SPIRE_ENABLED:-1}"
SPIRE_HOME="${SPIRE_HOME:-/opt/spire}"
SPIRE_SOCKET_DIR="${SPIRE_SOCKET_DIR:-/run/spire/sockets}"
```

在最终 gateway `docker run` 前检查：

```bash
[[ -S "$SPIRE_SOCKET_DIR/agent.sock" ]]
[[ -x "$SPIRE_HOME/bin/spire-agent" ]]
```

实际注入参数：

```bash
--label "argus.workload=openclaw"
-v "$SPIRE_SOCKET_DIR:$SPIRE_SOCKET_DIR"
-v "$SPIRE_HOME/bin/spire-agent:/usr/local/bin/spire-agent:ro"
-e "SPIFFE_ENDPOINT_SOCKET=unix://$SPIRE_SOCKET_DIR/agent.sock"
```

只为 OpenClaw gateway 签发 Phase 1 身份。gateway 通过 Docker socket 创建的 sandbox sibling containers 没有复用 gateway 私钥，也没有自动获得 gateway 的 SPIFFE ID。

### 10.1 本机运行时重建

实际容器与通用脚本默认值存在差异，因此重建时保留原容器的：

- host network。
- Docker named volumes。
- Docker socket。
- supplemental Docker GID。
- proxy/no_proxy 环境。
- gateway port `18889`。
- 原 gateway token；执行文档不记录其值。

旧容器先停止并重命名，没有删除：

```text
agentcc-openclaw-sbx-gateway-pre-spire-20260713143716
```

新容器继续使用原名：

```text
agentcc-openclaw-sbx-gateway
```

SPIRE 接入验证：

```bash
docker inspect agentcc-openclaw-sbx-gateway \
  --format '{{index .Config.Labels "argus.workload"}}'

docker exec agentcc-openclaw-sbx-gateway \
  spire-agent api fetch x509 \
  -socketPath /run/spire/sockets/agent.sock
```

预期并已得到：

```text
argus.workload=openclaw
SPIFFE ID: spiffe://argus.local/agent/openclaw
```

## 11. OpenViking 接入实现

修改文件：

```text
adapters/OpenViking/scripts/launch_openviking.sh
```

新增变量：

```bash
SPIRE_HOME="${SPIRE_HOME:-/opt/spire}"
SPIRE_SOCKET_DIR="${SPIRE_SOCKET_DIR:-/run/spire/sockets}"
```

`prepare_openviking_storage()` 现在无论是否启用 LUKS，都会生成包含 SPIRE 参数的最终 `OPENVIKING_DOCKER_CMD`：

```bash
--label argus.workload=openviking-cmem
-e SPIFFE_ENDPOINT_SOCKET=unix:///run/spire/sockets/agent.sock
-v /run/spire/sockets:/run/spire/sockets
-v /opt/spire/bin/spire-agent:/usr/local/bin/spire-agent:ro
```

如果启用 LUKS，再追加原有加密数据目录挂载。这样 SPIRE 参数进入最终提交给 Docker daemon 的 `dockercmd`，而不是只存在于 TC API 容器中。

参数保持为无空格 token，兼容当前 TC API 中对 `dockercmd.strip().split(" ")` 的处理方式。

### 11.1 本机运行时重建

本机现有 OpenViking 实际使用 bridge network 和 `8010:8010` 端口映射，而不是通用文档中的 host network 示例。重建时保留：

```text
image: localhost:5000/openviking-cmem:latest
port: 8010:8010
data: /tmp/agent-cc-openviking-data:/mnt/encrypted/openviking
command: python3 /app/openviking_service.py --serve
```

旧容器保留为：

```text
agentcc-openviking-service-pre-spire-20260713143740
```

新容器继续使用：

```text
agentcc-openviking-service
```

实际身份验证结果：

```text
argus.workload=openviking-cmem
SPIFFE ID: spiffe://argus.local/service/openviking-cmem
```

## 12. 验收脚本

执行：

```bash
core/spire/scripts/verify-svid.sh
```

脚本使用实际 workload 容器执行以下检查：

1. OpenClaw 容器的 `argus.workload` 必须为 `openclaw`。
2. OpenViking 容器的 `argus.workload` 必须为 `openviking-cmem`。
3. 在 OpenClaw 内通过 Workload API 获取 X.509-SVID。
4. 在 OpenViking 内通过 Workload API 获取 X.509-SVID。
5. OpenClaw 输出不得包含 OpenViking SPIFFE ID。
6. OpenViking 输出不得包含 OpenClaw SPIFFE ID。
7. 完全没有 workload label 的临时容器不得获得身份。
8. 使用未注册 label 的临时容器不得获得身份。
9. 使用错误 label `openclaw-invalid` 的临时容器不得获得身份。

正向结果：

```text
OpenClaw:
  spiffe://argus.local/agent/openclaw

OpenViking:
  spiffe://argus.local/service/openviking-cmem
```

负向容器的 Workload API 结果：

```text
rpc error: code = PermissionDenied desc = no identity issued
```

完整脚本结果：

```text
SVID identity, isolation, and negative selector checks passed.
```

验证必须在实际容器中完成。宿主机直接运行 `spire-agent api fetch` 不能代替 workload attestation 验证。

## 13. SVID 自动轮换验证

两个 workload entry 的 X.509-SVID TTL 为 600 秒。通过 OpenViking 容器中的 watch 命令观察轮换：

```bash
docker exec agentcc-openviking-service \
  spire-agent api watch \
  -socketPath /run/spire/sockets/agent.sock
```

同一 workload 的实际证书时间发生变化：

```text
轮换前：
  SVID Valid After: 2026-07-13 06:33:26 UTC
  SVID Valid Until: 2026-07-13 06:43:36 UTC

轮换后：
  SVID Valid After: 2026-07-13 06:43:09 UTC
  SVID Valid Until: 2026-07-13 06:53:19 UTC
```

这证明 Agent 在应用不重启的情况下为同一个 workload 自动更新 X.509-SVID。

验证结束后检查并清理了测试用 `spire-agent api watch` 子进程，主 SPIRE Agent 健康状态不受影响。

## 14. 最终健康状态

### 14.1 SPIRE

```bash
/opt/spire/bin/spire-server healthcheck \
  -socketPath /tmp/spire-server/private/api.sock

/opt/spire/bin/spire-agent healthcheck \
  -socketPath /run/spire/sockets/agent.sock
```

实际结果：

```text
Server is healthy.
Agent is healthy.
```

### 14.2 Argus 与 workload 服务

实施后检查：

| 服务 | URL | 结果 |
|---|---|---|
| Argus Evidence Provider | `http://127.0.0.1:8008/health` | HTTP 200 |
| Argus Guard | `http://127.0.0.1:8007/health` | HTTP 200 |
| OpenViking | `http://127.0.0.1:8010/health` | HTTP 200 |
| OpenClaw gateway | `http://127.0.0.1:18889/healthz` | HTTP 200 |

这说明 SPIRE 接入没有改变或中断原 Argus `8007/8008` 数据面服务。

## 15. 验收标准对应结果

| 编号 | 验收条件 | 结果 |
|---|---|---|
| 1 | SPIRE Server 配置校验并健康 | 通过 |
| 2 | Agent 使用 join token 首次 bootstrap | 通过 |
| 3 | Server 可列出预期 Agent | 通过，1 个 join-token Agent |
| 4 | 存在两个 workload entries | 通过 |
| 5 | OpenClaw 获得目标 SPIFFE ID | 通过 |
| 6 | OpenViking 获得目标 SPIFFE ID | 通过 |
| 7 | 两个 workload 不获得对方身份 | 通过 |
| 8 | 无 label 或错误 label 不获身份 | 通过 |
| 9 | 未注册第三容器不获身份 | 通过 |
| 10 | 观察到 X.509-SVID 自动轮换 | 通过 |

Phase 1 的身份签发链路已经在本机完整走通。

## 16. 已知问题与本机特有注意事项

### 16.1 OpenClaw Docker health 状态

当前 OpenClaw 业务端点：

```text
http://127.0.0.1:18889/healthz -> HTTP 200
```

但 Docker 将容器标记为 `unhealthy`。原因是镜像继承的 healthcheck 检查容器端口 `18789`，而本机已有配置实际让 gateway 监听 `18889`。该问题在 SPIRE 接入前已经存在，且与 SPIFFE 身份签发无关。

本次没有顺带修改 OpenClaw 业务端口或镜像 healthcheck，避免将无关行为变更混入 Phase 1。后续应单独统一：

- gateway 实际监听端口；
- Docker healthcheck 目标端口；
- `run-sbx.sh` 的 host/container 端口语义。

### 16.2 当前不是 systemd 部署

SPIRE Server 和 Agent 当前由 bootstrap 脚本通过 `nohup` 启动。主机重启后运行：

```bash
cd /home/ying_liu/agent-cc-argus-spiffe
core/spire/scripts/bootstrap-agent.sh
core/spire/scripts/register-workloads.sh
```

正式长期部署建议在后续变更中增加独立的 systemd units，并设置清晰的启动顺序：

```text
Docker -> SPIRE Server -> SPIRE Agent -> workloads
```

### 16.3 CLI 挂载只用于 Phase 1

当前将：

```text
/opt/spire/bin/spire-agent
```

只读挂载为：

```text
/usr/local/bin/spire-agent
```

目的是在容器内执行 `api fetch` 和 `api watch` 验证。应用接入正式 SPIFFE Workload API client 后应移除该 CLI 挂载。

### 16.4 Agent 不支持周期性 re-attestation

当前 join-token Agent 输出：

```text
Can re-attest: false
```

这符合 Phase 1 范围。周期性 re-attestation、TDX NodeAttestor 和硬件绑定均留待后续阶段。

## 17. 安全边界

本次没有提交或复制以下材料到仓库：

- 开发 CA 私钥。
- join token。
- SPIRE datastore。
- Server/Agent key material。
- 已签发 SVID 私钥。
- OpenClaw gateway token。

Workload API socket 只以 Unix domain socket 形式挂载，没有转换为 TCP 服务。

OpenClaw 和 OpenViking 使用不同 label、不同 entry 和不同 SPIFFE ID。没有复制、共享或持久化任一 workload 的 SVID 私钥。

本阶段的身份只能证明 Docker WorkloadAttestor 基于本机容器 label 识别了 workload，不代表身份已经与 TDX Quote、镜像摘要、launch ID 或 instance ID 形成硬件级绑定。

## 18. 非破坏性回滚

旧容器仍以 stopped 状态保留：

```text
agentcc-openclaw-sbx-gateway-pre-spire-20260713143716
agentcc-openviking-service-pre-spire-20260713143740
```

如需回滚 OpenClaw，可保留当前 SPIRE 容器作为额外备份，再恢复旧容器：

```bash
docker stop agentcc-openclaw-sbx-gateway
docker rename \
  agentcc-openclaw-sbx-gateway \
  agentcc-openclaw-sbx-gateway-spire-current

docker rename \
  agentcc-openclaw-sbx-gateway-pre-spire-20260713143716 \
  agentcc-openclaw-sbx-gateway

docker start agentcc-openclaw-sbx-gateway
```

OpenViking 回滚：

```bash
docker stop agentcc-openviking-service
docker rename \
  agentcc-openviking-service \
  agentcc-openviking-service-spire-current

docker rename \
  agentcc-openviking-service-pre-spire-20260713143740 \
  agentcc-openviking-service

docker start agentcc-openviking-service
```

这些命令不删除容器或数据卷。确认新环境长期稳定前，不应删除旧容器。

SPIRE 停止时可使用记录的 PID：

```bash
kill "$(cat /run/spire/agent.pid)"
kill "$(cat /run/spire/server.pid)"
```

不要删除 `/var/lib/spire`，除非明确需要重建整个 trust domain 状态并已确认影响。

## 19. 日常检查命令

```bash
# Server/Agent 健康
/opt/spire/bin/spire-server healthcheck \
  -socketPath /tmp/spire-server/private/api.sock
/opt/spire/bin/spire-agent healthcheck \
  -socketPath /run/spire/sockets/agent.sock

# Agent 与 entries
/opt/spire/bin/spire-server agent list \
  -socketPath /tmp/spire-server/private/api.sock
/opt/spire/bin/spire-server entry show \
  -socketPath /tmp/spire-server/private/api.sock

# 完整 Phase 1 验收
cd /home/ying_liu/agent-cc-argus-spiffe
core/spire/scripts/verify-svid.sh

# 日志
tail -f /var/log/spire/server.log
tail -f /var/log/spire/agent.log
```

## 20. 代码验证记录

已执行：

```bash
bash -n core/spire/scripts/bootstrap-agent.sh
bash -n core/spire/scripts/register-workloads.sh
bash -n core/spire/scripts/verify-svid.sh
bash -n adapters/OpenClaw/scripts/run-sbx.sh
bash -n adapters/OpenViking/scripts/launch_openviking.sh
git diff --check
```

以上检查通过。主机未安装 `shellcheck`，因此没有执行 shellcheck 静态分析。

## 21. Phase 1 结论

本机已经实现并验证以下完整链路：

```text
SPIRE Server
  -> join-token SPIRE Agent
  -> Docker WorkloadAttestor
  -> workload-specific selector
  -> distinct X.509-SVID
  -> automatic SVID rotation
```

当前状态满足 Argus-SPIFFE Phase 1 的目标：SPIRE 身份平面已独立运行，OpenClaw 和 OpenViking 能从同一宿主机 Agent 获取彼此隔离、可自动轮换的 X.509-SVID，同时未改变现有 Argus Quote 和 Guard 判定路径。

后续 Phase 2 应在此基础上接入正式 Workload API client 或 SPIFFE Helper，建立 OpenClaw 与 OpenViking 的 SPIFFE mTLS，并让 Argus Guard 使用已验证的 peer SPIFFE ID。
