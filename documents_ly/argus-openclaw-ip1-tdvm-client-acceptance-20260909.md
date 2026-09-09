# Argus OpenClaw IP1 TDVM 客户端实施与联合验收报告

- 实施时间：2026-09-09
- 实施主机：公司 IP1
- 运行 ID：`20260909T144253+0800-ip1-openclaw-client`
- 总体结论：**FAIL**
- 失败原因：客户端身份、mTLS、真实业务写入/读回、归档、轮换和故障恢复均已实际完成，但必验的“新会话从长期记忆召回随机事实”失败。已完成的归档任务返回 `memories_extracted={}`，ContextEngine 只注入顶层 `.abstract.md`，模型因此返回 `UNKNOWN`。不能把该结果宣称为完整端到端 PASS。

## 1. 状态汇总

| 验收项 | 状态 | 实际结果 |
| --- | --- | --- |
| 固定源码与隔离部署 | PASS | 基线 `b57f5210a82a29c3ebcf66a727b9ee13274df132`；独立 worktree 上增加本地修复提交 `84a735da834f5c2396e34e5efc452635c90283b2`，未推送；原部署未覆盖 |
| 独立 TDX Guest 启动 | PASS | VM `argus-openclaw-stage2-b57f5210`，QEMU PID `2777274`，SSH `127.0.0.1:2224`，Guest `/dev/tdx_guest` 存在 |
| OpenClaw TDX 远程证明 | NOT_RUN | 本阶段按合同只使用 x509pop；未把 TDX Guest 启动冒充远程证明 |
| SPIRE x509pop Node 加入 | PASS | SPIRE Agent 1.15.3；Agent ID `spiffe://argus.local/spire/agent/x509pop/d3c786bb67d4d88e82ddfaf681db4057f9aa4c9a`；可 re-attest |
| Helper 严格 Entry | PASS | Entry `25b693b9-7649-4a98-820b-8606cf644078`；同时约束 UID、路径和 Helper SHA-256 |
| OpenClaw 严格 Entry | PASS | 当前 Entry `68d29b15-a9d4-4177-a623-33ad763f3f9b`；同时约束 UID、可执行路径、Docker image config digest 和 label；关闭 X.509-SVID prefetch |
| 旧同身份旁路清理 | PASS | 删除旧临时 Entry `98f869c4-d430-4c50-ae1b-1a2c00f84e32`，未保留同身份宽泛授权 |
| Broker/凭据交付 | PASS | 独立 Workload API、Broker socket 和短租约凭据目录；最终 `ready=true` |
| OpenClaw Gateway | PASS | 容器 `1335ee9c27c09e1daffa841f3ff9fee3f9957ef7592062c742d3fdad77418a24`；Gateway PID `20306`；RestartCount `0` |
| OpenClaw → IP2 mTLS | PASS | 客户端和服务端 SPIFFE URI 均严格核对；真实归档资源 GET HTTP 200 |
| 真实 Gateway 写入与插件读回 | PASS | 随机 marker 经真实 OpenClaw Gateway 写入，并由原生 SPIFFE transport 从 OpenViking session 读回 |
| commit/archive | PASS/FAIL | session `7e3b...` 的 task `163b...` completed；session `25aa...` 的 task `4f8...` 最终因 `Request timed out` failed |
| 新会话长期记忆召回 | FAIL | mTLS 搜索请求 HTTP 200，但只返回/注入顶层记忆摘要；随机事实未进入长期记忆，模型返回 `UNKNOWN` |
| 随机不存在项目负例 | PASS | 独立新会话返回精确 `UNKNOWN` |
| 360 秒双方 SVID 轮换 | PASS | 1498 个连续样本；观察到 6 组 client/server serial 组合；readiness 与同一归档业务读取全程成功 |
| Helper SIGSTOP/SIGCONT | PASS | 2.241 秒观察到 fail-closed；恢复后 0.169 秒成功 |
| Helper stop/start | PASS | 0.180 秒观察到 fail-closed；恢复后 0.451 秒成功 |
| Helper SIGKILL | PASS | 临时 `Restart=no` 下 unit 进入 failed；0.094 秒失败，手动恢复后 0.409 秒成功；drop-in 已撤销 |
| Broker/Agent SIGSTOP/SIGCONT | PASS | 4.341 秒观察到 fail-closed，低于 12 秒边界；恢复后 0.267 秒成功 |
| 专用业务 TCP 隧道 stop/start | PASS | 0.024 秒失败；恢复后 0.692 秒成功；未停止 IP2 服务 |
| Gateway PID 替换 | PASS | 保留旧 PID 登记时新实例无法使用身份；清理后登记实际 PID `20306`，业务 GET 恢复 200 |
| 错误客户端 SPIFFE ID | PASS | 同一 SPIRE trust domain 的有效短期错误身份 `spiffe://argus.local/agent/openclaw-negative` 得到 NGINX HTTP 403；临时 Entry 和 SVID 已删除 |
| OpenClaw Entry 删除传播 | PASS | 删除后约 0.910 秒凭据清理并 fail-closed；恢复严格 Entry 后约 6.187 秒重新 ready |
| IP2 OutOfDate workload PoC 边界 | PASS | 未修改 IP2 policy、Agent 数据、CA 或工作负载容器；IP2 五个实际工作负载 unit 均 active |
| BLOCKED 项 | 无 | 当前问题不是权限或材料阻塞，而是 OpenViking 归档提取/召回的真实功能失败 |

