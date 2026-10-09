# E1 continuation — USER APPROVAL GRANTED

Approved at (IP1 clock): 2026-10-04T05:19:24Z
Approved by: E1 experiment owner (user decision relayed from the IP1
session; this file is the durable approval record for IP2).
References: E1-BC-CONTINUATION-DECISION.md (sha 1be5c6d3...) and IP2's
NEW-ISOLATED-ENVIRONMENT-PROPOSAL.md.

The user approves, within existing available resources, one additional
isolated IP2 TDX guest trust environment. The old guest and ALL preserved
evidence (P0, A3, unknown-stop, diagnosis) stay intact. This authorization
covers environment preparation, normal registration/admission, required
self-verification, and the Full/native E1 pre-experiment closure. No further
per-step confirmation is required from the user.

## I. Environment boundaries

1. New guest uses an independent database, chain initialization and owner
   material. Do NOT clone the old guest's identity, agent data, database or
   owner private key; do NOT wipe the old guest's database to fake a new
   environment.
2. Reuse already-verified software, images, deployment templates and
   non-secret configuration. No unrelated development; no model change.
3. IP1 control plane and the existing IP1 client guest continue to be
   reused. The new guest gets distinguishable Node/experiment identifiers
   and exact Entries. Old identities and their evidence mappings are
   preserved. Selectors are NOT broadened; policies are NOT relaxed.
4. Both groups are locked to actual running versions including the
   5e01780a fix; record code, build, configuration and policy digests. New
   measurement baselines go through the normal policy flow — do not copy the
   old guest's state directly.

## II. IP2 responsibilities

1. Create the environment, complete required self-verification, and deliver
   directly to IP1: actual endpoints, Node/target metadata and the exact
   Entry contract.
2. Verify the fix actually covers this stop-response-loss case. Prefer
   reusing the existing real regression artifacts; complete any missing
   minimal verification BEFORE barrier injection. "Process healthy" alone
   does not certify the fix.
3. Organize the new environment's normal admission plus business
   self-verification directly as Full A. Originals meeting E1 requirements
   suffice; do not run the same self-verification twice.
4. Coordinate with IP1 over the existing protected channel. After IP1
   sampling is ACTIVE, complete Full B->C within the same bounded window.
5. After the Full trajectory closes, switch to native A->B->C via the
   normal group switch. Keep the common local protection; report real
   results; do NOT pre-assume native must pass.
6. Deliver results and final environment state in one place. Do NOT enter
   formal repeat batches or E2-E5.

## III. IP1 responsibilities

1. On receiving new-environment metadata: complete exact Entries, control
   materials, routing, and live client identity verification for the group.
2. Start bounded sampling only after the experiment window is ready;
   re-measure clock skew — do not reuse the old offset or stale watcher
   receipts.
3. Continuously collect control-side and client-side originals and hand
   them back to IP2 promptly and directly, without waiting for the user to
   relay each stage.
4. Verify the new environment's Full A/B/C and native A/B/C separately;
   keep old P0/A3 and this batch's results distinct. The same
   deployment-self-verification and Full A originals count as ONE sample
   only.

## IV. Execution principles

- Ordinary preparation, verification, stage transitions and packaging
  proceed continuously; no per-step stops.
- B/C does not wait for human messages while the barrier is held.
- Failures, UNKNOWN, unreachable and gaps are all preserved. No loop
  reruns to force a PASS.
- If an unknown operation that cannot be safely handled occurs again: stop
  adding lifecycle actions, complete all independent diagnosis first, then
  submit root cause, impact and concrete decision options together. Do NOT
  clear fences, replay Docker, or keep rebuilding environments on our own.

## V. Single final report

Exactly ONE final report, verified consistent by both sides: new
environment information, fix verification, actual results of all six
Full/native stages, evidence index, current health state, and remaining
open items.
