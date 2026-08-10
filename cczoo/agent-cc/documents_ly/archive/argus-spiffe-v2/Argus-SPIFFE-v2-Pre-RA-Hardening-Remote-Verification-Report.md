# Argus-SPIFFE v2 Pre-RA Hardening Remote Verification Report

> **归档状态（2026-08-07）**：本文是旧Pre-RA增强防护Profile的历史远程证据，
> 不代表`argus-initial-direct` Profile已经验收。

- 验证日期（UTC）: 2026-08-06
- 执行依据: `cczoo/agent-cc/documents_ly/archive/argus-spiffe-v2/Argus-SPIFFE-v2-Pre-RA-Hardening-Plan.md`
- 验证主机: 远程 Linux/TDX 主机（Host + TDVM + Guest Docker + 真实 OpenClaw/OpenViking）
- 本轮日志目录: `/var/log/argus-spire-v2-verify/pre-ra-wp1-20260806T013248Z/`

> **后续代码审计补充（不属于本报告原始远程结果）**：初版
> `argus-docker-gate` 虽限制 endpoint 和 `containers/create`，但没有把
> container/exec 目标绑定到 gate 创建的 sandbox，因此仍可能对 SPIRE、Guard、
> mTLS 或其他已有容器执行已放行的 lifecycle/exec/archive API。原 WP2 PASS 仅代表
> 本报告所列矩阵通过，不能继续作为“无任意 Docker 控制”的完整安全结论。
> 当前源码已增加 run-scoped owner label 注入、container/exec parent 回查和对应
> 负向测试；修复版在远程重新构建并通过 `verify-wp2.sh` 与完整 E2E 前，WP2
> 当前状态按 **SOURCE FIXED / REMOTE REVALIDATION PENDING** 处理。

---

## 1. 结论

| 项目 | 状态 |
| --- | --- |
| 目标代码核对（远程仓库含本轮 WP1 加固代码） | **PASS** |
| 静态检查与编译 | **PARTIAL PASS**（cargo check/test --lib、gofmt、go test 通过；cargo fmt 与既有集成测试为工具链版本/既有问题，见 §4） |
| WP1 Guard 同请求强制门控（正向架构 19 项） | **PASS** |
| Guard fail-closed 矩阵（8 场景） | **PASS** |
| 真实 OpenClaw 插件 E2E | **PASS** |
| Node Attestation 回归（Provider 503 / Trustee 503 / timeout / replay） | **PASS** |
| 双 Agent v2（x509pop + argus_tdx，无 join_token） | **PASS** |
| WP2 数据面旁路收紧 | **PASS** |
| WP3 身份生命周期和拒绝收敛 | **部分完成，修复后待重验**（远程运行发现收敛行为；后续审查发现绝对连接寿命和负向断言缺陷，源码已修复但尚未在远程重验，见 §9.2） |
| WP4 - WP7 | **PENDING**（未实施与验证） |
| WP8 真实 RA 接口预留 | **PENDING**（仅接口预留，不视为真实 RA） |
| Real Quote/QGS | **DEFERRED** |
| Production Trustee | **DEFERRED** |
| Envoy/service mesh | **DEFERRED** |

**WP1 整体状态：PASS（架构 19 项 + Guard 矩阵 8 场景 + 真实 OpenClaw 插件 E2E 全部通过）。**

> 说明：E2E 首轮因连接脚本将插件 API key 覆盖为错误值而失败（OpenViking 401）；由用户手动执行恢复脚本 `/tmp/fix_ov_key.sh`（恢复 105 字符 key）后重跑 E2E 通过。修复过程详见 §9 与 §12。

---

## 2. 版本信息

- Git SHA（本报告对应提交）: `759379fdc0793a3a80c4e1c094e497050165c226`（`feat: WP3部分完成`）
- 报告一致性修订提交: `d757fa9a79125aa0cc99052978d9d3346897ab92`
- WP3 审查修复: 当前工作树源码，尚无远程验证 SHA；不得把该版本标记为 PASS
- Branch: `feat/argus-spiffe-v2`
- 相关提交链（时间序）:
  ```
  daf417e  Verify WP1 Guard-gated evaluation; fix Guard log correlation and Go module hygiene
  e9be85b  feat: complete wp2
  759379f  feat: WP3部分完成   <-- 本报告对应 HEAD
  ```
- 本报告验证的最终工作树状态（对应 `759379f` 提交内容，工作树干净）:
  - WP1: `core/argus/src/bin/guard.rs`、`core/spire/v2/mtls-smoke/go.mod`、`core/spire/v2/mtls-smoke/go.sum`、`core/spire/v2/verify-guard-gate-failures.sh`
  - WP2: `core/spire/v2/docker-gate/`（main.go + main_test.go + go.mod）、`start-docker-gate.sh`、`repoint-openclaw-socket.sh`、`apply-wp2.sh`、`verify-wp2.sh`、`start-openclaw-workload.sh`、`README.md`
  - WP3: `core/spire/v2/mtls-smoke/main.go`、`compose.center.yaml`、`deploy-v2-guest.sh`、`verify-wp3.sh`、`openclaw-agent.conf.tmpl`（回退）、计划文档、本报告
- 验证起点 SHA: `0e0db39`（本会话开始时 HEAD）
- 是否产生修复: **是**（详见 §12）
- `git diff --check`: **PASS**（初始与修复后均无空白错误）
- 未执行 commit / push / reset / clean；未覆盖用户已有修改。

---

## 3. 远程环境

### Host
- 主机名: 远程验证主机；OS `Linux 6.18.10-tdx`
- Docker 正常；`docker compose`（插件 v2）可用；`go`/`gofmt` 不在宿主 PATH，Go 工具链在 `golang:1.24-bookworm` 容器内（prepare.sh 的设计方式）
- Rust 工具链: `cargo 1.92.0 / rustc 1.92.0`；rustfmt 组件在宿主缺失，在 `rust:1.86-bookworm` 容器内安装后执行

