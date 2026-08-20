# Argus 非对称 SPIFFE 远程评估报告（E3–E7，Mock RA + Mock Trustee）

> **历史证据：** 本报告测量的是 2026-08-10 旧 OpenViking
> Python TLS/materializer Profile。数据继续保留，但不能外推为当前 Broker Sidecar
> 的功能、轮换、资源或容量结果。Broker Sidecar 的评估方法以
> [当前评估方案](./Argus-Asymmetric-Attestation-SPIFFE-Evaluation-Plan.md)为准。
>
> 对应评估方案：[Argus-Asymmetric-Attestation-SPIFFE-Evaluation-Plan.md](./Argus-Asymmetric-Attestation-SPIFFE-Evaluation-Plan.md)
>
> 功能验收报告：[Argus-Asymmetric-Attestation-SPIFFE-Remote-Validation-Report.md](./Argus-Asymmetric-Attestation-SPIFFE-Remote-Validation-Report.md)

- **Profile:** `Argus-Asymmetric-Attestation-SPIFFE`（OpenClaw = x509pop Relying Party，OpenViking = argus_tdx 已证明工作负载，caller-local Rust Guard，原生 SPIFFE mTLS）
- **Host:** 远程 Linux TDX 主机 `cwf-bkc`，kernel `6.18.10-tdx`，repo 工作目录 `/home/ying_liu/confidential-computing-zoo/cczoo/agent-cc`
- **Branch / HEAD:** `feat/argus-spiffe-v2-val` @ `d5d9ea5ec0cec28e810ea99006613b759020482c`
- **Runtime:** `/var/lib/argus-spiffe-asymmetric/run-20260810T032457Z`（复用已验证目录，Guard/OpenClaw 镜像重建后按新 digest 重注册）
- **Attestation profile:** **Mock Evidence Provider + Mock Trustee**（`manifest.json` 含 `attestation_profile: mock_ra_mock_trustee`、`real_quote_qgs: deferred`）
- **执行时间（UTC）:** 2026-08-10 12:20:20 起（run `started_at_utc`）
- **结果边界:** 本报告只描述软件数据路径、资源、容量、SVID 轮换与证明调用次数；**不代表真实 TDX Quote / QGS / production Trustee 性能，不构成生产容量承诺。**

---

## 1. 执行概况与退出码

| 阶段 | 命令 | 结果 | 退出码 |
|---|---|---|---|
| 单元测试 | `remote-test.sh unit` | **PASS**（Rust Guard 42 测试、Go 插件/组件、npm 传输、Python、benchmark 工具全绿） | 0 |
| 集成 | `remote-test.sh integration` | **PASS**（架构 / Guard 故障矩阵 / 业务 E2E 三组全过） | 0 |
| 基准预检 | `remote-benchmark.sh preflight` | **PASS**（Guard spiffe_identity 健康、/metrics、SPIRE metrics、SVID、预检 guarded 请求） | 0 |
| 全量基准 | `remote-benchmark.sh all` | **PASS**（E3×5 + E4×3 + E5×4 + E6×1 共 13 用例全部通过） | 0（`BENCHMARK_EXIT=0`） |
| 扩展容量阶梯 | `remote-benchmark.sh e5`（`E5_QPS_STEPS=250,500,1000,2000`） | **PASS**（4 用例，§5.1） | 0 |
| E6 新建连接探针（修复前） | `remote-benchmark.sh e6nc` | 运行成功；**暴露 load-generator 凭据缓存缺陷**（§6.1） | 0 |
| E6 新建连接探针（修复后复测） | `remote-benchmark.sh e6nc` | **PASS**（跨全部轮换窗口 100% 成功，§6.1） | 0 |

`BENCHMARK_EXIT=0` 语义：case 已实际执行且 ≥1 个成功请求，**不代表**达到目标 QPS 或保证成功率；各档目标达成情况见 §3–§6 与 §5.1。

执行顺序与失败记录（如实标注，未降低标准）：

