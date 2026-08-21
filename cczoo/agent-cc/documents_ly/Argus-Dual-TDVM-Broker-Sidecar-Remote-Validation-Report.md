# 双 TDVM + Broker Sidecar 远程验证报告

状态：远程验证完成。M3 ALLOW/DENY 与双 TDVM DENY/ALLOW 全部通过；ALLOW 含
wrong-client 负例、OpenViking 退出后 Sidecar 退出（pidfd）与 1943 关闭检查。
Application Readiness 未通过并如实记录（详见下文）。最终结论见文末。

## 构建信息

- 验证日期：2026-08-21（阶段验证）
- Git commit：`ea15713`（Record runtime image digest and readiness validation；上游分支 `feat/argus-spiffe-v2-val` fast-forward 同步至验证主机）
- Git 工作区状态：clean（`git status --short` 无输出）
- 验证主机：`cwf-bkc`（本地双 TDVM：QEMU slirp 127.0.0.1:2223/2222，center compose 本机运行）
- SPIRE Server / Agent：`1.15.2`
- 结论：Mock Evidence Provider + Mock Trustee 软件链路通过（见文末）

## 身份与运行对象

| 项目 | 远程实测值 |
|---|---|
| OpenClaw Parent ID | `spiffe://argus.local/spire/agent/argus_tdx/53a4db4804a094f581f46245c22f9d678769a8ef12b56addc3f37da527ead814` |
| OpenViking Parent ID | `spiffe://argus.local/spire/agent/argus_tdx/6619d9ab1a8ff1ddfdb216e8b938133d20ad757fa897876a959dc0b039443d9c` |
| `dual-openclaw-workload` selectors | `docker:label:argus.workload:openclaw`；`docker:image_id:argus-dual-openclaw:local`；`docker:image_config_digest:sha256:4c810e4f8366488e1479918c22af03c2c4a3182815ecb163e6c63f721eb60f5f` |
| `dual-openviking-broker` selectors | `docker:label:argus.component:openviking-broker`；`docker:image_id:argus-openviking-broker-sidecar:local`；`docker:image_config_digest:sha256:083fb46f736f5340c9ac2182d8a450decac7df217cd092653db3b6543780ac77` |
| `dual-openviking-target` selectors | `docker:label:argus.workload:openviking-cmem`；`docker:image_id:openviking-cmem:latest`；`docker:image_config_digest:sha256:71f9ba968fcb27f0068c65a53b4654deb3272eafe8a726c663fa598906d1c408`；`argus_tdx_workload:verified:true`；`argus_tdx_workload:workload_id:openviking-cmem`；`argus_tdx_workload:policy:openviking-cmem-v1`；`DisableX509SvidPrefetch: true` |
| OpenViking container ID | `f93de14f7d6cdfbd3b4a775db37c8d6c21d290c33fe5e46cf2f454e41289be44`（launch-e25535d，TC-API 启动） |
| OpenViking host PID | `100068` |
| Sidecar `-target-pid` | `100068`（实测相等） |
| `broker.sock` owner/mode | `0:1000 770`（脚本断言） |
| OpenViking source image config digest | `sha256:2b952bca11d0d6a09cb5dcf91ad0f1e08151c986a687abfddc24483d3e65e348`（`localhost:5000/openviking:v0.4.8`） |
| TC-API runtime image config digest | `sha256:71f9ba968fcb27f0068c65a53b4654deb3272eafe8a726c663fa598906d1c408`（`openviking-cmem:latest`，TC-API pull→load 产物） |
| 运行容器实际 image config digest | `sha256:71f9ba968fcb27f0068c65a53b4654deb3272eafe8a726c663fa598906d1c408`（与 runtime 一致） |
| `dual-openviking-target` 注册的 config digest | `sha256:71f9ba968fcb27f0068c65a53b4654deb3272eafe8a726c663fa598906d1c408`（以 `DUAL_OPENVIKING_IMAGE_CONFIG_DIGEST` 覆盖注册，见 digest 工程问题） |

## Non-intrusive 证据

