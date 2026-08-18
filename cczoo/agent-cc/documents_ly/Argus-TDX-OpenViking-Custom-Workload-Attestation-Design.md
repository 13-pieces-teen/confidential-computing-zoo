# Argus TDX OpenViking 自定义 Workload Attestation 设计

> 状态：Proposed / Not Implemented
>
> 文档职责：定义 V1 要证明的命题、最小信任链和组件边界。具体接口、代码改造和测试步骤见[实现文档](./Argus-TDX-OpenViking-Custom-Workload-Attestation-Implementation.md)。
>
> 目标 Workload SPIFFE ID：spiffe://argus.local/service/openviking-cmem

## V1 完成定义

V1 只完成首次目标身份交付前的一次 Workload Attestation：

~~~text
Node Attestation 已完成，SPIRE Agent 已入场
  -> OpenViking Python 在 pre-serve 阶段直接调用 Workload API
  -> SPIRE Agent 从 Unix peer credential 获得当前 Python PID
  -> 独立 Evidence Provider 测量该进程并解析实际 OCI manifest
  -> Evidence Provider 用 fresh TDX Quote 的 REPORTDATA
     单层绑定 Trustee session、nonce 和 workload claims
  -> Trustee 查询本次 boot 对应的 Node admission，验证 Quote 和 policy
  -> Trustee 返回当前 session 的 ALLOW
  -> WorkloadAttestor 仅返回唯一的 policy_digest selector
  -> SPIRE 返回匹配的 SVID 集合
  -> Python 精确选择目标 SPIFFE ID
  -> 同一个 Python 进程开始 bind/listen
~~~

以上闭环在真实 TDX 环境通过端到端验收，即为 V1 完成。V1 不包含运行期持续证明或即时撤销系统。

## 1. 要解决的问题

Node Attestation 回答：

> SPIRE Agent 是否位于满足 Node policy 的 TDVM 中？

它不直接回答：

> 当前请求 workload 身份并即将监听业务端口的 Python 进程，是否是批准的 OpenViking 实例？

当前 OpenViking 由独立 materializer 调用 Workload API，再把 SVID 文件交给 Python。此时 SPIRE 观察到的调用者是 materializer，不是最终 serving process。

V1 将身份主体收紧为实际 serving process：Python 自己请求 SVID，Evidence Provider 测量这个调用进程，Trustee 裁决后，SPIRE 才把目标身份返回给同一个 Python。

## 2. V1 范围与非目标

### 2.1 V1 必须证明

1. Node Attestation 已完成，当前 Workload 请求发生在已入场 Agent 的本地 Workload API 链路中；
2. Workload API peer PID 是 OpenViking 容器的 init/main process；
3. 该进程是预期的 Python serving process，并遵守批准镜像的默认启动契约；
4. 该进程解析到 Trustee policy 批准的 OCI manifest digest；
5. fresh Quote 把本次 session、nonce 和当前 workload claims 绑定到 REPORTDATA；
6. Trustee 能用待定的 boot-scoped node_context_id 查询到对应 Node admission；Node 启动测量是否合规沿用该 admission 的既有结论，不在 Workload Verify 中重复裁决；
7. 只有当前 session 获得 ALLOW，WorkloadAttestor 才返回唯一强制 policy_digest selector；
8. Python 只在取得精确目标 SVID 后开始监听。

### 2.2 V1 不证明

- OpenViking 没有代码漏洞或一定产生正确业务结果；
- Docker container 本身是安全边界；
- TDX Quote 自动理解 PID、OCI manifest 或配置语义；
- 取得 SVID 后进程永远不会被攻陷；
- 普通 TDVM 内的进程观测能够防御已经控制 guest kernel/root 的攻击者；
- 一次 Workload Attestation 自动形成运行期持续保证。

## 3. 信任模型

V1 依赖以下前提：

1. Node Attestation 已经接纳当前 SPIRE Agent；
2. 独立 Evidence Provider、SPIRE Agent、WorkloadAttestor 和必要的 runtime 解析路径属于 Node Attestation 接受的 guest TCB；
3. SPIRE Agent 与 Evidence Provider 之间使用受保护的本地接口；
4. guest kernel/root 不在本版攻击者模型内；
5. Trustee 是 Quote verifier 和 Workload policy authority。

对应的最小证明命题是：

~~~text
APPROVED_NODE
AND TRUSTED_LOCAL_OBSERVER
AND CURRENT_WAPI_CALLER
AND APPROVED_OCI_ARTIFACT
AND FRESH_QUOTE_BINDING
AND TRUSTEE_ALLOW
AND SAME_PROCESS_PRE_SERVE
~~~

