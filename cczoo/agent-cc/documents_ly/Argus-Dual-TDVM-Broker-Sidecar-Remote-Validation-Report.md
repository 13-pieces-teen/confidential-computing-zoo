# 双 TDVM + Broker Sidecar 远程验证报告

> 状态：历史软件链验证记录，仅适用于下列指定commit。它不能证明当前HEAD的
> 真实TDX Node Attestation、Trustee
> appraisal或Workload Attestation通过。

历史执行结果：M3 ALLOW/DENY 与双 TDVM DENY/ALLOW 全部通过；ALLOW 含
wrong-client 负例、OpenViking 退出后 Sidecar 退出（pidfd）与 1943 关闭检查。
Round 4（commit `e253767`）验证在 `ollama pull bge-m3` 处因环境出网限制失败并
停止（见“Round 4”一节）；Round 4.1（commit `8882144`，Ollama DMZ 代理注入 +
register-workloads.sh 可执行位修复）后在干净状态下单独重跑：DENY 与 ALLOW
全链路通过，含 Application Readiness（Ollama/bge-m3 经 DMZ 代理下载、
/ready=200）。最终结论见文末。

## 构建信息

- 验证日期：2026-08-21（阶段验证）
- 验证代码 commit：Round 1–3 于 `ea15713`；Round 4 同步至 `e253767`；Round 4.1 验证 `8882144`（fix(dual-tdvm): make register-workloads.sh executable and add Ollama proxy injection）
- 报告基线 / 当前状态：Round 4.1 报告由 `2d17f79` 提交；本次一致性修订基于该 commit，文档修改尚未提交
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
| OpenViking container ID | `ed41a435369d1022ebd435ba94c229fcf2b5a3da99f5173b0a1d521e40c97217`（launch-cff7b0f，TC-API 启动，Round 4.1 ALLOW 干净重跑；验证结束后经 PID-exit 检查停止，Exited 0） |
| OpenViking host PID | `141702` |
| Sidecar `-target-pid` | `141702`（实测相等） |
| `broker.sock` owner/mode | `0:1000 770`（脚本断言） |
| OpenViking source image config digest | `sha256:2b952bca11d0d6a09cb5dcf91ad0f1e08151c986a687abfddc24483d3e65e348`（`localhost:5000/openviking:v0.4.8`） |
| TC-API runtime image config digest | `sha256:71f9ba968fcb27f0068c65a53b4654deb3272eafe8a726c663fa598906d1c408`（`openviking-cmem:latest`，TC-API pull→load 产物） |
| 运行容器实际 image config digest | `sha256:71f9ba968fcb27f0068c65a53b4654deb3272eafe8a726c663fa598906d1c408`（与 runtime 一致） |
| `dual-openviking-target` 注册的 config digest | `sha256:71f9ba968fcb27f0068c65a53b4654deb3272eafe8a726c663fa598906d1c408`（Round 4.1 实测：TC-API 启动后 `register-workloads.sh` 自动注册 runtime digest，无 override） |

## Non-intrusive 证据

| 检查 | 结果 / 证据 |
|---|---|
| OpenViking 无 Workload API mount | 通过。`docker inspect agentcc-openviking-service` mounts：`/etc/tdx-attest.conf`、`/app/.openviking`（state）、`/etc/sgx_default_qcnl.conf`、`/dev/tdx_guest`、`/td-attest`（TDX attest 示例）——无任何 `/opt/spire`、`/run/spire`、`/run/argus-svid` |
| OpenViking 无 Broker API mount | 通过。同上，mounts 中无 broker 目录 |
| OpenViking 无 X.509-SVID/private-key mount | 通过。同上，无 SVID/密钥挂载 |
| OpenViking 无直接获取 SPIFFE 身份的环境配置 | 通过。env 仅 `OPENVIKING_CONFIG_FILE=/app/.openviking/ov.conf`、`OPENVIKING_WITH_BOT=0`、`OPENVIKING_CLI_CONFIG_FILE=…`；无 `SPIFFE_ENDPOINT_SOCKET`/`ARGUS_SPIFFE_ENABLED`/`ARGUS_WORKLOAD_SPIFFE_ID` |
| Workload API、Broker API 与目标 SVID 只由 Broker Sidecar 使用 | 通过。Sidecar mounts 恰为：`/opt/spire/run/agent <- /run/argus-spire-dual/openviking`、`/opt/spire/run/broker <- /run/argus-spire-dual/openviking-broker`；Sidecar cmd `-workload-api=unix:///opt/spire/run/agent/agent.sock -broker-socket=/opt/spire/run/broker/broker.sock` |
| Sidecar `-target-pid` 等于 OpenViking 实际 host PID | 通过：Round 4.1 中 `-target-pid=141702` == OpenViking 启动后的实际 host PID `141702` |
| 用户隔离 | Agent `0:0`（需要 Docker/pid 访问），Broker Sidecar `1000:1000`（非特权） |

