# 自定义 SPIRE WorkloadAttestor 与 TC-API 集成技术方案研讨会会议纪要

> 文档来源：语音转写《关于自定义 Spire Workload Attester 与 TCAPI 集成的技术方案深度研讨》
> 会议日期：原始转写未提供
> 会议时段：00:00:48—00:52:16，约 51 分钟
> 参会人：Speaker 1、Speaker 2（原始转写未提供姓名）
> 文档性质：经术语校正和语义整理的会议纪要，不是实现完成证明或最终协议规范

## 一、会议目标

本次会议围绕以下核心问题展开：

1. 在已有 TDX Node Attestation 基础上，如何增加自定义 SPIRE Workload Attestation，进一步证明“当前请求 OpenViking 身份的 Python 进程，确实属于批准的 OpenViking 实例”；
2. 如何把 TC-API 在容器启动阶段记录的镜像、启动命令、挂载、权限、网络和运行时审计信息纳入 Workload Attestation；
3. 如何划分 SPIRE Agent、WorkloadAttestor、Evidence Provider、Trustee、RVPS、TC-API、TruCon 与 Rekor 的职责；
4. 第一阶段应该真实接通哪些组件，哪些部分先使用 Mock，以便尽快形成可运行的最小闭环；
5. 澄清 OpenViking 是否需要感知 SPIRE Workload API，以及 SVID 轮换和 Workload 重新证明的触发关系。

## 二、结论摘要

### 2.1 会上形成的总体方向

1. **Node Attestation 与 Workload Attestation 解决不同问题。** 现有 Node Attestation 证明 SPIRE Agent 所在 TDVM 满足节点侧信任要求；新增 Workload Attestation 需要进一步证明，当前通过 Workload API 请求身份的进程是批准的 OpenViking Python 服务进程。
2. **不能只依赖 PID。** SPIRE Agent 可以从 Workload API 的 Unix Domain Socket 对端取得调用进程 PID，但 PID 会复用。后续证据采集必须把 PID 与稳定的进程实例、容器运行时状态以及 TC-API 启动记录关联起来。
3. **TC-API 启动证据与 TDX Quote 是两类不同材料。** Quote 反映 TDX 环境中的 MRTD、RTMR、REPORTDATA 等信息；TC-API/TruCon/Rekor 侧材料反映容器以何种镜像、命令、挂载、权限和网络配置被启动。不能把“读取 TC-API 日志”表述为“生成 Quote”。两类材料需要建立可验证的绑定，并由 Trustee 联合裁决。
4. **自定义 WorkloadAttestor 负责把验证结果转换为 SPIRE selector。** 会上以 `verified=true` 作为说明流程的示例；这只是示意值，最终 selector 的名称、唯一性和防绕过约束仍需在协议设计中冻结。
5. **Evidence Provider 暂时保留在链路中。** TC-API 不直接承担 Quote 生成和 Trustee 交互的全部职责。初步方向是由 WorkloadAttestor 发起证明，Evidence Provider 收集本机 Quote 和 TC-API/透明日志材料，Trustee 验证后返回同步结果，WorkloadAttestor 再决定是否返回 selector。
6. **第一阶段先搭通框架，再接真实 TDX。** 先梳理 SPIRE Workload Attestation 全链路，使用 Mock Quote、Mock Trustee/reference policy 或 Mock Evidence Provider 完成 selector 到 SVID 的最小闭环；同时尝试将 TC-API 作为 OpenViking 的启动入口。真实 RTMR、Quote/QGS、Trustee/RVPS 策略和 TDX 环境验收放到后续阶段。
7. **OpenViking 如何取得 SVID 尚未最终确认。** 需要进一步核实是由 OpenViking Python 进程直接调用 Workload API，还是通过现有集成组件完成；只有实际调用 Workload API 的进程才是 SPIRE Agent 看到并用于 Workload Attestation 的主体。
8. **SVID 轮换不应直接等同于重新生成 Quote。** 会上以约 5 分钟的 SVID 有效期举例，但实际 TTL 由部署配置决定。正常 SVID 轮换是否复用缓存 selector、何时重新触发 Workload Attestation，以及是否需要 fresh Quote，仍需结合 SPIRE 的流和重连语义进一步确认。

