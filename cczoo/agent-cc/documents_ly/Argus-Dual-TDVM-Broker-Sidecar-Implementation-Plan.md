# Argus 双 TDVM Egress / Ingress Broker 实施与验证计划

> 对应架构：[双 TDVM Broker 架构](./Argus-Dual-TDVM-Broker-Sidecar-Architecture.md)
>
> 状态：代码实施完成；本地回归与远程双 TDVM 验收状态分别记录

## 1. 当前唯一方案

OpenClaw 和 OpenViking 业务容器均保持源码无侵入。OpenClaw 插件通过本地普通 HTTP
调用 Egress Broker；Egress Broker 负责 Guard 授权和客户端 SPIFFE mTLS；OpenViking
Ingress Broker 负责服务端 mTLS 和回环 HTTP 转发。

旧 asymmetric runtime、OpenClaw preload、SVID materializer、Argus entrypoint 和对应
benchmark 不再作为兼容路径保留。

## 2. 实施内容

### OpenClaw runtime

- 使用 `Dockerfile.sbx-runtime` 构建 dual-TDVM 专用镜像；
- 保留 Docker CLI、sandbox 和 TDX skills；
- 以一次性容器完成 volume ownership 与 gateway/sandbox 配置；
- 业务容器直接执行 `node /app/dist/index.js gateway`；
- 不复制 materializer/preload，不设置 `NODE_OPTIONS`，不挂载 SPIRE/SVID/Guard。

### OpenClaw Egress Broker

- 独立 Go 服务位于 `adapters/OpenClaw/egress_sidecar`；
- 以自身 Broker 身份连接本 TDVM Broker API，通过真实 OpenClaw PID 订阅调用方 SVID；
- 在 Docker 网络监听 `0.0.0.0:1934`，不发布宿主机端口；
- 每个请求单独调用 Guard，不缓存 decision，不读取或摘要请求正文；
- ALLOW 后固定连接 `https://openviking.argus.local:1943`，精确校验 OpenViking ID；
- 目标 PID 或 Broker subscription 结束时退出，SVID 只保存在内存。

### dual-TDVM 接线

- OpenClaw Agent 增加仅授权 Egress Broker 的 Broker API socket；
- 创建四个强 Entry：两个 Broker、两个真实 workload；
- 启动顺序为 Guard、OpenClaw、读取 PID、Egress Broker、插件连接；
- 插件 `baseUrl` 固定为 `http://argus-dual-openclaw-egress:1934`；
- 外部 OpenViking host mapping 只配置给 Egress Broker；
- OpenViking Ingress Broker 和外部 mTLS 1943 保持不变。

## 3. 本地验证

- Egress Broker：`go test ./...`、`go vet ./...`、Linux amd64 build；
- Ingress Broker与 WorkloadAttestor 定向回归；
- Guard ALLOW/DENY/异常、无效 receipt、错误服务端 ID、PID 退出单元测试；
- 所有修改 shell 脚本 `bash -n`；
- Dockerfile 静态检查、Registration selector 审计、`git diff --check`；
- 全仓引用检查不再出现已删除的正式 asymmetric/preload/materializer 入口。

## 4. 远程双 TDVM 验收

1. 检查 OpenClaw 业务容器无 Argus 注入和身份材料；
2. 检查两个 Broker 的 target PID、socket mount、镜像 digest 和 Entry selectors；
3. 检查插件内部 baseUrl、Egress 不发布端口、Ingress 只接受精确客户端 ID；
4. Guard ALLOW 完成跨 TDVM `/health=200`；DENY 返回 403 且不上游；
5. 执行真实 OpenClaw agent turn，验证 OpenViking session capture、commit、archive；
6. 验证无客户端证书、错误客户端 ID、错误服务端 ID和明文 1933 负向路径；
7. 停止两个业务容器，验证对应 Broker 因 pidfd 退出；重启 OpenClaw 后使用新 PID；
8. 单独记录 `/ready` 的模型依赖状态，不把它混入身份链路结论。

## 5. 完成边界

本轮只保证可信计算基内的正常 OpenViking 插件路径经过 Guard。Provider/Trustee 的
Mock、真实 Quote/QGS 和生产验收边界不因本改动改变。远程验收实际运行前，只能报告
本地代码与静态/单元测试结果，不能声明新的双 TDVM 运行证据。
