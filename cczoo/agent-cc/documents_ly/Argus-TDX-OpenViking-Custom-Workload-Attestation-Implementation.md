# Argus TDX OpenViking 自定义 Workload Attestation 实施文档

> 状态：Implementation Plan / Not Implemented
>
> 目标：在 Node Attestation 已成功的前提下，证明本次请求 OpenViking SPIFFE ID 的调用者，是当前批准 OCI artifact 启动出的 Python serving process。
>
> 边界：本文只定义首次 pre-serve Workload Attestation。Mock、真实 Quote/QGS、真实进程级 SVID 和生产接受必须分别报告。

## 1. V1 成功标准

只有下面的闭环全部成立，V1 才算完成：

~~~text
Node Attestation 已成功
  -> OpenViking Python 直接连接 SPIRE Workload API
  -> SPIRE Agent 从 Unix peer credential 得到调用者 PID
  -> 独立 Evidence Provider 观测该进程和实际 OCI manifest
  -> Evidence Provider 用当前 challenge 和 claims 生成 fresh TDX Quote
  -> Trustee 查询 Node Attestation 已认可的 Node projection
  -> Trustee 验证 Quote、REPORTDATA 和 workload policy
  -> WorkloadAttestor 只在 ALLOW 后返回唯一强制 selector
  -> SPIRE 把目标 SVID 返回给同一个 Python process
  -> Python 取得精确目标 SVID 后才 bind/listen
~~~

V1 不包含：

- WAK、Node key 二次签名或 workload signing key；
- Trustee Receipt、签名 verdict 文件或文件挂载；
- TruCon、workload RTMR event、event log 或 RTMR head 推进；
- 周期重新证明、proof deadline、forced stream reconnect；
- quiesce/drain、即时撤销或 suspend/resume 时间证明；
- Agent deauthorization、Node reference revoke 到 Trustee record 的实时同步；
- 把普通配置、API key、业务数据、记忆或日志纳入 workload identity。

## 2. 工作包

V1 只包含六个工作包：

| 编号 | 工作包 | 完成结果 |
|---|---|---|
| W0 | 协议 | closed schema、单层 REPORTDATA binding、共享 fixture |
| W1 | Node 前置与 Evidence Provider | 可查询的 Node projection、独立本地 Evidence 服务 |
| W2 | Workload Evidence | peer PID、稳定进程实例、OCI manifest、TDX Quote |
| W3 | Trustee verifier | challenge、Node projection 查询、Quote 与 policy 裁决 |
| W4 | SPIRE WorkloadAttestor | PID 驱动证明、同步 verdict、唯一强制 selector |
| W5 | OpenViking pre-serve | Python 直接取得精确 SVID 后启动 TLS listener |

真实 TDX/QGS 验收不是新的工作包，按第 10 节作为独立证据层执行。

## 3. 当前仓库边界

### 3.1 可复用部分

| 路径 | 当前能力 |
|---|---|
| cczoo/agent-cc/core/spire/plugins/argus-tdx-nodeattestor/ | 已有 Argus TDX NodeAttestor 软件协议和 Trustee 调用基础 |
| cczoo/agent-cc/core/argus/src/bin/evidence_provider.rs | 已有独立 Rust Evidence Provider 进程基础 |
| cczoo/agent-cc/adapters/OpenViking/spiffe_server/server.py | 当前 Python server 与 TLS 初始化点 |
| cczoo/agent-cc/adapters/OpenViking/scripts/entrypoint-spiffe.sh | 当前 materializer 加 Python 的双进程入口 |
| cczoo/agent-cc/adapters/OpenViking/configs/Dockerfile.openviking | 当前 OpenViking runtime image |

### 3.2 尚未闭合的 Node context

当前 NodeAttestor schema 和 Trustee claims 已有 instance_id，Trustee response 还有可选 launch_id；Server binding store 也会按 Agent key 保存相关字段。

这些现有字段尚未同时满足下面三个条件：

1. 明确定义为一次 guest boot 的作用域；
2. 只在 Node Attestation 成功后形成 Trustee 可查询的认可投影；
3. Node Evidence 与 Workload Evidence 能从同一受保护本地来源读取。

因此本文不把现有 instance_id 或 launch_id 直接改名为已实现的 node_context_id。node_context_id 的来源和共享读取路径是 W1 阻塞项，关闭前不能宣称 Node 到 Workload 的关联已实现。

### 3.3 当前 OpenViking 状态目录