### 2.2 本次没有冻结的内容

- Workload Attestation 的最终协议、字段和接口；
- TC-API 启动记录与 Quote 的具体密码学绑定方式；
- Trustee 与 RVPS 的最终策略模型；
- 最终 selector 设计；
- Workload 重新证明周期与 SVID TTL 的关系；
- 是否保留 Docker WorkloadAttestor 作为并行 selector 来源；
- OpenViking 直接接入 Workload API 的具体代码路径；
- `Argus TDX NodeAttestor` 或后续 WorkloadAttestor 的最终命名。

## 三、讨论背景与问题定义

### 3.1 已有 Node Attestation 的边界

现有方案先通过 Node Attestation 证明 SPIRE Agent 运行在被接纳的 TDX/TDVM 环境中。该过程解决的是节点和 Agent 的入场问题，但不能自动证明某个具体 Python 进程就是批准的 OpenViking 服务实例。

本次新增 Workload Attestation 的目标命题是：

> 当前连接 SPIRE Workload API、请求 OpenViking SPIFFE 身份的进程，是否是由批准的镜像和启动配置拉起、并正在运行预期服务入口的 OpenViking Python 进程。

### 3.2 SPIRE 默认 Docker Workload Attestation 的不足

默认 Docker WorkloadAttestor 可以从调用方 PID 解析 container ID、image digest 等运行时 selector，再与 SPIRE Registration Entry 中的 selector 匹配。

对于本项目，仅有 container ID 或 image digest 仍不完整，因为还需要考虑：

- 实际启动命令和 Entrypoint/Cmd 是否被覆盖；
- 挂载目录是否替换了应用代码或依赖；
- 容器权限和网络配置是否符合预期；
- 当前 PID 是否仍对应最初被测量的进程实例；
- 运行实例是否能够关联到可信、可审计的 TC-API 启动记录。

因此，会议方向不是简单重复 Docker selector，而是让自定义 WorkloadAttestor 使用 TC-API 记录和 TDX 证据完成更强的工作负载证明。

## 四、TC-API 启动与审计链路

### 4.1 会议中梳理的职责

TC-API 相关链路可整理为：

1. OpenViking 或上层 Agent 框架发起 Docker 启动操作；
2. Docker 请求先进入 Docktap 代理，而不是直接进入 Docker daemon；
3. Docktap/TC-API 侧提取镜像、命令、容器和其他运行时元数据，并提交给 TruCon；
4. TruCon 负责可信事件编排、RTMR extend、记录持久化和后续透明日志提交；
5. 请求再转发给真实 Docker daemon，由 Docker daemon 实际拉起容器；
6. 可信记录异步提交到 Rekor 等透明日志后端，供后续公开审计和验证使用。

会议中特别区分了两类失败：

- **本地可信记录或 RTMR extend 失败：** 目标安全语义应为阻止启动，避免出现“工作负载已运行但没有形成可信审计记录”的状态；
- **Rekor 异步上传暂时失败：** 可以进入后台重试流程，不应与本地测量是否成功混为一谈。

TDX 保障的重点是：只要工作负载被允许运行，就能够证明其处于预期、可审计的状态。TDX 不解决恶意宿主机直接拒绝启动或终止 TDVM 的可用性问题，因此宿主机造成的拒绝服务不在本次安全目标内。

### 4.2 TC-API 可提供的关键材料

会上提到的候选材料包括：

- OCI image/manifest digest；
- 容器 ID 与运行实例关联信息；
- Docker run/Create/Start 请求参数；
- Entrypoint/Cmd；
- 挂载目录；
- 权限与安全配置；
- 网络配置；
- 启动事件、顺序和审计链标识；
- 可用于查询 Rekor 记录的 UUID、log index 或其他链路标识。

并非所有字段都适合直接作为 Trustee allowlist 的固定参考值。镜像 digest、批准启动命令等稳定字段可以与预置参考值比较；时间戳等动态字段更适合作为审计和新鲜度信息，不能要求每次与固定值完全相等。

## 五、Workload Attestation 架构逻辑与工作流

