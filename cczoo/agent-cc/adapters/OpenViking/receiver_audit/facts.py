"""Bounded streaming recognition of synthetic experiment facts, never secrets.

No expected facts are installed on the receiver. A recognized complete frame is
checked against the client's withheld manifest only by the offline assessor.
"""
import hashlib
import re

FRAME = re.compile(rb"ARGUS_FACT_V1\|id=([0-9a-f]{32})\|project=[0-9a-f]{12}\|chain=[0-9a-f]{12}\|ref=[0-9a-f]{32}\|amount=[0-9]{8}\|sha256=([0-9a-f]{64})\|END_ARGUS_FACT")
FRAME_BYTES = len(b"ARGUS_FACT_V1|id=" + b"0" * 32 + b"|project=" + b"0" * 12 + b"|chain=" + b"0" * 12 + b"|ref=" + b"0" * 32 + b"|amount=" + b"0" * 8 + b"|sha256=" + b"0" * 64 + b"|END_ARGUS_FACT")


class FactMatcher:
    def __init__(self):
        self.tail = b""
        self.total = 0

    def feed(self, body):
        data, base = self.tail + body, self.total - len(self.tail)
        matches = []
        for match in FRAME.finditer(data):
            frame = match.group()
            if base + match.end() <= self.total:
                continue
            prefix = frame.split(b"|sha256=", 1)[0]
            if hashlib.sha256(prefix).hexdigest().encode() != match.group(2):
                continue
            matches.append({"fact_id": match.group(1).decode(),
                            "full_fact_sha256": hashlib.sha256(frame).hexdigest(),
                            "fact_bytes": len(frame), "start_offset": base + match.start(),
                            "end_offset": base + match.end()})
        self.total += len(body)
        self.tail = data[-(FRAME_BYTES - 1):]
        return matches

    def partial(self):
        """A recognizable truncated prefix is a candidate, not a complete fact."""
        offset = self.tail.rfind(b"ARGUS_FACT_V1|id=")
        if offset < 0:
            return None
        candidate = self.tail[offset:]
        if len(candidate) >= FRAME_BYTES:
            return None
        identity = re.match(rb"ARGUS_FACT_V1\|id=([0-9a-f]{32})(?:\||$)", candidate)
        return {"fact_id": identity.group(1).decode() if identity else None,
                "candidate_frame_bytes": len(candidate), "start_offset": self.total - len(self.tail) + offset,
                "end_offset": self.total}
