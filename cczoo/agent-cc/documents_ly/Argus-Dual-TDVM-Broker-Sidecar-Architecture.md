# Argus 双 TDVM + OpenClaw Egress / OpenViking Ingress Broker 架构

> 状态：历史 Broker架构记录
>
> 当前实现边界：不作为当前 Node Attestation部署或验收入口
>
> 后续状态：Workload Attestation与第二次Quote重新设计前保持停用

## 1. 架构目标

1. OpenClaw 与 OpenViking 分别运行在独立 TDVM，使用独立 SPIRE Agent 和运行数据；
2. 两个业务容器均不修改上游源码、不挂载 SPIRE socket、不持有 SVID 私钥；
3. OpenClaw 的正常 OpenViking 插件路径由本地 Egress Broker 执行 Guard 授权并发起
   跨 TDVM mTLS；
4. OpenViking Ingress Broker 代表真实 OpenViking PID 终止 mTLS，并只转发到回环
   `127.0.0.1:1933`。

本方案信任 OpenClaw 进程、TDVM root、Docker daemon 和部署脚本。本轮不加入针对
已攻陷 OpenClaw 的不可绕过证明、iptables 出网封锁、source-IP ACL、Docker socket
代理、service mesh、请求正文摘要或逐请求 TDX Quote。

## 2. 总体拓扑

```text
OpenClaw TDVM                              OpenViking TDVM

OpenClaw official runtime                 OpenViking official runtime
  plugin baseUrl                              ^
  http://egress:1934                          | HTTP 127.0.0.1:1933
          |                                    |
          v                                    |
OpenClaw Egress Broker  === SPIFFE mTLS ===> OpenViking Ingress Broker
  |       |                                    |       |
  |       +-- OpenClaw PID reference           |       +-- OpenViking PID reference
  +-- caller-local Guard                       +-- exact OpenClaw client ID
          |                                            |
OpenClaw SPIRE Agent                         OpenViking SPIRE Agent
  Workload API + Broker API                    Workload API + Broker API
                                               argus_tdx_workload

                  Center: SPIRE Server + Mock Trustee
```

## 3. 组件与身份

| 组件 | SPIFFE ID / 职责 |
|---|---|
| OpenClaw | `spiffe://argus.local/agent/openclaw`；被引用进程，本身不持有 SVID |
| OpenClaw Egress Broker | `spiffe://argus.local/infra/openclaw-broker`；正常插件路径 PEP，调用本地 Guard，代表 OpenClaw 发起 mTLS |
| Guard | caller-local PDP；只返回逐请求 ALLOW/DENY，不执行逐请求 TDX re-attestation |
| OpenViking | `spiffe://argus.local/service/openviking-cmem`；被引用进程，本身不持有 SVID |
| OpenViking Ingress Broker | `spiffe://argus.local/infra/openviking-broker`；验证精确客户端身份并终止 mTLS |
| `argus_tdx_workload` | 对 OpenViking PID 收集并验证 workload 证据，返回可信 selectors |

## 4. 请求时序

1. 部署脚本启动 caller-local Guard 和无 Argus 注入的 OpenClaw 容器；
2. 脚本读取真实 OpenClaw 宿主机 PID，启动 Egress Broker；
3. Egress Broker 用自身 SVID 连接本 TDVM Broker API，并以
   `WorkloadPIDReference(OpenClaw PID)` 订阅 OpenClaw SVID；
4. OpenClaw 插件把普通 HTTP 请求发到 `http://argus-dual-openclaw-egress:1934`；
5. Egress Broker 按 HTTP method 映射 `memory.read/delete/write`，调用
   `POST /guard/v1/authorize`，授权元数据中的 origin 固定为远端 HTTPS origin；
6. Guard DENY 返回 403；Guard 不可用或 ALLOW receipt 无效返回 503，且不连接上游；
7. ALLOW 后 Egress Broker 不预读请求正文，使用 OpenClaw SVID 发起 mTLS，并精确
   校验服务端 `spiffe://argus.local/service/openviking-cmem`；