### 5.1 已确认的架构描述

> Node Attestation 首先确认 SPIRE Agent 所在的 TDVM 可信。随后，OpenViking 通过 Workload API 请求身份，SPIRE Agent 获取请求进程的 PID，并触发自定义 WorkloadAttestor。WorkloadAttestor 通过 Evidence Provider 收集当前进程的 TDX Quote，以及 TC-API/Rekor 保存的启动度量信息，再将这些证据交给 Trustee 判断该进程是否为可信的 OpenViking workload。验证通过后，WorkloadAttestor 返回相应的可信 selector；SPIRE 根据该 selector 匹配 Registration Entry，并向 OpenViking 交付 SVID。验证失败时不满足身份注册条件，因此不交付 SVID。

这段描述作为当前阶段的架构基线。它只定义组件之间的职责和信息流，不展开证据格式、接口协议、验证策略或具体实现。

### 5.2 按时间与触发顺序的工作流

| 顺序 | 触发条件 | 执行动作 | 阶段结果 |
|---|---|---|---|
| 1 | SPIRE Agent 启动并申请加入信任域 | 系统执行 Node Attestation，确认 Agent 所在 TDVM 是否可信 | 通过后，SPIRE Agent 具备继续为本机 workload 提供身份服务的前提 |
| 2 | OpenViking 需要获取自身身份 | OpenViking 调用本机 SPIRE Agent 提供的 Workload API | 发起一次 Workload Attestation 流程 |
| 3 | SPIRE Agent 收到 Workload API 请求 | SPIRE Agent 获取当前请求进程的 PID，并触发自定义 WorkloadAttestor | 确定本次需要验证的 workload 主体 |
| 4 | 自定义 WorkloadAttestor 被触发 | WorkloadAttestor 请求 Evidence Provider 收集当前进程的证明材料 | 进入 workload evidence 收集阶段 |
| 5 | Evidence Provider 开始收集证据 | Evidence Provider 获取当前进程的 TDX Quote，以及 TC-API/Rekor 保存的启动度量信息 | 形成供 Trustee 判断的 workload evidence |
| 6 | Workload evidence 收集完成 | 证明链路将证据交给 Trustee | Trustee 开始判断当前进程是否为可信的 OpenViking workload |
| 7 | Trustee 完成验证 | Trustee 返回验证通过或验证失败的结果 | 形成身份交付决策 |
| 8A | Trustee 验证通过 | WorkloadAttestor 返回可信 selector，SPIRE 使用该 selector 匹配 Registration Entry | 匹配成功后，SPIRE 向 OpenViking 交付 SVID |
| 8B | Trustee 验证失败 | WorkloadAttestor 不返回满足身份注册条件的 selector | Registration Entry 不匹配，OpenViking 无法获得目标 SVID |

整体触发顺序可以压缩为：

```text
Node Attestation 通过
  → OpenViking 请求身份
  → SPIRE Agent 获取 PID
  → 触发 WorkloadAttestor
  → Evidence Provider 收集证据
  → Trustee 验证
  → 通过则匹配 Registration Entry 并交付 SVID
  → 失败则不交付 SVID
```

### 5.3 各组件的初步职责

| 组件 | 初步职责 | 本次仍待确认的边界 |
|---|---|---|
| OpenViking Python | 触发 Workload API 请求并使用取得的 SVID | 是否必须直接集成 Workload API；何时触发请求；是否在取得身份后再监听业务端口 |
| SPIRE Agent | 从 UDS 对端获得调用方 PID；执行 WorkloadAttestor；依据 selector 匹配身份 | selector、Registration Entry 和本地缓存的准确交互语义 |
| 自定义 WorkloadAttestor | 编排本次 workload 证明，并只在验证通过时返回强制 selector | 是否直接调用 Trustee，还是统一经 Evidence Provider 调用 |
| Evidence Provider | 采集进程、容器、TC-API 和 TDX Quote 相关材料 | PID 稳定引用、TC-API 查询接口、证据格式和绑定协议 |
| TC-API / Docktap / TruCon | 拦截启动、记录运行时参数、推进可信日志链并提供实例关联材料 | 面向 Evidence Provider 的读取接口和权威查询键 |
| Rekor / TLog | 保存可审计的透明日志材料 | 查询结果、链头和当前运行实例如何绑定 |
| Trustee | 验证 Quote、新鲜度、证据绑定和 workload policy，返回同步裁决 | Workload 验证是否复用已有 Node admission；是否需要新的 workload Quote |
| RVPS | 保存或提供预期参考值 | 哪些字段稳定且应进入批准策略 |
| SPIRE Server | 管理 Registration Entry 和 CA 签发职责 | 不应直接把原始 Quote 解析职责塞入标准 Server 路径；具体缓存/签发调用待核实 |