### TDVM
- QEMU TDX 虚拟机 `argus-openviking-tdx` 运行中（`-machine q35,confidential-guest-support=tdx0`）
- SSH: `tdx@127.0.0.1:2222`（`/root/.ssh/id_rsa`）连通正常
- Guest 内核: `6.11.0-26-generic`

### Guest TDX 状态
- `/dev/tdx_guest` 为 live（`TDX_GUEST_LIVE`）

### Docker 状态
- Guest Docker 正常（`/usr/local/bin/docker`）
- Guest v2 容器（本轮）：`argus-v2-mock-evidence-provider`、`argus-v2-openviking-agent`、`argus-v2-openviking-mtls`，另有真实 OpenViking 服务 `agentcc-openviking-tdx`（1933）

### 公司代理与本地 NO_PROXY 处理
- 公司代理: `http://proxy-dmz.intel.com:911`（HTTP/HTTPS）
- 全局 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` **未清除**；外部网络继续经公司代理
- 本地绕过: 对 `127.0.0.1`、`localhost`、Guard（18007）、egress bridge（172.31.44.x）、TDVM 转发（1943/2222）均使用 `curl --noproxy` 或 `NO_PROXY` 精确绕过；egress 内 Guard 客户端 `http.Transport{Proxy: nil}` 绕过代理
- 注: `172.31.44.0/28` 不在全局 NO_PROXY 中，验证脚本已用 `--noproxy` 显式绕过

### 端口占用情况
| 端口 | 监听 | 归属 |
| --- | --- | --- |
| 18081 | SPIRE Server（docker） | 本轮 runtime |
| 18007 | Argus Guard（docker，127.0.0.1） | 本轮 runtime |
| 18017 | 无 | 故障 stub 临时端口（验证后已清理） |
| 1934 | `python3` 127.0.0.1:1934 + `spire-mtls` 172.31.44.1:1934 | python3 为旧 TCP forwarder（2026-07-29 启动，非本轮，地址不同不冲突，未 kill）；spire-mtls 为本轮 egress |
| 1943 | TDVM mTLS server（qemu hostfwd） | 本轮 OpenViking workload |
| 2222 / 2933 | QEMU hostfwd（SSH / OpenViking 1933） | 既有 TDVM 配置 |

端口冲突调查结论：**无冲突**。127.0.0.1:1934 的旧 python3 与本轮 egress 172.31.44.1:1934 绑定地址不同，互不影响；按要求未停止任何旧服务。

---

## 4. 静态检查和编译

| 命令 | 结果 | 日志 |
| --- | --- | --- |
| `git diff --check` | PASS | - |
| `bash -n`（v2 + ../scripts 共 19 个脚本） | PASS | - |
| `cargo check`（lib + bins） | PASS | `cargo-check.log` |
| `cargo test --lib --bins` | PASS（17 tests） | `cargo-test-lib.log` |
| `cargo test`（全目标） | **FAIL**（既有 tests/ 集成测试编译失败，非本轮回归，见下） | `cargo-test.log` |
| `cargo fmt --check`（rust:1.86 容器内） | **FAIL**（全代码库 119 处 rustfmt 版本偏差，非本轮回归，见下） | `cargo-fmt-check-docker2.log` |
| `gofmt -l`（mtls-smoke） | PASS | `gofmt-l.log` |
| `go test ./...`（默认 readonly 模式） | 修复前 FAIL（缺 go.sum）→ 修复后 PASS | `go-test.log` / `go-test-fixed.log` |
| `docker compose config` | PASS | `compose-config.log` |
| 可执行权限 | PASS（3 个脚本经 `bash` 调用，git 模式 100644 与磁盘一致） | - |

### 4.1 cargo fmt 说明
`cargo fmt --check` 在 rustfmt 1.86 下对**整个 argus 代码库**（40 个文件、119 处）报告风格差异（`use` 排序、`.map_err` 换行等行宽风格），且 `lib.rs` 自原始提交（7aa3cb7）起即未导出 `tdx_verifier` 等模块，说明该代码库从未在 rustfmt 1.86 规则下格式化。这是**工具链版本偏差**，波及全部既有文件（含 `errors.rs`/`types.rs`/`policy.rs` 等非 WP1 文件），且 WP1 仅修改 `guard.rs` 一个 Rust 文件、其格式与既有代码风格一致。最小范围修复需重排全部 40 个既有文件，违反"最小范围修复"与"不覆盖用户已有修改"约束，故未执行 `cargo fmt`，如实报告为工具链版本偏差。

### 4.2 既有集成测试编译失败说明
`tests/` 下 `unit_tests.rs`、`it_evidence_fetcher_http.rs`、`integration_test_helpers.rs` 引用不存在的 `argus::tdx_verifier` 模块、`generate_nonce_with_size` 函数，以及旧版 `BindingIdentityClaims` 字段。经 git 历史确认这些测试**自原始提交（7aa3cb7）起从未编译通过**，非 WP1 引入。本轮未修改这些既有测试（不覆盖用户代码），如实报告。

### 4.3 Go 模块 go.sum 修复
`mtls-smoke` 模块缺失 `go.sum` 且 `go.mod` 缺 indirect requires，导致默认 readonly 模式 `go test ./...`/`go build` 失败。最小修复：`go mod` 补齐 indirect requires 与 `go.sum`（16 行），修复后 `go test ./...` 默认模式 PASS（见 §12）。

### 4.4 Rust Guard / Go egress 代码合同核验（阅读源码 + 运行时验证）
- authorization context 版本: `argus-authorization-v2` ✓
- digest domain: `argus-business-authorization-v2\0` ✓
- context 字段齐全: `request_id`、`request_digest`、`method`、`path_and_query`、`body_sha256`、`caller_spiffe_id`、`target_spiffe_id`、`target_service`、`target_uri`、`operation`、`data_class`、`issued_at_unix`、`nonce` ✓
- Guard 重新计算 request digest（`compute_authorization_digest`，固定域 + 长度前缀 + 固定字段序）✓
- Guard 校验 caller、target service、target URI、target SPIFFE ID、operation、时间窗口（±60s）✓
- receipt（decision_id + request_digest + expires_at）在最终策略判定后生成 ✓
- egress 同步调用 Guard 后才转发同一冻结请求 ✓
- Guard ALLOW 与 DENY 均校验 decision_id、request_digest、过期时间、mode、claims ✓
- `mock_allow` claims 为 `null`（egress 强制校验 `bytes.Equal(claims, "null")`）✓
- 缺失/错误 receipt、digest mismatch、过期 receipt、malformed response 均 fail-closed ✓
- 内部 request_id 安全随机（`crypto/rand` / `getrandom`）；客户端 request_id 仅作为 `client_request_id` 保留 ✓
- Guard loopback 请求经 `http.Transport{Proxy: nil}` 绕过公司代理 ✓
- 错误不会转换为 ALLOW；`GUARD_MODE` 显式配置；`PORT=0` 启动失败；`GUARD_ALLOW_INCOMPLETE_EVIDENCE` 默认关闭 ✓
- egress 转发用同一冻结 method/path/query/body；Guard 错误路径不调用下游 mTLS transport；DENY=403 与 Guard 故障=503 语义分离；malformed DENY 不泄露 receipt ✓

---

## 5. Runtime 隔离

本轮正向 runtime（RUN_ID=`pre-ra-wp1-20260806T013248Z`），全部为全新路径：

| 类别 | Host 路径 | Guest 路径 |
| --- | --- | --- |
| 中心 runtime | `/var/lib/argus-spire-v2-runtimes/pre-ra-wp1-20260806T013248Z` | - |
| Guest root | - | `/opt/argus-spire-v2/pre-ra-wp1-20260806T013248Z` |
| Guest data | - | `/var/lib/argus-spire-v2/pre-ra-wp1-20260806T013248Z/openviking-agent` |
| Guest run | - | `/run/argus-spire-v2/pre-ra-wp1-20260806T013248Z/openviking` |

- OpenClaw Workload API socket mount source: `/var/lib/argus-spire-v2-runtimes/pre-ra-wp1-20260806T013248Z/openclaw-agent-run`
- OpenViking Workload API socket mount source（Guest）: `/run/argus-spire-v2/pre-ra-wp1-20260806T013248Z/openviking`
- 上一轮 runtime `verify-a155ad2-20260804-072045`（Host + Guest）**未删除、未覆盖**，其数据保留
- OpenClaw 与 OpenViking 的 Agent data、attestation key、Workload API socket 均不同
- 故障注入变量（`V2_EVIDENCE_*`、`V2_REPLAY_EVIDENCE`、`V2_TRUSTEE_*`、`GUARD_ALLOW_INCOMPLETE_EVIDENCE`）在正向运行前已清除

---

## 6. 双 Agent 和 workload 身份

| 项 | OpenClaw | OpenViking |
| --- | --- | --- |
| NodeAttestor | `x509pop` | `argus_tdx` |
| Agent ID | `spiffe://argus.local/spire/agent/x509pop/ca6d544d...` | `spiffe://argus.local/spire/agent/argus_tdx/5aa6b1c9...` |
| Workload SPIFFE ID | `spiffe://argus.local/agent/openclaw` | `spiffe://argus.local/service/openviking-cmem` |
| Entry parent | x509pop Agent | argus_tdx Agent |
| Selectors | `docker:label:argus.workload:openclaw`、`docker:image_id:sha256:54f7c4a9...`、`docker:image_config_digest:sha256:54f7c4a9...` | `docker:label:argus.workload:openviking-cmem`、`docker:image_id:sha256:f4c633d0...`、`docker:image_config_digest:sha256:f4c633d0...` |

