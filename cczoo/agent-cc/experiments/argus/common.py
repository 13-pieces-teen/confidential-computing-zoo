"""Shared experiment storage. No credentials or request bodies belong in records."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import subprocess
import threading

GROUPS = ("full_argus", "native_spire_guarded", "no_watchdog", "no_close", "static_mtls")
SAFE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}\Z")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def append(path, row):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextlib.contextmanager
def measurement_log(path):
    """Buffered observations; only a normal close seals the measurement file.

    Submission intent journals must continue using their durable writers.
    A crashed measurement has no complete result to analyze.
    """
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


def write_measurement(stream, row):
    stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")


def sanitize_diagnostic(value, secrets=()):
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    for secret in sorted((str(s) for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(\b(?:authorization|x-api-key|api[_-]?key|access[_-]?token|token|password|client[_-]?secret)\b[\s\"']*[:=][\s\"']*)([^\r\n,}]+)", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", text)
    return text[-16384:]


def run_logged(argv, *, diagnostic, stage, timeout=None, cwd=None, env=None, secrets=(), stdout=subprocess.DEVNULL):
    """Run an existing tool; retain a bounded stderr tail, never argv/env/body.

    PIPE stdout is opt-in for callers downloading declared evidence files. It
    is returned to that caller and never copied into the diagnostic record.
    """
    stderr_tail, stdout_parts = bytearray(), []
    error, process, threads = None, None, []
    def drain(stream, tail):
        try:
            while True:
                data = stream.read(4096)
                if not data:
                    break
                if tail:
                    stderr_tail.extend(data)
                    del stderr_tail[:-16384]
                else:
                    stdout_parts.append(data)
        finally:
            stream.close()
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=subprocess.PIPE)
        for stream, tail in ((process.stderr, True), (process.stdout, False)):
            if stream is not None:
                thread = threading.Thread(target=drain, args=(stream, tail), daemon=True)
                thread.start(); threads.append(thread)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            raise
        for thread in threads:
            thread.join(timeout=1)
        require(not any(t.is_alive() for t in threads), "child output pipes did not close")
        return subprocess.CompletedProcess(argv, process.returncode,
                                           b"".join(stdout_parts) if stdout == subprocess.PIPE else None)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        error = type(exc).__name__
        raise
    finally:
        for thread in threads:
            thread.join(timeout=1)
        atomic(diagnostic, {"stage": stage, "exit_code": process.returncode if process else None,
                            "error_class": error, "stderr_tail": sanitize_diagnostic(bytes(stderr_tail), secrets),
                            "stderr_limit_bytes": 16384})


@contextlib.contextmanager
def lock(directory):
    """OS-held lock: a crashed process releases it, a stale inode does not block resume."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / ".operation.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            os.write(fd, b"0")
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def protected_secret(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "secret reference must be a regular protected file")
    if os.name == "posix":
        require(path.stat().st_mode & 0o077 == 0, "secret file must not be group/world accessible")
    value = path.read_text(encoding="utf-8").strip()
    require(value and "\n" not in value and "\x00" not in value, "invalid secret file")
    return value


def resolve(base, name):
    path = Path(name)
    return path.resolve() if path.is_absolute() else (Path(base) / path).resolve()
