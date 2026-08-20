# OpenViking Workload Attestation 架构逻辑与工作流

> 状态：Broker Sidecar 决策前的架构逻辑记录
>
> 来源：[自定义 SPIRE WorkloadAttestor 与 TC-API 集成技术方案研讨会会议纪要](./Custom-Workload-Attestor-TC-API-Meeting-Notes-CN.md)
>
> 文档边界：本文只说明整体架构、组件关系以及时间与触发顺序，不定义协议字段、接口格式、selector 形式或具体实现。
>
> 后续决策：文中的“OpenViking Python 直接调用 Workload API 并持有 SVID”已经由
> [Broker Sidecar 当前架构](./Argus-Asymmetric-Attestation-SPIFFE-Architecture.md)
> 取代。Node Attestation、TC-API、Evidence Provider、Trustee、selector 与静态
> Registration Entry 的整体逻辑仍作为历史背景保留。

## 1. 文档目的

本文将会议讨论从时间线中抽离，形成一份可以独立阅读的 Workload Attestation 架构说明。

当前阶段只回答三个问题：

1. 为什么在 Node Attestation 之后还需要 Workload Attestation；
2. 各组件在整体架构中承担什么职责；
3. 从环境建立到 OpenViking 获得 SVID，事件按照什么顺序触发。

## 2. 核心目标

Node Attestation 已经确认 SPIRE Agent 所在的 TDVM 可信，但它不能单独证明当前请求身份的进程就是可信的 OpenViking workload。

Workload Attestation 需要进一步回答：

> 当前向 SPIRE Agent 请求身份的进程，是否是通过可信启动链路运行、并满足预期条件的 OpenViking workload。

验证通过后，SPIRE 才向该 workload 交付对应的 SVID；验证失败时不交付 SVID。

## 3. 已确认的架构描述

> Node Attestation 首先确认 SPIRE Agent 所在的 TDVM 可信。随后，OpenViking 通过 Workload API 请求身份，SPIRE Agent 获取请求进程的 PID，并触发自定义 WorkloadAttestor。WorkloadAttestor 通过 Evidence Provider 收集当前进程的 TDX Quote，以及 TC-API/Rekor 保存的启动度量信息，再将这些证据交给 Trustee 判断该进程是否为可信的 OpenViking workload。验证通过后，WorkloadAttestor 返回相应的可信 selector；SPIRE 根据该 selector 匹配 Registration Entry，并向 OpenViking 交付 SVID。验证失败时不满足身份注册条件，因此不交付 SVID。

这段描述是当前架构基线。后续设计可以细化组件接口和验证方法，但不应改变这条主链路，除非重新进行架构讨论。

## 4. 整体架构

整个架构由五个层次组成：

| 层次 | 主要组件 | 职责 |
|---|---|---|
| 节点信任层 | TDX、SPIRE Agent、Node Attestation | 确认 SPIRE Agent 所在的 TDVM 可信 |
| 启动度量层 | TC-API、RTMR、Rekor | 启动 OpenViking，并记录其启动过程的度量与审计信息 |
| Workload 识别层 | OpenViking、Workload API、WorkloadAttestor | 确认本次请求身份的具体进程，并发起 Workload Attestation |
| 证据与验证层 | Evidence Provider、Trustee | 收集 Quote 和启动度量信息，并判断当前 workload 是否可信 |
| 身份交付层 | SPIRE Agent、Registration Entry、SVID | 根据验证结果匹配身份规则，并决定是否交付 SVID |

### 4.1 两条关联链路

架构中存在两条先后发生、最终汇合的链路。

第一条是 OpenViking 启动度量链：

```text
TC-API 启动 OpenViking
  → 写入并 extend RTMR
  → 形成启动度量记录
  → 将透明日志提交到 Rekor
```

第二条是 OpenViking 身份申请链：

```text
OpenViking 调用 Workload API
  → SPIRE Agent 获取 PID
  → WorkloadAttestor 发起证明
  → Evidence Provider 收集证据
  → Trustee 验证
  → SPIRE 决定是否交付 SVID
```

两条链路在 Evidence Provider 和 Trustee 阶段汇合：当前进程的 TDX Quote 与此前形成的 TC-API/Rekor 启动度量信息共同用于判断该进程是否为可信的 OpenViking workload。

## 5. 组件职责

| 组件 | 架构职责 |
|---|---|
| OpenViking | 作为被验证的 workload，通过 Workload API 发起身份请求 |
| SPIRE Agent | 接收身份请求、获得调用进程 PID、触发 WorkloadAttestor，并负责向 workload 交付身份 |
| Node Attestation | 在 Workload Attestation 之前确认 SPIRE Agent 所在 TDVM 可信 |
| 自定义 WorkloadAttestor | 组织 Workload Attestation，并把验证结果转换为 SPIRE 可用于身份匹配的 selector |
| Evidence Provider | 收集当前进程的 TDX Quote，以及与该进程启动相关的 TC-API/Rekor 度量信息 |
| TC-API | 负责启动 OpenViking，并记录启动过程中的度量和审计信息 |
| RTMR | 保存由可信启动与运行事件推进的度量状态 |
| Rekor | 保存 TC-API 提交的透明日志，为后续验证提供可查询的审计记录 |
| Trustee | 根据 Evidence Provider 提供的证据，判断当前进程是否为可信的 OpenViking workload |
| Registration Entry | 预先定义 OpenViking 获得目标身份需要满足的 selector 条件 |
| SPIRE Server | 管理 Registration Entry，并承担标准 SVID 签发职责 |

## 6. 按时间与触发顺序的工作流

### 阶段一：建立节点信任

