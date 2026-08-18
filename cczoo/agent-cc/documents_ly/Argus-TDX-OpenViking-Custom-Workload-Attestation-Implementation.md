# Argus TDX OpenViking 自定义 Workload Attestation 实现文档

> 状态：Implementation Plan / Not Implemented
>
> 文档职责：把[设计文档](./Argus-TDX-OpenViking-Custom-Workload-Attestation-Design.md)中的信任语义映射为协议、模块、配置、实施阶段和验收用例。
>
> 实现原则：Node Attestation 成功是强前置条件；独立 Evidence Provider 同时服务 Node 和 Workload Attestation；先固定协议和失败语义，再依次打通 Node admission 复用、Workload Evidence、Trustee、SPIRE 签发和 OpenViking `pre-serve`；Mock 路径与真实 TDX 验收分层报告。

## 1. 实现范围

本功能拆成七个可独立验证、但有明确依赖关系的工作包：

| 编号 | 工作包 | 产出 | 依赖 |
|---|---|---|---|
| W0 | 协议与测试向量 | closed schema、canonicalization、digest fixtures | 无 |
| W1 | 独立 Evidence Provider 与 Node admission | Node 前置条件、Node record、stable boot measurement lookup | W0 |
| W2 | Workload Evidence | peer PID claims、OCI manifest、Quote | W0、W1 |
| W3 | Trustee workload verifier | challenge、Verify 时查询 Node admission、policy、session-bound verdict | W0、W1、W2 |
| W4 | SPIRE WorkloadAttestor | PID 驱动证明、verdict session 检查、selectors | W0、W2、W3 |
| W5 | OpenViking direct identity | Python 直接 Workload API、`pre-serve`、TLS context | W0、W4 |
| W6 | 重新证明与端到端验收 | proof deadline、stream reconnect、真实 TDX/QGS | W1～W5 |

依赖关系：

~~~mermaid
flowchart LR
    W0["W0 Protocol"] --> W1["W1 Evidence Provider + Node Admission"]
    W0 --> W2["W2 Workload Evidence"]
    W1 --> W2
    W1 --> W3["W3 Trustee"]
    W2 --> W3
    W2 --> W4["W4 WorkloadAttestor"]
    W3 --> W4
    W4 --> W5["W5 OpenViking"]
    W5 --> W6["W6 Re-attestation + E2E"]
    W3 --> W6
~~~

## 2. 当前仓库基线

### 2.1 已有实现基础

| 路径 | 可复用能力 |
|---|---|
| `cczoo/agent-cc/core/spire/plugins/argus-tdx-nodeattestor/` | 成对 Agent/Server NodeAttestor、challenge/Evidence/Trustee 软件协议 |
| `cczoo/agent-cc/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol/binding.go` | REPORTDATA binding 思路 |
| `cczoo/agent-cc/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol/schema.go` | 严格 schema 基础 |
| `cczoo/agent-cc/core/spire/plugins/argus-tdx-nodeattestor/internal/trustee/client.go` | 现有 NodeAttestor server-side Trustee client 的 HTTPS、server identity、timeout 和严格 response 校验思路 |
| `cczoo/agent-cc/core/argus/src/bin/evidence_provider.rs` | 已有独立 Evidence Provider HTTP 进程、`/ra/v1/evidence` 和 EvidenceEngine/TC-API 接入点；尚无 peer-PID workload API 和受保护 Unix socket |
| `cczoo/agent-cc/adapters/OpenViking/spiffe_server/server.py` | 单 worker Uvicorn server 和 TLS context 初始化点 |
| `cczoo/agent-cc/adapters/OpenViking/scripts/entrypoint-spiffe.sh` | 当前 materializer + Python 启动流程 |
| `cczoo/agent-cc/adapters/OpenViking/configs/Dockerfile.openviking` | 当前 OpenViking SPIFFE runtime image |

### 2.2 当前必须改变的行为

当前生产 SPIFFE 入口：

~~~text
entrypoint
  -> start argus-svid-materializer
  -> materializer opens Workload API stream
  -> materializer writes SVID files
  -> start python3 -m spiffe_server.server
~~~

目标行为：

~~~text
entrypoint
  -> exec python3 -m spiffe_server.server
  -> Python enters PRE_SERVE_ATTESTING
  -> Python opens Workload API stream itself
  -> same process receives exact SVID
  -> same process bind/listen
~~~

生产 attested image 不再允许通过 `ARGUS_SPIFFE_ENABLED=0` 静默回退到未认证 server。非 SPIFFE 运行方式使用明确分离的开发 profile 或入口。

## 3. 全局协议约束

### 3.1 版本

第一版协议固定：

~~~text
protocol_version = argus.tdx.workload.v1
~~~

未知版本直接失败，不做自动降级。

### 3.2 Canonicalization

所有 `canonical(...)` 都表示 RFC 8785 JSON Canonicalization Scheme（JCS）输出的 UTF-8 bytes。

统一要求：

- object key、string escaping、Unicode 和 number 编码遵循 JCS；
- digest、time、PID 和 process start ticks 使用 schema 规定的规范字符串；
- 二进制字段使用 RFC 4648 URL-safe、无 `=` padding 的 base64url；
- 拒绝重复 key、未知字段、非规范 digest 和 schema 规定之外的 number/string 互换；
- hash 输入使用固定 domain separator + JCS bytes；
- `agent_view_pid` 和 `process_start_ticks` 使用无前导零十进制 string；
- Go、Python 和 Trustee 必须共享同一组 golden fixtures。

### 3.3 Domain separators

固定使用：

~~~text
argus-tdx-workload-claims-v1\0
argus-tdx-workload-reportdata-v1\0
~~~

Node Attestation 与 Workload Attestation 不得复用同一个 domain separator。

### 3.4 收敛摘要定义

密码学绑定只保留以下两层摘要：

~~~text
claims_digest = SHA384(
    "argus-tdx-workload-claims-v1\0"
    || canonical(workload_claims)
)

reportdata_binding = SHA384(
    "argus-tdx-workload-reportdata-v1\0"
    || canonical(reportdata_binding_document)
)
~~~

JSON 中以 `sha256:<lowercase-hex>` 或 `sha384:<lowercase-hex>` 传输摘要。

不再定义以下重复层级：

~~~text
challenge_digest
evidence_digest
evidence_request_digest
transcript_digest
~~~

Challenge 由 `session_id + nonce` 唯一标识，并在 Trustee replay store 中绑定目标 SPIFFE ID 和 policy。Node admission 在 Verify 时根据 Evidence Provider 采集的 TD instance/boot context 查询。

## 4. W0：协议包与跨语言测试向量

### 4.1 产出

先固定以下 closed schema：

1. challenge request/response；
2. workload claims；
3. process instance；
4. REPORTDATA binding document；
5. Workload Evidence request；
6. Trustee verification verdict；
7. stable error code。

建议在 WorkloadAttestor 内建立最小 protocol package：

~~~text
cczoo/agent-cc/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol/
  schema.go
  canonical.go
  binding.go
  verdict.go
  testdata/
~~~

只有 Node 和 Workload 实现出现真实重复后，再提取共享库。本阶段不提前重构 Node 插件。

### 4.2 Golden fixtures

