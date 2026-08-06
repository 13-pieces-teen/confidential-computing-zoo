# Argus-SPIFFE v2：真实 RA 接入前的安全加固执行计划

## 1. 文档定位

本文定义 Argus-SPIFFE v2 在暂不接入真实 Quote/QGS、正式 Trustee 和真实远程证明服务时，仍可完成的安全、生命周期、运行和验收改造。

当前远程验收已经证明：

- 两个独立 SPIRE Agent、Workload API、Agent ID 和 Docker 身份域正常；
- OpenClaw 使用 `x509pop`，OpenViking 使用自定义 `argus_tdx`；
- v2 正式配置不依赖 `join_token`；
- mock Evidence Provider 与 mock Trustee 已拆为不同进程；
- OpenClaw 和 OpenViking workload 能取得各自 X.509-SVID；
- 真实 OpenClaw 插件请求能通过 SPIFFE mTLS 到达真实 OpenViking；
- 明文、无客户端 SVID、错误服务端 SPIFFE ID、错误来源和跨角色 Workload API 请求均被拒绝；
- replay、Evidence Provider 503、Trustee 503 和 Trustee timeout 按预期 fail-closed。

当前结果应描述为：

> Argus-SPIFFE v2 real OpenClaw plugin mock-stage E2E PASS。

它不等于真实 TDX 远程证明或生产安全闭环完成。当前状态和远程证据分别见：

- [Argus-SPIFFE-v2-Remote-TDX-Verification-Report.md](Argus-SPIFFE-v2-Remote-TDX-Verification-Report.md)
- [Argus-SPIFFE-v2-Implementation.md](Argus-SPIFFE-v2-Implementation.md)
- [Argus-SPIFFE-v2-Execution-Status.md](Argus-SPIFFE-v2-Execution-Status.md)

### 1.1 当前实施快照

截至 2026-08-06：

- WP1 Guard 同请求门控已完成远程验收；
- WP2 初版 Docker endpoint 白名单和 egress 数据面收紧曾完成远程验收；后续代码审计发现初版 proxy 未按 sandbox 所有权约束已有 container/exec 目标；
- WP2 修复版已加入 run-scoped owner label 注入、container/exec parent 回查和基础设施容器 fail-closed 验证，尚待远程重验；
- WP3 已完成连接生命周期和 OpenClaw 侧验证脚本的源码建设，但审查发现初版最大连接寿命和负向断言缺陷；
- WP3 审查修复已加入绝对连接到期、watcher 失败退出、实际 SVID 到期采集、正确的拒绝收敛断言和异常恢复 trap；
- WP3 修复版尚未远程重验，bundle/trust domain 和 OpenViking `can_reattest=false` 专项 runtime 仍未执行。

WP2 和 WP3 均只能标记为“源码修复完成、待远程重验”。WP4 至 WP8 仍按本文顺序继续实施。

## 2. 本阶段目标与非目标

### 2.1 目标

本阶段需要在不提高远程证明真实性的前提下，完成以下能力：

1. 将 Argus Guard 决策和同一次 OpenClaw 业务请求形成不可分离的因果门控。
2. 关闭 OpenClaw 到 OpenViking 数据面的已知旁路。
3. 验证 SVID 轮换、到期、撤销、Agent 重启和 Workload API 故障时的收敛行为。
4. 强化持久化克隆检测的并发、损坏恢复和文件安全。
5. 让 v2 runtime 支持多实例并行隔离和重复验收。
6. 建立从 OpenClaw 请求到 Guard、mTLS proxy 和 OpenViking 的统一审计链。
7. 完成 mock-stage canary、切换和回滚演练。

### 2.2 非目标

本阶段明确不交付：

- 真实 TD Quote；
- QGS/QE 连接和 collateral 获取；
- 正式 Trustee 对 Quote、TCB、measurement 或 RTMR 的独立判断；
- 真实 RA 的正向、tamper、TCB out-of-date 和 policy rejection 安全声明；
- Envoy 或完整 service mesh；
- OpenClaw 节点的 TDX Node Attestation；
- `can_reattest=true` 的正式启用；
- 多 SPIRE Server 副本的生产部署。

本阶段可以为上述能力冻结接口和测试契约，但不能使用 mock 结果替代真实安全验收。

## 3. 实施原则

1. **不增加隐式 fallback**
   Guard、Workload API、SPIRE mTLS 或配置异常时必须拒绝请求，不得自动退回明文、API key-only 或直连 OpenViking。