## 2. 源码、构建和交付产物

### 2.1 Git

- 指定基线提交：
  `b57f5210a82a29c3ebcf66a727b9ee13274df132`
- 隔离 worktree：
  `/home/ying_liu/openclaw-ip1-b57f5210`
- 实际部署代码提交：
  `84a735da834f5c2396e34e5efc452635c90283b2`
- 本地修复：
  `verify_openclaw_plugin_e2e.sh` 只对幂等 GET 的 `ECONNRESET`、`EPIPE`、`UND_ERR_SOCKET` 和 `socket hang up` 做一次有限重试；POST commit 不重试。
- 主工作树和原部署均未 reset 或覆盖。

### 2.2 哈希

| 产物 | SHA-256 |
| --- | --- |
| 最终交付包 | `c0aee42683850fd355d945f516e9be1bce74217b194c9626fc81e590d0c32bd7` |
| `spiffe-client-credentials` | `9480830cb8710fcb7f35e9484a1a00fd21b305940d2a1eb207b2248c5b8191f2` |
| 固定 OpenViking 插件 | `48e6d6385f81c83e6d97164ce86399f0a1e872b0c1aa9005276e7f808d5b18ce` |
| 上游插件 tgz | `461f566a1c07ed1dd56d705c18ecb4a940287421c6a4aff2568687ba5a63c539` |
| Guest OpenClaw image config | `sha256:71d3a51dca069dee52bf8c00c1794c5a99a8410bcc87bd457a7be308795833a0` |
| Guest OpenClaw manifest | `sha256:04d0690f18845526f46a138b6bc58fed2d66acdae1f6e9863bdb7766084ec62f` |

OpenClaw 为 `2026.7.1`，Node 为 `24.16.0`，SPIRE Server/Agent 为 `1.15.3`。插件固定为 `@openviking/openclaw-plugin@2026.6.18`、Argus revision `argus.2`。

## 3. TDVM、节点准入和身份

### 3.1 Guest

- VM：`argus-openclaw-stage2-b57f5210`
- QEMU PID：`2777274`
- 资源：8 vCPU / 8 GiB
- Guest：Ubuntu 24.10，kernel `6.11.0-26-generic`
- Docker：27.5.1
- Docker Compose：2.33.0
- SPIRE Agent：1.15.3
- Workload API：`/run/spire/openclaw/agent.sock`
- Broker：`/run/spire/openclaw-broker/broker.sock`

### 3.2 x509pop

- Node 证书 CN：`argus-openclaw-ip1-tdvm-stage2`
- Agent ID：
  `spiffe://argus.local/spire/agent/x509pop/d3c786bb67d4d88e82ddfaf681db4057f9aa4c9a`
- Server request ID：
  `73e15912-c9de-4eca-b230-22c99e22c58e`
