# 客户端执行通道 READY + 恢复缺口清单（IP1→IP2）2026-10-07

核对/执行时刻：2026-10-07T07:33Z（host cwf-bkc）。本轮按"原 IP2 同机新实验批次"优先恢复路线执行：恢复 stage2 Guest(2224) 合法操作通道并实际执行一次实时业务探针；控制侧仅停止 v2 agent 崩溃循环（日志已存），其余组件未动。按约定只列四类：客户端执行通道 READY / Node 恢复准备 / 控制侧实际更新 / 尚缺的具体 IP2 材料。

## 一、客户端执行通道 READY（实测证据）

1. **Guest SSH 通道已恢复**：`ssh -p 2224 -i id_ed25519 tdx@127.0.0.1`（与 /root/.bash_history 历史执行入口一致）。未换 Guest、未重建 Gateway、未输出私钥、未离线修改 guest 磁盘。guest 内 sudo/docker 组可用。
2. **Guest 内组件健康**（evidence/guest-facts-20261007.txt）：
   - spire-agent 1.15.3 **PID 4123738，自 2026-10-02 18:47:45 未变**（身份 0792eff1 保持，无重证明）；
   - argus-oc-paper01full Up 3 days (healthy)、agentcc-openclaw-stage2-gateway Up 8 days (healthy)；
   - 凭据 materializer 实时前滚：generation 820715195(07:32) → 2312226600(07:35)，serial 同步更替，expires_at 持续前滚（约 60-90s 一代）——均为**续期/轮换**。
3. **实时业务探针已实际执行一次**（label=recovery-check-20261007-live，2026-10-07T07:33:36.412Z，evidence/probe-live-20261007T073336Z.json）：三腿全部真实发起——活 SVID（generation-820715195 / serial 234D416BD63BF054EE05802168EA826F / spiffe://argus.local/agent/openclaw/experiment/paper01/full）+ 业务 key + 真实 request_id；SPIRE 组件日志记录完整 request_attempted→request_failed 链路。
4. **三腿结果全部 ECONNRESET，根因已定位在 IP2 侧**：IP2 主机 eth0 = 172.31.28.53，其 1943 端口实测 **Connection refused**（evidence/ip2-1943-refused-20261007.txt）。客户端、1944 隧道（IP1 ssh -L 会话 Sep 29 起 ESTAB 至代理）、凭据全部正常——复位点是 IP2 本机 1943 业务入口无监听（与 E1 冻结状态一致）。**IP2 恢复 1943 业务入口后，同一探针即可重跑闭环，客户端侧无需任何改动。**
5. **身份区分声明**：本轮全部观察均为续期/轮换（agent PID 未变、~40s BatchNewX509SVID、generation 前滚）；无 node 重认证、无硬件证明调用。SVID 轮换不是 TDX 证明，二者在日志中按事件类型区分。

## 二、Node 恢复准备（openviking-node，待 IP2 材料后按正常流程）

- 不先清缓存、不批量 evict；收到 IP2 材料前不动 openviking-node 相关 Entry/策略。
- 需 IP2 交付（见第四节第 2 条），随后按正常流程二选一：策略流续期，或授权下新 guest 重登记。
- Node 恢复与新 epoch workload baseline 分两条流水线记录，不混写。

## 三、控制侧实际更新（本轮已完成）

1. **v2 agent 崩溃循环已停**：docker stop + restart=no（现 Exited，防 daemon 重启复活）。此前累计 58719 次重启。日志与 inspect 已存 /root/argus-legacy-logs-20261007/（6 容器共 8.7MB + argus-v2-openclaw-agent.inspect.json，evidence/v2-agent-stopped-20261007.txt）。未删任何数据、CA、Entry、历史身份。
2. dual / m3 / v2 server 容器未动（不重建，维持现状待授权）；18225 死隧道、materializer 等其余清理项维持现状。
3. 其余控制侧组件（SPIRE 1.15.3、Trustee、隧道、策略库）零变更。

## 四、尚缺的具体 IP2 材料（请随下一交付，勿用旧 readiness 代替）

1. **recovery-check-20261007 正式核验确认**：请在 /root/IP1_HANDOFF.txt 追加确认行（含对其中 12 条 entry 清单、selector 基准、策略基准的接受/修正意见）。注：IP2 侧 07:28:28Z 已建同名 epoch 快照，说明已在响应，请补正式确认行以免 IP1 侧状态悬置。
2. **openviking-node 状态**：guest 内 agent 版本与启动时间、当前 SVID 序列/到期时间、bundle 序号、CanReattest 相关配置、上次成功证明的时间与 quote/测量来源。
3. **新 epoch 策略基准**（完整 ID + 摘要 + 目标关联，不以日期猜选 20260924 策略、不沿用其他 guest 测量值）：目标 policy 的 rego 摘要、RTMR2 基线值与来源（同 boot 累积 or clean boot）、适用 workload 构件清单、与 paper02 Entry（fadd18c2/653fe17e/7d2f3367/003f0642/5f75fa03/a92ef7d3）的目标关联。
4. **1943 业务入口恢复确认**：恢复后请回执（或提供替代路由），IP1 将立即重跑 recovery-check-20261007-live 探针并回传三腿结果。

附：本包 SHA256SUMS；上一包 recovery-check-20261007 已按约定置于 /root/argus-ip1-handoff/ 待消费。
