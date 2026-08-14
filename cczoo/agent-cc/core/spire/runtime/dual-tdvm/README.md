# OpenClaw + OpenViking 双 TDVM 运行配置

这个 profile 实现以下目标拓扑：

- 中心主机：SPIRE Server + Mock Trustee；
- OpenClaw TDVM：Mock Evidence Provider + `argus_tdx` SPIRE Agent + Guard + OpenClaw；
- OpenViking TDVM：Mock Evidence Provider + `argus_tdx` SPIRE Agent + OpenViking；
- OpenClaw 与 OpenViking 分别使用自己的 Agent data 和 Workload API socket。

当前 Evidence Provider 和 Trustee 都是 Mock。该配置可验证双 TDVM 部署、Node
Attestation 协议、workload identity、Guard 和 SPIFFE mTLS 链路，但不能作为真实
TDX Quote/QGS 或生产 Trustee 验收证据。

## 1. 前置条件

在一台可访问两个 TDVM 的 Linux 部署主机上准备：

- 当前仓库；
- Docker 与 Docker Compose；
- OpenSSL、SSH、Python 3、tar、sed、awk、sha256sum；
- 两台独立 TDVM，均有 `/dev/tdx_guest`、`/usr/local/bin/docker`、curl 和可免交互 sudo；
- OpenClaw TDVM 能访问 OpenViking TDVM 的 TCP 1943；
- 两台 TDVM 都能访问中心主机的 SPIRE TCP 18081。

两个 TDVM 不得共享 Docker daemon、Workload API 目录或 SPIRE Agent 数据目录。

## 2. 准备中心配置和镜像

以下命令在部署主机的 `cczoo/agent-cc` 目录执行：

```bash
export PROFILE_DIR="$(pwd)/core/spire/runtime/dual-tdvm"
export DUAL_RUNTIME_DIR=/opt/argus-dual-tdvm-runtime

# 必须是两台 TDVM 从各自网络看到的中心 SPIRE 地址。
export DUAL_OPENCLAW_SPIRE_SERVER_ADDRESS=192.0.2.10
export DUAL_OPENVIKING_SPIRE_SERVER_ADDRESS=192.0.2.10

export DUAL_OPENCLAW_TDVM_INSTANCE_ID=tdvm-openclaw-0001
export DUAL_OPENVIKING_TDVM_INSTANCE_ID=tdvm-openviking-0001
export DUAL_OPENVIKING_ORIGIN=https://openviking.argus.local:1943

sudo -E bash "$PROFILE_DIR/scripts/prepare.sh"
bash "$PROFILE_DIR/scripts/start-center.sh"
```

`prepare.sh` 默认构建 Guard、OpenClaw、OpenClaw sandbox 和 OpenViking 镜像。
如镜像已由外部流水线提供，可分别使用 `DUAL_BUILD_GUARD=0`、
`DUAL_BUILD_OPENCLAW=0` 或 `DUAL_BUILD_OPENVIKING=0` 跳过对应构建，但运行
`load-workload` 前部署主机必须已有相同 tag 的镜像。

## 3. 部署两台 TDVM 的 SPIRE Agent

```bash
export DUAL_OPENCLAW_TDVM_SSH_TARGET=tdx@192.0.2.21
export DUAL_OPENVIKING_TDVM_SSH_TARGET=tdx@192.0.2.22

# 非 22 端口时分别设置这两个变量。
export DUAL_OPENCLAW_TDVM_SSH_PORT=22
export DUAL_OPENVIKING_TDVM_SSH_PORT=22

bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw deploy-agent
bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking deploy-agent
```

两个命令分别向目标 TDVM 传输 SPIRE Agent 和 Mock Evidence Provider 镜像，写入
不同配置、数据目录和 socket 目录，然后独立完成 `argus_tdx` Node Attestation。

从中心查看两个 Agent：

```bash
docker compose -f "$PROFILE_DIR/compose.yaml" exec -T spire-server \
  /opt/spire/bin/spire-server agent list \
  -socketPath /opt/spire/run/server/api.sock
```

核对 instance ID 和 attestation 记录后，显式保存两个不同 Parent ID：

