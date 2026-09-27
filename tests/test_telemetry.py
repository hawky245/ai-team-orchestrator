"""Milestone 23 tests: stdlib telemetry sampler for the console's bottom bar.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_telemetry.py
"""

from __future__ import annotations

import time

from src import telemetry


def test_sample_shape_and_bounds():
    first = telemetry.sample()
    assert set(first) == {"cpu_percent", "mem_percent", "uptime_s"}
    # A CPU delta needs two sample points; the first call has none.
    assert first["cpu_percent"] is None
    assert first["uptime_s"] >= 0

    time.sleep(0.05)
    second = telemetry.sample()
    assert second["cpu_percent"] is None or 0.0 <= second["cpu_percent"] <= 100.0
    assert second["mem_percent"] is None or 0.0 <= second["mem_percent"] <= 100.0
    assert second["uptime_s"] >= first["uptime_s"]
    print("PASS  telemetry.sample() shape, bounds, and first-call None")


if __name__ == "__main__":
    test_sample_shape_and_bounds()
    print("\nTelemetry tests passed.")