当前启动脚本会把包含 ov.conf 的可写状态目录挂载到 /app/.openviking。V1 明确允许这一目录承载普通配置、API key、业务数据和日志；这些内容不进入 workload claims。

V1 只禁止外部挂载或 runtime override 替换：

- OpenViking Python 代码和模块；
- 服务入口；
- Python executable；
- 执行服务所需的代码依赖。

这意味着 V1 证明的是批准的代码 artifact 和当前 serving process，不证明业务配置内容也经过批准。

### 3.4 当前 Workload TEE 证据边界

当前仓库尚未实现自定义 `argus_tdx_workload` WorkloadAttestor。现有 OpenViking 路径由 materializer 调用 Workload API，并依赖 Docker selectors；它只间接继承“SPIRE Agent 已通过 TDX Node Attestation”的前提，没有生成 workload 级 fresh TDX Quote，也没有把 Python 进程或 OCI claims 绑定到 TEE Evidence。本文后续章节描述的是目标实现，不能作为当前已实现证据。

目标 V1 中真正使用的 TEE 信息只有 fresh TDX Quote 及其 REPORTDATA。Trustee 执行取得可信 REPORTDATA 所必需的标准 Quote 与 collateral 验证，再用 session 和 nonce 检查新鲜度；它不在 Workload policy 中再次裁决 MRTD、RTMR、TD attributes、debug 或 Node TCB reference。

## 4. W0：协议

### 4.1 版本与编码

~~~text
protocol_version = argus.tdx.workload.v1
~~~

所有进入 REPORTDATA hash 的 JSON 使用 RFC 8785 JCS 的 UTF-8 bytes。外部请求和响应使用 closed schema；未知字段、错误类型和非规范 digest 直接失败。

固定格式：

- PID 和 process start ticks：无前导零十进制 string；
- OCI digest：sha256 加小写 hex；
- node_context_id：32-byte opaque value，RFC 4648 base64url、无 padding；
- session_id 和 nonce：分别由 CSPRNG 生成 32 bytes，使用 RFC 4648 base64url、无 padding；
- Quote：RFC 4648 base64url、无 padding。

公式中的 `\0` 表示单个 `0x00` byte，不是反斜杠和字符 0。

### 4.2 Workload claims

claims 只包含：

~~~json
{
  "node_context_id": "<boot-scoped-context>",
  "agent_view_pid": "4321",
  "process_start_ticks": "778899",
  "oci_manifest_digest": "sha256:<hex>"
}
~~~

不加入 process role、container ID、namespace、cgroup、executable digest、launch digest、配置 digest、policy 或目标 SPIFFE ID。

### 4.3 单层 REPORTDATA binding

双方内部重建的 hash 输入固定为：

~~~json
{
  "session_id": "<base64url>",
  "nonce": "<base64url>",
  "workload_claims": {
    "node_context_id": "<boot-scoped-context>",
    "agent_view_pid": "4321",
    "process_start_ticks": "778899",
    "oci_manifest_digest": "sha256:<hex>"
  }
}
~~~

唯一的协议绑定摘要为：

~~~text
reportdata_binding = SHA384(
    "argus-tdx-workload-reportdata-v1\0"
    || JCS(binding_input)
)

REPORTDATA[0:48]  = reportdata_binding
REPORTDATA[48:64] = 0x00 * 16
~~~

`binding_input` 不是额外的 wire object。V1 不定义 claims_digest、challenge_digest、evidence_digest、transcript_digest 或应用层 Evidence 签名。

### 4.4 Challenge

请求：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "target_spiffe_id": "spiffe://argus.local/service/openviking-cmem",
  "policy_id": "openviking-workload-v1"
}
~~~

响应：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "session_id": "<base64url>",
  "nonce": "<base64url>",
  "expires_at": "<UTC-RFC3339>",
  "policy_digest": "sha256:<hex>"
}
~~~

Trustee session 在服务端保存 target_spiffe_id、policy_id、policy snapshot/digest、nonce、expiry 和 consumed 状态。Challenge 阶段不选择 Node record。session 只允许消费一次；challenge TTL 是 Trustee 的单一短时部署参数，其具体值按真实 Quote 延迟确定，不扩展为 proof window 或周期重证配置。

### 4.5 Evidence 与 Verify

Trustee 使用两个 JSON API：

~~~text
POST /v1/attest/tdx-workload/challenge
POST /v1/verify/tdx-workload
Content-Type: application/json
~~~

Evidence Provider 通过本地 Unix socket 暴露 `GenerateWorkloadEvidence` 操作，不再增加公开 HTTP workload endpoint。

