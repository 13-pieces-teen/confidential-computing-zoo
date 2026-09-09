# Argus OpenViking Workload Attestation 状态报告

## 1. 结论

- **PoC 联合验收：PASS**
- **生产级严格 TCB 验收：BLOCKED**
- **报告时点：2026-09-09 10:50 +08:00**
- **证据 Run ID：`20260907T125754+0800-ip1-workload-attestation`**

PoC 使用独立策略 `argus-workload-openviking-v1-poc-ignore-tcb`。该策略仅移除了
workload policy 对 `input.tdx.tcb_status == "UpToDate"` 的要求；真实 DCAP、
collateral 有效性、非 debug、TEE/vendor、MR_TD、RTMR0/1/2、镜像配置摘要、
业务配置摘要、可执行文件、runtime-data schema 和 Quote REPORTDATA 绑定仍然启用。

生产级验收不能标记为通过。平台实际 TCB 状态为 `OutOfDate`，原严格策略
`argus-workload-openviking-v1` 仍要求 `UpToDate`，且未被覆盖或放宽。

## 2. 当前部署分工

| 主机 | 当前职责 |
|---|---|
| IP1 `10.112.120.22` | SPIRE Server、Server 侧 `argus_tdx` NodeAttestor、Trustee/DCAP、Node 与 workload policy、SPIRE Registration Entries、受限 Trustee 网关、IP2 控制隧道 |
| IP2 TDX TDVM `172.31.28.53` | Evidence Provider、SPIRE Workload Agent、WorkloadAttestor、SPIFFE Helper、NGINX/AuthZ、OpenViking 容器 |
| IP2 管理入口 | `115.190.62.46` |

IP2 的独立 Node Agent 当前为 inactive，这是 Workload Agent 接管同一 SPIRE data
目录后的预期状态，不代表 Node 身份失效。

## 3. 代码、构建和运行二进制

| 项目 | 当前值 | 状态 |
|---|---|---|
| 分支 | `feat/argus-spiffe-v2-val` | PASS |
| IP1/IP2 Git SHA | `75509ed083d765ca68eaa636911c9651db5e32e2` | PASS，双端一致且工作树干净 |
| 相对上游 | ahead 3 | 信息项，未推送上游 |
| SPIRE Server/Agent | `1.15.3` | PASS |
| SPIFFE Helper 版本 | `0.11.0-argus.1` | PASS |
| Helper SHA-256 | `34f11f2c4b006e0177669a6427500b08c98f3904731ec3123f065d03a41f8492` | PASS，IP1、IP2、构建包一致 |
| 统一构建包 | `argus-workload-build-75509ed-linux-amd64.tar.gz` | PASS |
| 构建包 SHA-256 | `f3254eecd7767923fd4e6877611550774143937810c873f1bf424b168e83e7fa` | PASS，IP2 回读一致 |

提交 `75509ed0` 包含以下运行稳定性修复：

1. 删除 SPIFFE Helper 对本地 Unix Broker 连接的激进 10 秒 gRPC keepalive，避免
   SPIRE 返回 `ENHANCE_YOUR_CALM: too_many_pings`。
2. NGINX access log 从 `/dev/stdout` 改为
   `/run/argus-nginx/access.log`，解决 systemd/nsenter 环境中的 `ENXIO`。
3. `workload.py verify` 将 RFC3339 UTC 时间转换为 `journalctl --since @<epoch>`，
   适配 IP2 当前 systemd/journalctl。

## 4. IP1 控制面当前状态

| 服务/检查 | 当前状态 |
|---|---|
| `argus-spire-server.service` | active |
| `argus-trustee.service` | active |
| `argus-trustee-workload-gateway.service` | active |
| `argus-ip2-control-tunnel.service` | active |
| SPIRE Server 版本 | 1.15.3 |
| SPIRE Server socket | `/run/spire/server/private/api.sock` |
| `workload.py server-check` | PASS |

当前 Server Entry：

| 身份 | Entry ID | 核心约束 |
|---|---|---|
| `spiffe://argus.local/infra/openviking-helper` | `4694977c-66a0-4998-8617-c094ce7f88a7` | UID 0、实际路径、Helper SHA-256 |
| `spiffe://argus.local/service/openviking-cmem` | `f9275701-5734-4134-8f65-1113d2e3f375` | verified、workload、PoC policy、Agent ID、image/config digest；X.509-SVID prefetch disabled |

## 5. IP2 工作负载侧当前状态

2026-09-09 10:50 +08:00 实时采集结果：

