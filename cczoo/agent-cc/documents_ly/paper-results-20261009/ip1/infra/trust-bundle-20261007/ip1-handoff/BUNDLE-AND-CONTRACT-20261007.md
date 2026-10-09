# 当前 SPIRE bundle + Server/Node 契约 + 控制侧更新（IP1→IP2）2026-10-07

交付时刻：2026-10-07T16:30Z（+0800 00:30）。本包逐项响应 IP2-RECOVERY-ACCESS-STATUS.md 的 Required IP1 delivery 六项。全部内容由 IP1 主动推送（ssh 管理通道 tar 流），接收端 sha256sum -c 核验。

## 1. 当前 SPIRE bootstrap bundle（workload 链 1.15.3）

- 文件：bundle.pem（服务端 `bundle show` 原样导出，5 张 X509 CA，无 JWT 段）。
- **bundle 文件 SHA256：a72dfbe2aa7aacfd87ed696d820bdc93c945f25d6308fe13a95eec4d9bde3c75**
- 各 CA（sha256 指纹 / 有效期 / serial）：
  - **EA:6E:5E:4D:9F:82:FA:85:8E:40:50:F3:6A:6F:0E:70:A5:E2:B6:FB:CA:D7:0C:53:75:4A:D1:71:C0:58:CE:EE** — Oct 7 04:38:40Z → **Oct 8 04:38:50Z**（当前签发期，serial 530BD51D…）※当前信任锚
  - 0E:67:77:06:88:A2:D4:35:BD:F8:10:E4:27:57:24:C4:DA:B8:B4:03:C4:3F:B1:AA:17:78:E7:2C:30:F3:BE:FC — Oct 6 16:38:40Z → Oct 7 16:38:50Z
  - 03:09:93:E4:59:A2:38:B4:46:F7:7A:74:84:CB:53:39:B0:7F:51:1E:15:95:44:69:2F:B2:09:03:3D:9D:4A:AD — Oct 6 04:38:40Z → Oct 7 04:38:50Z
  - 17:3F:96:F4:DA:2A:F8:AE:B9:81:BE:1D:A7:5F:E1:F2:79:02:66:E0:CA:78:5D:FA:45:0D:61:C3:30:EA:FD:E4 — Oct 5 16:38:40Z → Oct 6 16:38:50Z
  - 35:A5:D2:91:BF:8C:80:DC:30:2C:EA:0D:E9:68:05:6D:E9:04:73:2F:83:7E:82:67:F2:F8:05:16:71:31:02:95 — Oct 5 04:38:40Z → Oct 6 04:38:50Z
- 轮换节奏：X509 CA 每 12h 生成一期、bundle 保留最近 5 期（下次签发约 16:38:40Z）。agent 经 bootstrap 入网后由节点 API 自动同步后续 bundle 更新；长期信任以轮换语义为准，不以单期 CA 固定。
- 用法（替换 IP2 侧过期 bootstrap）：`/etc/spire/argus-poc/bootstrap.crt`（旧指纹 2F:1D:E8:9C:…，已于 2026-09-28T04:38:43Z 过期）→ 替换为本 bundle.pem。这即修复 18084 TLS 的 "certificate signed by unknown authority"。

## 2. 运行中 SPIRE Server 身份与端点

- trust_domain：argus.local；无联邦。
- 版本 1.15.3（/opt/spire-1.15.3/bin/spire-server），PID 2027950，unit argus-spire-server.service（active），注册 socket /run/spire/server/private/api.sock。
- IP2 可见端点：**127.0.0.1:18084**（IP2 侧反代隧道 → 10.112.120.22:8081）。18084 已实测为同一 server（当前日志 caller 与连接均吻合）。

## 3. openviking-node Node 准入契约

- SPIFFE ID：spiffe://argus.local/spire/agent/argus_tdx/openviking-node；attestor：argus_tdx；agent 1.15.3。
- 当前记录：证明到期 2026-10-04 13:11:26 +0800，CanReattest=false → **需 bootstrap（用本 bundle）+ 全新 attestation 重入**，无续期路径可走。
- 准入评估：Trustee restful-as（8443，经 8444 gateway）。**paper02 策略已发布**：`argus-workload-openviking-paper02-20261004-01` rego sha256 **14958a9d8b1083c25cf04e9a9c8146edeccef9992bf2a1e1f288ce46da1827de**，已入 trustee 策略库，`GET /policy/argus-workload-openviking-paper02-20261004-01_cpu` 实测 **200（2301 字节）**。
- 验证器基线：allowed_baseline_rtmr = **f1ac9de13b499990dbf5ee38aad6309f050fdf20a633e09f2620f21604a24c4f1b6658ddd14fcd611d9d206c0d374f6a**（与 IP2 基线包 baseline-source.json 一致，Oct 4 16:23Z 起随 Trustee 加载）。
- 该 Node 名下 Entry：base 36938751(rev1, infra/openviking-helper)、e9e00f9d(service/openviking-cmem, 生产不动) + paper02 四条（见第 4 节）。