- 两个 Agent ID 不同；**无 live Join Token Agent**
- 双向跨角色 selector 请求均被拒绝（`verify-svid.sh` cross-role checks）
- OpenClaw / OpenViking workload 只能访问各自 Workload API
- 真实 OpenClaw 容器无 SPIRE Workload API mount（`verify-mtls.sh` 检查通过）

---

## 7. Guard 同请求门控

- Guard health: `mode=mock_allow`、`authorization_context_required=true`、`authorization_context_version=argus-authorization-v2`、`decision_ttl_seconds=15`（有限 TTL）、incomplete evidence 未启用
- digest 合法但 target 不匹配的 Guard 请求返回 **400**（`target_service=not-openviking` 测试）
- 正向 OpenClaw 请求获得 Guard receipt（`decision_id` + `request_digest` + `expires_at`）
- 同一请求在 Guard 与 egress 日志中出现相同 `request_id`、`decision_id`、`request_digest`
- Guard ALLOW 日志先于同请求 `forwarded_mtls`（日志时间序验证）
- 明文 HTTP 拒绝、无客户端 SVID 拒绝、错误服务端 SPIFFE ID 拒绝
- Host source 请求由真实 egress 返回 403（响应正文 `OpenClaw egress source rejected` + `source_rejected` 日志 + 匹配 request_id，非代理 403）
- 负向测试后正向 mTLS 请求仍成功

