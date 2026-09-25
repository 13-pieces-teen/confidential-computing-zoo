#!/usr/bin/env python3
"""Opt-in fault checks. Cleanup alone never proves that business traffic stopped."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import workload


def main():
    p = argparse.ArgumentParser()
    p.add_argument("event", choices=["helper-crash", "helper-freeze", "target-exit", "rotation", "wrong-client"])
    p.add_argument("--config", required=True)
    p.add_argument("--wrong-client-cert")
    p.add_argument("--wrong-client-key")
    p.add_argument("--execute-fault", action="store_true", help="explicitly execute the selected disruptive fault")
    p.add_argument("--stop-timeout", type=float, default=15, help="cleanup observation budget, not a security bound")
    args = p.parse_args()
    if args.event in ("helper-crash", "helper-freeze", "target-exit") and not args.execute_fault:
        p.error("disruptive events require --execute-fault; use remote_acceptance.py for independent traffic evidence")
    if not 1 <= args.stop_timeout <= 120:
        p.error("--stop-timeout must be in [1,120]")
    c = json.loads(workload.protected_file(args.config).read_text())
    d = workload.Deployment(c)
    before = workload.verify(c)
    serial = before["svid_and_business"]["server_serial"]
    if args.event == "wrong-client":
        if not args.wrong_client_cert or not args.wrong_client_key:
            raise ValueError("supply a valid same-domain SVID with a different SPIFFE ID")
        cmd = [d.bin / "spiffe-mtls-probe", "-url", c["business_url"], "-cert", args.wrong_client_cert,
               "-key", args.wrong_client_key, "-bundle", c["client_bundle"], "-server-id", d.identity["target_id"]]
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
            target = json.loads(workload.run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
        # Include the trigger command's latency; starting the clock after it
        # returns would under-report the observed shutdown interval.
        start = time.monotonic()
        if args.event == "helper-crash":
            workload.run(["systemctl", "kill", "--kill-who=main", "--signal=SIGKILL", d.unit("helper")])
        elif args.event == "helper-freeze":
            workload.run(["systemctl", "kill", "--kill-who=main", "--signal=SIGSTOP", d.unit("helper")])
        else:
            workload.run(["docker", "kill", target["container_id"]])
        # Observe unit/readiness/PEM cleanup. External command time contributes
        # to elapsed; this loop does not measure traffic on existing connections.
        while time.monotonic() - start < args.stop_timeout:
            state = workload.run(["systemctl", "is-active", d.unit("nginx")], check=False)
            if state in ("inactive", "failed") and not (d.credentials / "ready").exists():
                if any(d.credentials.rglob("*.pem")):
                    time.sleep(0.05)
                    continue
                elapsed = time.monotonic() - start
                result = {"event": args.event, "result": "NOT_RUN", "cleanup_result": "PASS",
                          "new_tls_traffic": "NOT_RUN", "existing_tls_traffic": "NOT_RUN", "receiver_delivery": "NOT_RUN",
                          "stop_observed_seconds": round(elapsed, 3),
                          "readiness_removed": True, "target_pem_removed": True, "nginx_state": state}
                break
            time.sleep(0.05)
        else:
            result = {"event": args.event, "result": "FAIL", "cleanup_result": "FAIL",
                      "new_tls_traffic": "NOT_RUN", "existing_tls_traffic": "NOT_RUN", "receiver_delivery": "NOT_RUN",
                      "reason": "cleanup not observed within the configured observation budget"}
        # Leave the deliberately disrupted test stopped for explicit re-registration.
        workload.stop(c)
    workload.write_json(d.records / ("lifecycle-" + args.event + ".json"), result)
    print(json.dumps(result, indent=2))
    if result["result"] == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(f"LIFECYCLE=FAIL: {error}", file=sys.stderr)
        sys.exit(1)