Node Attestation 是强前置条件，不在 Workload 协议中重复执行。Workload Attestation 只补充“当前请求身份的具体进程及其 OCI artifact”这一层事实。

## 4. 身份主体与进程检查

V1 的身份主体是：

~~~text
python3 -m spiffe_server.server
~~~

它不是 materializer、entrypoint shell、容器 label、sidecar 或镜像名称。

SPIRE Agent 从 Workload API Unix peer credential 获得实际调用者 PID。Evidence Provider 以该 PID 为起点，在 Evidence 采集期间保持稳定进程引用，并确认：

1. peer PID 没有在采集期间被替换；
2. peer PID 等于 runtime 记录的 OpenViking init/main PID；
3. runtime 没有覆盖批准镜像的默认 Entrypoint/Cmd；
4. 当前 init/main PID 是预期的 Python serving process。

这里区分两个事实：

- runtime 配置证明容器没有覆盖镜像默认启动契约；
- 当前进程状态证明 init/main PID 是实际 Python serving process。

不能把 exec 后的 Python argv 简化为“等于 manifest 中的 shell Entrypoint”。

process role 是 Evidence Provider 的固定成功检查，不进入核心 claims，也不成为 Trustee policy 字段或 Registration Entry selector。检查失败时不生成有效 Workload Evidence。

PID namespace、cgroup、container ID、完整 NSpid、mount namespace 和 uid/gid 可以用于本地解析或审计，但不进入核心密码学身份。容器只帮助解析进程，不是身份安全边界。

## 5. 权威 Workload Artifact

V1 只使用一个 artifact authority：

~~~text
oci_manifest_digest
~~~

Evidence Provider 必须从 peer PID 和受信任 runtime 状态解析实际运行的 OCI manifest digest，而不是接受 workload 自报。

为了让该 digest 能代表批准的 OpenViking 代码，V1 只禁止以下情况：

- bind mount 或 writable overlay 替换 OpenViking 代码；
- 外部挂载替换 Python 模块；
- runtime override 替换批准的服务入口；
- 外部路径替换运行所需的可执行依赖。

普通业务配置、API key、模型参数、业务数据、记忆数据和日志不进入 workload identity。它们可以位于外部可写目录，只要不能替换上述代码、Python 模块、服务入口或可执行依赖。

V1 不再定义 image config、rootfs、executable、application、launch 或 security config 等并列 digest。若未来确实要把外部安全配置纳入身份，应在后续版本单独定义其 authority，不在 V1 临时增加字段。

## 6. Boot-scoped Node Context

Trustee 在 Workload Verify 时需要把 fresh Quote 关联到此前成功的 Node admission。V1 为此只保留一个抽象值：

~~~text
node_context_id
~~~

它必须满足：

1. 由成功的 Node Attestation 记录；
2. Evidence Provider 能从同一受信本地来源读取；
3. 在同一次 TD boot 内稳定，boot 变化后改变；
4. Trustee 能据此唯一查询本次 boot 对应的 Node admission。

wire encoding 固定为 32-byte opaque value 的 RFC 4648 base64url、无 padding；该编码不预设它的权威生成方式。

node_context_id 只是 boot-scoped 关联值，不宣称是 TDX 原生全局唯一硬件身份，也不独立提供跨 TD relay 防护。

当前 NodeAttestor 已验证的 instance_id、可选 launch_id 和 server binding store 尚未构成已经闭合的 boot-scoped context。node_context_id 的权威来源，以及 Node Attestation 与 Evidence Provider 如何读取同一个值，是唯一未闭合的 Node-to-Workload 关联设计项。在关闭前，不宣称现有字段可以直接复用，也不增加 td_instance_id、td_boot_id、node_admission_id 或其他并列实例字段。代码选型和真实环境依赖另见实现文档，不与这一安全关联问题混称为设计阻塞。

Node admission 中具体保存哪些 MRTD、RTMR 或其他测量，以及如何构造 reference profile，属于 Node Attestation 设计。本文只要求 Trustee 能通过 node_context_id 查询到 Node Attestation 已认可的 admission；Workload Verify 不重新比较 MRTD/RTMR，也不定义 RTMR index、event log、reference profile 或 MRCONFIGID 语义。Workload Quote 在本协议中负责提供 fresh REPORTDATA 绑定，不重新执行 Node Attestation。

## 7. Workload Claims 与 REPORTDATA

核心 workload claims 保持最小：

