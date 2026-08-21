# OpenViking 非侵入式 Workload Attestation 与 SPIFFE Broker Sidecar 完整方案

## 文档状态

| 项目 | 内容 |
| --- | --- |
| 方案目标 | 不修改 OpenViking 上游 Python 业务源码，对实际运行的 OpenViking Python 进程完成 Workload Attestation，并由 sidecar 代表它进行 SPIFFE mTLS |
| 当前拓扑 | OpenClaw 与 OpenViking 分别运行在独立 TDVM；Broker Sidecar 位于 OpenViking TDVM |
| 主方案 | SPIRE 1.15.2 SPIFFE Broker API + `WorkloadPIDReference` + 自定义 WorkloadAttestor + Broker Sidecar |
| 当前仓库基线 | Broker 组件已接入统一双 TDVM Profile；本地静态验证完成，远程验收待执行 |
| 文档性质 | 方案决策与详细设计记录；当前实现状态和执行命令以架构文档及实施方案为准 |
| 关键限制 | SPIRE 1.15.2 的 Broker API 仍标记为 experimental；双 TDVM Broker Profile 仍需远程验证 |

> 当前状态：Broker Sidecar、自定义 WorkloadAttestor 和统一 `runtime/dual-tdvm`
> Profile 已完成代码集成；OpenViking 不再直接挂载 Workload API。远程 ALLOW/DENY
> 与跨 TDVM mTLS 尚未执行。当前状态以
> [双 TDVM + Broker Sidecar 架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)和
> [实施计划](./Argus-Dual-TDVM-Broker-Sidecar-Implementation-Plan.md)为准。

### 方案决策

本文是在“OpenViking 上游源码非侵入”成为优先约束之后形成的目标方案。它取代既有设计中“OpenViking Python 直接调用 Workload API”这一身份申请方式，但不改变已经确认的 Node Attestation、TC-API/Rekor、Evidence Provider、Trustee、selector 和 Registration Entry 主链路。

本文以 **Broker Sidecar** 为主方案和当前唯一落地路径。TDVM Host Broker Gateway 仅作为文末备注保留，用于记录未来可能的部署形态；它不进入当前实现范围，也不作为 Sidecar 的并行或回退路径。

既有直接 Python 方案及其OpenViking专用代码不再保留。实现 Broker Sidecar 时，同时删除OpenViking路径中的Python SPIFFE wrapper、凭据文件等待和materializer启动代码，最终只存在一条身份与mTLS路径。

## 1. 结论

推荐把 OpenViking 的身份获取和 mTLS 从 Python 进程中完全移出，交给一个专用的 **OpenViking Broker Sidecar**：

1. OpenViking 仍按原生方式运行，不调用 SPIRE，也不持有 SVID 私钥。
2. Sidecar 先通过普通 Workload API 获取自己的 Broker SVID 和信任域 bundle。
3. Sidecar 使用自身 SVID 连接 Broker Endpoint，并验证服务端证书中的精确 SPIRE Agent SPIFFE ID。
4. Sidecar 获得 OpenViking Python 进程的宿主机 PID，并持有对应的 `pidfd`。
5. Sidecar 通过 SPIRE Broker API 提交 `WorkloadPIDReference(PID)`。
6. SPIRE Agent 对该 Python PID 运行 Docker/Unix 以及自定义 WorkloadAttestor。
7. 自定义 WorkloadAttestor 联动 Evidence Provider、TC-API/Rekor 和 Trustee，验证该 PID 是否对应可信启动的 OpenViking。
8. 验证通过后，WorkloadAttestor 返回可信 selectors；SPIRE 匹配静态 Registration Entry，并把 OpenViking SVID 返回给 sidecar。
9. Sidecar 使用 OpenViking SVID 对外提供 mTLS，再把认证后的业务流量转发到内部 OpenViking HTTP 服务。

最终的信任语义是：

> SPIRE 仅在受信 Broker 引用的当前进程 PID，确实对应通过可信启动链路运行且满足预期策略的 OpenViking workload 时，才向 Broker 返回该 OpenViking workload 的 SVID；原进程退出或身份失效后，Sidecar不能再用该身份建立新的mTLS连接。

这不是“Python 自己持有 SVID”，而是“Sidecar 经过 SPIRE 授权后，代表已验证的 Python workload 使用其身份”。

本文按当前仓库的业务方向设计：OpenClaw 作为 mTLS 客户端访问 OpenViking，sidecar作为 OpenViking 的入站mTLS代理。如果未来由 OpenViking 主动发起对外mTLS连接，可以复用同一个 Broker身份获取链路，但还需要独立定义出口代理和流量重定向，不属于第一阶段范围。

## 2. 目标与非目标

### 2.1 目标

- OpenViking 上游 Python 业务源码保持不变。
- 被 Workload Attestation 验证的对象是实际 OpenViking Python PID，而不是 materializer 或 sidecar PID。
- SVID 只有在 Trustee 对 OpenViking 启动证据返回 ALLOW 后才能取得。
- 所有能够签发目标 OpenViking SPIFFE ID 的 Registration Entry 都必须强制包含自定义可信 selector，不能保留同 ID 的弱 Entry。
- OpenViking 的对外业务端口只能通过 sidecar 的 mTLS 入口访问。
- Python 退出、重启或身份失效后，旧 SVID 不再被 sidecar用于新连接。
- SVID 私钥保留在 sidecar 内存中，不传给 Python，也不落普通共享目录。

### 2.2 非目标