Sidecar 完整启动参数（docker inspect `{{json .Config.Cmd}}`）：

```json
["-workload-api=unix:///opt/spire/run/agent/agent.sock",
 "-broker-socket=/opt/spire/run/broker/broker.sock",
 "-broker-spiffe-id=spiffe://argus.local/infra/openviking-broker",
 "-agent-spiffe-id=spiffe://argus.local/spire/agent/argus_tdx/6619d9ab1a8ff1ddfdb216e8b938133d20ad757fa897876a959dc0b039443d9c",
 "-target-spiffe-id=spiffe://argus.local/service/openviking-cmem",
 "-client-spiffe-id=spiffe://argus.local/agent/openclaw",
 "-target-pid=141702",
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
| Sidecar PID 与 OpenViking PID 一致 | 通过：Round 4.1 中 `-target-pid=141702` == OpenViking 实际 host PID `141702` |
| Trustee decision / metric 为 ALLOW | 通过：Round 4.1 独立计数 `argus_m4_fake_requests_total{service="workload_trustee",result="ok"} 1` |
| verified selectors + runtime digest 命中强 Entry | 通过。Entry `dual-openviking-target` 的 config digest == 运行容器实际 digest == `sha256:71f9ba968fcb…`（精确选择器匹配） |
| Sidecar 获得目标 SVID 后监听 1943 | 通过。Round 4.1 Sidecar 日志：`OpenViking mTLS listener is ready for identity spiffe://argus.local/service/openviking-cmem` |
| Guard ALLOW 后 SPIFFE mTLS `/health=200` | 通过：`Guard ALLOW -> direct SPIFFE mTLS /health -> HTTP 200` |
| 无客户端证书访问失败 | 通过。Sidecar 日志：`tls: client didn't provide a certificate`（TLS 握手被拒） |
| 错误 expected-client ID 握手失败 | 通过：`Wrong-client SPIFFE ID rejected during mTLS handshake as expected: … ssl/tls alert bad certificate` |
| OpenClaw 无法访问明文 1933 | 通过。脚本断言 `http://10.0.2.2:1933/health` 不可达 |
| OpenViking 退出后 Sidecar 经 pidfd/lifecycle 退出 | 通过：Round 4.1 验证输出 `Target exit check: OpenViking stopped; Sidecar exited through pidfd monitoring`；随后确认 1943 关闭 |
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

## Application Readiness（Round 4.1 最终状态；非安全链路硬验收）

| 检查 | 结果 / 证据 |
|---|---|
| `/ready` HTTP 状态 | `200`（Round 4.1 跨 TDVM mTLS 实测） |
| OpenViking readiness 响应 | `{"status":"ready"}` |
| 结论 | `Application Readiness: READY - /ready HTTP 200` |
| 依赖状态 | Ollama 容器已部署，`bge-m3:latest` 已加载；Ollama 无宿主机端口映射 |

早期验证在未部署 Ollama/bge-m3 时曾得到 `/ready=503`；该历史结果保留在前序轮次
记录中。Round 4.1 显式启用 Application Readiness 后最终结果为 `/ready=200`。
Readiness 仍与身份及 mTLS 安全链路分开标记。

## Round 4（e253767）：deferred Sidecar + Ollama readiness 验证

验证对象：① TC-API 启动后自动读取 runtime image config digest 并注册 Entry
（`DUAL_OPENVIKING_IMAGE_CONFIG_DIGEST` 覆盖已从脚本移除）；② 可选
Ollama/bge-m3 Application Readiness。

### 执行记录

- 代码同步：`git fetch upstream feat/argus-spiffe-v2-val` → `git merge --ff-only`
  → HEAD `e253767800676362ba92f91a05a127a54e3e4044`，工作区 clean。
  两项改动已在 HEAD 中确认（register-workloads.sh 运行时 digest 注册、
  manage-guest.sh/verify.sh/launch_openviking.sh/README 的 readiness 模式）。
