# Argus TDX OpenViking 自定义 Workload Attestation 设计

> 状态：Proposed / Not Implemented
>
> 文档职责：解释为什么需要该能力、信任什么、各组件承担什么责任，以及系统应满足什么安全语义。
>
> 具体协议字段、代码改造和测试步骤见[实现文档](./Argus-TDX-OpenViking-Custom-Workload-Attestation-Implementation.md)。
>
> 目标 Workload SPIFFE ID：`spiffe://argus.local/service/openviking-cmem`

## 1. 背景

当前 `argus_tdx` Node Attestation 已经建立以下信任关系：

~~~text
TDX TDVM
  -> 独立 Evidence Provider
  -> SPIRE Agent
  -> 远端 Trustee 验证
  -> SPIRE Server 接纳 Agent
~~~

它回答的是：

> 这个 SPIRE Agent 是否位于满足 Node policy 的 TDVM 中？

它没有回答：

> 当前即将监听业务端口的 OpenViking Python 进程，是否是经过批准的服务实例？

当前 OpenViking 通过独立 `argus-svid-materializer` 访问 SPIRE Workload API，再把 SVID 以文件形式交给 Python server。SPIRE 看到的调用者是 materializer，而不是最终提供服务的 Python 进程。因此，现状最多形成容器或服务单元级身份，不能表述为 OpenViking Python 进程已经通过远程 Workload Attestation。

本方案把认证主体收紧到实际 serving process：OpenViking Python server 在监听业务端口前，以自身进程身份直接访问 Workload API；独立 Evidence Provider 测量该进程并生成真实 TDX Evidence；Trustee 完成远端裁决；SPIRE 只在裁决通过后向该进程交付目标 SVID。

## 2. 设计目标

本方案需要同时建立以下事实：

1. 复用 Node Attestation 已经建立的真实 TDX TDVM、启动基线和 Agent 信任；
2. 把后续 Workload Attestation 限定在该已入场 Agent 的本地 Workload API 请求链中；
3. 访问 SPIRE Workload API 的进程就是随后监听业务端口的 Python server；
4. 当前进程是该 OCI workload 的 init/main process，被解析到批准的 OCI manifest，且实际启动来自 manifest 的默认 Entrypoint/Cmd，没有用外部代码、配置或启动覆盖改变批准 artifact 的安全语义；
5. Workload Evidence 与 Trustee 当前 challenge、当前 Node 实例和当前进程实例绑定；
6. Trustee 返回与当前 session 严格匹配的 `ALLOW` 后，WorkloadAttestor 才能返回唯一强制 policy digest selector；
7. SPIRE Agent 仅在该 selector 命中当前 Agent 已获授权的 Registration Entry 后，才把该 Entry 对应的目标 SVID 返回 workload；
8. Python 取得精确目标 SVID 后才开始监听；
9. 证明窗口到期且不能重新验证时，服务停止接受连接。

## 3. 非目标

本方案不证明：

- OpenViking 没有代码漏洞；
- OpenViking 对所有业务输入都会产生正确输出；
- 取得 SVID 后进程永远不会被攻陷；
- Docker container 本身构成安全边界；
- TDX Quote 会自动理解 OCI manifest、PID 或业务配置的语义；
- 任一 RTMR 中出现某个事件就能单独证明当前进程仍在运行；
- 健康检查返回 200 就代表 workload 可信；
- stock SPIRE 可以瞬时撤销已经签发的 X.509-SVID；
- 普通 TDVM 内的进程观测可以防御已经控制 guest kernel/root 的攻击者。

系统最终证明的是一个有时效、受 policy 限定的远程验证结论，而不是对应用行为的永久保证。

## 4. 核心概念

### 4.1 Node Attestation 与 Workload Attestation

Node Attestation 建立 TDVM、独立 Evidence Provider 和 SPIRE Agent 的父信任。Workload Attestation 在此基础上识别 TDVM 内的具体进程，并决定该进程能否取得目标 workload SPIFFE ID。

~~~text
可信 TDVM / Evidence Provider / Agent
  └── 可信 OpenViking process instance
        └── OpenViking X.509-SVID
~~~

Node Attestation 不能替代 Workload Attestation。反过来，Workload Attestation 也不能绕过 Node admission 独立建立 TDVM 信任根。

本设计把“Node Attestation 已成功、SPIRE Agent 已进入正常工作状态”定义为 Workload Attestation 的强前置条件，而不是在 Workload 协议中再次证明的事实。自定义 WorkloadAttestor 只能由这个已入场 Agent 在处理本地 Workload API 请求时调用；如果 Agent 尚未入场或失去有效 Node 身份，后续 SVID 请求链本身不能成立。

### 4.2 进程级身份主体