**因果日志证据示例**（verify-mtls-health 请求）:
```
guard:  ... Guard returned mock ALLOW ... request_id=e3c0dc779397a811a22c3c78 request_digest=sha256:70cca8ca... decision_id=8f234e8325c2365534a2211512e2bf3d
egress: ... request_id=e3c0dc779397a811a22c3c78 client_request_id=verify-mtls-health ... decision=forwarded_mtls guard_decision_id=8f234e8325c2365534a2211512e2bf3d request_digest=sha256:70cca8ca... verification_mode=mock_allow
```

修复后 Guard 日志为无 ANSI、无引号的纯文本（修复见 §12）。

---

## 8. Guard failure matrix

执行: `bash core/spire/v2/verify-guard-gate-failures.sh` → **exit 0 PASS**

| 场景 | 预期 HTTP | 预期 decision | 实际结果 | receipt 状态 | 有无 forwarded_mtls |
| --- | --- | --- | --- | --- | --- |
| 合法 DENY | 403 | guard_denied | PASS | 保留合法 receipt | 无 |
| malformed DENY（缺 receipt） | 503 | guard_error | PASS | 不泄露 receipt | 无 |
| Guard HTTP 503 | 503 | guard_error | PASS | 不泄露 receipt | 无 |
| Guard timeout（1s） | 503 | guard_error | PASS | 不泄露 receipt | 无 |
| malformed JSON | 503 | guard_error | PASS | 不泄露 receipt | 无 |
| missing receipt | 503 | guard_error | PASS | 不泄露 receipt | 无 |
| request digest mismatch | 503 | guard_error | PASS | 不泄露 receipt | 无 |
| expired receipt | 503 | guard_error | PASS | 不泄露 receipt | 无 |

- 临时 fault Guard 仅影响 OpenClaw egress（stub 监听 `127.0.0.1:18017`）
- exit trap 成功恢复真实 Guard 配置；恢复后真实 Guard-gated `/health` 返回 200（脚本内置 recovery 检查通过）
- 每个被拒请求的 request_id 均无 `forwarded_mtls` 日志；不存在因重试/fallback/旧连接造成的下游转发
- 8 个场景的 request_id: `26335437...`、`38e113fd...`、`66d35e7e...`、`a489ceaf...`、`f44e45a5...`、`c1161422...`、`4eaa2bfd...`、`9f32d557...`

---

## 9. 真实 OpenClaw 插件 E2E

执行: `bash core/spire/v2/verify-openclaw-plugin-e2e.sh`

**状态：PASS**（exit 0）

> 首轮 E2E（`verify-openclaw-plugin-e2e.log`）因插件 API key 被 `connect-openclaw-plugin.sh` 覆盖为错误值而失败（OpenViking 返回 401 `Invalid API Key`；agent turn 本身成功，run_id=42b2c031-…）。本会话 auto-mode 凭据分类器持续不可用，恢复命令无法由本会话执行；随后由**用户手动运行恢复脚本 `/tmp/fix_ov_key.sh`**（从配置备份 `openclaw.json.bak.2026-08-06T02-19-40` 恢复 105 字符 key 到插件配置与 env 文件，`docker cp` + `chown node:node` + 重启网关），重跑 E2E 通过。

**最终通过证据（`verify-openclaw-plugin-e2e-2.log`）**:
1. 唯一 marker: `ARGUS-MTLS-E2E-20260806T053131Z-3940`
2. 真实 `openclaw agent` turn: status=ok, run_id=`75706391-7e9c-47f7-b5e1-bc2ad01422ea`, output_chars=12548
3. OpenClaw 返回合法 result 对象
4. Agent-turn mTLS write 证据 count=3（write-class `/api/v1/` 请求）
5. OpenViking 捕获对应 session: `975d4d7d-1ab2-4b4a-a88e-c9ff300916f8`
6. 精确命中该 session 的 `/messages` 写入:
   ```
   05:31:37 request_id=12cf43cf29ca21fa4f8e3cee client_request_id=none
   method=POST path=/api/v1/sessions/975d4d7d-1ab2-4b4a-a88e-c9ff300916f8/messages
   status=200 source_ip=172.31.44.2 decision=forwarded_mtls
   guard_decision_id=4ca3ff60c2e32b8374301371aa2c2af7
   request_digest=sha256:7b1fea014fec041ed849f6038c777b9e012c37a0461493a3296fecda15177c92
   verification_mode=mock_allow
   ```
7. 写入发生前已取得有效 Guard ALLOW receipt（Guard 日志）:
   ```
   05:31:37.014034Z WARN argus_guard: Guard returned mock ALLOW ...
   request_id=12cf43cf29ca21fa4f8e3cee request_digest=sha256:7b1fea014fec... decision_id=4ca3ff60c2e32b8374301371aa2c2af7
   ```
8. Guard、egress、OpenViking 证据通过相同 `request_id=12cf43cf...`、`decision_id=4ca3ff60...`、`request_digest=sha256:7b1fea01...` 关联
9. 对应请求确为 write-class（POST `/messages`），非健康检查或后续扫描请求（扫描请求的 client_request_id 以 `e2e-scan-`/`e2e-commit-`/`e2e-inspect-` 为前缀，被过滤器排除）
10. session commit 成功（task_id=`cce31dfb-d4fb-4e8f-aa97-ba5f868cb391`）
11. commit_count=1 > 0
12. archive overview 可读取（`"archive":true`）
13. 报告仅含 marker、session ID、request ID、decision ID、digest 与日志路径，未记录 API key 与敏感业务内容

**E2E 完成 → WP1 整体标记 PASS。**

### 9.1 WP2：数据面旁路收紧

**状态：PASS**（`verify-wp2.sh` exit 0；回归全部通过）