- 静态检查：`bash -n` 通过（manage-guest / register-workloads / verify /
  start-center / prepare / launch_openviking 共 6 个脚本）；`docker compose
  -f compose.yaml config` 通过。
- Ollama 镜像：宿主机 `docker pull ollama/ollama:0.5.7` 成功（明确版本，非
  latest）；`load-workload` 将镜像传入 OpenViking TDVM
  （guest image ID `sha256:f1fd985cee59…`，3.31GB）。
- 测试 ov.conf：`/opt/argus-dual-tdvm-runtime-secrets-ov-ollama.conf`，仅修改
  `embedding.dense.api_base` → `http://argus-dual-openviking-ollama:11434/v1`
  （provider=ollama、model=bge-m3 原样保留）；load-workload 内嵌 python 校验通过。
- 环境变量：`DUAL_OPENVIKING_APPLICATION_READY=1`、
  `DUAL_OPENVIKING_OLLAMA_IMAGE=ollama/ollama:0.5.7`、
  `DUAL_OPENVIKING_OLLAMA_MODEL=bge-m3`、`DUAL_EXPECT_APPLICATION_READY=1`；
  未设置 `DUAL_OPENVIKING_IMAGE_CONFIG_DIGEST`。
- Mock Trustee 切换至 `-workload-decision=deny`（README DENY 阶段）。

### 失败点（第一个真实失败，已停止）

`start-open-workload` 的 `start_openviking_ollama` 步骤失败于模型下载。
Ollama 容器按脚本完全相同的参数启动成功（`docker run -d --name
argus-dual-openviking-ollama --network argus-dual-openviking --restart
unless-stopped --env OLLAMA_HOST=0.0.0.0:11434 --volume
argus-dual-openviking-ollama-data:/root/.ollama ollama/ollama:0.5.7`），
`ollama ls` 1s 内就绪，随后：

```text
$ ollama show bge-m3
Error: model 'bge-m3' not found
$ ollama pull bge-m3
pulling manifest …
Error: pull model manifest: Get "https://registry.ollama.ai/v2/library/bge-m3/manifests/latest": dial tcp 104.18.16.170:443: i/o timeout
$ ollama show bge-m3
Error: model 'bge-m3' not found
```

按脚本逻辑，`ollama pull` 后仍有 `ollama show` 断言，失败即 `start-workload`
整体失败；该步骤先于 TC-API launch，因此变更① 的自动注册与 DENY/ALLOW
安全链路均未到达。依据“遇到第一个真实失败点立即停止”规则停止；未修改代码、
未绕过（未注入模型、未手工配置代理）。

### 根因判断

1. 验证主机 cwf-bkc 位于 Intel DMZ：宿主机直连外网被阻断，所有出网必须经
   HTTP 代理 `proxy-dmz.intel.com:911`（shell env）/ `:912`（docker daemon）。
2. OpenViking TDVM（QEMU slirp，无 `restrict=on`）无直连出网：guest 内 curl
   到 registry.ollama.ai、registry-1.docker.io、oauth2.sigstore.dev、
   example.com 全部 20s 超时（IPv4/IPv6 均验证），DNS 解析正常。
3. guest 经 slirp 可到达 `proxy-dmz.intel.com:911`（`curl -x` 实测返回真实
   响应：registry-1.docker.io HTTP 401、registry.ollama.ai HTTP 404），但
   guest 内无任何进程配置该代理（shell 无 env、docker daemon 无 conf、
   tc-api 容器无 proxy env）。
4. 新代码 `start_openviking_ollama` 启动容器仅设 `OLLAMA_HOST`，无代理注入
   机制；README 就绪模式也未要求/说明代理配置。因此容器内
   `ollama pull bge-m3` 必然超时。
5. 辅助证据：guest docker `net.ipv4.ip_forward=0`（docker run 警告
   "IPv4 forwarding is disabled. Networking will not work."），容器 NAT
   出网同样被禁用；tc-api 容器此前 sigstore TUF CDN 超时与此同源。

### 本轮已确认项

