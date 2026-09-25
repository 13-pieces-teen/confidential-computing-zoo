# Argus 1—3 代码交付与远程运行顺序

本轮交付单 Agent 业务诊断、服务端监督关闭和可恢复部署。真实 TDX、远程 OpenViking 提取/召回及关闭窗口均须在目标主机运行；本地测试不替代这些结果。保留当前批准策略，不新增 TCB UpToDate 要求。配置中继续用 `approved_policy_artifact` 固定已审阅的策略文件及 SHA-256；不要误用默认严格模板覆盖现行策略。

## 代码变化

| 范围 | 本轮行为 | 验证边界 |
| --- | --- | --- |
| OpenClaw 适配和验收 | 上游仍为 2026.6.18，定制包 argus.3；区分归档、提取完成和非空记忆；固定业务作用域；记录检索和 ContextEngine 输出；同 task 续查 | 不改上游提取/排序，远程业务失败原因由新材料定位 |
| 服务端 Helper | Check 完成进度、消费进度、固定发布期限共同约束 watchdog；ready 绑定 invocation/target/serial/expiry | 本地观察器属于受信任组件；没有新增周期 Quote |
| 入口 | 正常轮换 reload；失效 TERM，默认 2 秒后清理 NGINX 进程组 | 配置预算不等于实测交付停止上限 |
| 部署 | 创建请求前持久化；已知 launch ID 恢复查询；不重发未知创建 POST；显式恢复既有签名提交 | 不新增服务端幂等协议；丢失创建回执时需核对服务端 ID |
| 关联材料 | build-manifest、runtime-manifest、启动记录、业务阶段和独立流量观察 | 哈希用于版本关联，不是新增硬件证明或签名收据 |

节点 `CanReattest=false` 的既有修改保留：首次加入仍有 TDX Quote/Trustee 核验，普通有效 Agent SVID 续期不再走完整节点证明。已有 Server 的 Agent 状态不一定因替换插件而自动变化，应先观察运行记录；不能直接删除 Agent 数据或 CA 来获得一次“成功”。

服务名称集中在 `Deployment.units/unit()`，身份和路径仍由原配置传入，验收结果含运行/实例标识。当前仍为一个服务实例，未实现多目标路由、资格发现、任务级授权、自动协作者替代或完整 A/B/C 编排。

## 1. 构建、同步和升级

在 Linux x86_64、Python 3.11+、Go/Rust/NGINX/TC API 依赖齐全的构建环境，按 [README](README.md) 执行：

```bash
cd cczoo/agent-cc/core/spire/workload
bash scripts/build.sh
```

这会生成 12 个 Linux 可执行文件、`SHA256SUMS` 和 `build-manifest.json`。清单同时固定安装脚本/模板内容；构建后修改源码应重新构建，不能沿用旧清单安装。完整构建需要当前 Git checkout，以记录提交与工作区修改。

客户端按 [BUSINESS-ACCEPTANCE](../../../adapters/OpenClaw/spiffe_client/BUSINESS-ACCEPTANCE.md) 及其部署手册构建/安装 argus.3 包。更新客户端插件后重新登记 Gateway 的实际进程。

在两台主机备份已有配置和实验记录，再停止相关认证栈、安装同一构建。安装不自动启动、不清空 Agent 数据、也不删除业务容器。服务端使用原审批后的策略及信任材料。Helper 二进制变化后，需要按 README 更新其固定 SHA-256 Entry；工具发现旧宽泛或不匹配 Entry 时会拒绝继续。

readiness 是不兼容的内部格式升级：旧版本仅有数字 serial，新版为 JSON。必须成套更新 Helper、Python 脚本和 units。先用旧工具正常停止栈，再安装新版；不要运行中单独替换 ready 文件。首次新启动重新登记当前合法实例，普通凭据轮换不需要重新登记。

## 2. 启动与恢复

新建实例沿用原命令：

```bash
sudo --preserve-env=TC_API_IDENTITY_TOKEN,TC_API_BEARER_TOKEN \
  python3 /opt/argus-workload/scripts/workload.py launch \
  --config /etc/argus-workload/environment.json
```