2. **Guard 保持显式 mock**
   当前可以继续使用 `GUARD_MODE=mock_allow`，但响应必须继续声明 `verification_mode=mock_allow` 且不生成伪造 verified claims。

3. **门控机制和判定真实性分开验收**
   本阶段证明 Guard 决策不可绕过以及请求绑定正确，不证明 Guard 已完成真实 TDX 判断。

4. **不改变双 Agent 边界**
   OpenClaw 和 OpenViking 不共享 Agent data directory、Workload API socket、Agent ID、Docker daemon 或 workload parent。

5. **区分 Node Attestation 与 caller-side 新鲜证据**
   当前 Guest-local Evidence Provider 只服务于 OpenViking Agent Node Attestation，`mock_allow` Guard 不调用该 endpoint。若未来启用每请求新鲜证明，caller-side Guard 应通过独立、受保护的 service evidence endpoint 获取 OpenViking nonce-bound evidence；Evidence Provider 仍位于 OpenViking 侧。

6. **真实业务 E2E 是最终判据**
   每个影响数据面的改造都必须使用真实 OpenClaw 插件请求和真实 OpenViking session/message/commit 证据验收。

### 3.1 固定角色边界

本计划沿用 Argus 最初设计中的角色划分：

| 角色 | 当前组件 | 位置 | 职责 |
| --- | --- | --- | --- |
| Policy Enforcement Point，PEP | OpenClaw mTLS egress | OpenClaw/caller 侧 | 截获业务请求、调用 Guard、执行 ALLOW/DENY、持有 OpenClaw SVID 并建立 mTLS |
| Policy Decision Point，PDP | Argus Guard | OpenClaw/caller 侧 | 验证目标信任状态、执行 caller-local policy、返回 ALLOW/DENY |
| Attester endpoint | Argus Evidence Provider | OpenViking/service 侧 | 为本地 TDVM、Agent 或服务生成 nonce-bound evidence |
| Verifier / Identity Issuer | Trustee 与 SPIRE Server | 中心身份平面 | 验证 evidence 或基于已验证 Node Attestation 签发身份 |

Guard 不放在 OpenViking 侧，也不作为 SPIRE Server 插件运行。OpenViking mTLS server 负责验证精确的 OpenClaw SPIFFE ID，但 caller-side Guard 保留是否发送敏感业务请求的最终决策权。

当前 v2 中的 Guest-local Provider 是 Node Attestation adapter，只监听 TDVM loopback。它不等同于未来供 Guard 远程访问的 service evidence endpoint，不能为了连接 Guard 而直接暴露到不受保护的网络。

### 3.2 Guard 运行模式

计划明确区分三种模式：

| 模式 | Guard 的验证输入 | 是否访问 Provider | 本阶段状态 |
| --- | --- | --- | --- |
| `mock_allow` | caller、target 和业务授权上下文；不产生 verified claims | 否 | 当前实施与验收模式 |
| `spiffe_identity` | egress 已验证的 peer SPIFFE ID、trust domain、SVID 有效期和本地策略 | 通常否 | 仅冻结接口，当前 Guard 尚未实现 |
| `fresh_evidence` | Guard 生成 nonce，获取目标服务 evidence，并通过 RA Adapter/Trustee 验证 | 是，访问 OpenViking 侧 service evidence endpoint | 延后到真实 RA 阶段 |

`spiffe_identity` 接受 SPIRE Server 在 SVID 签发或轮换时完成的证明结果，不提供每请求 nonce freshness。只有明确需要每请求新鲜证明时才使用 `fresh_evidence`。

在 `spiffe_identity` 模式中，egress 可以先完成不携带敏感 HTTP body 的 TLS handshake，取得并验证 peer SVID，再将该身份交给 Guard；只有 Guard 返回 ALLOW 后，才在已认证连接上发送被冻结的业务请求。

## 4. 工作包总览

