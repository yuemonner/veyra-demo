"""Run a computation (Python written by the agent or saved by the team) away from the API process.

Layers, all applied to every run:
1. A separate process started with `python -I -B` (no environment variables, no user site, no current folder on
   the import path), in a new empty temporary folder that is deleted afterwards.
2. Operating-system isolation: a network namespace with no interfaces on Linux (`unshare --net`), or
   `sandbox-exec` on macOS (network denied; writes only to its own folder; Veyra's data folder unreadable).
   If neither is available the run is refused, unless the operator sets VEYRA_COMPUTE_ALLOW_WEAK_SANDBOX=true.
3. Resource limits: CPU seconds, memory (where the OS enforces it), file size, open files, and a wall-clock timeout
   that kills the whole process group.
4. An audit hook inside the process (see _child.py) that refuses network, processes, ctypes, and file access
   outside its folder and the Python libraries.

It is not a virtual machine. It is meant for code the agent wrote against data its owner controls, on thethe owner's own instance, with every run recorded.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

CHILD = Path(__file__).with_name("_child.py")
MAX_CODE_CHARS = 12_000
DEFAULT_LIMITS = {"wall_s": 30.0, "cpu_s": 20, "memory_mb": 1536, "file_mb": 32, "open_files": 256}


class ComputeError(ValueError):
    pass


@lru_cache(maxsize=1)
def isolation() -> Dict[str, Any]:
    """Which operating-system isolation this computer offers. Probed once."""
    system = platform.system()
    if system == "Linux" and shutil.which("unshare"):
        try:
            ok = subprocess.run(["unshare", "--net", "--map-root-user", "true"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return {"kind": "linux-netns", "label": "Separate process in a network namespace with no network; resource limits; audit hook", "strong": True}
    if system == "Darwin" and Path("/usr/bin/sandbox-exec").exists():
        return {"kind": "macos-sandbox", "label": "Separate process under macOS sandbox-exec: no network, writes only to its own folder; resource limits; audit hook", "strong": True}
    return {"kind": "weak", "label": "Separate process with resource limits and an audit hook only (no operating-system isolation)", "strong": False}


def _sb_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _command(work: str, protected: List[str]) -> List[str]:
    base = [sys.executable, "-I", "-B", str(Path(work, "_child.py")), work]
    kind = isolation()["kind"]
    if kind == "linux-netns":
        return ["unshare", "--net", "--map-root-user"] + base
    if kind == "macos-sandbox":
        rules = ["(version 1)", "(allow default)", "(deny network*)", "(deny process-fork)", "(deny file-write*)",
                 f"(allow file-write* (subpath {_sb_quote(os.path.realpath(work))}))", '(allow file-write* (literal "/dev/null"))']
        rules += [f"(deny file-read* (subpath {_sb_quote(os.path.realpath(p))}))" for p in protected if p]
        return ["/usr/bin/sandbox-exec", "-p", " ".join(rules)] + base
    if os.getenv("VEYRA_COMPUTE_ALLOW_WEAK_SANDBOX") != "true":
        raise ComputeError("Computations are switched off on this computer: no operating-system sandbox is available "
                           "(Linux network namespaces or macOS sandbox-exec). The operator can allow a weaker sandbox with VEYRA_COMPUTE_ALLOW_WEAK_SANDBOX=true.")
    return base


def _limits(limits: Dict[str, Any]):
    def apply():
        import resource
        os.setsid()
        for name, value in (("RLIMIT_CPU", int(limits["cpu_s"])), ("RLIMIT_FSIZE", int(limits["file_mb"]) << 20),
                            ("RLIMIT_NOFILE", int(limits["open_files"])), ("RLIMIT_AS", int(limits["memory_mb"]) << 20), ("RLIMIT_CORE", 0)):
            try:
                resource.setrlimit(getattr(resource, name), (value, value))
            except (ValueError, OSError, AttributeError):
                pass  # macOS does not enforce RLIMIT_AS; the wall-clock timeout still applies
    return apply


def run(code: str, series: Dict[str, Dict[str, Any]], *, params: Optional[Dict[str, Any]] = None,
        limits: Optional[Dict[str, Any]] = None, protected: Optional[List[str]] = None) -> Dict[str, Any]:
    """series: name -> output of CaseData.series (t, v, unit, clock, ... , provenance). Returns the result with provenance."""
    if not isinstance(code, str) or not code.strip():
        raise ComputeError("No code given")
    if len(code) > MAX_CODE_CHARS:
        raise ComputeError(f"Code is longer than {MAX_CODE_CHARS} characters; split the analysis")
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    inputs = {"series": {name: {k: s[k] for k in ("t", "v", "unit", "clock", "clock_max_skew_s", "signal", "scope", "numeric", "case_id", "window", "sources")}
                         for name, s in series.items()}, "params": params or {}}
    blob = json.dumps(inputs, separators=(",", ":"), default=str)
    work = os.path.realpath(tempfile.mkdtemp(prefix="veyra-compute-"))
    started = time.perf_counter()
    try:
        Path(work, "_child.py").write_text(CHILD.read_text())
        Path(work, "code.py").write_text(code)
        Path(work, "inputs.json").write_text(blob)
        command = _command(work, protected or [])
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "HOME": work, "TMPDIR": work}
        proc = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=_limits(lim), close_fds=True)
        try:
            _, err = proc.communicate(timeout=lim["wall_s"])
            timed_out = False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            _, err = proc.communicate()
            timed_out = True
        path = Path(work, "result.json")
        if timed_out:
            out = {"ok": False, "error": f"Stopped after {lim['wall_s']:g} s (time limit)"}
        elif path.exists():
            out = json.loads(path.read_text())
        else:
            reason = "CPU time limit" if proc.returncode in (-signal.SIGXCPU, -signal.SIGKILL) else f"exit code {proc.returncode}"
            tail = (err or b"").decode("utf-8", errors="replace").strip().splitlines()[-3:]
            out = {"ok": False, "error": f"The computation stopped ({reason}). " + " | ".join(tail)[:600]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out["provenance"] = {
        "code_sha256": hashlib.sha256(code.encode()).hexdigest(), "inputs_sha256": hashlib.sha256(blob.encode()).hexdigest(),
        "inputs": [s["provenance"] for s in series.values()], "params": params or {}, "isolation": isolation()["label"],
        "limits": lim, "duration_ms": round((time.perf_counter() - started) * 1000), "python": platform.python_version(),
    }
    return out
