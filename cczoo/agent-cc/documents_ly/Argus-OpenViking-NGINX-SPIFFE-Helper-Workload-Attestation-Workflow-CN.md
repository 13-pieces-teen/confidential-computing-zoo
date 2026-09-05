# OpenViking Workload 认证方案：NGINX + SPIFFE Helper

修订日期：2026-09-05。本文件为首轮实现对应的当前方案，取代此前同名文档中的 Rekor 强制门禁与 Agent 持续重证明设计。代码、配置及公司环境步骤见 [运行手册](../core/spire/workload/README.md)，执行结果见 [验证记录](../core/spire/workload/VALIDATION.md)。

## 1. 目标与本轮边界

贯通 **TC API 启动 OpenViking → Helper 引用实际服务 PID → WorkloadAttestor → 真实 TDX Quote → Trustee 评估 → SPIRE 目标 SVID → NGINX mTLS → OpenViking 业务响应**。

| 项目 | 已确定选择 |
|---|---|
| SPIRE | 官方 Server/Agent v1.15.3；两个 Attestor SDK 同步 v1.15.3 |
| Helper | 官方 v0.11.0 源码扩展 Broker 模式，定制版本 0.11.0-argus.1 |
| Trustee | 直接使用 v0.21.0 的 HTTPS `/attestation`，固定 EAR 公钥与固定 workload policy |
| 部署 | 每个 TDVM 一个 OpenViking 服务进程；Provider、Agent、Helper、NGINX、AuthZ 由宿主机管理 |
| Rekor | TC API/TruCon 原有日志上传保持，本轮不验证 Rekor，也不把上传成功作为身份门禁 |
| 失效处理 | Helper 持有 pidfd，systemd 联动 NGINX；Agent 独立持续监测和周期重新证明后续实现 |
| 完成边界 | 本地软件测试与公司真实 TDX 验收分别记录 |

不修改 SPIRE Core，不引入独立评估服务，不扩展服务网格、业务出站代理或多目标路由。单一逻辑服务可在重新 launch 后继续使用同一 SPIFFE ID，但新进程实例必须重新登记、订阅和证明。

## 2. 身份与密钥归属

| 用途 | SPIFFE ID | 持有/使用密钥的组件 |
|---|---|---|
| 节点身份 | `spiffe://argus.local/spire/agent/argus_tdx/openviking-node` | SPIRE Agent |
| Helper 调用 Broker | `spiffe://argus.local/infra/openviking-helper` | Helper 的 Workload API source |
| OpenViking 服务身份 | `spiffe://argus.local/service/openviking-cmem` | Agent 管理并交付；Helper 发布；NGINX 终止 TLS |
| 固定客户端身份 | `spiffe://argus.local/agent/openclaw` | 获准 OpenClaw 客户端或其身份代理 |

被证明主体是本次启动的 **OpenViking 实际服务进程实例**；SVID 的 TLS 使用者是 NGINX。OpenViking 不接触 SPIRE socket 或私钥。Helper 自身身份仅用于 Broker mTLS，不作为 OpenViking 目标身份写入 PEM。

Trustee HTTPS 使用独立预置 CA 与固定 EAR 签名公钥，不依赖尚未签发的 OpenViking SVID。避免启动时用目标身份申请目标身份的循环依赖。

## 3. Node 前提与版本迁移

保留原 Node challenge、PoP、证明公钥、REPORTDATA 编码、固定 Agent ID 和 Trustee EAR 验证合同。升级 SDK 与二进制路径；Agent 的原 proof key、Server 的 CA/Node policy 继续使用。

配置合并工具仅更新 Node 插件可执行文件及 SHA-256、Agent 的 Provider/Workload API socket，并加入 Workload/Broker 设置。历史 v1.15.2 报告保留原版本；必须在公司重新验证 v1.15.3 节点加入。

启动预检既检查待运行 Agent 二进制，也通过已有 SSH 信任连接读取 Server 的实际 `/proc/<MainPID>/exe` 版本和当前 Entry，不能只凭配置中的版本字符串判断。

## 4. TC API 启动与受控登记