| 工作包 | 优先级 | 能否在本阶段完整验收 | 主要结果 |
| --- | --- | --- | --- |
| WP1 Guard 同请求强制门控 | P0 | 是 | 未取得 ALLOW 的业务请求无法进入 mTLS 转发 |
| WP2 数据面旁路收紧 | P0 | 是 | OpenClaw 只有一条受控路径可访问 OpenViking |
| WP3 身份生命周期和拒绝收敛 | P0 | 是 | 轮换、到期、撤销和故障行为具有明确 SLA |
| WP4 持久化克隆检测加固 | P1 | 是 | 并发、崩溃和文件损坏场景 fail-closed |
| WP5 多 runtime 隔离 | P1 | 是 | 多实例可并行运行且身份状态不交叉 |
| WP6 结构化审计与指标 | P1 | 是 | 单个请求可跨组件关联和分类 |
| WP7 canary、切换与回滚演练 | P1 | 是 | mock-stage 部署过程可恢复、可审计 |
| WP8 真实 RA 接口预留 | P2 | 仅接口和契约 | 后续替换 mock 时不重写业务门控 |

## 5. WP1：Guard 同请求强制门控

### 5.1 当前问题

当前验证脚本分别检查 Guard ALLOW 和 SPIFFE mTLS 成功。两者不是同一个请求内的强因果关系：

```text
调用 Guard -> 得到 ALLOW
调用 mTLS proxy -> 转发另一个请求
```

这只能证明两个组件分别工作，不能证明每个到达 OpenViking 的业务请求都经过 Guard。

### 5.2 目标链路

```text
真实 OpenClaw 插件请求
  -> OpenClaw mTLS egress 接收并冻结请求
  -> 计算请求绑定摘要
  -> 调用 Argus Guard
  -> 仅在明确 ALLOW 时转发被冻结的同一请求
  -> SPIFFE mTLS
  -> OpenViking mTLS server
  -> 真实 OpenViking
```

本阶段先在现有 Go mTLS egress 中实现，不将 Envoy 作为前置依赖。

### 5.3 业务授权绑定合同

egress 必须为每次请求生成不可复用的 `request_id`，并基于规范化字段计算业务授权摘要：

```text
request_digest = SHA-256(
  method ||
  normalized_path_and_query ||
  body_sha256 ||
  caller_spiffe_id ||
  target_spiffe_id ||
  target_service ||
  target_uri ||
  operation ||
  data_class ||
  issued_at ||
  nonce
)
```

该摘要属于 egress 与 Guard 之间的 `authorization_context`，用于把 ALLOW/DENY 和同一次业务请求绑定。它不属于 Node Attestation 的 EvidenceRequest、canonical evidence request digest 或 TDX REPORTDATA 合同。

两类绑定必须分开：

| 绑定 | 字段 | 使用者 | 目的 |
| --- | --- | --- | --- |
| RA evidence binding | nonce、caller_id、target、requested_claims、profile_digest | Evidence Provider、RA Adapter、Trustee、SPIRE NodeAttestor | 证明 evidence 与验证方 challenge 和目标上下文一致 |
| Business authorization binding | method、path、body hash、caller/target SPIFFE ID、target service、target URI、operation、data class、request ID | mTLS egress、Argus Guard | 证明 Guard 决策对应同一业务请求和同一实际目标 |

本阶段不得为了同请求门控修改已经冻结的 NodeAttestor EvidenceRequest 或 REPORTDATA 公式。在未来 `fresh_evidence` 模式中，Guard 可在一次决策内同时使用 RA evidence binding 和 business authorization binding，但两套摘要仍保持独立。

约束如下：

- request body 必须设置最大容量，超限直接拒绝；
- egress 在调用 Guard 前完整读取并冻结 body；
- Guard 返回 ALLOW 后只能转发冻结的 method、path、query 和 body；
- 来自 OpenClaw 的同名内部审计 header 必须删除并由 egress 重建；
- Guard 请求必须使用独立的 `authorization_context` 承载业务字段，不能将其伪装成 EvidenceRequest；
- Guard 响应至少包含 `decision`、`decision_id`、`request_digest`、`verification_mode` 和有效期；
- 返回的 `request_digest` 必须和 egress 本地值恒等；
- ALLOW 响应过期、字段缺失或 JSON 非法时必须拒绝。

内部审计字段建议为：

```text
X-Argus-Request-ID
X-Argus-Decision-ID
X-Argus-Request-Digest
```

这些 header 在本阶段用于因果审计，不单独作为服务端身份凭据。OpenViking mTLS server 仍只接受精确的 OpenClaw workload SPIFFE ID。

### 5.4 代码改造范围

主要修改位置：

- `core/spire/v2/mtls-smoke/main.go`
- `core/spire/v2/compose.center.yaml`
- `core/spire/v2/start-openclaw-workload.sh`
- `core/spire/v2/verify-mtls.sh`
- `core/spire/v2/verify-openclaw-plugin-e2e.sh`
- Argus Guard 的 `/ra/v1/verify` 请求和响应结构

