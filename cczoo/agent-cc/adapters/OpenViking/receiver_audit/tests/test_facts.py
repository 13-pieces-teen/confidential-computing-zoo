"""Synthetic protocol and passive ASGI boundaries; no remote acceptance claim."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from receiver_audit.facts import FactMatcher, FRAME_BYTES
from receiver_audit.middleware import ReceiverAudit


def frame():
    prefix = "ARGUS_FACT_V1|id=" + "1" * 32 + "|project=" + "2" * 12 + "|chain=" + "3" * 12 + "|ref=" + "4" * 32 + "|amount=00000123"
    return (prefix + "|sha256=" + hashlib.sha256(prefix.encode()).hexdigest() + "|END_ARGUS_FACT").encode()


class FactTests(unittest.TestCase):
    def test_every_split_and_byte_chunks(self):
        raw = frame()
        self.assertEqual(len(raw), FRAME_BYTES)
        for split in range(len(raw) + 1):
            matcher = FactMatcher()
            found = matcher.feed(b"prefix" + raw[:split]) + matcher.feed(raw[split:] + b"suffix")
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["start_offset"], 6)
            self.assertEqual(found[0]["full_fact_sha256"], hashlib.sha256(raw).hexdigest())
        matcher = FactMatcher()
        found = [row for byte in raw for row in matcher.feed(bytes([byte]))]
        self.assertEqual(len(found), 1)
        self.assertLess(len(matcher.tail), FRAME_BYTES)

    def test_partial_mutated_and_separate_requests_do_not_match(self):
        raw = frame()
        self.assertEqual(FactMatcher().feed(raw[:-1]), [])
        self.assertEqual(FactMatcher().feed(raw.replace(b"00000123", b"00000124")), [])
        self.assertEqual(FactMatcher().feed(raw[:100]), [])
        self.assertEqual(FactMatcher().feed(raw[100:]), [])
        self.assertEqual(len(FactMatcher().feed(raw + b" " + raw)), 2)

    def test_partial_candidate_reports_its_read_bytes_not_a_complete_fact(self):
        raw = frame()
        matcher = FactMatcher()
        matcher.feed(b"header" + raw[:100])
        self.assertEqual(matcher.partial()["candidate_frame_bytes"], 100)
        self.assertEqual(matcher.partial()["fact_id"], "1" * 32)
        matcher.feed(raw[100:])
        self.assertIsNone(matcher.partial())

    def test_middleware_emits_only_complete_hash_after_read_and_returns_original(self):
        class Telemetry:
            def __init__(self): self.rows = []
            def start(self): pass
            def emit(self, op, **fields): self.rows.append(dict(op=op, **fields))
        telemetry = Telemetry()
        raw = frame()
        messages = [{"type": "http.request", "body": raw[:80], "more_body": True},
                    {"type": "http.request", "body": raw[80:], "more_body": False}]
        delivered = []
        async def receive(): return messages[len(delivered)]
        async def app(scope, receive, send):
            delivered.append(await receive()); delivered.append(await receive())
        scope = {"type": "http", "headers": [(b"x-argus-run-id", b"run"), (b"x-argus-request-id", b"request"),
                  (b"x-argus-task-id", b"s00"), (b"x-argus-client-id", b"alice")]}
        asyncio.run(ReceiverAudit(app, "/unused", "run", telemetry=telemetry, synthetic_facts=True)(scope, receive, None))
        self.assertEqual(delivered, messages)
        observed = [r for r in telemetry.rows if r["op"] == "fact_observed"]
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["completion_chunk"], 2)
        self.assertEqual(observed[0]["task_id"], "s00")
        self.assertNotIn("00000123", json.dumps(telemetry.rows))
        self.assertTrue(telemetry.fact_matching)


if __name__ == "__main__": unittest.main()