1. **unit 早期日志 `UNIT_EXIT=101`** 来自 `feat/argus-spiffe-v2-val` 正确检出之前的工作树状态（`core/argus` 测试目标无法编译：`unresolved import argus::tdx_verifier`、`generate_nonce_with_size` 缺失、`BindingIdentityClaims` 缺少新字段）。当前工作树 `remote-test.sh unit` 干净通过。
2. **integration 首轮业务 E2E FAIL**：OpenViking commit 的 Phase-2 `long_term` 记忆抽取调用外部 VLM 模型网关（`gateway.aichina.intel.com`，`minimax-m2.7`）时网关瞬时不可达，抽取请求挂满 openai 客户端默认 600 s 超时；归档本体已写入但归档概览未完成，E2E 60×5 s 轮询超时。网关恢复后（OpenViking 容器内实测 1.4 s 成功）重跑 `integration` 通过。该路径位于第三方 OpenViking 会话压缩逻辑 + 外部模型网关，**不在本分支 Guard/OpenClaw/benchmark 代码范围内，无代码修复**。
3. 一次中间 integration 运行仅因该 shell 未导出 `V2_RUNTIME_DIR` 导致架构检查路径不匹配而 FAIL（非代码问题）；补全环境后 PASS。

---

## 2. 结果目录（完整）

```
/var/lib/argus-spire-asymmetric/benchmarks/run-20260810T122020Z/
├── manifest.json                 # mock_ra_mock_trustee, git d5d9ea5, host cwf-bkc, target https://openviking.argus.local:1943/health
├── summary.json                  # 192 KB：全部 13 个 case 聚合 + E7 摊销
├── report.md                     # 自动生成的 Markdown 汇总
├── guard-metrics-before/after.prom
├── spire-metrics-before/after.prom
└── cases/  (13 个用例目录，各含 metadata.json / requests.jsonl / resources.jsonl / collector.{stdout,stderr}.log / collector.stop / load-generator.stderr.log)
     e3-guard-c1   e3-guard-c4   e3-guard-c8   e3-guard-c16   e3-guard-c32
     e4-guarded-new-connection   e4-guarded-keepalive   e4-diagnostic-mtls-only
     e5-qps-10   e5-qps-25   e5-qps-50   e5-qps-100
     e6-svid-rotation
```

- 目录总大小 66 MB；所有 case 的 `collector_errors: 0`。
- 唯一 stderr：`e4-diagnostic-mtls-only/load-generator.stderr.log` 中的 Node `MaxListenersExceededWarning`（并发 TLS listener 超过默认 10 上限的告警）；2000 请求 100% 成功，不影响数据质量。
- 顶层另存运行日志 `benchmarks/run-all-20260810T122019Z.log`。

**补充 run 目录**（同一宿主，`V2_RUNTIME_DIR` 相同，结构同上；证据见 §9 清单）：

```
run-20260810T122020Z-e5x/           # E5 扩展容量阶梯：cases/{e5-qps-250,e5-qps-500,e5-qps-1000,e5-qps-2000}（manifest action=e5, git edd1ea1）
run-20260810T122020Z-e6nc/          # E6 新建连接探针【修复前】：cases/e6-svid-rotation-new-connection（暴露凭据缓存缺陷）
run-20260810T122020Z-e6nc-fixed/    # E6 新建连接探针【修复后】：cases/e6-svid-rotation-new-connection
```

---

## 3. E3 —— Argus Guard 决策性能与开销

模式 `guard`（直接打 Guard `/guard/v1/authorize`），每档 30 s。QPS/延迟为 load-generator 实测。

| 并发 | 实测 QPS | 请求/成功 | P50 ms | P95 ms | P99 ms | Guard CPU avg % | Guard RSS 峰 MiB | Guard FD 峰 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 7,346.78 | 220,404 | 0.12 | 0.15 | 0.27 | 18.09 | 24.68 | 12 |
| 4 | 8,221.47 | 246,646 | 0.43 | 0.54 | 1.03 | 21.49 | 28.38 | 18 |
| 8 | 8,138.29 | 244,151 | 0.86 | 1.17 | 5.86 | 20.06 | 31.02 | 26 |
| 16 | 7,632.56 | 228,982 | 1.87 | 2.72 | 8.02 | 21.40 | 34.86 | 42 |
| 32 | 7,360.30 | 220,818 | 3.91 | 8.06 | 10.87 | 18.37 | 35.38 | 74 |

要点：