职责保持为：

- mTLS egress 是 PEP，负责截获、冻结和转发业务请求；
- Argus Guard 是 PDP，负责返回绑定当前业务上下文的决策；
- egress 不允许 OpenClaw 客户端直接指定 `verification_mode`；
- 本阶段 egress 只接受 Guard 明确返回的 `verification_mode=mock_allow`；
- Guard 和 egress 之间优先使用 Compose 内部网络或 Unix socket，不对公共网络开放。

egress 需要增加：

- Guard endpoint、timeout 和最大 body 配置；
- 请求摘要和 nonce 生成；
- Guard 响应严格解析；
- fail-closed 错误映射；
- request、decision 和 mTLS 转发关联日志；
- inbound 审计 header 覆盖；
- 对 Guard 调用禁用不必要的代理并设置固定超时。

Guard 需要增加：

- 可选、版本化的 `authorization_context`；
- `decision_id`、回显的 `request_digest` 和明确有效期；
- 对未知字段、缺失字段和超限 body digest 输入的严格处理；
- `mock_allow` 下继续保持 `claims=null`；
- 不把业务请求摘要传给 Node Attestation Provider；
- 不修改现有 EvidenceRequest 和 REPORTDATA 协议。

### 5.5 禁止的 fallback

以下行为均禁止：

- Guard 不可用时继续转发；
- Guard timeout 时使用上一次 ALLOW；
- Guard 响应缺失 `request_digest` 时只检查 `decision=ALLOW`；
- Guard 调用失败时回退到 API key-only；
- mTLS 失败时回退明文 OpenViking；
- 通过环境变量静默关闭 Guard；
- 验证脚本提前调用 Guard 后将结果视为后续业务请求授权。

### 5.6 验收矩阵

| 场景 | 预期结果 |
| --- | --- |
| Guard ALLOW，digest 一致 | 请求通过 mTLS 转发，OpenViking 写入成功 |
| Guard DENY | egress 拒绝，OpenViking 无对应 marker |
| Guard HTTP 503 | egress fail-closed，OpenViking 无写入 |
| Guard timeout | egress fail-closed，OpenViking 无写入 |
| Guard malformed JSON | egress fail-closed |
| Guard 缺少 decision/digest | egress fail-closed |
| Guard 返回错误 digest | egress fail-closed |
| ALLOW 后修改 method/path/body | 摘要校验失败，不转发 |
| 客户端伪造内部审计 header | egress 覆盖，不能污染审计链 |
| 绕过 egress 直连 OpenViking | 网络或 mTLS 身份拒绝 |

### 5.7 完成判据

- 真实 OpenClaw 插件 E2E 产生唯一 marker；
- 同一 `request_id` 同时存在 Guard ALLOW、`forwarded_mtls` 和 OpenViking 2xx 证据；
- 所有 Guard 故障场景均证明 OpenViking 没有对应业务写入；
- 独立的“先调用 Guard、再发另一个请求”不再满足验收；
- Guard 仍明确报告 `mock_allow`，文档不宣称真实 RA。

## 6. WP2：数据面旁路收紧

### 6.1 Docker socket 边界

SPIRE Agent 为 Docker Workload Attestation 挂载只读 Docker socket 属于当前身份方案的一部分。本工作包关注真实 `agentcc-openclaw-sbx-gateway` 对 Docker socket 的控制能力。

OpenClaw sandbox 当前使用 Docker backend，因此不能在没有替代方案时直接删除 socket。目标是将任意 Docker 控制从业务 Gateway 中拆出：

1. 首选将 sandbox 创建、启动、停止和查询移动到独立 sandbox controller。
2. Gateway 只访问受认证、受限的 controller API。
3. 若暂时继续使用 Docker socket proxy，只开放 sandbox 所需的最小 API。
4. 禁止 Gateway 创建 privileged 容器、任意 host mount、host network 或加入 Argus 身份网络。
5. SPIRE Agent 的只读 Docker socket 不向业务容器共享。
6. Proxy 必须覆盖写入 run-scoped sandbox owner label；不得信任 Gateway 自报 label。
7. 所有 container lifecycle、exec、attach、logs 和 archive 请求必须先回查目标 label；exec ID 必须回查其 parent container。
8. SPIRE、Guard、mTLS、OpenViking 和其他无匹配 owner label 的容器必须返回 403，Docker inspect 不可用时必须 fail-closed。