### 5.4 selector 与 SVID 交付

会上使用 `verified=true` 说明“Trustee 已批准本次 workload”的结果如何进入 selector 匹配。这个例子便于解释流程，但安全实现还需要解决：

1. selector 必须只由自定义 WorkloadAttestor 在本次验证成功后产生；
2. 目标 OpenViking Registration Entry 必须强制要求该 selector；
3. 不能保留一条只依赖较弱 Docker selector、却能交付同一高权限 SPIFFE ID 的旁路 Entry；
4. selector 最好绑定明确的 policy/version/digest，避免通用布尔值被不同策略误用；
5. Trustee 的 ALLOW 应绑定当前 challenge/session，不能成为可离线复用的通用通过标志。

这些是对会议示例的安全约束，最终字段仍需在设计文档中冻结。

## 六、关键技术争议与澄清

### 6.1 SVID 轮换是否自动重新执行 Workload Attestation

会上存在两种理解：

- SVID 到期后由 SPIRE Agent 自动轮换，业务双方无需干预；
- Workload Attestation 可能也需要按周期自动重新执行，并重新生成证明。

会议未对这一点形成最终结论。需要进一步核实的准确问题是：

1. 当前 Workload API 客户端是保持流、重连，还是每次主动重新请求；
2. SPIRE Agent 在什么条件下复用已缓存的 workload selector；
3. fresh Workload Attestation 的触发条件是什么；
4. fresh Workload Attestation 是否必须生成新的 TDX Quote；
5. SVID TTL、workload evidence freshness 和 Node Attestation freshness 是否应使用不同策略。

会上提到的“约 5 分钟”只是讨论示例，不是已经确认的生产配置。

### 6.2 是否需要第二次 TDX 证明

会议确认 Node Attestation 和 Workload Attestation 是两层不同证明，但没有最终决定 Workload 阶段需要：

- 重新执行一次完整 Node 级远程证明；
- 生成只绑定 workload claims 和 fresh challenge 的新 Quote；
- 或依赖既有 Node admission，再使用其他受保护机制绑定 workload claims。

当前最小实现应先明确威胁模型和新鲜度要求，再选择方案。不能仅因为 SVID 轮换就默认重复完整 Node Attestation，也不能在没有受保护绑定的情况下把普通软件 claims 当作 TEE-backed evidence。

### 6.3 PID 复用与 TOCTOU

只读取一个整数 PID 不足以证明进程身份。实现至少需要核实：

- Evidence 采集期间进程实例没有被替换；
- PID 对应的启动时间或稳定进程句柄保持一致；
- PID 所属 namespace、cgroup、container 与 TC-API 记录一致；
- 请求 SVID 的进程与最终提供 OpenViking 服务的进程是否为同一主体；
- 取得 SVID 到开始监听之间是否存在可被替换的窗口。

### 6.4 Quote 与 TC-API 记录如何绑定

会议确认二者必须能够“相互印证”，但没有冻结绑定方法。后续设计必须明确：

- 哪些 workload claims 写入或摘要绑定到 REPORTDATA；
- TC-API/Rekor 记录以什么权威 ID 被引用；
- Trustee 如何确认该记录属于当前 TD boot、当前容器和当前进程实例；
- 如何阻止旧记录、旧 Quote 或其他实例的材料被重放或拼接；
- Node Attestation 已认可的节点上下文如何被 Workload 验证查询和关联。

### 6.5 是否保留 Docker WorkloadAttestor

