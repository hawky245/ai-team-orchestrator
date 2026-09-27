"""Lightweight server telemetry for the console's bottom bar.

Stdlib-only: SYSTEM-wide CPU (not process CPU — the server idles at ~0%
while awaiting LLM responses, which reads as a dead graph), system memory
via the platform's native APIs, and uptime. The first sample returns
cpu_percent=None because a delta needs two points; the frontend keeps the
last value until real numbers arrive.
"""

from __future__ import annotations

import os
import time

_START = time.monotonic()
_last_cpu: tuple[float, float] | None = None  # (busy_ticks, total_ticks)


def _cpu_times() -> tuple[float, float] | None:
    """(busy, total) in native OS tick units for the whole machine.
    Both come from the same counter, so the units cancel in the delta."""
    if os.name == "nt":
        try:
            import ctypes

            idle = ctypes.c_ulonglong()
            kern = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            ok = ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)
            )
            if not ok:
                return None
            # kernel time includes idle; total = kernel + user
            total = float(kern.value + user.value)
            return (total - float(idle.value), total)
        except Exception:
            return None
    try:
        with open("/proc/stat") as fh:
            fields = [float(x) for x in fh.readline().split()[1:8]]
        # user, nice, system, idle, iowait, irq, softirq
        total = sum(fields)
        return (total - fields[3] - fields[4], total)
    except Exception:
        return None


def _mem_percent() -> float | None:
    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return float(stat.dwMemoryLoad)
        except Exception:
            return None
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                fields = rest.split()
                if fields:
                    info[key.strip()] = int(fields[0])
        total = info["MemTotal"]
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        return (total - avail) / total * 100.0
    except Exception:
        return None


def sample() -> dict:
    """One telemetry snapshot; cpu_percent is whole-machine utilization
    measured between successive polls."""
    global _last_cpu
    cpu = None
    now = _cpu_times()
    if now is not None:
        if _last_cpu is not None:
            dt = now[1] - _last_cpu[1]
            db = now[0] - _last_cpu[0]
            if dt > 0:
                cpu = round(min(100.0, max(0.0, db / dt * 100.0)), 1)
        _last_cpu = now
    mem = _mem_percent()
    return {
        "cpu_percent": cpu,
        "mem_percent": round(mem, 1) if mem is not None else None,
        "uptime_s": round(time.monotonic() - _START, 1),
    }