### 6.2 唯一受控访问路径

目标业务路径固定为：

```text
OpenClaw
  -> Guard-gated egress
  -> SPIFFE mTLS
  -> OpenViking mTLS server
  -> OpenViking loopback
```

网络约束：

- OpenClaw egress 不监听公共 Host 地址；
- OpenViking 原始 1933 端口只监听 TDVM loopback；
- 1943 只接受指定 OpenClaw SPIFFE ID；
- OpenClaw 不直接连接 TDVM 1933；
- 其他 Host 容器不能通过加入 bridge 冒充 OpenClaw；
- 若 OpenClaw 插件协议支持 Unix socket，优先以专用 Unix socket替代来源 IP；
- 若仍使用 bridge，必须同时完成 Docker 控制面隔离，不能只依赖固定 IP。

### 6.3 完成判据

- OpenClaw 正向插件 E2E 继续通过；
- Host、其他容器和错误 bridge 地址都不能调用 egress；
- OpenClaw 无法直接访问 OpenViking 1933；
- 只有 OpenClaw mTLS egress 挂载 OpenClaw Workload API；
- OpenClaw Gateway 无任意 Docker daemon 控制能力；
- Gateway 对 SPIRE、Guard、mTLS 和任意无 owner label 容器的 exec/start/stop/restart/kill/delete/archive 均被拒绝；
- Gate 创建的 sandbox 被强制标记且仍可完成正常 lifecycle/exec；
- 绕过 Guard 或 egress 的请求在 OpenViking 中没有 session/message 证据。

## 7. WP3：身份生命周期和拒绝收敛

### 7.1 测试范围

在 `can_reattest=false` 保持不变的情况下，补齐：

> **实现约束（远程验证记录 2026-08-06）**：SPIRE 1.15.1 的 `spire-server agent` CLI 不含 `update` 子命令（仅 ban/count/evict/list/purge/show），无法在 Agent 认证后修改 `can_reattest`。`can_reattest` 由 NodeAttestor 决定：OpenClaw `x509pop` Agent 为 `true`（可重认证），OpenViking `argus_tdx` Agent 为 `false`（满足本工作包"保持不变 false"要求）。因此 OpenClaw 侧生命周期测试观测 `can_reattest=true` 行为（SVID 到期自动重认证）；OpenViking 侧观测 `can_reattest=false` fail-closed。文档不应声称可在 CLI 层将 OpenClaw 设为 false。

- workload SVID 自动轮换；
- trust bundle 更新；
- SPIRE Agent 重启；
- SPIRE Server 重启；
- Workload API 短时不可用；
- 删除 workload entry；
- ban/delete Agent；
- SVID 到期；
- Agent data directory 丢失；
- 错误 trust domain；
- 旧 bundle；
- mTLS proxy 重启与恢复。

### 7.2 长连接问题

Agent 被 ban 或 entry 被删除后，已建立的 HTTP keep-alive/TLS 连接可能继续存在。本阶段必须定义拒绝收敛 SLA，并对代理增加：

- TLS 连接最大生命周期；
- idle timeout；
- SVID 或 bundle 更新后的旧连接排空；
- Agent 被撤销后的新连接强制重建；
- 轮换期间的错误分类和重试上限。

验收不得只检查新进程或新容器；必须同时检查已有连接和新连接。

### 7.3 建议验收指标

| 指标 | 目标 |
| --- | --- |
| SVID 轮换业务中断 | 明确上限并记录实际值 |
| Agent 重启恢复时间 | 明确上限并可重复测量 |
| Workload API 故障后的拒绝时间 | fail-closed，不继续使用过期身份 |
| Agent ban 后新连接拒绝时间 | 有界并满足定义的 SLA |
| 旧长连接排空时间 | 不超过配置的最大连接生命周期 |

### 7.4 完成判据

- 每个生命周期场景都有正向、负向和恢复结果；
- 失败阶段不会回退到明文或 API key-only；
- 旧 SVID、旧 bundle 和错误 trust domain 不能建立新连接；
- Agent/Server 重启不会破坏双 Agent 隔离；
- 所有收敛时间写入远程报告。

## 8. WP4：持久化克隆检测加固

当前 `binding_state_dir` 已解决单进程内存状态在重启后丢失的问题。本阶段继续完成：

