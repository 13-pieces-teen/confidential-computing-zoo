# IP1 恢复核对回执（控制端 + 客户端）2026-10-07

核对时刻：2026-10-07 15:10 +0800（host cwf-bkc，uptime 48d，内核 6.18.10-tdx）。全程只读核查：未重启 Guest、未改内核、未清 Agent 数据、未重建 Gateway、未新建 TDVM、未执行新链初始化。按约定仅列四类：可直接复用 / 需要例行恢复 / 缺失接口 / 具体下一动作，供 IP2 合并唯一恢复清单。

## 版本与材料基线（问题 1 / 4）

- 运行版本：SPIRE Server **1.15.3**（workload 链，systemd `argus-spire-server`，端口 8081）；遗留容器链 SPIRE 1.15.1(v2) / 1.15.2(dual) / 1.15.2(m3)；Trustee restful-as **93b9ed13**（v0.21.0，127.0.0.1:8443）+ 受限 workload gateway nginx（127.0.0.1:8444）；QEMU TDX Guest ×3（2222 openviking / 2223 openclaw / 2224 stage2-b57f5210）；隧道 systemd 服务 5 个 active。
- git：HEAD `3cf25a87`（feat/argus-spiffe-v2-val，领先 upstream 7 提交）。未提交：14 个 M（experiments/argus 工具/文档）+ 4 个未跟踪（PAPER-EVIDENCE-PLAN.md、POST-P0-HANDOFF.md、tests/test_runner_resources.py、post-p0-experiments-20261004.tar.gz）。本轮未改动任何文件。
- **8cdc1900 材料已取得**：git 对象在本机（`remotes/upstream/codex/argus-controlled-evaluation-20261004` 分支 tip，commit `8cdc1900af999f1eef5191e03fef725b66940d1f`）。已按 GIT-GET.md 流程 `git archive` 导出至 `/root/argus-paper-kit-8cdc1900-20261007/cczoo/agent-cc/documents_ly/paper-experiment-kit-20261004/`，`sha256sum -c SHA256SUMS` 全部 36 文件 OK（含 BACKEND-CONTRACT.md、MANUSCRIPT-METHODS.md、DELIVERY.json、TDX-ARCHIVE-INDEX.json）。旧解包 `/root/argus-paper-kit-20261004` 为 fa615b1c 旧版（DELIVERY.json 哈希不符、缺上述两文件），不再使用。
- **controlled 后端配置：未实现**（如实标记）。BACKEND-CONTRACT.md 自述"这是实现规格，不是已部署功能"；本机代码树与 /etc/argus-\* 配置 grep 无任何 controlled 实现/配置。仅契约文档可用。

## 一、可直接复用

1. **客户端身份链路当前有效**（workload 1.15.3 链）：stage2 Guest(2224) 内 x509pop agent `0792eff142e7acb0ab7e5360e690f662b9bb4ae4` 每 ~40s 全量轮换 7 条 openclaw SVID（服务端日志 BatchNewX509SVID 连续到 15:00+），14:50 完成新一轮重证明，agent 到期已续至 15:50 +0800 并持续前滚；连接由 `ss` 实证（qemu pid 2777274 ↔ 10.112.120.22:8081，端口 40844 与日志 caller 吻合）。
2. **证书/信任 bundle 有效**：workload 链 X509 CA 每 12h 轮换，最新一期 10-07 04:38:40Z → 10-08 04:38:50Z；JWT 权威 5 槽位 12h 轮换，最新到期 2026-10-11T02:05Z（epoch 1791434750）；12 条 entry 全部 expires_at=0（无 TTL 到期）。
3. **客户端工具链可复用**：experiments/argus 全套 + venv `/home/ying_liu/.local/argus-python311` → `pytest 486 passed, 3 skipped`（53.9s）。采样/负载/任务脚本与 config、examples 齐备。
4. **Trustee 与策略服务 active**：restful-as 8443、nginx workload gateway 8444、SPIRE 1.15.3 均 active；nginx 策略路由 `GET /policy/<name>_cpu` 就绪；策略库 16 个 `_cpu` rego 在 `/var/lib/attestation-service/storage/attestation_service_policy/`（含 `argus-workload-openviking-v2-trucon-poc-ignore-tcb-20260924-01` 及 `-audit-20261002-01` 审计版）。
5. **隧道存活**：控制隧道三反代（IP2:18084→10.112.120.22:8081、IP2:18443→127.0.0.1:8444、IP2:18022→22）与 openclaw 专用隧道（1944→172.31.28.53:1943）均 active；实测 1944 OPEN、443(siliconflow) OPEN。
6. **Guest 进程存活**：3 台 QEMU 均在（2222/2223 自 Aug 20，2224 stage2 自 Sep 9 14:45）；stage2 控制台已到 Ubuntu 24.10 tdx-guest 登录提示符。
7. **归档可读**：`post-p0-experiments-20261004.tar.gz`（7 文件）；`/tmp/e1-a3-blocked.tar.gz`（11 文件，含 IP2_A3_BLOCKED_RECEIPT.md）；`e1-20261004-full-a3-correlation`、`e1-20261004-full-a3-originals-supplement` 交接包完整。
8. **主机健康**：uptime 48d；当前 boot 无 MCE；跨 boot 仅 Aug 20 一条 pstore MCE-ERST 归档记录（历史事件，非本轮）。