Evidence Provider 返回：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "workload_claims": {
    "node_context_id": "<boot-scoped-context>",
    "agent_view_pid": "4321",
    "process_start_ticks": "778899",
    "oci_manifest_digest": "sha256:<hex>"
  },
  "quote_format": "tdx-quote-v4",
  "quote": "<base64url>"
}
~~~

WorkloadAttestor 提交：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "session_id": "<base64url>",
  "workload_claims": {
    "node_context_id": "<boot-scoped-context>",
    "agent_view_pid": "4321",
    "process_start_ticks": "778899",
    "oci_manifest_digest": "sha256:<hex>"
  },
  "quote_format": "tdx-quote-v4",
  "quote": "<base64url>"
}
~~~

Trustee 从原始 session 和 claims 重建 `binding_input`。客户端不提交派生 digest。

ALLOW response：

~~~json
{
  "protocol_version": "argus.tdx.workload.v1",
  "decision": "allow",
  "session_id": "<base64url>"
}
~~~

DENY response 只把 decision 改为 deny。具体失败阶段进入 Trustee 审计，不冻结一组对客户端公开的稳定错误码。网络、TLS 或 schema 错误直接使调用失败。

### 4.6 Fixtures

只为真正计算或重算 REPORTDATA 的实现提供同一组 fixture：

- Rust Evidence Provider：producer；
- Trustee verifier：verifier；
- Go WorkloadAttestor：只有实现确实重算 binding 时才参与。

OpenViking Python 不生成或验证 workload Evidence，不参与 JCS 或 digest fixture。

每个 fixture 只需包含：

- binding input；
- JCS bytes；
- SHA-384 结果；
- REPORTDATA 64-byte 结果；
- 一个字段被修改后的失败用例。

另提供一个最小 policy fixture，固定排序后的 policy JSON、JCS bytes 和唯一 SHA-256 policy digest，供 Trustee 与 Entry 发布工具共用。

W0 完成门：producer 和 verifier 对同一 REPORTDATA fixture 结果一致，Trustee 与发布工具对 policy fixture 结果一致，closed schema 和单层 binding 测试通过。

## 5. W1：Node 前置与独立 Evidence Provider

### 5.1 Node Attestation 是强前置

Trustee 只在 Node Attestation 成功后保存最小认可投影：

~~~text
NodeAdmissionProjection {
  node_context_id
}
~~~

这是 Workload 协议使用的最小投影。Trustee 可以在内部 Node record 中保留 Agent ID、验证时间和测量详情用于审计，但这些字段不进入 Workload claims 或 Workload policy。

Workload Verify 根据 claims 中的 node_context_id 查询该投影。workload、WorkloadAttestor 和 challenge request 都不能提交或选择 Trustee 内部 Node record ID。

V1 的 Workload verifier：

- 不重新执行 Node Attestation；
- 不重新加载 MRTD、RTMR 或 TDVF/CCEL reference profile；
- 不 replay event log；
- 不 extend RTMR；
- 不修改 Node projection。

MRTD、RTMR 和 reference profile 是否符合 Node policy，由已经完成的 Node Attestation 负责。Workload Quote 在本协议中用于验证 fresh REPORTDATA 和当前 TDX Quote 的真实性，不重新裁决 Node 启动测量。

V1 也不实现 Agent deauthorization、Node policy/reference revoke 到 Trustee projection 的实时同步。SPIRE Server 的 Agent/Entry authorization 仍是目标 SVID 的交付门；新的 guest boot 必须先产生新的 node_context_id 并重新完成 Node Attestation。

### 5.2 node_context_id 阻塞项

W1 的第一个实现决策必须定义：

1. 谁在一次 guest boot 内产生符合 W0 wire encoding 的 node_context_id；
2. Node Attestation 成功时如何把它写入 Trustee projection；
3. Node Evidence 和 Workload Evidence 如何从同一受保护本地来源读取；
4. 新 boot 如何取得不同值。

它是 boot-scoped 本地关联值，不宣称是 TDX Quote 原生字段或全局硬件实例 ID。现有 instance_id/launch_id 只有在满足以上契约后才能复用。

### 5.3 Evidence Provider

Evidence Provider 保持 TDVM 内独立进程，同时服务：

~~~text
GenerateNodeEvidence
GenerateWorkloadEvidence
~~~

Workload 接口只通过受保护的本地 Unix socket 暴露给 SPIRE Agent。socket 的 owner/mode 必须让实际 SPIRE Agent 进程可以连接、OpenViking workload UID 不能连接；V1 不再叠加客户端密钥或 WAK。具体 UID 和 mode 在确认实际服务用户后写入部署配置。

