# Argus-SPIFFE v2 真实 OpenClaw 插件链路验收报告

- 报告时间：2026-08-05 ~02:45 UTC（10:45 +0800）
- 验收环境：本机（TDX Host）+ 本机 QEMU TD VM（`argus-openviking-tdx`，SSH `tdx@127.0.0.1:2222`）
- 本轮验证中 `verify-architecture.sh` 与 `verify-openclaw-plugin-e2e.sh` 均实际重跑，退出码为第一手记录

## 1. 提交哈希与工作树状态

- 分支：`feat/argus-spiffe-v2`
- HEAD：`ea1c922ecb7e1fb71898a4e5546a5864a9d38308`（`fixs: 修复e2e脚本`，2026-08-05 10:08:30 +0800）
- 工作树状态：`git status` 输出 `nothing to commit, working tree clean`，`git status --porcelain` 为 0 行
- 注意：运行中的 runtime 目录标记为 `verify-a155ad2-...`，即环境是基于上一提交 `a155ad2` 部署的；`ea1c922` 仅修复 e2e 脚本，运行时容器未重建

## 2. 各命令退出码

| 命令 | 退出码 | 说明 |
| --- | --- | --- |
| `git status` | 0 | working tree clean |
| `core/spire/v2/verify-architecture.sh`（重跑） | 0 | 日志 `09b-verify-architecture-rerun-20260805T022753Z.log` |
| `verify-openclaw-plugin-e2e.sh` 第一次重跑（10b） | 1 | 操作失误：从 `openclaw config get ...apiKey` 取到的是脱敏占位符（长度 21），OpenViking 返回 401 `Invalid API Key`；agent turn 本身已成功，非系统缺陷 |
| `verify-openclaw-plugin-e2e.sh` 第二次重跑（10c，改用配置文件中的真实 key） | 0 | 日志 `10c-real-plugin-e2e-rerun2-20260805T023230Z.log` |
| 宿主机 `curl http://172.31.44.1:1934/health`（默认环境） | HTTP 403 | 注意：本机设有 `http_proxy=proxy-dmz.intel.com:911`，该 403 来自公司代理拦截页 |
| 宿主机 `curl --noproxy '*'` 直连 | HTTP 403 | 返回 proxy 自身文本 `OpenClaw egress source rejected`，并产生 `source_rejected` 日志 |
| 兄弟容器（默认 bridge）curl 1934 | HTTP 403 | 产生 `source_rejected` 日志 |
| Host/Guest 容器状态采集（12b/13b） | 0 | — |

另：02:01 的上一轮 e2e（日志 `10-real-plugin-e2e.log`）输出显示通过，但该日志未记录退出码，如实说明。

## 3. Host runtime 与 Guest data 路径

- Host runtime（`V2_RUNTIME_DIR`）：`/var/lib/argus-spire-v2-runtimes/verify-a155ad2-20260804-072045`（含 `env.sh`、`certs/`、`conf/`、`logs/`、`openclaw-agent-data/`、`openclaw-agent-run/`、`server-data/`、`server-run/`）
- Guest data（`V2_GUEST_DATA`）：`/var/lib/argus-spire-v2/verify-a155ad2-20260804-072045/openviking-agent`
- Guest run（`V2_GUEST_RUN`）：`/run/argus-spire-v2/verify-a155ad2-20260804-072045/openviking`
- Guest 内真实 OpenViking 数据挂载：`/var/lib/agentcc/openviking-real -> /app/.openviking`（容器 `agentcc-openviking-tdx`）

## 4. 两个 Agent SPIFFE ID

- OpenClaw 侧（x509pop）：`spiffe://argus.local/spire/agent/x509pop/b6e01f47ae607aec30d9f2c4d87e64f60a9b97d7`
- OpenViking/TDVM 侧（argus_tdx）：`spiffe://argus.local/spire/agent/argus_tdx/11b82fff772155611e19112a38f489b4fe281e1f1633b7dbdbbb91234c04aa93`
- 两者均未过期、相互独立，且无活跃的 join_token Agent

## 5. 两个 workload SPIFFE ID 及 parent

