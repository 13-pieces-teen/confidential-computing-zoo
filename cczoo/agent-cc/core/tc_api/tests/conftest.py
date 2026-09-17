"""Isolated host metadata for launch-profile tests run by ordinary CI users."""
import os
from pathlib import Path

import pytest


@pytest.fixture
def root_owned_config(monkeypatch):
    """Substitute only the owner of explicitly registered temporary files.

    Production still requires root ownership; mode, size and file kind remain
    real, and separate negative cases exercise the ownership rejection.
    """
    paths = set()
    original = Path.lstat

    def lstat(path):
        info = original(path)
        if path in paths:
            fields = list(info)
            fields[4] = 0  # stat_result.st_uid
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    return paths.add