WorkloadAttestor 传入 SPIRE SDK 提供的 peer PID、session_id 和 nonce；workload 不能直接调用接口或自报 PID。部署必须让 Evidence Provider 能权威解析 Agent 视角的 PID：二者共享同一 PID 视图，或由选定 runtime 接口完成明确映射，不能假定不同 PID namespace 中的相同数字表示同一进程。

生产信任前提是 Evidence Provider、SPIRE Agent、WorkloadAttestor 和用于 PID-to-OCI 解析的 runtime 路径属于 Node Attestation 已批准的 guest TCB。TDVM 启动后临时安装这些组件只能用于开发，不能形成生产证明。

W1 完成门：

- Node Attestation 未成功时，Trustee 查不到认可 projection；
- Node 和 Workload 两条 Evidence 路径读取相同 node_context_id；
- 新 boot 使用不同 node_context_id；
- 独立 Evidence Provider 的本地 socket 权限生效；
- OpenViking workload 无法直接访问 Evidence Provider socket 或 Quote device。

## 6. W2：Workload Evidence

### 6.1 输入

WorkloadAttestor 调用 Evidence Provider：

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

agent_view_pid 必须来自 SPIRE Workload API Unix peer credential。

### 6.2 当前进程观测

Evidence Provider 按以下顺序处理：

1. 为 peer PID 建立 pidfd 或等价稳定进程引用；
2. 读取 process start ticks；
3. 通过 container runtime 状态找到该 PID 所属 workload；
4. 要求 peer PID 等于 runtime 记录的 init/main PID；
5. 取得该 workload 实际使用的 OCI manifest digest；
6. 从 manifest 引用的 image config 读取默认 Entrypoint/Cmd；
7. 检查 container create/runtime metadata 没有覆盖默认 command；
8. 独立读取 live /proc/PID/exe 和 /proc/PID/cmdline；
9. 确认当前 live process 是预期的 OpenViking Python serving process；
10. 再次确认稳定进程引用和 start ticks 未变化；
11. 生成 claims、REPORTDATA 和 Quote。

第 7 步和第 8～9 步证明不同事实：

- runtime metadata 检查证明启动请求没有覆盖 image 默认 Entrypoint/Cmd；
- live /proc 检查证明身份请求时实际运行的是预期 Python serving process，而不是同镜像中的 shell、helper 或其他 Python 进程。

当前单进程入口应最终 exec：

~~~text
python3 -m spiffe_server.server
~~~

不把 process_role 写入 claims。是否为 init/main process 和是否为预期 serving process，是 argus.tdx.workload.v1 Evidence Provider 的固定成功条件；失败时不生成 Quote。

### 6.3 OCI manifest 是唯一 artifact authority

`oci_manifest_digest` 固定指 container runtime 为当前平台解析并用于创建该 workload 的 platform-specific OCI image manifest descriptor digest。它不是多平台 image index digest、image config digest 或本地 image ID。

Evidence Provider 必须通过实现前选定的 runtime authority，从当前 PID 对应 workload 取得该 manifest digest，并检查批准代码没有被运行时替换。

必须保护的范围：

- OpenViking Python 源码和 import module；
- image Entrypoint/Cmd 对应入口；
- Python executable；
- serving path 所需的代码依赖。

实现前必须把这些保护对象收敛成目标镜像中的有限路径集合。V1 采用部署不变量而不扫描整个 rootfs 或 upper-layer diff：这些路径对 workload 只读，不允许 writable mount 与其重叠；明确允许的可写目录不能参与 Python module 或 executable 解析。runtime 也不能用 `PYTHONPATH`、`PYTHONHOME`、`LD_PRELOAD` 等 override 改变受保护代码的解析。无法满足这些不变量时 W2 失败，不增加第二个 artifact digest。

允许位于可写或外部挂载目录、且不进入 identity 的范围：

- /app/.openviking 下的普通配置；
- API key 和模型参数；
- 业务数据与记忆；
- cache、临时文件和日志。

检查目标是这些可写目录不能通过 PYTHONPATH、模块遮蔽、入口替换或 executable 替换改变受批准代码。V1 不因存在普通数据卷本身而拒绝。

不再定义 image_config_digest、rootfs_digest、executable_digest、launch_digest 或 security_config_digest。

### 6.4 内部解析字段