SPIRE 可以同时启用多个 WorkloadAttestor。会上倾向于第一阶段先走自定义链路，不重复增加已有 TC-API 证据中已经覆盖的 image digest 检查。

是否同时保留 Docker selector 仍可在后续评估，但需要避免两种风险：

- 重复检查却没有增加实际安全性；
- 某条较弱 Registration Entry 绕过自定义 attestation，交付同一目标身份。

## 七、分阶段实施建议

### 阶段 1：链路与 Mock 闭环

目标：先证明组件调用顺序、selector 匹配和 SVID 交付路径可行。

- 画清 SPIRE Workload API、SPIRE Agent、WorkloadAttestor、Evidence Provider、Trustee 和 Registration Entry 的交互；
- 确认 Workload API 的实际调用者和 PID 获取行为；
- 使用 Mock Quote、Mock Trustee/reference policy 或 Mock Evidence Provider；
- 让 Mock 验证结果驱动强制 selector；
- 验证命中与未命中 selector 时的 SVID 正、负路径；
- 验证 OpenViking 是否需要代码改造或 pre-serve 集成。

### 阶段 2：TC-API 启动器接入

目标：先让 OpenViking 的启动操作稳定经过 TC-API/Docktap，即使真实 TDX 证明暂未接入。

- 通过 Docktap 代理启动 OpenViking；
- 获取容器、镜像、命令和 launch/workload 关联记录；
- 定义 Evidence Provider 查询 TC-API/TruCon 的最小接口；
- 验证记录失败时的 fail-closed 启动语义；
- 区分本地可信提交成功与 Rekor 异步上传状态。

### 阶段 3：真实 TDX 与 Trustee/RVPS

目标：在真实 TDX 环境完成端到端验收。

- 获取真实 Quote/QGS；
- 确定 workload claims 与 REPORTDATA 的绑定；
- 确定与已有 Node admission 的关联方式；
- 在 Trustee/RVPS 中配置批准策略和稳定参考值；
- 验证 replay、PID 替换、镜像/命令篡改、挂载替换和 Trustee 不可用等负路径；
- 分别报告 Mock 通过、真实 TDX 通过和生产接受，不能互相替代。

## 八、代码与项目进展（参会者口头报告，未在本次会议中验收）

Speaker 2 报告了以下进展：

1. 已按要求调整 OpenClaw 的 TDVM 部署位置；
2. 已完成部分 Go 代码重构，按 `cmd`、`internal`、Agent plugin 和 Server plugin 等职责重新梳理目录；
3. 已清理部分历史兼容逻辑；
4. 现有某个帮助 OpenViking 感知 SVID 的组件可能可以删除，但还需要确认 Workload API 的实际接入方式后再决定；
5. 插件命名被反馈容易混淆，但本次会议暂不改名，待职责和边界稳定后再统一处理。

以上内容是会议中的状态汇报，不等同于代码审查、测试通过或真实环境验收结果。

## 九、行动项

| 编号 | 行动项 | 负责人 | 完成标准 | 时间 |
|---|---|---|---|---|
| A1 | 重画并更新 Workload Attestation 方案图 | Speaker 2 | 明确 Workload API、PID、WorkloadAttestor、Evidence Provider、TC-API、Trustee、RVPS、selector 和 SVID 的调用顺序 | 待定 |
| A2 | 核实 SPIRE Workload API 与 SVID 轮换语义 | Speaker 2 | 回答调用者是谁、是否需要修改 OpenViking、何时重跑 Workload Attestation、何时复用缓存 selector | 待定 |
| A3 | 核实自定义 WorkloadAttestor 的插件边界 | Speaker 2 | 明确由谁请求 challenge、谁调用 Evidence Provider、谁调用 Trustee、谁返回 selector | 待定 |
| A4 | 搭建 Mock 最小闭环 | Speaker 2 | Mock 验证通过时交付目标 SVID，失败时无法取得目标 SVID，并具备明确的正、负测试 | 待定 |
| A5 | 尝试把 TC-API/Docktap 作为 OpenViking 启动入口 | Speaker 2 | OpenViking 可通过代理路径启动，并能读取到对应运行时审计记录 | 待定 |
| A6 | 定义 TC-API 记录与 workload evidence 的关联键 | Speaker 2 | Evidence Provider 能从当前 PID/容器实例定位唯一、可验证的启动记录 | 待定 |
| A7 | 协调可用的 TDX 环境 | Speaker 1 | 获得可用于 TC-API RTMR、Quote/QGS 和后续端到端验证的环境 | 待定 |
| A8 | 复核被删除或拟删除的兼容组件 | Speaker 2 | 确认删除不会破坏 OpenViking 获取和使用 SVID 的实际路径 | 待定 |
| A9 | 暂缓插件改名，待职责冻结后统一命名 | Speaker 1、Speaker 2 | NodeAttestor 与 WorkloadAttestor 名称不混淆，并与实际职责一致 | 待定 |
| A10 | 与 Luna 沟通转正/校招名额 | Speaker 2 | 确认是否存在转正或应届招聘机会以及后续流程 | 待定 |

