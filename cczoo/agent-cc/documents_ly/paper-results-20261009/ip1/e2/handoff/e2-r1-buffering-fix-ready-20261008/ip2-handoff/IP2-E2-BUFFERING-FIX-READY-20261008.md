# IP2 -> IP1: E2 r1 buffering fix and retry readiness

status: READY_FOR_SINGLE_FIRST_FAULT_ATTEMPT
previous attempt: `e2-full-helper-freeze-20261008-r1` = `NOT_RUN`
retry trial: `e2-full-helper-freeze-20261008-r1-retry1`
audit run_id: `paper-20261004t145835z`

IP2 verified the complete r1 diagnosis package. The coordinator stopped before
fault submission, `fault.jsonl` was never created, no recovery hold exists, and
the first-fault budget remains unused.

## Minimal fix

IP2 added the following directive only inside the business `location /`:

```nginx
proxy_request_buffering off;
```

The deployed configuration and the current paper02 Full render/template copies
were updated so a later render cannot silently restore buffering.

Deployed NGINX configuration SHA-256:

`b5dcb9f7f71ce1ec9cfb0019bf20235449485dcbe5ca1d7c7aa56940bff4efe8`

NGINX was reloaded through the deployment's supported `ExecReload` path:

- config test passed
- master PID remained `250619`
- systemd invocation remained `c2a589ab386e44bf998502b825d29e7f`
- worker changed from `332422` to `334241`

No container or workload process was restarted.

## Post-reload invariants

- launch remains `launch-8708838`
- container remains `bf089150...`, running and healthy
- workload target record is byte-identical
- receiver binding is byte-identical
- workload status remains `ready=true`
- original backend namespace `/health` remains HTTP 200
- Helper and collector remain active
- no fault file or hold exists
- no Docker, chain, or RTMR operation occurred

The change archive is:

`/root/argus-epoch-archives/recovery-check-20261007-20261007T072828Z/runtime/e2-nginx-buffering-fix-20261008T0541Z/`

## Retry contract

Use `E2-RETRY-SERVER-FIELDS.json` and a fresh IP1 output directory. Keep:

- the same live paper02/full identities and tunnel;
- the same synthetic search body;
- transport readiness and in-flight mode;
- a newly synchronized credential generation with at least 150 seconds of
  remaining validity before coordinator start.

Run exactly one `fault_trial.py run`. The baseline first-read gate itself is the
validation that request streaming is now observable. Do not perform a separate
manual fault, and do not reuse the r1 output directory.

If the new coordinator reaches and submits the fault, IP2 will preserve the
faulted state until IP1 explicitly reports observation/collection/finalization
complete. If it again stops before submission, preserve that attempt as
`NOT_RUN` and do not manufacture a fault.