本方案的 workload identity subject 是：

~~~text
python3 -m spiffe_server.server
~~~

不是以下对象：

- SVID materializer；
- entrypoint shell；
- Docker container label；
- OpenViking 镜像的抽象名称；
- 代理或 sidecar。

核心进程实例由以下字段定义：

~~~text
TD boot ID
+ SPIRE Agent 视角的 Workload API peer PID
+ process start ticks
~~~

Evidence Provider 必须在采集期间持有 `pidfd` 或等价的稳定进程引用，避免 PID 退出并复用后把不同进程拼接为同一份 Evidence。

这些字段只回答“是哪一个进程实例”，不能单独回答“它是不是 OpenViking serving process”。v1 还要求 Evidence Provider 验证：

- peer PID 等于 container runtime 记录的该 workload init/main PID；
- 该 PID 的实际可执行文件和参数符合批准 OCI manifest 所引用 image config 的默认 Entrypoint/Cmd；
- entrypoint 最终使用 `exec python3 -m spiffe_server.server`，取得 SVID 后不再 fork/exec 出另一个 serving process。

验证通过后，claims 只增加稳定枚举 `process_role: container_init`；不增加 executable/command digest。manifest 仍是唯一 artifact authority，实际启动检查只是证明当前进程遵守该 manifest 的运行契约。同镜像内的 helper 即使拥有相同 manifest digest，也因不是 container init/main process 而不能取得目标 selectors。

PID namespace、`cgroup`、container ID、完整 `NSpid`、mount/network namespace 和 uid/gid 可以由 Evidence Provider 用于解析或审计，但不进入核心密码学 claims。容器只帮助把 peer PID 解析到运行 artifact，不是身份安全边界。

### 4.3 `pre-serve`

`pre-serve` 是 Python server 的安全启动阶段。此时同一 Python 进程可以：

- 解析启动参数；
- 加载 OpenViking 模块和配置；
- 构造 ASGI application；
- 初始化进程内 Workload Identity Manager；
- 访问 SPIRE Workload API 并等待 SVID。

但不得：

- bind/listen 业务端口；
- 暴露 readiness；
- 启动能够接收外部请求的 Uvicorn serving loop；
- 把 SVID/private key 交给另一个进程再提供服务。

目标语义是：Workload API peer 和最终业务 listener 属于同一个 process instance。

### 4.4 独立 Evidence Provider

Evidence Provider 必须是独立进程，并同时服务两条链路：

~~~text
Node Attestation
  -> 生成 Node Evidence
  -> Trustee 建立 Node admission

Workload Attestation
  -> 根据 SPIRE peer PID 测量当前进程
  -> 生成 workload claims 和 Quote
~~~

这种解耦使 NodeAttestor、WorkloadAttestor 和业务 workload 都不直接拥有 Quote 生成能力。Evidence Provider 收集事实并检查本地一致性，但不拥有最终 allowlist；最终 policy 判断必须由 Trustee 完成。

Node 与 Workload 的继承关系不再引入额外的 per-boot key、客户端身份或 Evidence Provider 应用层签名。这个关系由 SPIRE 生命周期和 TDVM 内的受保护本地调用链建立；Trustee 不独立认证“是哪一个 WorkloadAttestor 客户端发起请求”。

这条“同一个 TDVM、同一个启动周期”的关系由四段事实共同建立，而不是由单个字段自报：

1. Node Attestation 已经建立包含 SPIRE Agent、Evidence Provider 和测量链的有效 Node admission；
2. SPIRE Agent 从 Workload API Unix peer credentials 获得当前 Python 调用者的真实 PID；
3. WorkloadAttestor 只通过受保护的本地 Unix socket，把该 PID 交给同一 measured guest TCB 内的 Evidence Provider；
4. Evidence Provider 生成的 fresh Quote 通过 REPORTDATA 绑定当前 challenge 和 workload claims；claims 中的 TD instance/boot context 只用于 Trustee 在 Verify 时查询并比较既有 Node admission。

Trustee 的 `ALLOW` 不是 bearer token，也不允许由 workload 提交给 WorkloadAttestor。它只作为 WorkloadAttestor 自己发起的当前 challenge/verify 调用的同步响应，并在本地立即转换为 selectors。外部调用者即使能够请求 Trustee，也不能把响应注入已入场 Agent 的 SPIRE 签发链。

这个结论依赖 `MEASURER_OK`、“guest kernel/root 非恶意”和受保护本地 socket 的既定边界。v1 不提供让 Trustee 脱离 SPIRE 本地生命周期、独立抵抗两个相同批准基线 TD 之间 Evidence relay 的能力；如果需要该能力，应复用 Node 已建立的认证通道另行设计，而不是把它隐含在 workload claims 中。如果要抵抗恶意 guest root，则应改用独立 workload TD。