对所有参与 hash 的对象，至少提供：

- canonical JSON；
- JCS UTF-8 bytes 的 hex；
- digest；

对所有 closed schema，至少提供：

- 字段被改变后的失败样例；
- unknown/duplicate field 失败样例；
- number/string 类型互换失败样例。

Go、Python 和 Trustee 对同一 fixture 必须产生完全一致的 bytes 和 digest。

### 4.3 W0 完成标准

- 所有 schema 都是 closed schema；
- canonicalization fixture 三端一致；
- 所有 domain separator 固定；
- verdict 只绑定当前 session，不形成可转移 token；
- protocol 单测不依赖真实 TDX 硬件。

## 5. W1：独立 Evidence Provider 与 Node admission 前置条件

### 5.1 独立进程职责

Evidence Provider 是 TDVM 内独立进程，同时提供：

~~~text
NodeAttestor
  -> GenerateNodeEvidence

WorkloadAttestor
  -> GenerateWorkloadEvidence
~~~

NodeAttestor 和 WorkloadAttestor 通过 root-owned Unix socket 调用。OpenViking 不得访问该 socket 或 TDX Quote device。

实现优先扩展现有 `cczoo/agent-cc/core/argus/src/bin/evidence_provider.rs`，不得再创建一个仅供 Workload Attestation 使用的第二 Evidence Provider 进程。现有公开 HTTP listener、`0.0.0.0` 默认绑定和宽 CORS 只能保留在明确的开发 profile；生产 Node/Workload Evidence 操作迁移到受保护本地接口。

### 5.2 Node Attestation 前置条件

Workload Attestation 不是独立入场协议。实现必须满足：

~~~text
Node Attestation successful
  -> SPIRE Agent admitted and operational
  -> local Workload API request received
  -> Agent invokes argus_tdx_workload for the peer PID
~~~

WorkloadAttestor 不接受 workload 提交的 Trustee verdict，也不验证一个可携带授权。它自己创建 challenge session、调用本地 Evidence Provider、把 Evidence 发送给 Trustee，并只把当前同步 response 转换为 selectors。因此，workload protocol 不再定义独立客户端身份、客户端证书或 Node key 二次签名。

Trustee API 可以因网络访问控制或限流启用客户端认证，但该机制不参与 Node/Workload Attestation 语义，也不能替代 Quote、REPORTDATA、peer PID measurement 或 Registration Entry 的 Parent ID/强制 selectors。

生产实现还必须满足 measured guest TCB 前置条件：Evidence Provider、SPIRE Agent、WorkloadAttestor、container runtime 解析路径及其策略预装在不可变 guest image 中，并被 Node policy 实际验证的 measured boot 链覆盖；Node Attestation 发生在该 boot measurement 完成之后。仅在 TDVM 启动后安装这些组件，不能据此声称 `MEASURER_OK`。

### 5.3 Node admission record

Trustee 只有在 Node Quote、平台 TCB、MRTD、实际 TDVF/CCEL reference profile 选定的 stable boot RTMR 和 Node policy 验证通过后才建立：

~~~text
NodeAdmissionRecord {
  node_admission_id        // Trustee 内部索引，不进入 workload 协议
  agent_spiffe_id
  td_instance_id
  td_boot_id
  mrtd
  selected_stable_rtmr     // map<index, sha384>
  node_policy_digest
  verified_at
  state                     // active | invalidated
  invalidated_at            // optional Trustee audit field
}
~~~

这是 Workload verifier 所需的 Node record 最小投影。现有 Node Attestation 内部可以保留其他实现字段，但 Workload 协议不复制 Node 内部 record ID 或 key digest，也不新增 workload signing key。

Agent 尚未完成 Node Attestation，或者 admission 因 TD boot 变化、Agent deauthorization、Node policy/reference 撤销或显式运维操作而失效，都必须阻止目标 Workload SVID 的产生。v1 不设置任意的数分钟 Node admission `max_age`；record 随已入场 Agent 的当前 TD boot 生命周期有效。Workload Verify 根据 Evidence Provider 采集并绑定进 REPORTDATA 的 `td_instance_id + td_boot_id` 查询未失效的 Node record；客户端不选择或提交 `node_admission_id`。

Stock SPIRE 不会自动修改 Trustee 的 NodeAdmissionRecord。实现必须提供明确的 invalidation 集成：至少在观察到新 boot 的 Node Attestation 时原子失效旧 boot record，并为 Agent deauthorization、Node policy/reference 撤销和运维操作提供 Trustee 管理接口或受认证事件。该集成尚未完成时只能验证“Node 曾经入场”，不能宣称 Trustee record 已跟随当前 SPIRE 授权状态收敛。

`td_instance_id` 不是 TDX Quote 原生提供的全局唯一实例 ID。实现必须定义它在 Node Attestation 时如何生成/取得、如何写入 Trustee record，以及 Evidence Provider 如何从同一受度量本地来源读取。关闭该事项前，`td_instance_id + td_boot_id` 只能作为本地生命周期关联值，不能声称独立提供跨 TD relay 防护。

### 5.4 Node measurement 复用规则

Workload Attestation 不重新执行 Node Attestation，也不推进 Node record：

~~~text
record = lookup_active_node_admission(
    workload_claims.node_context.td_instance_id,
    workload_claims.node_context.td_boot_id,
)

require quote.mrtd == record.mrtd
for each (index, expected) in record.selected_stable_rtmr:
    require quote.rtmr[index] == expected
~~~

`selected_stable_rtmr` 的 index 不能在 Workload 实现中硬编码为 RTMR0、RTMR1 或 RTMR2。必须先用真实 TDVF/CCEL event log 建立 Node reference profile，并证明该测量覆盖 Evidence Provider、SPIRE Agent、WorkloadAttestor、container runtime 解析路径及其策略。Workload v1 不 extend RTMR，不传 event log，不验证 suffix，也不原子推进 RTMR head。

### 5.5 W1 测试

- Node Attestation 未完成时不会执行目标 Workload Attestation/SVID 签发链；
- 使用 `td_instance_id + td_boot_id` 能查询唯一有效 Node admission；
- Node admission 已失效、instance/boot 不匹配时拒绝；
- 新 boot admission 会原子失效同一 Agent 的旧 boot record；deauthorization/reference revoke 能触发 Trustee invalidation；
- OpenViking 无法提交或复用 Trustee verdict；
- Workload 协议中不存在 `node_admission_id`；
- fresh Quote 的 MRTD 和每个 selected stable boot RTMR 必须与 Node record 一致；
- Workload Verify 不改变 Node record 或任一 RTMR。

## 6. W2：Workload Evidence

### 6.1 输入

Evidence Provider 接收：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "agent_view_pid": "4321",
  "attestation_context": {
    "session_id": "<base64url>",
    "nonce": "<base64url>"
  }
}
~~~

`agent_view_pid` 必须来自 SPIRE Workload API Unix peer credential。WorkloadAttestor 把 SPIRE SDK 提供的整数 PID 转换为无前导零十进制 string 后传递；Evidence Provider 不能接受 workload 自己提交任意 PID。

