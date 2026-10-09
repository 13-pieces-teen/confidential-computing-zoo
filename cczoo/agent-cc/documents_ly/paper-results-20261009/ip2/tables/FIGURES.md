# Paper02 figure captions and data types

## `e1-admission-cost`

Measured costs from the archived real Full A3 fresh-subscription admission on
the original IP2 TDX guest. Bars are one observed run: history snapshot,
Quote generation, Provider total and end-to-end readiness. They are not
confidence intervals or controlled-v2 software-backend timings.

## `e1-admission-stages`

Observed stage timestamps relative to the real A3 attempt start:
current-target check, Provider evidence, evidence binding, signed remote
appraisal and final target. All stages were `ALLOW`.

## `e2-receiver-boundaries`

Observed E2 Helper-freeze boundaries relative to fault invocation. Horizontal
segments retain measured timing intervals for entry inactivity and readiness
detection. The original backend remained locally live after entry closure;
this does not assert uninterrupted memory correctness.

## `e4-paired-first-answer`

First-answer latency for the three paired seeds under fault and healthy
conditions. The dashed line is the frozen 180-second deadline. Points are six
runs forming three pairs; request counts and 2507 receiver intervals per run
are coverage, not independent samples. Every point's task result remained
`ANSWER_INCORRECT`, followed by five `CHECKPOINT_UNCONFIRMED` steps.

## Missing values

Online E1 negative cases are categorical `NOT_RUN`; other unavailable fields
remain `UNKNOWN`. They are not plotted as zero. Figures describe the actual
paper01/paper02 TDX environment and do not claim a deployed controlled-v2
software backend.
