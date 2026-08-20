# Argus TDX OpenViking Workload Attestation 实施入口

> 状态：Broker Sidecar 源码已实现；远程 Linux/TDVM 验证待执行

当前唯一实施路径是：

```text
TC-API 启动 OpenViking
  -> Launcher 取得实际 container ID 和宿主机 PID
  -> Broker Sidecar 提交 WorkloadPIDReference
  -> argus_tdx_workload 调用 Mock Evidence Provider 和 Mock Trustee
  -> ALLOW 后返回可信 selectors
  -> 静态 Registration Entry 匹配并签发目标 SVID
  -> Sidecar 使用目标 SVID 与 OpenClaw 建立 mTLS
```

具体代码路径、部署顺序和验收命令见：

- [Broker Sidecar 实施与远程验证方案](./Argus-Asymmetric-Attestation-SPIFFE-Implementation-Plan.md)
- [SPIRE asymmetric runtime](../core/spire/runtime/asymmetric/README.md)
- [Broker Sidecar](../adapters/OpenViking/broker_sidecar)
- [自定义 WorkloadAttestor](../core/spire/plugins/argus-tdx-workloadattestor)

旧的 `spiffe_server/`、`entrypoint-spiffe.sh` 和 OpenViking Python 直接获取 SVID 的
实现已经删除，不提供运行时开关、回退或兼容入口。
