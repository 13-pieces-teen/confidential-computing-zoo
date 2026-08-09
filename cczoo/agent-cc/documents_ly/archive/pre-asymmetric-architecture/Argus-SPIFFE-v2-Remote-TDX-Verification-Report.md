# Argus-SPIFFE v2 真实 OpenClaw 插件验证报告（远程 TDX 主机）

## 1. 版本与运行时间

- HEAD: `f755c2325a5f803da3210fc9308d827dad14d9e3`（`Fix SPIFFE v2 verification contracts`）
- 起始工作树状态：干净，`git diff --check` 通过
- 验证窗口：2026-08-05 03:31–05:30 UTC（首轮 03:31–04:35 于 `aa240a5`；修复后复验 05:29–05:30 于 `f755c23`）
- 代码版本确认：`verify-mtls.sh` / `verify-svid.sh` / `verify-openclaw-plugin-e2e.sh` / `README.md` 中全部关键修复点均存在（命令级 `--noproxy`、`host_probe_request_id`、精确正文断言、同 Request ID 的 `decision=source_rejected` 日志断言、Workload API mount 检查与源匹配断言、`status=ok`+非空 `runId`+`result` 对象+无 `error` 的信封断言、captured-session `/messages` 写证据关联断言、`agent_write_evidence`、`e2e-scan-/e2e-commit-/e2e-inspect-` 排除）

## 2. 静态检查

- `bash -n core/spire/v2/verify-mtls.sh` → OK
- `bash -n core/spire/v2/verify-svid.sh` → OK
- `bash -n core/spire/v2/verify-openclaw-plugin-e2e.sh` → OK
- `git diff --check` → OK（exit 0）

## 3. 运行态确认

宿主机：`argus-v2-spire-server`、`argus-v2-openclaw-agent`、`argus-v2-guard`、`argus-v2-openclaw-mtls`、`agentcc-openclaw-sbx-gateway`（真实 OpenClaw，healthy）全部 Up。TDVM（pid 2471148，ssh:2222 / openviking:2933 / mtls:1943）内：`argus-v2-openviking-agent`、`argus-v2-openviking-mtls`、`agentcc-openviking-tdx`（healthy）全部 Up。

无关异常（仅报告，未处理）：`openclaw-openclaw-gateway-1`（项目 `/home/ed_song/source/openclaw`）因自身 `controlUi.allowedOrigins` 配置反复重启，与本验证无关，未占用 1934/1943 等验收端口。

## 4. 修复过程（先诊断、后最小修复）

**失败 1 — verify-svid OpenClaw 跨角色检查（exit 1）**
错误原文：`dial unix /opt/spire/run/openclaw/agent.sock: connect: no such file or directory`。
根因：运行中的 v2 栈绑定在 `V2_RUNTIME_DIR=/var/lib/argus-spire-v2-runtimes/verify-a155ad2-20260804-072045`，脚本默认用 `core/spire/v2/runtime`（空目录）。属环境配置不匹配，非网络/SPIRE 身份错误。
修复：export 正确的 `V2_RUNTIME_DIR`；TDVM 侧同理 export `V2_GUEST_RUN=/run/argus-spire-v2/verify-a155ad2-20260804-072045/openviking`（纯环境变量，无代码改动）。

**失败 2 — Host 403 精确正文断言（exit 1）**
错误原文：`Host-source HTTP 403 did not come from the OpenClaw mTLS proxy; body=OpenClaw egress source rejected␊.`。
根因：`mtls-smoke/main.go:182` 用 `http.Error()` 写拒绝正文，Go 会自动追加 `\n`，与脚本要求的字节级精确正文 `OpenClaw egress source rejected`（31 字节，无换行）不符。
最小修复（代码改动，唯一一处）：
- `core/spire/v2/mtls-smoke/main.go`：改为显式 `WriteHeader(403)` + `Write([]byte("OpenClaw egress source rejected"))`，保留 `Content-Type: text/plain; charset=utf-8` 与 `X-Content-Type-Options: nosniff`。`git diff --stat`：`1 file changed, 6 insertions(+), 1 deletion(-)`。
- 按 `prepare.sh` 的既有方式仅重建 `spire-mtls` 二进制与 `argus-spire-v2-mtls:local` 镜像（新 digest `sha256:9989e3ec…`），仅重建 `argus-v2-openclaw-mtls` 容器。
- 连带发现：注册条目 selector 钉住旧镜像 digest，重建后 workload `registered=false`；按项目既有 `register-workloads.sh` 流程重新登记（OpenClaw 条目 → 新 digest；OpenViking 条目 digest 不变），并用 `V2_MTLS_RUNTIME_IMAGE=sha256:9989e3ec…` 以 digest 形式重建容器（digest 引用是 docker attestor `image_id` selector 匹配的前提）。未放宽任何 selector。

