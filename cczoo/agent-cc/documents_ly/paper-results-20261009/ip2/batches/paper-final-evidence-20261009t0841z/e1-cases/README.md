# 七个历史规则案例 — 独立离线复核（IP2）

## 复核命令与运行环境

```bash
/home/ying_liu/confidential-computing-zoo/cczoo/agent-cc/core/tc_api/venv/bin/python3 \
  /root/argus-paper-final-evidence-20261009t0841z/e1-cases/verify-seven-cases.py
```

- Python 3.11.2；sigstore 3.6.7；cryptography 46.0.7；rekor_types 来自 tc_api venv。
- tlog 模块从部署树加载（`cczoo/agent-cc/core/tlog`），树摘要
  63d416636f00ca195f193f983dcabfcd5a7612c5 —— 与 fixture 记录提交 3cf25a87 及部署
  HEAD 12b365ed 字节一致，故部署树导入即历史验证器本身。
- analyzer-source 三文件实测摘要与 VERSIONS.json 逐一对上：
  admission_cases.py e4e18697…、history_diagnostics.py e5324fa7…、verify_trucon.py 495218b9…。
- Rekor 走固定归档 JSON（ArchiveTransport），无实时 Rekor 网络访问。
- 输入原件（request/rekor/trust.offline/trust.original/init.pem/rekor.pem）逐例记录
  sha256，见 seven-cases-reasons.json。

## 判定分类（与归档 verify_e1_fixtures.py 的关键区别）

归档脚本把任意 Exception 归为 DENY。本脚本区分：

| 分类 | 含义 |
|---|---|
| ALLOW | 验证器正常放行 |
| RULE_DENY | LogVerifier 内 require() 规则命中（预期拒绝） |
| ERROR | 归档缺条目、超时、缺依赖、密钥文件缺失、配置/路径/格式错误 —— 不算成功拒绝 |

本批运行中 0 例 ERROR：三个预期 DENY 全部为规则命中。

## 七例判定表（见 seven-cases-decisions.csv，结论如下）

| 案例 | 历史预期 | 独立复核 | 分类 | 判定 | 规则命中理由 |
|---|---|---|---|---|---|
| legal | ALLOW | ALLOW | ALLOW | PASS | — |
| unrelated_activity | ALLOW | ALLOW | ALLOW | PASS | — |
| same_image_new_instance | DENY | DENY | RULE_DENY | PASS | exactly one target launch record is required |
| hidden_stop | DENY | DENY | RULE_DENY | PASS | history does not reproduce Quote RTMR2 |
| config_mismatch | ALLOW | ALLOW | ALLOW | PASS | — |
| old_evidence | ALLOW | ALLOW | ALLOW | PASS | — |
| instance_mismatch | DENY | DENY | RULE_DENY | PASS | launch record does not match current container/image |

每例 verify 6.2–10.7 ms、取用 3–4 条归档 Rekor 条目（timings 见 reasons JSON）。
七例历史 result.json 的 per-rule 判定（fixed_accumulated_measurement /
full_history_current_instance / target_launch_only）与本复核的 LogVerifier 实际输出一致。

## 复核范围与两层检查的分界

- Fixed/Launch/History 三组均为**规则级比较**（同一生产 LogVerifier 对固定归档输入重放），
  不是完整 Native SPIRE 在线对照。
- 本次复核覆盖的是**历史子验证器**（LogVerifier.verify 全链：
  Rekor 条目与 checkpoint 校验、链式历史重放、RTMR2 基线、目标 launch 唯一性、
  实例绑定）。历史 result.json 中 not_checked_here 字段明确划出：
  TDX Quote、REPORTDATA、challenge 新鲜度、当前 PID/starttime、配置准入策略
  —— 这些是生产 E1 流水线的外层检查，不属于历史子验证器，本复核同样不触及，
  也不声称覆盖。
- 密钥与信任材料：init.pem/rekor.pem 及 trust.offline.json（仅重定位密钥路径）逐例
  按原件加载；trust.original.json 保留作对照（两者 sha256 均记录）。

## 输出

- seven-cases-decisions.csv — 七例完整决策表
- seven-cases-reasons.json — 每例原因、timings、历史 per-rule 判定、加载的验证器
  版本/摘要、命令与运行环境
- verify-seven-cases.py — 本复核脚本（只读，不修改任何原件/判据/历史结果）