~~~json
{
  "node_context_id": "<boot-scoped-context>",
  "agent_view_pid": "4321",
  "process_start_ticks": "778899",
  "oci_manifest_digest": "sha256:<hex>"
}
~~~

目标 SPIFFE ID 和 policy 由 Trustee session 固定，不在 claims 中重复。process role 和容器解析字段也不进入 claims。

REPORTDATA 只保留一层摘要：

~~~text
reportdata_binding = SHA384(
  "argus-tdx-workload-reportdata-v1\0"
  || JCS({
       session_id,
       nonce,
       workload_claims
     })
)
~~~

48 字节 digest 写入 REPORTDATA，其余字节置零。Trustee 使用自己的 session record 和收到的原始 claims 重算该值。

上述内联对象只是双方重建的逻辑 hash 输入，不作为额外 binding document 在线上传输。协议不再定义或传输 claims_digest、challenge digest、Evidence digest、transcript digest 或额外应用层签名。Quote digest 如需记录，只是 Trustee 内部审计索引，不属于身份协议。

REPORTDATA 证明的是“这份 claims 属于本次 fresh challenge”。PID 和 manifest 是否真实，仍依赖受信任 Evidence Provider 的独立采集。

Workload 阶段真正使用的 TEE 信息只有 fresh TDX Quote 及其 REPORTDATA。Trustee 执行取得可信 REPORTDATA 所必需的标准 Quote 与 collateral 验证，再用 session 和 nonce 检查新鲜度；它不在 Workload policy 中再次裁决 MRTD、RTMR、TD attributes、debug 或 Node TCB reference。node_context_id、PID、start ticks 和 OCI manifest 都不是 TDX 原生 workload 字段，而是受信 Evidence Provider 采集并由 REPORTDATA 绑定的软件 claims。

在当前协议没有把既有 Agent 凭据或其安全通道定义为 workload claims 认证机制的前提下，如果连 fresh Workload Quote 也删除，Trustee 收到的只会是普通软件 claims，无法形成 TEE-backed 的 challenge 绑定。因此该 Quote 是本方案对 TEE 的最小使用，而不是第二次完整 Node Attestation。

## 8. 组件职责

### 8.1 OpenViking Python

- 在 pre-serve 阶段以本进程连接 Workload API；
- 等待 SPIRE 返回的身份集合；
- 只接受精确等于目标 SPIFFE ID 的 SVID；
- 取得目标 SVID 后才创建业务 listener；
- 不读取 Trustee 文件，也不接收独立进程转交的 SVID。

### 8.2 SPIRE Agent 与 WorkloadAttestor

- 从 Unix peer credential 获取调用者 PID；
- 为本次调用向 Trustee 获取 challenge；
- 把 PID、session 和 nonce 交给独立 Evidence Provider；
- 把 claims 和 Quote 提交 Trustee；
- 校验当前同步 verdict 的 session；
- 只在 ALLOW 时返回一个 policy_digest selector。

所有能够向当前 Agent 交付目标 OpenViking SPIFFE ID 的 Registration Entry 都必须强制包含该 selector。实施时必须替换现有同 SPIFFE ID 的 Docker-only Entry，不能在保留弱 Entry 的同时新增一条强 Entry。其他 WorkloadAttestor 即使成功，也不能生成该 selector，因此不能绕过 Trustee 交付门。

SPIRE Workload API 可能返回多个匹配 SVID；Python 必须从中精确选择目标 SPIFFE ID。本文不展开 SPIRE Server 缓存、Parent ID 或签名调用的内部实现。

这里约束的是目标 SVID 是否交付给 Python，不要求关闭 SPIRE Agent 对 Entry SVID 的正常预取；预取本身不能绕过 workload selector 匹配。

### 8.3 独立 Evidence Provider

Evidence Provider 是独立进程，同时服务 Node Attestation 和 Workload Attestation。它负责：

- 稳定观测 SPIRE 提供的 peer PID；
- 执行固定的 init/main process 与启动契约检查；
- 从实际 runtime 状态解析 OCI manifest；
- 构造最小 workload claims；
- 构造 REPORTDATA 并生成真实 TDX Quote。

OpenViking 不得访问 Evidence Provider 本地接口或 Quote device。

### 8.4 Trustee

Trustee 负责：

1. 创建短时、一次性的 session 和 nonce；
2. 执行标准 Quote 与 collateral 验证，取得并核对 REPORTDATA；
3. 根据 node_context_id 查询对应 Node admission；
4. 验证 OCI manifest 是否在目标 policy allowlist 中；
5. 原子消费 challenge；
6. 返回绑定当前 session 的同步 ALLOW 或 DENY。