| 服务/对象 | 当前状态 |
|---|---|
| `argus-helper.service` | active |
| `argus-nginx.service` | active |
| `argus-authz.service` | active |
| `argus-workload-agent.service` | active |
| `argus-tdx-provider.service` | active |
| `argus-node-agent.service` | inactive，符合接管设计 |
| Workload ready | `true` |
| 当前 target SVID serial | `191810952943123307712179697806699028813` |
| OpenViking 容器 | running |
| Container ID | `680667b205349bd19c6d689afb6099c9ea93a95d98446ea508a2e8c2764c1cdc` |
| 容器 PID | `1390532` |
| RestartCount | `0` |
| TC API/TruCon 事件生产 | 冻结；未发现独立运行的事件生产进程 |

## 6. 访问与信任合同

### 6.1 SPIRE

- IP2 可达地址：`127.0.0.1:18084`
- 作用域：IP2 loopback，经持久反向隧道到 IP1 SPIRE Server
- IP2 Workload API：`/run/spire/argus-poc/agent.sock`
- 当前 bootstrap bundle：
  `/etc/spire/argus-poc/bootstrap.crt`
- Bundle SHA-256：
  `a1d4dd7ad26747e0904dc01b3c477ffced01555c693aef956a612b4bbd90e876`

### 6.2 Trustee

- IP2 可达 origin：`https://127.0.0.1:18443`
- TLS server name：`trustee.argus.local`
- 允许路由：
  - `POST /attestation`
  - `GET /policy/<safe-id>_cpu`
- policy write/list 及其他管理路径不通过该入口开放。
- Trustee TLS CA 文件 SHA-256：
  `4bb756cc19e4306259b38b4ab5237c92464fd4587ca45946f77b5612f3e332ed`
- Trustee TLS CA 证书 SHA-256：
  `65:0A:6C:35:B5:C8:90:AE:84:05:01:97:13:D0:29:90:10:E4:8A:E1:E4:CA:CD:C2:2C:3C:08:82:9F:62:4E:95`

### 6.3 EAR

- EAR P-256 公钥文件 SHA-256：
  `585a3f6f602acfba96bdecfb7305da8eb781fc3844a0f2a510dc5fdd72b1ac31`
- EAR 公钥 SPKI SHA-256：
  `baa6725bfdccca194c0fcebecc61295133138736dd98dd16367a30cecc86452c`
- issuer：`https://trustee.argus.local`
- profile：
  `tag:github.com,2024:confidential-containers/Trustee`

### 6.4 远程 server-check

- SSH alias：`argus-ip1-control`
- 脚本：`/opt/argus-workload/scripts/workload.py`
- 配置：`/etc/argus-workload/environment.json`
- 条件：`BatchMode=yes`、`StrictHostKeyChecking=yes`、root forced-command key
- 当前结果：PASS

## 7. Node Attestation

### 7.1 当前身份

- Agent SPIFFE ID：
  `spiffe://argus.local/spire/agent/argus_tdx/openviking-node`
- Attestation type：`argus_tdx`
- Agent version：`1.15.3`
- Can re-attest：`true`
- 2026-09-09 10:50 +08:00 观察到的 Agent SVID 到期时间：
  `2026-09-09 11:26:55 +08:00`
- 当前服务应自动轮换；上述时间是采样时刻的证书状态，不是 policy 截止时间。

### 7.2 当前 Node policy

- Policy ID：`argus-node-poc-d9a7029b57fe`
- 存储名：`argus-node-poc-d9a7029b57fe_cpu`
- SHA-256：
  `9aa127f964593ab0aec1b32ea9b52f825df250c3f0ad5bb792b0c002804a0feb`
- 有效窗口：
  `2026-09-07T07:00:00Z` 至 `2026-09-10T07:00:00Z`
- 相对上一版的安全相关变化：仅将 RTMR2 更新为经批准的冻结当前值；
  TCB、advisory、debug、vendor、TEE、MR_TD、RTMR0/1 等约束保留。

### 7.3 最近一次真实 Node 重新证明

- 时间：`2026-09-09T09:58:34+08:00`
- Server request：
  `3e57d103-ad35-420f-bfc8-5c498dbe3da4`
- Trustee request：
  `482181f5-5c24-438a-9fc4-5a608673efd4`
- 结果：真实 Quote DCAP、MRCONFIGID、endorsement 和 policy evaluation 均成功。

## 8. 冻结 workload 基线

| 字段 | 批准值 |
|---|---|
| launch ID | `launch-a3b85ae` |
| container ID | `680667b205349bd19c6d689afb6099c9ea93a95d98446ea508a2e8c2764c1cdc` |
| MR_TD | `81a3ac2d05c448a517c975fc2ffc1a83f2f99eb19176927329ae15848df00650135236cab85cece2ced656b07293f34f` |
| RTMR0 | `354e1a21735feea391c0f8c080d6ca24f6389ae4e66b6ae77281fee60001589cf9be4abf2c826e6fc3c024e8a78361af` |
| RTMR1 | `1798985188de9fc839e3954f0bb1f49b2f5d1e86d4e6eef17609f35713b18ae9ec08b0423cb0793929ec1bfb55f9ae64` |
| RTMR2 | `b344c608c0eedd894990a076ead11aca34c6c0aad97964fe39a6eb0c05520a18c5f2f111de372af140ff5963670cadc7` |
| image config digest | `sha256:60b69b7bcabd83c718cc8635a5a0055e55c5131bd20ccfc214a95952100d14a8` |
| config digest | `sha256:15b0ce483cad9f42a5ec3e08d8c623956a340d1b5989f25b65352868f9a28bac` |
| executable | `/usr/local/bin/python3.13` |

