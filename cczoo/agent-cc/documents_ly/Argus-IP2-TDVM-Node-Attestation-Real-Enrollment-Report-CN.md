# Argus IP2 TDVM 真实 Node Attestation Enrollment 执行记录

> 分类：POC 运行记录。本记录对应 commit `9564035`（分支 `feat/argus-spiffe-v2-val`）。
> 运行结论只对本次真实 TDVM 与本次签发窗口有效；新改动通过前不得复用本记录的运行结论。
> 本文不构成生产 acceptance，也不把本轮的 Quote 或 SVID 当作正式信任基准。

## 1. 执行范围与约束

- 执行主体：IP2 TDVM（veLinux GNU/Linux 2 (lyra)，内核 `5.15.120.ve.6-tdxg.3-amd64`）
- 目标：真实 TDX Node Attestation + SPIRE Agent SVID 签发（固定 Agent ID）
- 明确不部署：WorkloadAttestor 业务功能、OpenViking、Broker、业务 mTLS
- 全程禁止：join token、insecure_bootstrap、修改/输出 proof 私钥、生成新 proof key、
  连接 Trustee 8443、清理已有数据目录、把服务配置成永久运行、使用非 1.15.2 的 SPIRE Agent
- 执行时间（UTC）：2026-09-03 07:57Z — 2026-09-04 07:43Z

## 2. 环境与组件事实

| 项 | 值 |
|---|---|
| TDX 设备 | `/dev/tdx_guest`（char 10:124, mode 0600），`tdx_guest`/`tsm` 内核模块已加载 |
| TSM | `/sys/kernel/config/tsm/report` 存在，configfs 已挂载；Quote 经 TSM configfs → 内核 TDREPORT → Host QGS |
| Evidence Provider | `/usr/local/bin/argus-tdx-evidence-provider`，SHA-256 `7f4f5c827a7e5f2b56f40e98ab9bed782ce8db6f4007d5b1dd08350d8e413200`；无 systemd unit，本轮以前台进程启动（pid 837802），UDS `/run/argus/evidence-provider.sock`（0660） |
| proof key | `/var/lib/spire/argus-tdx/proof-key.pem`，PKCS#8 Ed25519，mode 0600，全程未修改；raw 公钥 SHA-256 `be8570d79b444c272b579f1fb5a4ca69f625bc9561b1b1683942786958efff58` |
| Agent NodeAttestor 插件 | `/opt/spire/plugins/argus-tdx-nodeattestor-agent`，SHA-256 `51ee6484c31a5019157a35c391813496dcf30120a6e14bbc9b0247a113128775`；go1.23.5，module `github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/cmd/agent`，`vcs.revision=36718d4aa043a0ec180befee0cdaa003ca813441`（当前分支祖先，构建时间 2026-08-27T06:18:13Z，`vcs.modified=false`） |
| SPIRE Agent | 官方 v1.15.2 linux-amd64-musl release，官方 `_sha256sum.txt` 验证通过；`/opt/spire-1.15.2/bin/spire-agent` SHA-256 `978ed3208bc11cde4af2fe8d1cba13fd38cc26de61b71f5118819f220494f9d2` |
| POC policy | base ID `argus-node-poc-d9a7029b57fe`，not_after `2026-09-04T10:09:27Z` |
| 固定 Agent ID | `spiffe://argus.local/spire/agent/argus_tdx/openviking-node` |

## 3. 第一阶段：TDX Node diagnostic evidence package

在发起任何 enrollment 之前，先完成只读诊断（产物在 TDVM 的
`/tmp/argus-node-diagnostic-20260903T075748Z/`，含 diagnostic-manifest.json 与
`/tmp/argus-node-diagnostic-20260903T075748Z.tar.gz`）：

- 独立重算 `NodeRuntimeData = LP16("argus.node.tdx.reportdata") || LP16(SPIFFE_ID) || nonce[32] || proof_public_key[32]`（LP16 为 u16 big-endian 长度前缀），先与仓库 Rust 单测向量比对一致，再对真实请求重算。
- `expected REPORTDATA = SHA-384(NodeRuntimeData) || 16 字节 0x00`。
- Provider 返回 `evidence_type=tdx_quote`、`quote_format=tdx`、quote 可 base64url-no-pad 解码。
- 产物：`/tmp/argus-node-diagnostic-20260903T075748Z.tar.gz`
  SHA-256 `86481212ca72489532aa46cffefcdf1c91058b5b641d6194683b075bef11ee8b`；
  quote 5006 字节，quote SHA-256 `78f03e9f3700bf14f1a90c740983244cd72ed3015274edbefad7aae9d1fc7c34`；
  expected REPORTDATA hex `6b7548889c0135a0d23ee9057ef4349da4acc81475510f8e96f9bffb53de9c8b4f6a84ea96a394a8f0fd95bf14883dc600000000000000000000000000000000`。