| 检查 | 结果 / 证据 |
|---|---|
| OpenViking 无 Workload API mount | 通过。`docker inspect agentcc-openviking-service` mounts：`/etc/tdx-attest.conf`、`/app/.openviking`（state）、`/etc/sgx_default_qcnl.conf`、`/dev/tdx_guest`、`/td-attest`（TDX attest 示例）——无任何 `/opt/spire`、`/run/spire`、`/run/argus-svid` |
| OpenViking 无 Broker API mount | 通过。同上，mounts 中无 broker 目录 |
| OpenViking 无 X.509-SVID/private-key mount | 通过。同上，无 SVID/密钥挂载 |
| OpenViking 无直接获取 SPIFFE 身份的环境配置 | 通过。env 仅 `OPENVIKING_CONFIG_FILE=/app/.openviking/ov.conf`、`OPENVIKING_WITH_BOT=0`、`OPENVIKING_CLI_CONFIG_FILE=…`；无 `SPIFFE_ENDPOINT_SOCKET`/`ARGUS_SPIFFE_ENABLED`/`ARGUS_WORKLOAD_SPIFFE_ID` |
| Workload API、Broker API 与目标 SVID 只由 Broker Sidecar 使用 | 通过。Sidecar mounts 恰为：`/opt/spire/run/agent <- /run/argus-spire-dual/openviking`、`/opt/spire/run/broker <- /run/argus-spire-dual/openviking-broker`；Sidecar cmd `-workload-api=unix:///opt/spire/run/agent/agent.sock -broker-socket=/opt/spire/run/broker/broker.sock` |
| Sidecar `-target-pid` 等于 OpenViking 实际 host PID | 通过：`-target-pid=100068` == inspect `{{.State.Pid}}` = `100068` |
| 用户隔离 | Agent `0:0`（需要 Docker/pid 访问），Broker Sidecar `1000:1000`（非特权） |

Sidecar 完整启动参数（docker inspect `{{json .Config.Cmd}}`）：

```json
["-workload-api=unix:///opt/spire/run/agent/agent.sock",
 "-broker-socket=/opt/spire/run/broker/broker.sock",
 "-broker-spiffe-id=spiffe://argus.local/infra/openviking-broker",
 "-agent-spiffe-id=spiffe://argus.local/spire/agent/argus_tdx/6619d9ab1a8ff1ddfdb216e8b938133d20ad757fa897876a959dc0b039443d9c",
 "-target-spiffe-id=spiffe://argus.local/service/openviking-cmem",
 "-client-spiffe-id=spiffe://argus.local/agent/openclaw",
 "-target-pid=100068",
 "-listen=0.0.0.0:1943",
 "-upstream=http://127.0.0.1:1933"]
```

## M3 Broker 基线

| 检查 | 结果 / 证据 |
|---|---|
| ALLOW：收到目标 SVID 后才 ready | 通过（`/tmp/argus-m3-allow-fix.log`）：“M3 workload and Broker PID-reference matrix passed” |
| DENY：无目标 SVID、无 ready、未监听 21943 | 通过（`/tmp/argus-m3-deny-fix.log`）：“M3 Broker deny matrix passed: Mock Trustee recorded DENY; no target SVID was delivered, port 21943 stayed closed” |
| DENY：Sidecar 保持运行，处于无身份阻塞服务状态 | 通过。同上日志：“the Sidecar remains waiting without identity (PID 2243444)” |
| DENY：Trustee denied metric | 通过。同上日志（fake-services metrics 记录 `result="denied"`） |
| 目标 PID 退出后 Sidecar 退出 | 通过。同上日志（ALLOW 矩阵含 PID-reference/lifecycle） |

## 双 TDVM DENY

| 检查 | 结果 / 证据 |
|---|---|
| 两个 Agent 的 Parent ID 不同 | 通过。见“身份与运行对象”表两行 |
| TC-API 已启动 OpenViking | 通过。容器 `9fff3392d1e291cb468a7c0f8679c01f7d10aeede4f2d80742f57499b71d2a9c`（PID 91361） |
| Trustee decision / metric 为 DENY | 通过。`argus_m4_fake_requests_total{service="workload_trustee",result="denied"} 1` |
| 目标 Entry 要求 `verified/workload_id/policy` 强 selectors | 通过。`dual-openviking-target` 含三个 `argus_tdx_workload` selectors + runtime digest |
| WorkloadAttestor 未产生可匹配的 verified selectors | 通过。DENY 决策下 reference attestation 被拒 |
| Sidecar 无目标 SVID、无 ready、未监听 1943 | 通过。Sidecar 日志无 `listener is ready`；1943 无监听；`/tmp/argus-dual-deny-r1.log` |
| Sidecar 保持运行，处于无身份阻塞服务状态 | 通过。容器仍运行，Broker snapshot 为空：“new mTLS handshakes are blocked” |

对称链路结论：

```text
Trustee DENY
  -> 无可匹配的 verified selectors
  -> 强 Registration Entry 不匹配
  -> 无目标 SVID
  -> Sidecar 无 identity
  -> 1943 不监听
```

DENY 结论必须由本轮配置的 Mock Trustee decision 与 denied metric 共同确认；
Sidecar 的空身份状态本身不能区分永久 DENY、Entry 尚未同步或暂时不匹配。

## 双 TDVM ALLOW 与 mTLS

