# OpenClaw → OpenViking 原生 SPIFFE mTLS 接入

本轮完整部署入口：[IP1 TDVM 操作手册](DEPLOY-IP1-TDVM.md)。新 TDVM 内使用独立 Agent/Broker 和 OpenClaw 容器，通过普通 `x509pop` 节点准入取得身份；OpenClaw 的 TDX 远程证明暂为 NOT_RUN。`deploy.py` 生成配置并执行分角色的安装、Entry 检查和真实 PID 登记；`build_delivery.py` 生成可校验的源码与二进制交付包。

公司主机执行提示：[IP1（含新 Guest）](PROMPT-IP1.md)、[IP2（现有服务）](PROMPT-IP2.md)。先返回 IP2 的接入交接信息，再由 IP1 完成业务联调，最后按 request/session ID 汇总服务端证据。Git 仅交付源码，`dist` 产物需按操作手册构建或从交付包校验取得。

本目录实现 OpenClaw 插件直接访问 OpenViking 的 NGINX HTTPS 入口。TLS 客户端位于 OpenClaw Gateway 进程内，使用 `spiffe://argus.local/agent/openclaw`；服务端固定验证 `spiffe://argus.local/service/openviking-cmem`。业务仍使用 OpenViking 的非 root 用户 API key。

```text
OpenClaw 本机 SPIRE Agent / Broker
    → spiffe-client-credentials（引用真实 Gateway PID，交付目标 SVID）
    → 受保护 tmpfs 凭据代次与短期租约
    → OpenClaw 插件的原生 HTTPS 客户端
    → NGINX :1943 → OpenViking 回环 :1933
```

`spiffe-client-credentials` 是凭据交付进程，没有 HTTP 业务监听器、不调用 Guard/Trustee、不签发身份。它使用独立 Helper 身份调用本机 Broker，由 SPIRE 根据 OpenClaw 自己的注册与 workload selectors 交付目标 SVID。原 OpenViking Helper 的目标登记合同保持原样。本次不新增 Rekor 验证。

## 版本与源码

- SPIRE Agent/Server 继续使用官方 **v1.15.3**；新增命令位于现有 SPIFFE Helper **v0.11.0** 定制源码中，复用其 `go-spiffe/v2 v2.8.1` 与快照验证。
- 插件基于 npm 正式发布包 `@openviking/openclaw-plugin@2026.6.18`，定制标识为 `argus.2`。`upstream.lock.json` 固定上游 SHA-512、运行依赖和版本要求。
- 当前仓库 Dockerfile 原为 `openclaw:latest`，连接脚本原为未锁版本的 ClawHub 安装；不能由此确定公司当前版本。历史报告记录 OpenClaw `2026.6.11`、OpenViking `v0.4.8`，均满足该发布包声明的最低要求（OpenClaw `2026.4.8`、OpenViking `0.4.1`），但这不代替本次实际部署验证。原镜像内容摘要和历史报告没有被改写。
- Node.js 最低 **22.17.0**。安装前实际检查 Gateway 的 Node/OpenClaw 版本。

`build_plugin.py` 校验上游包后，修改发布包的 `HttpTransport`、独立的 `setup` 请求和对应 TypeScript 源码。`argus.2` 还记录 ContextEngine 的召回输入/输出与 mTLS 请求关联，仅输出合成验收事实的摘要，不修改模型输入。增加的源文件全部位于 `lib/`，不修改全局 `fetch`。输出包保留上游版本号以兼容其版本解析器，通过 `package.json.argusSpiffe`、`ARGUS-UPSTREAM.json` 和整个包的 SHA-256 区分定制版本。保留上游包及其声明的许可信息。

```bash
python3 build_plugin.py
# 离线构建同样检查摘要：
python3 build_plugin.py --upstream /path/to/openclaw-plugin-2026.6.18.tgz
```

输出 `dist/openviking-openclaw-plugin-2026.6.18-argus.2.tgz` 及同名 `.json` 摘要记录。相同输入产生相同字节的安装包。公司环境部署包与记录一起传递。

## 公司环境前提

1. OpenViking 已按 [Workload 运行手册](../../../core/spire/workload/README.md)部署，NGINX 1943 从 OpenClaw 容器可达；双方信任同一 `argus.local` CA/bundle。
2. OpenClaw 所在主机有自己的 SPIRE Agent、Workload API 和 Broker UDS，实际版本为 v1.15.3。Agent 必须能识别宿主机上的 Gateway PID。不要把 OpenViking 的固定 Agent ID 当成 OpenClaw Agent ID。
3. OpenClaw 的目标 Entry 已由公司批准，固定 parent Agent，并包含该工作负载的批准 selectors。此次不把 Docker label/image selector 升格宣称为 TDX 证明；已有自定义认证条件应保留。
4. 给凭据交付进程注册独立身份 `spiffe://argus.local/infra/openclaw-helper`，只允许获准进程使用；在该 Agent 的 Broker allowlist 添加这个身份和 `WorkloadPIDReference`。不得把 Helper 自身 SVID 当作 OpenClaw SVID。