- 分类 `POC_DIAGNOSTIC_ONLY`，明确未通过 Trustee appraisal，不得作为 reference values。

## 4. 第二阶段：Enrollment 前置验证（2026-09-03 10:28Z）

- 时间窗口：`BOOTSTRAP_WINDOW_VALID=YES`（早于 09-04T04:38:22Z）、`POLICY_WINDOW_VALID=YES`。
- Bootstrap 包 `/tmp/ip2-agent-bootstrap-20260903T101957Z.tar.gz`：外层 SHA-256 与 IP1 一致；
  解包后 `sha256sum -c` 通过；归档严格 3 文件；bundle 仅 CERTIFICATE（4 条）；
  manifest 字段核对通过（trust_domain/server_port/NodeAttestor/固定 Agent ID/proof-key pin 与本机公钥匹配）。
- 本机组件：Provider 二进制/进程/UDS/proof key（0600，PKCS#8 Ed25519）/插件构建信息全部核验通过。
- 隧道 `nc -vz -w5 127.0.0.1 18081` 通过。

## 5. 第三阶段：尝试 1/2 与根因（2026-09-03 11:11Z — 11:17Z）

隔离 POC 路径（全新，不触碰任何既有数据）：

```
/etc/spire/argus-poc/agent.conf
/etc/spire/argus-poc/bootstrap.crt
/var/lib/spire/argus-poc/{data,keys}
/run/spire/argus-poc/agent.sock
```

- **尝试 1（启动即崩溃）**：SPIRE v1.15.2 catalog 合同要求至少 1 个 WorkloadAttestor，
  否则 `Agent crashed: plugin type "WorkloadAttestor" constraint not satisfied`。
  经用户批准的最小修正：加入内置 `unix` WorkloadAttestor（`plugin_data {}` 空默认，
  零 selector、零 registration entry、无 Workload API 消费者），并记录差异
  （`/tmp/ip2-agent-poc/config-contract-diff.txt`）。NodeAttestor/KeyManager 不变。
- **尝试 2（attestation 流握手 EOF）**：本地全部健康（bundle loaded、argus_tdx 插件启动、
  fresh attestation 发起），但 Server 方向 `authentication handshake failed: EOF`，
  重试约 55s 后 Agent 按 1.15.2 语义退出。诊断结论：18081 远端零字节回包，问题在
  隧道远端而非 IP2。两次启动限额用尽，停止并上报。

## 6. 第四阶段：传输闸门排查（2026-09-04 01:38Z — 07:14Z）

IP1 与公司电脑依次重建转发（18082 → 18083），IP2 侧每轮执行只读传输闸门
（TCP → TLS 验证 → ALPN=h2 → HTTP/2 SETTINGS），结果与根因如下：

| 轮次 | 端口 | 观测 | 结论 |
|---|---|---|---|
| 18082 | 闸门 | TCP PASS；TLS 握手零字节回包、约 2s 后关闭；无 ALPN、无 SETTINGS | 远端零字节，闸门 FAIL，不启动 Agent |
| 18083（会话 1） | 闸门 | 同上（`read 0 bytes / written 313-331 bytes`；静默 2.11s / 发送数据 2.12s 均被关） | 纠正早期误读：openssl 无证书可验时默认打印 `Verification: OK`，实际从未收到 ServerHello |
| 18083（会话 2） | 闸门 | 静默挂起 >25s（黑洞态） | 公司电脑重配后目标“连上了某个不回话的东西” |
| 18083（会话 3，改 RemoteForward 后） | 闸门 | 恢复快速关闭（0 字节） | 公司电脑到 IP1 **无法直连**，必须经代理 |