- 不证明 TLS 握手代码运行在 Python 进程内部。
- 不修改 OpenViking 的业务 API、模型、存储或核心逻辑。
- 不在第一阶段引入 service mesh、Kubernetes、跨节点 PID 引用或通用多租户 Broker。
- 不把每一次业务请求都变成一次 TDX Quote 验证。
- 不把 SVID 轮换等同于重新执行 Workload Attestation。
- 不保留 OpenViking 旧 materializer/Python TLS wrapper 的运行开关、兼容入口或回退路径。

## 3. 官方接口选择

### 3.1 主方案接口：SPIFFE Broker API

SPIFFE Broker API 的用途就是让受信基础设施组件代表 workload 获取 SVID。Broker 提交 workload reference，SPIRE Agent 根据该 reference 运行自己的 WorkloadAttestor，并由 Agent 产生 selectors。

本方案只启用：

```text
type.googleapis.com/spiffe.broker.WorkloadPIDReference
```

并且只通过本机 Unix Domain Socket 使用，不开放 TCP，不允许通配 reference type。

选择 Broker API 而不是普通 Workload API 的原因：

- 普通 Workload API 自动使用 Socket 对端凭据识别调用进程，Sidecar 无法把自己伪装成 Python PID。
- Broker API 明确支持受信 Sidecar 代表指定 PID 获取身份。
- Broker 只提交 PID reference，不能直接提交自造 selectors；Agent 的 WorkloadAttestor 仍是 selector 的可信来源。
- Broker API 为每一个 referenced workload 建立独立的 SVID 更新流，适合处理身份轮换；目标进程生命周期仍需 Broker 与 Endpoint 共同约束，本方案由 Sidecar 使用 `pidfd` 监控退出并主动取消订阅。

### 3.2 版本决定

当前仓库已经将 SPIRE Agent/Server 和新 WorkloadAttestor Plugin SDK 统一到 1.15.2：

```text
SPIRE Agent/Server: 1.15.2
新 WorkloadAttestor Plugin SDK: 1.15.2
```

现有 `argus_tdx` NodeAttestor 没有因 Broker API 被改写。本机插件测试、配置检查和
Mock 软件链路检查已经完成；Broker API 在 1.15.2 中仍属于 experimental，因此完整
兼容性结论要以远程 Linux/TDVM 验证为准。整个 OpenViking 身份链只按 Broker API
实现。

## 4. 总体架构

```text
                           Center / Control Plane
┌─────────────────────────────────────────────────────────────────┐
│ SPIRE Server                                                   │
│   Static Registration Entries                                 │
│   SPIFFE CA / X.509-SVID signing                              │
└───────────────────────────────▲─────────────────────────────────┘
                                │ Agent sync / SVID issuance
┌───────────────────────────────┴─────────────────────────────────┐
│ OpenViking TDVM                                                │
│                                                               │
│  TC-API / trusted launcher                                    │
│      │ launch + measure                                       │
│      ▼                                                        │
│  OpenViking Python process                                    │
│      │ internal HTTP only                                     │
│      │                                                        │
│      │ target PID / pidfd                                     │
│      ▼                                                        │
│  OpenViking Broker Sidecar                                    │
│      │ 1. Workload API: obtain Broker's own SVID              │
│      │ 2. Broker API: WorkloadPIDReference(OpenViking PID)     │
│      │ 3. receive OpenViking SVID and terminate mTLS           │
│      │                                                        │
│      ├──────── mTLS business listener ◄──── OpenClaw           │
│      └──────── authenticated HTTP forward ───► OpenViking      │
│                                                               │
│  SPIRE Agent                                                  │
│      ├── NodeAttestor: argus_tdx                              │
│      ├── WorkloadAttestor: docker / unix                      │
│      ├── WorkloadAttestor: argus_tdx_workload                 │
│      ├── Workload API UDS                                     │
│      └── Broker API UDS                                       │
│                         │                                     │
│                         ▼                                     │
│                 Evidence Provider                             │
│                   ├── TDX Quote                               │
│                   └── TC-API / Rekor launch evidence          │
└─────────────────────────┼─────────────────────────────────────┘
                          ▼
                       Trustee
                    ALLOW / DENY
```

## 5. 组件职责

### 5.1 TC-API / trusted launcher

- 按现有可信启动流程启动 OpenViking。
- 将启动度量扩展到约定的 RTMR，并把透明日志写入 Rekor。
- 保存 `launch_id`、Rekor UUID、容器 ID、镜像配置摘要与 workload 标识之间的映射。
- 向本机受信 Broker/启动监督组件提供实际 OpenViking Python 进程的宿主机 PID。
- 不让 OpenViking 自己声明“我是谁”或自行提交任意 PID。

### 5.2 OpenViking Python

- 使用上游原生启动方式运行。
- 只监听内部地址或 Unix Socket。
- 不挂载 SPIRE Workload API、Broker API 或 SVID 目录。
- 不保存 SVID 和私钥。
- 第一阶段保持单个服务进程/单 worker，确保一个 PID 对应一个服务实例。

### 5.3 OpenViking Broker Sidecar

- 通过普通 Workload API 获取自身 Broker SVID 和信任域 bundle。
- 使用自身 SVID 与 SPIRE Agent Broker Endpoint 建立 mTLS，并验证服务端证书中的精确 Agent SPIFFE ID；只验证信任域或证书链不够。
- 获取并固定 OpenViking Python 的 PID/pidfd。
- 发送 `WorkloadPIDReference`，并在每次 RPC 中携带官方要求的 `broker.spiffe.io: true` metadata。
- 订阅 OpenViking X.509-SVID 更新。
- 把每次流响应当成目标 workload 当前身份的完整快照，重新选择预期的 OpenViking SPIFFE ID；目标身份缺失或被删除时立即停止使用旧身份。
- 在内存中维护证书、私钥和 bundle，完成 mTLS 终止与轮换。
- 验证对端客户端 SPIFFE ID 后，才向内部 OpenViking 转发请求。
- 监听 pidfd；原目标进程退出时立即取消订阅、停止对外接入并清除对应身份材料。

