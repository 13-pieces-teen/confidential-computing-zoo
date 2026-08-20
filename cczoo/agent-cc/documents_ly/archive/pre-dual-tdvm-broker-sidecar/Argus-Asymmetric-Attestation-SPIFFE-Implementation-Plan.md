# Argus Broker Sidecar 实施与远程验证方案

> 对应架构：[Argus 非对称 Attestation-backed SPIFFE 架构](./Argus-Asymmetric-Attestation-SPIFFE-Architecture.md)
>
> 本机范围：代码、Go 单元测试、Linux 交叉编译、Bash/Compose 静态检查
>
> 远程范围：Docker、SPIRE UDS、PID namespace、pidfd、TC-API、完整 A-F 链路

## 1. 实施结论

仓库已从旧的 OpenViking Python TLS/materializer 路径收敛为单一 Broker Sidecar 路径：

- 删除 `adapters/OpenViking/spiffe_server/` 和
  `scripts/entrypoint-spiffe.sh`；
- OpenViking 镜像恢复为直接执行 `openviking-server`；
- 新增外部 `argus_tdx_workload` WorkloadAttestor；
- 新增 Go Broker Sidecar；
- SPIRE Server/Agent 目标 profile 升级到 1.15.2 并启用 Broker Endpoint；
- Launcher 在 TC-API 启动成功后解析真实容器 PID，再启动 Sidecar；
- Registration Entry 同时要求 Docker selectors 和可信 workload selectors；
- Mock Evidence Provider/Trustee 增加 workload ALLOW/DENY 协议。

旧 Python 方案没有运行时开关或回退路径。

## 2. 主要文件

| 路径 | 作用 |
|---|---|
| `core/spire/plugins/argus-tdx-workloadattestor` | PID-reference attestation、Mock EP/Trustee client、可信 selectors |
| `adapters/OpenViking/broker_sidecar` | Broker 订阅、SVID 快照、mTLS、反向代理、pidfd |
| `adapters/OpenViking/scripts/launch_openviking.sh` | TC-API 启动、container ID/PID 解析、Sidecar 启动 |
| `core/spire/runtime/asymmetric/config/openviking-agent.conf.tmpl` | Broker Endpoint 与外部 WorkloadAttestor |
| `core/spire/runtime/asymmetric/scripts/register-workloads.sh` | Broker Entry 与强目标 Entry |
| `core/spire/tests/nodeattestor-mock` | SPIRE 1.15.2 Broker ALLOW/DENY 软件链路 |

## 3. 本机已完成验证

在 Windows checkout 中已经完成：

- `argus-tdx-workloadattestor: go test ./...`；
- `broker_sidecar: go test ./...`；
- Broker Sidecar `GOOS=linux GOARCH=amd64 go build ./...`；
- NodeAttestor 新增 mock endpoints 与命令包测试；
- 修改脚本的 `bash -n`；
- asymmetric 与 nodeattestor-mock 的 `docker compose config --quiet`。

NodeAttestor 全量 `go test ./...` 在 Windows 仍会因既有 Unix absolute-path 和
permission 测试失败；新增 fakeservices/命令包测试已通过。该结果不能替代远程 Linux
测试。

## 4. 远程执行顺序

### 4.1 控制面与 Agent

```bash
cd cczoo/agent-cc
export V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001
export V2_OPENVIKING_ORIGIN=https://openviking.argus.local:1943

sudo env \
  V2_RUNTIME_DIR="$V2_RUNTIME_DIR" \
  V2_OPENVIKING_ORIGIN="$V2_OPENVIKING_ORIGIN" \
  bash core/spire/runtime/asymmetric/scripts/prepare.sh
bash core/spire/runtime/asymmetric/scripts/start-server.sh
bash core/spire/runtime/asymmetric/scripts/start-openclaw-agent.sh
bash core/spire/runtime/asymmetric/scripts/start-openviking-agent.sh
```

### 4.2 TD Guest 构建

```bash
cd cczoo/agent-cc
export OPENVIKING_LAUNCH_ACTION=build
bash adapters/OpenViking/scripts/launch_openviking.sh
```

### 4.3 Server Host 注册

```bash
bash core/spire/runtime/asymmetric/scripts/register-workloads.sh
```

记录命令输出的精确 OpenViking Agent SPIFFE ID。

### 4.4 TD Guest 启动

```bash
export OPENVIKING_WORKLOAD_API_DIR=/run/argus-spire-v2/openviking
export OPENVIKING_BROKER_API_DIR=/run/argus-spire-v2/openviking-broker
export OPENVIKING_AGENT_SPIFFE_ID='spiffe://argus.local/spire/agent/argus_tdx/<exact-id>'
export OPENVIKING_LAUNCH_ACTION=launch
bash adapters/OpenViking/scripts/launch_openviking.sh
```

随后回到 Server Host：

```bash
bash core/spire/runtime/asymmetric/scripts/deploy-v2-guest.sh start-workload
bash core/spire/runtime/asymmetric/scripts/start-openclaw-workload.sh
export OPENVIKING_API_KEY='<non-root OpenViking user key>'
bash core/spire/runtime/asymmetric/scripts/connect-openclaw-plugin.sh
```

## 5. 远程验证

先分别执行隔离的 ALLOW/DENY：

```bash
bash core/spire/tests/nodeattestor-mock/test.sh
M4_WORKLOAD_DECISION=deny bash core/spire/tests/nodeattestor-mock/test.sh
```

再执行正式 profile：

```bash
OPENVIKING_API_KEY='<non-root key>' \
  bash core/spire/runtime/asymmetric/scripts/remote-test.sh all
```

关键检查：

| 检查 | 预期 |
|---|---|
| Broker 自身 Workload API | 只取得 Broker ID |
| ALLOW PID reference | 1943 监听成功后，Sidecar 日志出现 mTLS listener ready |
| DENY PID reference | Mock Trustee 记录 denied、Broker 返回 PermissionDenied、无目标 SVID |
| OpenViking mount | 无 Workload/Broker API socket |
| Sidecar target PID | 等于当前 OpenViking `.State.Pid` |
| OpenClaw mTLS | health/ready 成功 |
| 无客户端证书 | TLS 失败 |
| 目标进程退出 | Sidecar 在 pidfd 事件后退出 |

QEMU TDVM launcher 同时把 1943 转发到宿主机 loopback 和 Docker bridge gateway。
OpenClaw 只通过该 gateway 地址访问 Sidecar；不会把 1943 绑定到宿主机外部网卡。使用
旧 loopback-only QEMU 命令启动的 TDVM 必须重启后再执行正式 profile。

## 6. 结果边界

远程通过后可以声明：

> 在 Mock Evidence Provider 和 Mock Trustee 边界下，SPIRE 1.15.2 Broker
> PID-reference、外部 WorkloadAttestor、强 Entry、目标 SVID 与 Sidecar mTLS
> 链路完成软件级验证。

仍不能声明真实 Quote、QGS、Rekor 或 production Trustee 已通过。后续替换两个 Mock
边界时，Broker Sidecar、Registration Entry 与 OpenViking 非侵入式数据路径不需要回退
到 Python 方案。
