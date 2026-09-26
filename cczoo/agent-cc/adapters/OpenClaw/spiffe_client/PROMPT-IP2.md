# IP2 执行 Prompt

你运行在公司 IP2，请为“IP1 新 TDVM 内的 OpenClaw → IP2 现有 OpenViking”完成服务端接入核对与联调证据收集。

范围：沿用实际部署记录中的 OpenViking 实例与批准策略，记录实际 tcb_status；当前批准策略不要求 TCB UpToDate，不将该字段另作准入条件。历史 Node/Workload 验证结果不替代本次代码与实例的验收。本轮 OpenClaw 在 IP1 新 TDVM 中运行，先用普通 x509pop/SPIFFE 身份；业务为 OpenClaw 请求、OpenViking 返回结果及双方 mTLS，不新增 OpenViking 主动调用 OpenClaw 的 API。

先执行并立即返回接入交接信息：
1. 读取适用 AGENTS.md，检查仓库后 git fetch origin。使用用户指定的本次提交，记录完整 SHA；当前运行目录有改动时用独立 worktree 检查新代码，不覆盖正在运行的程序或配置。
2. 阅读 cczoo/agent-cc/adapters/OpenClaw/spiffe_client/DEPLOY-IP1-TDVM.md（重点第 7 节）、VALIDATION.md、cczoo/agent-cc/core/spire/workload/README.md，以及根目录 documents_ly/argus-openviking-workload-attestation-status-20260909.md。历史报告是定位线索，逐项核实当前状态，不直接照抄旧的 PID、socket、证书有效期或通过结论。
3. 记录当前 OpenViking container ID、launch ID、PID、镜像/config digest；核实实际 Provider、SPIRE Agent、Helper、NGINX/AuthZ units、运行版本、Workload API/Broker sockets、业务监听与网络 namespace、SVID serial/有效期、policy ID 和原始策略摘要。
4. 给操作者一份供 IP1 使用的交接信息：从 IP1 Guest/Docker 容器应访问的 HTTPS origin 和必要 DNS/纯 TCP 隧道条件；服务端 ID spiffe://argus.local/service/openviking-cmem；允许的客户端 ID spiffe://argus.local/agent/openclaw；非 root OpenViking API key 的既有安全取用位置（不输出 key）；服务版本、PoC 策略 ID/摘要、当前健康状态及证据目录。可达性未实测时标为待验证，不把本机 localhost 地址直接宣称为 IP1 容器入口。

保留现有基线：
- 不运行 workload.py launch/register/start/stop，不重建或重启当前 OpenViking，不切换 Node Agent 数据目录，不重置 CA，不为本轮先追求 UpToDate。
- 检查 NGINX mTLS/AuthZ 确认唯一允许客户端身份。若必须调整入口或纯 TCP 路由，先记录实测缺口，只做本轮必要的最小配置修复，保留原配置和恢复办法；不关闭 mTLS、不开放任意客户端身份。
- 若需要新 workload.py 的策略一致性检查，提供公司实际批准的原始 PoC 文件及 SHA256，配置 approved_policy_artifact，核实对应 Trustee GET 回读逐字节一致；不得由 PoC 名称自动豁免，不从历史报告摘要猜出策略内容。缺少原件时报告缺口，不修改远端策略。检查历史临时探针证书是否过期；过期探针失败不能直接判为现有 OpenViking 故障。

收到 IP1 的联调时间范围、request ID、session ID 后继续：
- 从当前 NGINX/AuthZ/OpenViking 日志或可核验服务记录关联真实 Gateway 写入、读取、commit/archive 和后续召回，记录 HTTP 状态、客户端身份、服务端 serial、会话/任务关联。日志字段不足时如实说明并保留现有证据，不伪造匹配。
- 配合观察普通 SVID 轮换：记录 serial 变化与服务连续性；不把证书轮换写成重新 Quote/Trustee appraisal。
- 使用本轮有效的、CA 可验证但 SPIFFE ID 错误的既有负例证书验证 NGINX 返回 403，并保留同一入口的正例对照。若负例材料缺失或过期，明确记录 BLOCKED/NOT_RUN；TCP timeout、无证书 TLS 失败不等于错误身份 403。
- 不在本阶段对 IP2 既有 Provider/Agent/Helper/OpenViking 做暂停、杀进程或 target-exit 实验。客户端故障由 IP1 在新 Guest 内执行；IP2 负责观察拒绝/恢复及同一业务资源状态，不对结果不确定的写入盲目重试。

输出：按 PASS/FAIL/BLOCKED/NOT_RUN 给出当前服务基线、接入交接表和联调后的服务端报告，注明实际运行代码/配置、报告代码 SHA、策略 ID/摘要、OutOfDate PoC 边界、证书有效期、request/session/serial 关联、具体证据路径和本轮改动/恢复情况。未拿到 IP1 真实业务记录前，只完成服务端准备报告，不声称 OpenClaw 端到端已通过；收到关联信息后继续完成报告。原始证据和密钥留在受保护位置，仓库报告只含脱敏结果，代码修复留下 diff，不自动推送。
