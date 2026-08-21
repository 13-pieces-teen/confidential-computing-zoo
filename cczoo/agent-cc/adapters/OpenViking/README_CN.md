# OpenViking Ingress Broker 适配器

本适配器不修改 OpenViking 上游源码。官方 OpenViking 进程保持原样运行，由独立
Ingress Broker 代表其真实进程向 SPIRE 请求目标身份。

## 运行边界

- OpenViking 仅监听 TD Guest 回环地址的 1933 端口。
- OpenViking 不挂载 Workload API 或 Broker API socket。
- OpenViking 不接收 SVID 和私钥。
- 启动脚本把 OpenViking 容器的真实宿主机 PID 交给 Ingress Broker。
- Broker 通过 SPIRE Broker API 获取目标身份，只在内存中持有密钥材料，并对外
  监听 mTLS 1943。
- Broker 只接受 `spiffe://argus.local/agent/openclaw`，认证后将请求转发到
  OpenViking 回环端口。

Broker 不配置自动重启，因为新容器不能继续使用旧的目标 PID。

## 当前部署入口

当前唯一受维护的集成部署是[双 TDVM Runtime](../../core/spire/runtime/dual-tdvm/README.md)。
其中 `prepare.sh` 构建官方 OpenViking runtime 镜像和 Ingress Broker；
`manage-guest.sh` 加载镜像，通过 TDVM 中既有 TC API 启动 OpenViking，按实际
runtime digest 注册身份，再以返回的真实 PID 启动 Broker。

`scripts/launch_openviking.sh` 是该 Profile 复用的适配器 launcher，不是第二套
部署 Profile，也不会自行创建 Registration Entry。

## 验证

dual-TDVM 的 `verify.sh` 检查：

- OpenViking 没有 SPIRE 或 SVID mount；
- Ingress Broker 引用当前 OpenViking 宿主机 PID；
- 只有 Broker 挂载 Workload API 与 Broker API socket；
- Broker 只接受精确 OpenClaw SPIFFE ID，缺少证书或错误身份均失败；
- OpenViking 退出后 Broker 通过 pidfd 监控退出。

本机 checkout 可以运行 Go 单元测试和 Linux 交叉编译。Docker、Broker UDS 权限、
PID namespace、`pidfd_open` 和完整 SPIRE 颁发仍属于 Linux/TDVM 验证项。
