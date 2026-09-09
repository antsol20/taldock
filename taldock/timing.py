"""Startup timing marks, enabled with TALDOCK_TIMING=1.

The slow case is login, and login cannot be reproduced from a shell: the
dock starts while five other applications are starting, on a cold page
cache. So the marks go to stderr, which xfce4-session redirects into
``~/.xsession-errors`` -- log in, then read them back there.

Every mark is relative to *exec*, not to this module's import, so the cost
of the interpreter and of importing gi is inside the first number.
"""
from __future__ import annotations

import os
import sys
import time

ENABLED = bool(os.environ.get("TALDOCK_TIMING"))

_PREFIX = "taldock-timing"
_seen = set()


def _exec_time():
    """This process's start, on the same scale as time.monotonic().

    /proc/self/stat field 22 is the start time in clock ticks since boot,
    and CLOCK_MONOTONIC also counts from boot -- close enough to line the
    two up, and the only way to see the interpreter's own startup cost.
    """
    try:
        with open("/proc/self/stat", "rb") as fh:
            # The comm field can contain spaces and parentheses; everything
            # after the last ')' is fixed-width.
            fields = fh.read().rpartition(b")")[2].split()
        started = int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return time.monotonic()
    # A suspend/resume between boot and now pulls the two clocks apart.
    # Rather than report nonsense, fall back to "now".
    if not 0.0 <= time.monotonic() - started <= 300.0:
        return time.monotonic()
    return started


_T0 = _exec_time()


def elapsed_ms():
    return (time.monotonic() - _T0) * 1000.0


def mark(label):
    """Record that `label` has just finished. No-op unless enabled."""
    if not ENABLED:
        return
    sys.stderr.write(f"{_PREFIX} {elapsed_ms():8.1f} ms  {label}\n")
    sys.stderr.flush()


def mark_once(label):
    """As mark(), but only the first time -- for per-frame call sites."""
    if not ENABLED or label in _seen:
        return
    _seen.add(label)
    mark(label)
