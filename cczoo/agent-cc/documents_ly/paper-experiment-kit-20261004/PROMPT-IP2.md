# 给 IP2：受控实验 v2

本包controlled-v2取代旧的硬件等待前提。你负责受控服务端、最小后端适配、串行调度和数据汇总，IP1负责实验控制端与客户端。

## 立即执行
1. 按GIT-GET.md导出固定提交，核验SHA256SUMS。材料版本和运行版本分别记录，不覆盖当前修复。
2. 原平台仍记ORIGINAL_PLATFORM_PROVISIONING_UNCONFIRMED；停止仅等管理员的循环，继续软件工作。旧链/INFLIGHT、停滞guest和P0/A3原件保留；不启动新TDX guest或cwf-bkc验证。
3. 使用现有普通Linux用户态资源，独立目录/数据库/UDS/端口/身份/根与签名材料。不得使用旧TruCon默认路径、真实RTMR或旧owner key。不能隔离时报告普通Linux资源需求。
4. 复用12b365ed的E4/E5适配，核验缺项后补齐，不重复开发。

## 最小适配和S1
按BACKEND-CONTRACT.md提供实验签名证据、appraiser、软件测量/记录发布，复用Provider实例观测、生产历史验证与实际身份/入口。先核对实际源码接入点，再做最小差异；当前并不存在已部署的模拟开关。

优先复用签名fixture/归档transport；不要求生产TruCon/Docktap全链bootstrap、OIDC或真实Docker未知操作。实验类型/根隔离，默认实机继续要求硬件证据。签名、nonce/过期、目标/策略不符及模式隔离做针对性测试。不能无条件ALLOW或模拟最终业务结局。

与IP1完成一次S1：
有效材料/当前实例→真实准入及合法访问→实际Helper冻结/停止交付→解冻/必要重新准入→实际记忆回读；另确认失配/无效材料拒绝。
通过后冻结版本和配置。接口失败按具体接入点处理，不转回内核试验。

## 正式单线批次
1. E1受控A/B/C：两臂pilot后，三种子配对，共六轨迹。发布接口控制证据就绪，不额外Docker stop或制造INFLIGHT；被测准入代码判定，两臂共同检查/保护保持。离线规则单列。
2. E5+E3：两臂×两连接模式×三轮，每轮30s预热+120s测量。记录全部请求结局、资源、SVID和实验后端调用；软件与硬件成本分开。
3. E2：配置变化Full/no_close三对；Helper冻结Full/no_watchdog三对，共12运行。后端存活、receiver独立，记录实际读取；缺口/未关闭分别记未知/右删失。
4. E4：Full正常/恢复各一次pilot，正式三种子配对，共六任务。真实记忆读写与确认状态，不重放未知写入。

客户端采样READY后发START，双方直接交接，不逐阶段等用户。保留所有预定运行结局，不重复到成功挑选样本。

## 汇总
维护材料外的唯一results.json工作副本，用v2字段evidence_kind、environment_id、runtime_revision、run_id、evidence_ref。主实验controlled_prototype，历史规则offline；P0/A3进入TDX-ARCHIVE-INDEX.json，不填主统计。

每批校验、IP1复核后更新图表。首次交付普通Linux可用性、最小后端差异/测试和联调接口，下一份交付S1原件。管理员入口不再是这些工作的依赖。
