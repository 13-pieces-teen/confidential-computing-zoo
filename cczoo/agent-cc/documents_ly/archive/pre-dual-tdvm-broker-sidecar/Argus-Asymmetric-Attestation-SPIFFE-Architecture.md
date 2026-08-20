# Argus 非对称 Attestation-backed SPIFFE 架构

> 状态：Broker Sidecar 源码已实现，本机静态与单元验证完成，远程 Linux/TDVM 验证待执行
>
> 当前 Mock：Evidence Provider、Trustee
>
> 当前真实链路：SPIRE 1.15.2、Broker API、真实 PID、WorkloadAttestor、Entry 匹配、SVID、mTLS

## 1. 目标

判断 SPIRE Agent 当前收到的身份请求，是否代表一个通过可信启动链路运行并满足预期条件的
OpenViking workload；在验证通过后，以
`spiffe://argus.local/service/openviking-cmem` 与 OpenClaw 建立 mTLS。

OpenViking Python 源码保持不变。它不调用 Workload API，不持有 SVID 或私钥，也不挂载
SPIRE socket。身份请求和 mTLS 均由 Broker Sidecar 完成。

## 2. 当前边界

本阶段只 Mock 两个外部证明边界：

- Evidence Provider：按请求 PID 返回绑定的启动/度量证据；
- Trustee：验证请求与证据绑定，返回 ALLOW 或 DENY。

因此当前结果可以证明软件链路和身份签发逻辑，但不能声明真实 TDX Quote、QGS、
TC-API/Rekor 度量或生产 Trustee 已验证。

当前不增加以下安全目标：

- 防御已失陷的 TDVM、Docker 管理员或 SPIRE 管理员；
- 每请求 Quote、请求正文哈希或 TLS exporter 绑定；
- service mesh、即时吊销或额外 Gateway；
- OpenViking 内部 Guard。

## 3. 组件与身份

| 组件 | 身份/职责 |
|---|---|
| OpenClaw | `spiffe://argus.local/agent/openclaw`；调用本地 Guard 并发起 mTLS |
| OpenViking | 不直接持有 SPIFFE 身份；只在 TD Guest 回环地址提供 HTTP |
| Broker Sidecar | 自身身份 `spiffe://argus.local/infra/openviking-broker`；代表目标 PID 请求身份 |
| OpenViking 目标身份 | `spiffe://argus.local/service/openviking-cmem`；由 Sidecar 在内存中使用 |
| SPIRE Agent | Node Attestation、Workload/Broker API、WorkloadAttestor 调度 |
| `argus_tdx_workload` | 把 PID 交给 Evidence Provider/Trustee 并返回可信 selectors |
| SPIRE Server | Entry 匹配和 X.509-SVID 签发 |

OpenClaw 使用独立的 `x509pop` Agent；OpenViking TDVM 中的 Agent 使用
`argus_tdx` NodeAttestor。这里的“非对称”仅指 OpenViking 一侧执行 TDX
Attestation。

## 4. A-F 完整时序

### A. 节点建立信任

OpenViking TDVM 中的 SPIRE Agent 完成 `argus_tdx` Node Attestation。当前
Evidence Provider 和 Trustee 为 Mock；通过后，Agent 被 SPIRE Server 接纳。

### B. Broker 取得自身身份

Broker Sidecar 通过普通 Workload API 请求身份。Docker WorkloadAttestor 与 Broker
Registration Entry 匹配后，Sidecar 只取得自己的 Broker SVID。

### C. OpenViking 可信启动

TC-API 启动未修改的 OpenViking 容器并返回唯一 `container_ID`。Launcher 使用
`docker inspect` 取得当前宿主机 PID，然后以 `--pid host` 启动 Sidecar。

### D. Broker 代表目标 PID 请求身份

1. Sidecar 使用 Broker SVID 与 SPIRE Agent Broker Endpoint 建立 mTLS。
2. Sidecar 携带 `broker.spiffe.io: true` metadata，并提交
   `WorkloadPIDReference`。
3. Agent 验证 Broker 身份和允许的 reference type。
4. Docker/Unix WorkloadAttestor 解析该 PID；`argus_tdx_workload` 调用 Mock
   Evidence Provider 和 Mock Trustee。