```bash
export DUAL_OPENCLAW_PARENT_ID='spiffe://argus.local/spire/agent/argus_tdx/<openclaw-key-id>'
export DUAL_OPENVIKING_PARENT_ID='spiffe://argus.local/spire/agent/argus_tdx/<openviking-key-id>'
```

## 4. 加载 workload 并注册身份

先准备 OpenViking 官方 `ov.conf`。该文件包含模型和 API 凭据，不进入仓库：

```bash
export DUAL_OPENVIKING_CONFIG=/absolute/path/to/ov.conf

bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw load-workload
bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking load-workload
bash "$PROFILE_DIR/scripts/register-workloads.sh"
```

registration 从两台 TDVM 内读取实际 image config digest，并分别绑定：

| workload | SPIFFE ID | Parent |
|---|---|---|
| OpenClaw | `spiffe://argus.local/agent/openclaw` | OpenClaw TDVM Agent |
| OpenViking | `spiffe://argus.local/service/openviking-cmem` | OpenViking TDVM Agent |

镜像重建或 tag 指向的新 config digest 上线前，必须重新执行注册脚本。

## 5. 启动 workload

`DUAL_OPENVIKING_HOST_ADDRESS` 必须是 OpenClaw TDVM 能直接访问的 OpenViking
TDVM 地址，而不是中心主机地址：

```bash
export DUAL_OPENVIKING_HOST_ADDRESS=192.0.2.22

bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking start-workload
bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw start-workload
```

OpenViking 默认发布 `0.0.0.0:1943`。OpenClaw TDVM 内的 Docker DNS 将 Guard
解析为本地容器，并将 `openviking.argus.local` 映射到
`DUAL_OPENVIKING_HOST_ADDRESS`。

Guard 不是业务代理。OpenClaw 的 SPIFFE preload 只对配置的 OpenViking origin
执行以下逻辑：

1. 向 OpenClaw TDVM 内的 Guard 发送 caller、target、operation 和 data class；
2. Guard 返回 `DENY`、超时或不可用时，OpenClaw 不发送业务请求；
3. Guard 返回有效 `ALLOW` 时，OpenClaw 读取自己的 X.509-SVID；
4. OpenClaw 直接连接 OpenViking `:1943`，双方校验精确 SPIFFE ID。

因此业务链路是 `OpenClaw -> Guard (授权)`，随后
`OpenClaw -> OpenViking (SPIFFE mTLS)`，不是 `Guard -> OpenViking`。

## 6. 远端验收

```bash
bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw status
bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking status
bash "$PROFILE_DIR/scripts/verify.sh"
```

`verify.sh` 验证：

- 两个 SSH 目标都有 `/dev/tdx_guest`；
- 两个 SPIRE Agent 和 workload 都在各自 TDVM 中运行；
- 两个 workload 取得预期 SVID，registration 使用不同 Parent ID；
- OpenViking 拒绝无客户端证书和错误客户端 SPIFFE ID；
- OpenClaw 经本地 Guard 授权后，直接通过 SPIFFE mTLS 访问 OpenViking
  `/health` 和 `/ready`。

通过上述检查仍只代表 Mock RA 阶段完成。真实 TDX 验收还需把两个
Mock Evidence Provider 替换为真实 Quote/QGS provider，把 Mock Trustee 替换为生产
Trustee，并重新验证 measurement policy、binding 和 replay 失败路径。

## 7. 持久化和停止

默认 Guest 路径：

| TDVM | Agent data | Workload API | workload data |
|---|---|---|---|
| OpenClaw | `/var/lib/argus-spire-dual/openclaw-agent` | `/run/argus-spire-dual/openclaw` | 两个 `argus-dual-openclaw-*` Docker volume |
| OpenViking | `/var/lib/argus-spire-dual/openviking-agent` | `/run/argus-spire-dual/openviking` | `/var/lib/argus-spire-dual/openviking-state` |

按 TDVM 停止本 profile 容器但保留数据：

```bash
bash "$PROFILE_DIR/scripts/manage-guest.sh" openclaw stop
bash "$PROFILE_DIR/scripts/manage-guest.sh" openviking stop
docker compose -f "$PROFILE_DIR/compose.yaml" down
```

这些命令不删除 Agent data、OpenViking 数据或 OpenClaw Docker volumes。
