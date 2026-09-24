# Trustee 的 TruCon 日志验证接入

`argus_trucon.rs` 接入真实 Attestation Service：先完成 TDX Quote 与 REPORTDATA 校验，再把同一请求的 runtime_data、已认证的 RTMR2 和 Rekor 引用列表交给服务端本地验证器，最后才执行 Rego、签发 EAR。引用可以是 UUID 或数字索引；客户端不能传入验证结果或 Rekor 地址。

`verify_trucon.py` 使用项目原有 `tlog.digest` 算法和固定的 Sigstore 依赖。验证器属于 Trustee 的受信部署，按请求启动本地进程，没有新增网络服务。失败、超时、缺配置或缺材料直接拒绝。

## 构建和部署

仓库不包含上游 Trustee 源码。本接入针对 [Trustee v0.21.0](https://github.com/confidential-containers/trustee/tree/v0.21.0)，`apply.py` 另外检查已审查的 AS 源文件 SHA-256，拒绝套用到不同源码。

在满足上游 TDX/DCAP 构建依赖的 Linux 主机上执行：

```bash
git clone --branch v0.21.0 --depth 1 https://github.com/confidential-containers/trustee.git /srv/trustee-trucon
bash core/spire/workload/trustee/build.sh /srv/trustee-trucon
sudo bash core/spire/workload/trustee/install-verifier.sh /opt/argus-trucon
```

将生成的 `restful-as` 纳入现有 Trustee 部署，保留 TLS、DCAP、EAR 签名和 policy 存储配置。在同一服务环境中配置：

```ini
Environment=ARGUS_TRUCON_VERIFIER=/opt/argus-trucon/verify-trucon
Environment=ARGUS_TRUCON_CONFIG=/etc/argus-trucon/config.json
```

按 [config.example.json](config.example.json) 填写真正的信任材料。配置、验证器、Python 环境及其父目录由管理员控制；配置和启动文件要求 root 所有且组/其他用户不可写。

- `rekor_url` 固定 HTTPS origin；禁止重定向和从请求选择日志服务。
- `https_proxy` 可选，仅接受管理员配置的无凭据 `http://host:port` CONNECT 代理；Rekor 仍使用端到端 HTTPS、系统 CA 和固定 hostname 校验。未配置时禁止继承进程代理环境。
- `rekor_public_key_path` 来自预先批准的 Rekor 信任材料，不能把本次查询返回的任意公钥自动设为受信。
- `sigstore_trusted_root_path` 使用批准的 Sigstore trust root；明确设置 TC-API 的 OIDC subject 和 issuer。验证包含 Fulcio 证书链、CT、有效期和签名时间。
- `allowed_baseline_rtmr` 是批准的启动基准，必须与 workload policy 的 `rtmr2_baseline` 对齐。
- 私有记录者也可用 `init_public_key_paths` 固定初始化记录者公钥；初始化记录认证后才信任其中的 P-384 chain owner。后续记录必须有该 owner 对事件摘要和前驱链接的授权。

通过新生成的 workload policy 发布准入规则。保留原有 runtime_data 协议和身份流程；配置字段从 `approved.rtmr_2` 改为 `approved.rtmr2_baseline`，并新增 `trucon_socket_path`。已有人工批准的 policy artifact 需要重新审查、重新计算摘要，不能继续使用旧的固定 RTMR2 策略。

## 日志材料要求

TC-API 使用 `intoto` 上传，Rekor 必须启用 attestation storage，并能按 UUID 或数字索引返回包含 `attestation.data` 的条目。引用只用于定位材料，验证器核对返回 UUID、索引与原始 Merkle leaf、收录路径、签名 checkpoint、签名时间戳、payload hash、DSSE 签名、事件摘要及完整前驱链。仅有 payload hash、本地缓存或待上传状态不能准入。

Provider 复用 `GET /chain-state?include_history=true`：TruCon 在一次有界数据库读取中取得所有状态的记录，拒绝缺项或尚未确认的历史，从同一条末尾记录返回链头、序号和度量值，并保留原始 `log_ids`。Provider、Workload 插件与 Trustee 使用 `rekor_entry_ids` 传递这些引用。更新时需一起重新构建这些组件；runtime_data 与 REPORTDATA 的绑定格式未改变。

初始化记录只提供批准基准，不执行 extend；build 记录按 TruCon 的现有规则不 extend，但仍须在完整签名链中。其余事件依次重放。只有重放结果等于 Quote.RTMR2，且目标成功启动记录与 runtime_data 的 workload、launch、container、实际镜像对应，才生成 `tdx.trucon.verified=true`。目标启动后的已知成功 stop/rm 记录使该启动失效，随后重新 start 或登记不能恢复旧启动记录的效力。

Docktap 在转发 start/stop/rm 前通过原始 Docker socket 查询完整容器 ID，转发请求和签名日志使用同一个 ID；查询失败则拒绝操作。删除前完成查询，避免删除后无法确认对象。目标启动后的旧成功 stop/rm 日志若只有短 ID、名称或互相冲突的实例字段，也拒绝准入；不能猜测对象、忽略记录或改写已签名历史。需要在材料明确的新启动记录之后重新申请准入。

Fulcio 路径使用固定 Sigstore 版本的证书验证器，直接提供原始 `intoto` 条目、证书和签名，不经过不支持 `intoto` 的 `Bundle.from_parts` 转换。证书链、CT、固定 subject/issuer、Rekor 签名时间和 DSSE 校验均保留。

新上传与旧队列均可保留 UUID 或数字索引，无需迁移数据库即可认证。若运维需要统一引用格式，下面的工具是可选的：先备份 SQLite，再检查差异。

```bash
python3 core/tc_api/scripts/backfill_attestation_uuids.py --db /dev/shm/commit_queue.db --rekor-url https://rekor.sigstore.dev
# 核对输出后，同一命令加 --apply。只转换引用，不改变 payload、事件摘要或 RTMR。
```

旧记录若是无法返回正文的 `dsse` 条目，转换 UUID 也不能补齐材料；必须在新的受信启动周期按可提供正文的配置生成链。不能把已变化的当前 RTMR2 自动批准为启动基准。

每次最多 4096 条引用，每条远端响应最多 4 MiB。Provider 最多尝试 3 次固定快照；服务端日志验证总时限 50 秒、最多 4 个并发验证进程。超出限制拒绝，不裁剪链。部署配置 `request_timeout_seconds` 默认 55 秒，可设为 51—60 秒，保证客户端不会先于日志验证预算超时。Helper 启动预算为两次请求预算加 10 秒，覆盖顺序执行的取证、Trustee 验证及首次凭据发布；部署脚本再留 5 秒。默认分别为 120 秒和 125 秒。升级后需重新渲染 Agent/Helper 配置，旧的 20 秒插件配置会拒绝加载。检查点验证用于本次收录，不声称实现跨检查点的全局一致性审计。

## 验证

```bash
PYTHONPATH=core/tlog python3 -m pytest core/spire/workload/trustee -q
PYTHONPATH=core/tlog:core/tc_api python3 -m pytest core/tc_api/tests/test_attestation_snapshot.py -q
```

密码学测试生成真实签名、Merkle 路径和检查点，并用本地测试 CA、CT 密钥和有效 SCT 执行完整 Fulcio 证书验证路径，替换的只有远端查询边界。测试不代表真实 TDX/DCAP、在线 Rekor/公开 Fulcio 服务、Trustee EAR、SVID 或业务验收；这些结果单独记录在 [VALIDATION.md](../VALIDATION.md)。
