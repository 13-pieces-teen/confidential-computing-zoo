# E2 r4 receiver windows

This directory exports the necessary fault/recovery receiver windows without
repeating the injection and without publishing the 122 MB shared receiver.

- `receiver/experiment-window.jsonl` is the contiguous source line range
  162130-163242. It preserves every request event, coverage interval,
  watermark, `source_seq`, and the terminal `receiver_stop` in that range.
- The final experiment coverage interval is intentionally retained as
  `UNKNOWN / uncovered_final_or_crash_tail`; the crop is not relabeled
  COMPLETE.
- `receiver/recovery-window.jsonl` is the contiguous source line range
  17580-17620 around the sessions/search recovery probes. Exact source start,
  source stop, and collector finalize records are also provided.
- `originals/timeline.json` retains the 20 ms wall-clock uncertainty. Same-host
  ordering is recomputed from monotonic timestamps; no synthetic clock offset
  is applied.

Recompute locally:

```bash
python3 verify_e2_r4_windows.py
```

The script validates crop hashes, record-sequence continuity, source-sequence
monotonicity, target request lifecycles, surrounding watermarks, and the
preserved UNKNOWN final tail.