### 5.4 SPIRE Agent Broker Endpoint

- 只接受配置中允许的 Broker SPIFFE ID。
- 只允许 `WorkloadPIDReference`。
- Broker UDS 与 Workload API UDS 使用不同目录。
- 不开放 PID reference 的 TCP 使用。
- 根据 PID reference 对目标进程运行配置的 WorkloadAttestor stack。
- 只把与目标 selectors 匹配的 Registration Entry 身份返回给 Broker。

SPIRE 1.15.2 在每次 `SubscribeToX509SVID` 建立时执行一次 `AttestReference`，随后按得到的 selectors 订阅身份缓存。它不会保留 sidecar 的 `pidfd`，也不会在正常 SVID 轮换时重新检查目标 PID。因此本方案不能依赖 Agent 自动发现 Python 已退出；Sidecar 的 pidfd 监听和订阅取消是目标生命周期的必要约束。

### 5.5 自定义 WorkloadAttestor

- 输入是 OpenViking Python 的 PID，而不是 sidecar PID。
- 从 Agent 所在 PID namespace 中读取进程和容器属性。
- 将实时进程与 TC-API 启动记录关联。
- 触发 Evidence Provider 收集 Quote 和 Rekor/度量证据。
- 把证据交给 Trustee 验证。
- 仅在 Trustee ALLOW 后返回 `argus_tdx_workload` selectors。

SPIRE Plugin SDK 1.15.2 支持 `AttestReference`；对于 `WorkloadPIDReference`，如果插件没有实现该 RPC，Agent 可以回退到已有的 `Attest(pid)`。为了控制第一阶段范围，自定义插件可以先实现单一 PID 路径，不支持 KubernetesObjectReference 或自定义 reference。

SPIRE 会并行运行已配置的 WorkloadAttestor，并聚合成功插件返回的 selectors。单个自定义 attestor 返回错误或无可信 selector，不一定中止 Docker/Unix attestor 的成功结果。因此 Trustee 门禁最终依赖于：所有签发目标 OpenViking SPIFFE ID 的 Registration Entry 都必须要求 `argus_tdx_workload` selector，Docker/Unix selectors 不能单独取得该身份。

### 5.6 Evidence Provider 与 Trustee

Evidence Provider：

- 获取当前 TDVM 的 TDX Quote。
- 读取 TC-API 对应 launch record 和 Rekor 透明日志。
- 建立当前 PID、进程启动时间、容器/镜像、launch ID 与 Quote 之间的绑定。

Trustee：

- 验证 Quote、TCB/策略、证据完整性和 freshness。
- 验证 RTMR/Rekor 记录与批准的 OpenViking 启动配置一致。
- 验证当前实时进程与该启动记录对应。
- 返回 ALLOW 或 DENY；DENY、超时或证据不完整都不能产生可信 selector。

## 6. 身份与 Registration Entry

至少定义两个独立的 workload 身份。

### 6.1 Broker 身份

```text
spiffe://argus.local/infra/openviking-broker
```

该身份由 sidecar 自己通过普通 Workload API 获取，用于连接 Broker Endpoint。它只表示“这是被允许使用 Broker API 的基础设施组件”，不表示它已经是 OpenViking。

建议 Broker Entry 使用固定镜像摘要和专用标签，例如：

```text
docker:label:argus.component:openviking-broker
docker:image_config_digest:<broker-image-digest>
```

### 6.2 OpenViking workload 身份

```text
spiffe://argus.local/service/openviking-cmem
```

该 Entry 必须绑定：

```text
docker:label:argus.workload:openviking-cmem
docker:image_config_digest:<approved-openviking-digest>
argus_tdx_workload:verified:true
argus_tdx_workload:workload_id:openviking-cmem
argus_tdx_workload:policy:<approved-policy-id-or-hash>
```

Registration Entry 始终是静态的。验证成功时动态变化的是 WorkloadAttestor 返回的 selectors，不是 Entry 内部的 `verified` 状态。

这里的“该 Entry 必须绑定”适用于所有能够签发 `spiffe://argus.local/service/openviking-cmem` 的 Entry，而不只是新建的某一条。实施时必须替换或删除当前 Docker-only 的同 ID Entry，并在验收前枚举该 SPIFFE ID 的全部 Entry，确认不存在仅依赖 Docker、Unix 或其他较弱 selectors 的旁路。

创建 OpenViking Entry 时建议使用 CLI 选项 `-disableX509SVIDPrefetch`，使该身份按实际请求路径获取，而不是作为普通预取身份处理。

## 7. PID 获取与绑定

### 7.1 PID 必须是 Agent 可见的 PID

Broker API 中的 PID 必须是 OpenViking Python 自身进程在 SPIRE Agent 所在 PID namespace 中的 PID，不能使用：

- sidecar PID；
- 容器内部 namespace 中无法被 Agent 解析的 PID；
- Docker sandbox/init 之外的错误 PID；
- 已退出并可能被复用的旧 PID。

当前 SPIRE Agent 已使用宿主机 PID namespace。目标方案中 Broker Sidecar 也使用相同 PID namespace，确保双方对 PID 的解释一致。

### 7.2 推荐的非侵入式获取方式