批准继续使用当前 RTMR2 的依据：

- 旧 RTMR2：
  `50a599711d0c8f4a30023011f0a7232076d1e9bcb05f5c2245c1d725b56ffb9f49ed3384390b89e6fbd46b9c7c26c6b5`
- 事件 digest：
  `e1632940efee22fc9b6ddc17da50440de88483dd2b8b2f768b8ce6855a11908221e0aad530d3a265da78d47971d7eb31`
- 独立复算：
  `SHA384(old_rtmr2 || event_digest) == approved_rtmr2`
- Rekor logIndex：`2755707897`
- Rekor UUID：
  `108e9186e8c5677a1b9fe09ba33e273273991ad6a89f763cc0098f4fec0c82c4daa167a52caaee8d`

2026-09-09 10:50 +08:00 对 IP2 queue DB 的实时只读副本检查：

- record：`9f1db07b-587a-4c7c-a0f0-31285510a198`
- sequence：`2`
- event：`evt-c1596fb7`
- status：`CONFIRMED`
- `rtmr_extended=1`
- `mr_value=b344c608...670cadc7`

冻结条件仍然有效：任何新的 TC API/TruCon commit、queue sequence 变化、
RTMR 变化、容器替换或 runtime pin 变化都会使当前批准失效，必须暂停并重新审批。

## 9. Workload policy

| Policy | SHA-256 | 当前用途 |
|---|---|---|
| `argus-workload-openviking-v1_cpu` | `874a8323592378004f4f81166b2ebcc0143ec3f5f1081938a582fca88bf05060` | 生产严格策略；保留，当前因 TCB OutOfDate 阻塞 |
| `argus-workload-openviking-v1-poc-ignore-tcb_cpu` | `d3227a8d473b11e831e2d28d42b79f8fb943fa654ac2e3714bd3f0edf5505a4e` | 当前 PoC 策略 |

PoC policy 与严格 policy 的唯一预期差异是删除：

```rego
input.tdx.tcb_status == "UpToDate"
```

以下约束没有被删除：

- 真实 TDX Quote/DCAP 验证；
- `collateral_expiration_status == "0"`；
- `debug == false`；
- TEE/vendor；
- MR_TD 和 RTMR0/1/2；
- image config digest；
- `ov.conf` digest；
- 实际 executable；
- runtime-data schema；
- nonce、runtime-data 与 Quote REPORTDATA 绑定。

Trustee 本地存储、IP2 render 和受限 GET 回读的 PoC policy 内容一致。

## 10. 联合验收结果

| 验收项 | 结果 | 说明 |
|---|---|---|
| Node 真实 TDX 加入 | PASS | Server 与 Trustee 同次请求可关联，DCAP 等检查成功 |
| 严格 workload policy | BLOCKED | EAR `contraindicated`，唯一阻塞为平台 TCB `OutOfDate` |
| PoC workload EAR | PASS | fresh Quote 对 PoC policy appraisal accepted |
| OpenViking target SVID | PASS | 由 SPIRE Server CA 签发 |
| 正向业务 mTLS | PASS | `openclaw` 客户端到 `openviking-cmem`，HTTP 200 |
| 错误身份 | PASS（正确拒绝） | 同一信任域、不同 SPIFFE ID，AuthZ HTTP 403 |
| 篡改 config digest | PASS（正确拒绝） | Trustee HTTP 500，无 EAR |
| 跨 nonce Quote/runtime-data 配对 | PASS（正确拒绝） | Trustee HTTP 500，无 EAR |
| 普通 SVID 轮换 | PASS | 服务持续 ready，serial 变化且无需新 workload appraisal |
| Helper 重连稳定性 | PASS | 去除激进 keepalive 后未再出现 `too_many_pings` |
| `helper-crash` 破坏性测试 | NOT_RUN | 核心 PoC 已通过，未主动破坏当前演示服务 |
| `target-exit` 破坏性测试 | NOT_RUN | 会终止当前唯一 OpenViking 容器；冻结条件下不能安全重新 launch |

严格 policy 失败时签名 EAR 的关键状态：

- overall status：`contraindicated`
- `hardware=97`
- `executables=3`
- `configuration=2`
- `tcb_status=OutOfDate`
- `tcb_date=2025-05-14T00:00:00Z`
- `tcb_eval_num=22`
- `collateral_expiration_status=0`
- `debug=false`

