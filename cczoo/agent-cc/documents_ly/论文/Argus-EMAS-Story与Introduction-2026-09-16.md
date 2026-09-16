# Argus：论文 Story 与 Introduction

日期：2026-09-16。本文依据 Agent-CC v0.8 的整体部署架构与可信服务组合定位，结合当前 Argus 实现、Related Work 和实验方案修订。Introduction 按任务与目标、已有方法与技术问题、核心架构、关键机制与应用价值、实验安排五部分组织。中英文正文均为讨论稿；评价保留计划语态，低侵入与跨框架复用作为待验证的设计目标。

文档定位（2026-09-16 整理）：当前 Story 与引言的写作入口。章节、方法 pipeline 和候选贡献维护在[论文框架](./Argus-AAMAS2027-EMAS-论文框架.md)，实验细节维护在[评价方案](./Argus-EMAS-Agent场景实验设计与论文借鉴-2026-09-14.md)。其他材料的分工见[论文导航](./README.md)。

## 论文 Story

Agent 完成任务时，敏感上下文会随着模型调用、记忆读写和工具执行，在多个组件之间流动。例如，Agent 将对话、项目约束和任务结果写入独立的记忆服务，供后续任务检索和复用；记忆服务也因此成为直接接收和处理私有数据的一方。即使 Agent 自身运行在机密环境中，数据交付后的保护仍取决于接收方的软件和运行条件。保护 Agent 的敏感数据，因此需要覆盖参与任务处理的各个服务及其交互。

Agent-CC 从整体部署架构回应这一需求，将数据全生命周期保护、构建到运行时完整性和可信服务组合联系起来。Argus 聚焦其中的可信服务组合：在敏感数据交给目标服务前，根据其身份、执行环境和工作负载证据，判断该服务是否满足参与处理的条件。这里的服务可以属于同一组织，也可以由其他组织运营；需要衔接的是数据流经的不同执行边界上的信任要求。

远程证明为这种判断提供了证据，而实际的数据交付还需要将核验结果与运行中的服务对应起来：经过核验的是哪个业务实例，该实例使用什么身份，以及请求通过哪个入口到达它。服务重启、实例替换或身份失效后，这些对应关系还可能发生变化，系统需要判断原有准入依据是否仍然有效，并据此约束后续交付。因此，可信服务组合既涉及交付前的准入，也涉及运行变化后的失效处理与重新准入。

要让不同的 Agent 和服务采用这套机制，还需要将取证、验证和准入实现为可复用的运行时能力，减少应用在业务逻辑和接口上的改动。由此，Argus 的研究问题是：**如何以低侵入方式，将执行环境与工作负载的证明结果转化为已有 Agent 系统可用的服务准入能力，使敏感数据交付受接收方身份和运行条件约束，并在相关条件变化后更新准入状态？**

围绕这一问题，Argus 以部署与身份层承接证明和准入职责，将获准的业务实例、通信身份与实际接收入口关联起来，并设计相应的失效处理和恢复机制。论文计划通过真实 Agent 任务与系统负载，检验这些约束能否落实到实际交付，以及它们带来的接入改动、运行开销和停止恢复成本，从而评价可信服务组合在现有 Agent 系统中的可用性。

围绕可信服务组合，论文需要依次回答**服务凭什么获准、准入如何约束实际交付，以及运行变化后如何维护这些约束**三个研究问题：

| 研究问题 | 需要解决的关系 | 预期研究产出 |
|---|---|---|
| 准入依据：哪些服务可以接收敏感数据？ | 平台证据、工作负载属性与目标实例共同支持什么准入判断；结论依赖哪些受信组件、策略和有效性条件。 | 明确服务准入条件与证据要求，比较不同取证和证明组合能够支持的声明。 |
| 交付落实：准入如何约束真正的数据接收方？ | 低侵入部署中，业务进程、身份使用者与 TLS 入口可能分离；获准实例需要与实际接收路径对应。 | 建立实例、身份和入口的关联及执行机制，说明可复用的接入方式和受信边界。 |
| 运行维护：条件变化后如何处理后续交付？ | 进程实例、身份凭据和连接具有不同生命周期，原准入结果可能不再适用于后续交互。 | 定义可观测变化下的失效、停止与重新准入规则，分析停止窗口、恢复成本和可用性。 |