若启动查询超时，使用相同配置继续原操作：

```bash
sudo --preserve-env=TC_API_IDENTITY_TOKEN,TC_API_BEARER_TOKEN \
  python3 /opt/argus-workload/scripts/workload.py resume-launch \
  --config /etc/argus-workload/environment.json
```

若首次提交回执丢失，`launch-state.json` 保留 `submission_unknown`。先核对 TC API 受保护的操作记录，确认唯一的用户、workload、镜像和启动 ID，再用上述命令加 `--launch-id <确认的ID>`。不能通过删除状态再执行 launch 来重试。服务端返回的 ID、用户、profile、workload 和批准镜像不匹配时恢复失败。

服务端明确返回签名身份过期时，刷新原 OIDC 身份环境变量后再次 `resume-launch`。只使用既有 `/api/deploy-launch/commit/<id>`，commit 响应丢失后先查询结果，不立即再 POST。

成功后依次 `register`、`preflight`、`start`、`verify`。要新建替代实例，先停止并核对旧实例和端口占用；正常完成的启动记录自动存档。失败或不确定操作保留人工核对入口，不自动销毁实例或回滚到旧凭据。

恢复演练应在已取得 ID 的轮询阶段中断客户端进程，再执行 resume。比较前后启动 ID、容器 ID 和服务端创建记录，确认只有一次创建。中断不代表服务端操作取消。

## 3. 验收顺序与结果

1. 运行服务端 `verify`，核对当前实例、身份和业务入口。
2. 运行客户端 argus.3 业务验收，获得真实 Gateway 写入、归档、非空提取、新会话召回/注入/答案和 UNKNOWN 负例材料。提取为空仍是失败；新增诊断不会把它变为 PASS。
3. 运行正常 Workload SVID 轮换和错误客户端身份用例。
4. 按 [REMOTE_ACCEPTANCE](scripts/REMOTE_ACCEPTANCE.md) 分别运行 Helper 冻结、崩溃、目标退出，以及独立新旧连接/实际接收端观察。实验中若临时抑制自动恢复，单独记录该控制并在结束后显式恢复；生产默认自动重新准入继续保留。
5. 采集首次节点加入及至少两次普通 Agent SVID 续期区间，核对 Node 专用证明计数、进程连续性和公共 Agent 记录。不要用 Workload Quote 计数代替 Node 计数。
6. 执行上面的启动恢复演练，保存容器未重复创建的服务端材料。

正常状态下可导出运行版本关联：

```bash
sudo python3 /opt/argus-workload/scripts/workload.py manifest \
  --config /etc/argus-workload/environment.json
```

`binary_integrity` 与 `installed_source_integrity` 分别核对已安装产物；二者一致才得到 `build_integrity=MATCH`。运行配置及策略另记摘要，不和安装模板混淆。变更后的实际审批策略应由原 `preflight` 校验。

使用以下表格汇总，不以文件删除或 HTTP 2xx 替代实际接收观测：

| 检查 | 状态 | 材料/摘要 | 实测值或失败阶段 |
| --- | --- | --- | --- |
| 构建/安装关联 | NOT_RUN | build/runtime manifest | |
| 当前实例 Workload 准入 | NOT_RUN | verify.json + journal | |
| 单 Agent 业务闭环 | NOT_RUN | result.json + processing.json + gateway log | |
| 正常轮换/错误身份 | NOT_RUN | lifecycle records | |
| Helper freeze：清理/旧连接/新连接/receiver | NOT_RUN | fault + probe + receiver | |
| 目标退出/替换与重新准入 | NOT_RUN | 原实例/替代实例/准入记录 | |
| 节点首次加入/普通续期 | NOT_RUN | Node snapshots | |
| 启动查询恢复/无重复容器 | NOT_RUN | launch-state + TC API 操作记录 | |

结果用 PASS、FAIL、UNKNOWN、NOT_RUN。没有 receiver 覆盖时，不得把“客户端请求失败”写成“敏感数据零交付”。现有请求采样也不覆盖任意长流和全部网络字节，需要对应实验补充观察。