### 4.5 Trustee verdict 与 SVID

Trustee 的 session record 保存并绑定：

- session 和 challenge；
- 目标 workload SPIFFE ID；
- policy；
- Verify 后匹配的 Node admission、process instance、`container_init` process role 和 OCI manifest；
- Quote 和 claims；
- 服务端审计时间和裁决。

Trustee 通过经过服务器身份认证的 TLS 连接向 WorkloadAttestor 返回严格 schema 的同步 verdict。线上 `ALLOW` 只包含 protocol version、decision 和当前 `session_id`；Node、process、artifact、policy、claims、时间和 Quote 摘要不在响应中重复回显。

WorkloadAttestor 必须检查 TLS server identity、response schema 和当前 `session_id`，只在本次同步请求收到 `ALLOW` 时返回强制 selectors。challenge expiry 由 Trustee 在 Verify 时执行；同步 verdict 不再定义另一套 expiry。其余完整性由 Trustee 对 session record、Evidence 和 policy 的验证保证，不再建立第二套摘要包装。

Verdict 不签发为可携带凭据，不交给 OpenViking，也不作为 Python 的第二个服务门。Python 可见的身份就绪信号只有 SPIRE 签发的精确目标 SVID。

### 4.6 证明窗口与 SVID 生命周期

以下两个时间概念必须分开：

~~~text
SVID TTL != Workload Attestation freshness
~~~

同一 Workload API stream 内的普通 SVID 轮换不代表生成了新的 TDX Quote。新的远程证明需要关闭旧 stream 并建立新 stream，从而触发新 challenge、新 Evidence、新 Trustee 裁决和新的 SVID 上下文。

Python 从新 stream 的 attestation attempt 开始使用 monotonic clock 计算本地 proof deadline：

~~~text
proof_deadline =
  attempt_started_monotonic
  + local_max_proof_window
  - safety_margin
~~~

Registration Entry 的 SVID TTL 不得长于配置的最大证明窗口。即使同一 stream 中证书被普通轮换，Python 也不得越过本地 proof deadline 继续服务。

## 5. 信任模型

“可信 OpenViking”定义为以下谓词全部成立：

~~~text
TEE_OK
AND NODE_ADMISSION_OK
AND MEASURER_OK
AND CURRENT_PROCESS_OK
AND FRESH_BINDING_OK
AND NODE_CONTEXT_OK
AND WORKLOAD_POLICY_OK
~~~

| 谓词 | 含义 |
|---|---|
| `TEE_OK` | Quote、collateral、TCB、TD attributes 和 debug policy 通过 |
| `NODE_ADMISSION_OK` | Node policy 已验证 MRTD 和平台选定的 stable boot RTMR，并且该 Agent/TD boot 的 admission 未被失效 |
| `MEASURER_OK` | 独立 Evidence Provider、WorkloadAttestor、Agent 和 runtime 解析链受 approved measured guest TCB 覆盖 |
| `CURRENT_PROCESS_OK` | Workload API peer PID、start ticks、boot ID 和稳定进程引用一致；该 PID 是 runtime 记录的 container init/main process，实际启动符合批准 manifest 的默认运行契约 |
| `FRESH_BINDING_OK` | Quote 绑定未使用且未过期的 Trustee challenge、claims 和 policy |
| `NODE_CONTEXT_OK` | Workload Attestation 由已入场 Agent 本地触发；claims 中的 TD instance/boot context 能关联到未失效的 Node admission，当前 Quote 的稳定测量与其一致。该 context 是本地生命周期关联值，不是 TDX 原生唯一实例身份证明 |
| `WORKLOAD_POLICY_OK` | OCI manifest 符合 Trustee 权威 policy；受度量 Evidence Provider 已按 v1 固定语义拒绝 runtime command override、外部代码/配置替换和可写代码路径 |

准确的证明表述应是：

> 一个已经完成 Node Attestation、包含受信任独立 Evidence Provider 的 TD，在指定时间对当前 OpenViking Python process instance 和其 OCI artifact 进行了观测；该观测与 Trustee challenge 和当前 TD instance/boot context 绑定，并通过 Trustee policy。

## 6. 权威 Workload Artifact

v1 使用 OCI manifest digest 作为 OpenViking artifact 的权威定义。成立前提是：

1. 镜像按 digest 拉取并由受信任 container runtime 校验；
2. manifest 覆盖的 config descriptor 和 layer descriptors 与运行 rootfs 的解析关系可审计；
3. 安全相关 rootfs 在运行期间不可变；
4. 不允许 bind mount、writable overlay、runtime command/env 或外部配置替换 OpenViking 代码和安全相关语义；
5. Evidence Provider 从 peer PID 出发，独立解析到实际运行的 OCI manifest digest。