Evidence Provider 可以内部使用 container ID、cgroup、namespace、mount info、uid/gid 和 runtime task metadata完成解析与排障。这些字段不进入 claims、Trustee policy 或 SPIRE selector。

### 6.5 失败行为

V1 只区分三类本地失败行为：

| 阶段 | 行为 |
|---|---|
| process observation | 不生成 Quote，WorkloadAttestor 无 selector |
| artifact resolution | 不生成 Quote，WorkloadAttestor 无 selector |
| Quote generation | 调用失败，WorkloadAttestor 无 selector |

详细原因只进入受控日志，不冻结 Provider 公共错误码。

W2 完成门：

- peer PID、runtime init/main PID 和 live Python serving process 是同一 process instance；
- PID reuse 或 process replacement 被拒绝；
- 实际 OCI manifest 可解析；
- 普通 /app/.openviking 挂载不会因其可写性被误拒绝；
- 代码、模块、入口或 executable 能被外部替换时拒绝；
- Quote.REPORTDATA 与 W0 fixture 规则一致。

## 7. W3：Trustee verifier

### 7.1 最小 workload policy

~~~yaml
policy_id: openviking-workload-v1
target_spiffe_id: spiffe://argus.local/service/openviking-cmem
allowed_oci_manifest_digests:
  - sha256:<approved-oci-manifest>
~~~

policy 不包含可关闭的 TEE、nonce、session、REPORTDATA、Node admission 或 current-process 布尔开关。这些是 verifier 固定规则。

YAML 只用于展示。计算 digest 时先把 allowlist 去重并按 digest 字符串升序排列，再构造只含以上三个字段的 closed JSON object：

~~~text
policy_digest = "sha256:" + lowercase_hex(SHA256(
    "argus-tdx-workload-policy-v1\0"
    || JCS(policy_object)
))
~~~

Trustee、policy 发布工具和 Registration Entry 使用同一个 fixture；不再定义第二种 policy digest。Trustee 在创建 challenge 时固定 policy snapshot/digest，WorkloadAttestor 只在本次 session ALLOW 后把该 digest 映射成 selector。

### 7.2 固定验证顺序

Workload Verify 实际使用的 TEE 信息是 fresh TDX Quote 及其 REPORTDATA。标准 Quote 与 collateral 验证只为取得可信 REPORTDATA；MRTD、RTMR、TD attributes、debug 和 Node TCB reference 不作为本节 Workload policy 的再次裁决输入。node_context_id、PID、start ticks 和 OCI manifest 是受信 Evidence Provider 采集并由 REPORTDATA 绑定的软件 claims，不是 TDX 原生 workload 字段。

实际 Trustee verifier adapter 必须在 W3 完成前冻结 Quote verification result 和 collateral expiration status 到“可信 REPORTDATA / DENY”的映射。该映射只关闭 Quote 真实性边界，不得扩展成第二套 MRTD、RTMR、debug 或 Node TCB workload policy。

Trustee 按以下顺序快速失败：

1. 检查 request closed schema 和 protocol version；
2. 加载 session，检查 nonce 未过期、未消费；
3. 执行标准 raw TDX Quote 与 collateral 验证，取得可信 REPORTDATA；
4. 用 session_id、nonce 和原始 workload_claims 重算唯一 REPORTDATA binding；
5. 比较 Quote.REPORTDATA；
6. 根据 node_context_id 查询 Node Attestation 已认可的 projection；
7. 从 session 固定的 policy 验证 target SPIFFE ID 和 OCI manifest allowlist；
8. 原子消费 session，返回同步 verdict。

第 6 步只查询 Node Attestation 结果，不重复比较 MRTD/RTMR 或加载 reference profile。Trustee 也不假装远程重读 /proc；进程事实来自 Node 已信任的 Evidence Provider，并由 fresh REPORTDATA 绑定。

### 7.3 通道和 verdict

WorkloadAttestor 必须验证 Trustee TLS server identity。客户端认证可以用于 API 访问控制，但不形成新的 workload 密钥或证明层。

verdict 只供当前 Verify 调用同步消费，不写入文件、不返回给 OpenViking、不作为 bearer token。WorkloadAttestor 只接受当前 session_id 的 ALLOW。

失败行为压缩为：

| 阶段 | 结果 |
|---|---|
| session 或 replay | DENY，无 selector |
| Quote 或 REPORTDATA | DENY，无 selector |
| Node projection | DENY，无 selector |
| workload policy | DENY，无 selector |
| TLS、网络或 schema | 调用失败，无 selector |

