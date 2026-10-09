# E1 Full A3 离线闭环回执

分析时间：2026-10-04T03:27:56Z

## 结论

- 新准入：**ESTABLISHED / ADMITTED**
- 合法客户端业务访问：**ESTABLISHED / OBSERVED**
- 双机 A3：**CLOSED / PASS**

本回执只闭合既有 A3。没有重跑 A、没有发起新订阅、没有重启或重新部署
Provider，也没有执行 B/C、native、P0 或其他实验。

## 新准入证据

- attempt：`41aaef740d1944f4a8718931b38e2783`
- 新 Helper invocation：`664f18cca38e4171af0c9426d05add78`
- accepted nonce：`VAlcCOENN1fWQTUfqoFt-iR8fdUNt4sJjNDJAvm2YzE`
- 五阶段使用同一 nonce 和同一 target，全部 `ALLOW`
- identity publication 和 ingress readiness 均为 `ALLOW`
- 最终结果：`ADMITTED`

publication serial `4307E916AF2F7D010E660553AEDC5D2C` 的证书在
A3 开始前由正常轮换签发，并由本次新 Helper invocation 发布/装载。准入要求的是新订阅、
完整五阶段和新 invocation 的身份发布，不要求证书签发时间晚于 attempt 开始时间。

## 双机业务关联

IP1 原件中 rounds 37–54 共 18 轮，每轮包含：

1. mTLS `GET /health`
2. `GET /api/v1/sessions`
3. `POST /api/v1/search/find`

独立离线关联结果：

- 54/54 业务腿 HTTP 200
- 36/36 transport request ID 存在且唯一
- 客户端身份均为
  `spiffe://argus.local/agent/openclaw/experiment/paper01/full`
- 服务端身份均为
  `spiffe://argus.local/service/openviking-cmem/experiment/paper01/full`
- 三代客户端证书与三代服务端证书均有签发原件，且在对应请求时有效
- 应用已测 guest 时钟修正 `+7.028s` 后，54 条客户端记录与 54 条 IP2
  NGINX 访问记录逐腿匹配，最大时间偏差 610ms
- rounds 41 和 50 在轮内发生正常客户端证书轮换；逐腿 generation/serial
  与签发时间一致
- 服务端 serial 轮换与 NGINX reload 原件一致

业务观察发生在 attempt 完成后，符合既定口径；不要求业务请求落在准入执行窗口内。
19:02:31Z 的联动订阅仍只标记为诊断，不计作 A3。

## 原件保留

旧 UNKNOWN 原件未修改：

- `IP2_E1_FULL_A3_LOCAL_RECEIPT.md`
  - sha256 `7b08e5e4fadbf40241496da1b8cbc6bb282891bcf74068aba91504ea7d597dc7`
- `ip2-business-correlation.json`
  - sha256 `0d1be95d5b6b5c1caa3338353f4485cb309c1a12b34207a6d9720c2026ee2449`

旧 UNKNOWN 准确描述了当时尚未收到 IP1 客户端原件的状态；本目录是收到并核验
IP1 原件后的新增离线分析，不覆盖旧回执。

## 休息后只读就绪检查

- Full 五个隔离 unit：active
- `workload.py status`：`ready=true`
- 容器：running/healthy，PID `1716593`
- 实时服务 SVID：身份正确，检查时有效期至 `2026-10-04T03:32:05Z`
- collector：active/running，绑定仍指向 A3 的实际 container、PID、launch 和 boot
- 控制隧道：本地受保护监听 `127.0.0.1:18022`、`:18443` 可达；
  forced server-check 返回 SPIRE Server 1.15.3、PID 4161698 和当前 Full Entries
- TruCon：chain `default` sequence 10，head
  `f17a50fc-5837-48a5-be9a-cc05b9dc27bf`
- pending mutation：0
- 当前没有 armed/reached/release barrier 文件
- `docktap_mutations` 与 `delegations` schema 已存在
- workload policy sha256 `39044444...` 与批准配置一致，策略本身没有时间窗条款
- node policy `argus-node-poc-ignore-tcb-20261003-01` 的批准时间窗至
  `2026-10-05T16:00:00Z`
- 最近 delegation `del-13a8012059de` 已于
  `2026-10-03T22:35:09.932454Z` 过期

## 进入 B/C 前置条件

1. 通过正常控制流程续办新的 Docktap delegation，scope 保持
   `pull/create/start/stop/rm`，并记录实际 expiry；不得复用已过期 delegation。
2. 执行前复核 node policy 仍在有效期内；若窗口已过，按既有批准流程续办，
   不临时放宽策略。
3. 按 E1 配方为隔离的 Docktap/TruCon 实验进程设置
   `ARGUS_EXPERIMENT_MODE=1` 和新的 root-only
   `ARGUS_EXPERIMENT_BARRIER_DIR`。健康态进程当前默认未启用 barrier，这是预期状态。
4. arm 新的 `before_confirm` barrier 后，仅执行一次正常 `start` 操作；确认捕获的
   mutation/container 后再执行 pending、attempt、observe、compare。

当前无需重建 schema、清理链或身份数据，也无需修改 Entry、策略或 CanReattest。
