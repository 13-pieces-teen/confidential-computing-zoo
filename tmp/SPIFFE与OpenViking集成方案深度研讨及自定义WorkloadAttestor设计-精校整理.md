# SPIFFE 与 OpenViking 集成方案深度研讨及自定义 Workload Attestor 设计

## 精校说明

- 原始附件仅包含文字转写，没有原始音频；本稿依据上下文与当前仓库中的实际组件名进行校正，不能替代声学回听。
- 原文件标题和正文中的 `OpenWhisk`、`OpenYTP` 均按上下文统一为 `OpenViking`；`OpenCloud`、`OpenCore` 统一为 `OpenClaw`。
- 高频错识别已统一：`workloadpattern/workloadtester` → `Workload Attestor`，`SUID` → `SVID`，`Spe/Sprout/SPAR` → `SPIRE`，`GWT` → `JWT`，`五零九` → `X.509`，`TCAAPI` → `TC-API`，`code` → `Quote`，`trustT` → `Trustee`，`RDM` → `RTMR`，`勾写的` → `Go 写的`。
- `Speaker 1` 与 `Speaker 3` 的真实姓名无法仅凭附件确认，因此保留原标签。Speaker 3 很可能是会中临时加入的“斯远”，但本稿不把该推断当作事实。
- 为提高可读性，删除了口吃式重复和大部分无语义填充词，但保留全部时间戳、发言条目、确认性回应、争论、结论与未完成语句。

## 会议结论

1. OpenViking 的上游业务代码不应因 SPIFFE/SPIRE 集成而修改；容器镜像、入口脚本、端口、证书和代理等部署配置可以调整，这类调整不等同于修改业务源码。
2. 普通 Workload API 路径需要由调用进程通过 Workload API 获取身份材料；若不希望 OpenViking Python 进程感知 SPIRE，可以由代理或 sidecar 终止 mTLS。
3. 会中提出同时比较两类代理方案：通用的 NGINX/代理 + Workload API，以及 SPIFFE Broker API + sidecar。会议当时没有完成技术选型，只要求先跑通并验证。
4. Broker API 的核心价值是由受信基础设施组件根据目标 PID 代表 workload 请求 SVID，从而避免 OpenViking Python 直接挂载 Workload API 或持有 SVID。
5. 自定义 Workload Attestor 应作为 SPIRE Agent 侧插件工作：以目标进程 PID 为输入，调用 Evidence Provider 收集 Quote、TC-API/Rekor 启动记录和度量证据，再交给 Trustee 验证；验证通过后返回 selector，由静态 Registration Entry 匹配并签发 SVID。
6. Node Attestation 与 Workload Attestation 是两个阶段：前者证明 TDVM/SPIRE Agent 所在节点，后者在具体 workload 启动后证明目标进程。两次远程证明可能采集部分重复的环境信息，但验证对象和时点不同。
7. 待办包括：验证 Workload API 的标准部署方式；比较 NGINX/通用代理与 Broker API；确认自定义 Workload Attestor 能否调用 Trustee；在真实环境中跑通完整链路。

## 按时间线精校的完整发言

