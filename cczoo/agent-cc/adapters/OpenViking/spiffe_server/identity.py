"""Dependency-free helpers for exact SPIFFE peer identity validation."""

from __future__ import annotations

from typing import Iterable, Mapping


def is_exact_spiffe_identity(uris: Iterable[str], expected: str) -> bool:
    """Accept exactly one URI SAN and require it to equal the expected SPIFFE ID."""

    return list(uris) == [expected]


def certificate_uri_sans(certificate: Mapping[str, object] | None) -> list[str]:
    """Extract URI SANs from an already chain-validated stdlib peer certificate."""

    if not certificate:
        return []
    subject_alt_names = certificate.get("subjectAltName", ())
    if not isinstance(subject_alt_names, (tuple, list)):
        return []
    return [
        value
        for item in subject_alt_names
        if isinstance(item, tuple)
        and len(item) == 2
        and item[0] == "URI"
        and isinstance((value := item[1]), str)
    ]