核心 workload policy 只要求一个 artifact 字段：

~~~text
oci_manifest_digest
~~~

OCI manifest 引用 image config 和 layer descriptors；v1 要求安全相关启动参数和配置均固化在批准 artifact 中，并禁止运行时覆盖。`image_config_digest`、`rootfs_digest`、`executable_digest`、`application_digest`、`launch_digest` 和 `security_config_digest` 不再作为独立强制身份字段。它们可以用于构建验证或诊断，但不得形成多套互相竞争的 artifact authority。

如果部署必须允许镜像外注入安全配置，则该部署不满足 v1 artifact 模型，需要先版本化定义唯一的外部配置 authority，不能在实现中临时增加摘要字段。

## 7. TDX 能力的使用边界

| TDX 字段或能力 | 本方案中的用途 | 不能单独证明 |
|---|---|---|
| MRTD | Node policy 验证 TD 初始构建状态 | 当前 Python PID 和 OCI artifact |
| selected stable boot RTMR | Node policy 验证实际平台定义的启动测量；具体 index 由 TDVF/CCEL reference profile 决定 | 当前请求新鲜度或当前进程 liveness |
| 其他 RTMR | v1 不 extend、不 replay、不用于 Workload `ALLOW` | 当前进程或 artifact |
| MRCONFIGID | 启动器真实支持且 Node policy 定义语义时才验证 | 自动理解 init-data 语义 |
| REPORTDATA | 绑定当前 challenge、claims 和 TD instance/boot context | claims 本身真实 |
| Quote | 让 Trustee 远程验证 TD 测量和 REPORTDATA | 应用没有漏洞 |

### 7.1 MRTD 与 selected stable boot RTMR

Node Attestation 负责验证平台 TCB、MRTD 和由实际 TDVF/CCEL reference profile 选定的 stable boot RTMR，并生成 Node admission record。该记录至少保存：

~~~text
Agent SPIFFE ID
TD instance ID
TD boot ID
MRTD
selected stable boot RTMR map
Node policy digest
 admission state / invalidation metadata
~~~

Workload Attestation 不重新执行完整 Node Attestation，也不推进 Node record。Trustee 在 Verify 时根据 claims 中的 `td_instance_id + td_boot_id` 查询该 Agent/TD boot 未失效的 admission，并要求 fresh Quote 的 MRTD 和 selected stable boot RTMR 与该 record 一致；不一致时拒绝 workload，并要求重新完成 Node Attestation。v1 不给 Node admission 设置任意的数分钟 `max_age`：它随已入场 Agent 的当前 TD boot 生命周期有效，并在 boot 变化、Agent deauthorization、Node policy/reference 撤销或显式运维操作时失效。

这个生命周期不是 stock SPIRE 自动替 Trustee 维护的。实现必须把新 boot Node Attestation、Agent deauthorization、Node policy/reference 撤销和显式运维操作接入 Trustee record invalidation；在该集成完成前，只能证明 Node 曾经入场，不能声称 Trustee record 与当前 SPIRE 授权状态持续同步。

`td_instance_id` 不是 TDX Quote 原生提供的全局唯一 TD 身份。它和 `td_boot_id` 的权威来源、写入 Node record 与 Evidence Provider 读取的一致性必须在实现前关闭；在此之前，它只能表述为受度量本地链路使用的生命周期关联值，不能被宣传为独立防 relay 的硬件身份。

这里不预设 RTMR0、RTMR1 或 RTMR2 中哪一个一定代表 boot。必须先从真实 TDVF/CCEL event log 建立 reference profile，才能把相应 index 写入 Node policy。若现有启动链不能证明 Evidence Provider、SPIRE Agent、WorkloadAttestor 和 runtime 解析路径属于不可变 measured guest TCB，则 `MEASURER_OK` 尚未成立，不能宣称进程 claims 具有远程可信度。

### 7.2 Workload v1 不维护 RTMR 历史

Fresh REPORTDATA 已经把当前 challenge 和 process/artifact claims 绑定到 Quote。v1 不由 Workload Attestation extend 任何 RTMR，不定义 workload event log、writer、sequence、suffix 或 replay policy，也不把未被 Node reference profile 选中的 RTMR 用于 `ALLOW`。

如果后续需要证明 workload 加载历史或 IMA runtime integrity，应单独版本化 measurement event、唯一 writer、crash recovery 和 Trustee replay policy；该能力不属于 v1。

### 7.3 REPORTDATA 与摘要收敛

所有协议对象使用严格 closed schema 和 RFC 8785 JCS canonicalization。密码学绑定只保留两层摘要：