- 全部 100% 成功（0 DENY、0 error）。全 benchmark 累计 **1,195,246 次 Guard 决策全部 ALLOW**（`guard-metrics-after.prom` `argus_guard_requests_total{decision="allow"}=1195320`，before=74，增量 1,195,246；`deny=0`）。
- Guard 内部决策耗时直方图（`argus_guard_decision_duration_seconds`）显示几乎全部决策 ≤0.25 ms；高并发下 load-generator 实测 P50 上升来自 HTTP 往返/排队，决策逻辑本身亚毫秒。
- 峰值吞吐约 **8,200 QPS**（并发 4–8）；并发 32 时 P99 升至 10.87 ms（尾部退化）。
- 资源开销极小：RSS ≤35 MiB，CPU ≈0.2 核，FD 随并发 12→74。SPIRE Server、两侧 SPIRE Agent、Mock Trustee、Mock Evidence Provider 均 <0.5% CPU。

---

## 4. E4 —— SPIFFE mTLS 连接开销

并发 8，每档 2000 请求。

| Profile | 实测 QPS | P50 ms | P95 ms | P99 ms | 握手 P50 ms | 连接复用率 | OpenViking CPU avg % |
|---|---:|---:|---:|---:|---:|---:|---:|
| diagnostic-mtls-only（无 Guard 基线） | 1,101.99 | 6.94 | 8.35 | 9.17 | 7.98 | 99.65% | 1.48 |
| guarded-keepalive（正式路径） | 682.24 | 11.82 | 15.82 | 16.92 | N/A（连接复用） | — | 17.61 |
| guarded-new-connection（每请求新建 mTLS） | 199.36 | 32.53 | 38.90 | 89.55 | 8.66 | 0.00% | 90.11 |

要点：

- **完整受保护路径相对 diagnostic mTLS 直连基线的额外开销**：guarded-keepalive（正式路径）相对 diagnostic-mtls-only（直连基线）P50 **+4.9 ms**（11.82 vs 6.94 ms），吞吐约减半（682 vs 1,102 QPS）。需要注意：**两侧客户端实现不同**（正式路径走 Guard + SPIFFE 传输 + OpenViking 原生 TLS 的完整受保护路径；diagnostic 直连基线是独立的诊断 mTLS 客户端，跳过 Guard），因此该差值不能归因于 Guard 单一组件。**Guard 单点决策性能见 E3**（决策本身亚毫秒、单点 ~8,200 QPS）——E4 的 P50 增量包含传输、Guard 往返、客户端实现差异与并发排队等综合因素。
- **新连接开销**：每请求新建 mTLS 连接在 keep-alive 之上再 **+20.7 ms P50**（32.53 vs 11.82），尾部显著变差（P99 89.6 ms，单点 max 1,106 ms），吞吐再降约 3.4×（682→199）。
- **握手成本**：新连接握手 P50 ≈8.7 ms，且服务端可见——new-connection 档 OpenViking CPU 升至 90%（TLS 握手开销），keep-alive 仅 17.6%。
- **连接数说明（三个 E4 档的 connections 峰值）**：E4 实际执行顺序为 `e4-guarded-new-connection` → `e4-guarded-keepalive` → `e4-diagnostic-mtls-only`（由 resources.jsonl 时间戳确认）。新连接档每请求新建连接产生大量 **TIME-WAIT 残留**（TD 侧观测到 `TIME-WAIT=1234`，持续约 60 s），背靠背覆盖后两档：keep-alive 与 diagnostic 档各自的采样均含该残留（TD 侧 `target_total=1235`，其中 `TIME-WAIT=1234`、`ESTAB=5`、`LISTEN=7`）。**原始 resources.jsonl 中三个 E4 档的峰值均为 ≈1235**；`report.md` 自动表对 new-connection 档显示 735，是因为其报告窗口过滤掉了最后一条 1235 峰值采样（落在 load-generator 记录的 duration 窗口之后），属聚合口径差异而非实测差异。稳态 ESTAB 各档仅 5–8，LISTEN=7 恒定，正式 keep-alive 路径稳态连接占用极小。
- 验证声明：`diagnostic_mtls_only` 仅作传输诊断基线，**不是**正式业务性能结果；其与正式路径的客户端实现不同，差异不直接归因于 Guard。

---

## 5. E5 —— 完整链路容量曲线

模式 guarded-keepalive，并发 32，每档 60 s。

