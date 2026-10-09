# E3/E5 result boundary

The exported online result is the exact paper02 Full two-request closure:

- sessions GET: HTTP 200, request enter/end, COMPLETE coverage
- search POST: HTTP 200 with non-empty tenant-memory response, positive
  69-byte application body read, request end, COMPLETE coverage
- live client/server SPIFFE identities and least-privilege business
  authorization

The six matching receiver records are exported as a canonicalized derived
excerpt. The unchanged shared source journal remains protected; its size and
SHA-256 are in `receiver-request-excerpt-context.json`.

No separate formal E3 renewal/Quote-count series or E5 multi-round
load/resource series was completed. Those metrics are `NOT_RUN`; the two
business requests and E4 receiver counts must not be relabeled as those
experiments.
