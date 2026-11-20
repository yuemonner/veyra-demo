"""Runs inside the sandbox process. Not imported by the API.

argv[1] = the computation's own temporary folder, holding code.py and inputs.json. Writes result.json there.

Before the computation's code runs, an audit hook is installed that refuses network access, starting processes,
loading native code through ctypes, and writing or deleting files outside that folder. The hook cannot be removed
from Python code. It is a second layer: the operating-system sandbox (network namespace on Linux, sandbox-exec on
macOS) and the resource limits set by the parent are the first.
"""
import io
import json
import math
import os
import sys
import time
import traceback

WORK = os.path.realpath(sys.argv[1])
MAX_STDOUT = 4000

try:  # preloaded before the hook so their own internals can import what they need
    import numpy as np  # noqa: F401
except Exception:  # pragma: no cover - numpy is optional
    np = None
try:
    import statistics  # noqa: F401
except Exception:  # pragma: no cover
    statistics = None

with open(os.path.join(WORK, "inputs.json")) as handle:
    INPUTS = json.load(handle)
with open(os.path.join(WORK, "code.py")) as handle:
    CODE = handle.read()
if "pandas" in CODE or "pd." in CODE:
    try:
        import pandas as pd  # noqa: F401
    except Exception:
        pd = None
else:
    pd = None

BLOCKED_PREFIXES = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.fork", "os.posix_spawn", "os.kill", "os.killpg",
                    "ctypes.", "os.chdir", "os.chmod", "os.chown", "os.link", "os.symlink", "os.truncate", "shutil.", "webbrowser.",
                    "urllib.", "http.", "ftplib.", "smtplib.", "telnetlib.", "imaplib.", "poplib.", "nntplib.", "sqlite3.connect", "winreg.")
BLOCKED_MODULES = {"socket", "_socket", "subprocess", "_posixsubprocess", "ctypes", "_ctypes", "multiprocessing", "asyncio", "ssl", "_ssl",
                   "urllib.request", "http.client", "ftplib", "smtplib", "telnetlib", "pty", "fcntl", "resource", "signal", "mmap", "sqlite3"}
FILE_CHANGE = {"os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir", "os.mkdir", "os.makedirs", "os.utime"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


READ_ROOTS = tuple(sorted({os.path.realpath(p) for p in [WORK, sys.prefix, sys.base_prefix, sys.exec_prefix, *sys.path] if p and os.path.isabs(p)}
                          | {"/dev", "/proc/self", "/usr/share/zoneinfo", "/etc/localtime", "/System/Library", "/usr/lib", "/private/var/db/timezone"}))


def _readable(path) -> bool:
    try:
        real = os.path.realpath(os.fsdecode(path))
    except Exception:
        return False
    return any(real == root or real.startswith(root.rstrip(os.sep) + os.sep) for root in READ_ROOTS)


def _inside(path) -> bool:
    try:
        real = os.path.realpath(os.fsdecode(path) if not isinstance(path, int) else "")
    except Exception:
        return False
    return real == WORK or real.startswith(WORK + os.sep)


def _hook(event, args):
    if event.startswith(BLOCKED_PREFIXES):
        raise PermissionError(f"Not allowed in a Veyra computation: {event}")
    if event == "import" and args and (args[0] in BLOCKED_MODULES or str(args[0]).split(".")[0] in {"socket", "subprocess", "ctypes", "multiprocessing", "asyncio"}):
        raise PermissionError(f"Not allowed in a Veyra computation: import {args[0]}")
    if event in FILE_CHANGE and args and not _inside(args[0]):
        raise PermissionError(f"Not allowed in a Veyra computation: {event} outside its own folder")
    if event == "open" and args:
        path, mode, flags = (list(args) + [None, None, None])[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & WRITE_FLAGS)
        if writing and not isinstance(path, int) and not _inside(path):
            raise PermissionError("Not allowed in a Veyra computation: writing outside its own folder")
        if not writing and not isinstance(path, int) and not _readable(path):
            raise PermissionError("Not allowed in a Veyra computation: reading files outside its inputs and the Python libraries")
    if event in ("os.listdir", "os.scandir", "glob.glob") and args and args[0] not in (None, "") and not _readable(args[0]):
        raise PermissionError(f"Not allowed in a Veyra computation: {event} outside its own folder")


class Series(dict):
    """One input series: .t (seconds, UTC epoch), .v (values), .unit, .clock, .signal, .scope, .sources (import#row)."""
    __getattr__ = dict.__getitem__


def _clean(value, depth=0):
    if depth > 8:
        return str(value)
    if np is not None:
        if isinstance(value, np.ndarray):
            value = value.tolist()
        elif isinstance(value, np.generic):
            value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _clean(v, depth + 1) for k, v in list(value.items())[:500]}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(value)[:2000]]
    if pd is not None and hasattr(value, "to_dict"):
        return _clean(value.to_dict(), depth + 1)
    return str(value)


def main():
    series = {}
    for name, spec in INPUTS["series"].items():
        s = Series(spec)
        if np is not None and spec.get("numeric"):
            s["t"] = np.asarray(spec["t"], dtype=float)
            s["v"] = np.asarray(spec["v"], dtype=float)
        series[name] = s
    namespace = {"__name__": "__veyra__", "series": series, "np": np, "pd": pd, "math": math, "statistics": statistics,
                 "params": INPUTS.get("params") or {}, "result": None}
    sys.addaudithook(_hook)
    out = io.StringIO()
    old = sys.stdout
    sys.stdout = out
    started = time.perf_counter()
    status = {"ok": True}
    try:
        exec(compile(CODE, "<veyra computation>", "exec"), namespace)
        if namespace.get("result") is None:
            status = {"ok": False, "error": "The code finished without setting `result`."}
    except BaseException as error:  # noqa: BLE001 - report every failure to the caller
        tb = traceback.format_exception(type(error), error, error.__traceback__)
        status = {"ok": False, "error": f"{type(error).__name__}: {error}", "traceback": "".join(t for t in tb if "veyra computation" in t or t is tb[-1])[-1500:]}
    finally:
        sys.stdout = old
    payload = {**status, "result": _clean(namespace.get("result")) if status["ok"] else None, "stdout": out.getvalue()[:MAX_STDOUT],
               "compute_ms": round((time.perf_counter() - started) * 1000, 1), "numpy": np is not None, "pandas": pd is not None}
    with open(os.path.join(WORK, "result.json"), "w") as handle:
        json.dump(payload, handle)


main()
