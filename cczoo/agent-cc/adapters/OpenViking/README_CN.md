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

## 部署状态

本目录只包含适配器实现和 launcher，不是 SPIRE 集成部署，也不会自行创建
Registration Entry。Workload Attestation阶段不在当前可信身份运行链内。

## 验证

适配器单元测试只检查本地合同。未来的集成运行时还必须检查：

- OpenViking 没有 SPIRE 或 SVID mount；
- Ingress Broker 引用当前 OpenViking 宿主机 PID；
- 只有 Broker 挂载 Workload API 与 Broker API socket；
- Broker 只接受精确 OpenClaw SPIFFE ID，缺少证书或错误身份均失败；
- OpenViking 退出后 Broker 通过 pidfd 监控退出。

本机 checkout 可以运行 Go 单元测试和 Linux 交叉编译。Docker、Broker UDS 权限、
PID namespace、`pidfd_open` 和完整 SPIRE 颁发仍属于 Linux/TDVM 验证项。
