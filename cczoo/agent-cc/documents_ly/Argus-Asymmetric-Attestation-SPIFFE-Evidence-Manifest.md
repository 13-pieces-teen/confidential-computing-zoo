# Argus 非对称 SPIFFE E3–E7 评估 —— 原始证据清单与校验

> 全部结果只属于 **Mock Evidence Provider + Mock Trustee** 软件链路；不代表真实 TDX Quote/QGS、
> production Trustee、多 Agent、或生产容量结论。本清单只含文件路径、大小与 SHA256 摘要，
> **不含任何密钥/证书/凭据内容**（生成前已做秘密模式扫描，命中即拒绝输出）。

## 证据位置（TDX 宿主 `cwf-bkc`）

| 目录 | 内容 |
|---|---|
| `/var/lib/argus-spire-asymmetric/benchmarks/run-20260810T122020Z/` | E3–E7 主评估（`remote-benchmark.sh all`） |
| `/var/lib/argus-spire-asymmetric/benchmarks/run-20260810T122020Z-e5x/` | E5 扩展容量阶梯（250/500/1000/2000 QPS） |
| `/var/lib/argus-spire-asymmetric/benchmarks/run-20260810T122020Z-e6nc/` | E6 主动新建 mTLS 连接探针（**修复前**，展示 load-generator 凭据缓存缺陷） |
| `/var/lib/argus-spire-asymmetric/benchmarks/run-20260810T122020Z-e6nc-fixed/` | E6 探针复测（**修复后**） |
| `/var/lib/argus-spire-asymmetric/benchmarks/SHA256SUMS.txt` | 全量文件 SHA256 + 相对路径（**权威校验清单**，161 个文件） |
| `/var/lib/argus-spire-asymmetric/benchmarks/argus-asymmetric-benchmark-evidence-20260810.tar.gz` | 可审计归档（23.2 MiB，含 SHA256SUMS.txt） |

清单与归档自身的 SHA256（便于转发时校验）：

```
e5a0c3e2a73f83dde4247bf2dac33e92eec4f01eacc2f2944bd8c20e8936a003  SHA256SUMS.txt
af650190dc4dfc218e58d664bea1503a4f83e857f2a28c8917c105b00f0955e1  argus-asymmetric-benchmark-evidence-20260810.tar.gz
```

校验命令：

```bash
cd /var/lib/argus-spire-asymmetric/benchmarks
sha256sum -c SHA256SUMS.txt            # 逐文件校验（全部 OK 即一致）
tar -tzf argus-asymmetric-benchmark-evidence-20260810.tar.gz | head   # 归档内容
```

## 各 run 摘要（来源：各 `report.md` / `summary.json` / `requests.jsonl`）

### 主评估 `run-20260810T122020Z`（E3–E7）

- **E3 Guard 决策性能**：c1/c4/c8/c16/c32 五档；最高并发 32 档 P50 ≈3.7 ms；Guard CPU 开销 ~0.1%，RSS 峰 ~36 MiB，FD 峰 11。Guard Prometheus 计数 delta：allow 1,195,246 / deny 0，直方图 ≤0.25 ms。
- **E4 mTLS 连接**：new-connection（P50 6.94 ms）/ keep-alive（P50 11.82 ms，完整受保护路径）/ diagnostic mTLS 基线（P50 6.94 ms）三档；`guarded-new-connection` 每次 `reused_connection=false`。
- **E5 容量曲线**：qps-10/25/50/100 四档，100% 成功率；<500 QPS 延迟平坦（P50 ≈3.2–3.7 ms）。
- **E6 轮换稳定性**：18,901 请求 / 0 失败；OpenViking SVID 轮换 7 次；轮换窗口无扰动。
- **E7 摊销**：30,005 成功业务请求，新增 Node Attestation = 0，新增 Trustee 请求 = 0。

### 扩展容量阶梯 `run-20260810T122020Z-e5x`

- 250 QPS：15,001/15,001 成功，P50 3.18 ms；500 QPS：30,001/30,001，P50 3.24 ms。
- 1000 QPS：42,744/42,744 成功，达成 711.83 QPS，P50 44.02 ms（**延迟拐点在 (500,1000]**）。
- 2000 QPS：42,850/42,849 成功，达成 713.61 QPS，1 个 fetch-failed。
- 瓶颈：OpenViking（CPU 71–72%）而非 Guard（4.3–4.5%）。
- **独立 E7 佐证**：该 run 130,595 个业务请求期间新增 Node Attestation = 0、新增 Trustee 请求 = 0。

### E6 新建连接探针（修复前）`run-20260810T122020Z-e6nc`

- 18,901 请求 / 4,695 成功 / **14,206 失败（`socket hang up`）**。
- 失败自 t≈469.5 s 开始，与启动时加载 SVID 的 `not_after`（t≈467.8 s）吻合 —— **load-generator 凭据缓存缺陷**：每次新建连接都携带过期客户端证书被对端拒绝。
- 佐证：容器内 `/run/argus-svid/` 正常刷新（serial 每 ~280 s 轮换）、Guard 持续 ALLOW（deny=0）、OpenViking 容器持续存活。

### E6 新建连接探针（修复后复测）`run-20260810T122020Z-e6nc-fixed`

- **18,901 / 18,901 成功（100%），0 失败**；P50 / P95 / P99 = 7.36 / 8.63 / 9.41 ms（max 86.50）。
- 全部 `reused_connection=false`（每条都是新建 mTLS 连接）；握手 P50 / P95 = 3.11 / 3.57 ms。
- OpenViking SVID 轮换 6（OpenClaw serial 变化 7）；7 个轮换窗口内 17,875 个请求 **0 失败**。
- 新增 Node Attestation = 0、新增 Trustee 请求 = 0。

## 秘密扫描

清单生成器对每个文本文件做模式扫描（`sk-*` API key、`apiKey` 字段、`BEGIN …PRIVATE KEY`、
`BEGIN CERTIFICATE`、`Bearer …`、AWS `AKIA…`），命中即**拒绝**生成清单。当前证据目录扫描结果：**0 命中**。

## 结论边界

- 结果仅对 Mock Evidence Provider + Mock Trustee 软件链路成立。
- 真实 Quote/QGS、production Trustee、多 Agent、生产容量均不在本报告范围。