8. Ingress Broker 精确校验客户端 `spiffe://argus.local/agent/openclaw`，再转发同一
   请求到 OpenViking 回环 HTTP；mTLS 上游失败由 Egress Broker 返回 502。

## 5. Registration Entry

| Entry | Parent | 必需条件 |
|---|---|---|
| `dual-openclaw-workload` | OpenClaw Agent | OpenClaw label/image/config digest；关闭 X.509-SVID prefetch |
| `dual-openclaw-broker` | OpenClaw Agent | Egress Broker label/image/config digest |
| `dual-openviking-target` | OpenViking Agent | runtime label/image/config digest + `argus_tdx_workload` selectors；关闭 prefetch |
| `dual-openviking-broker` | OpenViking Agent | Ingress Broker label/image/config digest |

两个 Agent 的 Broker Endpoint 只允许各自本地 Broker 身份提交
`WorkloadPIDReference`。OpenViking target digest 来自 TC-API 启动后的实际 runtime
image，不使用转换前 source digest。

## 6. 网络、身份与数据边界

- OpenClaw 只知道 Docker 内部 Egress HTTP 地址，不知道 SPIFFE、Guard 或证书；
- 外部 `openviking.argus.local` host mapping 只放在 Egress Broker；
- Guard token 只挂载到 Guard 和 Egress Broker；
- 两个 Broker 的目标 SVID 和私钥只保存在内存；
- Egress `1934` 不映射 TDVM 宿主机端口；Ingress `1943` 保持跨 TDVM mTLS 入口；
- OpenViking 明文 `1933` 只在其 TDVM 回环地址可达；
- 日志只记录 request ID、method、path、decision ID、HTTP status 和耗时。

## 7. 生命周期

两个 Broker 都持有目标 PID 的 pidfd。目标进程退出后，对应 Broker 取消订阅并退出；
重启业务容器必须读取新 PID 并重建 Broker。Broker API subscription 终止时 Broker
同样退出。SVID snapshot 更新只替换内存状态，不落盘，也不等同于重新执行 TDX Quote。

## 8. 当前实现与证据边界

以下内容只记录当时的组件划分，不对应当前可执行 Profile：

- `adapters/OpenClaw/egress_sidecar`：OpenClaw Egress Broker；
- `adapters/OpenViking/broker_sidecar`：OpenViking Ingress Broker；
- `core/spire/plugins/argus-tdx-workloadattestor`：OpenViking workload evidence；
- `adapters/OpenClaw/scripts/Dockerfile.sbx-runtime`：无 Argus 代码的 OpenClaw runtime；
- ALLOW/DENY验证属于当时的软件链验收。

既有远程报告只能证明其记录时的代码与环境，不能沿用为当前结果。

## 9. 完成标准

1. `docker inspect` 证明 OpenClaw 无 SPIRE/Guard/SVID mount、Argus 环境变量、
   `NODE_OPTIONS`、Argus entrypoint 和外部 OpenViking host mapping；
2. Egress/Ingress Broker 分别引用当前 OpenClaw/OpenViking PID 并取得目标 SVID；
3. 插件 `baseUrl` 为内部 Egress 地址；Guard ALLOW 后跨 TDVM mTLS `/health=200`；
4. Guard DENY 返回 403 且 Ingress 没有收到该请求；Guard 异常返回 503；
5. 真实 OpenClaw agent turn 被 OpenViking capture、commit 和 archive；
6. 缺失/错误客户端身份失败，明文 1933 不可从 OpenClaw TDVM 访问；
7. 两个目标进程退出后 Broker 均退出，OpenClaw 重启后 Broker 使用新 PID；
8. 报告明确区分 Mock 软件链、真实 Quote/QGS 和生产验收。

## 10. 参考

- [实施与验证计划](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md)
- [OpenViking Ingress Broker 详细设计](./OpenViking-Non-Intrusive-SPIFFE-Broker-Sidecar-Plan-CN.md)
- [双 TDVM 远程验证报告](./Argus-Dual-TDVM-Broker-Sidecar-Remote-Validation-Report.md)