1. TC API 使用 `nginx-spiffe-helper-v1` profile 启动批准镜像，写入 workload/launch labels，并保留返回的 container ID、launch ID、实际 image config digest 和 container init 信息。现有日志仍上传到 Rekor。
2. OpenViking 使用非特权、只读 rootfs；配置单独只读挂载，业务数据独立可写。OpenViking 独占 network namespace，内部仅监听 `127.0.0.1:1933`。
3. 宿主机登记工具检查 Docker 与 `/proc`，从 TCP LISTEN socket 的唯一持有者解析 **实际服务 PID**。container init 可能是 supervisor，不能直接等同于服务进程。
4. 工具生成 root 保护的 `/run/argus-workload/target.json`。登记文件不作为批准基线，也不能由业务提供。只有一个监听进程；多 worker、共享 listener、错误监听地址会被拒绝。
5. Helper 在发起订阅之前，校验登记并同步打开 pidfd。替换目标必须停止旧栈，重新登记和认证。

镜像内容身份使用 Docker 实际 `.Image` / image inspect `.Id` 的 SHA-256 config digest。image tag、名称及其摘要不进入身份授权依据。

## 5. Workload 绑定合同与取证

协议域为 `argus.workload.tdx.v1`。WorkloadAttestor 生成新的 32 字节随机 nonce，通过本机 UDS 请求 Provider，只提交协议、nonce 和 PID。Provider 不接受调用方提供的运行属性作为已验证事实。

Provider 独立检查登记文件、Docker container、launch/workload labels、实际 image config digest、只读 rootfs/配置 mount、节点 boot ID、目标 PID 与启动时间、PID/network namespace、executable、配置实际内容摘要、cgroup/容器关联及唯一回环 listener；生成 Quote 后再检查一次，实例变化即失败。

runtime data 是固定 schema 的结构化 JSON，所有值为可打印 ASCII 字符串：

```text
protocol, nonce, agent_id, boot_id,
pid, start_time, pid_namespace, net_namespace,
container_id, launch_id, workload_id,
image_config_digest, config_path, config_digest,
executable, listen_port, rootfs_read_only, policy_id
```

十进制值用规范字符串表示，避免跨语言 JSON 数字精度差异。

```text
canonical = RFC 8785-compatible compact JSON with sorted keys
REPORTDATA = SHA-384(canonical) || 16 zero bytes
```

Provider 通过 Linux TSM 生成真实 TDX Quote。Node 的 `/node-evidence` 保持；新增 `/ra/v1/workload-evidence`。runtime 不提供 mock fallback。

[共用向量](../core/spire/workload/testdata/runtime-data.json) 由 Go、Rust Provider 和采用 Trustee 相同 canonicalizer 的测试共同验证。Quote 的密码学与平台评估交给 Trustee，本地插件不自行把 Quote 内容解释为通过。

## 6. Trustee 评估与静态身份授权

WorkloadAttestor 直接向固定 HTTPS origin 的 `/attestation` 提交：

- TDX evidence：内层 Quote 使用标准 base64；整个 evidence JSON 使用无补位 base64url。
- `runtime_data: { "structured": ... }` 与 `runtime_data_hash_algorithm: "sha384"`。
- 一个固定、非默认 workload policy ID。

Trustee 校验 Quote 与 REPORTDATA 绑定，向 Rego 暴露经过绑定的 `runtime_data_claims`。固定 policy 检查 TDX 平台、TCB 状态、非 debug、批准启动测量、镜像/配置内容摘要、workload 与运行属性。

插件只接受固定 P-256 公钥验证的 ES256 EAR，同时核对 issuer/profile、iat/exp/nbf、`cpu0` affirming 状态、policy ID 与本次 runtime data 对应的 64 字节 REPORTDATA。停止接受旧自定义 `allow` JSON。

只有上述检查和本地实例二次检查通过，插件才返回：

```text
argus_tdx:verified:true
argus_tdx:workload_id:openviking-cmem
argus_tdx:policy:<approved policy>
argus_tdx:agent_id:<fixed Agent ID>
argus_tdx:image_config_digest:<approved content digest>
argus_tdx:config_digest:<approved config digest>
```

目标静态 Entry 同时要求这些 selectors，固定 parent Agent，并禁用 X.509-SVID 预取。相同目标身份不得存在较弱的平行 Entry。普通 Workload API 路径不返回自定义证明 selectors。SPIRE CA 仍负责签发 SVID；Quote、EAR 与 selectors 都不是证书。

## 7. 官方 Helper Broker 模式

Helper 先通过 Workload API 取得自身 SVID，再经独立本机 Broker UDS 建立 mTLS，并检查 Agent ID。每个请求带 `broker.spiffe.io: true`；引用类型仅允许本机 `WorkloadPIDReference`。