W3 完成门：以上五组失败都不能产生 ALLOW，成功 session 只能消费一次。

## 8. W4：SPIRE WorkloadAttestor

### 8.1 最小实现

新增：

~~~text
cczoo/agent-cc/core/spire/plugins/argus-tdx-workloadattestor/
~~~

同时必须把插件接入实际 OpenViking Agent 的构建、配置和部署路径，包括当前 runtime profile 下的 `scripts/prepare.sh`、Agent config template、compose/deploy 脚本；只新增源码目录不算 W4 完成。

插件只承担：

1. 接收 SPIRE SDK 提供的 peer PID；
2. 使用固定 target SPIFFE ID 和 policy ID 请求 Trustee challenge；
3. 调用本地 Evidence Provider；
4. 提交 Trustee Verify；
5. 验证 TLS、closed verdict 和当前 session_id；
6. 只在 ALLOW 后返回一个 policy selector。

任一错误直接返回 attestation error，不使用 local/mock allow fallback。

### 8.2 唯一 selector

~~~text
argus_tdx_workload:policy_digest:sha256:<hex>
~~~

Registration Entry 不重复加入 verified、service、process role、manifest digest 或 Docker label。PID、start ticks、node context、nonce 和 Quote 也不进入 selector。

OpenViking Entry 示例：

~~~bash
spire-server entry create \
  -parentID "spiffe://argus.local/spire/agent/argus_tdx/<agent-key-id>" \
  -spiffeID "spiffe://argus.local/service/openviking-cmem" \
  -selector "argus_tdx_workload:policy_digest:sha256:<approved-policy>"
~~~

SPIRE 会聚合多个 WorkloadAttestor 的成功 selectors；单个插件失败不一定中止其他插件。因此 OpenViking Entry 必须包含这个自定义 selector，Docker 或 Unix selectors 不能代替它。

当前 runtime 中同一 OpenViking SPIFFE ID 已有 Docker-only Entry。实施时必须替换该弱 Entry，而不是并存新增；同一 Agent 授权范围内，任何能交付 `spiffe://argus.local/service/openviking-cmem` 的 Entry 都必须要求上述自定义 selector。否则 Python 只按 SPIFFE ID 选择时无法区分身份来自哪条 Entry。

该 selector 约束的是 SVID 向 workload 的匹配与交付，不要求关闭 SPIRE 的正常 Entry SVID prefetch。

### 8.3 配置

插件配置只需要：

~~~hcl
WorkloadAttestor "argus_tdx_workload" {
  plugin_data {
    evidence_socket  = "/run/argus/evidence.sock"
    trustee_url      = "https://trustee.example"
    trustee_ca_path  = "/etc/argus/trustee-ca.pem"
    target_spiffe_id = "spiffe://argus.local/service/openviking-cmem"
    policy_id        = "openviking-workload-v1"
  }
}
~~~

不配置 WAK、Receipt 路径、Node admission ID、RTMR index 或本地 allowlist。

W4 完成门：

- 插件使用的 PID 等于 SPIRE peer PID；
- Evidence 或 Trustee 失败时没有 policy selector；
- 只有当前 session 的 ALLOW 能生成唯一 selector；
- 其他 attestor 成功不能绕过 OpenViking Entry；
- 不存在缺少该 selector、但能向同一 Agent 交付目标 OpenViking SPIFFE ID 的旧 Entry；
- 新插件已进入实际 Agent build/config/deploy 路径。

## 9. W5：OpenViking pre-serve

### 9.1 单进程入口

修改：

~~~text
cczoo/agent-cc/adapters/OpenViking/scripts/entrypoint-spiffe.sh
cczoo/agent-cc/adapters/OpenViking/configs/Dockerfile.openviking
cczoo/agent-cc/adapters/OpenViking/spiffe_server/server.py
~~~

生产入口删除 argus-svid-materializer、凭据文件等待和双进程监管，最终执行：

~~~bash
exec python3 -m spiffe_server.server "$@"
~~~

OpenViking Python 自己连接 SPIRE Workload API。只有这个连接的 Unix peer PID 才是被证明和签发的主体。

attested production 入口不保留 `ARGUS_SPIFFE_ENABLED=0` 的未认证 fallback；非 attested 开发模式使用单独 profile 或入口。

### 9.2 首次 pre-serve

server.py 的固定顺序：

~~~text
load application config
  -> open Workload API stream
  -> wait for exact target X.509-SVID
  -> build server TLS context
  -> start uvicorn listener
~~~