`attestation_context` 只包含 Trustee response 中的 session 和 nonce；challenge expiry 由 Trustee 在 Verify 时执行，不需要传给 Evidence Provider。目标 SPIFFE ID、policy ID/digest 由 Trustee session record 固定，Evidence Provider 不接收或重复这些字段。TD instance/boot context 由 Evidence Provider 独立采集，不由 WorkloadAttestor 或 workload 选择 Node record。

### 6.2 核心 process instance

Evidence Provider 从 Node-local context 和 `/proc/<agent_view_pid>` 采集核心字段：

~~~json
{
  "td_instance_id": "<node-attested-instance-id>",
  "td_boot_id": "<uuid>",
  "agent_view_pid": "4321",
  "process_start_ticks": "778899",
  "process_role": "container_init"
}
~~~

验证规则：

- `agent_view_pid` 来自 SPIRE peer credential；
- Evidence Provider 在采集前打开 `pidfd` 或等价的稳定进程引用，并持有到 Quote 返回；
- `process_start_ticks` 来自 `/proc/<pid>/stat`；
- `td_instance_id` 使用 Node Attestation 已建立的同一 TD instance 定义，`td_boot_id` 来自当前 guest boot；
- Evidence 完成前再次读取 start ticks，进程引用失效或字段变化即判定 PID reuse/process replacement；
- container runtime 记录的该 workload init/main PID 必须等于 `agent_view_pid`；
- 实际 executable/argv 必须符合批准 OCI manifest 所引用 image config 的默认 Entrypoint/Cmd，且不存在 runtime command override；
- 只有上述 role/launch 检查通过，才能写入固定枚举 `process_role: container_init`；
- `td_instance_id + td_boot_id` 只作为 Trustee 在 Verify 时查询 Node admission 的上下文，不由 Evidence Provider 自行批准。

`process_role` 不是由 workload 自报的标签，也不是第二个 artifact authority。它表示受度量 Evidence Provider 已完成 container init PID 与 manifest 默认启动契约检查。同镜像内 helper 因不是 runtime init/main PID，必须在生成 Quote 前失败。

### 6.3 解析字段与审计字段

Evidence Provider 可以内部采集：

~~~text
完整 NSpid
cgroup path
container ID
PID namespace
mount/network namespace
uid/gid
executable path
container runtime metadata
~~~

这些字段用于：

- 从 peer PID 解析实际 OCI workload；
- 检查 PID、cgroup 和 runtime mapping 的内部一致性；
- 审计与排障。

它们默认不进入 `claims_digest`，也不作为 Registration Entry selector。容器不是安全边界。

### 6.4 权威 OCI artifact

Evidence Provider 必须从 peer PID 出发，通过受信任 container runtime 状态解析实际运行的 OCI manifest digest。

成立前提：

- 镜像按 digest 拉取；
- manifest descriptor 与解包 rootfs 的对应关系可审计；
- 安全相关 rootfs 不可变；
- 不允许 bind mount 或 writable overlay 替换 OpenViking 代码或安全相关依赖。

如果任何前提不成立，不能继续使用单一 OCI manifest digest 声明完整 artifact identity，必须先调整部署或重新定义 authority；不得静默增加多个互相竞争的摘要作为补救。

v1 要求安全相关 command、environment 和配置固化在批准的 OCI artifact 中，不允许 runtime override 或镜像外安全配置。Evidence Provider 必须拒绝能够替换代码、安全依赖或安全配置的 bind mount、writable overlay 和 runtime override。

因此 v1 不定义 `image_config_digest`、`rootfs_digest`、`executable_digest`、`launch_digest` 或 `security_config_digest` 等并列 authority。若未来必须支持外部安全配置，应先版本化唯一的外部配置 authority，而不是在 v1 中增加可选摘要。

### 6.5 Workload claims

规范对象固定为：

~~~json
{
  "version": "v1",
  "node_context": {
    "td_instance_id": "<node-attested-instance-id>",
    "td_boot_id": "<uuid>"
  },
  "process_instance": {
    "agent_view_pid": "4321",
    "process_start_ticks": "778899",
    "process_role": "container_init"
  },
  "artifact": {
    "oci_manifest_digest": "sha256:<hex>"
  }
}
~~~

计算：

~~~text
claims_digest = SHA384(
    "argus-tdx-workload-claims-v1\0"
    || canonical(workload_claims)
)
~~~

目标 SPIFFE ID、policy ID/digest、session 和 nonce 已由 Trustee session record 固定，不在 claims 中重复。普通业务数据、用户请求和可变记忆数据不进入 workload identity。

### 6.6 REPORTDATA binding

Binding document 固定为：

~~~json
{
  "session_id": "<base64url>",
  "nonce": "<base64url>",
  "claims_digest": "sha384:<hex>"
}
~~~

计算：

~~~text
reportdata_binding = SHA384(
    "argus-tdx-workload-reportdata-v1\0"
    || canonical(reportdata_binding_document)
)

REPORTDATA[0:48]  = reportdata_binding
REPORTDATA[48:64] = 0x00 * 16
~~~

TD instance/boot context、process 和 OCI manifest 已受 `claims_digest` 覆盖；目标 SPIFFE ID、policy ID/digest 和 expiry 由 `session_id` 指向的 Trustee session record 固定，不在 binding document 中重复。Agent ID 不属于 workload claims，由 SPIRE 的 Node admission 和 Registration Entry Parent ID 关系确定。

### 6.7 Quote digest

Evidence Provider 以第 6.6 节 REPORTDATA 获取真实 TDX Quote：

~~~text
quote_digest = SHA256(raw_quote_bytes)
~~~

`quote_digest` 由 Trustee 从 raw Quote 派生，只用于内部审计和关联，不属于协议绑定摘要。Evidence Provider 不传输该字段，verdict 也不回显它。它不再进入额外 transcript，也不产生 Evidence Provider 应用层签名；Evidence 的新鲜度与完整性由 Quote.REPORTDATA 覆盖的 challenge 和 claims 建立。

### 6.8 Evidence response

Evidence Provider 返回：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "workload_claims": {},
  "quote_format": "tdx-quote-v4",
  "quote": "<base64url>"
}
~~~

Trustee 必须根据原始 claims 和自己的 session record 重算 `claims_digest` 与 REPORTDATA binding；WorkloadAttestor 不接收 workload 提交的上述派生对象。Trustee 可从 raw Quote 计算 audit-only Quote digest。

### 6.9 W2 完成标准

- 独立 Evidence Provider 同时支持 Node 和 Workload 调用；
- 能从真实 SPIRE peer PID 生成确定性 process claims；
- 使用稳定进程引用拒绝 PID reuse/process replacement；
- 拒绝不是 container init/main PID 的 helper，并验证实际启动符合批准 manifest 默认 Entrypoint/Cmd；
- peer PID 能解析到权威 OCI manifest digest；
- rootfs 不可变、无安全相关 runtime override 和外部配置的前提有部署验证；
- REPORTDATA 与 golden fixture 一致；
- Evidence Provider 不持有额外 workload signing key；
- Workload Evidence 不产生或传输任何 RTMR event/history。

Evidence Provider 在 Quote 生成前失败时返回 provider-local error，WorkloadAttestor 直接结束本插件调用，不向 Trustee 伪造 `DENY`。最小错误码包括：

