# 双 TDVM + Broker Sidecar 远程验证报告

状态：待在远程 Linux/TDVM 环境执行。本文档是本次改动的独立验收记录，不预填通过结论。

## 构建信息

- 验证日期：
- Git commit：
- 验证主机：
- SPIRE Server / Agent：`1.15.2`
- 结论：待验证

## 身份与运行对象

| 项目 | 远程实测值 |
|---|---|
| OpenClaw Parent ID | |
| OpenViking Parent ID | |
| `dual-openclaw-workload` selectors | |
| `dual-openviking-broker` selectors | |
| `dual-openviking-target` selectors | |
| OpenViking container ID | |
| OpenViking host PID | |
| Sidecar `-target-pid` | |
| `broker.sock` owner/mode | |

## M3 Broker 基线

| 检查 | 结果 / 证据 |
|---|---|
| ALLOW：收到目标 SVID 后才 ready | |
| DENY：无目标 SVID、无 ready、未监听 21943 | |
| DENY：Sidecar 保持运行，处于无身份阻塞服务状态 | |
| DENY：Trustee denied metric | |
| 目标 PID 退出后 Sidecar 退出 | |

## 双 TDVM DENY

| 检查 | 结果 / 证据 |
|---|---|
| 两个 Agent 的 Parent ID 不同 | |
| TC-API 已启动 OpenViking | |
| Sidecar 无目标 SVID、无 ready、未监听 1943 | |
| Sidecar 保持运行，处于无身份阻塞服务状态 | |
| Trustee workload denied metric | |

DENY 结论必须由本轮配置的 Mock Trustee decision 与 denied metric 共同确认；
Sidecar 的空身份状态本身不能区分永久 DENY、Entry 尚未同步或暂时不匹配。

## 双 TDVM ALLOW 与 mTLS

| 检查 | 结果 / 证据 |
|---|---|
| OpenViking 无 SPIRE mount / SVID | |
| Sidecar PID 与 OpenViking PID 一致 | |
| Sidecar 获得目标 SVID 后监听 1943 | |
| 无客户端证书访问失败 | |
| 错误 expected-client ID 握手失败 | |
| OpenClaw 无法访问明文 1933 | |
| Guard ALLOW 后 `/health`、`/ready` 成功 | |
| OpenViking 退出后 Sidecar 退出 | |

## Trustee metrics 与日志摘录

```text
待填写；不得记录 TC-API identity token 或 bearer token。
```

## 最终结论

只有上述检查全部由远程实测通过后，才填写：

> Mock Evidence Provider + Mock Trustee 软件链路通过。

该结论不覆盖真实 TDX Quote/QGS、Rekor 度量验证、生产 Trustee 或生产安全验收。