| 检查 | 结果 / 证据 |
|---|---|
| OpenViking 无 SPIRE mount / SVID | 通过。docker inspect 摘录见 Non-intrusive 证据表 |
| Sidecar PID 与 OpenViking PID 一致 | 通过：`-target-pid=100068` == `{{.State.Pid}}` = `100068` |
| Trustee decision / metric 为 ALLOW | 通过：`argus_m4_fake_requests_total{service="workload_trustee",result="ok"} 2` |
| verified selectors + runtime digest 命中强 Entry | 通过。Entry `dual-openviking-target` 的 config digest == 运行容器实际 digest == `sha256:71f9ba968fcb…`（精确选择器匹配） |
| Sidecar 获得目标 SVID 后监听 1943 | 通过。Sidecar 日志：`2026/08/21 03:20:02 OpenViking mTLS listener is ready for identity spiffe://argus.local/service/openviking-cmem` |
| Guard ALLOW 后 SPIFFE mTLS `/health=200` | 通过：`Guard ALLOW -> direct SPIFFE mTLS /health -> HTTP 200` |
| 无客户端证书访问失败 | 通过。Sidecar 日志：`tls: client didn't provide a certificate`（TLS 握手被拒） |
| 错误 expected-client ID 握手失败 | 通过：`Wrong-client SPIFFE ID rejected during mTLS handshake as expected: … ssl/tls alert bad certificate` |
| OpenClaw 无法访问明文 1933 | 通过。脚本断言 `http://10.0.2.2:1933/health` 不可达 |
| OpenViking 退出后 Sidecar 经 pidfd/lifecycle 退出 | 通过：`Target exit check: OpenViking stopped; Sidecar exited through pidfd monitoring`（Sidecar 日志：`2026/08/21 03:20:09 OpenViking target PID 100068 exited`） |
| Sidecar 退出后 1943 不再提供服务 | 通过。脚本断言 `port 1943 closed` |

对称链路结论：

```text
Trustee ALLOW
  -> verified selectors
  -> runtime digest + 强 Registration Entry 匹配
  -> 目标 SVID
  -> Sidecar 1943 ready
  -> OpenClaw Guard ALLOW
  -> SPIFFE mTLS /health = 200
```

## Application Readiness（非安全链路硬验收）

| 检查 | 结果 / 证据 |
|---|---|
| `/ready` HTTP 状态 | `503`（实测） |
| OpenViking readiness 响应 | `{"status":"not_ready","checks":{"agfs":{"status":"ok",…},"vectordb":"ok","api_key_manager":"ok","embedding":"error: provider=ollama model=bge-m3: OpenAI API error: Connection error.","ollama":"unreachable at 172.18.0.1:11434"}}` |
| 结论 | `Application Readiness: NOT READY - /ready HTTP 503; this profile does not deploy Ollama/bge-m3` |
| 原因 | dual-TDVM profile 未部署 Ollama/bge-m3 |

本轮验收不部署 Ollama。`/ready=503` 如实记录，不阻断 wrong-client、pidfd 与
1943 关闭检查（上述均通过）。

## Image digest 工程问题

- 本轮以 `DUAL_OPENVIKING_IMAGE_CONFIG_DIGEST=sha256:71f9ba968fcb…` 覆盖重注册
  `dual-openviking-target`，使 Entry digest 与运行时镜像一致（脚本原生支持的 override，非代码修改）。
- 报告同时保留 source（`2b952bca11d0…`）、runtime（`71f9ba968fcb…`）、运行容器
  （`71f9ba968fcb…`）和 Entry（`71f9ba968fcb…`）四个 digest。
- 根因：TC-API 管线（skopeo copy → oci → docker-archive → docker load）确定性剥离
  镜像 config 的 `Healthcheck` 字段，导致运行时 config digest ≠ registry source digest。
- 后续工程项：Registration Entry 应基于 Attestor 实际观察到的 runtime
  measurement，而不是未经 TC-API 转换的 source artifact measurement。

## Trustee metrics 与日志摘录

```text
argus_m4_fake_requests_total{service="workload_trustee",result="denied"} 1
argus_m4_fake_requests_total{service="workload_trustee",result="ok"} 2
```

- 日志文件：`/tmp/argus-dual-deny-r1.log`、`/tmp/argus-dual-allow-final.log`、
  `/tmp/argus-m3-deny-fix.log`、`/tmp/argus-m3-allow-fix.log`
- 不记录 TC-API identity token 或 bearer token（本报告不含任何 token 值）。

## 最终结论

> Mock Evidence Provider + Mock Trustee 软件链路通过。

> Application Readiness 未通过：dual-TDVM profile 未部署 Ollama/bge-m3。

该结论不覆盖真实 TDX Quote/QGS、Rekor 度量验证、生产 Trustee 或生产安全验收。