**修复 2（提交 `f755c23`，闭环首轮 E2E 阻塞点）** — agent JSON 信封断言与真实 CLI 契约对齐：

- 首轮 E2E 在 agent JSON 信封断言处 fail-fast（根因见 §10）。`f755c23` 将断言从 `ok=true`+`status=ok` 修正为该 gateway 路径的真实契约：`status=="ok"`、非空 `runId`、`result` 对象、`error` 为空（不再要求不存在的顶层 `ok` 字段）。
- 同提交附带两处证据强化：`verify-openclaw-plugin-e2e.sh` 新增 captured-session 写证据关联断言（agent turn 的 mTLS 写证据必须命中捕获到的 OpenViking session 的 `/messages` 端点）；`verify-svid.sh` 新增活跃 Workload API mount 源与显式 `V2_RUNTIME_DIR` / `V2_GUEST_RUN` 的逐一匹配断言。
- 未放宽/删除既有断言，未改动 SUT。

## 5. 架构验证（修复后完整重跑，PASS）

日志：`/tmp/argus-v2-verify-architecture-fixed.log`，exit 0。

- OpenClaw Agent（x509pop）：`spiffe://argus.local/spire/agent/x509pop/b6e01f47…97d7`
- OpenViking Agent（argus_tdx）：`spiffe://argus.local/spire/agent/argus_tdx/11b82fff…aa93`（两 Agent ID 独立，无 join_token）
- Workload SVID：`spiffe://argus.local/agent/openclaw`、`spiffe://argus.local/service/openviking-cmem`；跨角色 label 请求在两个独立 Workload API 上均被拒绝
- OpenClaw→OpenViking mTLS：`{"status":"ok","healthy":true,"version":"v0.4.8","auth_mode":"api_key"}`

## 6. Guard 状态

`/health` → `{"status":"OK","version":"v1","mode":"mock_allow"}`；`/ra/v1/verify` → `decision=ALLOW`、`verification_mode=mock_allow`、`claims=null`（架构脚本内断言通过）。

## 7. Host 403 假阳性修复验证（PASS）

- 自动化断言（本轮 architecture 运行内）通过：直连 403 + 字节级精确正文 + 同 Request ID 日志行：
  `2026/08/05 03:42:57 request_id=verify-mtls-host-source-20260805T034257Z-2975750 method=GET path=/health status=403 duration=0s source_ip=10.112.120.22 decision=source_rejected`
- 人工交叉检查（`curl --noproxy 172.31.44.1`，未 unset 代理）：`HTTP/1.1 403 Forbidden`，带 `X-Argus-Request-Id`，正文恰好 31 字节 `OpenClaw egress source rejected` —— 明确来自 mTLS proxy，不是公司代理拦截页。

## 8. 公司代理保留证据

全程未 unset：`http_proxy=SET https_proxy=SET`（no_proxy 含 localhost/127.0.0.1 等）；仅对 `127.0.0.1`、`localhost`、`172.31.44.1` 按单次命令使用 `--noproxy`。

## 9. 真实 OpenClaw Workload API 隔离（PASS）

- `agentcc-openclaw-sbx-gateway` mounts 仅含：config/workspace volume、`/var/run/docker.sock`、模型 CA 证书 —— 无 `/run/spire`、无 `/opt/spire/run`、无 `agent.sock`。
- `argus-v2-openclaw-mtls` 仍持有自己的 Workload API mount：`…/openclaw-agent-run → /opt/spire/run/openclaw:ro`。两个结论分别成立。

## 10. 真实插件 E2E（修复后完整重跑，PASS）

**首轮 FAIL（`remote-fixed-20260805T034447Z`，已由 `f755c23` 修复）**