**根因**：OpenSSH `RemoteForward` 的远段目标地址由 **SSH 客户端（公司电脑）自身解析连接**，
不支持经代理转发。公司电脑所在办公网不能直连 IP1 `10.112.120.22:8081`
（`127.0.0.1:8081` 的写法等于连公司电脑自己），因此远段连接失败，
IP2 本地表现为“ClientHello 发出后立即 EOF”。

**解决**：IP1 另行提供可用链路（18084），闸门由 IP1 预验证后交 IP2 复测。

## 7. 第五阶段：第 3 次 enrollment 成功（2026-09-04 07:36Z — 07:43Z）

### 7.1 闸门复测

- `POC_POLICY_VALID=YES`（07:36:22Z < 10:09:27Z）
- `ss -ltnp '( sport = :18084 )'` → sshd 监听；`nc -vz -w5 127.0.0.1 18084` PASS
- `/tmp/bootstrap-current-18084.crt` SHA-256 `c3a99a9b608c175976e3cffbbea42fa703879c13be263bf699aadb85072a861f`
  与 IP1 一致；仅 CERTIFICATE（6 条），无 PRIVATE KEY

### 7.2 配置变更（仅两个连接字段）

- 备份：`/tmp/argus-agent-attempt3.9xqy2E/argus-poc/`（agent.conf + bootstrap.crt），
  `start.utc=2026-09-04T07:36:33Z`；改前 agent.conf SHA-256 `973700b2…` 已记录。
- 修改：`server_port = "18084"`、`trust_bundle_path = "/tmp/bootstrap-current-18084.crt"`。
- 不变：`server_address=127.0.0.1`、NodeAttestor=argus_tdx、proof_key_path、
  evidence_socket_path、plugin_cmd/checksum、trust_domain、unix WorkloadAttestor。
- `spire-agent validate -config /etc/spire/argus-poc/agent.conf` → `SPIRE agent configuration file is valid.`

### 7.3 唯一一次启动与成功链

启动方式：`/opt/spire-1.15.2/bin/spire-agent run -config /etc/spire/argus-poc/agent.conf`
前台运行，日志 `tee` 至 `/tmp/argus-agent-attempt3.9xqy2E/agent-run-3.log`，
启动 UTC 时间 `2026-09-04T07:38:59Z`（`agent-start.utc`）。

```
15:38:59  Starting agent data_dir=/var/lib/spire/argus-poc/data version=1.15.2
15:38:59  Plugin loaded argus_tdx NodeAttestor（external 插件启动）
15:38:59  Bundle loaded trust_domain_id="spiffe://argus.local"      ← bootstrap 验证 Server
15:38:59  No pre-existing agent SVID found. Will perform node attestation
15:38:59  SVID is not found. Starting node attestation               ← fresh
15:39:02  Node attestation was successful reattestable=true
          spiffe_id="spiffe://argus.local/spire/agent/argus_tdx/openviking-node"
15:39:02  Bundle added (svid_store_cache)                            ← SVID 入缓存
15:39:02  Starting Workload and SDS APIs address=/run/spire/argus-poc/agent.sock
```

中间步骤（AgentHello → NodeChallenge → Provider fresh Quote → transcript PoP →
Trustee appraisal）发生在插件与 Server 内部；IP2 侧的可观测证明是 **Server 仅在
appraisal 通过后签发 SVID**：3 秒内拿到固定 SPIFFE ID 即证明全链通过。

### 7.4 运行后核验

- `spire-agent healthcheck -socketPath /run/spire/argus-poc/agent.sock` → `Agent is healthy.`
- Agent 进程 `936284` 保持前台运行，未停止。
- `spire-agent api fetch x509`（JSON 输出，仅提取元数据）→
  `rpc error: PermissionDenied desc = no identity issued`，`registered=false`：
  **预期结果**——本轮按要求零 registration entry，Workload API 不会为调用方签发
  workload SVID，不影响 Agent 自身 SVID。SVID 有效期受 POC policy
  not_after `2026-09-04T10:09:27Z` 约束，签发时刻 07:39:02Z。

## 8. 成功链对照（验收标准）