- Node attestation：attempts=1，约 19 ms
- 最终 Server 记录：Agent 1.15.3、`Can re-attest=true`

x509pop 证明节点证书链和持钥，不证明 Guest 的 TDX 测量或 Trustee appraisal。

## 4. Registration Entries

### 4.1 Helper

- Entry ID：`25b693b9-7649-4a98-820b-8606cf644078`
- SPIFFE ID：`spiffe://argus.local/infra/openclaw-helper`
- Parent：本轮 x509pop Agent ID
- Selectors：
  - `unix:uid:0`
  - `unix:path:/opt/argus-workload/bin/spiffe-client-credentials`
  - `unix:sha256:9480830cb8710fcb7f35e9484a1a00fd21b305940d2a1eb207b2248c5b8191f2`

### 4.2 OpenClaw

- 当前 Entry ID：`68d29b15-a9d4-4177-a623-33ad763f3f9b`
- 原验收 Entry ID：`94cd6178-c7d5-426d-a688-21f50ddef44b`
- 变更原因：原 Entry 在传播测试中被有意删除，随后以完全相同的严格参数恢复，SPIRE 分配了新 ID。
- SPIFFE ID：`spiffe://argus.local/agent/openclaw`
- Parent：本轮 x509pop Agent ID
- Selectors：
  - `unix:uid:1000`
  - `unix:path:/usr/local/bin/node`
  - `docker:image_config_digest:sha256:71d3a51dca069dee52bf8c00c1794c5a99a8410bcc87bd457a7be308795833a0`
  - `docker:label:org.argus.workload:openclaw`
- `DisableX509SvidPrefetch=true`

## 5. 网络和 mTLS

实际路径：

```text
OpenClaw container
  -> Guest 10.0.2.2:1944
  -> IP1 127.0.0.1:1944
  -> 专用 SSH TCP tunnel
  -> IP2 172.31.28.53:1943
  -> IP2 NGINX/AuthZ
  -> OpenViking
```

TLS 始终终止于 IP2 NGINX。客户端按 SPIRE bundle 验证证书链，并精确核对服务端 URI：

`spiffe://argus.local/service/openviking-cmem`

IP2 AuthZ 只允许：

`spiffe://argus.local/agent/openclaw`

最终正向样本：

- request ID：`f018e76d-34b0-4b12-933f-7cb26e303ca4`
- path：`/api/v1/sessions/7e3b3bfe-ce2a-46f0-9f2b-b341ea588668/context`
- HTTP：200
- client serial：`E05D832E84DD78C0342A7971B1CD1EE6`
- server serial：`1D5A64198549E3800C9BFB4933E28632`

后续 task 查询又观察到新的 client serial `A62E857A939E6708A3CD849F02FEAE4D`，说明短期 SVID 继续轮换。

## 6. 真实业务结果

### 6.1 已完成的 session

- session ID：`7e3b3bfe-ce2a-46f0-9f2b-b341ea588668`
- task ID：`163b22d8-dc1b-4789-b0ab-ec4bb4501b2b`
- task status：`completed`
- archive：
  `viking://user/openclaw-ip1/sessions/7e3b3bfe-ce2a-46f0-9f2b-b341ea588668/history/archive_001`
- 完成时间：2026-09-09T07:32:01.689306Z
- `memories_extracted={}`
- `session_skills_extracted=0`

该 session 的随机 marker 在归档 context 中可稳定读回，证明 Gateway 写入、OpenViking session、commit 和 archive 路径真实工作；但空 `memories_extracted` 解释了长期记忆召回失败。

### 6.2 超时失败的 session

- session ID：`25aa85a0-9eba-4c75-88ec-875b8eec49fe`
- task ID：`4f8f60f7-b0b0-44e3-b365-50f549497488`
- 最终 status：`failed`
- error：`Request timed out.`
- 失败时间：2026-09-09T08:00:34.975225Z

### 6.3 新会话召回与 UNKNOWN

真实新会话执行了以下流程：