| 目标 QPS | 实测 QPS | 请求/成功 | P50 ms | P95 ms | P99 ms | 稳态连接 |
|---|---:|---:|---:|---:|---:|---:|
| 10 | 10.00 | 601/601 | 4.03 | 4.79 | 5.34 | 2–3 |
| 25 | 25.00 | 1,501/1,501 | 3.87 | 4.53 | 5.01 | 2–3 |
| 50 | 50.00 | 3,001/3,001 | 3.80 | 4.47 | 4.93 | 2–3 |
| 100 | 100.00 | 6,001/6,001 | 3.65 | 4.28 | 4.92 | 2–3 |

### 5.1 扩展容量阶梯（250–2000 QPS，独立 run `run-20260810T122020Z-e5x`）

**`BENCHMARK_EXIT=0` 只代表各档用例执行完成**（run.sh 的 `run_case` 校验仅要求 summary 中至少有 1 个成功请求），不代表目标 QPS 被满足、也不代表无错误率。为定位真实拐点，在独立 run 目录按 **250/500/1000/2000 QPS** 阶梯继续上探（每档 60 s，并发 32，独立 `ARGUS_BENCHMARK_RUN_DIR=run-20260810T122020Z-e5x`），并预先设定停止条件：**错误率**（任一失败请求，基线为 100% 成功）、**延迟**（P50 ≥15 ms 或 P99 ≥30 ms，≈10× 低负载基线）、**资源**（任一组件 CPU avg ≥85% 或主机 load_1m ≥4）。未预先设定或伪造"最大 QPS"。

| 目标 QPS | 实测 QPS | 请求/成功 | P50 ms | P95 ms | P99 ms | OpenViking CPU avg % | 停止条件触发 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 250 | 250.00 | 15,001/15,001 | 3.18 | 3.85 | 4.43 | 37.8 | — |
| 500 | 499.98 | 30,001/30,001 | 3.24 | 6.58 | 11.81 | 60.5 | — |
| 1000 | **711.83** | 42,744/42,744 | **44.02** | 59.35 | 64.25 | 72.4 | **延迟拐点**（P50 44 ms） |
| 2000 | **713.61** | 42,850/42,849 | 43.99 | 58.97 | 63.72 | 71.5 | **错误率**（1/42,850 fetch failed） |

要点：

- **容量拐点位于 (500, 1000]**：≤500 QPS 时实测 QPS 与目标一致、100% 成功、P50 平坦（3.2–3.7 ms）；1000 QPS 起系统无法维持目标速率（实测仅 ~712 QPS，因并发 32 的排队饱和），P50 跳升至 44 ms（约 14×），P99 64 ms。2000 QPS 档确认饱和平台 ~713 QPS，并出现本阶梯唯一 1 次失败（`fetch failed`，网络层超时/重置）。
- **瓶颈为 OpenViking（TD 侧工作负载）**：饱和档 OpenViking CPU 71–72%，Guard 仅 4.3–4.5%、OpenClaw 37–41%、主机 CPU 1.4%。Guard 单点即可 ~8,000 QPS（E3），**完整受保护路径的吞吐上限由 OpenViking 的 TLS/HTTP 服务能力决定，而非 Guard 或 OpenClaw**。
- **不承诺"最大稳定 QPS"**：500 QPS 是最后一个完全维持目标速率且延迟平坦的档位；超过 500 后饱和平台 ~712 QPS 是并发 32 下的观测吞吐上限（非线性容量）。为不进一步过度打共享服务，未在 500–1000 之间补采（如 750 QPS）细化拐点。
- 连接数同 §4 说明：`e5-qps-10` 的 "connections peak 1236" 为 E4 新连接残留 TIME-WAIT；`e5-qps-25/50/100` 显示真实稳态 2–3；扩展阶梯稳态连接 1–3（keep-alive 复用）。

---

## 6. E6 —— SVID 轮换期间业务稳定性

QPS 10、并发 8、持续 1890 s（≈31.5 min，覆盖远超 3 个 SVID TTL 周期）。

