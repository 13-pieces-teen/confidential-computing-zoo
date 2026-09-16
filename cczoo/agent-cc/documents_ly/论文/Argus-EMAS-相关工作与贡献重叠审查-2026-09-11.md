# Argus EMAS 相关工作与贡献重叠审查

日期：2026-09-11。性质：第一轮定向文献审查与研究建议，不是系统综述、实验结果或新颖性证明。

文档定位（2026-09-16 整理）：维护候选贡献的重叠风险、比较要求和待回答问题；文献来源与公开状态的后续核对见[09-15 汇总表](./Argus-Related-Work-汇总表-2026-09-15.md)。本文保留原审查时点和证据范围，不能把未复现的行为写成近邻缺陷。职责见[论文导航](./README.md)。

## 1. 结论

当前三个候选贡献中，C1“TDX 分层远程证明与身份准入”和 C2“证明结果落实到 Agent 通信”与已有工作存在较强重叠；C3“真实 TDX 上的端到端实验”属于必要证据，单靠采用真实硬件和增加实验不能独立建立新颖性。

EMAS 的研究定位可以保留。需要进一步建立的是：Argus 在什么明确的信任假设、实例变化和数据路径条件下，提供了已有方案尚未满足的性质，或以同等保证取得可测量的工程收益。当前文献证据不足以宣布这一差异已经成立。

## 2. 核心近邻

“覆盖”表示论文已有相应设计或机制，不表示该论文的全部设计均已在原型中完成。下面的不同点是待核实、待实验的比较轴，不是 Argus 已获证明的优势。