1. TC-API/launcher 启动 OpenViking 容器。
2. OpenViking 的容器入口使用 `exec openviking-server ...`，使容器主进程就是 Python 服务进程。
3. Launcher 从容器运行时获得宿主机 PID、容器 ID和进程启动时间，并通过受保护的本机通道把PID和启动记录引用交给Sidecar。
4. Sidecar收到PID后自行打开并持有该PID的 `pidfd`；本方案不引入Launcher向Sidecar转交文件描述符的额外协议。
5. Broker 只接受来自受保护本机启动通道的 PID，不接受 OpenViking 业务请求中的 PID 参数。
6. WorkloadAttestor 独立从 `/proc/<pid>`、cgroup 和容器运行时重新确认 PID与镜像/容器关系。

Broker API 只传递数值 PID，不传递 `pidfd` 或进程启动时间。Sidecar 持有 `pidfd` 的作用是稳定识别原进程并及时获知它已经退出；它不能让 SPIRE Agent 通过 pidfd 解析目标，也不能阻止内核复用同一个数值 PID。

PID 复用由独立绑定检查拒绝：WorkloadAttestor/Evidence Provider 读取当前 `/proc/<pid>` 的进程启动时间、cgroup 和容器关系，并与 TC-API launch record 中的 `host_pid`、`process_start_time` 和 `container_id` 一致性校验。原进程一旦退出，Sidecar必须依赖 pidfd立即取消旧订阅；即使数值PID随后被复用，新进程也不能通过旧启动记录取得身份。

### 7.3 启动绑定记录

TC-API 至少需要让验证链能够关联以下事实：

```text
launch_id
workload_id
container_id
host_pid
process_start_time
image_config_digest
rekor_uuid
measurement/profile identifier
```

Sidecar只需要向 Broker API提交 PID。其余属性由 Agent/WorkloadAttestor、Evidence Provider 和 Trustee 独立收集并交叉验证，不能把 sidecar提供的声明直接当成 selector。

## 8. 完整时序

### 阶段 A：节点建立信任

1. SPIRE Agent 在 OpenViking TDVM 中启动。
2. `argus_tdx` NodeAttestor 完成 Node Attestation。
3. SPIRE Server确认该 Agent 所在 TDVM可信，并下发 Agent 身份和授权数据。

### 阶段 B：Broker 自身取得身份

1. OpenViking Broker Sidecar 启动。
2. Sidecar连接普通 Workload API。
3. SPIRE Agent根据 sidecar 自身 PID执行 Docker/Unix Workload Attestation。
4. Sidecar获得 `spiffe://argus.local/infra/openviking-broker` SVID和信任域bundle。
5. Sidecar使用该 SVID与 Agent Broker Endpoint 建立 mTLS，并把服务端身份精确校验为部署配置中固定的 Agent SPIFFE ID。

### 阶段 C：OpenViking 可信启动

1. TC-API 触发 OpenViking 启动。
2. TC-API 执行 RTMR extend 并保存 Rekor透明日志 UUID。
3. OpenViking Python 以内部服务方式启动，但外部 mTLS端口尚未开放。
4. Launcher获得 Python宿主机 PID、启动时间和容器 ID，并把PID和启动记录引用交给Sidecar。
5. Sidecar确认该 PID仍然存活，自行打开pidfd并建立目标生命周期上下文。

### 阶段 D：Broker 代表 OpenViking 请求身份

1. Broker调用 `SubscribeToX509SVID`。
2. 请求包含 `WorkloadPIDReference(OpenViking host PID)`。
3. SPIRE Agent验证 Broker SVID、reference type授权和必需的 gRPC metadata。
4. SPIRE Agent对该 PID运行 Docker、Unix和自定义 `argus_tdx_workload` attestors。
5. 自定义 attestor请求 Evidence Provider收集 TDX Quote和 TC-API/Rekor证据。
6. Trustee验证当前 PID是否对应获批的 OpenViking可信启动实例。
7. ALLOW 时，自定义 attestor返回可信 selectors。
8. SPIRE Agent用完整 selector集合匹配静态 OpenViking Entry。
9. SPIRE Server签发或 Agent取得 OpenViking X.509-SVID。
10. Broker API向 sidecar返回 OpenViking SVID、私钥和 trust bundle。

### 阶段 E：对外 mTLS 服务

1. Sidecar确认返回集合中存在且只选择预期 OpenViking SPIFFE ID。
2. Sidecar在内存中构建 TLS context。
3. Sidecar开始监听外部 mTLS端口。
4. OpenClaw连接时，sidecar验证证书链和精确客户端 SPIFFE ID。
5. 验证通过后，sidecar把请求转发到内部 OpenViking HTTP地址。
6. OpenViking返回结果，sidecar通过原 mTLS连接返回给 OpenClaw。

### 阶段 F：轮换、重连与退出

1. Broker保持 `SubscribeToX509SVID` stream；每个响应都表示该目标 workload 当前被授权身份的完整快照，而不是增量。
2. 每次收到快照时，Sidecar重新查找精确的 OpenViking SPIFFE ID。找到时原子替换新连接使用的 TLS context；目标身份缺失或快照为空时，立即清除旧身份并进入 `BLOCKED`。
3. 如果订阅以 `PermissionDenied` 结束，Sidecar同样立即清除旧身份并进入 `BLOCKED`。
4. SVID普通轮换不自动等同于新的 Quote/Trustee验证。
5. 需要强制刷新证明时，Broker关闭目标订阅并重新提交 PID，触发新一轮 Workload Attestation。
6. pidfd显示原 Python进程退出时，sidecar立即取消订阅、停止新连接并清除 OpenViking身份材料；不等待 Agent自行发现进程退出。
7. OpenViking重启后必须以新 PID/pidfd重新执行完整流程，不能复用旧身份上下文。

## 9. Broker Sidecar 状态机