- `spiffe://argus.local/agent/openclaw` ← parent = x509pop Agent（`.../x509pop/b6e01f47...`）
- `spiffe://argus.local/service/openviking-cmem` ← parent = argus_tdx Agent（`.../argus_tdx/11b82fff...`）

## 6. OpenClaw bridge 与 proxy 地址

- 专用网络：`argus-openclaw-egress`，subnet `172.31.44.0/28`，gateway `172.31.44.1`
- 真实 OpenClaw 固定 IP：`172.31.44.2`（容器实际 IP 已核对一致）
- mTLS proxy 监听：`172.31.44.1:1934`，进程参数：`-listen=172.31.44.1:1934 -target=https://127.0.0.1:1943 -server-id=spiffe://argus.local/service/openviking-cmem -allow-source-ip=172.31.44.2`

## 7. 插件配置（不含 API key）

- `plugins.slots.contextEngine` = `openviking`
- `mode` = `remote`
- `baseUrl` = `http://172.31.44.1:1934`
- `peer_role` = `assistant`
- API key 已配置（长度 105 的字符串），按要求不予输出

## 8. verify-architecture 全部正向/负向结论（重跑退出码 0）

正向：

- 两个独立 Agent（x509pop + argus_tdx 各一）、两个独立 Workload API
- 两个 workload SVID 与预期 ID 精确一致；跨角色 label 检查在两个 Workload API 上均被拒
- 真实 OpenClaw 经 proxy 的 `/health` 返回 `{"status":"ok","healthy":true,"version":"v0.4.8","auth_mode":"api_key"}`
- Argus Guard 真实进程健康、显式 `mock_allow`、`/ra/v1/verify` 返回 ALLOW 且不伪造 claims
- 双向 X.509-SVID 认证与精确 peer ID 校验通过
- 两个 workload entry parent 不同且归属正确（x509pop / argus_tdx）

负向：

- 宿主机来源访问 1934 被拒（HTTP 403；另经 `--noproxy` 直连补证，见第 2、11 节说明）
- TDVM 1943 明文 HTTP 被拒
- 1943 无客户端 SVID 的 TLS 被拒
- 错误服务端 SPIFFE ID（`spiffe://argus.local/service/not-openviking`）被拒，实际 ID 与预期不符即 fail-closed

## 9. E2E 标识（本轮重跑 10c）

- E2E Marker：`ARGUS-MTLS-E2E-verify-a155ad2-rerun2-20260805T023230Z`
- OpenClaw session key：`argus-mtls-e2e-verify-a155ad2-rerun2-20260805T023230Z`
- OpenViking session ID：`2f75f185-e48f-44d7-b3f0-3c9151d39c30`
- commit task_id：`a4948bce-e80c-47d9-93ae-cf53b8ac5800`

（参考：02:01 上一轮——Marker `ARGUS-MTLS-E2E-verify-a155ad2-final-20260805T020120Z`，session `306daf7b-e483-4fbe-8882-0deb329351f3`，memory_count=2。）

## 10. commit/archive/memory

- `commit_count = 1`
- archive：true（`latest_archive_overview` 非空，脚本判定 `archive: true`）
- `memory_count = 0`（本轮 `V2_E2E_REQUIRE_MEMORY=0`，记忆抽取非强制项；脚本输出原样为 `{"commit_count":1,"archive":true,"memory_count":0}`）

## 11. proxy 日志证据（`docker logs argus-v2-openclaw-mtls`）

forwarded_mtls（本轮 10c 业务路径，均来自 `172.31.44.2`）：

```text
2026/08/05 02:32:44 request_id=e2e-scan-...-rerun2-20260805T023230Z   method=GET  path=/api/v1/sessions status=200 ... decision=forwarded_mtls
2026/08/05 02:32:45 request_id=e2e-scan-...-rerun2-20260805T023230Z   method=GET  path=/api/v1/sessions/2f75f185-.../context status=200 ... decision=forwarded_mtls
2026/08/05 02:32:46 request_id=e2e-commit-...-rerun2-20260805T023230Z method=POST path=/api/v1/sessions/2f75f185-.../commit  status=200 ... decision=forwarded_mtls
2026/08/05 02:32:47 request_id=e2e-inspect-...-rerun2-...             method=GET  path=/api/v1/tasks/a4948bce-... status=200 ... decision=forwarded_mtls
```