| 检查 | 结果 |
|---|---|
| `ollama/ollama:0.5.7` 明确版本镜像可用（host pull + guest 加载） | 通过 |
| ov.conf embedding 校验（provider=ollama / api_base 精确匹配 / model=bge-m3） | 通过 |
| Ollama 容器启动与 `ollama ls` 就绪 | 通过 |
| Ollama 容器无宿主机端口映射（`docker port` 为空；仅 EXPOSED 11434/tcp） | 通过 |
| Ollama 容器在 argus-dual-openviking 网络、volume 挂载 `…-ollama-data:/root/.ollama` | 通过 |
| bge-m3 模型加载 | 失败（下载出网超时，见上） |
| 变更① 自动注册（runtime digest = 容器 digest = Entry digest） | 未到达（启动在 ollama 步骤先失败） |
| DENY / ALLOW 安全链路（1943、/health、/ready、负例、pidfd） | 未到达 |
| Trustee metrics | 无新增（trustee 容器在 deny 切换时重建，计数器重置；metrics 端点本轮返回 HTTP 200 空 body，与“无请求”一致） |

### Round 4 结论

变更②（Ollama/bge-m3 readiness）在模型下载处因环境出网限制（DMZ 直连阻断 +
代码无代理注入机制）失败，属于环境限制而非代码逻辑缺陷；变更① 与安全链路
未在本轮重新执行。此结论不推翻前几轮“Mock Evidence Provider + Mock Trustee
软件链路通过”的既有结论。

## Round 4.1（8882144）：Ollama DMZ 代理注入 + 干净状态全链路重跑

用户授权“开始变更”后新增本地提交 `8882144`（fix(dual-tdvm): make
register-workloads.sh executable and add Ollama proxy injection），三项改动：

1. `DUAL_OPENVIKING_OLLAMA_EXTRA_ENV`：空格分隔的 `KEY=VALUE` 列表，作为额外
   `--env` 传给 Ollama 容器（DMZ 环境注入 `HTTP_PROXY`/`HTTPS_PROXY`）。
2. 脚本强制合并注入 `NO_PROXY`（`localhost,127.0.0.1,0.0.0.0,::1,10.0.0.0/8,
   172.16.0.0/12`）：容器内 ollama CLI 是遵循 `HTTP(S)_PROXY` 的 Go 客户端，
   不加豁免时连自连 `0.0.0.0:11434` 也会被导向 DMZ 代理。
3. `register-workloads.sh` 补上可执行位（100755，原 100644）——修复失败点 1。

### 失败点（真实，已修复；修复后重跑未再出现）

**失败点 1：auto-register `Permission denied`。** 首次 DENY 重跑停在
`manage-guest.sh:628` 直接调用 `register-workloads.sh`（git blob 模式 100644，
无 exec 位）。非本轮代码改动引入（`git diff` 确认仅 ollama 块）；按“遇到第一个
真实失败点立即停止”停止并询问，用户选择“修复并继续” → `chmod +x` 并随代理
注入一并提交 `8882144`。

**失败点 2：注入代理后容器内 `ollama` CLI “something went wrong”。** 裸 HTTP
API 正常、CLI 命令（`ls`/`show`/`pull`）全失败。根因：CLI 遵循 `HTTP(S)_PROXY`
（Go 客户端），目标 `0.0.0.0:11434` 不在默认 `NO_PROXY`（`localhost,127.0.0.1,…`
不含 `0.0.0.0`），回环拨号被导向 DMZ 代理。修复：脚本强制合并 `NO_PROXY`。
脚本等价环境下复测通过：`ollama ls` 1s 就绪、`ollama show bge-m3` 成功、
`ollama pull bge-m3` 成功、无宿主机端口映射。

### DENY（干净状态重跑，launch-9b0d0aa）

| 检查 | 结果 / 证据 |
|---|---|
| TC-API 启动后自动注册三个 Entry（无 override） | 通过。`dual-openclaw-workload`、`dual-openviking-broker`、`dual-openviking-target`（target 含 runtime digest + `verified/workload_id/policy` selectors） |
| Trustee decision / metric | `denied` 1 |
| Sidecar 无目标 SVID、无 1943、mTLS 阻塞 | 通过（verify.sh） |
| 强 Entry 不匹配（无 verified selectors） | 通过 |
| STAGED_DENY_RESULT | `LAUNCH_FAILED_AS_EXPECTED`（launch 失败即停止，未执行 verify） |

### ALLOW（干净状态单独重跑，launch-cff7b0f）

