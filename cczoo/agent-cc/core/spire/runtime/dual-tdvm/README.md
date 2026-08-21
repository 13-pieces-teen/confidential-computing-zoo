# 双 TDVM + OpenViking Broker Sidecar

这个 profile 验证以下软件链路：

```text
OpenClaw TDVM                         OpenViking TDVM
OpenClaw -> Guard -> SPIFFE mTLS -> Broker Sidecar -> 127.0.0.1:1933 OpenViking
   |                                      |                 ^
   +-- OpenClaw SPIRE Agent               +-- PID reference-+
                                          +-- OpenViking SPIRE Agent
                                              + Broker API
                                              + argus_tdx_workload

                 Center: SPIRE Server + Mock Trustee
```

两台 TDVM 都通过 `argus_tdx` 完成 Node Attestation。OpenViking 由 TDVM 中既有的
TC-API 和 Registry 启动；OpenViking 本身不挂载 SPIRE socket，也不持有 SVID。
Broker Sidecar 将 TC-API 返回容器对应的宿主机 PID 交给 SPIRE Broker API，目标 PID
通过 Workload Attestation 后，Sidecar 才获得 OpenViking SVID 并监听 `0.0.0.0:1943`。

当前 Evidence Provider 和 Trustee 均为 Mock。本 profile 的通过结论只能表述为
“Mock Evidence Provider + Mock Trustee 软件链路通过”，不能作为真实 Quote/QGS、
Rekor 或生产 Trustee 的验收证据。

## 1. 前置条件

部署主机需要 Docker、Docker Compose、OpenSSL、SSH、Python 3、tar、sed、awk 和
sha256sum。两台 TDVM 均需要：

- `/dev/tdx_guest`、`/usr/local/bin/docker`、curl 和免交互 sudo；
- 能访问中心主机的 SPIRE `18081` 和 Mock Trustee `18443`；
- OpenViking TDVM 已有健康的 `http://127.0.0.1:8000` TC-API 和
  `http://127.0.0.1:5000` Registry；
- OpenClaw TDVM 能通过 `DUAL_OPENVIKING_HOST_ADDRESS:1943` 访问 OpenViking
  TDVM 的 mTLS 转发，但不能访问其回环端口 `1933`。

两个 TDVM 不共享 Docker daemon、Agent data、Workload API 或 Broker API 目录。

## 2. 准备中心和镜像

在部署主机的 `cczoo/agent-cc` 目录执行：

```bash
export PROFILE_DIR="$(pwd)/core/spire/runtime/dual-tdvm"
export DUAL_RUNTIME_DIR=/opt/argus-dual-tdvm-runtime

export DUAL_OPENCLAW_SPIRE_SERVER_ADDRESS=192.0.2.10
export DUAL_OPENVIKING_SPIRE_SERVER_ADDRESS=192.0.2.10
export DUAL_OPENVIKING_TRUSTEE_ADDRESS=192.0.2.10
export DUAL_OPENCLAW_TDVM_INSTANCE_ID=tdvm-openclaw-0001
export DUAL_OPENVIKING_TDVM_INSTANCE_ID=tdvm-openviking-0001
export DUAL_OPENVIKING_ORIGIN=https://openviking.argus.local:1943

sudo -E bash "$PROFILE_DIR/scripts/prepare.sh"
```

`prepare.sh` 使用 SPIRE `1.15.2`，构建 NodeAttestor、WorkloadAttestor、Mock
服务、Broker Sidecar、Guard 和两个 workload 镜像，并将插件 checksum 写入配置。
Go 模块依赖会先下载到 `ARGUS_GO_CACHE_DIR`，正式构建使用只读模块定义与离线缓存。

## 3. 部署两个 Agent

```bash
export DUAL_OPENCLAW_TDVM_SSH_TARGET=tdx@192.0.2.21
export DUAL_OPENVIKING_TDVM_SSH_TARGET=tdx@192.0.2.22
export DUAL_OPENCLAW_TDVM_SSH_PORT=22
export DUAL_OPENVIKING_TDVM_SSH_PORT=22

export DUAL_WORKLOAD_DECISION=deny
bash "$PROFILE_DIR/scripts/start-center.sh"
bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw deploy-agent
bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking deploy-agent
```

OpenClaw Agent 只启用 Docker WorkloadAttestor。OpenViking Agent 额外启用 Broker
Endpoint、`argus_tdx_workload` 与到中心 Mock Trustee 的 mTLS。Agent 保持 root，
Sidecar 保持 `1000:1000`；OpenViking Broker 目录为 `1000:1000/2770`，因此 Agent
创建的 `broker.sock` 必须是 `root:1000/0770`。

从中心取得两个不同的实时 Parent ID：

