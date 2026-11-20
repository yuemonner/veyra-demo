"""Give freed memory back to the operating system after heavy work.

CPython frees the parsed rows of an investigation, but glibc keeps the pages, so a small hosted instance
(Render free: 512 MB) creeps toward its limit after a few investigations. malloc_trim returns them.
No-op where glibc is not available (macOS, Windows).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import gc

try:
    _LIBC = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
    _TRIM = getattr(_LIBC, "malloc_trim", None)
except OSError:  # pragma: no cover - platform without a loadable libc
    _TRIM = None


def release() -> None:
    gc.collect()
    if _TRIM is not None:
        try:
            _TRIM(0)
        except Exception:  # pragma: no cover
            pass
