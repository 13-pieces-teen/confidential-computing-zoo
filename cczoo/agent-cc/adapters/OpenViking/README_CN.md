# OpenViking 适配器

当前 Workload Attestation 部署使用 TC API 的 `nginx-spiffe-helper-v1` 启动
profile、共享 TDX Evidence Provider、SPIRE WorkloadAttestor、支持 Broker API
的 SPIFFE Helper 和 NGINX。架构见[当前方案](../../documents_ly/Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)，
部署见[运行手册](../../core/spire/workload/README.md)，执行结果见[验证记录](../../core/spire/workload/VALIDATION.md)。

TC API 启动 OpenViking，Workload 工具登记实际服务进程。Helper 通过本机 SPIRE
Broker API 订阅该目标的 SVID，并将凭据交付给 NGINX。NGINX 在 1943 端口终止
mTLS，精确核对 OpenClaw 的 SPIFFE ID，再将请求转发到服务 network namespace
中的 `127.0.0.1:1933`。

[OpenClaw 客户端](../OpenClaw/spiffe_client/README.md)通过原生 HTTPS 发起业务请求。
历史设计和验证记录见[文档归档](../../documents_ly/archive/pre-workload-implementation/README.md)。