5. Trustee ALLOW 时，自定义 attestor 返回：
   `verified:true`、`workload_id:openviking-cmem`、
   `policy:openviking-cmem-v1`。
6. SPIRE 同时匹配 Docker image/label selectors 与上述可信 selectors。
7. 强 Entry 命中后，Broker stream 向 Sidecar 交付 OpenViking 目标 SVID。

Trustee DENY 或任何必要 selector 不匹配时，目标 Entry 不命中，因此不交付目标 SVID。

### E. 对外 mTLS

Sidecar 在内存中使用目标 SVID，在 1943 端口终止 mTLS，只接受精确的 OpenClaw
SPIFFE ID，并把通过的 HTTP 请求转发到 `http://127.0.0.1:1933`。
QEMU 只将 1943 转发到宿主机回环地址和 Docker bridge gateway，不绑定宿主机外部网卡。

### F. 轮换与退出

Broker stream 的每个响应按完整快照处理；目标身份消失时立即清空当前身份，新 TLS
握手失败。Sidecar 使用 pidfd 监视目标进程，OpenViking PID 退出后 Sidecar 也退出。
Sidecar 不自动重启，以免新容器继续引用旧 PID。

## 5. Registration Entry

Broker Entry 只匹配 Sidecar 的固定 label、运行镜像 ID 和 image config digest。

OpenViking 目标 Entry 必须同时包含：

- `docker:label:argus.workload:openviking-cmem`；
- TC-API 实际运行时 image ID；
- 固定 image config digest；
- 三个 `argus_tdx_workload` 可信 selectors。

目标 Entry 使用 `disableX509SVIDPrefetch`，由 Broker PID-reference 请求触发目标身份
计算。

TC-API 默认把 `IMAGE_ID=openviking-cmem` 加载为
`openviking-cmem:latest`。Registration Entry 使用这个运行时 image ID，而 digest
取自启动前构建的源镜像。

## 6. 网络与密钥边界

```text
OpenClaw --SPIFFE mTLS--> Broker Sidecar:1943 --HTTP loopback--> OpenViking:1933
                                   |
                                   +-- Workload API: Broker 自身 SVID
                                   +-- Broker API: 目标 PID 的 OpenViking SVID
```

OpenViking 容器没有 SPIRE socket、SVID 文件或私钥。Broker 自身身份与目标身份是两套
不同 SVID；目标私钥只存在于 Sidecar 内存中。

## 7. 完成标准

当前 Mock 阶段的远程完成条件是：

1. ALLOW：真实运行 PID 经 Broker API、WorkloadAttestor 和强 Entry 获得目标 SVID；
2. DENY：Mock Trustee DENY 时 Sidecar 始终得不到目标 SVID；
3. OpenViking 无 SPIRE mount，Sidecar 的 PID 参数等于当前 OpenViking 宿主机 PID；
4. OpenClaw SVID 可通过 Sidecar mTLS 访问 health/ready，无客户端证书时握手失败；
5. OpenViking 退出后 Sidecar 因 pidfd 退出；
6. 所有结果明确标记 Mock Evidence Provider/Trustee，不升级为真实硬件证明结论。

## 8. 参考

- [SPIRE 1.15.2 Agent Broker 配置](https://github.com/spiffe/spire/blob/v1.15.2/doc/spire_agent.md)
- [SPIRE 1.15.2 Changelog](https://github.com/spiffe/spire/blob/v1.15.2/CHANGELOG.md)
- [SPIRE 1.15.2 Docker WorkloadAttestor 源码](https://github.com/spiffe/spire/blob/v1.15.2/pkg/agent/plugin/workloadattestor/docker/docker.go)
- [SPIRE 1.15.2 Entry CLI 源码](https://github.com/spiffe/spire/blob/v1.15.2/cmd/spire-server/cli/entry/create.go)
- [实施与远程验证方案](./Argus-Asymmetric-Attestation-SPIFFE-Implementation-Plan.md)
- [Broker Sidecar 详细方案](../../OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)
- [SPIRE asymmetric runtime](../../../core/spire/runtime/asymmetric/README.md)
- [WorkloadAttestor](../../../core/spire/plugins/argus-tdx-workloadattestor)
- [Broker Sidecar](../../../adapters/OpenViking/broker_sidecar)
