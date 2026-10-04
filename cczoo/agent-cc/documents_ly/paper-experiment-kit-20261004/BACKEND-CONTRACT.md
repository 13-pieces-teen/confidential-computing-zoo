# 受控后端接入契约

这是实现规格，不是已部署功能。本地可见Provider的QuoteSource/observe/snapshot接口、生产LogVerifier和签名fixture；IP2须对照实际最新代码接入。

| 接入点 | 实验替代 | 保持的被测逻辑 |
|---|---|---|
| QuoteSource | 实验签名证据，明确类型 | challenge/REPORTDATA对应语义、当前实例前后观测一致性 |
| 远程评估 | 独立实验appraiser与实验根 | 签名、nonce/时效、目标/策略版本、历史核验后实际判定 |
| TruCon snapshot/材料发布 | 本地签名fixture发布端与软件SHA-384状态 | snapshot约束、记录就绪拒绝、生产历史验证器 |
| Node bootstrap | 实际SPIRE软件入网，两臂相同 | Node/Workload身份、精确Entries、SVID轮换 |
| 执行与业务 | 实际组件，不替代 | Helper/入口/监督、应用读取、权限、模型任务 |

优先窄接口适配，不引入完整TruCon/Docktap/OIDC/外部日志部署。复用TruCon软件MR后端更简单时可用，但不访问真实RTMR。

## 契约

1. 实验材料包含evidence_type、run_id、nonce、实际目标观测摘要、软件测量状态、策略版本及签发/过期时间，由独立实验密钥签名。
2. 不伪装TDX Quote/DCAP/真实Trustee结果。显式实验配置/构建选择后端，默认实机路径拒绝实验类型和根，未知模式不自动降级。
3. 信任根、身份、端口、socket、数据库和SPIRE状态隔离；不能更改全局verifier影响原workload。
4. 场景生成器控制输入与发布时刻，不按expected值输出准入结果。独立oracle比较实际判定。
5. 正常材料的PID/起始时间/配置/监听来自实际观测；负例中的失配明确标为注入。软件扩展保留算法和顺序，但没有硬件不可篡改性。
6. 链、签名、Merkle和owner规则使用测试信任材料实算。归档transport调用生产验证代码，不把fixture结果字段当成策略输出。

## 接入检查与交付

合法材料进入实际验证器；签名错误、nonce错误/过期、target错配与策略不符在对应层拒绝。覆盖默认实机拒绝实验类型和模式隔离。

运行一次S1真实软件闭环，入口与receiver事实独立于appraiser日志。IP2交付文件清单、接口映射、构建摘要、针对性测试与替代范围。

若必须更改准入判据而非获取/传输接口，明确说明语义差异，不能隐藏为“后端适配”。本轮无需新TDX VM、宿主权限、真实RTMR写入、未知Docker操作仲裁或恢复旧链。
