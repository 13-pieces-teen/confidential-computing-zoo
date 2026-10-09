# IP2 -> IP1: E5/E3 paper02 Full receiver closure

status: CONFIRMED
observed_at_utc: 2026-10-08T04:34Z
run_id: `paper-20261004t145835z`

IP1's minimal rerun handoff and `SHA256SUMS` verify successfully. The
independent receiver journal provides exact, source-bound correlation for both
fresh request IDs.

## Sessions GET

- request ID: `913ea143-89fa-4f64-b9ca-09f7158e1426`
- client result: HTTP 200, application status `ok`
- receiver records:
  - record 44031: `request_enter`, `correlated=true`
  - record 44032: `request_end`
- body read: not required for a bodyless GET

## Search POST

- request ID: `76037f47-0204-4775-b48d-d5b31b1dff91`
- client result: HTTP 200, 2129-byte response containing real tenant memories
- receiver records:
  - record 44035: `request_enter`, `correlated=true`
  - record 44036: receive pending
  - record 44037: positive `body_read`, 69 bytes, `more_body=false`
  - record 44038: `request_end`
- collector live lookup returns record 44037 as this request's `first_read`

## Provenance and coverage

Both requests are bound by kernel sender credentials and deployment records to:

- launch: `launch-8708838`
- container:
  `bf089150f38ad2298a1bb88ac6161ec1ba2842156ce73b90c7c48923d43e533c`
- host PID: `248534`
- boot:
  `c379b316-287e-4c6b-821b-63ae5a51c45e`
- receiver source: `062ee1d478ee46e6a9da7d7663e34666`
- provenance: `kernel_process_and_deployment`

The surrounding source watermarks are contiguous and the intervals covering
the requests are `COMPLETE`:

- GET source sequence: 22422-22423
- POST source sequence: 22425-22428
- coverage interval reason: `contiguous_source_watermarks`

The durable source is:

`/var/lib/argus-receiver/paper-20261004t145835z/receiver.jsonl`

## Closure

The paper02 Full real-business path is closed end to end:

- live paper02/full client SVID accepted through mTLS/AuthZ
- live paper02/full server SVID verified by IP1
- least-privilege `default/default:user` business authorization
- sessions and search HTTP 200
- real non-empty tenant memory result
- exact independent receiver correlation and positive search-body read

E5/E3 receiver correlation moves from `UNKNOWN` to `CONFIRMED`. No health,
launch, registration, P0/A3, Docker mutation, fault, chain, or RTMR operation
was rerun for this closure.
