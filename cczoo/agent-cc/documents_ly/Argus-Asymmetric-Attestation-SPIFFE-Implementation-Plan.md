# Argus 非对称 Attestation-backed SPIFFE 实施与远程验证方案

> 对应架构：[Argus-Asymmetric-Attestation-SPIFFE-Architecture.md](./Argus-Asymmetric-Attestation-SPIFFE-Architecture.md)
>
> 源码状态：完整实现已落入本地仓库，尚未在本机执行构建或测试
>
> 下一 Gate：用户同步仓库后，在远程主机完成构建、实验和结果记录
>
> Real Quote/QGS/production Trustee：Deferred

## 1. 冻结 Profile

| 项目 | 当前值 |
|---|---|
| Trust domain | `argus.local` |
| OpenClaw Agent | `x509pop`，无 Evidence Provider |
| OpenClaw workload ID | `spiffe://argus.local/agent/openclaw` |
| OpenViking Agent | `argus_tdx`，Evidence Provider 位于 TD workload 侧 |
| OpenViking workload ID | `spiffe://argus.local/service/openviking-cmem` |
| Guard | caller-local `spiffe_identity` |
| 数据路径 | OpenClaw 进程内 Guard gate -> direct SPIFFE HTTPS -> OpenViking 原生 ASGI server |
| 当前 Attestation | Mock Evidence Provider + Mock Trustee |

“非对称”只指远程证明：只有 OpenViking 的 Agent 通过 `argus_tdx`。两侧仍有独立
SPIRE Agent、Workload API 和 workload SVID，业务链路使用双向 mTLS。

本阶段不加入 OpenClaw Evidence Provider、OpenViking Guard、每请求 Quote、TLS
exporter/body hash/receipt 绑定、不可绕过 PEP、service mesh 或“OpenClaw 已失陷”防护。

## 2. 已完成的源码实现

### 2.1 SPIRE 结构重排

```text
core/spire/
  components/       svid-materializer
  plugins/          argus-tdx-nodeattestor
  benchmarks/       非对称 runtime 与 Agent Task 评测工具
  runtime/
    asymmetric/     正式 Compose、配置、启动与远程验收脚本
  tests/            nodeattestor-mock、tdvm
```

正式数据路径不引用独立 mTLS proxy 或 Docker-gate。旧命令 wrapper、proxy-era
WP2/WP3、`mtls-diagnostic` 与 `docker-gate` 已在 native asymmetric 路径完成远程
验证后删除；实现及当时的验证证据由 Git 历史和归档报告保留。

### 2.2 Rust Guard

新增：

- `GUARD_MODE=spiffe_identity`；
- `POST /guard/v1/authorize`；
- strict YAML policy、trust-domain/SPIFFE ID/HTTPS origin 校验；
- caller、target、service、origin、operation 和 data class 精确匹配；
- 1–300 秒 decision TTL、`decision_id`、`policy_id` 和 `rule_id`；
- policy mismatch 返回明确 `DENY`，配置错误启动失败；
- identity mode 默认 loopback、无 CORS；Compose 只放到内部 control network。

旧 `/ra/v1/verify` 和 Evidence/Mock 模式保留兼容，但 identity mode 不会误走旧接口。
Guard 不接收 certificate、SVID 时间、Quote、TLS exporter、HTTP path 或 body hash。

### 2.3 SVID materializer

新增通用 Go helper：

- watch `Workload API X509Context`；
- 只选择命令行指定的精确 SPIFFE ID；
- 原子发布 cert chain、PKCS#8 key、bundle 和 status；
- key mode 为 `0600`，目录为 `0700`，拒绝 symlink output；
- watcher 或 materialization 永久失败时退出，由 workload supervisor 使容器 fail-closed。

它不监听端口，不解析或转发业务流量，因此不是新的 Transport Proxy。

### 2.4 OpenClaw 原生链路

OpenClaw 镜像内新增进程内 fetch preload：

