# IP1 S1 客户端交付 — workload-rotation 窗口

run_id: argus-paper-minimal-20261009t0718z-01-rotation (batch argus-paper-minimal-20261009t0718z-01)
材料 commit b471a34b；工具=部署树 d34f389d 冻结版（SHA 见 load-fleet.log-and-tool-sha.txt）。
客户端=guest 内独立有界进程 (pid 1089911, 自然结束), 冻结参数: reuse/0.2/s/并发2/超时10s/预热30s/测量900s/
memory_query/固定查询( E5 冻结体 sha 84c3297f…)。全程无组件重启、无故障、无新准入。

## 轴 1 — rotation（IP1 客户端观察, IP2 collect 定论）
- 客户端每请求经 ContextProvider 刷新动态凭据 generation; 窗口内观察到 **8 个不同对端 SVID serial**
  (peer_svid_serial, 全部 peer_identity_verified=true, peer_spiffe_id=…/service/openviking-cmem/experiment/paper02/full)。
- 连接复用 144 次(80%), 17 次重连: credentials_rotated×5 + previous_error×12;
  观察到的连接 id 18 个。轮换在窗口内多次发生, 具体次数下界由 IP2 helper journal 精确定。

## 轴 2 — business_continuity（实际请求, 非脚本 rc）
- 计划 180 (900s×0.2/s) / 实际 180: success **155** (86.1%), unknown **25** (13.9%), rejected 0, timeout 0, overload 0。
- success 行全部 memory_result=nonempty（每响应 2 片 level-2 viking://user/e4p2-h1/ 叶子）; empty 0。
- unknown 25 行: 全部 error_class=ValueError, http_status=None —— 发生在 SVID generation 目录交换竞态
  (ready.json 与 generation 目录读取之间), 请求未发送或连接级失败即记 unknown, 不重放; 随后请求全部恢复
  (previous_error 重连)。这是轮换对业务连续性的真实代价, 非工具缺陷, 按协议如实保留。
- 采样间隔: 中位 5000ms, 最大 5002ms (<= max_probe_gap_ms 10000)。
- 端到端 api_ms (success, n=155): p50 112.0 / p95 257.7 / p99 350.8 ms。
- 覆盖: 客户端窗口 [1791531467347, 1791532367347] (guest ms)。IP2 observe
  [1791531867266, 1791532468115]。起始侧覆盖余量 ~384s; **末端侧不足** —— IP2 completed_at+20000ms 界
  = 1791532488115 > 客户端末端, 不足 ~105s (原因: CLIENT_ACTIVE 发出时测量已运行 ~3.5min, IP2 观察
  启动又在其后 ~207s; 600s 观察把末端挤出 900s 窗口)。时间戳全部冻结未改, 正式窗口仅 1 次不重跑,
  末端覆盖不足交 IP2 collect 照实标记。

## 轴 3 — quote_generated（IP2 侧 Provider 计数）
- IP1 无 Provider counter 接口; 该轴由 IP2 collect 报告 (workload/node attempted/generated/failed 增量)。
- IP1 侧无 Quote 调用证据可加。

## 时钟
guest 落后 IP2 +15.43±0.31s (n=10, max 15.92s, HTTP Date 直接测量), 保守界 20000ms;
IP2↔IP1-control −497.7ms (IP2 测量)。collect 用统一界 20000ms。

## S2 / S3
- S2 授权矩阵 SKIP: P0 负例 7/7 原件 (negative-probes-20261003T1000Z.json sha 3af191ef…) 已支撑说明。
- S3 真实召回 SKIP: P0 真实 Agent 召回原件 (ip1-p0d-sf-recall-exactness-20261003, 报告 sha 17f6c651…) 可用。

## 文件
requests.jsonl (顶层合并, 每行含 instance_id/request_id/时间戳/结果/连接/序列号/memory 分类),
c1/requests.jsonl (客户端原始), load-result.json (汇总+内存分类+连接统计),
load-fleet.log-and-tool-sha.txt (进程 stdout=最终 result JSON + 工具 SHA),
load-config.json (非秘密配置副本), body.json (查询体), clock-measurement.json (时钟实测)。
API key 不随包 (protected 文件留在 guest, 永不导出)。
