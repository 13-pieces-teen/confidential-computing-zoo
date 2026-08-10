# Argus 非对称 SPIFFE 远程评估报告（E3–E7，Mock RA + Mock Trustee）

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
| 单元测试 | `remote-test.sh unit` | **PASS**（Rust Guard 43 测试、Go 插件/组件、npm 传输、Python、benchmark 工具全绿） | 0 |
| 集成 | `remote-test.sh integration` | **PASS**（架构 / Guard 故障矩阵 / 业务 E2E 三组全过） | 0 |
| 基准预检 | `remote-benchmark.sh preflight` | **PASS**（Guard spiffe_identity 健康、/metrics、SPIRE metrics、SVID、预检 guarded 请求） | 0 |
| 全量基准 | `remote-benchmark.sh all` | **PASS**（E3×5 + E4×3 + E5×4 + E6×1 共 13 用例全部通过） | 0（`BENCHMARK_EXIT=0`） |

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

- **Guard 开销**：keep-alive 相对无 Guard 基线 P50 **+4.9 ms**（11.82 vs 6.94），吞吐约减半（682 vs 1,102）。
- **新连接开销**：每请求新建 mTLS 连接在 keep-alive 之上再 **+20.7 ms P50**（32.53 vs 11.82），尾部显著变差（P99 89.6 ms，单点 max 1,106 ms），吞吐再降约 3.4×（682→199）。
- **握手成本**：新连接握手 P50 ≈8.7 ms，且服务端可见——new-connection 档 OpenViking CPU 升至 90%（TLS 握手开销），keep-alive 仅 17.6%。
- **连接数说明**：report 中三个 E4 档 "connections peak ≈1235" 是 `e4-guarded-new-connection` 留下的 **TIME-WAIT 残留**（每请求新建连接产生大量 TIME-WAIT，持续约 60 s，跨越背靠背的 E4 用例；首个 keep-alive 采样即含 1234 条 TIME-WAIT）。稳态 ESTAB 各档仅 5–8，LISTEN=7 恒定。正式 keep-alive 路径稳态连接占用极小。
- 验证声明：`diagnostic_mtls_only` 仅作传输诊断基线，**不是**正式业务性能结果。

---

## 5. E5 —— 完整链路容量曲线

模式 guarded-keepalive，并发 32，每档 60 s。

| 目标 QPS | 实测 QPS | 请求/成功 | P50 ms | P95 ms | P99 ms | 稳态连接 |
|---|---:|---:|---:|---:|---:|---:|
| 10 | 10.00 | 601/601 | 4.03 | 4.79 | 5.34 | 2–3 |
| 25 | 25.00 | 1,501/1,501 | 3.87 | 4.53 | 5.01 | 2–3 |
| 50 | 50.00 | 3,001/3,001 | 3.80 | 4.47 | 4.93 | 2–3 |
| 100 | 100.00 | 6,001/6,001 | 3.65 | 4.28 | 4.92 | 2–3 |

要点：

- 100% 成功，实测 QPS 与目标一致，延迟全程平坦（~3.6–4.0 ms，随 QPS 略降）。**在配置的 10–100 QPS 区间内未观察到容量拐点（capacity knee）**。
- QPS=100 时资源：OpenClaw CPU 12.96% avg、OpenViking 15.79%、Guard 1.09%、主机 CPU 0.74%，TCP retransmit 峰值 1/间隔；稳态连接仅 2–3 条。所有组件距饱和极远（E3 显示 Guard 单点即可 ~8,000 QPS）。
- **瓶颈结论**：10–100 QPS 区间内无瓶颈；`max_stable_qps` 与 `capacity_knee` 均未触及。要定位真实拐点需将 E5 QPS 阶梯继续上探（超出本轮 README 默认 10/25/50/100），本轮**未越界承诺任何"最大稳定 QPS"**。
- 连接数同 §4 说明：`e5-qps-10` 的 "connections peak 1236" 为 E4 新连接残留 TIME-WAIT；`e5-qps-25/50/100`（分钟级后、残留已清）显示真实稳态 2–3。

---

## 6. E6 —— SVID 轮换期间业务稳定性