production 目标 SPIFFE ID 固定为 `spiffe://argus.local/service/openviking-cmem`。如果仍通过 `ARGUS_WORKLOAD_SPIFFE_ID` 传入，启动时必须先要求它精确等于这个固定值，不能由任意运行时配置选择另一身份。

Python 只检查：

- 返回身份中存在上述精确目标 SPIFFE ID；
- SVID 尚未过期；
- trust bundle 可用于配置的 mTLS peer verification。

Workload API 请求本身不能指定目标 SPIFFE ID，并可能返回多个匹配身份。因此收到其他 SVID 不能解除 pre-serve gate。

第一版保持单 worker、no reload，不在取得 SVID 后 fork/exec，也不使用预先 bind 的业务 socket。

### 9.3 正常 SVID 轮换

首次 pre-serve 成功后，Python 把每次 Workload API stream response 当作完整身份集合，重新精确选择目标 SPIFFE ID 并检查有效期；其他身份不能替代目标身份。目标身份从新集合中消失或已过期时，不得继续安装或使用旧目标 context，也不能回退到其他 SVID。该规则属于正常 SVID 生命周期，不触发新的 remote Workload Attestation。

`FetchX509SVID` stream 建立时执行一次 workload attestation；同一 stream 后续收到的正常缓存/SVID 更新不重新调用 WorkloadAttestor。普通轮换因此不等于新的 remote Workload Attestation。V1 不主动关闭 stream、不设置 proof deadline，也不因轮换实现强制重新证明。

如果 Python TLS 库只能通过临时文件加载私钥，可由同一 Python process 在仅自身可访问的 /run 路径内短暂 materialize。该实现选择必须在编码前验证，但不会恢复独立 materializer 进程。

W5 完成门：

- Workload API caller、runtime init/main process 和监听进程是同一 process instance；
- 精确目标 SVID 到达前业务端口不可连接；
- 缺失或错误 SPIFFE ID 时 Python 不监听；
- OpenViking container 不挂载 Trustee verdict 文件；
- 跨至少一次普通 SVID 更新，远程 challenge/verify 调用次数仍保持为首次的一次；
- 每次更新仍精确选择目标 SPIFFE ID；目标缺失或过期时不继续使用旧身份；
- 普通 SVID 更新不触发自定义重证状态机。

## 10. 验证与证据分层

### 10.1 最小测试组

| 组 | 必须证明 |
|---|---|
| Protocol | Rust producer 与 Trustee verifier 的单层 REPORTDATA fixture 一致；字段修改后失败 |
| Node precondition | 没有认可的 node_context_id 时 DENY；新 boot 使用不同 context |
| Process and artifact | caller PID、start ticks、runtime init/main、live Python process、OCI manifest 解析正确 |
| Trustee and selector | nonce/Quote/REPORTDATA/policy 任一失败都没有自定义 selector |
| Pre-serve | Python 只在取得精确目标 SVID 后监听；同 stream 正常轮换不重复远程证明 |

代表性负向用例只保留：

1. Node Attestation 未完成或 node_context_id 不存在；
2. nonce 重放或 REPORTDATA 被修改；
3. peer PID 被替换，或 caller 是 helper；
4. live process 不是预期 Python serving process；
5. OCI manifest 不在 allowlist，或代码路径可被挂载替换；
6. Trustee 不可达、TLS identity 错误或 malformed verdict；
7. 自定义 attestor 失败但 Docker attestor 成功；
8. 仍存在同 SPIFFE ID 的 Docker-only 弱 Entry；
9. SPIRE 返回的身份中没有精确目标 SPIFFE ID。

### 10.2 证据层

| 层级 | 能证明 | 不能证明 |
|---|---|---|
| Mock | schema、session、binding、失败关闭、selector 流程 | 真实 TDX Quote、QGS、collateral |
| 真实 Quote/QGS | TDX Quote 生成、REPORTDATA、collateral verification | 当前 Python 最终获得真实 SVID |
| 真实进程级 SVID E2E | peer PID 到 OCI manifest、Trustee ALLOW、selector、同一 Python 取得 SVID | 生产 baseline 和运维已接受 |
| 生产接受 | 已批准 Node baseline、真实 Trustee/reference/collateral、实际部署和审计均通过 | 不自动提供持续重证或即时撤销 |

任何报告都必须标记属于哪一层，不能用 Mock 或真实 Quote 组件测试代替生产接受。

### 10.3 最小观测

只要求两个指标：

~~~text
argus_workload_attestation_attempts_total{stage,result}
argus_workload_attestation_duration_seconds{stage}
~~~