| 指标 | 值 |
|---|---:|
| 请求 / 成功 / 失败 | 18,901 / 18,901 / **0** |
| P50 / P95 / P99 ms | 3.68 / 4.31 / 4.87（max 358.04，t≈1051 s 短瞬抖动簇，远离轮换） |
| **OpenViking SVID 轮换次数** | **7**（OpenClaw workload SVID 亦 7 次；实测 ~300 s 周期，`svid_rotations{openviking}=7`） |
| **轮换期间失败请求 / 新连接失败** | **0 / 0** |
| **轮换期间新增 Trustee 请求** | **0**（`trustee_requests_observed_delta=0`，SPIRE 前后快照一致） |
| 轮换窗口延迟 | 每次轮换 ±15 s 窗口与基线无差异（窗口 p50 3.5–3.8 ms、p99 ≤5.1 ms、max ≤13 ms） |

要点：

- 7 次 SVID 轮换对持续业务**无可观测延迟扰动、无失败**。
- 唯一 max 358 ms 出现在 t≈1051 s，远离任何轮换时刻（最近一次轮换在 t=968 s，相隔 82.8 s），为一次短暂抖动簇（t=1050.8–1051.1 s 内连续 4 个请求 358/263/164/64 ms，疑为 GC/调度），与轮换无关。
- E6 资源：OpenClaw CPU 1.29% avg、OpenViking 3.02%、Guard 0.11%；OpenClaw RSS 峰 514 MiB。
- 边界：**Workload SVID 轮换不是重新远程证明**，不触发新的 Trustee 验证。

### 6.1 主动新建 mTLS 连接探针（新增独立成功/失败指标）

§6 的 keep-alive 路径只证明「已建立的连接在轮换期间保持可用」，无法独立证明「轮换期间**新建** mTLS 连接」成功。为补足该证据，新增 `e6nc` action（`guarded-new-connection` 模式：每请求先执行 Guard `authorize()`，再新建一条 mTLS 连接，`reused_connection=false`，逐请求独立记录 `handshake_ms` 与 `ok/error`），以 QPS 10 / 并发 4 / 1890 s（>3×SVID TTL）运行。

**首轮运行（`run-…-e6nc`）暴露 load-generator 凭据缓存缺陷**：

| 指标 | 值 |
|---|---:|
| 请求 / 成功 / 失败 | 18,901 / 4,695 / **14,206**（全部 `socket hang up`） |
| 失败起始 | t≈469.5 s，与启动时加载 SVID 的 `not_after`（t≈467.8 s）吻合 |
| 首个轮换窗口（t≈144 s）失败 | **0**；此后所有窗口（t≈710、983 s…）持续 100% 失败 |

根因：`load-generator.mjs` 仅在 executor 启动时读取一次 `/run/argus-svid/{svid.pem,svid-key.pem,bundle.pem}`；启动时加载的 SVID 过期后（t≈468 s），每次新建连接都携带**过期客户端证书**被对端拒绝。已排除运行时/环境原因：容器内 `/run/argus-svid/` 由 materializer 正常刷新（SVID serial 每 ~280 s 轮换）；Guard 全程 **ALLOW**（`argus_guard_requests_total{decision="allow"}` 持续递增、`deny=0`）；OpenViking 容器全程存活（CPU ~6.5%→~3%、pids 46 恒定，失败期 CPU 下降仅为「更少请求被处理」的伴生现象）。

**修复**：`load-generator.mjs` 改为每次新建连接前重新读取 SVID 凭据（`loadCredentials()` 移入每次请求执行路径；`diagnostic-mtls-only` 同样受益，新建 socket 均使用当前 SVID）。这是 **benchmark 测试工具修复**，不是 Guard/OpenClaw/OpenViking 运行时改动。

**复测（`run-…-e6nc-fixed`，同一 `V2_RUNTIME_DIR`）**：

| 指标 | 值 |
|---|---:|
| 请求 / 成功 / 失败 | **18,901 / 18,901 / 0（100%）** |
| P50 / P95 / P99 ms | 7.36 / 8.63 / 9.41（max 86.50） |
| 握手 P50 / P95 ms | 3.11 / 3.57（全部 `reused_connection=false`） |
| **OpenViking SVID 轮换** | **6**（OpenClaw SVID serial 变化 7，5 s 采样边界差 1） |
| 轮换窗口内请求 / 失败 | 17,875 / **0**（7 个轮换窗口 100% 成功） |
| **新增 Trustee 请求** | **0**（`trustee_requests_observed_delta=0`） |

