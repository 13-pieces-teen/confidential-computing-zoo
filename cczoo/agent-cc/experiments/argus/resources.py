#!/usr/bin/env python3
"""Linux /proc process resource sampler; PID reuse ends a series, never merges it."""
import argparse
import os
from pathlib import Path
import time
from common import measurement_log, write_measurement, atomic, sha, require


def sample(pid, proc=Path("/proc")):
    fields = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start_ticks": int(fields[19]), "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
            "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"), "at_ms": time.time_ns() // 1000000}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pid", type=int, action="append", required=True); p.add_argument("--run-id", required=True)
    p.add_argument("--duration", type=float, default=150); p.add_argument("--interval", type=float, default=1)
    p.add_argument("--output", required=True)
    a = p.parse_args(); require(a.duration > 0 and a.interval > 0 and os.name == "posix", "Linux and positive intervals required")
    require(not Path(a.output).exists(), "fresh resource output required")
    starts, stopped = {}, set()
    end = time.monotonic() + a.duration
    with measurement_log(a.output) as stream:
        while time.monotonic() < end:
            for pid in a.pid:
                if pid in stopped: continue
                try:
                    row = sample(pid)
                    starts.setdefault(pid, row["start_ticks"])
                    require(starts[pid] == row["start_ticks"], "PID reused")
                except (OSError, ValueError, IndexError) as exc:
                    row = {"pid": pid, "at_ms": time.time_ns() // 1000000, "result": "UNKNOWN", "error_class": type(exc).__name__}
                    stopped.add(pid)
                write_measurement(stream, dict(row, run_id=a.run_id))
            time.sleep(min(a.interval, max(0, end - time.monotonic())))
    atomic(str(a.output) + ".complete.json", {"run_id": a.run_id, "complete": True, "sha256": sha(a.output)})


if __name__ == "__main__": main()