1. 只拦截固定 OpenViking HTTPS origin；其他 fetch 保持原行为。
2. body 发出前调用 caller-local Guard。
3. DENY、timeout、非 2xx、malformed response 或过期 decision 全部抛错。
4. ALLOW 后直接加载 OpenClaw SVID，使用 Undici Agent 发起 mTLS。
5. CA 链验证后，额外要求 server certificate 只有一个 URI SAN，且精确等于 OpenViking ID。
6. 禁止 HTTP redirect；凭据文件轮换时关闭旧 dispatcher 并加载新 context。
7. materializer 或 Gateway 任一退出时，容器 supervisor 停止另一进程。

这不需要修改 OpenViking 官方插件的业务协议：插件仍使用原生 HTTP API，preload 位于
同一个 OpenClaw Node 进程中。

### 2.5 OpenViking 原生链路

基于固定 OpenViking v0.4.8 API application 增加原生 Uvicorn bootstrap：

- TLS context 使用 OpenViking workload SVID 和 trust bundle；
- `CERT_REQUIRED`，最低 TLS 1.2；
- 在 HTTP parsing 前检查唯一、精确的 OpenClaw URI SAN；
- SNI/new-handshake 时加载轮换后的 cert/key/bundle；
- 保持 OpenViking 原路由、请求格式、User API key 和应用权限；
- 原生 profile 只发布 1943，不发布 plaintext 1933；
- materializer 或 OpenViking server 任一退出时容器停止。

OpenViking 侧没有 Argus Guard。TLS workload authentication 之后仍由 OpenViking 自己做
API-key/application authorization。

### 2.6 部署、迁移和验收脚本

- workload registration 改为绑定真实 `openclaw-sbx` 和 OpenViking image digest；
- `prepare.sh` 生成 Guard policy，并构建真实 Guard/OpenClaw workload image；
- OpenViking launcher 支持 `build` / `launch` 两阶段，先固定 image digest 再注册；
- `start-openclaw-workload.sh` 直接启动带 Workload API mount 的真实 OpenClaw；
- `remote-test.sh` 统一执行远程 unit/integration/business E2E。

## 3. 远程执行顺序

所有以下命令均由用户同步仓库后在远程环境执行；Codex 不从本机 SSH。

### 3.1 准备控制面与双 Agent

```bash
cd cczoo/agent-cc
export V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001
export V2_OPENVIKING_ORIGIN=https://openviking.argus.local:1943

sudo env \
  V2_RUNTIME_DIR="$V2_RUNTIME_DIR" \
  V2_OPENVIKING_ORIGIN="$V2_OPENVIKING_ORIGIN" \
  bash core/spire/runtime/asymmetric/scripts/prepare.sh
bash core/spire/runtime/asymmetric/scripts/start-server.sh
bash core/spire/runtime/asymmetric/scripts/start-openclaw-agent.sh
bash core/spire/runtime/asymmetric/scripts/start-openviking-agent.sh
```

### 3.2 OpenViking build -> registration -> launch

在 TD Guest 构建并推送镜像，但暂不启动 workload：

```bash
export OPENVIKING_SPIFFE_ENABLED=1
export OPENVIKING_SPIFFE_WORKLOAD_API_DIR=/run/argus-spire-v2/openviking
export OPENVIKING_LAUNCH_ACTION=build
bash adapters/OpenViking/scripts/launch_openviking.sh
```

回到验证 Host 注册两个真实 image digest：

```bash
bash core/spire/runtime/asymmetric/scripts/register-workloads.sh
```

在 TD Guest 启动已经注册的 OpenViking workload：

```bash
export OPENVIKING_LAUNCH_ACTION=launch
bash adapters/OpenViking/scripts/launch_openviking.sh
```

### 3.3 OpenClaw 与插件

```bash
export V2_OPENVIKING_HOST_ADDRESS=host-gateway   # 或实际 TDVM 地址
bash core/spire/runtime/asymmetric/scripts/start-openclaw-workload.sh

export OPENVIKING_API_KEY='<non-root OpenViking user key>'
bash core/spire/runtime/asymmetric/scripts/connect-openclaw-plugin.sh
```