实现：
- 新增 Go 最小 Docker socket proxy `core/spire/v2/docker-gate/`（端点白名单 + `POST /containers/create` 强制校验：拒绝 privileged / host 或命名网络 / cap_add / unconfined / 任意 host bind / device，仅允许配置的 sandbox 镜像）。
- 新增 `start-docker-gate.sh`（构建+运行 proxy，socket 在 `/var/run/argus/docker-proxy.sock`）、`repoint-openclaw-socket.sh`（重建网关换接 proxy socket，保留全部其余配置）、`apply-wp2.sh`（幂等应用 WP2 全状态）、`verify-wp2.sh`（验证）。
- 修改 `start-openclaw-workload.sh`：egress bridge 重建为 `--internal`，仅网关（172.31.44.2）可挂载。

`verify-wp2.sh` 证据（`verify-wp2.log`）：
1. 网关 `/var/run/docker.sock` 挂载源 == `/var/run/argus/docker-proxy.sock`；`docker ps` 经 proxy 正常；`docker run --privileged` 被拒（`blocked by argus-docker-gate: privileged containers are forbidden`）
2. egress bridge `Internal=true`，成员仅 `agentcc-openclaw-sbx-gateway=172.31.44.2/28`
3. 兄弟容器（172.31.44.3）加入 egress bridge → curl egress 返回 **403** + 正文 `OpenClaw egress source rejected` + egress 日志 `source_rejected`（request_id=2b366cf4…）
4. 网关 `NetworkMode != host`、未 attach 身份网络（`argus-spire-v2-center_center`）、无法访问 `127.0.0.1:2933`（TDVM 原始 OpenViking）
5. 正向 Guard-gated egress 返回 200 且带 mock_allow receipt 头

回归（WP2 后重跑全部 PASS）：
- `verify-architecture.sh` PASS（19 项）
- `verify-guard-gate-failures.sh` PASS（8 场景）
- `verify-openclaw-plugin-e2e.sh` **PASS**（真实 OpenClaw sandbox 经 proxy 生成并正常工作；marker `ARGUS-MTLS-E2E-20260806T063139Z-30477`、session `6bd3ca8c-…`、messages 写入 status=200、Guard ALLOW 与 forwarded_mtls 关联、commit_count=1、archive=true）——证明 WP2 Docker 控制面隔离**未破坏**真实数据面。

静态检查：docker-gate 模块 `gofmt`/`go vet`/`go test`（含 allowlist 与 create 校验单元测试）PASS；新脚本 `bash -n` PASS。

### 9.2 WP3：身份生命周期和拒绝收敛

**状态：远程运行观测到连接收敛和恢复行为，但后续代码审查发现最大连接寿命实现不能中断已阻塞的活动 I/O，entry 删除与 Agent ban 的身份拒绝断言方向错误。因此原“阶段 A 完成”和“6/7 PASS”结论撤回。相关源码已修复，等待远程重新编译和完整重验；WP3 整体仍为部分完成。**

**WP3 隔离 runtime（阶段 B 执行环境）**：
- Host runtime: `/var/lib/argus-spire-v2-runtimes/wp3-lifecycle-20260806T072158Z`
- Guest root: `/opt/argus-spire-v2/wp3-lifecycle-20260806T072158Z`
- Guest data: `/var/lib/argus-spire-v2/wp3-lifecycle-20260806T072158Z/openviking-agent`
- Guest run: `/run/argus-spire-v2/wp3-lifecycle-20260806T072158Z/openviking`
- 测试后已恢复正向 runtime（`pre-ra-wp1-20260806T013248Z`），E2E 回归 PASS

**WP3 原始日志路径**（`/var/log/argus-spire-v2-verify/pre-ra-wp1-20260806T013248Z/`）：
- `wp3-build.log`（mtls-smoke 编译）、`wp3-prepare.log`（阶段 A prepare）
- `verify-wp3.log` / `verify-wp3-2.log` / `verify-wp3-3.log` / `verify-wp3-4.log` / `verify-wp3-5.log`（各轮 verify-wp3.sh 输出）
- `wp3-b-prepare.log` / `wp3-b-start-server.log` / `wp3-b-register.log` 等（WP3-B runtime 部署）
- `wp3-restore-positive-*.log`（正向 runtime 恢复）
- `verify-openclaw-plugin-e2e-wp3.log` / `wp3-restore-positive-e2e.log`（E2E 回归）

远程运行时使用的初版实现：
- `mtls-smoke/main.go` 新增连接生命周期：
  - `-conn-max-lifetime`（默认 60s）与 `-conn-idle-timeout`（默认 30s）
  - `lifetimeConn` + `lifetimeListener`；审查确认初版只在进入 `Read`/`Write` 前检查时间，不能中断已经阻塞的活动 I/O
  - client Transport 增加 `MaxIdleConns`/`MaxIdleConnsPerHost`/`IdleConnTimeout`/`TLSHandshakeTimeout`/`ResponseHeaderTimeout`
  - X509Context drain watcher：Workload API 报告 SVID/bundle 更新时 `CloseIdleConnections()` 排空旧连接
- egress 与 guest mTLS server 均已部署新 flags（日志确认 `conn max lifetime=1m0s idle timeout=30s`）
- E2E 回归 **PASS**，但该结果只证明普通短请求链路没有回归，不证明活动连接在 60 秒内强制排空

审查后的源码修复（尚待远程验证）：
- 连接创建时设置绝对 deadline，并用定时器到期主动关闭；
- 限制 `SetDeadline`/`SetReadDeadline`/`SetWriteDeadline` 不能越过绝对寿命；
- 增加阻塞读取和清除 deadline 的 Go 回归测试；
- X509Context watcher 发生错误时记录日志，永久退出时使 egress 失败退出；
- 修正 WP3 脚本的身份拒绝语义、SVID 实际到期时间采集、SLA deadline 循环和 EXIT/INT/TERM 恢复 trap。