QPS 10、并发 8、持续 1890 s（≈31.5 min，覆盖远超 3 个 SVID TTL 周期）。

| 指标 | 值 |
|---|---:|
| 请求 / 成功 / 失败 | 18,901 / 18,901 / **0** |
| P50 / P95 / P99 ms | 3.68 / 4.31 / 4.87（max 358.04，单点孤立） |
| **OpenViking SVID 轮换次数** | **7**（OpenClaw workload SVID 亦 7 次；实测 ~300 s 周期，`svid_rotations{openviking}=7`） |
| **轮换期间失败请求 / 新连接失败** | **0 / 0** |
| **轮换期间新增 Trustee 请求** | **0**（`trustee_requests_observed_delta=0`，SPIRE 前后快照一致） |
| 轮换窗口延迟 | 每次轮换 ±15 s 窗口与基线无差异（窗口 p50 3.5–3.8 ms、p99 ≤5.1 ms、max ≤13 ms） |

要点：

- 7 次 SVID 轮换对持续业务**无可观测延迟扰动、无失败**。
- 唯一 max 358 ms 出现在 t≈1051 s，远离任何轮换时刻，为孤立抖动（GC/调度），与轮换无关。
- E6 资源：OpenClaw CPU 1.29% avg、OpenViking 3.02%、Guard 0.11%；OpenClaw RSS 峰 514 MiB。
- 边界：**Workload SVID 轮换不是重新远程证明**，不触发新的 Trustee 验证。

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

结论：**Node Attestation 与 Trustee 调用与业务请求量完全解耦；业务请求增长不引起证明请求线性增加，SVID 轮换也不触发新 Trustee 验证。** 这是本轮最有意义的软件链路结论。按评估方案要求，不把该摊销折算成任何"真实 RA 节省的时间"收益（真实 Quote/QGS/Trustee 时间收益不在本报告范围）。

---

## 8. 修改文件（本轮唯一，未提交、未 push）

- `core/spire/runtime/asymmetric/scripts/register-workloads.sh` — 将 `docker:image_id` 固定为镜像 **tag**（val 分支缺 main 分支 `d8facb0` 的修复）。原因：SPIRE docker WorkloadAttestor 的 `docker:image_id` 来自 `container.Config.Image`（即 `docker run` 传入的 tag），而 `docker:image_config_digest` 才是不可变 config digest；旧脚本把 image_id 也指向 digest，镜像重建后会导致两个 selector 无法同时匹配、workload 拿不到 SVID。已按重建后的新 digest 重注册并验证 SVID 正常签发。

---

## 9. 证据与数据来源（未手工填写）

- 所有 QPS/延迟/成功率来自各 case 的 `requests.jsonl`（load-generator 输出）；资源曲线来自 `resources.jsonl`（collector.py 每 5 s 采样，host + TD Guest 容器 + SPIRE/Guard metrics + SVID serial）。
- Guard 决策总数与内部延迟直方图来自 `guard-metrics-before/after.prom`；SPIRE NodeAttestor/Trustee 计数器来自 `spire-metrics-before/after.prom`。
- E7 由 `report.py` 按方案公式从 E5/E6 请求数与 SPIRE 计数器增量计算（`summary.json` `e7` 字段）。
- `null` / `N/A` 表示无实测数据，未用配置值或目标值补齐。

---

## 10. 结果边界（Mock RA + Mock Trustee）

- 本结果来自 **Mock Evidence Provider + Mock Trustee** 环境下的远程主机实测，`manifest.json` / `report.md` 均显式携带 `mock_ra_mock_trustee` 与 `real_quote_qgs: deferred`。
- 不能由本报告得出：真实 TDX Quote 或 QGS 性能、production Trustee 与真实 TCB 验证性能、真实远程证明节省的绝对时间、多 OpenClaw / 多 Agent Service / 生产环境容量、生产级安全与可用性承诺。
- `diagnostic_mtls_only` 仅作传输诊断基线；E5 容量结论仅适用于配置的 10–100 QPS 阶梯。
- 保密约束：本报告不含任何 API Key、SVID 私钥、证书私钥或网关 token。

---

*End of report.*