Broker 开始订阅时触发 WorkloadAttestor。Helper 对返回消息采用完整快照语义：仅选择配置中的目标 ID；目标消失、重复身份、错误证书链、SVID 属性、私钥配对或过期都不能保留旧目标凭据。

每次凭据更新：

1. 在受控 tmpfs 下写完整新代次的证书、私钥和 bundle，权限 0600。
2. 原子切换 `current` 链接。
3. 验证 NGINX 配置，首次启动或 reload。
4. 用真实 TLS 握手检查 NGINX 实际加载的新证书，随后发布 readiness。
5. 清理旧代次私钥文件。

未配置 `broker {}` 时继续运行官方 Workload API 行为。首版 Broker 仅支持 Linux 本机 PID、X.509-SVID、常驻进程，不支持 JWT、单次运行或上游 cmd/PID-file 生命周期选项。

## 8. NGINX、AuthZ 与失效联动

NGINX 进入 OpenViking 的专用 network namespace，只对外发布 1943。OpenViking 的 1933 回环接口不能从宿主机/容器外直接访问。NGINX 先完成 TLS 客户端证书链验证，再经 root/NGINX 组保护的 AuthZ UDS 发送实际证书。

AuthZ 校验 NGINX 的 TLS 验证结果、实际叶证书中的唯一合法 SPIFFE URI、有效期、非 CA、key usage、client/server EKU，以及固定 OpenClaw ID。外部客户端发送的同名请求头由 NGINX 覆盖；业务响应必须通过 AuthZ。客户端同时核对 OpenViking 目标 ID。

TLS session resumption 与 early data 关闭。Helper 检测目标退出、实例变化、目标身份移除、订阅断开、凭据过期或发布失败后，撤下 readiness、清除 PEM 并停止 NGINX。Helper 崩溃由 systemd 依赖和退出清理兜底。停服连接清理默认最多 5 秒，目标检测另有约 500 ms 轮询与调度延迟。

本轮的持续性边界必须明确：SPIRE v1.15.3 Broker 在订阅开始时运行证明，随后推送缓存中的 SVID 更新；它不会因普通轮换自动发起新的 TDX 取证。Helper 重启/重连产生新订阅、新 nonce、新证明。独立周期重新证明及 Agent 自有进程生命周期收紧留待后续。

## 9. SPIFFE 符合性与信任假设

该流程采用标准 SPIFFE ID、X.509-SVID、Workload API，以及 SPIRE 提供的实验性 Broker API。通过自定义 WorkloadAttestor 提供可信 selectors，静态 Entry 决定授权，SPIRE CA 签发身份，NGINX 代理使用身份，符合 SPIRE 的插件和身份交付分工。

Trustee appraisal 与 Rekor 日志属于项目自定义取证/审计流程；SPIFFE 不要求必须使用某个启动器或 Rekor。SPIFFE 本身也不证明业务执行结果，实际 2xx 响应单独验收。

本方案信任 TDVM 内的宿主机内核、Provider、Agent、TC API/Docker 控制链、Helper、NGINX 与 AuthZ。只读镜像、真实 config digest 和实例绑定覆盖取证时的运行状态，不声称证明运行后所有内存、动态代码或被攻陷宿主机的行为。NGINX 与 Helper 作为目标身份代理属于可信计算基础。

## 10. 验收

本地验证包括 Node 原绑定/PoP/EAR 回归、Workload nonce/实例/基线与 EAR 反例、Rust/Go/Trustee 共用向量、真实 Rego 引擎、Helper 身份隔离/完整快照/PEM/reload 失败、真实 Linux pidfd，以及 NGINX mTLS/AuthZ/轮换。

公司验收另行执行 v1.15.3 Node 加入、真实 Quote/DCAP、直接 Trustee HTTPS、固定 policy、目标 SVID、OpenViking 业务调用、Helper crash/目标退出/普通轮换，并保存版本、launch、PID、nonce、EAR 摘要、policy、SVID 序列号和业务结果关联。没有公司记录时不标记真实 TDX 链路已跑通。

接口依据：[SPIRE v1.15.3 Broker](https://github.com/spiffe/spire/blob/v1.15.3/doc/spire_agent.md#spiffe-broker-api)、[官方 Helper v0.11.0](https://github.com/spiffe/spiffe-helper/tree/v0.11.0)、[Trustee v0.21 runtime data](https://github.com/confidential-containers/trustee/blob/v0.21.0/attestation-service/src/lib.rs)、[EAR claims 与 policy](https://github.com/confidential-containers/trustee/blob/v0.21.0/attestation-service/src/ear_token/broker.rs)。