**`can_reattest` 实现约束（重要）**：SPIRE 1.15.1 `spire-server agent` CLI **无 `update` 子命令**（仅 ban/count/evict/list/purge/show），无法在 Agent 认证后修改 `can_reattest`；它由 NodeAttestor 决定。实测：OpenClaw `x509pop` Agent `can_reattest=true`（默认允许重认证）、OpenViking `argus_tdx` Agent `can_reattest=false`（满足计划"保持不变 false"）。OpenClaw 侧生命周期测试将观测 `can_reattest=true` 行为；文档不声称可在 CLI 层将 OpenClaw 设为 false。详见计划文档 §7.1。

**WP3-B 验证结果（`verify-wp3.sh`，OpenClaw 侧，隔离 runtime）**：

**历史输出**（`verify-wp3-final.log`）：脚本整体 exit 1。后续审查确认其中部分 PASS 来自错误断言，因此下表按可采信程度重新分类；修复后的脚本尚未产生远程结果。

| 场景（verify-wp3.sh 测试函数） | 结果 | 说明 |
| --- | --- | --- |
| `test_proxy_restart` — mTLS egress 重启与恢复 | **PASS** | egress 重启后重新取得 SVID，正向路径恢复 |
| `test_workload_api_outage` — Workload API 短时中断 | **PARTIAL** | 新建 identity client 在 socket 中断时失败、恢复后正常；未证明持有未过期缓存 SVID 的业务请求会立即停止，也不应如此声称 |
| `test_agent_restart` — SPIRE Agent 重启 | **PASS** | x509pop 重新认证（can_reattest=true），恢复 |
| `test_server_restart` — SPIRE Server 重启 | **PASS** | 双 Agent 身份与 entry 持久化，恢复 |
| `test_entry_deletion` — workload entry 删除 | **历史结果无效，待重验** | 初版脚本把 `-expect-no-identity` 成功返回当成失败、其他错误当成成功；修复版只在此场景验证 control-plane 删除和恢复，最终拒绝由收敛场景验证 |
| `test_agent_ban` — Agent ban | **历史结果无效，待重验** | 初版身份拒绝断言方向错误；修复版将验证 banned 状态、实际 SVID 到期、业务路径停止、明确身份拒绝及 re-attestation 恢复 |
| `test_connection_convergence` — 连接收敛 | **PARTIAL** | 单轮实测正路径在 entry 删除后 ~208s 停止（90s 预算轮断言失败但观测到收敛）；360s 预算轮因 SVID-TTL 钳制导致的收敛时长波动未在工具超时窗口内完成，自动化断言未干净通过 |
| trust bundle 更新 / 旧 bundle / 错误 trust domain | **SKIP** | 需第二 trust domain 或 CA 轮换的专用 runtime |
| SVID 到期（can_reattest=false） | **SKIP** | OpenViking argus_tdx 侧；x509pop 为 true 会重认证 |

收敛语义说明：撤销（entry 删除/ban）停止**新身份签发**，但已签发 SVID 在到期前仍有效。保守的端到端上界应按 **撤销时 SVID 剩余有效期 + 最大连接寿命 + 探测/调度容差** 计算，而不是只写 SVID TTL。单轮 ~208s 仅代表历史运行中重复短请求最终停止，不证明旧活动连接已在 60 秒内排空。

---

## 10. Node Attestation 回归

执行方式：每个场景使用全新、相互隔离的 runtime（不同 Host runtime、Guest root、Guest data、Guest run、attestation key），按 README 的故障注入变量，顺序执行 prepare/start-server/start-openviking-agent。

| 场景 | runtime（Host） | 故障注入 | 结果 | 原始错误（唯一故障源匹配） |
| --- | --- | --- | --- | --- |
| Evidence Provider 503 | `na-provider-503-20260806T033413Z` | `V2_EVIDENCE_STATUS=503` | **fail-closed PASS** | `nodeattestor(argus_tdx): obtain evidence: evidence provider returned HTTP 503` |
| Trustee 503 | `na-trustee-503-20260806T034212Z` | `V2_TRUSTEE_STATUS=503` | **fail-closed PASS** | `nodeattestor(argus_tdx): Trustee verification failed: Trustee returned HTTP 503` |
| Trustee timeout | `na-trustee-timeout-20260806T034945Z` | `V2_TRUSTEE_DELAY=20s` | **fail-closed PASS** | `nodeattestor(argus_tdx): Trustee verification failed: call Trustee: context deadline exceeded` |
| evidence replay | `na-replay-20260806T041512Z` | `V2_REPLAY_EVIDENCE=true` | **fail-closed PASS** | 首次 attestation 成功；强制二次 attestation（新 key+新 challenge）时 provider 重放旧 evidence → `Trustee returned HTTP 422`，agent crashed、容器进入 restart 循环、无有效 workload SVID |

- 4 个故障场景中 `argus_tdx` Agent 均未成功注册（SPIRE agent list 显示 `No attested agents found`，或 re-attestation 被拒）
- 故障场景使用全新隔离 runtime，未通过删除 positive runtime 触发重新认证；未修改已认证 Agent 后再声明重新执行
- 上述回归证明 mock v2 Node Attestation 未被 WP1 破坏；**不证明**真实 Quote/QGS 或正式 Trustee

---

## 11. 运行终态

| 组件 | 状态 | restart policy | 网络 |
| --- | --- | --- | --- |
| argus-v2-mock-trustee | Up | unless-stopped | argus-spire-v2-center_center |
| argus-v2-spire-server | Up | unless-stopped | argus-spire-v2-center_center |
| argus-v2-guard | Up | unless-stopped | argus-spire-v2-center_center |
| argus-v2-openclaw-agent | Up | unless-stopped | argus-spire-v2-center_center |
| argus-v2-openclaw-mtls（egress） | Up | unless-stopped | host（172.31.44.1:1934） |
| Guest: argus-v2-mock-evidence-provider | Up | unless-stopped | host（Guest 127.0.0.1:18080） |
| Guest: argus-v2-openviking-agent | Up | unless-stopped | host |
| Guest: argus-v2-openviking-mtls | Up | unless-stopped | host（Guest 1943） |
| agentcc-openclaw-sbx-gateway（真实 OpenClaw） | Up | unless-stopped | argus-openclaw-egress（172.31.44.2）+ bridge |
| agentcc-openviking-tdx（真实 OpenViking） | Up | - | Guest 1933 |