```text
BOOT
  │
  ▼
BROKER_IDENTITY_READY
  │ receive target PID + pidfd
  ▼
TARGET_DISCOVERED
  │ SubscribeToX509SVID(PID)
  ▼
ATTESTING
  ├── RPC error / no exact target SVID ──► BLOCKED
  └── full snapshot contains exact target SVID
          ▼
       SERVING
          ├── next full snapshot contains exact target SVID
          │        └── atomic replace TLS context ──► SERVING
          ├── stream retry ─► reconnect under validity policy
          ├── empty snapshot / target identity absent
          │        └── clear old identity ──► BLOCKED
          ├── stream ends with PermissionDenied
          │        └── clear old identity ──► BLOCKED
          └── target pidfd exits ─► TERMINATED
```

状态约束：

- `SERVING` 之前不能开放对外业务端口。
- `BLOCKED` 状态不能回退为明文服务。
- 每个流响应都必须整体替换前一个身份快照；不能把“本次响应没有目标身份”解释为继续沿用旧 SVID。
- 目标退出后必须销毁该目标的证书、私钥和 bundle引用。
- 不允许用 Broker自身 SVID代替 OpenViking SVID对外服务。

## 10. 网络与密钥边界

### 10.1 Socket 布局

```text
/opt/spire/run/openviking/agent.sock       # 普通 Workload API
/opt/spire/run/broker/broker.sock          # Broker API，必须位于不同目录
```

Sidecar还必须固定两个独立配置值：

```text
broker_endpoint = unix:///opt/spire/run/broker/broker.sock
expected_agent_spiffe_id = <由当前部署的Node Attestation/Agent身份结果确认并固化>
```

`expected_agent_spiffe_id` 不能只写成信任域，也不能从本次连接对端自报的数据动态接受。

挂载关系：

| 组件 | Workload API | Broker API | OpenViking SVID |
| --- | --- | --- | --- |
| SPIRE Agent | 服务端 | 服务端 | Agent缓存/签发路径 |
| Broker Sidecar | 读取自身身份 | 代表目标 PID订阅 | 仅内存持有 |
| OpenViking Python | 不挂载 | 不挂载 | 不接触 |

### 10.2 业务网络

- 外部只暴露 sidecar 的 mTLS端口，例如 `1943`。
- OpenViking原生 HTTP端口不映射到 TDVM外部。
- Sidecar 与 OpenViking使用专用本地网络或共享 Unix Socket。
- 对端身份校验在 sidecar完成，校验失败的请求不能进入 OpenViking。
- 禁止为“可用性”保留绕过 sidecar 的明文外部端口。

### 10.3 密钥

- Broker自身私钥仅用于连接 Agent Broker Endpoint。
- OpenViking workload私钥只在 sidecar内存中用于该 workload的业务 TLS。
- 两种身份材料不能混用。
- 不把 OpenViking私钥写入 `/run/argus-svid` 后再共享给 Python。

## 11. SPIRE Agent 配置草案

以下结构基于仓库当前使用的 SPIRE 1.15.2；接入双 TDVM 运行配置后仍需用 `spire-agent validate` 确认最终配置：

```hcl
agent {
    trust_domain = "argus.local"
    socket_path = "/opt/spire/run/openviking/agent.sock"

    experimental {
        broker {
            socket_path = "/opt/spire/run/broker/broker.sock"
            brokers = [
                {
                    id = "spiffe://argus.local/infra/openviking-broker"
                    allowed_reference_types = [
                        {
                            type_url = "type.googleapis.com/spiffe.broker.WorkloadPIDReference"
                        }
                    ]
                }
            ]
        }
    }
}

plugins {
    WorkloadAttestor "docker" {
        plugin_data {
            docker_socket_path = "unix:///var/run/docker.sock"
        }
    }

    WorkloadAttestor "unix" {
        plugin_data {}
    }

    WorkloadAttestor "argus_tdx_workload" {
        plugin_cmd = "/opt/spire/plugins/argus-tdx-workloadattestor"
        plugin_checksum = "<checksum>"
        plugin_data {
            evidence_endpoint = "http://127.0.0.1:<port>/ra/v1/evidence"
            policy_id = "openviking-cmem-v1"
        }
    }
}
```

不配置 `bind_address`，因此 PID reference不能经网络发送。Broker配置中也不使用 `type_url = "*"`。

上述 `brokers[].id` 是 Agent 对 Broker 客户端的允许列表；Sidecar 中的 `expected_agent_spiffe_id` 则用于 Broker 反向认证 Agent 服务端。两者方向不同，必须同时配置和测试。

## 12. 对当前仓库的改造边界

### 12.1 已新增并复用

| 位置 | 作用 |
| --- | --- |
| `cczoo/agent-cc/adapters/OpenViking/broker_sidecar/` | 专用 Broker API客户端、PID生命周期管理、内存SVID轮换、mTLS代理 |
| `cczoo/agent-cc/core/spire/plugins/argus-tdx-workloadattestor/` | 对目标 PID执行 OpenViking Workload Attestation |
| Broker/WorkloadAttestor测试与故障用例 | 验证 PID、selector、轮换、退出和拒绝路径 |

### 12.2 当前统一 Profile 实现