source_rejected：

```text
2026/08/05 02:41:20 ... method=GET path=/health status=403 source_ip=172.18.0.4   decision=source_rejected   （兄弟容器，本轮实测）
2026/08/05 02:43:12 ... method=GET path=/health status=403 source_ip=10.112.120.22 decision=source_rejected （宿主机 --noproxy 直连，本轮实测）
2026/08/05 01:30:41 / 01:32:25 / 01:36:33 ... status=403 source_ip=172.18.0.3 decision=source_rejected （历史 3 条）
```

诚实说明：本机 shell 带有公司 `http_proxy`，verify-mtls 中宿主机 403 检查的 403 实际由代理拦截设备返回；通过 `--noproxy '*'` 直连补证了 proxy 自身对非白名单来源同样 fail-closed 并记录 `source_rejected`。这不影响结论，但两者证据来源不同，如实区分。

## 12. Host 与 Guest 最终容器状态

Host（12b，退出码 0）：

- `agentcc-openclaw-sbx-gateway`：Up（healthy），egress IP `172.31.44.2`
- `argus-v2-openclaw-mtls` / `argus-v2-openclaw-agent` / `argus-v2-guard` / `argus-v2-spire-server` / `argus-v2-mock-trustee`：均 Up 17 小时

Guest（13b，经 SSH 采集，退出码 0）：

- `agentcc-openviking-tdx`：Up（healthy），restart=`unless-stopped`
- `argus-v2-openviking-mtls` / `argus-v2-openviking-agent` / `argus-v2-mock-evidence-provider`：均 Up 17 小时

## 13. 日志文件路径

目录 `/var/lib/argus-spire-v2-runtimes/verify-a155ad2-20260804-072045/logs/`：

- `01-prepare.log`、`02-start-server.log`、`03-start-openclaw-agent.log`、`04-start-openviking-agent.log`、`05-register-workloads.log`、`06-start-openclaw-workload.log`、`07-start-openviking-workload.log`、`08-connect-openclaw-plugin.log`
- `09-verify-architecture.log`（02:01 轮）、`09b-verify-architecture-rerun-20260805T022753Z.log`（本轮重跑）
- `10-real-plugin-e2e.log`（02:01 轮通过）、`10b-real-plugin-e2e-rerun-20260805T022821Z.log`（401 失败轮）、`10c-real-plugin-e2e-rerun2-20260805T023230Z.log`（本轮通过）
- `11-mtls-proxy.log`、`12-host-containers.log`、`13-guest-containers.log`
- `12b-host-containers-final-*.log`、`13b-guest-containers-final-*.log`（本轮最终状态）
- QEMU 串口日志：`/tmp/argus-spiffe-m4-0/argus-openviking-tdx/console.log`
- proxy 运行日志无文件，经 `docker logs argus-v2-openclaw-mtls` 获取

## 14. 结论

**PASS**：真实 OpenClaw 插件消息通过 SPIFFE mTLS 被 OpenViking 捕获并完成 commit/archive。

依据：真实 OpenClaw agent turn（session key `argus-mtls-e2e-...-rerun2-20260805T023230Z`）经插件 `afterTurn` 通过 `http://172.31.44.1:1934` 的 source-IP 受限 SPIFFE mTLS 通道写入 TDVM 内 OpenViking v0.4.8；session context 中出现唯一 Marker；commit 被接受（task `a4948bce-...`），`commit_count=1` 且 archive overview 生成；proxy 日志含对应 `forwarded_mtls` 业务路径。memory_count=0 系本轮未强制记忆抽取（`V2_E2E_REQUIRE_MEMORY=0`），不影响 PASS 判定。

## 15. 延期项重申

- Real Quote/QGS：**DEFERRED**
- 不可绕过的 Guard-to-mTLS 同请求门控：**DEFERRED**（Guard 当前为真实进程但显式 `mock_allow`，未内联到每个业务请求）
- Envoy/service mesh：**DEFERRED**