Guard health 终态: `mode=mock_allow`、`authorization_context_required=true`、`authorization_context_version=argus-authorization-v2`、`decision_ttl_seconds=15`

仍在运行且保留（不属于本轮新 runtime、按要求未停止）：
- m4 遗留 SPIRE server/agent（join token 时代，`/home/ying_liu/agent-cc-argus-spiffe/...`）
- 127.0.0.1:1934 的旧 python3 TCP forwarder
- 上一轮 v2 runtime 数据 `verify-a155ad2-20260804-072045`（Host + Guest）

---

## 12. 代码修复

**有**。修复后均已重跑相关完整验证。

### 修复 1：`core/argus/src/bin/guard.rs` — Guard mock ALLOW 日志字段格式（2 处）
- **根因**：`mock_allow_response` 的 `tracing::warn!` 中 `request_id`、`request_digest`、`decision_id` 字段缺少 `%`（Display）sigil，被 tracing 以 Debug 格式化并在值两侧加引号（`request_id="..."`）；同时 `tracing_subscriber::fmt::init()` 输出 ANSI 颜色码（`\033[0m\033[2m=\033[0m...`）。二者导致 `verify-mtls.sh` / `verify-openclaw-plugin-e2e.sh` 按 `request_id=<value>` 的 grep 无法匹配 guard 日志，破坏"同请求因果日志关联"验收。
- **最小改动**：
  - 将三个字段改为 `%log_request_id` / `%log_request_digest` / `%decision_id`（Display，无引号），并提前计算局部变量；
  - 将 `tracing_subscriber::fmt::init()` 改为 `tracing_subscriber::fmt().with_env_filter(...).with_ansi(false).init()`（纯文本输出）。
- **重跑结果**：`cargo check` PASS；guard 镜像重建；`verify-architecture.sh` 重跑 **PASS**（19 项，因果日志匹配成功）。

### 修复 2：`core/spire/v2/verify-guard-gate-failures.sh` — egress 重建保留 immutable 镜像引用
- **根因**：脚本的 `recreate_egress()` 在重建 egress 容器时未设置 `V2_MTLS_RUNTIME_IMAGE`，compose 回落到默认 `argus-spire-v2-mtls:local`（repo:tag）；SPIRE Docker Workload Attestor 的 `image_id` selector 取自容器 `Config.Image`，导致与 workload entry 的 `docker:image_id:sha256:54f7c4a9...` 不匹配 → egress 反复得到 `No identity issued` → 故障矩阵脚本在 `wait_for_egress_identity` 处挂起（首次运行 10 分钟超时）。
- **最小改动**：`recreate_egress()` 增加 `V2_MTLS_RUNTIME_IMAGE`（`docker image inspect argus-spire-v2-mtls:local --format '{{.Id}}'`）传入 compose，与 `start-openclaw-workload.sh` 行为一致。
- **重跑结果**：`bash -n` PASS；`verify-guard-gate-failures.sh` 重跑 **PASS**（8 场景全部 fail-closed，egress 恢复）。

### 修复 3：`core/spire/v2/mtls-smoke/go.mod` + 新增 `go.sum` — Go 模块完整性
- **根因**：`mtls-smoke` 模块缺 `go.sum` 且 `go.mod` 缺 indirect requires，默认 readonly 模式 `go test ./...` / `go build` 失败（仅 `-mod=mod` 可构建，掩盖问题）。
- **最小改动**：补齐 indirect requires（go-jose、x/net、x/sys、x/text、genproto、grpc、protobuf 均为 go-spiffe v2.8.1 的构建依赖）与 `go.sum`（16 行）。
- **重跑结果**：`go test ./...`（默认 readonly）**PASS**；`gofmt -l` PASS。

**修复文件清单与 diff 摘要**
```
 M core/argus/src/bin/guard.rs            | 31 ++++++++++++---------
 M core/spire/v2/mtls-smoke/go.mod        | 10 ++++++++++
 M core/spire/v2/verify-guard-gate-failures.sh |  7 +++++
 ?? core/spire/v2/mtls-smoke/go.sum       | 16 +++++++++++++++
```
`git diff --check` 通过；未 commit / push。

### WP2 新增组件（数据面旁路收紧）
- 新增 `core/spire/v2/docker-gate/`（`main.go` + `main_test.go` + `go.mod`）：Go 最小 Docker socket proxy，端点白名单 + containers/create 强制校验（拒绝 privileged/host 网络/cap_add/unconfined/任意 host bind/device，仅允许 `openclaw-sandbox:bookworm-slim`），审计日志。
- 新增 `core/spire/v2/start-docker-gate.sh`（构建+运行 proxy，socket GID 与 docker 一致）。
- 新增 `core/spire/v2/repoint-openclaw-socket.sh`（重建网关换接 proxy socket，保留 mounts/env/端口/group_add，按旧网络重连附加网络）。
- 新增 `core/spire/v2/apply-wp2.sh`（幂等应用：proxy → repoint → egress `--internal` 重建 → egress 容器重建）。
- 新增 `core/spire/v2/verify-wp2.sh`（5 项验证）。
- 修改 `core/spire/v2/start-openclaw-workload.sh`（egress `--internal` 创建/重建 + 调用 start-docker-gate/repoint）。