~~~text
PROCESS_NOT_FOUND
PROCESS_REUSED
PROCESS_ROLE_MISMATCH
PROCESS_ARTIFACT_MAPPING_MISMATCH
RUNTIME_CONTRACT_VIOLATION
QUOTE_GENERATION_FAILED
~~~

这些错误属于 TDVM 本地采集/一致性边界；只有 Trustee 实际收到结构正确的 Evidence 并完成 policy 路径后，才返回 Trustee `DENY`。

## 7. W3：Trustee Workload Verifier

### 7.1 Challenge API

~~~http
POST /v1/attest/tdx-workload/challenge
~~~

请求：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "target_spiffe_id": "spiffe://argus.local/service/openviking-cmem",
  "policy_id": "openviking-workload-v1"
}
~~~

Challenge 阶段不选择 Node admission。`agent_spiffe_id`、`td_instance_id`、`td_boot_id` 和 Trustee 内部 `node_admission_id` 都不由请求自报。Node record 在 Verify 时根据受 REPORTDATA 绑定的 workload claims 查询。

响应：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "session_id": "<base64url>",
  "nonce": "<base64url>",
  "issued_at": "<UTC-RFC3339>",
  "expires_at": "<UTC-RFC3339>",
  "policy_digest": "sha256:<hex>"
}
~~~

WorkloadAttestor 保留自己刚才发出的 `target_spiffe_id`、`policy_id`、响应中的权威 `policy_digest` 和 challenge expiry；只把 session 与 nonce 组成 Evidence Provider 的 attestation context。Challenge response 不重复回显请求字段。

Trustee 同时保存不可由客户端修改的 session record：

~~~text
ChallengeSessionRecord {
  session_id
  nonce
  target_spiffe_id
  policy_id
  policy_digest
  issued_at
  expires_at
  consumed
}
~~~

Trustee 只有在目标 SPIFFE ID/policy 组合合法时才签发 challenge。Challenge 必须使用 CSPRNG、短时有效、一次性消费，并绑定目标 SPIFFE ID 和 policy。API 客户端认证若启用，只用于访问控制和限流。

### 7.2 Verify API

~~~http
POST /v1/verify/tdx-workload
~~~

请求直接携带第 6.8 节 Evidence response：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "session_id": "<base64url>",
  "workload_claims": {},
  "quote_format": "tdx-quote-v4",
  "quote": "<base64url>"
}
~~~

WorkloadAttestor 必须提交自己取得的 `session_id`，并且不提供接受外部 verdict/Evidence 的接口。Trustee 重新解析 raw Quote 并重算所有摘要；不存在独立的 `evidence_request_digest` 或 Evidence transcript。

### 7.3 Trustee 验证顺序

按以下顺序快速失败：

1. schema 和 protocol version；
2. challenge 存在、未过期、未消费且请求绑定一致；
3. 从原始 claims 重算 claims digest，并可计算 audit-only Quote digest；
4. 验证 Quote signature、collateral、TCB、TD attributes 和 debug policy；
5. 从 session record 和 claims digest 重建 REPORTDATA binding，并与 Quote.REPORTDATA 比较；
6. 根据 claims 中的 `td_instance_id + td_boot_id` 查询唯一、有效的 Node admission；
7. 验证 fresh Quote 的 MRTD 和每个 selected stable boot RTMR 与 Node admission 一致；
8. 检查 process/artifact claims 的严格 schema，并要求 `process_role=container_init`；PID-to-OCI mapping、container init PID、实际启动、不可变 rootfs 和 runtime override 检查由受度量 Evidence Provider 在生成 Quote 前完成，Trustee 不假装远程重读 `/proc`；
9. 从 Trustee authority 加载 OpenViking policy，验证 session 中的 target/policy、claims 中的 OCI manifest 和固定 process role；
10. 生成 session-bound verdict，记录匹配的 Node admission，并原子消费 challenge。

Trustee policy 不从 workload 请求加载。Challenge request 只选择 `policy_id`；Trustee 从受保护配置读取权威 policy，把其 digest 写入 session，并在 Verify 时使用同一版本裁决。

### 7.4 最小 policy

~~~yaml
version: v1
policy_id: openviking-workload-v1

tee:
  type: tdx
  debug: false
  accepted_tcb_statuses: [UpToDate]
  mrtd: node_admission_match
  selected_stable_rtmr: node_admission_match

node_admission:
  require_active_admission: true

workload:
  target_spiffe_id: spiffe://argus.local/service/openviking-cmem
  allowed_oci_manifest_digests:
    - sha256:<approved-oci-manifest>
  required_process_role: container_init

binding:
  require_fresh_nonce: true
  require_report_data: true
  require_session_match: true
  require_current_process_binding: true
  max_session_age_seconds: 60
  max_proof_window_seconds: 600
~~~

Reference values 必须来自受控 TDVM baseline 和按 digest 发布的 OCI 构建，不允许第一次运行时自动学习并批准当前值。

无 runtime command override、无镜像外安全配置、代码 rootfs 不可变，以及 peer PID 等于 runtime init/main PID，是 `argus.tdx.workload.v1` Evidence Provider 的固定成功语义，不作为 Trustee policy 中可关闭的布尔开关。任一条件不成立时 Provider 在生成 Quote 前报错；Trustee 只允许选择批准 manifest 与固定 `container_init` role，不能通过宽松 policy 关闭这些本地不变量。

### 7.5 Session-bound verdict

Trustee 通过经过服务器身份认证的 TLS 连接返回 closed-schema verdict。Verdict 只供发起当前 verify 调用的 WorkloadAttestor 同步消费，不暴露给 OpenViking，也不作为 bearer token。

ALLOW response：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "decision": "allow",
  "session_id": "<base64url>"
}
~~~

Node、process、artifact、policy、claims、时间和 Quote 的绑定由 Trustee 在 session record、服务端审计和 Verify API 中检查，不在 verdict 里重复回显。WorkloadAttestor 只验证 Trustee TLS server identity、closed schema、`decision=allow` 和当前 `session_id`。challenge 是否过期由 Trustee 在消费 session 时判断；同步 verdict 不再定义独立 expiry。任何 unknown field、缺失字段或不一致都返回 attestation error。

进入 policy 裁决后产生的 `DENY` 使用独立 closed response schema，只包含 protocol version、`decision=deny`、session 和稳定 error code。裁决时间只记 Trustee 审计。传输错误、TLS server identity 错误、schema 错误或 Provider 本地错误直接失败，不伪造 policy verdict。

### 7.6 Stable error codes

至少定义：

~~~text
QUOTE_INVALID
TCB_REJECTED
TD_DEBUG_ENABLED
NODE_ADMISSION_INVALIDATED
NODE_CONTEXT_MISMATCH
BOOT_MEASUREMENT_MISMATCH
NODE_ADMISSION_NOT_FOUND
NONCE_REPLAYED
REPORT_DATA_MISMATCH
OCI_MANIFEST_MISMATCH
PROCESS_ROLE_MISMATCH
POLICY_MISMATCH
CHALLENGE_EXPIRED
~~~

### 7.7 W3 完成标准