```bash
docker compose -f "$PROFILE_DIR/compose.yaml" exec -T spire-server \
  /opt/spire/bin/spire-server agent list \
  -socketPath /opt/spire/run/server/api.sock

export DUAL_OPENCLAW_PARENT_ID='spiffe://argus.local/spire/agent/argus_tdx/<openclaw-key-id>'
export DUAL_OPENVIKING_PARENT_ID='spiffe://argus.local/spire/agent/argus_tdx/<openviking-key-id>'
```

## 4. 加载 workload

OpenViking 的 `ov.conf` 含模型/API 凭据，只通过绝对路径传入，不写入仓库：

```bash
export DUAL_OPENVIKING_CONFIG=/absolute/path/to/ov.conf

bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw load-workload
bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking load-workload
```

OpenViking 的 `load-workload` 不重建 TC-API：它把 OpenViking 和 Broker 镜像传入
TDVM，将 OpenViking 源镜像推送到既有本地 Registry，并复制 launch-only 脚本。

OpenViking 的 runtime image config digest 只有在 TC-API 完成镜像转换后才能确定。
因此 `start-workload` 会按固定顺序执行：TC-API 启动 OpenViking、读取运行容器实际
config digest、自动创建三个强 Entry，最后启动 Broker Sidecar。注册不再使用 source
image digest，也不需要人工传入 digest override。`verify.sh` 会分别记录 source、
TC-API runtime、运行容器与 Entry digest，并要求后三者完全一致。

注册脚本只创建本 profile 的三个身份 Entry：

| Entry | SPIFFE ID | 必要 selector |
|---|---|---|
| `dual-openclaw-workload` | `spiffe://argus.local/agent/openclaw` | OpenClaw Parent + Docker label/image/digest |
| `dual-openviking-broker` | `spiffe://argus.local/infra/openviking-broker` | OpenViking Parent + Broker label/image/digest |
| `dual-openviking-target` | `spiffe://argus.local/service/openviking-cmem` | OpenViking Parent + Docker label/image/digest + `verified/workload_id/policy` |

目标 Entry 关闭 X.509-SVID 预取。脚本同时删除旧的弱
`dual-openviking-workload` Entry。

## 5. DENY 阶段

非交互启动必须二选一传入 TC-API token，变量不会写入运行报告：

```bash
export DUAL_TC_API_IDENTITY_TOKEN='<short-lived-token>'
# 或：export DUAL_TC_API_BEARER_TOKEN='<bearer-token>'

if bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking start-workload; then
  echo 'unexpected: DENY launch returned success' >&2
  exit 1
fi

DUAL_EXPECT_WORKLOAD_DECISION=deny \
  bash "$PROFILE_DIR/scripts/verify.sh"
```

首次 `start-workload` 会在 TC-API 启动目标容器后自动执行 `register-workloads.sh`。
Sidecar 只在包含实际 runtime digest 的 Entry 创建完成后启动。

TC-API 应已启动 OpenViking。Mock Trustee 拒绝后，Sidecar 保持运行并等待身份，
但没有目标 SVID、没有 ready 日志且不监听 1943；Mock Trustee metrics 记录
`denied`。空身份快照本身不能区分永久 DENY、Entry 尚未同步或暂时不匹配，
因此 DENY 结论由本轮配置的 Mock Trustee decision 与其 metric 共同确认。

## 6. ALLOW 和跨 TDVM mTLS 阶段

```bash
export DUAL_WORKLOAD_DECISION=allow
bash "$PROFILE_DIR/scripts/start-center.sh"

bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking start-workload

export DUAL_OPENVIKING_HOST_ADDRESS=192.0.2.22
bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw start-workload
bash "$PROFILE_DIR/scripts/verify.sh"
```

从 DENY 切回 ALLOW 后必须再次执行 `start-workload`，以重启 Sidecar 并重新发起
reference attestation；当前阶段不实现自动重新认证。

`verify.sh` 检查三类关系：

- OpenViking 无 SPIRE/SVID mount；Sidecar 的 target PID 等于 OpenViking 实际 PID；
- 无客户端证书失败，临时错误 expected-client ID 的 Sidecar 也拒绝 OpenClaw；
- Guard 返回 ALLOW 后，OpenClaw 使用自己的 SVID 访问 Sidecar `/health=200`；
  OpenClaw 无法访问明文 `1933`。

`/ready` 始终与身份和 mTLS 安全链路分开。默认模式不部署模型依赖，`/ready` 只记录
Application Readiness。需要同时完成应用就绪验证时，在 `load-workload` 前显式启用：