### 环境级修复（非代码改动）：E2E API key 恢复
- **根因**：`connect-openclaw-plugin.sh` 使用 21 字符解码值重新配置插件 apiKey，覆盖了 OpenViking 期望的 105 字符原始值 → 插件写请求 401。
- **修复**：由用户手动执行 `/tmp/fix_ov_key.sh`——从配置备份 `openclaw.json.bak.2026-08-06T02-19-40`（1528 字节，含正确 key）`docker cp` 恢复插件配置并 `chown node:node`、重启网关，同时把正确 105 字符 key 写入 `/root/.argus_openviking_api_key.env`（0600）。
- **重跑结果**：`verify-openclaw-plugin-e2e.sh` **PASS**（见 §9）。

### WP3 审查修复（尚待远程验证）

- `mtls-smoke/main.go`：用绝对 deadline、到期关闭定时器和 deadline 上限替换初版的调用前时间检查；增加 SVID `NotAfter` Unix 时间输出；X509Context watcher 永久退出时使 egress 失败退出。
- `mtls-smoke/main_test.go`：新增阻塞读取到期关闭、清除 read deadline 不能绕过绝对寿命的回归测试。
- `verify-wp3.sh`：删除 entry/ban 的反向 `expect-no-identity` 断言；基于实际 SVID 到期时间验证业务拒绝和明确身份拒绝；使用绝对 SLA deadline；增加失败时恢复 Agent、entry、ban 状态和 egress 的 trap。
- `deploy-v2-guest.sh`：连接生命周期参数作为单个参数安全传递。
- 本段仅记录源码修复，**没有远程 PASS 结论**。

---

## 13. 工作包状态

| 工作包 | 状态 | 说明 |
| --- | --- | --- |
| WP1 Guard 同请求强制门控 | **PASS** | 架构 19 项 PASS + Guard 矩阵 8 场景 PASS + 真实 OpenClaw 插件 E2E PASS（§9）。完整远程验证通过。 |
| WP2 数据面旁路收紧 | **PASS** | Docker 控制面经 `argus-docker-gate` 最小 socket proxy 隔离；egress bridge `--internal`；网关网络身份检查 + 兄弟容器 403 自动化测试；真实 E2E 回归通过（见 §9.1）。 |
| WP3 身份生命周期和拒绝收敛 | **部分完成，修复后待重验** | 历史 E2E 与部分恢复场景有效；绝对连接寿命和负向断言已在源码中修复，但尚无远程验证结果。bundle/trust domain 与 can_reattest=false（OpenViking 侧）仍为 PENDING。见 §9.2。 |
| WP4 持久化克隆检测加固 | **PENDING** | 未实施和验证 |
| WP5 多 runtime 隔离 | **PENDING** | 未实施和验证（本轮仅按 README 顺序串行运行单 runtime） |
| WP6 结构化审计与指标 | **PENDING** | 未实施和验证 |
| WP7 canary、切换与回滚演练 | **PENDING** | 未实施和验证 |
| WP8 真实 RA 接口预留 | **PENDING（仅接口预留）** | 未部署 service evidence endpoint、未启用 `fresh_evidence`；不视为真实 RA 完成 |

---

## 14. 准确结论边界

**可以声称**：

> Argus-SPIFFE v2 已在 mock RA 条件下验证：真实 OpenClaw 业务请求经同步 Guard authorization binding 取得 `mock_allow` receipt 后，才由 OpenClaw SVID 经 SPIFFE mTLS 转发至 OpenViking；Guard DENY、503、timeout、malformed response、receipt 缺失、digest mismatch 和 receipt 过期均 fail-closed，且故障请求无下游 `forwarded_mtls`。双 Agent（x509pop + argus_tdx）v2 Node Attestation 在 Provider 503 / Trustee 503 / Trustee timeout / evidence replay 故障下均 fail-closed。真实 OpenClaw 插件 marker 请求跨 Guard、egress 与 OpenViking session 完成同请求因果关联，session 写入、commit 与 archive 均成功。WP2 数据面收紧后，OpenClaw 网关不再直接控制 Docker daemon（经 `argus-docker-gate` 最小白名单 proxy，privileged/host 网络/任意 host mount 均被拒绝），egress bridge 为 `--internal` 且仅网关可挂载、兄弟容器被 source-IP 拒绝，真实 E2E 仍通过。

**不得声称**：
- ❌ 已经完成真实 TDX 远程证明
- ❌ 已经接入真实 Quote/QGS
- ❌ 已经接入正式 Trustee
- ❌ 已经达到生产安全
- ❌ 整份 Pre-RA Hardening 计划已经完成（WP3 仅部分完成、WP4–WP8 仍为 PENDING）

---

## 附：终端结论摘要

- WP1: **PASS**（架构 PASS + Guard 矩阵 PASS + E2E PASS）
- 双 Agent v2: **PASS**
- Guard failure matrix: **PASS**
- Real OpenClaw plugin E2E: **PASS**
- Node Attestation regression: **PASS**
- **WP2 数据面旁路收紧: PASS**（Docker socket proxy + egress `--internal` + 兄弟容器 403 + 回归全过）
- **WP3 身份生命周期: 部分完成，修复后待重验**（历史 ~208s 仅为行为观测；绝对连接寿命、entry/ban 拒绝和失败恢复需使用修复版脚本重跑）
- WP4-WP8: **PENDING**
- Real Quote/QGS: **DEFERRED**
- Production Trustee: **DEFERRED**
- 报告: `cczoo/agent-cc/documents_ly/archive/argus-spiffe-v2/Argus-SPIFFE-v2-Pre-RA-Hardening-Remote-Verification-Report.md`
- 是否修改代码: **是**（WP1、WP2 已远程验证；WP3 审查修复尚待远程验证，详见 §12）
