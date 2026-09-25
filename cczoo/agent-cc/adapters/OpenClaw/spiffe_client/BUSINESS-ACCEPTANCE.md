# 单 Agent 业务闭环与可恢复验收

本轮固定上游仍为 `@openviking/openclaw-plugin@2026.6.18`，定制版本为 `argus.3`。新增代码只负责通信适配、阶段观测和验收，不改变 OpenViking 的 memory extraction、检索排名或模型输入。真实远程闭环仍需运行以下流程，软件测试不代表远程 PASS。

## 安装与运行

按 [部署手册](DEPLOY-IP1-TDVM.md) 构建、安装新的插件包，重启 Gateway 后重新登记实际 PID，再启动凭据交付。旧 `argus.2` 插件会被接入前检查拒绝；替换插件不能省略进程重新登记。

```bash
# 在 Guest 设置已有非 root 的 OpenViking 用户 API key；不要把 key 写进日志。
export OPENVIKING_API_KEY='your-existing-user-key'
export DUAL_E2E_AGENT_ID=main
export DUAL_E2E_EVIDENCE_DIR=/root/argus-evidence/business-run-001
bash adapters/OpenClaw/scripts/verify_openclaw_plugin_e2e.sh

# 若轮询超时，使用同一目录继续 GET 原 task；不会重写事实或重发 commit。
bash adapters/OpenClaw/scripts/verify_openclaw_plugin_e2e.sh \
  --resume /root/argus-evidence/business-run-001
```

新运行的目录必须不存在；脚本使用 `umask 077`。续查必须保留原目录的 `run.json`、`fact.txt` 与 `processing-events.jsonl`。正常恢复后使用新的召回／负例会话，避免重复使用上次模型回答上下文。

`DUAL_E2E_CAPTURE_ATTEMPTS` 默认 30 次、间隔 2 秒；`DUAL_E2E_COMMIT_ATTEMPTS` 默认 60 次、间隔 5 秒。每次请求另有 30 秒超时，因此该次数不等于严格的端到端时间上限。参数必须是 1–3600 的整数。轮询完成时间是客户端观察时间，不能当作服务端精确完成时间。

## 准入身份和业务作用域

调用使用安装包本身的配置解析器与 Agent 路由器。账号、用户、peer prefix、Agent ID 对应的 actor，在 session 查询、读回、commit 和 task 查询中保持一致。验收传入的 API key 必须与已解析插件配置相同。SPIFFE 客户端／服务端 ID 从受保护的 `client.json` 读取，验证器不再固定为唯一 OpenClaw/OpenViking ID。

这些参数为后续独立 Agent 部署保留入口，当前仍只运行一个 Agent。恢复期间若账号、用户、actor、Origin 或预期 SPIFFE ID 变化，返回 `RESUME_SCOPE_MISMATCH`；不要编辑状态绕过这个检查。

## 阶段和判定

| 结果码 | 含义与后续动作 |
| --- | --- |
| `CAPTURE_FAILED` | 在当前作用域中未找到真实 Gateway 写入的 marker。检查 Gateway capture 与 actor/user 配置。 |
| `COMMIT_OUTCOME_UNKNOWN` | POST 返回前断线或丢失回执，是否成功未知。不会自动重发；先在服务端确认原操作，不能用新 POST 掩盖。 |
| `EXTRACTION_TIMEOUT` | 已知 task 尚未观察到终态，允许 `--resume` 继续查询同一任务。 |
| `EXTRACTION_FAILED` | 服务端 task 明确失败，需检查受保护的服务端日志／模型配置。 |
| `EXTRACTION_RESULT_UNKNOWN` | completed 任务未返回合法的分类计数，不能推断为非空或零。 |
| `EXTRACTION_EMPTY` | 归档存在、提取完成，但分类计数总和为零。归档成功不等于长期记忆生成成功。 |
| `ARCHIVE_FAILED` | 没有同时观察到 commit_count 和归档概要，不能宣称归档完成。 |
| `RETRIEVAL_EMPTY` / `RETRIEVAL_FAILED` | 新会话检索无候选／存在检索失败；由真实 Gateway 的 assemble 审计区分。 |
| `FACT_NOT_RETRIEVED` | 召回内容未包含随机事实；可能是上游生成、作用域、检索或摘要选择问题，不直接认定算法 bug。 |
| `RECALL_SELECTION_FAILED` | 候选摘要中出现事实摘要，但最终召回块未包含。 |
| `INJECTION_FAILED` | 召回块包含事实，ContextEngine 返回的消息没有事实。 |
| `ANSWER_FAILED` / `NEGATIVE_CONTROL_FAILED` | 模型未精确回答随机事实／不存在项目未精确回答 UNKNOWN。 |
| `RECALL_NOT_OBSERVED` / `RECALL_AUDIT_MISSING` | 缺少足够阶段证据，不能把未知直接归为检索失败。 |
| `PASS` | Gateway 写入、归档、非空提取、独立新会话事实注入与准确答案、负例、匹配身份的 mTLS 关联全部通过。 |

`processing-events.jsonl` 在提交前、获得 task ID、各次轮询和最终判定时追加完整状态快照，`processing.json` 为最后完整快照。异常结束也生成 `result.json` 和 `SHA256SUMS`。丢失 POST 回执时，续查入口始终拒绝再次提交；`--resume` 也不会在没有已知 task ID 时重新捕获并写入。

只有幂等 GET 的 `ECONNRESET`、`EPIPE`、`UND_ERR_SOCKET`、`socket hang up` 做一次有限重试。证书、身份、HTTP 权限或业务错误不自动重试；POST commit 和 POST search 均不自动重试。

新增 Argus 审计只记录阶段、计数、会话／请求关联和合成验收事实的 SHA-256，不记录一般对话、候选全文、URI、API key 或上游错误正文。证据目录仍含合成测试提示、回答及原生 Gateway 日志；上游日志可能包含业务内容，应作为受保护的实验材料保留。ContextEngine 返回边界不等同于捕获模型供应商网络请求。

## 本地验证

`bash test-client.sh` 覆盖固定上游构建、真实本地 mTLS、空提取／失败／超时、同 task 续查、POST 不重发、作用域变化拒绝、空召回审计与失败分类。必须提供或允许下载 checksum 固定的 npm tarball；未运行真实远程模型和 OpenViking，不声称已修复远程空记忆的上游根因。
