"""Bounded synthetic E4 facts. These identifiers are observations, not credentials."""
import hashlib
import re

PATTERN = re.compile(
    r"ARGUS_FACT_V1\|id=([a-f0-9]{32})\|project=([a-f0-9]{12})"
    r"\|chain=([a-f0-9]{12})\|ref=([a-f0-9]{32})\|amount=([0-9]{8})"
    r"\|sha256=([a-f0-9]{64})\|END_ARGUS_FACT"
)


def encode_fact(fields):
    prefix = ("ARGUS_FACT_V1|id={fact_id}|project={project_id}|chain={chain_id}"
              "|ref={event_ref}|amount={amount_cents:08d}").format(**fields)
    frame = prefix + "|sha256=" + hashlib.sha256(prefix.encode("ascii")).hexdigest() + "|END_ARGUS_FACT"
    if parse_fact(frame) is None:
        raise ValueError("invalid synthetic fact fields")
    return frame


def parse_fact(frame):
    match = PATTERN.fullmatch(frame)
    if not match:
        return None
    prefix = frame.rsplit("|sha256=", 1)[0]
    if hashlib.sha256(prefix.encode("ascii")).hexdigest() != match[6]:
        return None
    return dict(zip(("fact_id", "project_id", "chain_id", "event_ref"), match.groups()[:4]),
                amount_cents=int(match[5]), full_fact_sha256=hashlib.sha256(frame.encode("ascii")).hexdigest(),
                fact_bytes=len(frame))


def facts_in(text):
    return [value for match in PATTERN.finditer(text) if (value := parse_fact(match[0])) is not None]