ALLOW 不是 bearer token，不写入文件，也不交给 OpenViking。Python 可见的成功结果只有 SPIRE 返回的精确目标 SVID。

### 8.5 SPIRE Server

SPIRE Server 保持标准 Registration Entry 和 CA 职责，不直接解析原始 Quote。OpenViking Entry 只增加一个强制 policy_digest selector。

## 9. 最小失败语义

以下任一情况发生时，WorkloadAttestor 不返回 policy_digest selector：

- Node admission 无法由 node_context_id 查询；
- peer PID 已退出、被替换或不是目标 init/main process；
- 启动契约或 OCI manifest 解析失败；
- 外部挂载或 override 替换代码、Python 模块、入口或可执行依赖；
- Quote、REPORTDATA、challenge 或 Trustee policy 验证失败；
- Trustee 不可达或 verdict 不属于当前 session。

缺少强制 selector 时，目标 Registration Entry 不匹配。Python 收不到精确目标 SVID，因此不创建业务 listener。

V1 不引入 WAK、Receipt、Trustee 文件挂载、TruCon、Workload RTMR event/history 或第二套签名凭据。

## 10. V1 验收分层

### 10.1 V1 功能范围

Mock 或组件测试可以验证：

- Python 是 Workload API 直接调用者；
- peer PID 到进程和 OCI manifest 的解析链成立；
- 单层 REPORTDATA 在 producer 和 Trustee 两端一致；
- Trustee ALLOW 才产生唯一 policy_digest selector；
- Python 在精确目标 SVID 到达前不监听。

这些测试不能证明真实 TDX Quote 或 production reference values 已通过。

### 10.2 真实 TDX 验收

V1 只有在以下事实都由真实环境证明后才能宣称完成：

1. Node Attestation 已使用真实 TDX Evidence 接纳 Agent；
2. 独立 Evidence Provider 生成真实 fresh Quote；
3. production Trustee 使用真实 collateral 验证 Quote；
4. Trustee 根据 node_context_id 查询到对应 Node admission，并沿用该 admission 的 Node 测量结论；
5. peer PID、OpenViking init/main process 和最终 listener 属于同一个进程实例；
6. 该进程解析到批准的 OCI manifest；
7. REPORTDATA 精确绑定本次 session、nonce 和 claims；
8. SPIRE 只在 ALLOW 后返回目标身份，且同一 Agent 下不存在缺少强制 selector 的同 SPIFFE ID Entry；
9. Python 取得精确目标 SVID 后才监听。

Mock、真实 Quote、production Trustee 和真实进程级 SVID E2E 的证据必须分开报告。

### 10.3 后续能力

以下能力不属于 V1：

- 运行期持续保证和更快的撤销收敛；
- 把外部配置纳入 workload identity；
- IMA 或其他运行时完整性历史；
- Workload RTMR event log；
- 防御恶意 guest kernel/root 的独立 workload 隔离。

后续需求应单独版本化，不修改 V1 已完成命题。

## 11. V1 结论

完成真实 TDX 验收后，系统可以作出以下受限声明：

> 在一个已经通过 Node Attestation 的 TD boot 中，OpenViking Python 在监听前直接请求 SPIRE Workload API。独立 Evidence Provider 对该调用进程执行固定的 init/main process 与启动契约检查，从实际 runtime 解析批准的 OCI manifest，并用 fresh TDX Quote 的 REPORTDATA 单层绑定当前 Trustee session、nonce 和 workload claims。Trustee 查询对应 Node admission、验证 Quote 和 policy 后返回当前 session 的 ALLOW；WorkloadAttestor 随后产生唯一强制 policy_digest selector。Python 从 SPIRE 返回集合中取得精确目标 SVID 后，才由同一进程开始监听。

该声明只覆盖身份请求发生时的当前进程实例，不是对后续运行状态的永久保证。

## 12. 相关文档与规范

- [具体实现、协议和验收步骤](./Argus-TDX-OpenViking-Custom-Workload-Attestation-Implementation.md)
- SPIRE WorkloadAttestor v1 API：<https://github.com/spiffe/spire-plugin-sdk/blob/main/proto/spire/plugin/agent/workloadattestor/v1/workloadattestor.proto>
- SPIFFE Workload API specification：<https://github.com/spiffe/spiffe/blob/main/standards/SPIFFE_Workload_API.md>
- Linux Intel TDX attestation：<https://docs.kernel.org/arch/x86/tdx.html>
