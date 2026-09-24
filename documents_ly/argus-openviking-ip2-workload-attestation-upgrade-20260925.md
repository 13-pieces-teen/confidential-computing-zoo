# IP2 OpenViking Workload 认证升级与验证总结（2026-09-24/25）

- 目标提交：`6ffc30ddbfc69a0fb530944e7d020f7f830e0d27`（IP1 在 `9a382735` 后代提交，仅新增 IP1 侧 verifier 代理支持；IP2 保持 `6ffc30dd`）
- 构建产物：`/root/argus-delivery/workload-6ffc30dd`（12 项 SHA256SUMS 复核通过，复用未重建 Rust）
- IP2：TDX TDVM Agent-CC（172.31.28.53）；IP1：SPIRE Server + Trustee（控制面经 IP1 发起隧道：SPIRE 127.0.0.1:18084、Trustee 127.0.0.1:18443、受限 server-check ssh alias `argus-ip1-control`）
- 交付报告：`/root/argus-ip2-handoff/20260924T143000+0800-ip2-new-init-chain/`（PHASE1-DELIVERABLE / PHASE2-FINAL 及各阶段 STATUS、evidence/）

## 1. 目标

旧日志链缺少 sequence 1（历史条目 2755707897 为缺正文的 dsse），无法直接用于新准入。按两阶段执行：

- **Phase 1**：建立可验证的新初始化链（chain.init = Rekor sequence 1），复用当前受信启动周期；
- **Phase 2**：按 IP1 最终策略与配置完成真实 Workload 准入（launch → register → preflight → start → status → verify），保持 PoC 例外范围不变。

## 2. Phase 1：新初始化链

### 2.1 受信启动周期判定（复用成立）

- 启动：2026-09-24 ~10:58 +08，boot_id `c379b316-287e-4c6b-821b-63ae5a51c45e`。
- 实测 RTMR0/1 与批准值一致；RTMR3 全零；TruCon/TC API/Docktap 未运行、队列随重启清空。
- **决定性证据**：CCEL 事件日志（13048 used bytes，90 条引导事件，终止于 Exit Boot Services Return）从零重放，CCEL register1/2/3 分别与 TDREPORT RTMR0/1/2 逐字节一致。即 RTMR2 = `50a59971…c26c6b5` 是本次引导的确定性测量值（与 9-18 历史初始化基线候选一致，但由 TruCon 实时读取，非伪造/拼接），引导后无任何应用侧 RTMR2 扩展。

### 2.2 两阶段 init-chain

按仓库既有流程（TruCon 单独启动，队列为空，无自动初始化抢占）：

1. `GET /init-chain/default/baseline`：rtmr_value=`50a59971…`、ccel_digest=`sha384:a9f3e1fb…`、init_token（TOCTOU 绑定）。
2. `POST /commit-intents/reserve`（is_baseline）→ intent_token。
3. OIDC 设备流登录（GitHub 账号 13-pieces-teen；sigstore dex token 仅 60 秒有效，登录与签名需连续执行）→ `build_baseline_sigstore_bundle` 签名（复用磁盘 chain owner key，公钥 sha256 `97adaf93…`）。
4. `POST /init-chain` → record `8c6e5253-e8f1-47f0-9f12-9f3b784b5465`，seq 1。

注意：init_token 一次性；曾因先用过期 intent 消耗了 token，需重新取 baseline 并复用已签名 bundle（bundle 不引用 token）。

### 2.3 Sequence 1 条目验证（rekor.sigstore.dev）

| 项 | 值 |
|---|---|
| Rekor UUID / index | `108e9186e8c5677a02193ac2046e8d1d4b3290e3dc5867e01a97ab342033ceab869becffa9ae68ed` / 2934047626 |
| 条目类型 | intoto v0.0.2；attestation.data 可取回（24988 b64 字符） |
| 正文摘要 | payloadHash `26cbb00a…` = sha256(attestation.data) 实测一致 |
| 签名验证 | DSSE PAE 以条目内嵌 Fulcio 证书验证通过 |
| 签名身份 | SAN `77741887+13-pieces-teen@users.noreply.github.com`；OIDC issuer `https://github.com/login/oauth`（1.3.6.1.4.1.57264.1.1 与 v2 扩展）——均与要求精确匹配 |
| predicate | event_type `chain.init`；baseline_rtmr=`50a59971…`；ccel_eventlog_b64 内嵌；owner pub key；sequence 1、null 前驱 |
| 初始化 owner | chain owner 公钥 `97adaf93…`；owner_attestation v1/tdx：REPORTDATA 绑定 chain_id+seq+baseline_rtmr+ccel_digest+owner_pub_key，真实 TDX Quote 随记录保存 |
| TruCon 状态 | CONFIRMED；`/verify-chain` valid、rekor_confirmed=1、owner_ok |

## 3. Phase 2：部署与真实准入

### 3.1 节点信任恢复（关键发现）

- IP1 交付 bundle（sha256 `fec89eab…`，5 个 CA 级联，活跃 CA serial 2892…→后轮换至 1617…，SKI 084a9fe1…）与本地 bootstrap 一致。
- **旧定制 agent 不读 `bootstrap.crt`**：strace 确认其信任根来自 `/var/lib/spire/argus-poc/data/agent-data.json` 的 `bundle` 字段，且**只信任首条证书**。修复：备份后以严格 PEM 重编码交付 bundle 写入该字段，并将当前签发者 CA 置首；TLS 立即通过（PEM 换行不严格会导致 "no PEM blocks" 解析失败，需 openssl 规范化）。
- 新栈 `argus-workload-agent`（官方 spire-agent + Broker）读完整 bundle，无此问题。

