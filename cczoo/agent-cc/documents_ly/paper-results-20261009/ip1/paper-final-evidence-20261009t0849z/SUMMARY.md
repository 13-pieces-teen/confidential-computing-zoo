# 论文定稿前补充验证 — IP1 交付 SUMMARY

批次目录: `paper-final-evidence-20261009t0849z`。日期: 2026-10-09。方: IP1 (OpenClaw)。
本批只读取原件、编写离线分析、导出脱敏材料; 未重跑 P0/A3/E4/S1, 未重启组件,
未改模型/策略/身份/业务 key/运行环境。结果分支: `codex/argus-results-ip1-20261009`
(本批提交 SHA 由渠道通知随附, 见 ip1-handoff 目录 IP1-DELIVERY-NOTE.md 与
本机 /root/IP1_HANDOFF.txt; 上一已核对提交 7c53fee33c4c9a52a9ed4f8ef57303ab01e89382,
经 ls-remote 确认 fork/upstream 均无后续更新)。

## 1. P0 原件补齐(有界查找 ~20 分钟, 全部找回, 未重做实验)

- **权限负例**: `p0/negative-probes-20261003T1000Z.json`(sha 3af191ef…) —
  7/7 负例原件(invalid/missing key 401, wrong_identity 403, cross_user 3/3,
  direct_backend 拒绝/超时), 与已提交索引一致。
- **真实 Agent 召回**(`p0/ip1-p0d-sf-recall-exactness-20261003/`, 8 文件之 7 个):
  - run.json(e40f608d…): run_id p0-20261002t1521z-sf-a1, marker
    ARGUS-DUAL-TDVM-E2E-p0-20261002t1521z-sf-a1, 起止与 fact_generation=random;
  - recall-response.json(f70d7872…): 正例完整回答 + 真实工具调用;
  - result.json(77d548a8…): 一轮 FAIL(ANSWER_FAILED, session
    018cbfba-e2d4-4fa9-8cee-92251407e132) 原件保留, 不只看成功摘要;
  - negative-response.json(ea8c4340…): 负例轮次原回答(含旧判据);
  - gateway.log(8c80fd7f…): 真实网关传输记录, 13:12:49→14:48:06Z,
    含该 session UUID 50 处出现, 可关联身份/时间/会话;
  - IP1-P0D-SF-RECALL-EXACTNESS-20261003.md(17f6c651…): 报告原件(3 轮真实会话,
    正例 4 样本 fact 完整出现, 逐轮审计通过, 1 次限流恢复, 负例判定与
    transport mTLS serial/generation 关联);
  - ADDENDUM-1-NEGATIVE-CHECK-REFINEMENT.md(1b21a2de…): 负例判定细化
    (UNKNOWN 存在且无 ARGUS_FACT_ 形态 token)。
- 排除: recall-session-key.txt(85806637…, session key 属秘密)留原受保护目录,
  见 PROVENANCE-AND-EXCLUSIONS.md。原件=导出件, 无内容脱敏(扫描 0 命中)。

## 2. S1 结果说明追加(更正文件, 不改原判定)

见 `s1/S1-CORRECTION-ADDENDUM.md`(重算脚本与输出同目录)。要点:
180 条为计划采样; 155 成功非空; **19 条 ValueError 在 HTTP 提交前中止**;
**6 条 RemoteDisconnected 交付结果未知**(3 条在观察窗口内: row 112/140/169);
**末尾 13 条中止无窗口内恢复证据**(row 174–186, 全部 ValueError);
两类错误机制分开表述, 不统一归因轮换竞态; 对端 serial 以文件重算 **7 个**为准
(交付包 README 的"8"系统计口径误差, 轮换轴结论不受影响);
原 FAIL/UNKNOWN 与冻结时间戳不变。

## 3. 条件召回(S3 新案例): SKIP

按指令: 旧原件足够即直接 SKIP。P0 真实召回原件已完整找回(3 轮真实会话、
正负例、审计、transport/session 关联), 满足"不执行"条件; 其余执行前提
(IP2 日志关联确认、已确认合成 fact、零恢复/零新写入)因此无需评估。
未发起任何新模型调用, 未产生新 run_id, 不把新案例补写为旧 P0 原件。

## 4. 文件清单

- SUMMARY.md(本文件)
- PROVENANCE-AND-EXCLUSIONS.md
- p0/negative-probes-20261003T1000Z.json
- p0/ip1-p0d-sf-recall-exactness-20261003/{run.json, recall-response.json,
  result.json, negative-response.json, gateway.log,
  IP1-P0D-SF-RECALL-EXACTNESS-20261003.md, ADDENDUM-1-NEGATIVE-CHECK-REFINEMENT.md}
- s1/S1-CORRECTION-ADDENDUM.md
- s1/s1_recompute.py, s1/s1-recompute-output.txt
- SHA256SUMS

全部文件 sha256 见 SHA256SUMS; 秘密零导出(排除项见 PROVENANCE 文件)。

## 5. 仍缺(如实列出, 不展开新排障)

1. 6 条 RemoteDisconnected 的服务端是否实际收到 — IP2 侧 NGINX/应用日志关联职责。
2. P0 负例 key 本身与 recall-session-key.txt — 按规则永不导出。
3. S1 末端覆盖不足(120768 ms > 阈值)— 冻结判定, 不重跑, 无法在本批补齐。

## 6. 当前业务服务状态

无任何变更: 网关容器 Up 10 days healthy, argus-oc-paper01full Up 5 days healthy,
无重启/故障/新准入。本批结束后停止工作。
