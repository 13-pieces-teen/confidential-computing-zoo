#!/usr/bin/env python3
"""Print existing Workload journal events; never initiate or appraise attestation."""
import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
import sys


UNITS = ("argus-tdx-provider", "argus-workload-agent", "argus-helper")
STAGES = (
    ("workload subscription", "INSTANCE"),
    ("fresh workload TDX Quote generated", "QUOTE"),
    ("workload EAR accepted", "EAR"),
    ("target SVID published", "SVID"),
)
ERRORS = (
    "workload evidence failed", "collect evidence:", "evidence binding:",
    "Trustee appraisal:", "target changed during appraisal:",
    "target instance ended:", "target credentials unavailable or expired",
    "Broker subscription ended:", "Helper identity removed or expired",
    "registered target identity mismatch", "PID is not the registered instance",
    "Helper identity unavailable", "target changed:",
)
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def plain(value):
    """Keep each record on one terminal line, without terminal control sequences."""
    return "".join(c if c.isprintable() else " " for c in ANSI.sub("", value))


def format_event(record):
    if not isinstance(record, dict) or not isinstance(record.get("MESSAGE"), str):
        return None
    message = plain(record["MESSAGE"])
    unit = record.get("_SYSTEMD_UNIT") or record.get("UNIT")
    if not isinstance(unit, str) or unit not in {name + ".service" for name in UNITS}:
        return None
    # Failures take precedence over success-like substrings in error messages.
    if any(marker in message for marker in ERRORS):
        stage = "ERROR"
    elif str(record.get("PRIORITY")) in ("0", "1", "2", "3"):
        stage = "PROCESS-ERROR"
    else:
        stage = next((name for marker, name in STAGES if marker in message), None)
    if stage is None:
        return None
    try:
        seconds, micros = divmod(int(record["__REALTIME_TIMESTAMP"]), 1_000_000)
        stamp = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=micros)
        stamp = stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        stamp = "time-unavailable"
    if stage == "SVID":
        serial = re.search(r"\bserial=([0-9]{1,128})\b", message)
        if serial:
            message += f" serial_hex={int(serial[1]):X}"
    return f"{stamp} [{stage}] {unit}: {message}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="1 hour ago", help="journalctl time window (default: 1 hour ago)")
    parser.add_argument("--no-follow", action="store_true", help="print recorded events and exit")
    args = parser.parse_args(argv)
    command = ["journalctl", "--no-pager", "--output=json", "--since", args.since]
    for unit in UNITS:
        command.extend(("--unit", unit))
    if not args.no_follow:
        command.extend(("--follow", "--no-tail"))
    print("只读查看：先显示指定时间窗内的已有事件，再跟随新事件。" if not args.no_follow
          else "只读查看：显示指定时间窗内的已有事件。", flush=True)
    print("时间为原始 journal UTC 时间；SVID 发布/轮换不是新 Quote；无事件不代表认证通过。", flush=True)
    print("[QUOTE] 仅表示生成；[EAR] 表示校验接受；[SVID] 表示发布；错误保留原文。", flush=True)
    process = None
    count = 0
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            try:
                event = format_event(json.loads(line))
            except json.JSONDecodeError:
                print("[READER-ERROR] 无法解析一条 journal 记录；该记录未显示。", file=sys.stderr)
                continue
            if event:
                print(event, flush=True)
                count += 1
        code = process.wait()
        if code:
            print(f"[READER-ERROR] journalctl 退出码 {code}；请检查权限和时间参数。", file=sys.stderr)
        elif count == 0:
            print("[NO-EVENTS] 未找到匹配记录；可扩大 --since 时间窗。", flush=True)
        return code if code >= 0 else 1
    except FileNotFoundError:
        print("[READER-ERROR] 找不到 journalctl；请在 IP2 的 systemd 主机运行。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()


if __name__ == "__main__":
    sys.exit(main())