## 十、风险与待确认清单

1. **调用主体风险：** 如果由独立 materializer 请求 SVID，SPIRE 证明的是 materializer，而不是最终 OpenViking Python 服务进程。
2. **PID 复用风险：** PID 与进程实例没有稳定绑定时，可能发生错误归属或 TOCTOU。
3. **证据拼接风险：** Quote、TC-API 记录、Rekor 条目和当前 workload 没有统一会话/实例绑定时，可能被跨实例重放或拼接。
4. **selector 过宽风险：** 通用 `verified=true` 可能被其他验证路径误用，需绑定明确策略或版本。
5. **Registration Entry 旁路：** 如果同一 SPIFFE ID 仍有弱 selector Entry，自定义 Workload Attestation 可能被绕过。
6. **新鲜度混淆：** Node Attestation、Workload Attestation、SVID TTL 和 mTLS 连接寿命是不同时间维度，不能共用一个“自动更新”概念。
7. **透明日志状态混淆：** 本地 RTMR/可信记录成功、Rekor 已提交、Rekor 可查询是不同状态，需要分别表达。
8. **环境依赖：** 没有真实 TDX、Quote/QGS 和生产 Trustee/RVPS 时，只能证明 Mock 或接口链路，不能宣称真实远程认证完成。
9. **可用性边界：** TDX 不防御宿主机拒绝服务；宿主机阻止 TDVM 运行时，方案只能安全失败，不能保证服务可用。

## 十二、术语校正说明

原始语音存在较多同音词和英文技术词识别错误。本文结合会议上下文和项目现有命名，统一采用以下术语：

| 统一术语 | 含义 |
|---|---|
| SPIRE / SPIRE Agent / SPIRE Server | SPIFFE Runtime Environment 及其 Agent、Server |
| SPIFFE ID / X.509-SVID | 工作负载身份与证书形式的 SVID |
| NodeAttestor / WorkloadAttestor | SPIRE 节点认证与工作负载认证插件 |
| Workload API | 工作负载从 SPIRE Agent 获取身份的本地 API |
| OpenViking | 本次计划进行 workload 级证明的服务 |
| OpenClaw | 与 OpenViking 交互的 Agent 侧组件，不能与 OpenViking 混称 |
| TDX / TDVM | Intel TDX 与其可信域虚拟机环境 |
| Quote / MRTD / RTMR / REPORTDATA | TDX 证明与测量相关字段 |
| Evidence Provider | 收集证据并生成 Quote 的独立组件 |
| Trustee / RVPS | 证明验证与参考值服务 |
| TC-API / Docktap / TruCon | 容器控制面、Docker 代理与可信事件核心 |
| Rekor / TLog | 透明日志后端与项目可信日志能力 |

## 十三、一句话会议结论

先用 Mock 搭通“OpenViking 实际进程请求 SVID → SPIRE Agent 取得 PID → 自定义 WorkloadAttestor 调用 Evidence Provider → 联合验证 TDX Quote 与 TC-API 启动记录 → Trustee ALLOW 后产生强制 selector → SPIRE 交付目标 SVID”的最小闭环；随后再在真实 TDX 环境中补齐证据绑定、Trustee/RVPS 策略和端到端验收。