### 3.2 安装与受控切换

- 旧 `argus-node-agent` stop+disable、旧 Provider 替换；`install.sh`（`ARGUS_WORKLOAD_BUILD_DIR=/root/argus-delivery/workload-6ffc30dd`）INSTALL=PASS；`workload.py render` 生成 agent.conf（plugin_checksum `7bd64ffd…`、policy v2-trucon、request_timeout 55s、trustee_endpoint 走 18443 隧道）。
- 环境适配（IP2 侧真实路径）：trustee_url=`https://127.0.0.1:18443`（交付中 8444 为 IP1 侧路径）、server_ssh=argus-ip1-control；策略文件安装至 `/etc/argus-workload/policies/`（sha256 `486d5865…`，Trustee 回读逐字节一致）。
- 从 IP2 实测受限 server-check：新 Entries（helper `36938751…`/be09053a…、target `e9e00f9d…`/v2-trucon policy）合同与交付一致。
- TC API/TruCon/Docktap 全程未动，链未重新初始化、队列未清空。

### 3.3 真实启动记录（launch 入链）

- `workload.py launch` 经 TC API 启动 launch-3e2c339（客户端曾因轮询端点阻塞 >30s 报 "timed out"，服务端实际成功；launch.json 按已验证 launch-result 同构补写）。
- 链：seq1 → seq2（record `687aeaf5…`，Rekor UUID `108e9186…9821`）连续、全部 CONFIRMED；`/verify-chain` valid、2/2 confirmed、0 pending；**硬件 RTMR2 = 链头 `f74b5808…`，sha384(baseline‖launch event_digest) 逐字节一致**。

### 3.4 准入与业务验证

| 步骤 | 结果 |
|---|---|
| register | target.json 全字段正确（container `c12a4ad3…`、policy v2-trucon、digest 60b69b7b/15b0ce48、PID 87690、netns、rootfs ro） |
| preflight | PASS（Trustee 直连/策略、server-check 合同、agent 1.15.3、target） |
| start | 5 units active、ready=true |
| 凭据发布 | NGINX 发布当前 CA 签发 SVID（300s 轮换）；`/run/argus-credentials/ready` 与 status serial 一致 |
| verify | **PASS、业务 HTTP 200**：client `spiffe://argus.local/agent/openclaw` ↔ server `spiffe://argus.local/service/openviking-cmem`（mTLS+AuthZ）；EAR accepted（ear_sha256 `5dd80fca…`，launch/nonce/container/pid/start_time 全匹配）；journal 关联 113 行存证 |
| 入口 | `https://172.31.28.53:1943`（mTLS 必选，按 SPIRE CA 链 + SPIFFE ID 校验） |

## 4. 遇到的问题与根因

1. **agent 崩溃循环（TLS）**：IP1 SPIRE Server CA 更换，旧定制 agent 只认 agent-data.json 首条证书 → 见 §3.1。
2. **节点证明被拒（23:20 翻转）**：21:54 加入成功后，周期重证明被 Trustee 节点策略拒绝（EAR cpu0 non-affirming），agent 按设计移除 SVID 关机 → helper/nginx 连锁下线。根因在 IP1 侧 Trustee 节点策略的时变条件（旧策略 9-10 过期 → 新策略生效 → 23:20 前后评估条件再翻转，最可能为 DCAP collateral 更新导致 TCB 判定变化）。IP1 修复后 23:55:58 自动重新加入，0 拒绝。**每次新 Quote 与 fail-closed 停服均为设计行为**；建议节点策略固定时变条件（TCB 钉扎或同 PoC 例外）。
3. **TC API 422**：镜像注册表 `127.0.0.1:5000` 不在默认白名单（含端口需精确匹配）→ 重启 TC API 注入 `ALLOWED_EXTERNAL_IMAGE_REGISTRIES`。
4. **commit 400（owner authorization 失败）**：TC API 进程 cwd 错误导致 `OWNER_KEY_DIR`（相对路径）解析到错误位置、新建了假 owner key → 显式设置 `OWNER_KEY_DIR` 重启修复；假 key 已移走。
5. **OIDC 登录反复失败**：用户终端代理环境变量导致 `.well-known` 获取失败（库异常消息为空，难以直接定位）；另 dex token 仅 60 秒，登录与签名/launch 必须连续执行。
6. **verify 探针失败**：本地 OpenClaw 客户端材料为 9-18 旧证书（旧 CA、已过期）→ IP1 按 5 分钟轮换投递新 SVID+key+bundle 后通过（bundle 本地已更新为新 5-CA）。

## 5. 边界与遗留事项

- **PoC 例外范围不变**：仅 `tcb_status` 不作为准入条件；Quote/DCAP、collateral、MRTD/RTMR0/1、TruCon 链验证（`trucon.verified` + `baseline_rtmr`）、镜像/配置摘要、executable、runtime-data 绑定全部保留。本总结不把 PoC 通过表述为严格 UpToDate 通过。
- 节点策略窗口至 **2026-09-27 23:50 +08**；E2E 业务流量不触发新 Quote 评估，但节点重证明受窗口约束，跨时点需按同 PoC 边界续期。
- 完整链验证（Trustee 侧按 Rekor 引用重放整链并关联容器）由 IP1 验证器在真实 launch 后执行；IP2 侧已确认硬件 RTMR2 与链头一致。
- 后续：IP1 OpenClaw Gateway E2E（写入/读取/归档/召回、错误身份 403 负例）以 IP1 联调轮为准。
- TC API/TruCon/Docktap 持续运行；TruCon 队列无积压。