| 工作与公开状态 | 本轮核实到的内容 | 与 Argus 的重叠及比较重点 |
|---|---|---|
| [Multi-Platform and Vault-Free Attestation of Confidential VMs](https://doi.org/10.1145/3697090.3698036)，LADC 2024 | 出版方全文索引 §4–6：TDX SPIRE NodeAttestor、多 TEE/多云身份准入、密钥交付及性能评价 | 直接覆盖 TDX + SPIRE + 证明后身份/秘密交付。Argus 需解释业务实例及实际入口关联增加了什么；不能把原生 SPIRE 两阶段准入归为自有创新。 |
| [Full Trust Alchemist](https://www.comsys.rwth-aachen.de/publication/2025/2025_galanou_trust-alchemist/2025_galanou_trust-alchemist.pdf)，Middleware 2025 | 作者 PDF §3.3.2、§3.4、§6.2.3：验证方特定的运行证据、策略、工作负载密钥；将运行证据摘要绑定 REPORT_DATA | 直接覆盖动态工作负载证明及证据与密钥关联。不能将前人概括为只证明 VM 启动状态。应比较取证可信根、实例粒度及通信生命周期。 |
| [dstack-capsule: Implement Kubernetes Pod-Level Remote Attestation for Confidential Workloads on dstack](https://arxiv.org/html/2606.03323v2)，2026-06-03 arXiv v2，本轮未核实正式 venue | 全文 §3.3–3.4、§4.2：TDX 平台与 Pod 身份两层证明，Pod UID/spec digest、UDS 调用者与进程/cgroup 关联，RA-TLS/KMS 及生命周期处理 | 对 C1 的直接近邻。PID/实例识别、将应用元数据放进 Quote 不是空白；需要比较其 Pod 边界与 Argus 实际业务进程、持钥者、入口之间的关系。 |
| [Trusted AI Agents in the Cloud / Omega](https://arxiv.org/html/2512.05951v2)，2025-12-13 arXiv v2，本轮未核实正式 venue | §6.5、§7：平台、Agent、模型、输入、策略的分层/差分证明及 MCP/A2A 管控。§9：SEV-SNP/H100 原型与任务评价 | 与整体可信 Agent 框架主张高度重叠。§9 明确原型评估尚不包含 VMPL isolation 和 trusted boot；不可混淆完整架构与已测范围。 |
| [Grimlock: Guarding High-Agency Systems with eBPF and Attested Channels](https://arxiv.org/html/2605.27488v2)，AgenticOS 2026 vision paper | §2：eBPF 强制中介、TLS exporter/nonce 绑定证明、短期 scope token、验证后释放明文 | 对 C2 的强近邻，尤其是实际通道绑定。论文不是 ASPLOS 正会论文；[官方仓库](https://github.com/Roblox/grimlock)有透明 mTLS PoC，本轮未核实完整证明与 scope 链已落地。 |
| [Transparent Attested DNS for Confidential Computing Services / aDNS](https://www.usenix.org/conference/usenixsecurity25/presentation/delignat-lavaud)，USENIX Security 2025 | [全文](https://arxiv.org/html/2503.14611v1) §5：平台/代码/配置/实例与新公钥关联、TLS 密钥校验、更新和缓存；§8.3：机密容器、sidecar、Nginx 和 AI 推理 | 直接覆盖证明→服务身份→实际 TLS 接收入口。不能仅以 DNS 换 SPIFFE 区分创新；应比较客户端相信谁、证据如何更新和已有连接如何处理。 |

补充近邻：

- [HIEST: Hierarchical Attestation to Support Trusted Dynamic Workloads](https://link.springer.com/chapter/10.1007/978-3-032-35576-8_13)，CyResTrust / ARES Workshops 2026。**本轮仅核实出版方摘要**：TCB→解释器→应用的分层证明及复用攻击。足以排除“层次证明无人研究”的表述；不足以判定其未实现某种绑定或撤销机制。
- [AgenTEE](https://arxiv.org/html/2604.18231v2)，EuroMLSys 2026 Workshop，§3–5：Agent/模型/应用隔离、证明后交付私有资产及受保护通信。原型为 Rock 5B 上 OpenCCA，评价范围需按原文限定。说明采购 Intro 中“证明后才交付机密数据”的场景本身已有近邻。
- [D-MUTRA](https://arxiv.org/html/2608.01938v1)，2026-08-03 arXiv v1，本轮未核实正式 venue，§IV–VIII：纯软件运行时完整性验证、链上信任协调、ROS2/Gazebo 协作隔离。不能将其当成硬件 TEE 方案；也不能声称持续维护 MAS 信任是空白。
- [Integrating Remote Attestation with Transport Layer Security](https://arxiv.org/abs/1801.05863)，2018 年 RA-TLS 白皮书/预印本：[Intel 原文](https://cdrdv2-public.intel.com/671415/integrating-remote-attestation-with-transport-layer-security.pdf)描述 SGX 证明与 TLS 端点关联，支持多 TLS 库。此处仅用于确定基础概念已有先例，不作完整安全协议比较。

## 3. 三项贡献的处理建议

| 原表述 | 当前判断 | 应改写为需要被证明的结果 |
|---|---|---|
| C1：基于 TDX 的分层远程证明与准入 | 重叠高；技术组合与两层结构不足以承担独立新颖性 | 精确定义被证明实例、证据生产者、身份授权和数据接收者之间的关联性质，并给出已有方法不能直接满足的反例或工程约束。 |
| C2：将证明结果落实到 Agent 通信的运行时 | 重叠高；但仍可能承载核心系统贡献 | 在保持标准身份互操作的条件下，建立可复用的实例、凭据和入口生命周期机制；明确新增保证或同等保证下的成本差异。 |
| C3：真实 TDX 上的端到端评价 | 必要证据，不天然构成新颖性 | 从强基线、消融和真实 Agent 任务中得出哪些机制必要、何时失效及保证—成本—可用性的可推广结论。 |

以上均为候选改写。不能通过换用“契约”“生命周期”“可组合”等名称替代机制差异。

## 4. 必须进一步说明的问题

### 4.1 谁被证明，谁被信任

区分 TDVM 平台、TDVM 内核、Provider/启动登记链、业务实例、SPIRE、Helper 与 NGINX。运行属性由受信 Guest 组件观察；Quote 的签名不能自行证明这些观察真实，也不证明模型输出或后续任意行为正确。

同样区分第三方宿主威胁、TD 内受信组件被攻陷、合法升级故障和恶意业务应用。不能把威胁模型外的攻击算作对照方案失败。

### 4.2 第二份 Workload Quote 增加了什么

必须明确第二份 Quote 的消费者、证据新鲜度与可验证绑定语义。现有[评估方案 B3b](./框架评估研究-优势对比与实验方案-2026-09-07.md)已提出：已证明节点 + 同等独立实例检查 + 等价策略/凭据发布/失效联动，但不再生成第二份 Workload Quote。

如果该基线在相同假设下达到同样保证，就不能宣称第二份 Quote 带来独有安全性。仍可研究证据可复用性、独立核验需求或成本，但需要分别定义和测量。

### 4.3 证明准入与密码学通道绑定有什么区别

当前 Argus 是 Trustee 评估、插件产生 selectors、SPIRE Entry 授权、SPIRE CA 签发 SVID、应用或 NGINX 使用凭据。业务客户端依赖签发域执行的准入规则。当前不是每条 TLS 通道都直接携带或绑定新 Quote。

应逐项说明：Quote 绑定什么、EAR 绑定什么、SVID 代表什么、TLS 私钥由谁持有、代理如何对应业务实例。不能将 proof key 当作 SVID 私钥，也不能宣称逐请求证明某 PID 已处理上下文。

依据：[当前主设计 §3.4–4.2](../Argus-Agent-Agent-Service-Trusted-Communication-CN.md)。

### 4.4 变化后到底停止什么，多久停止

核心待检验轨迹：业务实例退出或被替换，而旧身份、代理或连接仍存活。它是研究探针，不是已确认的现有系统漏洞。

区分新连接、旧连接上的新请求和在途流式数据；记录变化、检测、最后实际交付、确认停止和恢复。实验观测最大值不是无条件时间上界。普通 SVID 轮换不等于新 Quote，当前也不能宣称跨域策略立即失效。

应对 aDNS、Grimlock 或其他可复现系统的实际更新/凭据规则进行相应对照，而非默认它们只能等证书过期。

### 4.5 Agent 相关性和通用性

采购/审查 Intro 用于说明动机，不是创新。研究需落到多轮敏感上下文、Agent 委托、异构 Agent/服务接入与任务中断结果，并验证迁移到另一个 Agent 或服务时规则能否复用。

当前主设计仍把普通 Attest、原生 SDK 同等证明、多域政策互认与多副本列为未完成内容。不能把规划作为相对近邻的已实现优势。

## 5. 建议保留的论文定位

可以继续研究“基于 TDX 远程证明的、感知业务实例的 Agent 可信交互运行时”，但将 TDX、SPIFFE 和层次证明放在明确的基础与设计选择中，把贡献集中在可检验的实例—身份—入口关系、变化处理与系统性发现。

优先复核：aDNS、Grimlock、dstack-capsule；随后用 Omega、Full Trust Alchemist、TDX/SPIRE 工作检查整个框架故事是否重复。最先要回答的是一个强反例和一条相同假设下的强基线，尚不需要增加新的规划器、治理体系或多套独立功能。

## 6. 本轮范围

仅进行了文献和本地设计/源码入口的只读核对，没有部署、实验复现或攻击运行。论文原型的限制按原文分别记录；本轮未找到某项说明，不代表该工作不存在相应能力。HIEST 的全文机制仍待合法可得版本进一步核实。
