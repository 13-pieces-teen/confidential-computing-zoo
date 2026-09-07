#!/usr/bin/env python3
"""Opt-in destructive company checks; records observed stop/rotation outcomes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import workload


def main():
    p = argparse.ArgumentParser()
    p.add_argument("event", choices=["helper-crash", "target-exit", "rotation", "wrong-client"])
    p.add_argument("--config", default="/etc/argus-workload/environment.json")
    p.add_argument("--wrong-client-cert")
    p.add_argument("--wrong-client-key")
    args = p.parse_args()
    c = json.loads(workload.protected_file(args.config).read_text())
    before = workload.verify(c)
    serial = before["svid_and_business"]["server_serial"]
    if args.event == "wrong-client":
        if not args.wrong_client_cert or not args.wrong_client_key:
            raise ValueError("supply a valid same-domain SVID with a different SPIFFE ID")
        cmd = [workload.BIN / "spiffe-mtls-probe", "-url", c["business_url"], "-cert", args.wrong_client_cert,
               "-key", args.wrong_client_key, "-bundle", c["client_bundle"]]
        r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 or "business HTTP 403" not in r.stderr:
            raise ValueError("expected AuthZ HTTP 403 for a valid wrong-identity client SVID")
        result = {"event": args.event, "result": "PASS", "observed": "HTTP_403"}
    elif args.event == "rotation":
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            current = workload.status(c)
            if not current["ready"]:
                raise ValueError("service lost readiness during normal rotation")
            if current["target_serial"] != serial:
                after = workload.verify(c)
                if after["appraisal"] != before["appraisal"]:
                    raise ValueError("this was a new attestation/subscription, not ordinary certificate rotation")
                result = {"event": args.event, "result": "PASS", "previous_serial": serial,
                          "current_serial": current["target_serial"], "new_attestation": False}
                break
            time.sleep(1)
        else:
            raise TimeoutError("no target certificate rotation in 360s")
    else:
        target = None
        if args.event == "target-exit":
            target = json.loads(workload.run([workload.BIN / "argus-workload", "-action", "check"]))
        # Include the trigger command's latency; starting the clock after it
        # returns would under-report the observed shutdown interval.
        start = time.monotonic()
        if args.event == "helper-crash":
            workload.run(["systemctl", "kill", "--kill-who=main", "--signal=SIGKILL", "argus-helper"])
        else:
            workload.run(["docker", "kill", target["container_id"]])
        while time.monotonic() - start < 6:
            state = workload.run(["systemctl", "is-active", "argus-nginx"], check=False)
            if state in ("inactive", "failed") and not Path("/run/argus-credentials/ready").exists():
                if any(Path("/run/argus-credentials").rglob("*.pem")):
                    time.sleep(0.05)
                    continue
                elapsed = time.monotonic() - start
                result = {"event": args.event, "result": "PASS", "stop_observed_seconds": round(elapsed, 3),
                          "readiness_removed": True, "target_pem_removed": True, "nginx_state": state}
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("NGINX/readiness not removed within stop timeout plus observation allowance")
        # Leave the deliberately disrupted test stopped for explicit re-registration.
        workload.stop(c)
    workload.write_json(workload.RECORDS / ("lifecycle-" + args.event + ".json"), result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(f"LIFECYCLE=FAIL: {error}", file=sys.stderr)
        sys.exit(1)