```text
[00:05:05] Speaker 1: 怎么样？先说说这个 SPIFFE 的事情。
[00:05:08] Ryan: 对，我也正想讲一下这个。
[00:05:10] Speaker 1: 好。
[00:05:10] Ryan: 上周不是说要做自定义 Workload Attestor 吗？我想了一下，主要有两个方案。
[00:05:21] Speaker 1: 嗯。
[00:05:21] Ryan: 我之前的方案，是想通过插件形式把 SPIFFE 接入这个容器。
[00:05:45] Speaker 1: 嗯。
[00:05:45] Ryan: 但这种方式对业务有一定侵入性：需要用 Python 监听端口；后续 OpenViking 更新时，也可能出现适配问题。然后我看到——
[00:06:03] Speaker 1: 没理解。回去重新讲一下。
[00:06:04] Ryan: 好。之前我跑通 SPIFFE 时——
[00:06:10] Speaker 1: 对。
[00:06:10] Ryan: 我原本想让 OpenViking 自身的 Python 进程取得它的 SVID。要直接这样做，就需要对 OpenViking 做一些侵入式设计，例如把 SPIRE Workload API 的 socket 挂进容器，这会改变 OpenViking 容器的加载方式。后来我看到 SPIFFE/SPIRE 新版本提供了 Broker API。它想解决的问题是：如果让 OpenViking Python 进程直接取得自己的 SVID，首先 workload——
[00:07:09] Speaker 1: 你为什么要让 OpenViking 的进程自己取 SVID？
[00:07:13] Ryan: 因为 Workload Attestation 的入参是 PID，也就是进程 ID；它需要通过这个进程 ID 获取——
[00:07:24] Speaker 1: 那和 OpenViking workload 本身没有关系。你启动 OpenViking workload 的流程里就可以拿到它的 PID。
[00:07:36] Ryan: 不，不是——
[00:07:37] Speaker 1: 为什么要侵入业务呢？
[00:07:39] Ryan: 因为常规 SPIFFE 接入方式需要把 socket 挂进去。否则 OpenViking 怎么感知 SPIFFE、为什么要取 SVID？总得有一种接入方式，它才能进入 SPIFFE 这套框架。
[00:08:01] Speaker 1: OpenViking 取得 SVID 后怎么用？我反问你：它拿这个做什么？
[00:08:07] Ryan: 因为 OpenClaw 要和 OpenViking 通信。通信前需要验证双方的 SVID，通过验证后才能建立连接。如果说——
[00:08:29] Speaker 1: 我觉得你需要回到 SPIFFE/SPIRE 的标准部署方式，重新理解它。
[00:08:35] Ryan: 嗯。
[00:08:35] Speaker 1: SPIFFE 的部署应该和 workload 解耦，这一点一定要理清。我们做 SPIFFE 本来就是要和 workload 的构建流程解耦，怎么能又把它拉回去？
[00:08:52] Ryan: 但是……嗯。
[00:08:54] Speaker 1: SPIRE 产生的是 SVID 和 trust bundle。在两个服务通信时，原来用 TLS，就对应 X.509 证书链；另一种则是 JWT。
[00:09:12] Ryan: 嗯。
[00:09:12] Speaker 1: 服务前面通常会有一套流量入口。最简单的例子是前面放一个 NGINX。OpenViking 本身不一定有责任在内部直接提供 TLS/HTTPS。
[00:09:35] Speaker 1: 对吧？
[00:09:36] Ryan: 嗯。
[00:09:36] Speaker 1: 这个思路一定要重新检查。难道每个 workload 都要自行集成 SVID 吗？不可能，这不合理。
[00:09:50] Speaker 1: 对吧。
[00:09:51] Ryan: 可能因为当前框架是这样，我就顺着这个方向想了，没有——
[00:09:55] Speaker 1: 不是顺着它想，而是一定要先搞清原理。SVID、trust bundle 与 workload 的业务代码是解耦的。比如 OpenViking 原来提供 REST API——
[00:10:11] Speaker 1: OpenViking 提供 REST API，其中可能有 TLS，也可能没有；可能是 HTTPS，也可能不是。但无论如何，SPIFFE 身份可以是 X.509-SVID，也可以是 JWT-SVID。
[00:10:34] Ryan: 嗯。
[00:10:34] Speaker 1: 身份和证书链应该通过 proxy 配在入口，不能去改业务服务；连接时也应该连接这个入口。
[00:10:47] Ryan: 嗯。
[00:10:48] Speaker 1: 对吧？证书链从这个入口进入。如果 OpenViking 有接口可直接配置证书链，那也可以；如果没有，就通过代理方式接入。所以你们要吃透 SPIFFE/SPIRE 的标准部署。它给每个服务一个标准的 service ID，也就是 SPIFFE ID。
[00:11:17] Ryan: 嗯。
[00:11:17] Speaker 1: 这个 service ID 关联其身份材料和 trust bundle，例如 X.509 证书或 JWT token。
[00:11:26] Ryan: 嗯。
[00:11:26] Speaker 1: 原有协议和证书怎么用，接入 SPIFFE 后仍按对应方式使用。比如 X.509-SVID 用于 TLS/mTLS，具体如何配置取决于部署方式。
[00:11:40] Ryan: 嗯。
[00:11:40] Speaker 1: 不应该侵入业务代码；部署层面的调整不属于这种侵入。
[00:11:51] Ryan: 嗯。
[00:11:51] Speaker 1: 所以首先要弄清 SPIFFE/SPIRE 与 OpenViking 的标准集成方式。我不认为这个部署需要修改 OpenViking 业务代码；如果需要改业务代码，方向大概率不对。
[00:12:05] Ryan: 现在是通过 TC-API 启动 OpenViking。
[00:12:10] Speaker 1: 对。TC-API 启动 OpenViking，但不改变 OpenViking 服务本身的端口。
[00:12:15] Ryan: 嗯。
[00:12:15] Speaker 1: 现在要做的是把 SPIFFE 身份和 trust bundle 用在它的服务入口上。
[00:12:20] Ryan: 嗯。
[00:12:21] Speaker 1: 具体怎么做，要看 SPIRE 的标准部署方式。这就是我最早让你做 SPIRE 部署 demo 的原因。
[00:12:30] Ryan: 嗯。
[00:12:31] Speaker 1: 你应该有一条非常清晰的路径：workload 和 SPIRE 是两套东西。workload 可以使用 SPIRE 提供的证书，但不应该因此修改 workload 的业务代码，对不对？
[00:12:48] Ryan: 对。之前我是把 Workload API 挂进 OpenViking；后来改用 SPIFFE Broker API。因为 OpenViking 如果要取得自己的 SVID，需要经过 Workload Attestation；这又会调用 Workload API，才能让 SPIRE Agent——
[00:13:14] Speaker 1: 你联系一下斯远，我们一起讨论。
[00:13:16] Ryan: 好。
[00:13:16] Speaker 1: 看斯远在不在。我觉得这里可能走岔了。我们要解决的问题就是不侵入业务，不能为了接入 SPIFFE 反而侵入 workload。Workload Attestor 获取的只是进程信息和进程号，这些都不需要由修改后的 workload 自己提供，对吧？
[00:14:05] Ryan: 嗯，可能是我的表述有问题。
[00:14:09] Speaker 1: 你说的“侵入 workload”具体指什么？如果是修改 OpenViking 代码，那肯定不能做。
[00:14:24] Ryan: 我的意思可能是，需要修改容器构建和启动的一些参数、流程，然后——
[00:14:35] Speaker 1: 这个没问题，完全可以改。这不叫侵入业务。
[00:14:39] Ryan: 哦。
[00:14:40] Speaker 1: 用词要准确。OpenViking 要接入这套体系，可以在服务端口前配置证书入口，把证书交给代理或服务配置。
[00:14:56] Ryan: 嗯。
[00:14:56] Speaker 1: 这不需要侵入 OpenViking workload 本身。OpenClaw 客户端接入时也一样。我们讨论的是：OpenViking 使用 SPIFFE/SPIRE 产生 SVID 和 trust bundle 后，原有 OpenViking 服务与 OpenClaw 客户端都尽量保持不变。
[00:15:31] Ryan: 嗯。
[00:15:31] Speaker 1: 它们原来就有一套通信流程，对吧？
[00:15:35] Ryan: 嗯。
[00:15:35] Speaker 1: 现在假设 SPIRE 给它们颁发了 SVID，那么如何把 SVID 放进原有的验证流程？
[00:15:50] Ryan: 放到哪里？
[00:15:53] Speaker 1: 放进它们的通信和验证流程中。
[00:15:58] Ryan: mTLS 通信时不就——
[00:16:00] Speaker 1: 对，mTLS 会使用它。但 OpenViking 启动时，证书是缓存到某个地方，还是在前面加一个 NGINX 代理并给代理配置证书？要把这条部署路径弄清楚。
[00:16:19] Ryan: 对，这个我们上次没有讨论。
[00:16:22] Speaker 1: 对。
[00:16:23] Ryan: 我上次留给刘洋的问题就是：OpenViking 需要感知 SPIFFE 的流程吗？
[00:16:29] Speaker 1: 应该不需要。
[00:16:29] Ryan: 可以不需要，就是用——
[00:16:32] Speaker 1: 不是“可以不需要”，而是“应该不需要”。
[00:16:34] Ryan: 哦。
[00:16:35] Speaker 1: OpenViking 本身不需要感知。不过如果要为 OpenViking 构建专门的容器镜像，那镜像和部署配置可能需要感知。
[00:16:43] Ryan: 明白。
[00:16:43] Speaker 1: 比如端口、trust bundle 如何传入，需要通过部署方式解决；但通信两侧的业务代码都不应该修改。
[00:16:55] Ryan: 嗯。
[00:16:55] Speaker 1: 我是这样理解的。[后半句原转写无法可靠恢复]
[00:16:59] Ryan: 嗯，明白。
[00:17:02] Speaker 1: 好。
[00:17:04] Ryan: 我现在想用 SPIFFE 提供的 Broker API 处理中间流程。目前我把环境分成两个 TDVM，然后——
[00:17:20] Speaker 1: 先不讨论 Broker API。已有 Workload API，为什么不能用？
[00:17:24] Ryan: 如果使用 Workload API——
[00:17:26] Speaker 1: 对。
[00:17:26] Ryan: OpenViking 就需要感知 SVID 的存在吧？
[00:17:33] Speaker 1: 为什么？
[00:17:35] Ryan: Workload API 通过 UDS 和调用进程通信。这个 socket 必须挂到调用方，它才能主动获取 SVID；不是 SPIRE Agent 主动向它推送。调用方要先调用 Workload API，SPIRE Agent 才会通过后续流程把 SVID 给它。
[00:18:03] Speaker 1: 我知道，但这仍然和修改 OpenViking 业务代码无关。workload 启动时总会有 init 或 entry 脚本。可以由这个脚本和 Workload API 交互。
[00:18:24] Ryan: 通过脚本的形式？
[00:18:27] Speaker 1: 对。不能改业务源码，但一定有一种部署方式；在我们的场景里就是容器。
[00:18:36] Ryan: 嗯。
[00:18:36] Speaker 1: 进入容器后，首先执行一个脚本——如果采用这条流程。
[00:18:40] Ryan: 嗯。
[00:18:40] Speaker 1: 这个脚本取得 SVID 后，再拉起后面的业务服务。
[00:18:45] Ryan: 嗯。
[00:18:45] Speaker 1: 对吧？你改的不是 workload 本身，而是 workload 的启动和配置过程。
[00:18:52] Speaker 3: 这应该遵循 SPIRE 的标准流程。
[00:18:56] Speaker 1: 对，这是我们的前提：把它运行在容器镜像中，通过部署入口处理。
[00:19:00] Speaker 3: 对。
[00:19:01] Speaker 1: 进入容器镜像后，第一步做什么？如果没有 SPIRE，你可能会给它配置一张静态 TLS 证书。
[00:19:10] Speaker 3: 嗯。
[00:19:11] Speaker 1: 然后服务直接启动。但现在不能给它配置长期不变的静态证书，需要先取得 SVID 和相应的 trust bundle。
[00:19:22] Speaker 3: 嗯。
[00:19:22] Speaker 1: 这个过程需要一个脚本端。它就是容器镜像里的第一个脚本，可以叫 init，也可以叫 entry script。
[00:19:35] Speaker 3: 明白。
[00:19:35] Speaker 1: 一般都会有 entry script。
[00:19:38] Speaker 3: 嗯。
[00:19:38] Speaker 1: 这个 entry script 会通过 Workload API 与外部 SPIRE Agent 通信。
[00:19:48] Ryan: 嗯。
[00:19:48] Speaker 1: 通信时，它提供必要信息，SPIRE Agent 返回所需的身份材料。
[00:19:55] Speaker 3: 嗯。
[00:19:55] Speaker 1: 最终得到的就是证书链和身份材料。
[00:19:58] Speaker 3: 嗯。
[00:19:59] Speaker 1: 取得证书链后，再加载 OpenViking；或者把证书配置给代理。具体采用哪种方式另说。
[00:20:10] Speaker 3: 所以现在的问题之一，是 SVID 取得后存放在哪里，对吧？
[00:20:16] Speaker 1: 存在哪里不是当前最重要的问题。我想说的是，刚才那位——
[00:20:22] Ryan: Ryan。
[00:20:22] Speaker 1: 对。Ryan 的问题是，他认为不能用 Workload API，必须用 Broker API。我的反问是：为什么 Workload API 不行？
[00:20:36] Ryan: 我认为 Broker API 可以更好地处理刚才提到的 SVID 存储等问题。
[00:20:43] Speaker 1: 这些问题都能处理。但使用 Workload API 也不等于必须修改 OpenViking 本身，对吧？
[00:20:50] Speaker 3: 嗯。
[00:20:51] Speaker 1: 而且 Workload API 本身就是现有、成熟的接口。
[00:20:55] Ryan: 对，是现有接口。
[00:20:56] Speaker 1: 对吧。
[00:20:56] Ryan: 我看到 OpenViking 容器现在由 TC-API 启动。TC-API 能取得 container ID 和 PID。自定义 Workload Attestor 的输入需要这个 PID，并据此执行后续验证。TC-API 启动 OpenViking并拿到 PID 后，进入自定义 Workload Attestor 环节；Workload Attestor 再与 Evidence Provider 交互。Evidence Provider 一方面生成或获取 Quote，查询 TC-API 上传的 launch record 和相关信息；另一方面可以把 TEE 相关证据交给 Trustee 验证。
[00:21:58] Speaker 1: 对。
[00:21:58] Ryan: 如果这些信息都验证通过，自定义 Workload Attestor 这条链路就通了，随后进入 SVID 签发流程。
[00:22:09] Speaker 1: 对。但我的意思是，Workload API 可以做；Broker API 我没看过，不清楚它具体解决什么问题。无论如何，使用 Workload API 也不要求做侵入式业务改造。
[00:22:26] Speaker 1: 对吧？
[00:22:28] Ryan: 那么 OpenViking 和 OpenClaw 通信时，中间需要有一个 proxy 才能完成这套接入。
[00:22:40] Speaker 1: 如果 OpenViking 原生支持配置 TLS 或 HTTPS，这个代理可能不需要。
[00:22:46] Speaker 3: 对。
[00:22:46] Speaker 1: 如果不支持，就一定需要代理。
[00:22:48] Speaker 3: 对。
[00:22:48] Speaker 1: 因为我们需要在通信流程中使用证书。
[00:22:52] Ryan: 它确实不支持。
[00:22:53] Speaker 1: 那就必须在旁边配置一个 sidecar。可以是 NGINX，也可以是其他代理。
[00:23:00] Speaker 3: 对。
[00:23:02] Speaker 1: NGINX 是最常用的选择之一，可以配置 TLS/HTTPS。
[00:23:11] Ryan: 我的想法是，Broker 也可以配置双方之间的 TLS 通信；而且它正好是 SPIFFE 新提供的组件，可以实现这个目标。
[00:23:24] Speaker 1: 可以把两种方式分别试一下。但最通用的部署不应该依赖某一个专门的 Broker 实现。原服务不需要自带 TLS/HTTPS，只要在前面配置 NGINX 即可；这是很常见的部署方式，NGINX 的性能和稳定性也经过了长期验证。
[00:23:55] Speaker 3: 对。
[00:24:02] Speaker 1: 嗯。
[00:24:05] Ryan: 嗯。
[00:24:06] Speaker 1: 好吧。
[00:24:11] Ryan: 我想按这个方向做。
[00:24:13] Speaker 1: 嗯。[原转写“肯定时间小”，语义无法可靠恢复]
[00:24:17] Speaker 1: 我还有一个问题。这个问题上周没有解决。你先把前面的方案想明白。
[00:24:28] Speaker 1: 我的建议是采用 workload + NGINX 作为通用部署方式。如果你想试 Broker，也可以把它作为另一个选项。Workload 配合 NGINX 或其他通用代理服务器，是更通用的部署方式。
[00:25:02] Speaker 3: 嗯。
[00:25:02] Speaker 1: 想明白了吗？还有问题吗？
[00:25:05] Ryan: 我可能还需要实际试一下直接使用 Workload API。现在已有的一些方案都涉及调整 OpenViking，我需要确认能接受的改动边界。
[00:25:23] Speaker 1: OpenViking 业务代码肯定不能改。
[00:25:26] Speaker 3: 对。
[00:25:26] Speaker 1: 但是用于部署的容器一定需要调整。
[00:25:30] Speaker 3: 对，就是部署时使用的 entry/启动脚本。
[00:25:37] Speaker 1: 对。
[00:25:37] Ryan: 启动相关的配置信息都可以改？
[00:25:40] Speaker 1: 都可以改。
[00:25:42] Ryan: 明白。我原来以为这些也不能改。
[00:25:44] Speaker 1: 这些可以改。不调整部署配置，怎么支持新的部署方式？
[00:25:49] Speaker 3: 对，就是配置文件和入口脚本。
[00:25:50] Speaker 1: 回到我的另一个问题。上次我们讨论 Workload Attestor 应该在 SPIRE Agent 侧还是远端服务侧。调查后发现，Workload Attestor 的原生设计只能运行在 Agent 侧，不能运行在 Server 侧。
[00:26:08] Ryan: 哦。
[00:26:08] Speaker 1: 那这里要怎么做验证？
[00:26:12] Speaker 3: 远程证明后，SVID 应该缓存于 SPIRE Agent，对吧？
[00:26:19] Speaker 1: 对。
[00:26:19] Speaker 3: Workload Attestor 验证通过后，就可以取得 SVID。
[00:26:23] Speaker 1: 对。
[00:26:24] Speaker 3: 我们还有 Evidence Provider 组件。
[00:26:29] Speaker 1: 对。
[00:26:30] Speaker 3: 我在考虑，它能不能与 Trustee 通信并执行远程证明（RA）。
[00:26:37] Speaker 1: 可以。但这样一来，执行 RA 的整个流程就都落在这一层。
[00:26:45] Speaker 3: 嗯。
[00:26:47] Speaker 1: 对吧？
[00:26:47] Speaker 3: 对。
[00:26:48] Speaker 1: 原则上可以。
[00:26:54] Speaker 3: 嗯。
[00:26:55] Speaker 1: 相当于分两级：Node Attestor 先验证整个 node。
[00:27:01] Speaker 3: 嗯。
[00:27:01] Speaker 1: Node Attestation 对应的身份签发决策在 Server 侧，对吧？
[00:27:05] Speaker 3: 对。
[00:27:05] Speaker 1: Node Attestor 验证通过后，就认为这一层的 trust base 可以信任。
[00:27:12] Speaker 3: 对。
[00:27:13] Speaker 1: 然后 Workload Attestor 的验证需要包含前面 Node Attestation 已经确认的度量或上下文。
[00:27:20] Speaker 3: 是。
[00:27:20] Speaker 1: 这部分需要关联起来。Evidence Provider 只是一个独立的证据获取能力，不应把它和 Workload Attestor 本身混为一谈。
[00:27:32] Speaker 3: 明白。
[00:27:32] Speaker 1: 你需要设计一个面向 TEE 的——
[00:27:39] Speaker 3: 那等于说——
[00:27:40] Speaker 1: Workload Attestor。刚才说错了，不是 Node Attestor。
[00:27:41] Speaker 3: 哦，那等于说要改 SPIRE Agent 的插件，对吧？
[00:27:45] Speaker 1: 对，做插件。
[00:27:46] Speaker 3: Plugin。
[00:27:47] Speaker 1: 对，专门针对 TDX/TEE 增加一个 Workload Attestor。
[00:27:52] Speaker 3: 对。
[00:27:52] Speaker 1: 这个插件负责组织证据——
[00:27:54] Speaker 3: 是。
[00:27:55] Speaker 1: 并完成与 Trustee 的通信。
[00:27:57] Speaker 3: 对。这样更直接，但我不确定技术实现难度。玲玲这边可能需要看看这个 plugin 具体怎么做。[“玲玲”姓名听写待确认]
[00:28:07] Speaker 1: 它是用 Go 写的，对吧？
[00:28:09] Ryan: 对，是 Go 写的。Workload Attestor 本身并不复杂。
[00:28:15] Speaker 1: 很简单，主要就是验证策略和你提供的 attributes。
[00:28:20] Ryan: 我不确定它是否支持这种思路。
[00:28:23] Speaker 3: 对，这需要看一下。
[00:28:25] Speaker 1: 还有一种方式是……我想想，最终还是需要连接 Trustee，只有这样取得的验证结果才有意义。
[00:28:39] Speaker 3: 对。
[00:28:41] Speaker 1: 这套信息能从 Server 侧配置下来吗？有没有这种可能？哦，不行，因为 Workload Attestor 原则上不会调用到 Server。
[00:28:52] Speaker 3: 对。
[00:28:54] Speaker 1: Server 侧只是预先配置与身份匹配相关的 attributes/selectors 或 Registration Entry。
[00:28:58] Speaker 3: 对。
[00:29:00] Speaker 1: 现在的问题是，我能预先给它一个 workload 的……[原句未说完]
[00:29:10] Speaker 3: 或者在 Node Attestation 过程中，把 Trustee 相关的信息传到那边。
[00:29:15] Speaker 1: 传不了。
[00:29:16] Speaker 3: 为什么传不了？
[00:29:17] Speaker 1: 因为在我们的场景里使用的是 RTMR 累积度量方式。
[00:29:24] Speaker 3: 嗯。
[00:29:24] Speaker 1: 这个方式允许后续启动多个镜像、多个服务镜像。
[00:29:29] Speaker 3: 嗯。
[00:29:29] Speaker 1: 它们的启动先后顺序等信息，也会影响或进入后续度量。
[00:29:32] Speaker 3: 哦，明白。
[00:29:33] Speaker 1: 原来我们说要把透明日志和 RTMR 绑定起来，再与 Trustee 通信。
[00:29:41] Speaker 3: 嗯。
[00:29:41] Speaker 1: 对吧？
[00:29:42] Speaker 3: 对。
[00:29:42] Speaker 1: 我们原先想把这套流程集中在 SPIRE Server 侧。
[00:29:49] Speaker 3: 嗯。
[00:29:49] Speaker 1: 但 Workload Attestor 只在本地 Agent 侧执行验证。
[00:29:53] Speaker 3: 对。
[00:29:54] Speaker 1: 不过这个“本地验证”实际仍需要与 Trustee 通信。
[00:29:58] Speaker 3: 对。
[00:29:59] Speaker 1: 所以架构就变得比较微妙。因为我们原先设想的 attestation，最终都要由可信第三方完成验证。
[00:30:10] Speaker 3: 对。那这一步是不是有一点重复？因为会获取两次 Quote。
[00:30:18] Speaker 1: 不完全重复。第一次获取 Quote 是为了证明当前 node。
[00:30:24] Speaker 3: 对。两次验证内容不同，但可能包含重复部分。
[00:30:30] Speaker 1: 对。
[00:30:31] Speaker 3: 也就是某些环境信息会采集两遍，对吧？
[00:30:34] Speaker 1: 对。第二步是在 workload 启动后、准备给它颁发证书的过程中——
[00:30:41] Speaker 3: 对。
[00:30:42] Speaker 1: 再验证这个具体 workload 的启动和运行状态。
[00:30:44] Speaker 3: 嗯。
[00:30:45] Speaker 1: 远程证明这套东西确实比较繁琐。
[00:30:48] Speaker 3: 嗯。
[00:30:49] Speaker 1: 但这已经是相对好一些的方法。
[00:30:51] Speaker 3: 对，至少可以先按这个思路实现。
[00:30:53] Speaker 1: 对。
[00:30:56] Ryan: 所以先尝试通过自定义 Workload Attestor，把验证请求或证据传给 Trustee。
[00:31:05] Speaker 1: 对。
[00:31:05] Ryan: 我觉得可以先试试。
[00:31:07] Speaker 1: 你之前写 Node Attestor 时也有类似过程。可以把与 Trustee 通信、证据处理等部分做成 Node Attestor 和 Workload Attestor 共用的组件。
[00:31:16] Speaker 3: 对。
[00:31:16] Speaker 1: 对吧？这件事需要讨论清楚。上次我留下的问题是 Workload Attestor 应该怎么做、是否需要 Server 侧能力；现在看 SPIRE Server 并没有为此设计对应执行路径。
[00:31:32] Speaker 3: 对。
[00:31:35] Speaker 1: 任重而道远，这些东西都还需要继续做。
[00:31:40] Speaker 1: 还有其他问题吗？我的 one-on-one 都变成讨论会了。
[00:31:46] Speaker 1: [停顿]
[00:31:47] Ryan: 其他问题暂时没有。我先在真实环境里试一下。
[00:31:52] Speaker 1: 好，先跑起来。
[00:31:59] Ryan: [原句“会觉得”不完整，无法可靠恢复]
```