| 文件 | 目标变化 |
| --- | --- |
| `core/spire/runtime/dual-tdvm/config/openviking-agent.conf.tmpl` | Broker Endpoint、自定义 WorkloadAttestor 和 Trustee mTLS |
| `core/spire/runtime/dual-tdvm/scripts/manage-guest.sh` | 部署 Agent/Sidecar，通过既有 TC-API 启动 OpenViking，并分别挂载 Workload/Broker UDS |
| `core/spire/runtime/dual-tdvm/scripts/register-workloads.sh` | 创建 OpenClaw、Broker、OpenViking target 三个 Entry，删除已知旧弱 Entry |
| `adapters/OpenViking/scripts/launch_openviking.sh` | 不再把 Workload API挂入 OpenViking；只开放内部HTTP；把启动结果交给受信PID绑定路径 |
| `adapters/OpenViking/configs/Dockerfile.openviking` | OpenViking镜像不再内置其自己的 materializer和TLS wrapper，恢复原生服务入口 |
| `core/spire/runtime/dual-tdvm/scripts/verify.sh` | 检查 Broker目标订阅、实际 PID、URI SAN、ALLOW/DENY 和跨 TDVM mTLS结果 |

### 12.3 已移除的旧 OpenViking 身份路径

Broker Sidecar完成后，删除以下仅服务于旧 OpenViking Python方案的代码和配置：

- `cczoo/agent-cc/adapters/OpenViking/spiffe_server/` 整个目录，包括TLS wrapper、SPIFFE身份校验辅助代码及对应测试；
- `cczoo/agent-cc/adapters/OpenViking/scripts/entrypoint-spiffe.sh`；
- `Dockerfile.openviking` 中构建和复制 `argus-svid-materializer`、复制Python wrapper及设置旧入口的步骤；
- `launch_openviking.sh` 中向OpenViking挂载Workload API、传入凭据目录和启动旧SPIFFE入口的逻辑；
- `deploy-v2-guest.sh` 中检查 `/run/argus-svid/status.json` 的旧就绪判断；
- 仅供旧 OpenViking Python方案使用的环境变量、测试和文档说明。

旧 `svid-materializer` 共享组件及 OpenClaw preload 链已经移除。当前 OpenClaw 也由独立 Egress Broker 通过真实 PID 获取内存态 SVID，业务容器不再直接持有身份材料。

## 13. 失败语义

| 场景 | 预期行为 |
| --- | --- |
| Broker自身无法获取SVID | 无法连接 Broker Endpoint；OpenViking外部mTLS端口不开放 |
| Broker SPIFFE ID不在允许列表 | TLS层拒绝 |
| Agent证书链有效但SPIFFE ID不是固定的预期Agent ID | Sidecar拒绝连接；不发起Broker RPC；外部mTLS端口不开放 |
| 请求缺少 `broker.spiffe.io: true` | Broker RPC被拒绝 |
| 使用非PID reference | `PermissionDenied` |
| 通过TCP提交PID reference | 拒绝；本方案只允许本机UDS |
| PID不存在或已退出 | `NotFound`/attestation失败；不返回OpenViking SVID |
| PID指向其他进程 | Docker/进程/自定义selectors不匹配；不返回OpenViking SVID |
| TC-API/Rekor证据不匹配 | Trustee DENY；无可信selector |
| Quote或Trustee不可用 | 快速失败；不开放mTLS入口 |
| Docker/Unix attestor成功但自定义attestor DENY或失败 | 因所有目标ID Entry都强制包含自定义selector，快照中不得出现OpenViking身份 |
| 首个身份快照中没有精确OpenViking ID | sidecar拒绝进入SERVING |
| 后续完整快照为空或不再包含精确OpenViking ID | 立即清除旧TLS context并进入BLOCKED |
| 目标进程退出 | 取消订阅、清除身份、停止新连接 |
| OpenViking重启 | 新PID重新attest，不复用旧上下文 |
| SVID轮换 | 原子替换新连接使用的TLS context |
| SVID被redact或收到PermissionDenied | 立即停止使用并进入BLOCKED |
| sidecar崩溃 | 外部mTLS入口消失；不回退到OpenViking明文端口 |

表中的具体 gRPC 状态码用于测试和诊断，不作为唯一安全判断。Sidecar只需要遵循统一条件：RPC失败或当前完整快照中没有精确目标SPIFFE ID时，不提供OpenViking mTLS服务。

## 14. 分阶段落地

以下 M0-M5 保留 Broker 组件的落地过程。M0-M3 及双 TDVM Profile 代码接入已经完成；远程验收仍以当前实施计划为准。

### M0：接口与版本基线

- SPIRE Server、Agent 与新 WorkloadAttestor统一使用仓库当前的1.15.2版本基线。
- 先对现有 `argus_tdx` NodeAttestor做兼容性回归，不把依赖升级和Broker改造捆绑为一个不可拆分变更。
- 运行配置校验和现有 NodeAttestor回归。
- 用最小测试 Broker验证自身SVID、双向精确身份认证和PID reference权限。

完成条件：1.15.2 Server/Agent可启动，现有 Node Attestation不回归，Sidecar只接受固定的Agent SPIFFE ID，未授权Broker和错误reference均被拒绝。

### M1：自定义 WorkloadAttestor

- 实现 `argus_tdx_workload` PID attestation。
- 接入 mock Evidence Provider和mock Trustee。
- 只在ALLOW时返回可信selectors。
- Registration Entry使用Docker selectors与自定义selectors联合匹配。
- 替换当前 `v2-openviking-workload` Docker-only Entry，并枚举所有目标SPIFFE ID Entry，确认不存在弱Entry。

完成条件：正确PID取得OpenViking SVID；错误PID、错误镜像和Trustee DENY均明确无身份；即使Docker/Unix attestor成功，自定义attestor失败时仍不能取得目标身份。

### M2：Broker Sidecar 身份路径

- Sidecar通过Workload API取得Broker自身SVID。
- 通过Broker API订阅指定PID的OpenViking SVID。
- 把每次响应作为完整快照，精确选择目标SPIFFE ID并完成内存轮换或身份撤销。
- 实现pidfd退出监听、主动取消订阅和fail-closed状态机。