- 失败命令：`verify-openclaw-plugin-e2e.sh` 的 agent JSON 信封断言；exit 1
- 错误原文：`agent JSON output is not successful: ok=None status='ok'`
- 根因（含上游证据）：远程容器使用的 OpenClaw v2026.6.11 及当时 upstream main，`openclaw agent --json` gateway 路径信封均为 `{runId, status, summary, result}`，无顶层 `ok` 字段；`src/commands/agent-via-gateway.ts` 的 `buildGatewayJsonResponse` 原样输出 gateway payload。带 `{"ok":true,"status":"ok"}` 的信封属于另一子命令 `openclaw agent exec --json`（本容器 v2026.6.11 无此子命令）。即旧 `ok=true` 断言不符合本次被测 gateway CLI 契约。
- 修复（`f755c23`，§4 修复 2）：断言改为该契约的 `status=="ok"` + 非空 `runId` + `result` 对象 + 无 `error`；未放宽/删除断言、未改 SUT。

**修复后完整重跑（PASS）**：RUN_ID=`remote-e2e-20260805T052928Z`（全新，未复用）；日志 `/tmp/argus-v2-plugin-remote-e2e-20260805T052928Z.log`。`OPENVIKING_API_KEY` 从环境中安全加载（未打印）。自动化断言全部通过，exit 0：

- 预检：`OpenClaw plugin preflight passed: http://172.31.44.1:1934`
- agent 信封（修复点）：`run_id=8d6a2e82-2a6f-4838-a109-bbd989bf9e00`，output_chars=12577
- agent-turn mTLS 写证据：count=3，均满足证据过滤条件（`source_ip=172.31.44.2`、写类 method、`/api/v1/`、2xx、无 `e2e-` 前缀）
- Marker 捕获：`session_id=7ddd71f0-0a20-4ead-8949-ea160df595fe`，marker=`ARGUS-MTLS-E2E-remote-e2e-20260805T052928Z`
- **captured-session 写证据关联断言（新）**：写证据路径与捕获 session 的 `/messages` 端点完全一致：
  `2026/08/05 05:29:35 request_id=2bb249650eba7726e2407742 method=POST path=/api/v1/sessions/7ddd71f0-0a20-4ead-8949-ea160df595fe/messages status=200 duration=18ms source_ip=172.31.44.2 decision=forwarded_mtls`
- commit：`task_id=cf7b5931-faf8-4af5-81ce-b42744ae2f21`
- processing：`{"commit_count":1,"archive":true,"memory_count":0}`（`V2_E2E_REQUIRE_MEMORY=0`，memory 不计入通过）
- 最终：`Real OpenClaw -> OpenViking plugin E2E passed.`

## 11. 负向测试汇总（全部通过）

明文 HTTP → 1943 拒绝；无客户端 SVID 的 TLS → 拒绝；错误服务端 SPIFFE ID → 拒绝（`server SPIFFE ID rejected as expected`）；直接 host 源 → 403 精确正文 + 匹配日志；跨角色 workload label → 两边 Workload API 均拒绝。

## 12. 最终结论：**PASS**

- 通过：公司代理假 403 修复（含精确正文与日志匹配）、双 Agent/双 Workload API 隔离、SVID、mTLS 正反向、Guard mock_allow、真实 OpenClaw 无 Workload API mount、代理环境保留；agent JSON 信封断言修正为真实 CLI 契约后，完整插件 E2E（agent turn → mTLS 写证据 → captured-session `/messages` 关联 → marker 捕获 → commit → archive）以全新 RUN_ID `remote-e2e-20260805T052928Z` 全部自动化断言通过。
- 结论从首轮 PARTIAL 提升为 PASS；§10 的阻塞点由 `f755c23` 闭环，未放宽/删除断言、未改 SUT。

## 13. 尚未覆盖的边界（仍为 DEFERRED）

Real Quote/QGS；Guard-to-mTLS 同请求不可绕过门控；Envoy/service mesh。

已达成 `Argus-SPIFFE v2 real OpenClaw plugin mock-stage E2E PASS`（结论为 PASS）。原始日志均保留：`/tmp/argus-v2-verify-architecture-fixed.log`、`/tmp/argus-v2-plugin-remote-fixed-20260805T034447Z.log`、`/tmp/argus-v2-plugin-remote-e2e-20260805T052928Z.log`、`/tmp/argus-host-probe.{headers,body}`。