1. 只提供项目 marker，不在问题中泄露随机事实。
2. OpenViking `/api/v1/search/find` 经双方 mTLS 返回 HTTP 200。
3. 插件记录 `injecting 1 memories`，但唯一注入项是：
   `viking://user/openclaw-ip1/memories/.abstract.md`
4. 注入内容只是“用户长期记忆存储”的通用摘要，不含随机事实。
5. 召回回答为 `UNKNOWN`，因此正向召回 FAIL。
6. 独立随机不存在项目也返回精确 `UNKNOWN`，负例 PASS。

这不是网络、mTLS、AuthZ、API key 或模型调用失败，而是归档没有生成可召回的长期事实。

## 7. 轮换和故障恢复

| 场景 | fail-closed | 恢复 | 结果 |
| --- | ---: | ---: | --- |
| 360 秒正常轮换 | 无业务失败 | 不适用 | PASS，1498 samples、6 serial pairs |
| Helper SIGSTOP | 2.241 s | 0.169 s | PASS |
| Helper stop | 0.180 s | 0.451 s | PASS |
| Helper SIGKILL | 0.094 s | 0.409 s | PASS |
| Agent/Broker SIGSTOP | 4.341 s | 0.267 s | PASS |
| 专用 TCP tunnel stop | 0.024 s | 0.692 s | PASS |
| Gateway PID 替换 | 旧登记下探测失败 | 新 PID 登记后 HTTP 200 | PASS |
| OpenClaw Entry 删除 | 约 0.910 s | 约 6.187 s | PASS |

Helper SIGKILL 前仅对本轮 Guest unit 临时设置 `Restart=no`；验证后已删除 drop-in、daemon-reload，并恢复 `Restart=on-failure`。网络故障只停止本轮 `argus-openclaw-ip2-tunnel.service`，没有停止 IP1 SPIRE/Trustee 或 IP2 服务。

## 8. 最终服务状态

### 8.1 IP1 Host

| Unit | PID | 状态 | NRestarts |
| --- | ---: | --- | ---: |
| `argus-spire-server` | 2840260 | active/running | 0 |
| `argus-trustee` | 1015247 | active/running | 0 |
| `argus-trustee-workload-gateway` | 3194801 | active/running | 0 |
| `argus-openclaw-ip2-tunnel` | 3151282 | active/running | 0 |

### 8.2 OpenClaw Guest

| 组件 | PID | 状态 |
| --- | ---: | --- |
| `argus-openclaw-agent` | 5336 | active/running |
| `argus-openclaw-credentials` | 22257（状态快照时） | active/running、ready=true |
| Docker | 2471 | active/running |
| local registry | 4631 | active/running |
| Gateway init | 20272 | running |
| `openclaw-gateway` | 20306 | running |

凭据交付 unit 的 `NRestarts=9` 包含本轮有意的 Entry 删除、旧 Gateway PID 和 Broker 故障测试，不代表当前持续崩溃。

### 8.3 IP2

| Unit/容器 | PID | 状态 |
| --- | ---: | --- |
| `argus-authz` | 1516476 | active/running |
| `argus-helper` | 1518981 | active/running |
| `argus-nginx` | 1519024 | active/running |
| `argus-tdx-provider` | 1516477 | active/running |
| `argus-workload-agent` | 1516482 | active/running |
| OpenViking container `680667b2...1cdc` | 1390532 | running，RestartCount=0 |

IP2 OpenViking 实际 image config：

`sha256:60b69b7bcabd83c718cc8635a5a0055e55c5131bd20ccfc214a95952100d14a8`

## 9. 关键命令

以下命令均为实际执行路径的脱敏形式；API key、Gateway token、私钥和节点证明材料未写入报告。