完成条件：日志和测试能够分别证明Broker身份与OpenViking身份没有混用；后续快照移除目标身份时，旧TLS context被立即清除。

### M3：非侵入式 mTLS 代理

- OpenViking恢复原生内部HTTP启动。
- Sidecar使用OpenViking SVID提供mTLS入口。
- 校验精确OpenClaw SPIFFE ID后才转发。
- 移除OpenViking的Workload API挂载和凭据文件依赖。
- 删除 `spiffe_server/`、`entrypoint-spiffe.sh` 以及OpenViking镜像中的materializer构建和启动逻辑。

完成条件：上游OpenViking源码无修改；对端看到OpenViking SPIFFE ID；明文外部端口不可达；仓库中不再存在可启动旧OpenViking Python身份路径的代码。

### M4：TC-API/TDX 真实绑定

- 把PID、进程启动时间、容器ID和TC-API launch record关联起来。
- 接入真实TDX Quote、Rekor记录和生产形态Trustee验证。
- 验证pidfd只负责识别原进程退出，PID复用由当前进程启动时间、容器关系和旧launch record不一致而被拒绝；同时验证进程重启、证据篡改和policy更新路径。

完成条件：真实TDVM正向路径通过；关键负向路径明确拒绝。Mock通过不能替代该阶段。

### M5：生命周期与运行验收

- 验证SVID轮换、Broker重连、目标退出、重启和证书到期。
- 验证Registration Entry删除或授权变化导致的空快照/身份redaction，并确认旧身份立即失效。
- 验证长连接在身份失效后的处理策略。
- 增加attempt、ALLOW/DENY、attestation latency、rotation和target-exit指标。

完成条件：在规定的故障矩阵和持续运行窗口内没有身份串用、明文回退或旧PID身份复用。

### 单路径收敛原则

- Broker Sidecar、Broker Entry、自定义WorkloadAttestor和新验证脚本作为一个完整目标链路交付。
- 旧OpenViking身份获取和Python TLS wrapper在同一改造中删除，不保留运行时feature flag。
- OpenViking容器只运行原生内部HTTP服务；对外只暴露Broker Sidecar的mTLS端口。
- 部署、测试和文档统一以Broker Sidecar为唯一身份入口，避免两套语义并存。

## 15. 验证矩阵

### 15.1 正向

- Node Attestation成功。
- Broker通过普通 Workload API取得自身SVID。
- Sidecar验证Broker Endpoint服务端证书的精确Agent SPIFFE ID。
- Broker使用正确PID通过 Broker API取得OpenViking SVID。
- Agent日志显示 WorkloadAttestor输入为Python宿主机PID。
- Trustee返回ALLOW后出现自定义selectors。
- OpenClaw使用预期身份成功连接sidecar。
- 对端证书URI SAN等于OpenViking SPIFFE ID。
- OpenViking业务请求经过mTLS代理成功完成。

### 15.2 负向

- sidecar PID替代Python PID。
- 随机PID、已退出PID和被复用PID。
- 容器标签正确但镜像摘要错误。
- 镜像正确但TC-API/Rekor启动记录缺失。
- Quote、nonce/绑定或Trustee策略失败。
- 未授权Broker SPIFFE ID。
- 使用同一信任域中证书链有效但SPIFFE ID错误的服务端冒充Agent。
- 缺少Broker必需metadata。
- 试图通过TCP提交PID reference。
- Docker/Unix selectors正确，但自定义attestor返回DENY或失败。
- 保留或新增同一OpenViking SPIFFE ID的Docker-only弱Entry。
- 返回集合中缺少预期OpenViking SPIFFE ID。
- OpenClaw使用错误客户端SPIFFE ID。
- 绕过sidecar访问OpenViking明文端口。
- 目标退出后继续使用旧SVID建立新连接。

### 15.3 生命周期

- SVID正常轮换不中断新请求。
- Broker API短暂断开后按策略重连。
- 后续完整快照为空、不含目标身份或发生身份redaction后，清除旧TLS context并停止服务。
- 订阅以`PermissionDenied`结束后，清除旧TLS context并停止服务。
- OpenViking重启后使用新PID执行新attestation。
- Sidecar重启后不能从普通文件目录恢复旧私钥。

### 15.4 证据等级

测试结果必须分开记录：

1. 静态配置/代码检查；
2. mock Evidence Provider/Trustee软件链路；
3. Linux PID/pidfd和容器集成；
4. 真实TDX Quote/QGS与Rekor；
5. 生产Trustee和部署验收。

前一级通过不能替代后一级。

## 16. 最终成功标准

以下条件全部满足，才能认为目标完成：

1. OpenViking上游Python业务源码没有SPIFFE/SPIRE集成改动。
2. 旧 `spiffe_server/`、`entrypoint-spiffe.sh` 和OpenViking materializer启动逻辑已经删除，不存在旧路径运行开关。
3. OpenViking容器不访问 Workload API、Broker API和SVID私钥目录。
4. SPIRE Agent实际attest的是OpenViking Python宿主机PID。
5. Sidecar以pidfd识别原进程退出；WorkloadAttestor以进程启动时间、容器关系和TC-API launch record拒绝数值PID复用，进程重启后不能复用旧身份上下文。
6. 所有签发目标OpenViking SPIFFE ID的Registration Entry都强制包含自定义可信selector，不存在Docker-only、Unix-only或其他弱Entry。
7. Trustee DENY、证据失败或selector不匹配时，Broker拿不到OpenViking SVID，即使其他WorkloadAttestor仍返回selectors。
8. Broker只能通过UDS使用 `WorkloadPIDReference`，不能提交selectors或使用其他reference type。
9. Sidecar使用信任域bundle并精确验证预期Agent SPIFFE ID，不接受同一信任域中的其他服务端身份。
10. Sidecar把每个流响应作为完整快照；只有快照中包含 `spiffe://argus.local/service/openviking-cmem` 时才能进入或保持 `SERVING`。
11. 对端通过mTLS看到的是OpenViking workload身份，同时明确TLS由sidecar代理执行。
12. OpenViking明文端口对外不可达，无法绕过sidecar。
13. 目标退出、身份从后续快照移除、收到`PermissionDenied`或SVID到期后，sidecar不能继续建立新的有效mTLS连接。
14. 真实TDVM环境完成正向和关键负向验证；mock结果不被描述成生产远程证明。