这证明失败点是 TCB 状态，不是 Quote、collateral、measurement 或 runtime-data
绑定失败。

## 11. 临时验收客户端

| 用途 | SPIFFE ID | Entry ID | Entry 到期 |
|---|---|---|---|
| 正向 | `spiffe://argus.local/agent/openclaw` | `98f869c4-d430-4c50-ae1b-1a2c00f84e32` | `2026-09-10 10:18:24 +08:00` |
| 错误身份 | `spiffe://argus.local/agent/not-openclaw` | `84acdc37-0e8b-41db-b12b-086173e5d7f9` | `2026-09-10 10:18:24 +08:00` |

两个 Entry 均绑定 UID 0、独立 Helper 路径和 SHA-256
`6417ae89d3ddc7d4eef445902e22bfccc284fe64b00b08ea77d655aa5d196a6c`，
不是宽泛授权。

当前两份客户端证书的 `notAfter` 均为
`2026-09-09 11:18:27 +08:00`。私钥仅保存在 IP2 root 权限目录，未写入报告或证据
文本。证书过期后如需继续演示，必须通过合法 SPIRE Entry 重新取证，不能使用自签材料。

## 12. 当前风险与阻塞

1. **生产 TCB 阻塞**：平台为 `OutOfDate`。必须完成平台/固件/TCB 更新并取得
   `UpToDate` EAR，才能切回严格 workload policy 并声明生产验收通过。
2. **冻结 RTMR2 时序约束**：TruCon 每次 commit 都会 extend RTMR2。当前 PoC
   运行期间必须继续冻结 TC API/TruCon 事件生产。
3. **Node policy 有效期**：当前窗口在 `2026-09-10T07:00:00Z`
   （`2026-09-10 15:00:00 +08:00`）结束；之后重新证明需要经批准的新窗口。
4. **临时客户端材料**：当前证书在 `2026-09-09 11:18:27 +08:00` 到期；
   Entry 次日自动过期。结束演示后应删除两个临时 Entry 和两组私钥。
5. **代码尚未推送上游**：双端本地提交一致，但分支相对上游 ahead 3。

## 13. 恢复与回退

- IP1 备份目录：
  `/var/backups/argus-workload/20260907T125754+0800-ip1-workload-attestation`
- 原严格 workload policy 仍在 Trustee 中，没有被 PoC policy 覆盖。
- 原 Helper 二进制已分别在 IP1/IP2 证据目录备份。
- SPIRE CA、Server data dir、Node proof key 和现有信任材料未重新初始化。
- 未执行 `reset --hard`，未清除用户已有工作。

生产切换建议顺序：

1. 升级平台使真实 EAR 返回 `tcb_status=UpToDate`。
2. 重新采集并审批当前 MR/RTMR、镜像、配置和 executable。
3. 停止事件生产并固定 RTMR2。
4. 将环境切回 `argus-workload-openviking-v1`。
5. 重建严格 OpenViking Entry。
6. 重新执行真实正向、错误身份、篡改和跨 nonce 验收。

## 14. 常用复核命令

IP1：

```bash
sudo python3 /opt/argus-workload/scripts/workload.py server-check
sudo systemctl is-active \
  argus-spire-server \
  argus-trustee \
  argus-trustee-workload-gateway \
  argus-ip2-control-tunnel
sudo sha256sum /opt/argus-workload/bin/spiffe-helper
```

IP2：

```bash
sudo python3 /opt/argus-workload/scripts/workload.py status
sudo python3 /opt/argus-workload/scripts/workload.py verify
sudo sha256sum /opt/argus-workload/bin/spiffe-helper
```

## 15. 证据位置

主证据目录：

```text
/root/.copilot/session-state/ada7d94c-d335-4314-8cbf-0bf403447a00/files/
  20260907T125754+0800-ip1-workload-attestation/
```

关键文件：

- `FINAL_STATUS.txt`
- `IP1_HANDOFF.txt`
- `FROZEN_WORKLOAD_BASELINE_APPROVAL.txt`
- `POC_TCB_EXCEPTION_APPROVAL.txt`
- `workload-ear-diagnostic-claims.json`
- `poc-workload-policy-install-result.txt`
- `ip2-poc-final-positive-and-wrong-client.txt`
- `ip2-poc-lifecycle-rotation-after-helper-fix.json`
- `ip2-poc-binding-negative-results.txt`
- `ip2-verify-helper-75509ed.txt`
- `final-report-ip1-current-state.txt`
- `final-report-ip2-current-state.txt`
- `final-report-control-contract.txt`
- `final-report-ip2-queue-current.txt`
- `ip2-package-75509ed-verification.txt`

SVID 私钥、token 和原始 Quote 未写入本报告。
