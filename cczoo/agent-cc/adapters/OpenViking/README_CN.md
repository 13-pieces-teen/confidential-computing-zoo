# OpenViking Broker Sidecar 适配器

本适配器不修改 OpenViking Python 源码。官方 OpenViking 进程保持原样运行，
由独立的 Broker Sidecar 代表它向 SPIRE 请求目标身份。

## 运行边界

- OpenViking 仅监听 TD Guest 回环地址的 1933 端口。
- OpenViking 不挂载 Workload API 或 Broker API socket。
- OpenViking 不接收 SVID 和私钥。
- 启动脚本把 OpenViking 容器的真实宿主机 PID 交给 Broker Sidecar。
- Sidecar 通过 SPIRE Broker API 请求该 PID 对应的目标身份，在内存中持有
  SVID 与私钥，并对外监听 mTLS 1943 端口。
- Sidecar 只接受
  `spiffe://argus.local/agent/openclaw`，验证通过后把请求转发到
  OpenViking 的回环端口。

Sidecar 不配置自动重启，因为新容器不能继续使用旧的目标 PID。

## 准备 OpenViking

选择固定 digest 的 OpenViking 镜像，并准备正常的模型和 API 配置：

```bash
export OPENVIKING_LUKS_MOUNT_ROOT="<mounted-storage-path>"
export OPENVIKING_LUKS_SUBDIR=openviking
OPENVIKING_HOST_DATA_DIR="${OPENVIKING_LUKS_MOUNT_ROOT}/${OPENVIKING_LUKS_SUBDIR}"
mkdir -p "$OPENVIKING_HOST_DATA_DIR"
cp configs/ov.conf.example "$OPENVIKING_HOST_DATA_DIR/ov.conf"
chmod 700 "$OPENVIKING_HOST_DATA_DIR"
chmod 600 "$OPENVIKING_HOST_DATA_DIR/ov.conf"

export OPENVIKING_VERSION="<tested-version>"
export OPENVIKING_BASE="ghcr.io/volcengine/openviking@sha256:<digest>"
```

## 构建、注册与启动

先在 TD Guest 中构建 OpenViking 与 Broker Sidecar 镜像：

```bash
export OPENVIKING_LAUNCH_ACTION=build
bash scripts/launch_openviking.sh
```

随后在 SPIRE Server 所在主机创建 Broker Entry 和 OpenViking 目标 Entry。
对于 asymmetric profile：

```bash
bash ../../core/spire/runtime/asymmetric/scripts/register-workloads.sh
```

该命令会输出 OpenViking Agent 的精确 SPIFFE ID。回到 TD Guest，使用该
值启动：

```bash
export OPENVIKING_WORKLOAD_API_DIR=/run/argus-spire-v2/openviking
export OPENVIKING_BROKER_API_DIR=/run/argus-spire-v2/openviking-broker
export OPENVIKING_AGENT_SPIFFE_ID='spiffe://argus.local/spire/agent/argus_tdx/<exact-id>'
export OPENVIKING_LAUNCH_ACTION=launch
bash scripts/launch_openviking.sh
```

启动脚本先通过 TC API 启动 OpenViking，等待启动结果，从唯一的
`container_ID` 解析真实宿主机 PID，然后使用该 PID 启动 Sidecar。

TC API 会把默认的 `IMAGE_ID=openviking-cmem` 重新标记为
`openviking-cmem:latest`。asymmetric 注册脚本使用这个运行时 image ID，
同时单独固定源镜像的 config digest。

## 远程验证

以下检查需要在远程 Linux/TDVM 环境执行：

```bash
docker inspect agentcc-openviking-service +  --format '{{.State.Pid}} {{json .Mounts}}'
docker inspect agentcc-openviking-broker-sidecar +  --format '{{json .Config.Cmd}} {{json .Mounts}}'
docker logs agentcc-openviking-broker-sidecar
```

预期结果：

- OpenViking 容器没有任何 SPIRE socket 挂载；
- Sidecar 命令中的目标 PID 等于当前 OpenViking 宿主机 PID；
- Sidecar 挂载 Workload API 与 Broker API 两个 socket 目录；
- Sidecar 日志显示已经收到目标身份并开始监听 mTLS。

本机 Windows checkout 可以运行 Go 单元测试、静态检查和 Linux
交叉编译。Docker、Broker UDS 权限、PID namespace、`pidfd_open` 与完整
SPIRE 颁发链路由远程 Linux/TDVM 环境验证。