## 17. 主要取舍

| 取舍 | 结果 |
| --- | --- |
| OpenViking源码零侵入 | 身份私钥和TLS握手由sidecar持有/执行 |
| 验证真实Python PID | pidfd用于监听原进程退出；启动时间、容器关系和launch record用于拒绝PID复用 |
| 使用官方Broker语义 | 采用SPIRE 1.15.2版本基线，并接受experimental接口风险 |
| Sidecar只传PID | WorkloadAttestor/Evidence Provider必须独立重建和验证进程属性 |
| mTLS入口外置 | 必须封闭OpenViking明文外部访问，避免代理旁路 |
| 长流处理SVID轮换 | 普通轮换不代表新的Quote；新鲜度策略需要显式触发重新attest |

## 18. 备注：TDVM Host Broker Gateway 备选形态

> 本备注只记录未来可能的架构形态。当前实现、测试和验收均以 Broker Sidecar 为唯一主方案；Gateway 不与 Sidecar 并行部署，也不作为失败时的回退路径。

Gateway 方案不改变 Workload Attestation 主链：它仍以实际 OpenViking Python PID 构造 `WorkloadPIDReference`，由 SPIRE Agent、自定义 WorkloadAttestor、Evidence Provider 和 Trustee 独立完成验证。区别仅在于，持有 OpenViking SVID 并终止 mTLS 的组件从“每个 OpenViking 实例一个 Sidecar”变为“每个 TDVM 一个长驻 Gateway”。

主方案中对Agent服务端精确身份校验、同目标ID弱Entry清理、完整快照替换和PID/pidfd生命周期的约束同样适用于Gateway；Gateway不能因为集中部署而放宽这些条件。

Gateway 的概念流程为：

```text
TDVM 启动 Host Broker Gateway
    -> Gateway 取得自身 Broker SVID
    -> TC-API 启动 OpenViking
    -> TC-API 通过受保护的本机通道登记 launch_id / PID / 内部端点
    -> Gateway 以该 PID 请求 OpenViking SVID
    -> Gateway 建立对应的 mTLS 入口并转发到 OpenViking
```

只有出现以下明确需求时，才需要重新评估 Gateway：

- 同一 TDVM 内运行多个需要 Broker 身份的 workload。
- 平台希望集中管理 mTLS 入口、SVID 订阅和代理升级。
- 不希望为每个 OpenViking 实例部署独立 Sidecar。

选择 Gateway 时，必须接受并处理额外代价：

- 维护 `launch_id -> PID -> pidfd/启动时间 -> 内部端点 -> SPIFFE ID` 的受信映射。
- 一个 Gateway 可能同时持有多个 workload 的私钥，需要更强的身份隔离。
- Gateway 故障可能影响 TDVM 内所有由它代理的 workload，共享故障域大于 Sidecar。
- Gateway 需要端口、SNI 或显式路由表，防止把某个 OpenViking 身份用到另一个实例。

官方 Broker API 规范的状态机示例是同节点 egress gateway。将相同的 Broker 语义应用于 OpenViking 入站 mTLS 是本项目的架构适配，不应表述为官方强制的入站 Gateway 模式。

## 19. 官方参考

由于 SPIFFE Broker规范仍为Incubating、SPIRE实现仍为experimental，本文核验和实现验收固定在以下版本。后续若升级SPIFFE规范或SPIRE版本，必须重新核对reference、生命周期、流响应和错误语义，不能无审查地跟随`main`。

- [SPIFFE Broker API specification（本次核验固定提交）](https://github.com/spiffe/spiffe/blob/dc4e9d9b4eff8aa181a54cd330ff9f877186060e/standards/SPIFFE_Broker_API.md)
- [SPIFFE Broker Endpoint specification（本次核验固定提交）](https://github.com/spiffe/spiffe/blob/dc4e9d9b4eff8aa181a54cd330ff9f877186060e/standards/SPIFFE_Broker_Endpoint.md)
- [SPIRE 1.15.2 Agent Broker API configuration](https://github.com/spiffe/spire/blob/v1.15.2/doc/spire_agent.md#spiffe-broker-api)
- [SPIRE 1.15.2 Broker API service implementation](https://github.com/spiffe/spire/blob/v1.15.2/pkg/agent/broker/api/service.go)
- [SPIRE 1.15.2 WorkloadAttestor aggregation implementation](https://github.com/spiffe/spire/blob/v1.15.2/pkg/agent/attestor/workload/workload.go)
- [SPIRE 1.15.2 upgrade compatibility](https://github.com/spiffe/spire/blob/v1.15.2/doc/upgrading.md)
- [SPIRE Plugin SDK 1.15.2 WorkloadAttestor API](https://github.com/spiffe/spire-plugin-sdk/blob/v1.15.2/proto/spire/plugin/agent/workloadattestor/v1/workloadattestor.proto)
- [SPIRE 1.15.2 changelog](https://github.com/spiffe/spire/blob/v1.15.2/CHANGELOG.md)