## 二、需要例行恢复 / 需决策

1. **三条遗留容器链 CA 均过期且无法轮换**（服务端当前实时报错，非历史推断）：
   - v2/asymmetric：上游签发 CA 已于 **2026-09-09T03:35:22Z** 过期，server 每 5s 报 "already expired"；agent 容器累计 **58719 次重启**崩溃循环；gateway 容器的 SPIFFE socket（openclaw-agent-run/agent.sock）自 Sep 9 起不存在（Gateway 进程健康、身份接口断，spiffe_identity 模式失效）。
   - dual：CA **2026-09-15T05:23:14Z** 过期（bundle 仅一期证书，日志同证）；2 个已证明 agent、4 条 entry 为 Round 5 mock 遗留。
   - m3：CA 2026-08-27 过期 + 容器内 `keys.json.tmp` 写失败（数据目录缺失）。
   - 处置（停用或重建 CA）需授权；未授权前保持现状。当前业务不依赖这三条链。
2. **openviking-node 证明已到期、无续期版本**（已核查 agent list 仅 1 条记录）：argus_tdx 证明到期 2026-10-04 13:11:26 +0800，CanReattest=false；10-05 以来服务端 0 条该节点活动（最后活动 10-04 12:29，当时签发的是 paper01 条目，现条目集已为 paper02 版本）。trustee 网关 access.log 自 10-05 起为空 → 与 E1 现场冻结状态一致（E1-CONTINUATION-APPROVAL.md 已授权范围内），**不是新发故障**；恢复需走正常策略流或按授权新 guest 重登记，本机不自动重入。
3. 遗留反向隧道 **18225→127.0.0.1:2225**：ssh 进程存活但目标端口无监听（无 2225 VM）→ 需移除或重指。
4. 遗留 `argus-svid-materializer`（Aug 20 起）：其 socket `/run/spire/agent/agent.sock` 与输出目录 `/run/argus-svid` 均不存在 → 停用候选，新链不依赖。
5. 历史 x509pop agent 记录 d3c786bb（9-16）、74fbd387（10-01）已过期未清理（可 server 侧 ban）。
6. `/tmp/argus-dual-openviking-qemu`、`/tmp/argus-dual-openclaw-qemu` 目录已不存在（VM 仍运行，console/pid 工件丢失）。

## 三、缺失接口

1. **Guest 内深度核查通道**：2222/2223/2224 无可用 SSH 凭据（root + 本机 id_ed25519/id_rsa 均被拒），IP1 无 manage-guest.sh → Guest 内服务状态（openviking/openclaw 进程、guest 侧 agent 配置）需 IP2 提供凭据或由 IP2 侧核查。
2. **controlled 后端实现**：无（仅 8cdc1900 契约文档）。
3. **P0 交接包 p0-20261002t1521z**：已按约定被 IP2 消费移除，本地无原件（P0 结果以 IP2 侧与 git 历史为准）。
4. **IP2 侧隧道端口的实际监听状态**：IP1 侧只能证实 ssh 连接层存活，远端端口绑定需 IP2 确认。

## 四、具体下一动作（供 IP2 合并，本机未执行）

1. 新批次沿用 stage2 Guest 同机：agent `0792eff1` 无需重证明（CanReattest=true 自动续期已实证）；若换 Guest/清 agent 数据 → 新 x509pop ID，需更新 7 条 openclaw entry 的 parent：e9baf44f(rev1)、7abe485a(rev3)、cea379df、c5397769、5f75fa03、a92ef7d3(rev0)。
2. 实验 workload selector 基准：docker image_config_digest `sha256:71d3a51dca069dee52bf8c00c1794c5a99a8410bcc87bd457a7be308795833a0` + label `org.argus.workload:openclaw` + `org.argus.instance:paper0{1,2}{full,native}` + unix uid 21001（实验客户端）/1000（agent 基类）/0（helper 路径 sha256 绑定）。
3. openviking 侧 5 条 entry（36938751 rev1、fadd18c2、653fe17e、e9e00f9d、7d2f3367、003f0642 共 6 条）依赖 argus_tdx/openviking-node 重入；同机新批次必须先解决该节点证明（授权下按 E1 continuation 新 guest 重登记，或走策略流续期）。
4. 策略基准：trustee 引用 `argus-workload-openviking-v2-trucon-poc-ignore-tcb-20260924-01_cpu`（及其 `-audit-20261002-01` 审计版）；nginx `GET /policy/<name>_cpu`；guard 旧 policy `argus-asymmetric-openviking-v1` 属 v2 遗留链，新批次勿引用。
5. 路由保持：1944（probe）、443（siliconflow）、18084/18443/18022（控制三反代）；清理 18225→2225。
6. 遗留链（v2/dual/m3）与 materializer 的停用/重建单列，待授权。
7. Guest SSH 凭据/核查通道由 IP2 补齐或提供。

以上为一次核对的完整结论；状态无变化时不重复汇报。
