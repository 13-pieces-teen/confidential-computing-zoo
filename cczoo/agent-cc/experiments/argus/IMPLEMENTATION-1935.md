# Argus：revision 1935 精简实验实现

日期：2026-10-02。依据已核验的论文 revision 1935 和 `output/argus-code-adjustments-20261002.md`。本次在现有工作树继续实现；前序生命周期修复见 [IMPLEMENTATION-20261002.md](IMPLEMENTATION-20261002.md)。这里记录代码交付，不是论文实验结果。

## 已补齐的代码

| 实验 | 改动 | 执行入口 |
|---|---|---|
| E1 准入判定 | 显式新建 Helper 订阅；按 nonce 关联目标检查、Provider、远端评价和身份/就绪；区分明确拒绝、未到达与 UNKNOWN。默认关闭的定点屏障支持真实 running-but-unconfirmed 路径及 RTMR/数据库崩溃点分类 | [E1-STAGE-RECEIPTS.md](E1-STAGE-RECEIPTS.md) |
| E2 接收与关闭 | 独立观察固定业务后端是否仍能响应；核对原 PID、启动时间、boot、netns、listener。健康探针仅在受信管理域内发无凭据 loopback GET，不新增公开入口 | [FAULT-FIXTURES.md](FAULT-FIXTURES.md) |
| E3 续期与重建 | 增加同实例新订阅、新受控 launch/容器替换；保存 Helper journal cursor、订阅/发布计数和实际 Quote 差值；恢复响应关联真实 TLS 对端 SVID serial | [E3-LIFECYCLE-RECIPE.md](examples/E3-LIFECYCLE-RECIPE.md) |
| E4 六步任务 | 原始 typed Proposal 独立于 Decision 存储；回读核验原文及哈希；UNKNOWN 阻止所有后继写入；空审计不冒认未尝试或拒绝；实际派发/工具阶段分开；合法恢复关联独立准入、同请求读取和真实答案时间 | [WORK-ITEM.md](WORK-ITEM.md)、[四条件配置](examples/work-item-paper-suite.example.json) |
| E5 小型成本 | 三个冻结历史长度点，命令先记意图且不重放；关联同次 Provider/Trustee 计时与新就绪；两服务同链待决，分别观察 B 的新订阅和既有连接，保留原 Helper 运行 | [E5-COST-TRIALS.md](E5-COST-TRIALS.md) |

主实验使用一个 OpenClaw 客户端、固定模型和六步行程任务。Full/native × 正常/故障为四条件；监督与主动关闭消融只用于 E2。LoCoMo、静态 mTLS、旧 ledger、多客户端工具仍兼容，但不作为主实验前置要求。模型采样配置会归档，无法从提供方核验的有效值保留默认/未知。

计时与阶段日志是实验观测，不进入 REPORTDATA 或批准策略。Helper 只取证模式使用同一个已批准二进制和身份选择器，不发布凭据、不触发 hook、不停止原通信路径；接到身份不单独证明远端历史政策通过。

## 保持的边界

- 管理接口仍只面向受信管理员；本轮没有增加恶意部署者模式。
- 待决记录阻止新的 Workload 历史快照，不等于主动关闭已有入口，也不阻止直接生成 Node Quote。
- 生命周期恢复仍限于同一 guest boot；`/dev/shm` 默认数据库不提供 VM 重启持久性。RTMR extend/数据库提交切点标记 `MEASUREMENT_COMMIT_UNKNOWN`，没有实现自动修复或防回滚。
- E2 后端健康证据、实际应用读取、接收者政策资格分别报告；缺完整资格区间时，未经准入实例接收仍为 UNKNOWN。
- 完整六步成功、已确认状态上的正确继续、合法恢复是三个结果。未知写入只查询原操作，不重放、不补写明确拒绝的旧输入。
- Full/native 的差异是整体 Workload 机制差异。若差异只来自记录待决，不据此宣称完整历史具有独立优势。

## 验证与下一步

最终本地验证如下。原件路径均相对仓库根目录；测试范围有重叠，不把多次定向回归的数量累加为独立用例数。

| 验证范围 | 结果 | 原件 / 命令 |
|---|---|---|
| 全部 Argus 实验工具、Trustee、remote_acceptance、receiver_audit 合并回归 | **559 passed，37 skipped，8 subtests passed** | `tmp/argus-1935-final.xml` |
| 生命周期、TruCon client、proxy、snapshot 与 barrier 核心回归 | **133 passed** | `tmp/e1-core-20261002.xml` |
| E4 审查修复定向回归 | 82 passed；最后审计字段调整另有 7 passed，已纳入上述最终合并回归 | `tmp/argus-e4-boundaries-tests.xml`、`tmp/argus-e4-boundaries-audit-tests.xml` |
| 成本归因、未知状态停止与源码交付定向回归 | 23 passed，已纳入上述合并回归 | `tmp/argus-cost-and-delivery-final.xml` |
| Go WorkloadAttestor / Trustee / evidence client | 三个相关包通过 | 插件模块 `go test ./internal/workloadattestor ./internal/trustee ./internal/evidence` |
| Go Helper Broker / CLI | 测试通过；Linux amd64 Helper 和 broker 测试二进制交叉编译通过 | Helper 模块 `go test ./pkg/broker ./cmd/spiffe-helper`；未执行 Linux 二进制 |
| Node task-audit、Gateway | 3 项测试通过；Gateway 语法检查通过 | `node --test adapters/OpenClaw/spiffe_client/test/task-audit.test.mjs`、`node --check experiments/argus/continuous_gateway.mjs` |
| 交付接口和文档 | 6 个 CLI 帮助、4 份示例 JSON、本轮入口文档本地链接与 `git diff --check` 通过 | CLI/配置/链接检查及最终工作树检查 |

Windows 跳过的 Linux 文件锁、权限及宿主关联测试不算通过。生命周期测试用真实本地持久与 HTTP 处理路径，但 Docker、RTMR、Rekor 为替身；任务测试使用可核验的记忆替身，没有调用真实模型。真实 TDX、systemd/netns 故障、模型任务效果、停止时延及跨机成本均为 **NOT_RUN**。真实 OpenViking 是否逐字保留并回读 Proposal 还须在单轨迹 pilot 验收，不能从替身测试推导模型实验成功。

本机没有 Rust 工具链，Provider 修改仅完成静态核验，尚未编译。Linux 构建主机在部署前应运行 `cargo test --locked --manifest-path core/argus/Cargo.toml --bin argus-spire-evidence-provider`，再按原构建流程生成实际产物。本地 Go 编译通过不能代替这一步。

执行顺序：核验管理域隔离与实际构件 → 单客户端健康路径 → E1 第一拒绝层/待决切点 → E2 后端存活且入口关闭 → E3 重新建立 → E4 四条件配对 → E5 正常 API、三档历史和共享待决。具体交接见 [TWO-HOST-SEQUENCE.md](TWO-HOST-SEQUENCE.md)。

源码清单由 `delivery.py create/verify` 在修改稳定后刷新。双机仍须重新构建并核验实际二进制、镜像、策略与配置；源码一致不等于远端产物已更新。后续按作者要求提交源码与配套说明；本记录不代表已执行远端部署，也未改写飞书或 Overleaf。
