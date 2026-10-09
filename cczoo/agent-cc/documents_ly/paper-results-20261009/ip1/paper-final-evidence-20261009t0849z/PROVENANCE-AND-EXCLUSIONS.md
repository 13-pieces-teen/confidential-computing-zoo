# 原件来源 / 排除与脱敏说明 — paper-final-evidence-20261009t0849z

## 一、P0 原件来源(全部逐字节复制, 未改动)

源路径(IP1 本机交接镜像):
- `/root/argus-ip1-handoff/p0-20261002t1521z/p0d-six-stage-20261003/negative-probes-20261003T1000Z.json`
- `/root/argus-ip1-handoff/p0-20261002t1521z/ip1-p0d-sf-recall-exactness-20261003/`

包内文件与源文件 sha256 完全一致(见 SHA256SUMS; 与已提交索引
existing-evidence-index.json 中记录的 3af191ef…/17f6c651…/e40f608d…/f70d7872…/
77d548a8…/ea8c4340…/8c80fd7f… 逐一相符)。

## 二、排除项(受保护原件留原地, 不导出)

| 文件 | sha256 | 排除原因 |
|---|---|---|
| `…/ip1-p0d-sf-recall-exactness-20261003/recall-session-key.txt` | 858066374ab42fbe77ce50758898674fd5848c6800c9783300255021d6b8239a | 召回会话 session key, 属秘密, 永不导出 |

该文件仍保存在源受保护目录(0600), 未删除未移动。

## 三、秘密扫描结论(未做内容脱敏, 因无命中)

对全部导出文件扫描 key/token/私钥形态(x-api-key、api_key、authorization、
bearer、sk- 前缀、长 base64 载荷): 0 命中。recall-response.json /
negative-response.json 中仅含 contextTokens/reasoningTokens/promptTokens
用量计数; gateway.log 中仅含 tokenBudget/maxInjectedChars 日志字段与
session UUID; negative-probes json 中仅含负例 key 的 sha 前缀(8e58bf13…),
不含 key 本身。故未产生脱敏副本: 原件=导出件, 摘要一致。

## 四、准备期语句指向(不修改已提交历史文件)

已提交的 existing-evidence-index.json 与 paper-minimal-20261009/README.md
含有 S0 准备期表述, 现指向最终回执(不改写原文, 追加指向):

- `"batch_id": "argus-paper-minimal-<UTC>-01 (pending IP2 RUN_PLAN)"`
  → 实际 batch_id = `argus-paper-minimal-20261009t0718z-01`,
  见通道件 final-receipt-minimal-20261009/ip1-handoff/IP1-FINAL-RECEIPT.md。
- README.md "本批 batch_id: 待 IP2 RUN_PLAN。所有新增实验当前 NOT_RUN"
  → S1 已执行并关闭; 全部结论见 IP1-FINAL-RECEIPT.md 与 IP2 联合
  SUMMARY(IP2-JOINT-SUMMARY.md, ip2-handoff/)。
- `results_branch.head: "8936abaa"`(索引时值)
  → 提交 7c53fee3 之后的分支头见各次交付记录; 本文档批次提交见 SUMMARY.md。