- challenge replay store 生效；
- Node admission 能按 TD instance/boot context 在 Verify 时准确查询；
- fresh Quote 的 MRTD 和 selected stable boot RTMR 与 Node record 一致；
- Workload Verify 不 extend、replay 或推进任何 RTMR；
- policy 由 Trustee authority 加载；
- Quote、REPORTDATA、session 或 policy 任一失败都不能生成 `ALLOW`；
- WorkloadAttestor 能拒绝 TLS server identity、schema 或 session 错误的 verdict；
- 503、timeout、malformed 和 unknown fields 都失败关闭。

## 8. W4：SPIRE `argus_tdx_workload` WorkloadAttestor

### 8.1 SPIRE 1.15.1 基线流程

在插入自定义插件前，先固定 stock SPIRE 的实际 X.509 Workload API 语义：

~~~text
Workload process opens FetchX509SVID stream over the Agent Unix socket
  -> peer tracker obtains the kernel-associated caller PID
  -> Agent invokes all configured WorkloadAttestors concurrently for that PID
  -> selectors from successful plugins are aggregated
  -> Agent subscribes to its local workload cache with the selector set
  -> cache matches authorized Entries where Entry selectors are a subset of
     the observed workload selectors
  -> Agent obtains/renews SVIDs for the matching Entry IDs
  -> Agent streams every matching identity and later cache updates to caller
~~~

需要据此修正四个常见简化：

1. `FetchX509SVID` 请求是空请求，workload 不提交目标 SPIFFE ID；一个请求可以得到多个匹配 SVID。
2. `Parent ID` 是 Server 对 Agent 的 Entry 授权关系，不是 WorkloadAttestor selector。Server 根据已认证 Agent caller 的 SPIFFE ID，从直接以该 Agent 为 Parent 的 Entries 以及允许的 descendant/node-alias 路径构造 authorized-entry 集合并同步给 Agent；每次 workload selector 匹配发生在 Agent 本地缓存。
3. Agent 为匹配 Entry 生成或轮换 SVID 时调用 Server `BatchNewX509SVID`；Server 根据 Agent caller 再检查请求 Entry ID 是否授权，未授权返回 `entry not found or not authorized`。
4. 单个 WorkloadAttestor 失败时，SPIRE 1.15.1 记录错误并丢弃该插件 selectors，但保留其他成功插件的 selectors；只有 context 取消/超时才使聚合调用整体返回错误。

因此本设计的 fail-closed 点不是“自定义插件报错必然中断全部 Workload API 身份”，而是“OpenViking 目标 Registration Entry 必须包含唯一的自定义 policy digest selector”。自定义插件失败后目标 Entry 必然不匹配；其他无关 Entry 即使仍匹配，也不能成为 Python 的精确目标 SVID。

### 8.2 新增目录

~~~text
cczoo/agent-cc/core/spire/plugins/argus-tdx-workloadattestor/
  cmd/
  internal/config/
  internal/protocol/
  internal/evidence/
  internal/trustee/
  internal/verdict/
  internal/selectors/
~~~

最小职责：

1. 实现 SPIRE Agent WorkloadAttestor/Config service；
2. 接收 SPIRE 提供的 peer PID；
3. 使用固定目标 SPIFFE ID 和 policy 请求 Trustee challenge；
4. 让 Evidence Provider 为精确 PID 生成 Evidence；
5. 提交 Trustee verify request；
6. 检查 session-bound verdict；
7. 把本次通过的权威 policy digest 映射为唯一强制 selector。

任一错误直接返回 attestation error，不返回 policy selector，不使用 local/mock allow fallback。

### 8.3 Trustee 通道

至少要求：

- TLS 1.3 并验证 Trustee server identity；
- 使用显式 `trustee_ca_bundle_path` 建立服务器证书信任，并验证配置的 URI SAN `trustee_spiffe_id`；不得只比较字符串而不验证证书链；
- response 使用 closed schema，并回显本次 session；
- timeout、重试和连接复用不能越过 challenge expiry；
- 不在日志中记录 nonce 或可重放完整 Evidence。

如果部署因访问控制或限流启用 mTLS，它只负责 API 边界，不进入 workload claims、REPORTDATA 或 Node/Workload 信任语义。TLS server authentication 也不能替代 Quote 和 REPORTDATA request binding。

v1 依赖已入场 Agent、受保护本地 socket 和 measured guest TCB 把 WorkloadAttestor 与 Evidence Provider 连接起来，不额外定义 node-bound workload signing key。因此 Trustee verdict 只能由发起当前调用的本地 WorkloadAttestor 同步消费；若未来要求 Trustee 独立抵抗两个相同批准基线 TD 之间的 Evidence relay，应复用 Node 已建立的认证通道另行版本化。

### 8.4 Selectors

仅在 Trustee `ALLOW` 后返回一个 selector：

~~~text
argus_tdx_workload:policy_digest:sha256:<hex>
~~~

该 digest 对应 Trustee authority 中的完整 closed policy，其中已经包含目标 SPIFFE ID、Node/TEE 要求、`container_init` role 和 OCI manifest allowlist。Registration Entry 不再重复 materialize `verified:true`、service、process role 或 manifest digest；否则会形成多层重复字段，却不增加 Trustee `ALLOW` 之外的新安全事实。

nonce、session、PID、start ticks、container ID、manifest digest、Quote digest 和 timestamp 只进入 Evidence/Trustee policy/audit，不进入 Registration Entry。

### 8.5 W4 完成标准

- 插件拿到的 PID 等于 Workload API peer PID；
- 独立 Evidence Provider 是唯一 Quote 产生者；
- Evidence/Trustee 任一错误不会产生 policy selector；
- Trustee verdict 在映射 selectors 前已经完成 TLS server identity、schema 和 session 检查；
- 多 attestor 场景下自定义插件失败不能被 Docker selectors 绕过；
- Entry selector 子集匹配、Parent ID authorized-entry 范围、多 Entry/多 SVID 返回行为与 SPIRE 1.15.1 一致；
- telemetry 能区分 challenge、Evidence、Trustee verdict 和 selector mapping 失败。

## 9. W4 配套：SPIRE 配置与 Registration Entry

### 9.1 Agent 插件配置

OpenViking 目标身份只要求启用：

~~~text
argus_tdx_workload
~~~

Agent 可以因其他 Registration Entries 继续启用 Docker 等 attestor，但 OpenViking Entry 不依赖其 selectors；它们不属于本方案的安全门。

自定义插件配置至少包括：

~~~hcl
plugin_data {
  evidence_provider_socket = "/run/argus/evidence-provider.sock"
  trustee_endpoint = "https://trustee.argus.local"
  trustee_ca_bundle_path = "/run/spire/trust-bundle.pem"
  trustee_spiffe_id = "spiffe://argus.local/trustee"
  target_spiffe_id = "spiffe://argus.local/service/openviking-cmem"
  policy_id = "openviking-workload-v1"
  request_timeout = "30s"
}
~~~

不存在 Trustee 结果输出路径、detached verdict key 或 workload attestation client identity 配置。

### 9.2 Registration Entry

~~~bash
spire_server entry create \
  -parentID "spiffe://argus.local/spire/agent/argus_tdx/<agent-key-id>" \
  -spiffeID "spiffe://argus.local/service/openviking-cmem" \
  -selector "argus_tdx_workload:policy_digest:sha256:<approved-policy>" \
  -x509SVIDTTL 600
~~~