| 顺序 | 触发条件 | 执行动作 | 结果 |
|---|---|---|---|
| 1 | SPIRE Agent 启动并申请加入信任域 | 系统执行 Node Attestation，确认 Agent 所在 TDVM 是否可信 | 验证通过后，SPIRE Agent 获得为本机 workload 提供身份服务的前提 |

如果 Node Attestation 失败，后续 Workload Attestation 不应进入正常身份交付流程。

### 阶段二：启动并记录 OpenViking

| 顺序 | 触发条件 | 执行动作 | 结果 |
|---|---|---|---|
| 2 | 系统准备启动 OpenViking | TC-API 作为启动入口启动 OpenViking | OpenViking 进程被创建 |
| 3 | TC-API 执行启动流程 | TC-API 将启动度量写入并 extend RTMR，同时形成对应的透明日志 | 当前 OpenViking 实例形成可供后续验证的启动记录 |
| 4 | 启动度量记录形成 | TC-API 将透明日志提交到 Rekor | Rekor 保存可查询的 OpenViking 启动度量信息 |

### 阶段三：OpenViking 发起身份请求

| 顺序 | 触发条件 | 执行动作 | 结果 |
|---|---|---|---|
| 5 | OpenViking 需要取得自身身份 | OpenViking 调用本机 SPIRE Agent 提供的 Workload API | 发起一次 Workload Attestation |
| 6 | SPIRE Agent 收到 Workload API 请求 | SPIRE Agent 获取当前请求进程的 PID，并触发自定义 WorkloadAttestor | 确定本次需要验证的 workload 主体 |

### 阶段四：收集并验证 Workload 证据

| 顺序 | 触发条件 | 执行动作 | 结果 |
|---|---|---|---|
| 7 | 自定义 WorkloadAttestor 被触发 | WorkloadAttestor 请求 Evidence Provider 收集当前进程的证明材料 | 进入 workload evidence 收集阶段 |
| 8 | Evidence Provider 开始工作 | Evidence Provider 获取当前进程的 TDX Quote | 获得当前 TDX 环境的证明材料 |
| 9 | Evidence Provider 需要关联启动记录 | Evidence Provider 通过 TC-API 取得对应的 Rekor 查询信息，并获取 OpenViking 启动度量日志 | 获得当前 workload 的启动度量材料 |
| 10 | 两类证据收集完成 | 证明链路将 TDX Quote 和启动度量信息交给 Trustee | Trustee 开始判断当前进程是否可信 |
| 11 | Trustee 完成验证 | Trustee 返回验证通过或验证失败的结果 | 形成身份交付决策 |

阶段四只规定 Trustee 使用两类证据完成判断。两类证据如何绑定、具体由哪个组件调用 Trustee，留待后续设计。

### 阶段五：交付或拒绝身份

| 顺序 | 触发条件 | 执行动作 | 结果 |
|---|---|---|---|
| 12A | Trustee 验证通过 | WorkloadAttestor 返回可信 selector，SPIRE 根据该 selector 匹配 Registration Entry | 匹配成功后，SPIRE 向 OpenViking 交付 SVID |
| 12B | Trustee 验证失败 | WorkloadAttestor 不返回满足身份条件的 selector | Registration Entry 不匹配，OpenViking 无法获得目标 SVID |

## 7. 完整触发链

```text
SPIRE Agent 启动
  → Node Attestation 通过
  → TC-API 启动 OpenViking
  → TC-API extend RTMR 并向 Rekor 提交启动度量日志
  → OpenViking 调用 Workload API 请求身份
  → SPIRE Agent 获取请求进程 PID
  → SPIRE Agent 触发自定义 WorkloadAttestor
  → Evidence Provider 收集 TDX Quote 和 TC-API/Rekor 启动度量信息
  → Trustee 判断当前进程是否为可信 OpenViking workload
  → 验证通过：返回 selector，匹配 Registration Entry，交付 SVID
  → 验证失败：不满足 Registration Entry，不交付 SVID
```

## 8. 成功与失败路径

### 8.1 成功路径

```text
Node Attestation 通过
AND OpenViking 已由 TC-API 启动并形成度量记录
AND Evidence Provider 成功取得两类证据
AND Trustee 判断当前 workload 可信
AND selector 命中 Registration Entry
THEN SPIRE 向 OpenViking 交付目标 SVID
```

### 8.2 失败路径

以下任一关键阶段失败，最终结果均是不交付目标 SVID：

- Node Attestation 未通过；
- 无法确定请求身份的 OpenViking 进程；
- Evidence Provider 无法取得所需证明材料；
- Trustee 验证失败；
- WorkloadAttestor 没有返回满足身份条件的 selector；
- selector 未命中目标 Registration Entry。

## 9. 当前阶段不展开的内容

以下内容需要后续单独进行设计，不属于本文范围：

- Quote、RTMR 和 Rekor 日志的具体绑定方法；
- Evidence Provider、WorkloadAttestor 与 Trustee 的接口协议；
- Rekor UUID 和日志内容的具体查询方式；
- Trustee 的验证规则与参考值管理；
- selector 的具体命名和字段；
- Registration Entry 的具体配置；
- SVID 有效期、轮换和重新执行 Workload Attestation 的关系；
- Mock、真实 TDX 和生产环境的部署与验收方案。

## 10. 架构结论

本架构的核心逻辑是：

> Node Attestation 先建立 TDVM 和 SPIRE Agent 的节点信任；TC-API 在 OpenViking 启动阶段形成 RTMR 与 Rekor 度量记录；OpenViking 请求身份时，自定义 WorkloadAttestor 再通过 Evidence Provider 收集当前进程的 TDX Quote 和对应启动度量信息，并交由 Trustee 判断。只有 Trustee 验证通过并满足 Registration Entry 条件时，SPIRE 才向 OpenViking 交付 SVID。