```bash
# IP1 创建/审计 Entries
sudo python3 adapters/OpenClaw/spiffe_client/deploy.py \
  apply-entries --config /root/argus-ip1-openclaw/.../artifacts/openclaw-deployment.json

# Guest 登记实际 Gateway PID
sudo python3 /root/argus-stage2-84a735da/agent-cc/adapters/OpenClaw/spiffe_client/deploy.py \
  guest-register --pid 20306

# Guest 状态
sudo python3 /root/argus-stage2-84a735da/agent-cc/adapters/OpenClaw/spiffe_client/deploy.py \
  guest-status

# 360 秒轮换
sudo bash /root/argus-stage2-84a735da/agent-cc/adapters/OpenClaw/scripts/observe_openclaw_lifecycle.sh \
  '/api/v1/sessions/7e3b3bfe-ce2a-46f0-9f2b-b341ea588668/context?token_budget=128000' \
  360 '<saved-marker>'

# Server 当前 Entries
sudo /opt/spire-1.15.3/bin/spire-server entry show \
  -socketPath /run/spire/server/private/api.sock \
  -parentID 'spiffe://argus.local/spire/agent/x509pop/d3c786bb67d4d88e82ddfaf681db4057f9aa4c9a'
```

## 10. 证据路径

主证据目录：

`/root/argus-ip1-openclaw/20260909T144253+0800-ip1-openclaw-client/`

关键证据：

| 内容 | 路径 |
| --- | --- |
| Host 最终状态、Agent、Entries | `evidence/final-host-status.txt` |
| Guest 最终状态、PID、当前 SVID | `evidence/final-guest-status.txt` |
| IP2 正确 unit 状态 | `evidence/final-ip2-status-corrected.txt` |
| 最终正向 mTLS receipt | `evidence/final-positive-receipt.txt` |
| 两个归档 task 的最终完整结果 | `evidence/archive-task-status-final.jsonl` |
| 错误 SPIFFE ID HTTP 403 | `evidence/openclaw-guest/wrong-identity-http.txt` |
| Entry 删除传播 | `evidence/openclaw-entry-delete.txt` |
| Entry 严格恢复 | `evidence/openclaw-entry-restore.txt` |
| 网络隧道故障状态 | `evidence/network-tunnel-fault.txt` |
| 轮换 trace | `evidence/openclaw-guest/faults/rotation.jsonl` |
| Helper/Broker/网络/Gateway/Entry 故障证据 | `evidence/openclaw-guest/faults/argus-openclaw-faults/` |
| 新会话召回失败与 UNKNOWN 负例 | `evidence/openclaw-guest/faults/argus-openclaw-evidence/20260909T083100Z-recall-existing-archive/` |
| 已完成归档 session 原始证据 | Guest `/root/argus-openclaw-evidence/20260909T073300Z-ip1-openclaw-stage2/` |

证据目录包含随机验收事实和测试对话，权限保持 root-only；不包含 API key、Gateway token、私钥或 Quote 原文。

## 11. 恢复信息

- SPIRE Server 修改前配置：
  `/root/argus-ip1-openclaw/20260909T144253+0800-ip1-openclaw-client/artifacts/server.conf.before-x509pop`
- 本轮专用 Host unit：
  `/etc/systemd/system/argus-openclaw-ip2-tunnel.service`
- 本轮 x509pop CA：
  `/etc/argus-openclaw/x509pop-ca.pem`
- Guest 节点和客户端配置：
  `/etc/argus-openclaw/`
- Guest 最终交付目录：
  `/root/argus-stage2-84a735da`

当前服务已恢复为正常运行状态，因此未执行回滚。若要撤销本轮，应先停止本轮 Guest 和专用 tunnel，再仅删除当前两个 OpenClaw Entries、x509pop 节点记录和 Server 的 x509pop 增量配置；不得删除 SPIRE Server data dir、CA、IP2 `argus_tdx` Agent 或 IP2 OutOfDate PoC policy。

## 12. 最终判定

本轮已经实际完成 OpenClaw 客户端 TDVM、x509pop 节点准入、严格 workload selectors、Broker 凭据交付、双方 SPIFFE mTLS、真实 Gateway 写入/读回、至少一次归档、双端 SVID 轮换以及全部指定客户端侧故障恢复。

完整联合验收仍为 **FAIL**，不是 BLOCKED：OpenViking completed archive 没有提取长期 memory，导致新会话正向召回失败。修复目标应限定为 IP2/OpenViking 的 session commit memory extraction 或插件的叶级 memory 检索；不得通过放宽 mTLS、改写预期答案、把旧 session context 当作长期召回，或把负例 `UNKNOWN` 当作正向成功来绕过。