SPIRE 在一个 WorkloadAttestor 失败时可能仍保留其他成功插件的 selectors，因此 Entry 中这个唯一的 `argus_tdx_workload:policy_digest` 不能省略。它只在当前 Trustee session 对对应 policy `ALLOW` 后产生；插件失败时，Docker 等其他 attestor 无法伪造该 selector。SPIRE 的匹配规则是“Entry selectors 必须是调用者聚合 selectors 的子集”；调用者拥有额外 selectors 不影响匹配。

`<agent-key-id>` 是 Node Attestation 已经建立的 SPIRE Agent ID 组成部分，不是为 Workload Attestation 新增的 key。

### 9.3 Runtime mount

OpenViking container 只需要 SPIRE Workload API socket 和 Python 自身需要的私有运行目录。不得挂载：

- Trustee 生成的结果文件或目录；
- Evidence Provider socket；
- TDX Quote device。

## 10. W5：OpenViking Python 直接获取 SVID

### 10.1 Entrypoint

修改：

~~~text
cczoo/agent-cc/adapters/OpenViking/scripts/entrypoint-spiffe.sh
~~~

目标入口：

~~~bash
#!/usr/bin/env bash
set -euo pipefail

test -n "$SPIFFE_ENDPOINT_SOCKET"
test -n "$ARGUS_WORKLOAD_SPIFFE_ID"

exec python3 -m spiffe_server.server "$@"
~~~

删除 materializer 启动、等待、凭据目录轮询和双进程监管。

### 10.2 Dockerfile

修改：

~~~text
cczoo/agent-cc/adapters/OpenViking/configs/Dockerfile.openviking
~~~

操作：

- 删除 `argus-svid-materializer` build/copy；
- 加入选定的 Python SPIFFE Workload API client 依赖；
- 保留单进程入口；
- 不在 production image 中提供未认证 fallback；
- 保证安全相关 rootfs 满足 OCI manifest authority 的不可变约束；
- 禁止 production profile 覆盖安全相关 command/environment 或挂载镜像外安全配置。

Python Workload API client 的具体库和兼容版本是实现前必须关闭的选型项，不在文档中假定某个尚未验证的库。

### 10.3 `WorkloadIdentityManager`

在：

~~~text
cczoo/agent-cc/adapters/OpenViking/spiffe_server/
~~~

新增进程内 identity manager。最小职责是：

~~~text
record attempt_started_monotonic
  -> open Workload API stream
  -> wait for exact OpenViking X509Context
  -> build SSLContext
  -> set local proof deadline
  -> publish identity-ready
  -> watch ordinary SVID updates without extending proof deadline
  -> close/reopen stream for forced re-attestation
  -> fail closed at proof deadline
~~~

Python 至少验证：

- 返回的 SPIFFE ID 精确等于 `ARGUS_WORKLOAD_SPIFFE_ID`；
- trust domain 和 bundle 符合部署配置；
- X.509-SVID 尚未过期；
- SVID NotAfter 不越过允许的证书窗口；
- 当前 stream 是本次 attempt 新建的 stream。

Python 只消费 SPIRE 返回身份集合中 SPIFFE ID 精确等于 `ARGUS_WORKLOAD_SPIFFE_ID` 的 SVID，不解析 Trustee verdict 或读取 Trustee 生成的文件。Workload API 请求本身不能指定这个目标；返回其他 Entry 的 SVID 不代表 OpenViking identity ready，也不能代替目标 SVID。

### 10.4 `server.py` pre-serve gate

修改：

~~~text
cczoo/agent-cc/adapters/OpenViking/spiffe_server/server.py
~~~

顺序必须是：

~~~text
load config/application
  -> initialize WorkloadIdentityManager
  -> await initial exact SVID
  -> build TLS config
  -> only now call uvicorn.Server(config).serve()
~~~

在 identity ready 之前不能创建业务 listener 或 readiness endpoint。

第一版保持：

- `workers=1`；
- no reload；
- no Gunicorn/subprocess worker；
- no inherited or pre-bound business socket；
- no fork/exec after SVID acquisition。

### 10.5 TLS context 轮换

现有 `RotatingServerContext` 从 materializer 文件读取证书。目标改成：

- X509Context 由 Python 直接取得；
- Python 构造新的 `ssl.SSLContext`；
- 新握手使用当前 context；
- 旧连接按明确策略结束；
- 私钥不经过另一个进程；
- 同 stream 的普通证书轮换不更新 proof deadline。

如果 stdlib `load_cert_chain()` 必须使用文件，则由同一个 Python 进程写入仅自身可访问的私有 `/run` 目录，加载后按明确生命周期删除或替换。这属于进程内 materialization，不改变 Workload API caller。

### 10.6 W5 完成标准

- materializer 进程不存在；
- runtime state 与 `lsof/ss` 共同证明 Workload API caller、container init/main process 和监听 1943 的进程是同一个 process instance；
- SVID 到达前端口不可连接；
- 错误 SPIFFE ID 或缺失 SVID 均不监听；
- OpenViking container 中不存在 Trustee 结果文件挂载；
- 私钥仅由 Python process 和 SPIRE Agent 处理；
- 非 SPIFFE 开发模式与 production attested image 明确分离。

## 11. W6：重新证明和 proof deadline

### 11.1 本地 deadline

Python 在每次新建 Workload API stream 前记录：

~~~text
attempt_started_monotonic
previous_stream_generation
~~~

成功取得该 stream 上的目标 SVID 后计算：

~~~text
proof_deadline =
  attempt_started_monotonic
  + local_max_proof_window
  - safety_margin
~~~

从 attempt 开始计时，使网络、Quote 和 Trustee 延迟只会缩短本地有效窗口，不会延长。

`local_max_proof_window` 必须不大于 Trustee policy 的 `max_proof_window_seconds`，并与 Registration Entry SVID TTL 一起作为部署验收项。

### 11.2 强制重新证明

在 proof deadline 前的安全窗口：

1. 关闭当前 Workload API watch stream；
2. 增加本地 stream generation；
3. 新建 Workload API stream；
4. SPIRE 重新执行 `argus_tdx_workload`；
5. Trustee 产生新 session/nonce；
6. Evidence Provider 生成新 Quote；
7. WorkloadAttestor 检查新的 session-bound verdict 并返回 selectors；
8. Python 从新 stream 收到精确目标 SVID；
9. 更新 TLS context 与本地 proof deadline。

Python 不从任何 Trustee 文件判断成功。新 stream 返回目标 SVID 是对 Python 可见的成功结果；新的 Trustee session、Quote 和 verdict 由 WorkloadAttestor telemetry 与服务端审计证明。

不能因为同一旧 stream 收到普通轮换证书就判断重新证明成功，也不能用普通轮换延长 proof deadline。

### 11.3 到期失败

如果到 deadline 仍未在新 stream 取得目标 SVID：

~~~text
REATTESTING
  -> QUIESCING
  -> stop accepting new connections
  -> close active connections
  -> clear private-key material
  -> close Workload API stream
  -> process exit
~~~

Trustee policy/reference value 的远端吊销最迟在配置的 proof window 和现有 SVID TTL 边界内收敛。v1 不声称提供即时 push revocation。

### 11.4 时间边界

