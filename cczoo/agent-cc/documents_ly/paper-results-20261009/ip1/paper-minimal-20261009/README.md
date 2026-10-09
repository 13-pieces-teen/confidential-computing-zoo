# IP1 本批证据目录（paper-minimal-20261009）

材料版本: b471a34b73e4f8490fa633b7ea3bcfb510756d39 (docs/argus-minimal-experiments-20261009)
本批 batch_id: 待 IP2 RUN_PLAN。所有新增实验当前 NOT_RUN，本目录仅含 S0 产物与 S1 骨架。

## 目录
- existing-evidence-index.json — S0-a 旧材料索引（P0 负例 7/7 + 真实召回原件；S2/S3 SKIP 依据）
- s0-reality/ — S0-b 三轮探针原件（A mTLS health / B1 sessions / B2 search/find）+ 时钟测量副本
- rotation/ — S1 客户端产物（requests.jsonl、load-result.json、子目录）待 RUN_PLAN 后填充
- authorization/、agent-recall/ — S2/S3 条件目录，本批 SKIP（引用 P0 原件）

## S1 冻结参数
case workload-rotation；当前 Full；1 客户端；固定 /api/v1/search/find；reuse；0.2 req/s；
并发 2；超时 10s；预热 30s + 测量 900s；IP2 观察 600s；max_probe_gap_ms=10000；仅 1 正式窗口。

## 客户端运行位置与文件（guest, 受保护）
- 工具: /root/argus-paper-minimal/tool/（部署冻结版, SHA 见 IP1-S0-STATUS 包）
- 配置: /root/argus-paper-minimal/s1/load-config.json（0600, 非秘密结构可导出副本）
- 查询体: /root/argus-paper-minimal/s1/body.json（= E5 冻结件 sha 84c3297f…）
- API key: /root/argus-paper-minimal/s1/api-key.protected（0600, 容器当前业务 key, 永不导出）
- 时钟: /root/argus-paper-minimal/clock-measurement.json（guest 落后 IP2 +15.4s, 界 20000ms）

## 现状
S0-a/S0-b 完成并已推 IP2（s0-status-minimal-20261009, 远端校验 OK）。等 IP2 RUN_PLAN。
