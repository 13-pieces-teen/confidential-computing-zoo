# IP2 -> IP1: E2 r4 final closure

status: `CLOSED`
result: `PASS`
next phase: `E4`

IP2 verified the seven-file scientific result package and the recovery probe
package. E2 used exactly one executed fault:

- trial: `e2-full-helper-freeze-20261008-r4`
- event: `helper-freeze`
- fault checkpoint: `executed=true`
- traffic: PASS
- receiver delivery: PASS
- in-flight delivery: PASS
- client response delivery: PASS
- lifecycle observation: COMPLETE

All earlier r1/retry1/r3/window attempts remain immutable pre-fault
`NOT_RUN`/window accidents and did not consume the fault budget.

## Recovery

IP2 released both holds through their exact owner records, restored the normal
Helper configuration and NGINX reload path, and explicitly restarted the
existing Helper. The normal publication hook restored NGINX and 1943.

The recovered stack remains the same admitted target:

- launch `launch-8708838`
- container `bf089150f38ad2298a1bb88ac6161ec1ba2842156ce73b90c7c48923d43e533c`
- PID `248534`
- boot ID `c379b316-287e-4c6b-821b-63ae5a51c45e`

No launch, registration, container replacement, Docker mutation, chain, RTMR,
policy, or database change occurred.

## Recovery business correlation

IP1 used current live SPIFFE credentials, the tenant user key, the fixed run ID,
and fresh audit request IDs.

Sessions GET `91aed3cc-4eaa-4272-a1d9-fca7a9101c76`:

- HTTP 200
- record 17595: correlated request_enter
- record 17596: request_end
- records 17597/17598: COMPLETE contiguous-source coverage, watermark 100498

Search POST `cfe0f582-59dd-4574-9807-1d7ad501b7c7`:

- HTTP 200 with positive real tenant memory data
- record 17599: correlated request_enter
- record 17600: receive_pending
- record 17601: positive 69-byte body_read, `more_body=false`
- record 17604: request_end
- records 17605/17606: COMPLETE contiguous-source coverage, watermark 100504

All records bind through kernel process credentials and deployment evidence to
the original container/PID above.

The recovery collector was finalized normally. Its final output is:

`/var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/recovery-20261008T0854Z/receiver-recovery.jsonl`

SHA-256:

`9a859b90a8b25332ba1547c0cc854cfd2186b180a60a35ec2e7ac34096792525`

The generic final collector tail is marked UNKNOWN because shutdown occurs
between watermark intervals; both target requests independently have COMPLETE
coverage before that tail. The original finalized experiment receiver remains
unchanged at SHA-256
`142d0e1d5cca9f3ca7a357da6507437367c7e9ac6294e2d2c2cba4db677f2186`.

E2 is closed end-to-end. The campaign may proceed to E4 without rerunning
P0/A3 or creating another fault, launch, or Docker operation.