Python 使用本地 monotonic deadline 和 SVID NotAfter，但不能声称 guest monotonic clock 能抵抗恶意 host 长时间暂停 TD。若需求包含“TD 恢复后任何新连接前必须取得外部可信时间”，需要增加 resume detection + 在线 Trustee refresh，或把 Trustee 授权提升为每个 serving epoch/连接的在线门；这不属于 W6 默认 v1 范围。

## 12. 审计与指标

### 12.1 审计字段

至少记录：

- workload attestation attempt/session ID；
- Verify 时匹配的内部 Node admission ID、Agent SPIFFE ID；
- 目标 workload SPIFFE ID；
- TD instance/boot ID、peer PID、start ticks；
- OCI manifest 和 policy digest；
- MRTD 与 selected stable boot RTMR 比较结果；
- Quote digest；
- Trustee decision/stable error code；
- Trustee verdict accepted audit time；
- SVID serial/not-after；
- `PRE_SERVE -> SERVING -> REATTESTING -> QUIESCING/STOPPED` 状态变化。

container ID、cgroup、完整 namespace 可以记录在受控诊断日志中，但不作为核心身份字段。不得记录 private key、完整可重放 token、业务数据或完整原始 Quote。

### 12.2 最小指标

~~~text
argus_workload_attestation_attempts_total{result,error_code}
argus_workload_attestation_duration_seconds{stage}
argus_workload_quote_generation_seconds
argus_workload_trustee_verify_seconds
argus_workload_verdict_validate_total{result}
openviking_identity_state
openviking_proof_deadline_seconds
openviking_svid_not_after_seconds
openviking_reattestation_total{result}
~~~

Mock、真实 Quote/QGS 和 production Trustee 的指标必须带有明确环境标签，不能混合形成生产结论。

## 13. 测试拆分

### 13.1 单元测试

| 模块 | 必测内容 |
|---|---|
| Protocol | JCS、domain separator、claims/reportdata digest、unknown field、类型错误 |
| Evidence Provider | Node/Workload 双路径、本地 socket 权限、Quote device 隔离 |
| Process claims | peer PID、start ticks、稳定进程引用、PID reuse、process instance、container init/main role |
| OCI mapping | PID 到 manifest、默认 Entrypoint/Cmd、不可变 rootfs、bind mount/overlay/runtime override 拒绝 |
| Node measurements | MRTD 和 selected stable boot RTMR 匹配；Workload Verify 不修改 Node record 或 RTMR |
| Node precondition | Node 未入场、admission 已失效、instance/boot mismatch、Parent ID/强制 policy selector |
| Trustee verifier | nonce replay、session/claims/Quote 绑定、policy 裁决 |
| Trustee channel | CA bundle、TLS server identity、response schema 和 session |
| WorkloadAttestor | fail closed、verdict session 检查、selector mapping |
| Python manager | exact SPIFFE ID、proof deadline、stream reconnect、TLS context swap |

### 13.2 Mock 集成

Mock Evidence Provider + Mock Trustee 只验证：

- 独立 Provider 的 Node/Workload 调用 contract；
- Workload API peer PID 传递；
- challenge/Evidence/session-bound-verdict contract；
- Verify 时按 TD instance/boot context 查询 Node record 并比较稳定测量；
- Workload v1 不产生、传输或 replay RTMR history；
- selector 和 Registration Entry；
- Python `pre-serve` gate；
- SVID 轮换、forced reconnect 和失败状态机；

Mock 通过不能证明 TDX Quote、QGS、collateral 或真实 reference values 已通过。

### 13.3 真实 TDX 集成

至少验证：

1. TSM ConfigFS/libtdx_attest/QGS 生成真实 Quote；
2. Trustee 使用真实 collateral 验证；
3. 依据真实 TDVF/CCEL reference profile 选定 stable boot RTMR，不硬编码 index；
4. MRTD 和 selected stable boot RTMR 与 Node admission 对齐；
5. Workload Attestation 不 extend 或 replay 任何 RTMR；
6. REPORTDATA 精确匹配本次 challenge/claims；
7. Workload Attestation 只由已完成真实 Node Attestation 的当前 Agent 本地触发；
8. peer PID 等于 runtime init/main PID，实际启动符合批准 manifest 默认 Entrypoint/Cmd；
9. peer PID 解析到实际按 digest 运行的 OCI manifest；
10. OpenViking Python process 取得真实 end-to-end SVID。

### 13.4 负向矩阵

| Case | 预期结果 |
|---|---|
| 非 TDX 或无法取得 Quote | 无 SVID，Python 不监听 |
| debug TD | Trustee DENY |
| TCB/MRTD/selected stable boot RTMR mismatch | Trustee DENY |
| Node admission 已失效 | Trustee DENY |
| Trustee TLS server identity 错误 | WorkloadAttestor error，无 selector |
| 重放旧 nonce/Quote/Evidence | Trustee DENY |
| REPORTDATA claims 被篡改 | Trustee DENY |
| TD instance/boot context 无法查询唯一且未失效的 Node admission | Trustee DENY |
| PID start ticks/boot ID mismatch 或稳定进程引用失效 | Evidence Provider error；不调用 Verify，无自定义 selector |
| peer PID 不是 container init/main PID，或实际启动不符合批准 manifest 默认 Entrypoint/Cmd | Evidence Provider error；不调用 Verify，无自定义 selector |
| peer PID 无法解析到权威 OCI manifest | Evidence Provider error；不调用 Verify，无自定义 selector |
| 存在 runtime override、外部安全配置或可写代码路径 | Evidence Provider error；不调用 Verify，无自定义 selector |
| Quote 已生成且 Trustee 检查发现 OCI manifest/process role 不符合 policy | Trustee DENY |
| Trustee verdict schema 或 session 错误 | WorkloadAttestor error，无 selector |
| Trustee 503/timeout/malformed | fail closed |
| 自定义 attestor 失败而 Docker attestor 成功 | Entry 不匹配，无目标 SVID |
| 同镜像 materializer/helper 调用 Workload API | `process_role=container_init` 本地检查失败；目标 Entry 不匹配 |
| SVID SPIFFE ID 错误 | Python 拒绝，不监听 |
| proof 到期且重新证明失败 | 停止监听并退出 |
| 普通 SVID 轮换 | 不更新 proof deadline |
| 强制 stream reconnect | 新 challenge、新 Quote、新 verdict、新 SVID |

## 14. 端到端验收标准

仅在以下事实同时成立时，进程级 Workload Attestation 通过：

