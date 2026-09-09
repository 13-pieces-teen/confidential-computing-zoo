# IP1 执行 Prompt

你运行在公司 IP1，请实际实施 OpenClaw 客户端阶段，并管理 IP1 新建 TDVM 内的操作。

目标：在 IP1 创建独立 TDVM，在 Guest 内部署独立 SPIRE Agent/Broker、OpenClaw 容器和 spiffe-client-credentials；由真实 OpenClaw Gateway 调用 IP2 OpenViking 并收到结果，双方通过 mTLS 认证。OpenClaw 暂用 x509pop 普通节点准入及 unix/Docker workload selectors，不做 TDX Quote/Trustee 远程证明。IP2 的 OutOfDate PoC 条件继续保留。

先执行：
1. 在现有 confidential-computing-zoo 仓库读取适用 AGENTS.md，检查工作区并 git fetch origin。使用用户指定的本次提交；若现有部署目录有修改，建立该提交的独立 worktree，不覆盖原部署目录。记录实际完整 SHA，确认本次提交属于 origin/feat/argus-spiffe-v2-val。
2. 阅读 cczoo/agent-cc/adapters/OpenClaw/spiffe_client/DEPLOY-IP1-TDVM.md、README.md、VALIDATION.md，以及 cczoo/agent-cc/documents_ly/Argus-IP1-OpenClaw-IP2-OpenViking-Implementation-Plan-CN.md。dist 产物不在 Git；从此提交构建插件和 Linux Helper，或核对用户交付包清单，不能默认拉取代码后已有二进制。只需构建客户端，不运行 IP2 的整套 Workload build/launch。
3. 记录既有 SPIRE Server/Trustee 的实际配置、unit、可执行版本、CA/bundle、监听地址和当前状态。向操作者索取 IP2 的接入交接信息；在其尚未返回时，继续完成 Host 预检、TDVM 创建和 Guest 准备。

实施：
- 使用 IP1 已验证的 TDVM 能力、批准的基础镜像/TDVF，分配独立 overlay、名称和未占用 SSH 端口。优先运行 scripts/openclaw_tdvm.sh；如果公司 QEMU 参数不同，沿用已验证的启动方法并记录差异。启动预检用 boot 范围，不因缺少 QGS/Quote 通路阻塞本轮。确认 Guest 已启动并具有 TDX Guest 标识；此项不等于远程证明。
- Guest 安装官方 SPIRE 1.15.3、Docker、Python 和客户端 Helper，固定 OpenClaw 镜像 manifest digest、image config digest、Node/OpenClaw 版本和启动命令。按手册在 Guest 生成节点私钥/CSR，由 IP1 的独立 x509pop CA 签发；保留现有 SPIRE CA。增量合并 Server 的 x509pop 配置，校验后按既有流程应用，并回查原控制面/IP2 连接状态。
- 填写 deployment.example.json，用 deploy.py render 和 guest-install 生成/安装配置。Workload API 与 Broker sockets 必须分目录，容器只读挂载 client.json/credentials，不挂载 Agent/Broker sockets。核实 Guest → IP1 Server 和容器 → IP2 HTTPS 的真实可达性；TCP 隧道须保持 TLS 在 IP2 NGINX 终止，不把 Host/Guest/容器的 loopback 混用。
- 使用当前 Server 回读的真实 x509pop Agent ID，执行 apply-entries/server-check。Helper 绑定二进制摘要，OpenClaw 绑定实际 UID、可执行路径、镜像摘要和 label。发现旧 parent 或宽松同身份 Entry 时明确报告冲突并保留证据，不静默放宽。
- 按 install → Gateway 加载插件 → 核对真实 Guest host PID → guest-register → connect 执行。setup 后如需再次重启，先 guest-stop，再重启及登记新 PID。使用实际模型配置和 OpenViking 非 root 用户 API key，不把任何私钥、token/API key 写入报告。
- 运行 verify_openclaw_plugin_e2e.sh，完成真实 Gateway 写入、插件读回、commit/archive、独立新会话召回随机事实，以及不存在项目的 UNKNOWN 负例。必须保留 Gateway mTLS 请求与 ContextEngine 召回关联；独立 curl/health 成功不替代业务成功。
- 按手册连续观察 SVID 轮换及业务读取，并分别验证本轮 Guest Helper 的 stop/SIGKILL/SIGSTOP、Guest Broker 无响应、Gateway PID 替换和本轮业务 TCP 故障后的收敛/恢复。故障只作用于本轮客户端/Guest/专用链路，不暂停 IP1 Server/Trustee 或 IP2 现有服务。保留实际故障命令、时间、重启策略、journal、trace/events；每项完成后恢复本轮临时配置。

遇到缺少模型凭据、IP2 origin 或网络资料时，先完成不依赖它们的步骤，再具体列出缺少项。失败可以修复本轮新增配置或代码，但不能用 mock、关闭 mTLS、跳过身份检查或修改成功判据替代验收。代码修复需留下 diff、测试和实际运行 SHA，不自动推送。

输出：按 PASS/FAIL/BLOCKED/NOT_RUN 提交 IP1 Host 与 OpenClaw Guest 分开的报告，包含提交/产物摘要、VM 参数、真实 Agent ID/Entries、容器/进程实例、双方 SVID serial、request/session ID、业务/轮换/失效证据路径和恢复结果。OpenClaw TDX 远程证明固定记 NOT_RUN；IP2 OutOfDate 与 PoC 例外明确保留。把本轮时间范围和 request/session ID 交给操作者，供 IP2 对齐服务端日志。原始证据留在受保护目录，仓库报告仅含脱敏结果。