## 4. 远程验证矩阵

### 4.1 完整入口

```bash
OPENVIKING_API_KEY='<non-root key>' \
  bash core/spire/runtime/asymmetric/scripts/remote-test.sh all
```

`all` 依次执行：

1. `unit`：Rust Guard、NodeAttestor、SVID materializer、OpenClaw transport、
   OpenViking exact-ID helper 和 compatibility components；
2. `attestation`：隔离的 `argus_tdx` 准入、replay、Evidence Provider、Trustee 和
   timeout 矩阵；
3. `integration`：正式非对称 profile 的 identity、Guard、native mTLS 与业务 E2E。

隔离 attestation stack 默认使用 host metrics 端口 29988/29989，不与正式 profile
的 19988 冲突。也可以分别执行：

```bash
bash core/spire/runtime/asymmetric/scripts/remote-test.sh unit
bash core/spire/runtime/asymmetric/scripts/remote-test.sh attestation
```

### 4.2 仅 Integration/business

```bash
OPENVIKING_API_KEY='<non-root key>' \
  bash core/spire/runtime/asymmetric/scripts/remote-test.sh integration
```

| 场景 | 必须结果 |
|---|---|
| Mock Trustee ALLOW | OpenViking Agent 准入，workload SVID 成功 |
| Mock Trustee DENY / Evidence 无效 | Agent 不准入，无目标 SVID |
| Guard exact policy match | `ALLOW` |
| wrong target / Guard DENY | body 不进入 OpenViking fetch |
| Guard malformed / 503 / timeout / outage | fail-closed |
| correct client/server SVID | OpenViking health/ready 成功 |
| missing client cert | TLS 失败 |
| OpenViking SVID 作为 client | exact-ID gate 在 HTTP 前关闭连接 |
| real OpenClaw turn | OpenViking 捕获唯一 marker |
| commit/archive | session commit count 和 archive overview 完成 |

### 4.3 结果记录

远程报告至少记录：

- commit 与 `git status`；
- `V2_RUNTIME_DIR`、TDVM 地址、镜像 config digest；
- Guard policy ID 与 target origin；
- 每条命令 exit code 和失败原文；
- SVID ID/NotBefore/NotAfter（不记录 private key）；
- Mock RA 与 Real RA 边界。

## 5. 当前完成判定

现在可以确认的是“源码实现已完成并已做静态引用核对”。尚不能确认：

- Rust/Go/Node/Python 能在远程依赖环境中成功构建；
- Mock Node Attestation 回归通过；
- native mTLS 和 business E2E 通过；
- 生命周期/failure matrix 的实际时序满足预期。

只有第 4 节远程矩阵完成后，当前 Mock-RA Profile 才能标记为验证完成。即使全部通过，
仍不能声明真实 Quote、QGS 或 production Trustee 已完成，也不能把 SVID rotation 表述为
新的 TDX remote attestation。

## 6. Real RA 后续（Deferred）

后续单独替换 Mock Evidence Provider/Trustee，并验证真实 Quote/QGS、challenge 与
attestation-key binding、TCB/measurement policy、replay 和 Trustee 故障。该工作不改变
当前运行时 Guard + SPIFFE identity 复用证明结果的基本模型。

## 7. 参考

- [非对称架构](./Argus-Asymmetric-Attestation-SPIFFE-Architecture.md)
- [SPIRE asymmetric runtime](../core/spire/runtime/asymmetric/README.md)
- [`argus_tdx` protocol](../core/spire/plugins/argus-tdx-nodeattestor/proto/argus/spire/nodeattestor/v1/nodeattestor.proto)
- [Rust Guard identity policy](../core/argus/src/spiffe_guard.rs)
- [OpenClaw SPIFFE transport](../adapters/OpenClaw/spiffe-transport/preload.mjs)
- [OpenViking native SPIFFE server](../adapters/OpenViking/spiffe_server/server.py)