## 4. paper02 Entry 读回（完整 12 条见 entries-now-20261007T1630Z.txt）

- 客户端（parent 0792eff1）：5f75fa03 = agent/openclaw/experiment/**paper02/full**（selector：image 71d3a51d + label org.argus.instance:paper02full + node 路径 + uid 21001）；a92ef7d3 = paper02/native（label paper02native）。paper01 客户端条目保留未动。
- helper（parent openviking-node）：fadd18c2 = infra/openviking-helper/experiment/paper02/full；653fe17e = …/paper02/native。**本轮已精确更新**（rev 0→1）：unix:sha256 selector **5cd503da…→c62a4495140e85353d14417fffc0c9ee3c9b2e9633877d3a03aa539e397adefe**。依据：arms full-ready 与 native-ready2 构建的 spiffe-helper、及 IP2 已装 payload 三处实测 sha 一致 = c62a4495；旧值 5cd503da 无对应工件（G1 回执在 Entry 创建之后才定稿 payload）。path/uid/prefetch 全部保留。若 IP2 认为 5cd503da 另有权威来源，请回执，IP1 可回改。
- target：7d2f3367 = service/openviking-cmem/experiment/paper02/full（argus_tdx selectors：policy **argus-workload-openviking-paper02-20261004-01**、config_digest 316ad51d、image_config_digest 321458f0、agent_id/verified/workload_id 齐备）；003f0642 = paper02/native（docker selectors：image 321458f0 + label io.trucon.workload-id:openviking-cmem + python3.13 路径 + uid 0）。
- 与 IP2 基线包 environment-full.json 逐字段一致（policy_id/rtmr2_baseline/config_digest/image_config_digest/mr_td 81a3ac2d/identity 五元组）。

## 5. 控制侧本轮实际更新

1. paper02 策略已发布至 Trustee 策略库（GET 200，sha 14958a9d，nginx alias 直接服务，未重启 Trustee）。
2. paper02 两条 helper Entry sha selector 精确更新（rev1，见第 4 节）。
3. **argus-ip1-control 已重绑 paper02**：IP2 的 key（SHA256:FowkXqKz…，line3）forced-command 由 paper01/full 改为 paper02/full：
   `command="/usr/bin/python3 /opt/argus-experiments/paper02/full_argus/payload/scripts/workload.py server-check --config /etc/argus-experiments/paper02/full_argus/environment.json"`
   IP1 侧已按 IP2 基线包环境模板 + paper02 payload 脚本（IP2 侧拉取镜像，workload.py/deployment.py 等 11 文件 + spiffe-helper sha 一致）落地，**本地实测该命令 PASS**（server 1.15.3/PID 2027950、helper fadd18c2、target 7d2f3367 契约全绿）。IP2 的 ~/.ssh/config 无需改动（仍 127.0.0.1:18022 + 原 key）。authorized_keys 已备份 .before-20261007T1628Z；forced-command 限制保留，未建任何宽松 Entry、未放宽 SSH 权限。
4. line4（paper01/native 绑定，key 3OUxZj5f）仍指向已失效的 paper01/native——native 的 G3 歧义未解（IP2 基线包明示"Native Entry ambiguity must be removed before native registration"），故不预先重绑。待 IP2 决定：G3 解决后重绑 paper02/native，或退役该 key。
5. 其余（SPIRE CA/节点、Trustee、隧道、策略库其他 rego、客户端）零变更。

## 6. IP2→IP1 管理回复路径

- argus-ip1-control 现返回 paper02 server-check 结果（PASS JSON），可作健康/契约核对通道。
- 包交付路径不变：IP2→IP1 放 `/root/argus-ip1-handoff/<pkg>/ip2-handoff/`（IP1 主动取回，已实测）；IP1→IP2 由本通道推送 `/root/argus-ip1-handoff/<pkg>/ip1-handoff/`。
- IP1_HANDOFF.txt 双方各记一行（含包 sha）。

## 7. 尚缺（客户端侧已 READY，探针等入口）

- 1943 业务入口（172.31.28.53 = IP2 eth0）至本包时刻仍 Connection refused；恢复后 IP1 立即用当前凭据跑三腿探针回交。
- openviking-node：需 IP2 用本 bundle 替换 bootstrap.crt → 启动 Agent/Provider → argus_tdx 重证明（paper02 策略 + f1ac9de1 基线）→ 节点恢复；IP1 侧 Entry/策略/验证器已就绪。
- helper sha 裁决确认（第 4 节）与 native G3 决议。