三个问题形成“建立准入依据—将依据用于交付—在变化中维护依据”的递进关系。分层证明、工作负载身份和通信代理是回答这些问题的机制选择；真实 TDX 上的系统评价为三个问题共同提供证据。各项研究的贡献需要落在具体机制及其比较发现上。

低侵入与复用贯穿这三个问题。将证明和准入职责交给部署与身份层，有助于减少业务改造，也要求明确这些受信组件如何约束实际接收方。评价将记录业务逻辑、适配器、核心代码、配置及部署步骤的改动，说明接入需要多少调整、哪些部分可以复用，以及哪些部分仍需针对应用适配。

比较实验按研究问题组织。在准入依据方面，B3b 基线保留同等的实例取证、策略检查、凭据发布和失效处理能力，但不额外生成 Workload Quote，用于检验这份额外证明的作用和开销。交付落实与运行维护则结合实例和入口错配、运行变化等受控情形及单项消融，检查实际接收、停止窗口和恢复过程。机制设计与实验发现还需与最接近的相关工作比较。

跨企业研发与银行核验用于说明框架的潜在应用场景。当前评测计划聚焦 Agent 对上下文服务的访问，采用 OpenViking 的 τ²-bench 适配、LoCoMo/OpenClaw 和 Server 混合负载：前两者用于观察框架对 Agent 任务执行的影响，后者用于分析服务性能和并发负载下的系统开销。

## Introduction：中文论证稿

### 1. 任务、应用与评价目标