~~~text
claims_digest =
  SHA384(
    "argus-tdx-workload-claims-v1\0"
    || canonical(workload_claims)
  )

reportdata_binding =
  SHA384(
    "argus-tdx-workload-reportdata-v1\0"
    || canonical({
         session_id,
         nonce,
         claims_digest
       })
  )
~~~

`REPORTDATA[0:48]` 写入 `reportdata_binding`，`REPORTDATA[48:64]` 为零。

Workload 协议不传输 Trustee 内部 `node_admission_id`。Quote 绑定 session 和 nonce 后，Trustee 从 session record 恢复目标 SPIFFE ID、nonce 和 policy，再根据 claims 中的 `td_instance_id + td_boot_id` 查询有效 Node admission，并根据原始 claims 重建 REPORTDATA binding document；Evidence/Verify API 不重复传输 `claims_digest` 或 binding document。

Trustee 可以从 raw Quote 派生 `SHA256(raw_quote)` 作为内部审计索引，但它不属于协议绑定层，不作为 Evidence Provider 或 verdict 字段，也不再派生额外 transcript。

REPORTDATA 只负责密码学绑定。PID 和 manifest claims 的可信度来自受度量 Evidence Provider 的独立采集、Node admission 和 Trustee policy，而不是来自字段名称本身。

## 8. 架构与职责

~~~mermaid
flowchart LR
    O["OpenViking Python server"] -->|"direct Workload API stream"| A["SPIRE Agent"]
    A --> W["argus_tdx_workload WorkloadAttestor"]
    W -->|"peer PID + attestation context"| P["Independent TDX Evidence Provider"]
    P -->|"claims + Quote"| W
    W -->|"challenge / verify over server-authenticated TLS"| T["Remote Trustee"]
    T -->|"session-bound ALLOW / DENY"| W
    W -->|"policy digest selector on ALLOW"| A
    A -->|"matching X.509-SVID set"| O
~~~

### 8.1 OpenViking Python server

OpenViking 是身份的直接持有者，负责：

- 以本进程连接 Workload API；
- 在 `pre-serve` 等待精确目标 SVID；
- 在进程内构造和轮换 TLS context；
- 在本地 proof deadline 前主动重建 Workload API stream；
- 重新证明失败或 deadline 到达时停止服务。

如果 Python TLS 库要求证书文件，可以由同一个 Python 进程在私有 `/run` 路径短暂 materialize，再加载到内存。禁止由独立进程取得或转交 SVID。

### 8.2 SPIRE Agent 与 WorkloadAttestor

SPIRE Agent 从 Workload API Unix peer credentials 获得实际调用者 PID。自定义 `argus_tdx_workload` WorkloadAttestor 负责取得 challenge、调用 Evidence Provider、把 Evidence 交给 Trustee、检查当前 session 的 Trustee verdict，并只在 `ALLOW` 时返回一个稳定的 `policy_digest` selector。

Registration Entry 必须强制要求该自定义 `policy_digest` selector。这个 digest 对应 Trustee authority 中包含目标 SPIFFE ID、Node/TEE 要求、process role 和 OCI manifest allowlist 的完整 policy；Entry 不重复写 `verified=true`、service、role 或 manifest selector。Docker WorkloadAttestor 即使成功，也不能产生这个 selector 或绕过 Trustee 签发门。

SPIRE 1.15.1 的实际 X.509 Workload API 链路是：

~~~text
caller opens FetchX509SVID stream over Unix socket
  -> Agent peer tracker obtains caller PID and keeps a liveness watcher
  -> Agent invokes every configured WorkloadAttestor concurrently for that PID
  -> Agent aggregates selectors returned by successful attestors
  -> Agent subscribes to its local cache with the aggregate selector set
  -> local cache matches every authorized Entry whose selectors are a subset
     of the observed selector set
  -> Agent obtains/renews SVIDs for matching Entry IDs and streams all matching
     identities to the caller
~~~

其中 `Parent ID` 的位置需要准确理解：SPIRE Server 先按已认证 Agent 的 SPIFFE ID、Registration Entry Parent ID 层级和 node alias 计算该 Agent 有权获得的 Entry 集合，并同步给 Agent；每次 Workload API 请求的 selector 匹配发生在 Agent 本地授权缓存中。Agent 为缺失或将过期的 Entry SVID 请求签名时，Server 还会再次检查请求的 Entry ID 是否对该 Agent caller 授权。因此，“匹配当前 Agent Parent ID 下的 Entries”是正确的授权概念，但不是每个 Workload API 请求都携带 Parent ID 去 Server 现查。

`FetchX509SVID` 请求不携带目标 SPIFFE ID。一个 selector 集可能命中多个 Entry，SPIRE 会返回多个 SVID；本方案中的“精确目标 SVID”是指 Python 从返回集合中选择配置的 OpenViking SPIFFE ID，而不是客户端先向 Agent 指定该 ID。