1. 独立 Evidence Provider 同时服务 Node 和 Workload Evidence；
2. Workload API Unix peer、runtime 记录的 container init/main process 与监听 1943 的 Python process 是同一进程；
3. process instance 由 boot ID、peer PID 和 start ticks 绑定，Evidence 采集期间持有稳定进程引用，实际启动符合批准 manifest 默认 Entrypoint/Cmd；
4. Trustee 收到真实 TDX Quote 和当前 challenge；
5. Workload Attestation 由已入场 Agent 本地触发，Trustee 在 Verify 时按 TD instance/boot context 查询到未失效的 Node admission；
6. fresh Quote 的 MRTD 和 selected stable boot RTMR 与 Node admission 一致；
7. Workload Attestation 不 extend、传输或 replay 任何 RTMR history；
8. REPORTDATA 精确匹配当前 challenge 和包含 TD instance/boot context 的 claims；
9. peer PID 被解析到批准的 OCI manifest，rootfs 不可变前提成立；
10. 安全相关 command、environment 和配置固化在批准 artifact 中，不存在 runtime override；
11. Trustee 通过经过服务器身份认证的 TLS 返回当前 session 的 `ALLOW` verdict；
12. WorkloadAttestor 检查同步 verdict 的 schema 和 session，然后返回唯一强制 policy digest selector；
13. SPIRE Agent 在 Server 已授权给当前 Agent 的本地 Entry 缓存中执行 selector 子集匹配；Server 对需要签名的 Entry ID 再做 caller authorization；
14. SPIRE 可返回多个匹配 SVID，Python 只把其中的精确目标 SPIFFE ID 视为 identity ready；
15. OpenViking 不存在 Trustee 结果文件或挂载；
16. 身份到达前 1943 不可连接，到达后开始监听并完成精确 SPIFFE mTLS；
17. 普通 SVID 轮换不延长 proof deadline；
18. proof 到期重新证明失败时，监听和连接按策略关闭；
19. 验收材料明确区分 Mock、真实 TDX/QGS、production Trustee 和生产接受。

## 15. 推荐实施顺序与提交边界

### 阶段 A：协议先行

1. W0 schema/canonicalization/golden fixtures；
2. 收敛 claims/REPORTDATA digest；
3. Trustee TLS server identity 和 session-bound verdict contract；
4. stable error code。

完成门：无需 TDX 硬件即可通过跨语言协议测试。

### 阶段 B：建立 Node 到 Workload 的信任继承

1. 独立 Evidence Provider 的本地 Unix socket；
2. Node Attestation 成功到 WorkloadAttestor 可运行的生命周期门；
3. Trustee Node admission record；
4. 新 boot、Agent deauthorization 和 policy/reference revoke 到 Trustee record 的 invalidation 集成；
5. Verify 时按 TD instance/boot context 查询 record，并比较 MRTD 和 selected stable boot RTMR。

完成门：Node 尚未入场或 admission/instance/boot 无效时不能产生目标 SVID，授权/boot 变化能失效旧 record，Workload Verify 不修改 Node record 或 RTMR。

### 阶段 C：完成远程 workload verdict

1. 精简 PID/process claims；
2. peer PID 到 OCI manifest 解析；
3. REPORTDATA + real/mock Quote provider；
4. Trustee challenge/verify/policy/session-bound verdict；
5. WorkloadAttestor + selectors。

完成门：Mock E2E 和真实 Quote 组件测试分开通过，失败路径无 policy digest selector。

### 阶段 D：改变 OpenViking 进程语义

1. Python Workload API client；
2. `WorkloadIdentityManager`；
3. `pre-serve` gate；
4. 进程内 TLS context；
5. 删除 production materializer path。

完成门：实际 Workload API caller 和 listener 是同一进程，OpenViking container 不包含 Trustee 结果通道。

### 阶段 E：续期和真实端到端验收

1. 本地 proof deadline；
2. forced stream reconnect；
3. quiesce/exit；
4. TDVM + QGS + Trustee + SPIRE + OpenViking E2E；
5. 完整负向矩阵和证据分层报告。

完成门：满足第 14 节全部条件。

建议每个阶段独立提交，避免把 Node protocol、Evidence Provider、Trustee、SPIRE plugin 和 OpenViking runtime 的大规模改动压在一个不可审阅提交中。

## 16. 实现前必须关闭的事项

以下事项会实质改变代码或信任边界，不能在实现中默认为某个答案：

1. Python 直接 Workload API client 的库和 SPIRE 1.15.1 兼容版本；
2. Python stdlib TLS 的进程内 private-key 临时文件策略；
3. Evidence Provider 独立进程的部署、socket ACL 和生命周期；
4. 从 peer PID 到 OCI manifest digest 的实际 runtime 接口；
5. 不可变 rootfs、bind mount 和 writable overlay 的部署强制方式；
6. Node Attestation 中 `td_instance_id`、`td_boot_id` 的权威来源、唯一性和生命周期；
7. SPIRE Agent deauthorization、新 TD boot 和 policy/reference revoke 如何驱动 Trustee Node admission invalidation；
8. 真实 TDVF/CCEL reference profile 中哪些 RTMR index 构成 stable boot measurement；
9. Evidence Provider/WorkloadAttestor/runtime 解析路径如何进入不可变 measured guest baseline；
10. Trustee 使用的真实 Quote verification backend 和 collateral 运维；
11. production reference-value 的发布、版本和撤销流程；
12. `max_session_age`、proof window、SVID TTL、retry window 和 safety margin；
13. 重新证明失败时活跃连接的最大 drain 时间；
14. 是否需要超越短 TTL 的即时撤销能力；
15. 是否需要针对 TD suspend/resume 的外部时间重新验证。

这些事项关闭前，可以完成协议和 Mock 软件链路，但不能宣称生产级强进程证明完成。

## 17. 证据分层与交付物

每个阶段的报告必须标记证据等级：

| 等级 | 可支持的结论 |
|---|---|
| 单元/协议测试 | schema、digest、session binding 和本地状态机正确 |
| Mock Evidence + Mock Trustee | 软件组件和故障链路可运行 |
| TDVM 软件部署 | 独立 Provider、socket、PID、OCI mapping 和启动顺序正确 |
| 真实 Quote + QGS | 硬件 Evidence 路径可用 |
| production Trustee/reference values | 远端 policy、Node admission、MRTD/selected stable boot RTMR 和真实 collateral 可用 |
| 真实进程级 SVID E2E | Python process 证明与 SVID 签发闭环成立 |
| 生产接受 | 运维、轮换、负向矩阵和长期运行全部验收 |

低一层证据不能替代高一层结论。

## 18. 参考规范

- [设计文档](./Argus-TDX-OpenViking-Custom-Workload-Attestation-Design.md)
- SPIRE WorkloadAttestor v1 API：<https://github.com/spiffe/spire-plugin-sdk/blob/main/proto/spire/plugin/agent/workloadattestor/v1/workloadattestor.proto>
- SPIRE 1.15.1 workload attestor orchestration：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/attestor/workload/workload.go>
- SPIRE 1.15.1 Workload API handler：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/endpoints/workload/handler.go>
- SPIRE 1.15.1 peer tracker attestor：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/endpoints/peertracker.go>
- SPIRE 1.15.1 Agent manager synchronization：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/manager/sync.go>
- SPIRE 1.15.1 Agent workload cache：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/agent/manager/cache/lru_cache.go>
- SPIRE 1.15.1 Server authorized entries：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/server/authorizedentries/cache.go>
- SPIRE 1.15.1 Server X.509-SVID service：<https://github.com/spiffe/spire/blob/v1.15.1/pkg/server/api/svid/v1/service.go>
- SPIFFE Workload API specification：<https://github.com/spiffe/spiffe/blob/main/standards/SPIFFE_Workload_API.md>
- RFC 8785 JSON Canonicalization Scheme：<https://www.rfc-editor.org/rfc/rfc8785>
- Linux Intel TDX attestation：<https://docs.kernel.org/arch/x86/tdx.html>