Agent 通过模型调用、记忆读写和工具执行完成任务，敏感上下文也随之在多个组件之间流动。以服务化记忆为例，企业助手将私有对话、项目约束和任务结果写入独立的记忆服务，再在后续任务中检索和复用。记忆服务因此成为直接处理私有数据的一方。即使 Agent 自身运行在可信执行环境中，数据交付后的保护仍取决于接收方的软件和运行条件。本文沿 Agent-CC 的可信服务组合定位，研究如何将本地机密执行的信任要求落实到跨服务的数据处理路径。[Agent-CC](../../../../output/pdf/Agent-CC.zh-CN.md#5-可信服务组合)

这一任务需要同时考虑交付约束、任务可用性与工程成本。系统应依据批准的身份和执行条件决定目标实例能否接收敏感数据，并在相关条件失效后约束后续交付；满足这些条件的任务应能够正常完成，并在适用故障处理后恢复服务。相应地，评价需要观察实际接收者、停止与恢复过程、任务质量和完成情况，以及首次可信就绪、稳态访问和应用接入的额外成本。这些目标共同决定可信服务组合能否被已有 Agent 系统实际采用。

### 2. 已有方法与技术问题

已有工作从运行证据、服务身份和 Agent 平台等层面建立了技术基础。SPIFFE/SPIRE 将节点与工作负载条件纳入身份签发；Full Trust Alchemist 研究动态工作负载属性的证明，dstack-capsule 将平台证据与 Pod 身份分层关联；aDNS 将证明结果关联到服务身份与 TLS 密钥，Grimlock 提出了证明与具体通信通道绑定的设计；Omega 则从整体平台出发，研究可信 Agent 执行及外部交互管控。这些工作为跨服务信任提供了不同的实现路径。[SPIRE](https://spiffe.io/docs/latest/spire-about/spire-concepts/)、[Full Trust Alchemist](https://www.comsys.rwth-aachen.de/publication/2025/2025_galanou_trust-alchemist/)、[dstack-capsule](https://arxiv.org/html/2606.03323v2)、[aDNS](https://www.usenix.org/conference/usenixsecurity25/presentation/delignat-lavaud)、[Grimlock](https://arxiv.org/html/2605.27488v2)、[Omega](https://arxiv.org/abs/2512.05951v2)

在已有应用中采用这些能力，需要协调服务调用方式与可信执行条件之间的差异。应用通过稳定的接口和逻辑身份持续访问服务，而准入证据对应的是具体平台、软件、配置与运行实例。为了减少业务改造，取证、身份管理和 TLS 处理可以由公共组件承担；采用代理接入时，被核验的业务进程与凭据持有者、TLS 终止入口可能分属不同组件。框架需要说明这些主体如何保持对应，以及调用方通过服务身份所依赖的准入条件如何落实到实际数据路径。

这种对应关系还受运行变化影响。服务实例、身份凭据和通信连接具有不同的生命周期：正常证书轮换未必改变实例的准入资格，实例替换却可能使仍在使用的身份或连接失去原有依据。多轮 Agent 任务需要复用有效身份和连接来控制开销，也需要在相关条件失效时停止继续交付。本文据此研究：**如何以较少的业务改动，将运行证据形成的准入判断落实到实际接收实例，并在运行变化后维护交付约束？** 与已有方案的比较将围绕相同交付要求下的实例关联、变化处理和接入成本展开，以识别各项设计的作用及取舍。

### 3. Argus 的核心架构

我们提出 Argus，一个基于 Intel TDX 远程证明与工作负载身份的 Agent 可信服务组合框架。Argus 将证明和准入职责置于部署与身份层，使应用通过身份认证和通信适配使用准入结果。在服务准入阶段，框架依据受控启动记录和受信组件的实例取证，将平台证据、实际业务实例及其软件和配置纳入策略核验。通过核验的结果参与 SPIRE 身份准入；调用方核对预期服务身份，并依赖所认可签发域执行的准入规则。凭据交付与受保护的通信入口进一步将获准身份关联到实际业务进程。

Argus 将证明准入与稳态业务交互分开。正常业务请求复用有效身份和连接，无须逐次生成 Quote 或调用证明验证服务。当前服务端原型由 Helper 交付凭据、NGINX 终止 TLS，并经受保护的本地路径访问业务进程；实例检查、凭据状态和连接管理共同约束身份与连接的复用。这一组织方式保留服务的业务接口，将信任职责集中在部署和通信组件中，使准入开销与稳态访问成本可以分别分析。

### 4. 关键机制与应用价值

支撑这一架构的技术内容包括实例关联与生命周期处理。实例关联机制将取证目标、身份请求和服务入口对应到同一业务实例，并在核验过程的关键阶段复查目标状态，处理 PID 复用、监听者变化和实例错配等情形。生命周期机制区分正常凭据轮换与准入依据失效：前者通过完整凭据发布和连接切换支持持续访问，后者针对可观测的失效，通过实例监测、凭据清理及入口与连接管理停止使用旧依据。目标实例完成必要的登记与重新准入后恢复访问。两类机制共同支撑证明和身份职责外置后的可信服务访问，其效果由实际交付与停止恢复实验检验。

本文同时考察这种架构的应用价值。将证明、身份和通信职责实现为公共能力，有望减少不同应用重复实现信任逻辑的工作；分离准入和稳态访问，使一次准入的成本能够由后续交互分摊；明确失效与恢复职责，则有助于组织服务更新和故障处理。上述价值需要结合实际代价判断：低侵入会将部分复杂度从业务实现转移到公共基础设施，因此评价同时统计业务代码、适配器和核心组件的改动，以及新增的配置、部署和运维步骤。论文将据此分析哪些能力能够复用、哪些仍需业务适配，以及框架在何种条件下具有工程价值。

### 5. 实验问题与评价安排

计划中的评价在真实 Intel TDX 环境中围绕五个问题展开：准入是否约束实际接收者；运行条件变化后，停止与恢复是否符合声明规则；各项机制是否必要；框架如何影响 Agent 任务与服务性能；更换调用方或服务后，哪些能力能够复用。正常访问、身份与实例错配、运行变化等受控情形用于检查实际交付和生命周期行为，强基线与单项消融用于解释机制作用。其中，B3b 在已证明节点上保留同等的实例取证、策略检查、凭据发布和失效处理，但不额外生成 Workload Quote，用于单独检验这份证明的作用与成本；与最近邻方案的机制比较则说明整体架构的差异。

任务与性能评价采用 OpenViking 的 τ²-bench 适配、LoCoMo/OpenClaw 和 Server 混合负载，在固定模型、任务、数据、策略与资源条件下，分别检查任务结果、敏感内容的实际接收、首次可信就绪、稳态延迟、成功吞吐及停止恢复成本。合法可完成、需要重新准入后恢复和应当拒绝的任务分别统计。复用评价将记录不同接入的业务适配、通信适配和核心修改；不同调用端的结果用于说明调用侧复用范围，跨服务复用则由另一类服务的实际接入支撑。这些实验将为交付约束、任务可用性和总体工程成本提供相互对应的证据。[τ²-bench 适配](https://github.com/volcengine/OpenViking/blob/192b813e7e3106680a5534e2d4c9bcf6d2390abd/benchmark/tau2/llm/README.md)、[LoCoMo/OpenClaw](https://github.com/volcengine/OpenViking/blob/192b813e7e3106680a5534e2d4c9bcf6d2390abd/benchmark/locomo/README.md)

## Introduction: English Draft

### 1. Task, Applications, and Evaluation Objectives

Agents complete tasks through model calls, memory access, and tool execution, moving sensitive context among multiple components. With memory provided as a service, for example, an enterprise assistant stores private conversations, project constraints, and task results in a separate service for subsequent retrieval and reuse. This service directly handles private data. Even if the agent runs in a trusted execution environment, protection after delivery still depends on the recipient's software and operating conditions. Following Agent-CC's trusted service composition model, this work examines how trust requirements for local confidential execution can extend to data processing across services. [Agent-CC](../../../../output/pdf/Agent-CC.zh-CN.md#5-可信服务组合)

This requires considering delivery constraints, task availability, and engineering cost together. Approved identities and execution conditions should determine whether an instance may receive sensitive data and constrain further delivery when those conditions cease to hold. Eligible tasks should complete normally, with service restored following appropriate fault handling. Evaluation must therefore examine actual recipients, stopping and recovery behavior, task quality and completion, and the additional costs of initial trusted readiness, steady-state access, and application integration. Together, these objectives determine whether trusted service composition is practical for existing agent systems.

### 2. Prior Approaches and Technical Challenges

Prior work establishes foundations in runtime evidence, service identity, and agent platforms. SPIFFE/SPIRE incorporates node and workload conditions into identity issuance. Full Trust Alchemist investigates attestation of dynamic workload properties, while dstack-capsule links platform evidence and Pod identity through layered attestation. aDNS associates attestation results with service identities and TLS keys, and Grimlock proposes binding attestation to specific communication channels. Omega takes a platform approach to trusted agent execution and controlled external interactions. These systems provide different paths to trust across services. [SPIRE](https://spiffe.io/docs/latest/spire-about/spire-concepts/), [Full Trust Alchemist](https://www.comsys.rwth-aachen.de/publication/2025/2025_galanou_trust-alchemist/), [dstack-capsule](https://arxiv.org/html/2606.03323v2), [aDNS](https://www.usenix.org/conference/usenixsecurity25/presentation/delignat-lavaud), [Grimlock](https://arxiv.org/html/2605.27488v2), [Omega](https://arxiv.org/abs/2512.05951v2)

Applying these capabilities to existing applications requires reconciling service invocation with trusted execution conditions. Applications access services through stable interfaces and logical identities, whereas admission evidence concerns particular platforms, software, configurations, and running instances. Shared components can handle evidence collection, identity management, and TLS to reduce application changes. In a proxy-based integration, the verified business process, credential holder, and TLS termination endpoint may belong to different components. A framework must explain how they remain associated and how the admission conditions relied upon through a service identity govern the actual data path.

Runtime changes complicate this association. Instances, credentials, and connections have different lifecycles: routine certificate rotation need not alter an instance's eligibility, whereas instance replacement may invalidate the basis for an identity or connection still in use. Multi-step agent tasks need to reuse valid identities and connections to control cost, while stopping further delivery when relevant conditions fail. We therefore ask: **How can admission decisions derived from runtime evidence govern delivery to the actual receiving instance, with limited application changes, and remain effective as runtime conditions change?** Comparisons will examine instance association, change handling, and integration cost under equivalent delivery requirements to identify the effects and trade-offs of individual design choices.

### 3. Core Architecture of Argus

We present Argus, a framework for trusted service composition in agent systems based on Intel TDX remote attestation and workload identity. Argus places attestation and admission responsibilities in the deployment and identity layers; applications use admission results through authentication and communication adapters. During service admission, controlled launch records and instance evidence collected by trusted components bring platform evidence, the actual business instance, its software, and its configuration under policy evaluation. Successful evaluation contributes to SPIRE identity admission. Callers verify the expected service identity and rely on admission rules enforced by an accepted issuing domain. Credential delivery and a protected communication endpoint associate the admitted identity with the business process.

Argus separates attestation-based admission from steady-state interaction. Normal requests reuse valid identities and connections without generating a Quote or invoking an attestation verifier for each request. The current server prototype uses a Helper to deliver credentials and NGINX to terminate TLS, reaching the business process over a protected local path. Instance checks, credential state, and connection management jointly constrain identity and connection reuse. This organization preserves the service's business interface while placing trust responsibilities in deployment and communication components, allowing admission overhead and steady-state access cost to be analyzed separately.

### 4. Key Mechanisms and Application Value

Instance association and lifecycle handling support this architecture. The association mechanism connects the evidence target, identity request, and service endpoint to the same business instance. It rechecks target state at key verification stages to address PID reuse, listener changes, and instance mismatches. Lifecycle handling distinguishes routine credential rotation from invalidation of the admission basis. Rotation uses complete credential publication and connection transitions to support continued access. For observable invalidation, instance monitoring, credential cleanup, and endpoint and connection management stop reliance on the previous admission decision. Access resumes after the target completes the necessary registration and readmission. Delivery and stopping-and-recovery experiments will evaluate how these mechanisms support trusted service access when attestation and identity responsibilities are placed outside the business process.

We also examine the architecture's application value. Shared attestation, identity, and communication capabilities may reduce repeated implementation of trust logic across applications. Separating admission from steady-state access allows admission cost to be amortized over subsequent interactions, while explicit invalidation and recovery responsibilities can support service updates and fault handling. These benefits must be assessed alongside their costs. Limited application changes can shift complexity into shared infrastructure. Evaluation will therefore account for changes to business code, adapters, and core components, together with additional configuration, deployment, and operational steps, to determine what is reusable, what requires application-specific adaptation, and when the framework offers engineering value.

### 5. Research Questions and Evaluation Plan

The planned evaluation uses real Intel TDX environments to address five questions: whether admission constrains the actual recipient; whether stopping and recovery follow the declared rules after runtime changes; whether individual mechanisms are necessary; how the framework affects agent tasks and service performance; and what remains reusable when the caller or service changes. Controlled normal access, identity and instance mismatches, and runtime changes will test delivery and lifecycle behavior. Strong baselines and individual ablations will examine mechanism effects. In particular, B3b retains equivalent instance evidence collection, policy checks, credential publication, and invalidation handling on an attested node, but omits an additional Workload Quote, isolating that evidence's role and cost. Comparisons with the closest related systems will characterize broader architectural differences.

Task and performance evaluation will use OpenViking's τ²-bench adaptation, LoCoMo/OpenClaw, and mixed server workloads. With models, tasks, data, policies, and resources held fixed, we will examine task outcomes, actual receipt of sensitive content, initial trusted readiness, steady-state latency, successful-request throughput, and stopping and recovery costs. Authorized tasks eligible for completion, tasks requiring readmission before recovery, and tasks that should be rejected will be reported separately. Reuse evaluation will record business, communication-adapter, and core changes across integrations. Results from different callers will establish the scope of caller-side reuse; cross-service reuse will require an actual integration with another service type. Together, these experiments will provide corresponding evidence for delivery constraints, task availability, and overall engineering cost. [τ²-bench adaptation](https://github.com/volcengine/OpenViking/blob/192b813e7e3106680a5534e2d4c9bcf6d2390abd/benchmark/tau2/llm/README.md), [LoCoMo/OpenClaw](https://github.com/volcengine/OpenViking/blob/192b813e7e3106680a5534e2d4c9bcf6d2390abd/benchmark/locomo/README.md)

## 作者备注与依据

本稿从 Agent-CC 的整体系统模型引出 Argus 的可信服务组合定位，以服务化记忆贯穿动机与当前评测。数据全生命周期保护、构建到运行时完整性属于 Agent-CC 的整体架构范围；Argus 聚焦跨服务准入与交互，不能将整个架构的全部保护能力归为当前 Argus 实现。

Introduction 保留五部分小标题，便于讨论各段职责。第二部分提出既有应用接入可信服务时需要协调的技术问题，不将未复现的行为写成近邻缺陷；第三部分组织当前架构的主要选择；第四部分连接关键机制与待评价的应用价值；第五部分以五个核心问题覆盖机制、任务和工程成本。正式结果形成后，再凝练贡献和结果表述。

跨企业研发与银行核验继续保留在原场景文档，作为这一框架的应用例子。实际执行条件、身份和接收入口的关联，以及生命周期处理，是将可信服务组合落实为运行机制的问题。低侵入属于设计目标，不能提前写成零改动、已量化的成本下降或对任意 Agent 框架的支持。

本稿按准入依据、交付落实与运行维护组织研究问题。原论文框架中的 C1–C3 保留作候选贡献记录，与这里的研究问题划分不作逐项对应。最终贡献段应在强基线与实测结果形成后，写清新增机制或可推广发现；当前评价计划不能在投稿时替代必要结果。第二份 Workload Quote、性能影响和接入复用均不预设正向结论。证明反馈参与 Agent 决策、敏感交付时按需核验继续作为候选。

当前计划直接评价 Agent→OpenViking 的受测交互。其他工具、外部模型、持久化数据的额外保护机制、跨域互认和第二运行时按实际完成范围另行表述。整体处理链是架构目标，对具体路径的实验不能推导为整条工作流均已获得保护。

本轮为写作修订，没有新增实验。保存的 9 月 9 日报告中，服务端真实 TDX PoC 通过、严格 UpToDate 策略阻塞；客户端长期记忆联合验收失败，客户端 TDX 证明未执行。9 月 13 日迁移后的真实硬件与部署复验仍未执行。正文保留原型、设计和计划的区别，未写入未经测量的收益。

项目依据：

- [Agent-CC v0.8 原文](../archive/pre-asymmetric-architecture/Agent-CC.pdf)及[中文转写](../../../../output/pdf/Agent-CC.zh-CN.md)：§1 的系统模型、§3 的服务化记忆、§4 的可信绑定、§5 的可信服务组合与低侵入接入。上一轮已核对原 PDF 第 1、11、15–19 页及相关架构图，本轮重新对照相关章节。
- [当前 Agent-CC 介绍](../../README_CN.md)：整体三支柱与 Argus 组件定位。
- [原论文框架](./Argus-AAMAS2027-EMAS-论文框架.md)：研究问题、C1–C3、对象与信任假设。
- [整体思路与候选想法](./Argus-EMAS-论文整体思路与候选想法-2026-09-12.md)：研究对象与候选方向。
- [跨企业场景与原 Intro](./Argus-EMAS-Intro-跨企业可信服务协作-2026-09-12.md)：研发与银行场景及配图。
- [Related Work 汇总](./Argus-Related-Work-汇总表-2026-09-15.md)：近邻及比较边界；保留前稿已核对的一手来源。
- [当前任务与性能评价设计](./Argus-EMAS-Agent场景实验设计与论文借鉴-2026-09-14.md)：τ²-bench、LoCoMo/OpenClaw、Server 混合负载与 B3b。
- [通信设计](../Argus-Agent-Agent-Service-Trusted-Communication-CN.md)：实例、身份、入口与生命周期的实现及目标边界。
- [当前代码架构](../Argus-SPIFFE-Current-Code-Architecture-CN.md)：证明准入与业务流分工、实例取证、身份交付、实际入口、正常轮换与失效处理；相关源码已在本次讨论中抽查。
- [服务端报告](../../../../documents_ly/argus-openviking-workload-attestation-status-20260909.md)、[客户端报告](../../../../documents_ly/argus-openclaw-ip1-tdvm-client-acceptance-20260909.md)、[后续验证记录](../../core/spire/workload/VALIDATION.md)：实验证据范围。