还要注意 SPIRE 的聚合语义：单个 WorkloadAttestor 报错时，其 selectors 会被丢弃，但其他成功插件的 selectors 仍会保留。因此自定义插件的错误不等于整个 SPIRE attestation 调用全局报错；安全闭环依靠目标 Registration Entry 强制包含 `argus_tdx_workload:policy_digest:<approved>`。缺少该 selector 时，目标 Entry 不匹配，即使 Docker selectors 仍存在也不会取得 OpenViking SVID。

### 8.3 独立 Evidence Provider

Evidence Provider 负责：

- 在 Node Attestation 中生成 Node Evidence；
- 根据 SPIRE peer PID 建立 `pidfd` 或等价稳定进程引用并独立读取 `/proc`；
- 计算 process instance；
- 通过内部 cgroup/runtime 信息解析 OCI manifest digest；
- 拒绝会改变批准 artifact 安全语义的 runtime override、外部配置或可写代码路径；
- 构造 REPORTDATA 并获取真实 TDX Quote。

Evidence Provider 是独立进程；NodeAttestor 和 WorkloadAttestor 通过受保护的本地 Unix socket 调用它。Quote device 不暴露给 OpenViking。

### 8.4 Trustee

Trustee 是最终 Verifier 和 policy authority，负责：

- 生成一次性 challenge 并防止 replay；
- 验证 Quote、collateral、TCB 和 TD attributes；
- 在 Verify 时根据 TD instance/boot context 查询和验证 Node admission；
- 验证 fresh Quote 的 MRTD 和 selected stable boot RTMR 与 Node admission 一致；
- 验证 REPORTDATA 与当前 challenge、claims 和 TD instance/boot context 的绑定；
- 根据权威 policy 判断 OCI manifest 和不可变运行约束；
- 通过经过服务器身份认证的 TLS 连接返回绑定本次 session 的 `ALLOW` 或稳定 `DENY`。

Workload 只能提交 `policy_id`，不能提交一份新的宽松 policy 要求 Trustee 接受。

### 8.5 SPIRE Server

SPIRE Server 保持标准 Registration Entry 和 CA 职责，不直接解析原始 Quote。它根据已认证 Agent caller 和 Parent ID/alias 层级决定哪些 Entries 对该 Agent 授权，并在 Agent 请求为 Entry ID 签名时再次执行授权检查；Entry 的 selectors 和 SPIFFE ID 则由 Agent 本地缓存用于 workload 匹配和返回身份。

## 9. 目标生命周期

~~~mermaid
stateDiagram-v2
    [*] --> STARTING
    STARTING --> PRE_SERVE_LOADING
    PRE_SERVE_LOADING --> PRE_SERVE_ATTESTING
    PRE_SERVE_ATTESTING --> IDENTITY_READY: exact SVID
    PRE_SERVE_ATTESTING --> STOPPED: deny / timeout / invalid evidence
    IDENTITY_READY --> SERVING: create listener
    SERVING --> REATTESTING: proof renewal window
    REATTESTING --> SERVING: fresh attestation + new SVID context
    REATTESTING --> QUIESCING: proof deadline reached
    QUIESCING --> STOPPED
~~~

目标启动顺序是：

~~~text
entrypoint
  -> exec python3 -m spiffe_server.server
  -> Python enters pre-serve
  -> Python directly opens Workload API stream
  -> WorkloadAttestor obtains peer PID
  -> Evidence Provider measures current process and generates Quote
  -> Trustee returns session-bound ALLOW
  -> WorkloadAttestor returns the mandatory policy digest selector
  -> Agent aggregates selectors and matches authorized cached Entries
  -> Agent/Server obtain SVIDs for matching Entry IDs
  -> Python selects the exact target SVID from the returned identity set
  -> Python creates mTLS context
  -> same process bind/listen
~~~

生产模式要求单一 serving process。第一阶段不支持 Uvicorn reload、多 worker、Gunicorn worker、socket activation、继承预绑定 socket，以及取得 SVID 后再 fork/exec。

`exec` 不是 PID 证明的密码学必要条件，但它消除不需要的父 shell，简化容器主进程、信号和退出语义，因此作为生产生命周期约束。

## 10. 关键设计决策

