"""Real CPU/RAM/disk-I/O limits for agent-run commands (Sandbox Security
Layer). Uses ``psutil`` to read a process TREE's actual resource usage —
``deps.py::run_cancellable`` polls :func:`check_limits` on the same cadence it
already uses for cancel/timeout, and kills the tree the moment a configured
cap is exceeded.

Best-effort by design: any single metric psutil can't read on this platform
(e.g. ``io_counters()`` is unavailable on macOS without extra permissions) is
silently skipped rather than raised — a monitoring gap must never crash the
command it's watching.
"""
from __future__ import annotations

from typing import Optional

import psutil


def check_limits(pid: int, limits: dict) -> Optional[str]:
    """Return a human-readable reason if the process tree rooted at ``pid``
    exceeds one of ``limits`` (``cpu_percent``, ``memory_mb``, ``disk_mb`` —
    any subset, unset keys are not checked), else ``None``.

    Sums the metric across the root process AND all its descendants, since a
    shell wrapping the real command (or a build tool forking workers) means
    the root process alone often under-reports actual usage."""
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return None  # process already gone — nothing to enforce
    procs = [root]
    try:
        procs += root.children(recursive=True)
    except psutil.Error:
        pass

    cpu_cap = limits.get("cpu_percent")
    mem_cap = limits.get("memory_mb")
    disk_cap = limits.get("disk_mb")
    total_cpu = total_mem_mb = total_disk_mb = 0.0

    for p in procs:
        try:
            if cpu_cap is not None:
                total_cpu += p.cpu_percent(interval=None)
            if mem_cap is not None:
                total_mem_mb += p.memory_info().rss / (1024 * 1024)
            if disk_cap is not None:
                io = p.io_counters()
                total_disk_mb += (io.read_bytes + io.write_bytes) / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except (AttributeError, NotImplementedError):
            pass  # io_counters() not supported on this platform — skip disk only

    if mem_cap is not None and total_mem_mb > mem_cap:
        return f"memory limit exceeded ({total_mem_mb:.0f}MB > {mem_cap:.0f}MB)"
    if cpu_cap is not None and total_cpu > cpu_cap:
        return f"CPU limit exceeded ({total_cpu:.0f}% > {cpu_cap:.0f}%)"
    if disk_cap is not None and total_disk_mb > disk_cap:
        return f"disk I/O limit exceeded ({total_disk_mb:.0f}MB > {disk_cap:.0f}MB)"
    return None


def prime_cpu_counter(pid: int) -> None:
    """``Process.cpu_percent(interval=None)`` always returns 0.0 on its FIRST
    call for a given process (it measures the delta since the last call) —
    call this once right after spawning, before the first :func:`check_limits`
    poll, so the very first real measurement isn't silently skipped as 0%."""
    try:
        psutil.Process(pid).cpu_percent(interval=None)
    except psutil.Error:
        pass