stage 使用固定的小集合：challenge、evidence、verify、svid。详细失败原因记录在受控日志，不放入高基数标签。

审计至少能关联 session、node_context_id、PID/start ticks、OCI manifest、policy digest、Trustee decision 和 Quote digest。Quote digest 只用于审计，不进入协议绑定。

## 11. 实施顺序

### 阶段 A：W0

1. 固定 claims、challenge、Evidence、Verify 和 verdict schema；
2. 实现单层 REPORTDATA binding；
3. 建立 Rust Evidence Provider 与 Trustee 共享 fixture。

完成门：无需 TDX 硬件即可通过协议测试。

### 阶段 B：W1

1. 关闭 node_context_id 来源和共享读取路径；
2. Node Attestation 成功时建立 Trustee projection；
3. Evidence Provider 增加受保护 Workload Unix socket。

完成门：Node 未入场时无 projection；Node/Workload 两条路径读取同一 boot context。

### 阶段 C：W2 和 W3

1. 从 peer PID 观测稳定进程；
2. 区分 runtime command 与 live /proc process 检查；
3. 解析 OCI manifest，并只保护代码/入口/可执行依赖；
4. 生成真实或 Mock Quote；
5. Trustee 实现固定验证顺序和最小 policy。

完成门：Mock 流程通过；真实 Quote 验收仍单独报告。

### 阶段 D：W4

1. 实现 SPIRE WorkloadAttestor；
2. 接入实际 Agent build/config/deploy；
3. 只映射一个 policy digest selector；
4. 用要求该 selector 的 Entry 替换同 SPIFFE ID 的旧 Docker-only Entry。

完成门：任一失败路径都没有目标 selector/SVID。

### 阶段 E：W5

1. 删除 production materializer；
2. Python 直接使用 Workload API；
3. 增加首次 exact-SVID pre-serve gate；
4. 从 Workload API context 构造 TLS context。

完成门：实际 caller、被观测进程、SVID consumer 和 listener 为同一 Python process。

完成 W0～W5 后，再按第 10.2 节分别执行真实 Quote/QGS、真实进程级 SVID E2E 和生产接受，不把这些证据层合并成一个结论。

## 12. 尚未关闭的事项

### 12.1 Node-to-Workload 关联设计阻塞

`node_context_id` 的权威来源、boot 生命周期和 Node/Workload 共享读取路径尚未定义。它阻塞 W1 和真实端到端闭环，但不妨碍先实现 W0 的 closed schema 与 fixtures。

### 12.2 各工作包的实现选择

- W2：Evidence Provider 从 Agent 视角 peer PID 查询实际 container runtime/platform manifest 的接口及 PID namespace 映射；目标镜像中需要保护的有限代码、module、entrypoint、executable 路径；
- W3：实际 Quote verifier backend 的 verification result/collateral expiration 到可信 REPORTDATA 或 DENY 的固定映射；
- W5：Python 使用的 SPIFFE Workload API client及其 SPIRE 版本兼容性；TLS context 是否需要同进程临时私钥文件。

这些选择只阻塞对应工作包，不应被扩展成新的身份字段或通用 rootfs 扫描。

### 12.3 真实验收前置条件

- 可用的真实 TDX Quote/QGS 和 Trustee verification backend 环境；
- Evidence Provider、SPIRE Agent、WorkloadAttestor 和 runtime 解析路径已进入批准的 Node baseline。

这些条件阻塞真实证据和生产声明，不阻塞 Mock 软件链路编码。文档在它们关闭前仍是实施计划；Mock 成功不能宣称真实 TDX 或生产 Workload Attestation 已完成。

## 13. V1 最终声明

V1 完成后可以声明：

> 在已经通过 Node Attestation 的当前 guest boot 中，SPIRE Agent 从 Workload API peer credential 确定身份请求者 PID。受 Node 信任的独立 Evidence Provider 确认该稳定进程实例是 OCI runtime 的 init/main process、当前执行预期 OpenViking Python serving process，并解析其实际 OCI manifest。Evidence Provider 使用 Trustee 的当前 nonce，把 node context、PID/start ticks 和 manifest 通过单层 REPORTDATA 绑定到 fresh TDX Quote。Trustee 查询已有 Node projection、验证 Quote 和批准 manifest 后，SPIRE 才把目标 SVID 返回给同一 Python process；Python 随后开始 TLS 监听。

该声明只覆盖首次身份请求时的当前实例，不声称提供持续运行时完整性、周期重新证明或即时撤销。