| 事项 | 决策 | 原因 |
|---|---|---|
| 身份主体 | OpenViking Python process | 让取得身份和实际 serving 的主体一致 |
| Workload API caller | Python 自身 | 消除 materializer 中介语义 |
| 初始服务门 | `pre-serve` + exact SVID | 未通过 Trustee/SPIRE 签发链前不暴露端口 |
| Evidence 产生者 | 独立 Evidence Provider | 同时复用 Node/Workload Evidence，并隔离 Quote 能力 |
| 远端裁决 | Trustee | TD 内组件不能自报可信并自行批准 |
| Node/Workload 绑定 | Node Attestation 前置条件 + Agent 本地调用链 | 复用 SPIRE 已建立的 Agent/TD boot 信任，不重复认证 WorkloadAttestor |
| Artifact authority | OCI manifest digest | 在不可变 rootfs 约束下形成单一权威 artifact 定义 |
| 当前实例绑定 | boot ID + peer PID + start ticks，并在采集期间持有稳定进程引用 | 抵抗 PID 复用，避免把容器当作安全边界 |
| Serving role | peer PID 必须是 container init/main process，实际启动符合批准 manifest 默认 Entrypoint/Cmd | 防止同镜像 helper 仅凭相同 manifest 冒充 OpenViking |
| Node 测量 | MRTD + 平台选定的 stable boot RTMR | 只建立 measured guest TCB，不承担当前进程语义 |
| Workload RTMR | v1 不 extend、不 replay、不作为历史签发门 | 当前 Evidence 由 REPORTDATA 绑定，历史语义等待独立版本 |
| 当前请求绑定 | nonce + claims + REPORTDATA | 提供新鲜度并抵抗 Evidence replay |
| Trustee 结果 | 当前 verify 调用内的非 bearer verdict | 只回显当前 session，不重复时间、Evidence 摘要或建立第二套签名凭据 |
| 签发门 | Registration Entry 强制唯一自定义 policy digest selector | 防止其他 attestor 成功后绕过 Trustee，同时不重复 manifest/role/service 字段 |
| Python 服务门 | 精确目标 SVID + 本地 proof deadline | Python 不再直接消费 Trustee 产物 |
| 轮换 | 进程内 watcher | 私钥不经过其他进程 |
| 重新证明 | 新 Workload API stream | 区分证书轮换与新 TDX attestation |
| 失败语义 | fail closed | 初次失败不监听，证明到期失败则停服 |

## 11. 失败、续期与撤销语义

首次启动阶段，以下任一情况都必须导致自定义 WorkloadAttestor 不返回 policy digest selector；SPIRE 仍可能聚合其他插件的 selectors，但目标 Entry 因缺少该强制 selector 而不匹配，Python 收不到目标 SVID、不监听并退出：

- Workload API、Evidence Provider 或 Trustee 不可达；
- Trustee challenge/verify 失败；
- Quote/QGS、TCB、MRTD、selected stable boot RTMR 或 REPORTDATA 验证失败；
- TD instance/boot context 无法查询到未失效的 Node admission，process instance 不匹配，或 peer PID 不是批准 workload 的 container init/main process；
- OCI manifest 不匹配，或检测到安全相关 runtime override、外部配置或可写代码路径；
- Trustee verdict 缺失、过期、schema 错误或 `session_id` 不匹配；
- SPIRE 返回的 SVID SPIFFE ID 不精确。

运行阶段允许在当前 proof deadline 前重试重新证明；一旦 deadline 到达仍未得到新 stream 上的成功 attestation 和目标 SVID，Python 必须停止接受连接、关闭活跃连接、清理私钥材料并退出。

Stock SPIRE Registration Entry 没有动态 selector expiry，也不能保证瞬时撤销已签发证书。因此：

- X.509-SVID TTL 不得长于配置的最大证明窗口；
- Python 本地 proof deadline 独立于同 stream 的普通 SVID 轮换；
- 已签发证书的残余凭据窗口由较短 TTL 和进程停服共同限制；
- 如果需要低于 TTL 的即时撤销，必须额外设计 Trustee push 或 SPIRE Server 撤销机制，该能力不属于 v1。

TDX 不提供可直接替代上述机制的可信 wall clock。guest monotonic clock 可以避免普通 wall-clock 回拨，但不能对恶意 host 暂停 TD 后恢复提供绝对外部时间保证。更强要求需要在恢复或新连接前强制联系 Trustee。

## 12. Guest root 与测量边界

TDX 主要保护 TD 不受外部 host/VMM、网络和物理内存攻击。它不会自动防御 TD 内已经被控制的 kernel 或 root。

v1 的生产前置条件是：Evidence Provider、SPIRE Agent、WorkloadAttestor、container runtime 解析路径及其策略预装在不可变 guest image 中，并被 Node policy 实际验证的 measured boot 链覆盖。Node Attestation 必须发生在该 boot measurement 完成之后。只把组件安装进 TDVM、但没有进入 MRTD 或经 reference profile 验证的 stable boot RTMR，不能满足此前提。