- 同一 `instance_id` 并发绑定的竞争测试；
- 文件锁或进程级互斥；
- 临时文件写入、`fsync` 和原子 rename；
- binding 目录 `0700`、状态文件 `0600`；
- 文件截断、非法 JSON、重复记录和权限错误；
- 状态损坏时 fail-closed；
- 备份、恢复和升级兼容；
- 合法幂等重试与 clone conflict 的区分；
- attestation key 合法轮换的显式流程。

建议先抽象存储接口：

```go
type BindingStore interface {
    Bind(instanceID string, keyID string) error
    Lookup(instanceID string) (Binding, error)
}
```

本阶段仍可使用文件后端。接口抽象用于未来替换为共享事务存储，不代表已经支持多 SPIRE Server 副本。

完成判据：

- Server 重启后 clone conflict 仍被拒绝；
- 并发绑定只能有一个结果生效；
- 状态文件损坏不能导致 fail-open；
- 恢复备份后原有 binding 继续有效；
- 日志和指标能够区分 `idempotent`、`clone_conflict`、`state_corrupt` 和 `storage_error`。

## 9. WP5：多 runtime 隔离

### 9.1 改造内容

- 使用 `RUN_ID` 或 Compose project name 生成运行实例；
- 容器名、网络名、runtime 目录、Guest runtime 和端口全部参数化；
- 消除脚本对固定容器名和固定端口的非必要依赖；
- 每次运行生成独立 Agent data、attestation key、binding state 和 Workload API socket；
- 验证脚本必须校验实际 mount source 与声明的 runtime 完全一致；
- 清理脚本只能处理当前 `RUN_ID`，不得删除其他 runtime。

### 9.2 隔离矩阵

至少并行运行 A、B 两套身份状态，验证：

- A 的 Workload API 不能获得 B 的 SVID；
- B 的 OpenClaw label 不能从 A Agent 获取身份；
- A/B 不共享 Agent data inode；
- A/B 不共享 attestation key；
- A 的 OpenClaw 不能冒充 B；
- A 的 OpenViking SVID 不能用于 B 的服务；
- 停止 A 不影响 B；
- 清理 A 不删除 B 的状态。

### 9.3 完成判据

- 两套 runtime 可同时完成正向 E2E；
- 所有交叉身份测试均失败；
- 单套停止、重启和删除不会影响另一套；
- 日志、容器和 artifact 均能按 `RUN_ID` 归档。

## 10. WP6：结构化审计和指标

### 10.1 统一关联字段

建议所有业务路径日志至少包含：

```json
{
  "request_id": "...",
  "openclaw_run_id": "...",
  "guard_decision_id": "...",
  "request_digest": "...",
  "client_spiffe_id": "...",
  "server_spiffe_id": "...",
  "session_id": "...",
  "method": "POST",
  "path": "/api/v1/sessions/.../messages",
  "status": 200,
  "decision": "forwarded_mtls"
}
```

### 10.2 指标

至少增加：

- Guard allow、deny、error、timeout；
- request digest mismatch；
- mTLS handshake success/failure；
- peer SPIFFE ID mismatch；
- Workload API disconnect；
- 当前 SVID 剩余有效期；
- attestation success/failure/replay/clone conflict；
- OpenViking 转发延迟和状态；
- SVID 轮换、Agent ban 和连接排空收敛时间。

### 10.3 日志安全

禁止记录：

- 私钥；
- 完整 API token；
- Workload SVID 私钥；
- 完整 raw evidence；
- 未来的原始 Quote；
- 可重放的 Guard 授权材料。

### 10.4 完成判据

- 单个真实 OpenClaw marker 可从 Gateway 关联到 Guard、egress、mTLS server 和 OpenViking session；
- 负向测试可以通过结构化字段证明具体拒绝原因；
- 相同 request ID 不会被两个不同业务 body 复用；
- 远程报告能基于日志生成可审计证据表。

## 11. WP7：canary、切换和回滚演练

即使本阶段仍使用 mock RA，也应完成部署过程演练。

### 11.1 切换前

- 记录 Git SHA、镜像 config digest、Compose 配置和 runtime ID；
- 备份 OpenClaw 配置、OpenViking 配置和 SPIRE 状态；
- 验证旧链路仍可恢复；
- 确认新旧 runtime 不共享 Workload API 或 Agent data；
- 记录当前 SVID `NotAfter`。

### 11.2 Canary