Broker 配置片段需合并到 **OpenClaw Agent** 的既有配置，保留已有节点准入及插件设置：

```hcl
agent {
  experimental {
    broker {
      socket_path = "/run/spire/openclaw-broker/broker.sock"
      brokers = [{
        id = "spiffe://argus.local/infra/openclaw-helper"
        allowed_reference_types = [{ type_url = "type.googleapis.com/spiffe.broker.WorkloadPIDReference" }]
      }]
    }
  }
}
```

如果需要新建客户端 Entry，使用公司批准的实际 parent/进程 selectors；Docker 内容标识应使用官方 `docker:image_config_digest`，不要编造不存在的 `docker:container_id` selector。本目录不自动创建较弱的同身份 Entry。签发授权仍由 SPIRE 管理。

## 凭据交付部署

完整 Workload `build.sh` 已包含新命令；也可以在 Linux 单独构建：

```bash
cd core/spire/helpers/spiffe-helper
go build -mod=readonly -trimpath -o /opt/argus-workload/bin/spiffe-client-credentials ./cmd/spiffe-client-credentials
```

复制并填写 `config/credentials.example.json` 为 `/etc/argus-openclaw/credentials.json`，填写真实 OpenClaw Agent ID、UDS 路径、宿主机映射后的客户端读取 GID。复制 `config/client.example.json` 为同目录 `client.json`，填写实际可达 HTTPS origin。`credentials.json` 只给 root 读取；`client.json` 可由 OpenClaw 读取，均不可由 group/others 修改。

建立专用 `argus-openclaw` 组，GID 必须与 `reader_gid` 及 Gateway 的宿主机读取身份一致。示例使用 20000；普通 Docker 可通过 `--group-add 20000` 给 Gateway 增加读取组，启用 user namespace 时需换算实际宿主机 GID。运行目录 `/run/argus-openclaw`、`/run/argus-openclaw/credentials` 均为 root 所有、该组可读/遍历、0750，后者必须位于 tmpfs。把示例 unit 安装到 systemd；根据公司实际安装路径核对 `ExecStartPre` 的官方 Agent 路径。另行用 Agent unit 的 `/proc/<MainPID>/exe -version` 核对实际运行版本，不能只检查磁盘上待运行的二进制。

Gateway 容器需要两个只读挂载，保留现有其他运行参数：

```text
/etc/argus-openclaw/client.json → /etc/argus-openclaw/client.json : ro
/run/argus-openclaw/credentials → /run/argus-openclaw/credentials : ro
```

只挂载客户端配置文件，不把包含宿主机 Broker 配置的整个 `/etc/argus-openclaw` 目录挂入业务容器。SPIRE/Broker socket 不挂给 Gateway。私钥供获准 Gateway 进程使用；凭据目录及父目录的组读取权限需匹配 Docker user namespace 的实际映射。传输模块默认读取 `/etc/argus-openclaw/client.json`；使用其他位置时，要在 Gateway 的持久环境中设置 `OPENVIKING_SPIFFE_CONFIG`，仅对 `docker exec` 设置环境变量不生效于 Gateway。

## 安装、登记和业务验收

以下命令在公司 OpenClaw 主机执行。导出既有 `OPENVIKING_API_KEY`，不替换公司的用户凭据。

1. 用现有容器创建方式加入上述挂载，启动 Gateway。运行 `bash ../scripts/connect_openclaw_openviking.sh install` 安装已校验摘要的包；该阶段不要求目标 SVID 就绪。`--force` 用于明确替换原插件来源，不绕过 OpenClaw 的安装策略。
2. 按现有运维方式重新启动 Gateway，使其加载新插件。通过 `docker top <container> -eo pid,args` 确认 **实际 Gateway 进程的宿主机 PID**；容器 init、shell、supervisor 和临时 `docker exec` 进程都不能代替它。
3. 用确认过的 PID 登记并启动交付进程：

```bash
/opt/argus-workload/bin/spiffe-client-credentials \
  -config /etc/argus-openclaw/credentials.json -register-pid ACTUAL_GATEWAY_HOST_PID
systemctl daemon-reload
systemctl start argus-openclaw-credentials
journalctl -u argus-openclaw-credentials -n 30 --no-pager
```

登记文件绑定 PID、启动时间、boot ID 和 PID namespace；启动时验证并打开 pidfd。已存在的登记不覆盖。交付进程拿到的是 PID 引用所对应的 **OpenClaw** SVID，同时验证完整快照、唯一身份、证书链、用途和密钥配对。

