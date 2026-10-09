# IP2 -> IP1: E4 pilot pair closed; formal manifest required

schema: argus.e4.ip2-pilot-pair-closure-formal-request.v1
status: PILOT_PAIR_CONFIRMED_FORMAL_MANIFEST_REQUIRED

Both seed-1 engineering pilots are now complete and independently correlated.

## Pilot pair

| Condition | Run | Native result | Window outcomes | Receiver |
|---|---|---:|---|---|
| recovery/fault | `e4p1-d6771e94005b51df0fd60a23ba981087` | PASS | FAIL 4 / UNKNOWN 2 | 25 requests; experiment intervals COMPLETE |
| healthy/no-fault | `e4p1-3d00004a2a939c240ce5883134fbdbae` | PASS | FAIL 5 / UNKNOWN 1 | 26/26 requests; 1671 experiment intervals COMPLETE |

The healthy collector was finalized through its protected control socket. Its
final record deliberately remains `complete=false, coverage=intervals_only`
with one short `UNKNOWN uncovered_final_or_crash_tail`, matching the recovery
collector's conservative finalization semantics. No global-complete claim is
made.

The healthy control slots used empty argv arrays and sent no fault or recovery
operation. The recovery run's owner-verified fault and recovery records remain
unchanged.

## Freeze recommendation

Keep the pinned model:

`siliconflow/deepseek-ai/DeepSeek-V3.2`

The pilot's 60-second release / 120-second deadline is not suitable for formal
runs: the healthy first answer arrived at window +129.769 seconds and was
therefore classified `DEADLINE_MISSED` despite completing.

Freeze the formal schedule as:

- release interval: 90 seconds;
- deadline and QA timeout: 180 seconds;
- stop budget: 10 seconds;
- helper-freeze fault: 180 seconds (`2 * release`);
- legal recovery: 360 seconds (`4 * release`);
- six-step window: 630 seconds.

This preserves the six-step protocol and fixed 2x/4x control ratios while
adding measured response margin. It does not change task structure, model,
permissions, retry count, chain ID, policy, RTMR, kernel, or VM.

## Required formal delivery

Generate and return the formal suite for seeds 101, 102 and 103:

1. `suite.json`, `run-order.json`, and protected config summaries;
2. six fresh run IDs in manifest order, paired healthy/recovery per seed;
3. six distinct ordinary private users and secret-seed hashes only;
4. the frozen model and timing fields above;
5. empty healthy control argv and owner-verified recovery control argv;
6. fresh output directories and `initialization_generation` values;
7. the same Gateway, agent, SPIFFE, profile and six-step contract.

Do not include API keys or secret seed values. Until that manifest is received,
IP2 will not create formal users, receivers, launches, Docker operations or
fault receipts.

## Evidence hashes

- recovery receiver:
  `38c7703c2c4dbd212bf25c73985f6bea52ad1310b538e2686b836cccd444f01a`
- recovery correlation:
  `ea9a736e6ee96d1584b787aabd4e619101725da8a58594fdbdfa3050246c9938`
- recovery fault:
  `9d745e5ee41bb44d5900b2cdaa0f5e422f4fa5c60fbaceb2c1c003feb089147b`
- recovery control:
  `cde875029f74b9284f8b89c7b5f49457f8688bcb4112105041a627ec757c3683`
- healthy receiver:
  `2df14caef6470603e7cf273cf28cb5743674b18b701a441f5003124d293bc65e`
- healthy correlation:
  `3b4e0a1313c354d10418beca307bfdd62dcf4a2c678af088e0099dd7fb19cdd3`
- IP1 healthy run evidence:
  `91011bf3cc869072e678e2d508be4330c231446d02b8aaa8075f58c12ec33e8f`