结论：修复后，跨全部轮换窗口的主动新建 mTLS 连接 **100% 成功**，与 §6 keep-alive 结果一致，独立证明**轮换不破坏新建连接**（每条连接都在轮换前后新建立且握手成功）。缺陷与修复已留档（见 §8.2 与证据清单 §9）。

---

## 7. E7 —— 证明摊销（E5+E6 成功业务请求 30,005）

| 指标 | 值 |
|---|---:|
| 成功业务请求（E5+E6） | 30,005 |
| 期间新增 Node Attestation | **0** |
| 期间新增 Trustee 请求 | **0** |
| business requests / attestation | 30,005.00 |
| Trustee requests / 1000 business requests | 0.03 |
| Trustee requests / SVID rotation | 0.00 |

SPIRE 前后 Prometheus 快照完全一致（`spire_server_argus_nodeattestor_attempts{result="success"}=1`、`spire_server_argus_nodeattestor_trustee_requests{result="success"}=1`，均为初始 Agent admission）。

**独立佐证（扩展容量阶梯 `run-…-e5x`）**：该独立 run（E5 250/500/1000/2000 QPS 四个 case，合计 **130,595** 个业务请求，见 §5.1）期间新增 Node Attestation = **0**、新增 Trustee 请求 = **0**（SPIRE 前后快照一致），`Trustee requests / 1000 business requests = 0.01`。即摊销结论在更高请求量（13 万级）下同样成立。

结论：**Node Attestation 与 Trustee 调用与业务请求量完全解耦；业务请求增长不引起证明请求线性增加，SVID 轮换也不触发新 Trustee 验证。** 这是本轮最有意义的软件链路结论。按评估方案要求，不把该摊销折算成任何"真实 RA 节省的时间"收益（真实 Quote/QGS/Trustee 时间收益不在本报告范围）。

---

## 8. 修改文件与可复现性

**评估期落盘的提交**（已 push 到 `upstream/feat/argus-spiffe-v2-val`）：

- `core/spire/runtime/asymmetric/scripts/register-workloads.sh` — 将 `docker:image_id` 固定为镜像 **tag**（val 分支缺 main 分支 `d8facb0` 的修复）。原因：SPIRE docker WorkloadAttestor 的 `docker:image_id` 来自 `container.Config.Image`（即 `docker run` 传入的 tag），而 `docker:image_config_digest` 才是不可变 config digest；旧脚本把 image_id 也指向 digest，镜像重建后会导致两个 selector 无法同时匹配、workload 拿不到 SVID。已按重建后的新 digest 重注册并验证 SVID 正常签发。

**本轮补充的工作树改动**（未 push，见 §8.2）：

- `core/spire/benchmarks/asymmetric/run.sh` — 新增 `e6nc` action（`run_e6nc`，`guarded-new-connection` 模式、默认 3×TTL+60 s，独立于 `all`）。
- `core/spire/benchmarks/asymmetric/load-generator.mjs` — 修复 SVID 凭据缓存：每次新建 mTLS 连接前重新读取 `/run/argus-svid/`（详见 §6.1）。
- `documents_ly/Argus-Asymmetric-Attestation-SPIFFE-Evidence-Manifest.md` — 新增：原始证据 SHA256 清单说明（详见 §9）。

### 8.1 可复现性时间线

- **实验时刻 HEAD**：`d5d9ea5ec0cec28e810ea99006613b759020482c`（`feat/argus-spiffe-v2-val`，与 `manifest.json` 的 `git_commit` 一致）。E3–E7 全部原始数据均在 `d5d9ea5` 之上测得。
- **实验时的工作树补丁**：上述 `register-workloads.sh` 修改在实验期间仅存在于工作树（未提交）。`manifest.json` 记录的是 `d5d9ea5`，不含该补丁的 commit SHA；该补丁正是镜像重建后 workload 能拿到 SVID 的原因，因此是运行基准的前提。
- **实验后的落盘提交**（已 push 到 `upstream/feat/argus-spiffe-v2-val`）：
  - `c379385` — `fix(agent-cc): pin docker:image_id to image tag in workload registration`（即实验期工作树补丁的正式提交）。
  - `edd1ea1` — `docs(agent-cc): record E3–E7 asymmetric benchmark results under Mock RA/Mock Trustee`（本报告）。
