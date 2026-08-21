# 双 TDVM + Broker Sidecar 远程验证报告

状态：远程验证进行中。M3 ALLOW/DENY 与双 TDVM DENY 已通过；双 TDVM ALLOW
已通过正向身份链路和 `/health=200`，wrong-client 与 pidfd/lifecycle 尚待在修正后的
验收脚本中继续执行。本文档不预填最终通过结论。

## 构建信息

- 验证日期：2026-08-21（阶段验证）
- Git commit：
- Git 工作区状态：
- 验证主机：
- SPIRE Server / Agent：`1.15.2`
- 结论：进行中

## 身份与运行对象

| 项目 | 远程实测值 |
|---|---|
| OpenClaw Parent ID | |
| OpenViking Parent ID | |
| `dual-openclaw-workload` selectors | |
| `dual-openviking-broker` selectors | |
| `dual-openviking-target` selectors | |
| OpenViking container ID | `f93de14f7d6cdfbd3b4a775db37c8d6c21d290c33fe5e46cf2f454e41289be44`（ALLOW r2） |
| OpenViking host PID | `100068`（ALLOW r2） |
| Sidecar `-target-pid` | `100068`（ALLOW r2） |
| `broker.sock` owner/mode | `0:1000 770`（ALLOW r2 在 `/ready` 前已通过脚本断言） |
| OpenViking source image config digest | 待补完整 `sha256:` 值；阶段日志短值 `2b952bca11d0` |
| TC-API runtime image config digest | 待补完整 `sha256:` 值；阶段日志短值 `71f9ba968fcb` |
| 运行容器实际 image config digest | |
| `dual-openviking-target` 注册的 config digest | |

## Non-intrusive 证据

| 检查 | 结果 / 证据 |
|---|---|
| OpenViking 无 Workload API mount | ALLOW r2 已通过；待补 `docker inspect` 摘录 |
| OpenViking 无 Broker API mount | ALLOW r2 已通过；待补 `docker inspect` 摘录 |
| OpenViking 无 X.509-SVID/private-key mount | ALLOW r2 已通过；待补 `docker inspect` 摘录 |
| OpenViking 无直接获取 SPIFFE 身份的环境配置 | ALLOW r2 已通过；待补 `docker inspect` 摘录 |
| Workload API、Broker API 与目标 SVID 只由 Broker Sidecar 使用 | ALLOW r2 已通过；待补 Sidecar mounts/log 摘录 |
| Sidecar `-target-pid` 等于 OpenViking 实际 host PID | 已通过：`100068 == 100068` |

## M3 Broker 基线

| 检查 | 结果 / 证据 |
|---|---|
| ALLOW：收到目标 SVID 后才 ready | 已通过；待补日志摘录 |
| DENY：无目标 SVID、无 ready、未监听 21943 | 已通过；待补日志摘录 |
| DENY：Sidecar 保持运行，处于无身份阻塞服务状态 | 已通过；待补容器状态 |
| DENY：Trustee denied metric | 已通过；待补 metric 原文 |
| 目标 PID 退出后 Sidecar 退出 | 已通过；待补容器状态 |

## 双 TDVM DENY

| 检查 | 结果 / 证据 |
|---|---|
| 两个 Agent 的 Parent ID 不同 | 已通过；待补完整 Parent ID |
| TC-API 已启动 OpenViking | 已通过；待补 container ID/PID |
| Trustee decision / metric 为 DENY | 已通过；待补 metric 原文 |
| 目标 Entry 要求 `verified/workload_id/policy` 强 selectors | 已通过；待补 Entry 原文 |
| WorkloadAttestor 未产生可匹配的 verified selectors | 已通过；待补 Agent 日志/selector 摘录 |
| Sidecar 无目标 SVID、无 ready、未监听 1943 | 已通过；待补日志与端口证据 |
| Sidecar 保持运行，处于无身份阻塞服务状态 | 已通过；待补容器状态 |

对称链路结论：

```text
Trustee DENY
  -> 无可匹配的 verified selectors
  -> 强 Registration Entry 不匹配
  -> 无目标 SVID
  -> Sidecar 无 identity
  -> 1943 不监听
```

DENY 结论必须由本轮配置的 Mock Trustee decision 与 denied metric 共同确认；
Sidecar 的空身份状态本身不能区分永久 DENY、Entry 尚未同步或暂时不匹配。

## 双 TDVM ALLOW 与 mTLS

| 检查 | 结果 / 证据 |
|---|---|
| OpenViking 无 SPIRE mount / SVID | 阶段验证已通过；待补 `docker inspect` 摘录 |
| Sidecar PID 与 OpenViking PID 一致 | 已通过：`100068` |
| Trustee decision / metric 为 ALLOW | 已通过：`argus_m4_fake_requests_total{service="workload_trustee",result="ok"} 2` |
| verified selectors + runtime digest 命中强 Entry | 已通过 override 启动；待补完整 Entry selectors 与 digest |
| Sidecar 获得目标 SVID 后监听 1943 | 已通过：日志出现 `ready for identity spiffe://argus.local/service/openviking-cmem` |
| Guard ALLOW 后 SPIFFE mTLS `/health=200` | 已通过 |
| 无客户端证书访问失败 | 已通过 |
| 错误 expected-client ID 握手失败 | 待执行 |
| OpenClaw 无法访问明文 1933 | 已通过 |
| OpenViking 退出后 Sidecar 经 pidfd/lifecycle 退出 | 待执行 |
| Sidecar 退出后 1943 不再提供服务 | 待执行 |

对称链路结论：

```text
Trustee ALLOW
  -> verified selectors
  -> runtime digest + 强 Registration Entry 匹配
  -> 目标 SVID
  -> Sidecar 1943 ready
  -> OpenClaw Guard ALLOW
  -> SPIFFE mTLS /health = 200
```

## Application Readiness（非安全链路硬验收）

| 检查 | 结果 / 证据 |
|---|---|
| `/ready` HTTP 状态 | `503`（ALLOW r2 实测） |
| OpenViking readiness 响应 | `embedding: Connection error`；`ollama: unreachable at 172.18.0.1:11434` |
| 结论 | `Application Readiness: NOT READY` |
| 原因 | dual-TDVM profile 未部署 Ollama/bge-m3 |

不要为了本轮 Mock attestation + mTLS 验收部署 Ollama。`/ready=503` 必须如实记录，
但不阻断 wrong-client、pidfd 与 1943 关闭检查。

## Image digest 工程问题

- 本轮可使用 `DUAL_OPENVIKING_IMAGE_CONFIG_DIGEST` 指定实测 runtime digest；
- 报告必须同时保留 source、runtime、运行容器和 Entry 中的 digest；
- 后续工程项：Registration Entry 应基于 Attestor 实际观察到的 runtime
  measurement，而不是未经 TC-API 转换的 source artifact measurement。

## Trustee metrics 与日志摘录

```text
待填写；不得记录 TC-API identity token 或 bearer token。
```

## 最终结论

只有身份、attestation、mTLS、负例、non-intrusive 与 lifecycle 硬验收全部由远程
实测通过，并且 Application Readiness 状态已如实记录后，才填写：

> Mock Evidence Provider + Mock Trustee 软件链路通过。

如果 `/ready=503`，还必须同时填写：

> Application Readiness 未通过：dual-TDVM profile 未部署 Ollama/bge-m3。

该结论不覆盖真实 TDX Quote/QGS、Rekor 度量验证、生产 Trustee 或生产安全验收。