## 待验证项

- `00:16:55` Speaker 1 的后半句无法从文字上下文恢复。
- `00:24:13` 的“肯定时间小”语义不通，需要原音频确认。
- `00:27:57` 的姓名“玲玲”需要参会人名单或原音频确认。
- `00:31:59` 的“会觉得”是截断语句，需要原音频确认。
- `00:29:17`—`00:29:33` 原转写为 `PCAP / RDM`；结合当前仓库中的 TDX 度量链，本稿暂按 `RTMR 累积度量 / RTMR` 整理。这是上下文校正，不是声学确认。

## 会后行动项

| 事项 | 建议负责人 | 验收方式 |
| --- | --- | --- |
| 跑通普通 Workload API + 通用代理路径 | Ryan | OpenViking 业务源码不改；代理取得并使用 SVID；OpenClaw 与 OpenViking 通信成功 |
| 验证 Broker API + `WorkloadPIDReference` 路径 | Ryan | Broker 能代表真实 OpenViking PID 取得目标 SVID；错误 PID 无法取得身份 |
| 明确 NGINX 与 Broker Sidecar 的职责和取舍 | Ryan / 斯远 | 对比身份获取、SVID 保管、mTLS 终止、轮换与进程生命周期绑定 |
| 验证自定义 Workload Attestor 的插件能力 | Ryan | SPIRE Agent 能把目标 PID 交给插件；插件能调用 Evidence Provider/Trustee 并返回可信 selector |
| 区分 Node Attestation 与 Workload Attestation 证据 | 方案负责人 | 明确两次 Quote 的验证对象、时点、复用字段与不可复用字段 |
| 在真实 TDVM 环境完成端到端验证 | Ryan | Node Attestation、Workload Attestation、SVID 签发与跨 TDVM mTLS 全链路有可复查证据 |