4. 执行 `bash ../scripts/connect_openclaw_openviking.sh connect`。它检查只读挂载、持久环境、版本、安装包标识，使用包内客户端探测 `/health`，然后配置并检查插件。`TARGET_URI` 从受控客户端配置推导，不再使用旧 egress 默认值。必要时可显式设置 `OPENCLAW_PLUGIN_DIR` 为容器内实际插件安装目录。
5. 如果 setup 引发 Gateway 进程替换，或还需重启以应用配置，先停止交付 unit、删除旧的 `/run/argus-openclaw/target.json`，核对新 PID 后重新登记、启动。不得保留旧 PID 登记冒充新实例。连接脚本不再自动 `docker restart` 后声称身份仍有效。
6. 执行 `bash ../scripts/verify_openclaw_plugin_e2e.sh`。它发起带唯一标识的真实 OpenClaw 会话，在 OpenViking 中找到内容，用安装包中的真实 `OpenVikingClient` 读回，完成 commit/archive，并从 **Gateway 容器日志** 找到该 session 的原生 mTLS POST 记录。独立探针或 `docker exec` CLI 的成功不能替代 Gateway 写入记录。

可用 `OPENCLAW_CONTAINER`、`OPENCLAW_USER`、`OPENCLAW_CONFIG_PATH` 选择实际容器；`OPENVIKING_REQUIRE_READY=1` 额外通过 mTLS 检查 `/ready`。两个阶段都要求非 root 用户 API key。该 key 仍由上游 setup 保存；调用方式沿用现有 CLI，不把它写入 TLS 审计记录。

## 轮换与失效合同

- 每个新快照先写完整不可变代次，再原子发布 `ready.json`。租约每 500 ms 更新，最长 2.5 秒；内容包含代次、十六进制证书序列号、证书链最早失效时间。
- 客户端每次请求以及每 250 ms 核对租约；不完整代次、错误身份/密钥、移除、过期、租约失效均清理连接并拒绝请求。交付进程被 SIGKILL/SIGSTOP 后，旧租约最多约 2.75 秒加调度延迟失效。
- 轮换时新请求改用新代次；旧连接最多保留 5 秒供在途请求结束，且不超过双方叶证书/客户端链的失效时间。不会自动重试业务写入。中断后的业务结果需要按 session/request ID 查询。
- TLS session resumption 关闭；HTTP 重定向、HTTP 明文和其他 origin 不会使用客户端身份。标准证书链验证始终开启，SPIFFE URI 校验替代 DNS SAN 身份匹配。
- 完整快照移除、已检测到的 Broker 断连或目标退出使交付进程退出并清理 PEM；systemd 的 ExecStopPost 覆盖 SIGKILL 后清理。已移除 10 秒 gRPC keepalive，改为每 5 秒建立 3 秒 deadline 的新 Broker 订阅验证首个快照，失败即取消主订阅。计入本地 lease 和客户端检查后，Broker 无响应的验收目标为 12 秒；删除 SPIRE Entry 的控制面传播时间需独立记录。
- 普通证书轮换不算新的 TDX 证明。订阅是否触发哪种证明取决于 OpenClaw Agent 上实际配置的 WorkloadAttestor，不能沿用 OpenViking 的 Quote 结论。

停止：`systemctl stop argus-openclaw-credentials`。状态：unit 日志、Gateway `argus-openclaw-spiffe` JSON 日志、包内 `dist/argus-spiffe/cli.mjs check/health`。审计记录只含请求 ID、路径（无 query/body）、双方 SPIFFE ID、证书序列号、代次及 HTTP 状态。

## 测试和待执行验收

`bash test-client.sh` 构建并验证固定插件，执行真实 TLS 的 Node 测试、发布包真实客户端/setup 测试、摘要及 Gateway 审计反例、Go 交付/快照测试、Go vet 和 shell 语法检查。可用 `ARGUS_TEST_UPSTREAM=/path/to/package.tgz` 离线复用上游包；npm 运行依赖也必须预先可用。

本地执行结果见 [VALIDATION.md](VALIDATION.md)。公司另需验证真实 Agent/Broker 身份签发、root/tmpfs/GID/user-namespace 权限、systemd、Gateway 重启后新 PID、真实 OpenViking 写入/读取/归档、双端轮换、错误客户端及 SIGKILL/SIGSTOP/网络故障。普通 HTTPS 测试服务不代表真实 TDX 或公司业务验收。

依据：[SPIFFE X.509-SVID](https://github.com/spiffe/spiffe/blob/main/standards/X509-SVID.md)、[SPIRE v1.15.3 Docker selectors](https://github.com/spiffe/spire/blob/v1.15.3/doc/plugin_agent_workloadattestor_docker.md)、[OpenClaw 插件安装](https://docs.openclaw.ai/cli/plugins)。
