# Argus 双机论文材料：controlled-v2

本版采用**受控原型实验＋已有真实TDX集成验证**，取代fa615b1c的新TDX环境等待前提。旧现场继续冻结，停止仅等待管理员的循环，普通Linux实验可以开始准备。

## 入口
1. 按GIT-GET.md取得用户提供的固定提交，只导出本目录并核验SHA256SUMS。
2. 阅读EXPERIMENT-PLAN.md、BACKEND-CONTRACT.md及各自PROMPT-IP1.md/PROMPT-IP2.md。
3. IP2提供最小受控后端，IP1准备隔离身份/观测；12b365ed适配核验后复用。
4. 跑S1软件闭环，再跑E1→E5/E3→E2→E4，沿用收敛轮次。
5. IP2合并数据，IP1核对客户端；P0/A3单独归档。

## 材料
- EXPERIMENT-PLAN.md：范围、阶段、数量及分工。
- BACKEND-CONTRACT.md：需实现的接入点与检查。
- PROMPT-IP1.md / PROMPT-IP2.md：角色执行指令。
- MANUSCRIPT-METHODS.md：正文方法建议，尚未写回飞书。
- TDX-ARCHIVE-INDEX.json：实机证据索引模板，待双机核验。
- figures/results.json：v2空槽位；data-contract.md为指标规则。
- figures/argus-experiment-templates.pdf：两表两图及附录两表。
- SHA256SUMS：导出后的文件摘要。

本提交只交方案、图表与数据校验，没有实现/部署远端后端或宣称S1通过。实际适配由IP2在最新代码上完成。默认实机配置继续拒绝实验材料。主统计拒绝混入硬件归档，所有实测数值留空。

## 离线生成
在材料目录外保存结果工作副本；非pending记录标明环境、版本与原件。先执行：
```bash
python3 figures/validate_results.py /absolute/path/to/results.json
```
绘图使用独立分析环境的requirements-render.txt：
```bash
python3 figures/render.py --data /absolute/path/to/results.json --out /absolute/path/to/rendered
```
可用--table-python指定另一Python。不要在正式测量期间占用被测机绘图。Node/Workload计数为实验后端请求，不是硬件Quote。
