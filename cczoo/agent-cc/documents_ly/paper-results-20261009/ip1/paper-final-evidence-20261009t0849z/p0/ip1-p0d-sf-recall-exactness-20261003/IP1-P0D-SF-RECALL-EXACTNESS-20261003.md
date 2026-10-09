# IP1 → IP2 发现: 字节级精确答案规则与 DeepSeek-V3.2 真实行为系统性不符

- 日期: 2026-10-03
- 工具基线: 0c9f72a1 (已安装核验, 见 §1)
- run_id: p0-20261002t1521z, backend: siliconflow DeepSeek-V3.2 + enable_thinking

## 1. 工具修复 0c9f72a1 核验: 生效

- 离线用修复版 verify_business 重验归档证据 business-six-stage-1 →
  `RECALL_AUDIT_MISSING` 已消除, 正确推进到 `ANSWER_FAILED` (与 IP2 描述一致)
- guest 部署三文件 (verify_audit.py/verify_business.py/fleet_probe.mjs), grep 核验通过

## 2. 最小重跑执行情况 (按 IP2 指示)

- `--resume` 流程 (不复 commit POST): processing PASSED → write 审计 PASS → 新召回会话 → 新负例 → verify_business
- 共执行 3 轮全新独立召回会话 (真实网关轮, 全新 session key), 其中 1 轮撞硅基流动 API 限流 (瞬态, 等待后恢复)
- 每轮审计匹配均通过 (find_recall 修复版生效): fact digest 在 recall/output_fact_hashes,
  transport mTLS 记录齐备 — **召回管线本身没有问题**

## 3. 发现: 精确答案检查测的是模型聊天风格, 不是记忆管线

### 3.1 正例: 4 个真实样本全部带前导句 (fact 均完整出现)

| # | 时间(UTC) | 模型答案文本 |
|---|---|---|
| 1 | 13:22 (attempt-1 归档) | `找到匹配项目。校验码为：\n\nARGUS_FACT_…` |
| 2 | 14:29 | `找到了！根据长期记忆记录，项目 … 的校验码是：\n\nARGUS_FACT_…` |
| 3 | 14:43 | `太好了！我找到了相关的项目内存。根据OpenViking长期记忆中的记录，项目 **…** 的校验码是：\n\nARGUS_FACT_…` |

(样本 3 完整文本见随包 recall-response.json; 样本 1 见此前交付的 business-six-stage-1; 样本 2 的完整响应文件已被同目录后续重跑覆盖, 文本摘录见上表, 其审计 span 仍存于随包 gateway.log)

### 3.2 负例: 同样不稳定

- 前两轮: 精确 `UNKNOWN` ✓
- 第三轮: `基于对长期记忆的全面搜索，我没有找到项目 … 的校验码信息。在长期记忆中只找到了两个相似的ARGUS项目，但都不是你询问的这个具体项目。\n\nUNKNOWN`
  → 在 `verify()` 顺序下先被 ANSWER_FAILED 拦截, 否则将报 NEGATIVE_CONTROL_FAILED

### 3.3 分析

- fact 为 128 位随机值, 模型不可能猜中 → 答案**包含** fact 字符串即已证明来自记忆召回
- 真正的安全属性全部由审计独立证明且已通过: digest 在 recall/output_fact_hashes、
  input_fact_hashes 空、transport mTLS (serials+generation) 齐备
- 字节级精确只测试 chat 模型对提示词的服从度; 该行为随提供商/模型版本/温度漂移,
  换模型即重新破裂 (本发现本身即证明: 同一提示词下 4 样本 3 种前导)
- 提示词 "仅原样回答校验码" 无法约束 LLM 输出形态

## 4. 建议 (由 IP2 决定, IP1 不改验收代码)

方案 A (推荐): 调整答案判定语义, 保留全部审计检查不动——
- 正例: `fact in answer` (子串包含; 随机 fact 不可猜, 证明力等价)
- 负例: `'UNKNOWN' in answer and fact not in answer` (防幻觉本质保留: 声明未找到 + 绝不输出任何校验码)

方案 B: IP2 调整工具内召回/负例提示词强化输出格式约束 — 但样本 3.1/3.2 表明 LLM 合规无保证, 仍脆弱

## 5. 状态

- IP1 未修改任何验收代码, 未放宽规则, 未改写证据
- 等待 IP2 决策/新 commit → IP1 按新语义离线重验现有证据 (无需重跑网关流程)
- P0 仍 NOT_PASSED, 未进入 E1

## 6. 随包

- recall-response.json (14:43Z 轮新召回完整网关响应, session key …-143715Z-30332)
- negative-response.json (同轮, 带前导句负例完整响应)
- gateway.log (自 13:12Z 全量审计, 含 attempt-1 与全部新召回/负例 span)
- recall-session-key.txt / result.json (ANSWER_FAILED)
- 注: 14:29Z 轮完整响应文件已被同目录后续重跑覆盖, 该轮文本摘录见 §3.1, 审计 span 仍存于 gateway.log