具体实现可以采用受度量 initrd、只读 dm-verity 根文件系统，或已经验证 init-data/MRCONFIGID 语义的 Confidential Containers/Kata 隔离栈；文档不预先把任一方案视为已经成立。

如果威胁模型包含恶意 guest kernel/root，普通 TDVM 内的进程级观测不足以提供强隔离，需要把 OpenViking 放入独立 workload TD 或更小的受信执行边界。增加更多 PID 字段、workload RTMR event 或 guest 内凭据文件不能修复这个边界。

## 13. 设计完成标准

设计层面只有同时满足以下条件，才能进入生产实现验收：

1. 明确 OpenViking Python process 是唯一身份主体；
2. 明确独立 Evidence Provider 同时服务 Node 和 Workload Attestation；
3. 明确 Node admission 保存 MRTD、selected stable boot RTMR 和生命周期状态，Workload Verify 只查询并比较，不推进 Node record，也不使用任意数分钟 `max_age`；
4. 明确 Node Attestation 是强前置条件，并由 Agent 本地调用链继承 Node 信任；
5. 明确 OCI manifest digest 是唯一 artifact authority，并禁止安全相关 runtime override、外部配置和可写代码路径；
6. 明确核心进程 claims 与解析/审计字段的边界，并要求 peer PID 是遵守批准 manifest 默认启动契约的 container init/main process；
7. 明确 Workload v1 不 extend 或 replay 任何 RTMR，历史证明需求将单独版本化；
8. 明确 REPORTDATA 的收敛摘要结构，并避免额外 transcript 和重复 digest；
9. 明确 Trustee verdict 只是当前 verify 调用的同步响应，只回显 session，不回显重复摘要或时间，也不做签名凭据或文件挂载；
10. 明确 SVID、本地 proof deadline 和重新证明的不同语义；
11. 明确首次启动和运行中失败时的 fail-closed 行为；
12. 明确 stock SPIRE、TDX time 和 guest root 边界下不能提供的保证；
13. 明确 SPIRE 的 Parent ID 授权发生在 Server authorized-entry 集合与签名检查，selector 匹配发生在 Agent 本地缓存，Workload API 可返回多个 SVID；
14. 明确 Registration Entry 只强制一个 policy digest selector，不重复 manifest、role、service 或 `verified` 字段；
15. 实现文档能够把这些约束映射到具体模块、接口和测试。

## 14. 设计结论

完成并通过真实硬件验收后，系统可以作出以下受限但准确的声明：

> OpenViking Python server 在监听业务端口前，以自身进程作为 SPIRE Workload API 调用者。Workload Attestation 只在 Node Attestation 已成功的 Agent 内触发；独立 Evidence Provider 以稳定进程引用观测当前 Python process instance，确认它是遵守批准默认启动契约的 container init/main process，并解析权威 OCI manifest。远端 Trustee 验证 fresh TDX Quote、REPORTDATA，在 Verify 时按 TD instance/boot context 查询未失效的 Node admission，比较 MRTD 和 selected stable boot RTMR，并执行 Workload policy；当前 session `ALLOW` 后，自定义 attestor 返回唯一的 policy digest selector。SPIRE Agent 在 Server 已授权给本 Agent 的 Entries 中执行本地 selector 子集匹配，并把匹配身份集合返回 Python；Python 取得其中的精确目标 SVID 后才监听。证明到期且无法重新验证时停止提供连接。Workload v1 不维护 RTMR 历史。

## 15. 相关文档与规范

- [具体实现、协议和验收步骤](./Argus-TDX-OpenViking-Custom-Workload-Attestation-Implementation.md)
- SPIRE WorkloadAttestor v1 API：<https://github.com/spiffe/spire-plugin-sdk/blob/main/proto/spire/plugin/agent/workloadattestor/v1/workloadattestor.proto>
- SPIRE 1.15.1 workload attestor orchestration：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/attestor/workload/workload.go>
- SPIRE 1.15.1 Workload API handler：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/endpoints/workload/handler.go>
- SPIRE 1.15.1 peer tracker attestor：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/endpoints/peertracker.go>
- SPIRE 1.15.1 Agent workload cache：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/manager/cache/lru_cache.go>
- SPIRE 1.15.1 Server authorized entries：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/server/authorizedentries/cache.go>
- SPIRE 1.15.1 Server X.509-SVID service：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/server/api/svid/v1/service.go>
- RFC 8785 JSON Canonicalization Scheme：<https://www.rfc-editor.org/rfc/rfc8785>
- Linux Intel TDX attestation：<https://docs.kernel.org/arch/x86/tdx.html>
- Confidential Containers Runtime Attestation：<https://confidentialcontainers.org/docs/features/runtime-attestation/>
- Confidential Containers Init-Data：<https://confidentialcontainers.org/docs/features/initdata/>
