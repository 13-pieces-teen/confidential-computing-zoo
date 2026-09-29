"""Paper figures from measured continuous-task metadata; no synthetic timings."""
from common import sha


def render(output, analysis, plt):
    files = []
    def save(fig, name):
        for suffix in ("svg", "pdf"):
            path = output / "figures" / (name + "." + suffix)
            fig.savefig(path)
            files.append({"path": str(path.relative_to(output)), "sha256": sha(path)})
        plt.close(fig)
    runs = analysis.get("continuous_results", [])
    if not runs: return files
    fig, ax = plt.subplots(figsize=(9, max(3, .4 * len(runs))), layout="constrained")
    labels = [r["run_id"] for r in runs]
    left = [0] * len(runs)
    def unresolved(run, category):
        if not run["steps"]:
            return run["planned_tasks"] if category == ("NOT_RUN" if run["axes"]["task"]["NOT_RUN"] else "UNKNOWN") else 0
        if category == "UNKNOWN":
            return sum("UNKNOWN" in (s["task_result"], s["receipt_result"]) for s in run["steps"])
        return sum("UNKNOWN" not in (s["task_result"], s["receipt_result"])
                   and "NOT_RUN" in (s["task_result"], s["receipt_result"]) for s in run["steps"])
    for cell, color in (("PASS/PASS", "#26834b"), ("PASS/FAIL", "#5c9dc6"),
                        ("FAIL/PASS", "#d98235"), ("FAIL/FAIL", "#b63838"),
                        ("UNKNOWN", "#c28b24"), ("NOT_RUN", "#999999")):
        values = [unresolved(r, cell) if cell in ("UNKNOWN", "NOT_RUN")
                  else r["joint_receipt_task"][cell] for r in runs]
        ax.barh(labels, values, left=left, label=cell, color=color)
        left = [a + b for a, b in zip(left, values)]
    ax.set(xlabel="All planned tasks", title="Receipt criterion / task result (unknown axes remain separate in tables)")
    ax.legend(fontsize=7, ncol=3); save(fig, "continuous-joint-outcomes")
    for run in runs:
        origin = run.get("started_at_ms")
        if not isinstance(origin, (int, float)): continue
        steps = run["steps"]
        fig, ax = plt.subplots(figsize=(10, max(4, len(steps) * .16)), layout="constrained")
        colors = {"PASS": "#26834b", "FAIL": "#b63838", "UNKNOWN": "#c28b24", "NOT_RUN": "#999999"}
        for y, step in enumerate(steps):
            planned, end = step.get("planned_at_ms"), step.get("completed_at_ms")
            if not isinstance(planned, (int, float)): continue
            start = step.get("started_at_ms")
            ax.scatter((planned-origin)/1000, y, marker="|", color="black", s=22)
            if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                ax.plot([(start-origin)/1000, (end-origin)/1000], [y, y], linewidth=3, color=colors[step["task_result"]])
            deadline = step.get("deadline_at_ms")
            if isinstance(deadline, (int, float)):
                ax.scatter((deadline-origin)/1000, y, marker="x", color="#888888", s=10)
        ax.set(yticks=range(len(steps)), yticklabels=[s["client_id"] + "/" + s["step_id"] for s in steps],
               xlabel="Seconds since fixed schedule origin (tick = release; cross = deadline)", title=run["run_id"])
        ax.tick_params(axis="y", labelsize=6); save(fig, run["run_id"] + "-task-timeline")
        # Draw only milestones collected by the receiver/lifecycle code. Missing
        # phases stay absent; command success never supplies an admission time.
        recovery = run.get("receiver", {}).get("recovery", run.get("recovery", {}))
        milestones = recovery.get("milestones", []) if isinstance(recovery, dict) else []
        anchor = recovery.get("anchor_at_ms") if isinstance(recovery, dict) else None
        times = [(m.get("stage"), m.get("at_ms")) for m in milestones if isinstance(m.get("at_ms"), (int, float))]
        if times and isinstance(anchor, (int, float)):
            fig, ax = plt.subplots(figsize=(8, 3.5), layout="constrained")
            ax.scatter([(t-anchor)/1000 for _, t in times], range(len(times)))
            for i, (stage, _) in enumerate(times):
                interval = next(m.get("elapsed_from_recovery_command_ms") for m in milestones if m.get("stage") == stage)
                if interval:
                    ax.plot([interval["lower_ms"]/1000, interval["upper_ms"]/1000], [i, i])
            ax.set(yticks=range(len(times)), yticklabels=[s for s, _ in times], xlabel="Seconds since actual recovery command (clock intervals shown)",
                   title="Observed recovery milestones: " + run["run_id"])
            save(fig, run["run_id"] + "-recovery")
    contrasts = [c for c in analysis.get("paired_client_fault_minus_control", [])
                 if c["metric"] == "continuous_task_success_rate" and c.get("n_runs", 0)]
    if contrasts:
        fig, ax = plt.subplots(figsize=(8, max(3, .5*len(contrasts))), layout="constrained")
        for i, c in enumerate(contrasts):
            points = c.get("paired_blocks", [])
            if points:
                ax.scatter([p["difference"] for p in points], [i]*len(points), alpha=.5, s=15)
            ax.scatter(c["mean"], i, marker="D")
            if c.get("low") is not None: ax.plot([c["low"], c["high"]], [i, i])
        ax.axvline(0, color="#777777", linewidth=.7)
        ax.set(yticks=range(len(contrasts)), yticklabels=[c["group"]+" / "+c["client_id"]+" n="+str(c["n_runs"]) for c in contrasts],
               xlabel="Fault minus matched no-fault task success rate", title="Per-client changes (shared faults affect all dependent clients)")
        save(fig, "continuous-client-effects")
    losses = [c for c in analysis.get("paired_uninjected_completion_loss_pp", []) if c.get("n_runs", 0)]
    if losses:
        fig, ax = plt.subplots(figsize=(8, max(3, .5*len(losses))), layout="constrained")
        for i, c in enumerate(losses):
            ax.scatter([p["difference"] for p in c["paired_blocks"]], [i]*len(c["paired_blocks"]), alpha=.5, s=15)
            ax.scatter(c["mean"], i, marker="D")
            if c.get("low") is not None: ax.plot([c["low"], c["high"]], [i, i])
        ax.axvline(0, color="#777777", linewidth=.7)
        ax.set(yticks=range(len(losses)), yticklabels=[c["group"]+" / "+c["client_id"] for c in losses],
               xlabel="Completion loss: no-fault minus fault (percentage points)",
               title="Predeclared uninjected clients in local-fault trials")
        save(fig, "continuous-uninjected-loss")
    return files