- **复现步骤**：`git checkout d5d9ea5` → 应用 §8 的 `register-workloads.sh` 修改（等价于 `git checkout c379385`）→ 复用 `V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-20260810T032457Z` → 按 asymmetric README 运行 `remote-benchmark.sh all`。

### 8.2 补充评估 run（复现性）

以下补充 run 均在同一宿主、同一 `V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-20260810T032457Z` 上执行，证据保留（见 §9 清单）：

| Run | 内容 | 状态 |
|---|---|---|
| `run-20260810T122020Z-e5x` | E5 扩展容量阶梯 250/500/1000/2000 QPS（`remote-benchmark.sh e5` + `E5_QPS_STEPS`） | 完成（§5.1） |
| `run-20260810T122020Z-e6nc` | E6 主动新建 mTLS 连接探针（**修复前**，暴露 load-generator 凭据缓存缺陷） | 完成（§6.1） |
| `run-20260810T122020Z-e6nc-fixed` | 同探针复测（**修复后**） | 完成（§6.1） |

三个补充 run 的 `manifest.json` 均记录 `git_commit=edd1ea1`（启动时 HEAD）。注意：`e6nc-fixed` 复测在启动时 **load-generator.mjs 修复仅存在于工作树（未提交）**，由 run.sh 在启动前 `docker cp` 进容器；复现复测需应用 §8 的 `load-generator.mjs` 修复（等价于提交后的 commit）。

---

## 9. 证据与数据来源（未手工填写）

- 所有 QPS/延迟/成功率来自各 case 的 `requests.jsonl`（load-generator 输出）；资源曲线来自 `resources.jsonl`（collector.py 每 5 s 采样，host + TD Guest 容器 + SPIRE/Guard metrics + SVID serial）。
- Guard 决策总数与内部延迟直方图来自 `guard-metrics-before/after.prom`；SPIRE NodeAttestor/Trustee 计数器来自 `spire-metrics-before/after.prom`。
- E7 由 `report.py` 按方案公式从 E5/E6 请求数与 SPIRE 计数器增量计算（`summary.json` `e7` 字段）。
- `null` / `N/A` 表示无实测数据，未用配置值或目标值补齐。
- **原始证据清单与校验**：见 [`Argus-Asymmetric-Attestation-SPIFFE-Evidence-Manifest.md`](./Argus-Asymmetric-Attestation-SPIFFE-Evidence-Manifest.md) 与宿主 `/var/lib/argus-spire-asymmetric/benchmarks/SHA256SUMS.txt`。主评估与全部补充 run（e5x/e6nc/e6nc-fixed）的每个文件均按 SHA256 留档；清单生成前对全部文本证据做秘密模式扫描（API key、私钥、证书、Bearer、AKIA），命中即拒绝输出，当前 **0 命中**。归档 `argus-asymmetric-benchmark-evidence-20260810.tar.gz` 含 `SHA256SUMS.txt`，可用 `sha256sum -c` 校验。

---

## 10. 结果边界（Mock RA + Mock Trustee）

- 本结果来自 **Mock Evidence Provider + Mock Trustee** 环境下的远程主机实测，`manifest.json` / `report.md` 均显式携带 `mock_ra_mock_trustee` 与 `real_quote_qgs: deferred`。
- 不能由本报告得出：真实 TDX Quote 或 QGS 性能、production Trustee 与真实 TCB 验证性能、真实远程证明节省的绝对时间、多 OpenClaw / 多 Agent Service / 生产环境容量、生产级安全与可用性承诺。
- `diagnostic_mtls_only` 仅作传输诊断基线；E5 容量结论适用于 10–100 QPS 阶梯及扩展阶梯（250/500/1000/2000 QPS，见 §5.1）：<500 QPS 延迟平坦，超过 500 QPS 后进入饱和平台（并发 32 下观测吞吐上限 ~712–714 QPS，瓶颈为 OpenViking），未作"最大稳定 QPS"承诺。
- §6.1 发现的 load-generator 凭据缓存缺陷是 **benchmark 测试工具缺陷**（已修复并复测），不是 Guard/OpenClaw/OpenViking 运行时缺陷；复测跨全部轮换窗口 100% 成功。
- 保密约束：本报告与证据清单不含任何 API Key、SVID 私钥、证书私钥或网关 token。

---

*End of report.*