| 检查 | 结果 / 证据 |
|---|---|
| OpenViking 容器 / PID | `ed41a435369d…`（PID `141702`）；验证后按 PID-exit 检查停止（Exited 0） |
| runtime digest = 容器 digest = Entry digest | `sha256:71f9ba968fcb…`（自动注册，无 override） |
| Sidecar 收到目标 SVID 并监听 1943 | 通过（`spiffe://argus.local/service/openviking-cmem`） |
| Guard ALLOW → SPIFFE mTLS `/health` | `HTTP 200` |
| Application Readiness `/ready`（跨 TDVM mTLS） | `HTTP 200`（`{"status":"ready"}`；启动后 1×5s 等待即就绪） |
| 错误 expected-client SPIFFE ID | 拒绝（mTLS 握手 bad certificate，SSL alert 42） |
| 无客户端证书 / wrong-expected-client 负例 | 均按预期失败 |
| OpenClaw 访问明文 1933 | 不可达（通过） |
| Trustee metric | `ok` 1 |
| PID 生命周期：OpenViking 停止 → Sidecar pidfd 退出 → 1943 关闭 | 通过 |
| STAGED_ALLOW_RESULT | `VERIFY_PASSED` |

### Ollama 最终状态（ALLOW 验证结束时实测）

| 项 | 实测 |
|---|---|
| 容器 | `argus-dual-openviking-ollama`（网络 `argus-dual-openviking`；agent/evidence/registry/tc-api 保持运行） |
| 宿主机端口映射 | 无（`docker port` 为空） |
| 环境 | `OLLAMA_HOST=0.0.0.0:11434`；`HTTP_PROXY`/`HTTPS_PROXY=http://proxy-dmz.intel.com:911`；强制 `NO_PROXY`（`localhost,127.0.0.1,0.0.0.0,::1,10.0.0.0/8,172.16.0.0/12`） |
| 模型 | `bge-m3:latest` 已加载（ID `790764642607`，1.2GB）；模型 volume 持久化 1.1G |
| 验证结束后容器状态 | ollama 保持运行；openviking Exited(0)、sidecar Exited(1)——目标退出检查的预期结果 |

### Round 4.1 结论

DENY 与 ALLOW 在干净状态下全链路重新验证通过：变更① 自动注册（runtime digest
= 容器 digest = Entry digest，无 override）、变更② 应用就绪（Ollama 经 DMZ
代理下载 bge-m3，跨 TDVM mTLS `/ready=200`）、安全链路负例与 PID 生命周期
检查全部通过。Application Readiness 与安全链路结论分开标记，不改变安全链路
通过结论。

## Image digest 工程问题

- Round 4.1 之前的验证曾以 `DUAL_OPENVIKING_IMAGE_CONFIG_DIGEST=sha256:71f9ba968fcb…`
  覆盖重注册 `dual-openviking-target`，用于确认 Entry digest 与运行时镜像一致。
- 报告同时保留 source（`2b952bca11d0…`）、runtime（`71f9ba968fcb…`）、运行容器
  （`71f9ba968fcb…`）和 Entry（`71f9ba968fcb…`）四个 digest。
- 根因：TC-API 管线（skopeo copy → oci → docker-archive → docker load）确定性剥离
  镜像 config 的 `Healthcheck` 字段，导致运行时 config digest ≠ registry source digest。
- 后续工程项：Registration Entry 应基于 Attestor 实际观察到的 runtime
  measurement，而不是未经 TC-API 转换的 source artifact measurement。
- Round 4.1 中 override 不再需要：注册由 TC-API 启动后自动执行（e253767 变更①），
  Entry digest 取自 runtime，实测与运行容器一致（`71f9ba968fcb…`）。

## Trustee metrics 与日志摘录

Round 4.1（trustee 容器在 decision 切换时重建，计数按阶段独立）：

```text
# DENY 阶段（launch-9b0d0aa）
argus_m4_fake_requests_total{service="workload_trustee",result="denied"} 1
# ALLOW 阶段（launch-cff7b0f）
argus_m4_fake_requests_total{service="workload_trustee",result="ok"} 1
```

- 日志文件：`/tmp/argus-dual-deny-r1.log`、`/tmp/argus-dual-allow-r1.log`、
  `/tmp/argus-m3-deny-fix.log`、`/tmp/argus-m3-allow-fix.log`
- 不记录 TC-API identity token 或 bearer token（本报告不含任何 token 值）。

## 最终结论

> Mock Evidence Provider + Mock Trustee 软件链路通过。

> Application Readiness 通过：Round 4.1 干净重跑中，Ollama 容器注入 DMZ 代理
> （`proxy-dmz.intel.com:911`）并强制 `NO_PROXY` 豁免，bge-m3 下载成功
> （1.2GB，无宿主机端口映射），跨 TDVM mTLS `/ready=200`。

该结论不覆盖真实 TDX Quote/QGS、Rekor 度量验证、生产 Trustee 或生产安全验收。