```bash
export DUAL_OPENVIKING_APPLICATION_READY=1
export DUAL_OPENVIKING_OLLAMA_IMAGE='ollama/ollama:<tested-version>'
export DUAL_OPENVIKING_OLLAMA_MODEL=bge-m3

docker pull "$DUAL_OPENVIKING_OLLAMA_IMAGE"
```

模型下载发生在 OpenViking TDVM 内的 Ollama 容器（`ollama pull bge-m3`），因此
部署主机与 TDVM 都必须能访问模型仓库 `registry.ollama.ai`。Intel DMZ 等只允许
经 HTTP 代理出网的环境，通过 `DUAL_OPENVIKING_OLLAMA_EXTRA_ENV` 为容器注入
代理（空格分隔的 `KEY=VALUE` 列表，作为额外 `--env` 传入）：

```bash
export DUAL_OPENVIKING_OLLAMA_EXTRA_ENV='HTTP_PROXY=http://proxy-dmz.intel.com:911 HTTPS_PROXY=http://proxy-dmz.intel.com:911'
```

容器内 `ollama` CLI 也是遵循 `HTTP(S)_PROXY` 的 Go 客户端：脚本会自动注入并合并
`NO_PROXY`（含 `localhost`、`127.0.0.1`、`0.0.0.0`、`::1`、`10.0.0.0/8`、
`172.16.0.0/12`），保证 API 自连与容器网络流量不会被代理劫持；用户不需要也不应
在 `EXTRA_ENV` 里重复设置 `NO_PROXY`。

对应的 `ov.conf` embedding 配置必须使用 `provider=ollama`、模型 `bge-m3`，并将
`api_base` 指向 `http://argus-dual-openviking-ollama:11434/v1`。Profile 会把显式指定
的 Ollama 镜像传入 OpenViking TDVM，在专用 Docker 网络内启动它并准备模型；不发布
Ollama 宿主机端口。启用后 `verify.sh` 默认要求跨 TDVM mTLS `/ready=200`，但该结果
仍单独标记为 Application Readiness，不改变安全链路结论。

默认最后执行 PID 生命周期检查：停止 OpenViking 后，Sidecar 必须通过 pidfd 退出。
因此完整验收结束时 OpenViking 与 Sidecar 处于停止状态；要保留运行状态可在非正式
诊断时设置 `DUAL_VERIFY_TARGET_EXIT=0`，但这不满足完整验收标准。

## 7. 主要配置接口

| 变量 | 默认值 |
|---|---|
| `DUAL_OPENVIKING_GUEST_BROKER_RUN` | `/run/argus-spire-dual/openviking-broker` |
| `DUAL_OPENVIKING_BROKER_IMAGE` | `argus-openviking-broker-sidecar:local` |
| `DUAL_OPENVIKING_SOURCE_IMAGE` | `localhost:5000/openviking:v0.4.8` |
| `DUAL_OPENVIKING_RUNTIME_IMAGE_ID` | `openviking-cmem:latest` |
| `DUAL_OPENVIKING_APPLICATION_READY` | `0`；设为 `1` 时部署并硬验收应用依赖 |
| `DUAL_OPENVIKING_OLLAMA_IMAGE` | 应用就绪模式必填；部署主机上已有的显式 Ollama image tag |
| `DUAL_OPENVIKING_OLLAMA_MODEL` | `bge-m3` |
| `DUAL_OPENVIKING_OLLAMA_API_BASE` | `http://argus-dual-openviking-ollama:11434/v1` |
| `DUAL_OPENVIKING_OLLAMA_EXTRA_ENV` | 空；空格分隔的 `KEY=VALUE` 列表，作为额外 `--env` 传给 Ollama 容器（如 DMZ 环境的 `HTTP_PROXY`/`HTTPS_PROXY`）；`NO_PROXY` 由脚本自动注入，不需要也不应重复设置 |
| `DUAL_EXPECT_APPLICATION_READY` | 默认跟随 `DUAL_OPENVIKING_APPLICATION_READY` |
| `DUAL_OPENVIKING_TRUSTEE_ADDRESS` | `DUAL_OPENVIKING_SPIRE_SERVER_ADDRESS` |
| `DUAL_TDVM_TRUSTEE_PORT` | `18443` |
| `DUAL_WORKLOAD_DECISION` | `allow` |
| `DUAL_TC_API_IDENTITY_TOKEN` / `DUAL_TC_API_BEARER_TOKEN` | 启动时二选一 |

## 8. 状态、停止与报告

```bash
bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw status
bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking status

bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw stop
bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking stop
docker compose -f "$PROFILE_DIR/compose.yaml" down
```

`stop` 只删除本 profile 的容器，不删除 TDVM 磁盘、镜像、Agent data、OpenViking
数据、Ollama 模型 volume 或 OpenClaw volumes。远程执行证据记录在
[`Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md`](../../../../documents_ly/Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md)。