- 只将指定 OpenClaw 实例指向新 egress；
- 执行真实插件 marker E2E；
- 执行 Guard deny/timeout 和 mTLS 负向矩阵；
- 观察规定窗口内的错误率、延迟和 SVID 状态；
- 不满足门槛时立即回滚。

### 11.3 回滚

- 恢复旧 OpenClaw endpoint；
- 停止对应新 runtime，但保留证据；
- 验证旧业务路径恢复；
- 验证新 SVID 不再被业务使用；
- 验证残留代理和端口不能继续接收请求；
- 归档回滚前后容器、配置、身份和日志状态。

### 11.4 完成判据

- canary、正式切换和回滚都有可重复脚本；
- 回滚不依赖手工修改容器内部文件；
- 旧链路恢复后真实 OpenClaw 插件请求成功；
- 被回滚 runtime 不再接收业务流量；
- 运行报告记录精确版本和所有 artifact。

## 12. WP8：真实 RA 接口预留

本工作包只冻结兼容边界，不进行真实安全验收。

### 12.1 两类 Evidence Provider 使用方式

需要明确区分：

1. **Node Attestation Provider**
   - 位于 OpenViking TDVM；
   - 由 OpenViking `argus_tdx` SPIRE Agent 本地调用；
   - 当前只监听 TDVM loopback；
   - evidence 由 SPIRE Server 插件交给 Trustee 验证；
   - 不直接对 OpenClaw Guard 暴露。

2. **Service Evidence Endpoint**
   - 仍位于 OpenViking/service 侧；
   - 仅在 `fresh_evidence` 模式中由 caller-side Guard 访问；
   - 为目标 OpenViking 服务生成 nonce-bound evidence；
   - 必须经过受保护的 endpoint adapter、目标身份绑定和网络认证；
   - 可以复用 Node Attestation Provider 的 Evidence Engine 和规范化协议，但不能直接暴露 Guest loopback 管理端点。

当前阶段只使用第一类。第二类只冻结接口，不部署、不计入 PASS。

### 12.2 Guard 后端模式

需要保持：

- `mock_allow`：Guard 不访问 Provider 或 Verifier，只验证必填 caller/target/authorization context 并明确返回 mock 决策；
- `spiffe_identity`：Guard 消费 egress 已验证的 peer SVID 上下文并执行 caller-local policy，不重新验证 Quote；
- `fresh_evidence`：Guard 生成 fresh nonce，访问 OpenViking service evidence endpoint，通过 RA Adapter/Trustee 验证后执行 policy；
- Evidence Provider 始终位于 OpenViking/service 侧，不在 OpenClaw 侧部署假 Provider；
- Trustee 仍是 SPIRE Server `argus_tdx` 插件或 Guard RA Adapter 的外部验证依赖；
- 业务授权摘要只属于 Guard/egress 合同，不替代 RA evidence binding；
- mock 和 future production profile 必须显式分离；
- 启动 production profile 时禁止 `mock_allow`、mock Evidence Provider 和 mock Trustee。

### 12.3 启用 evidence 模式前的代码前置修复

现有 Argus Guard `evidence` 模式不能直接视为生产可用。在接入真实 RA 前必须：

- 由 Guard 根据自己生成的 nonce、target 和 canonical EvidenceRequest 独立重算 expected binding；
- 禁止从 Provider 返回的 `evidence.report_data` 或 `canonical_request_digest` 反向构造 expected binding；
- 将默认 `AllowAllPolicyEvaluator` 替换为显式 caller-local policy；
- 验证 target service identity、SPIFFE ID、trust domain、profile digest 和 evidence freshness；
- 为 Guard 到 service evidence endpoint、Guard 到 Trustee 的连接增加身份认证和 TLS；
- 将 Guard endpoint 限制在 caller-side egress 可访问的本地边界；
- 验证同一个 evidence/decision 不能跨 target、operation 或 request digest 重放。

上述修复可以提前编码和进行 fake-service 契约测试，但没有真实 Quote/QGS/Trustee 时不能计入真实 RA PASS。

### 12.4 配置安全栏

可以提前增加的配置安全栏：

- `ARGUS_ALLOW_MOCK=1` 才允许启动 mock profile；
- mock 服务启动时输出明确 banner 和指标标签；
- production profile 检测到 mock endpoint 时拒绝启动；
- 验收报告强制记录 `verification_mode`；
- CI 检查 production 模板不存在 `join_token`、`mock_allow` 和 fake endpoint。

本阶段不得把接口兼容测试写成真实 Quote/QGS 或正式 Trustee 已完成。

