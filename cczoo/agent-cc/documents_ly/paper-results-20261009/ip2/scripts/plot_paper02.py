#!/usr/bin/env python3
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "tables" / "FIGURE-DATA.json").read_text())
OUT = ROOT / "figures"
OUT.mkdir(exist_ok=True)


def save(fig, name):
    for suffix in ("svg", "pdf"):
        fig.savefig(OUT / f"{name}.{suffix}")
    plt.close(fig)


cost = DATA["e1_admission_cost_ms"]
fig, ax = plt.subplots(figsize=(7.2, 4.2), layout="constrained")
labels = ["History snapshot", "Quote", "Provider total", "Admission ready"]
values = [cost["history_snapshot"], cost["quote"], cost["provider_total"], cost["ready_elapsed"]]
ax.bar(labels, values, color=["#5c9dc6", "#d98235", "#26834b", "#6b55a3"])
ax.set(ylabel="Milliseconds", title="E1 real admission measured costs (single archived run)")
ax.tick_params(axis="x", rotation=18)
save(fig, "e1-admission-cost")

stages = DATA["e1_stage_elapsed_ms"]
fig, ax = plt.subplots(figsize=(7.2, 4.2), layout="constrained")
ax.plot(list(stages.values()), list(stages), marker="o")
ax.set(xlabel="Milliseconds from fresh-subscription attempt start",
       title="E1 real admission stage timeline")
ax.grid(axis="x", alpha=.25)
save(fig, "e1-admission-stages")

boundary = DATA["e2_boundary_ms"]
fig, ax = plt.subplots(figsize=(8, 4.5), layout="constrained")
rows = [
    ("Fault command complete", boundary["fault_command_complete"]),
    ("Entry inactive", boundary["entry_stop"]),
    ("Last application read", boundary["last_application_read"]),
    ("Readiness detection", boundary["readiness_detection"]),
    ("Backend last observed", boundary["backend_last_observed"])
]
for y, (label, interval) in enumerate(rows):
    lo, hi = interval
    ax.plot([lo / 1000, hi / 1000], [y, y], linewidth=5)
    ax.scatter([(lo + hi) / 2000], [y], marker="|", color="black")
ax.set(yticks=range(len(rows)), yticklabels=[r[0] for r in rows],
       xlabel="Seconds from fault command", title="E2 observed receiver and ingress boundaries")
ax.grid(axis="x", alpha=.25)
save(fig, "e2-receiver-boundaries")

runs = DATA["e4_runs"]
fig, ax = plt.subplots(figsize=(8.2, 4.5), layout="constrained")
for condition, marker, color in (("healthy", "o", "#26834b"), ("fault", "s", "#b63838")):
    selected = [r for r in runs if r["condition"] == condition]
    ax.scatter([r["seed"] for r in selected], [r["first_answer_s"] for r in selected],
               marker=marker, color=color, s=60, label=condition)
for seed in (101, 102, 103):
    pair = [r for r in runs if r["seed"] == seed]
    ax.plot([seed, seed], [min(r["first_answer_s"] for r in pair),
                          max(r["first_answer_s"] for r in pair)],
            color="#888888", linewidth=1)
ax.axhline(180, linestyle="--", color="#555555", label="deadline")
ax.set(xticks=[101, 102, 103], xlabel="Paired seed (n=3 pairs)",
       ylabel="First answer latency (s)",
       title="E4 first answer timing; every run ended FAIL5 / UNKNOWN1")
ax.legend()
save(fig, "e4-paired-first-answer")