| 阶段 | 结果 | 证据 |
|---|---|---|
| 连接 127.0.0.1:18084 | PASS | `nc` + 18084 sshd LISTEN |
| TLS/HTTP2 transport established | PASS | IP1 预验证 + IP2 闸门复测（ALPN h2 + SETTINGS） |
| Agent attestation stream opened | PASS | `Starting node attestation` |
| argus_tdx payload sent / NodeChallenge received / fresh TDX Quote / transcript PoP | PASS（Server 侧接受） | SVID 仅在 appraisal 通过后签发 |
| Trustee appraisal affirming | PASS（由 SVID 签发证明） | `Node attestation was successful` |
| Agent SVID received | PASS | 固定 SPIFFE ID + `Bundle added` + health ready |
| Agent health ready | PASS | `Agent is healthy.` |

分类：`POC_DIAGNOSTIC_ONLY`（诊断包）→ 本轮 enrollment 为 **POC 成功**，非生产 acceptance。

## 9. 合规确认

- 全程未使用 join token / insecure_bootstrap；未生成新 proof key；proof key 未修改、
  未输出；public pin `be8570d7…` 不变。
- SPIRE Agent 严格 v1.15.2 官方 release（官方校验和验证）；未使用 1.15.3/latest。
- 未部署 WorkloadAttestor 业务功能（仅内置 unix 空配置满足 v1.15.2 catalog 合同，
  已记录差异并经批准）。
- 未清理任何已有数据目录；未创建 systemd unit；Agent 前台运行（`AGENT_RUNTIME_PRIVILEGE=ROOT_POC_ONLY`）。
- 未连接 Trustee 8443；未把本轮 Quote/SVID 当作正式信任基准。
- 私钥与完整 SVID 内容未输出；`fetch x509` 仅以 JSON 元数据方式确认 workload 身份为空（预期）。

## 10. 残余风险与后续

- POC policy 于 2026-09-04T10:09:27Z 到期；如需持续运行，由 IP1 提前发放新 policy
  （新 base ID / not_after），并重新验证 bootstrap 链路。
- 本轮未执行 re-attestation 观察（`reattestable=true` 只是允许，非完成证据，
  见主方案文档 §7）。
- 公司电脑 → IP1 的链路依赖代理，长期方案应改为 IP1 侧可控的反向隧道或专用网络放行。
- 后续 Stage 2（Workload Attestation、Broker、业务 mTLS）不在本轮范围内。

## 附录 A：关键产物清单

| 文件 | 路径 |
|---|---|
| 诊断包 | `/tmp/argus-node-diagnostic-20260903T075748Z.tar.gz` |
| 尝试 1 日志 | `/tmp/ip2-agent-poc/agent-run-1.log` |
| 尝试 2 日志 | `/tmp/ip2-agent-poc/agent-run-2.log` |
| 尝试 3 备份目录 | `/tmp/argus-agent-attempt3.9xqy2E/`（含 agent.conf/bkp、bootstrap.crt、start.utc、agent-start.utc、agent-run-3.log、svid-fetch.json/err） |
| 尝试 3 运行日志 | `/tmp/argus-agent-attempt3.9xqy2E/agent-run-3.log` |
| v1.15.2 catalog 差异记录 | `/tmp/ip2-agent-poc/config-contract-diff.txt` |

## 附录 B：最终变量表（2026-09-04T07:43:10Z）

```
UTC_NOW=2026-09-04T07:43:10Z
POC_POLICY_VALID=YES
NODE_ATTESTATION_RESULT=SUCCESS
AGENT_SPIFFE_ID=spiffe://argus.local/spire/agent/argus_tdx/openviking-node
AGENT_HEALTH=HEALTHY
AGENT_SVID_AVAILABLE=YES
AGENT_PID=936284
AGENT_LOG_PATH=/tmp/argus-agent-attempt3.9xqy2E/agent-run-3.log
AGENT_CONFIG_BACKUP=/tmp/argus-agent-attempt3.9xqy2E/argus-poc/
AGENT_SERVER_ADDRESS=127.0.0.1
AGENT_SERVER_PORT=18084
IP2_BOOTSTRAP_BUNDLE=/tmp/bootstrap-current-18084.crt
IP2_BOOTSTRAP_BUNDLE_SHA256=c3a99a9b608c175976e3cffbbea42fa703879c13be263bf699aadb85072a861f
ADDITIONAL_RETRY_PERFORMED=NO
WORKLOAD_COMPONENTS_DEPLOYED=NO
CLASSIFICATION=POC_DIAGNOSTIC_ONLY（诊断包）/ POC_ENROLLMENT_SUCCESS（本轮）
```