## 13. 推荐执行顺序

### 阶段 A：安全门控

1. 冻结 business authorization binding 和 Guard decision 合同。
2. 在 mTLS egress 中实现同步 Guard 门控。
3. 删除验证脚本中的独立 Guard 通过假设。
4. 完成 Guard fail-closed 矩阵。
5. 使用真实 OpenClaw marker E2E 验证同请求因果链。

阶段 A 完成后，才能描述为：

> 每个通过当前 mTLS egress 的 OpenClaw 业务请求都必须取得对应 Guard ALLOW。

仍不能描述为：

> Guard 已完成真实 TDX 远程证明。

### 阶段 B：旁路和生命周期

1. 隔离 OpenClaw Gateway 的 Docker 控制能力。
2. 收紧 OpenClaw egress 和 OpenViking ingress 网络。
3. 增加 SVID 轮换、到期、撤销和 Agent ban 测试。
4. 设置连接最大生命周期和排空策略。
5. 强化 binding store。

### 阶段 C：工程化验收

1. 参数化 runtime、网络、容器和端口。
2. 完成双 runtime 并行隔离矩阵。
3. 统一日志、指标和 artifact 归档。
4. 完成 canary、切换和回滚演练。
5. 更新状态文档和远程验收报告。

## 14. 远程验证要求

本机不要求运行完整测试，代码完成后在具备 Host、TDVM、Guest Docker 和真实 OpenClaw/OpenViking 的远程机器验证。

远程验证不得：

- 放宽断言；
- 跳过负向测试；
- 复用其他运行的 Agent data、attestation key 或 Workload API；
- 切回 `join_token`；
- 使用代理返回的 HTTP 状态代替 Argus 服务响应；
- 使用测试辅助请求冒充真实 OpenClaw 插件写入；
- 将 `mock_allow` 描述为真实 RA。

每轮报告至少记录：

- Git SHA 和工作树状态；
- Host/Guest runtime 绝对路径；
- Agent ID、workload SVID 和 entry parent；
- Workload API mount source；
- Guard mode 和决策；
- request ID、decision ID 和 request digest；
- OpenViking session/message/commit 证据；
- 所有负向测试的原始错误；
- 容器、端口和 restart policy；
- 明确的 PASS/FAIL/DEFERRED 边界。

## 15. 最终验收清单

只有全部满足以下条件，本计划才算完成：

1. Guard 作为 caller-side PDP，已位于真实 OpenClaw 请求的同步转发路径。
2. Guard DENY、503、timeout、malformed 和 digest mismatch 均 fail-closed。
3. Guard 失败时 OpenViking 没有对应 marker 写入。
4. OpenClaw 到 OpenViking 不存在已知直连旁路。
5. OpenClaw Gateway 不具备任意 Docker daemon 控制能力。
6. SVID 轮换、到期、撤销和 Agent ban 具有有界收敛时间。
7. 已建立连接不会无限绕过新的身份拒绝状态。
8. binding store 能抵抗并发、重启和损坏场景。
9. 两套 runtime 能并行运行且身份、状态和清理完全隔离。
10. 真实业务请求能跨 Guard、mTLS 和 OpenViking 完整关联。
11. canary、切换和回滚均有远程证据。
12. mock 与 future production profile 有显式安全栏。
13. 文档仍明确 Real Quote/QGS、正式 Trustee、真实 RA 和 Envoy 为 DEFERRED。
14. Node Attestation Provider 与 future service evidence endpoint 已在接口、网络和验收声明中明确区分。
15. business authorization binding 未修改或替代 NodeAttestor RA evidence binding。

## 16. 阶段完成后的准确结论

完成本计划后，可以声称：

> Argus-SPIFFE v2 已在 mock RA 条件下完成真实业务请求的不可绕过 Guard 门控、SPIFFE mTLS 强制传输、身份生命周期验收、多 runtime 隔离和可恢复切换。

仍然不能声称：

> Argus-SPIFFE v2 已完成真实 TDX Quote/QGS、正式 Trustee 验证或生产级远程证明安全验收。

下一阶段再将 Node Attestation Provider 的 mock quote 生成替换为真实 Quote/QGS，将 mock Trustee 替换为正式 Trustee；若要求每请求新鲜证明，再增加受保护的 OpenViking service evidence endpoint 并启用 Guard `fresh_evidence` 模式。数据面门控、业务授权绑定、生命周期、审计和切换机制继续复用。
