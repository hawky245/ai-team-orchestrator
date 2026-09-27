"""Milestone 20 tests: the write_file tool (sandbox, round-trip, denials).

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_write_file.py
"""

from __future__ import annotations

import json

from src.tools.worker_tools import (
    PROJECT_ROOT,
    TOOL_REGISTRY,
    WORKER_TOOLS,
    read_file,
    write_file,
)


def test_advertised():
    names = [t["function"]["name"] for t in WORKER_TOOLS]
    assert names == ["web_search", "read_file", "write_file"], names
    assert set(TOOL_REGISTRY) == set(names)
    print("PASS  write_file is advertised and registered")


def test_round_trip():
    res = json.loads(write_file("test_output/m20_roundtrip.md", "# hello\nline two"))
    assert res["status"] == "ok", res
    assert res["filepath"] == "test_output/m20_roundtrip.md"
    assert res["bytes_written"] == len("# hello\nline two")
    back = json.loads(read_file("test_output/m20_roundtrip.md"))
    assert back["status"] == "ok" and back["contents"] == "# hello\nline two", back
    # overwrite is allowed (memo updates)
    res2 = json.loads(write_file("test_output/m20_roundtrip.md", "v2"))
    assert res2["status"] == "ok"
    assert json.loads(read_file("test_output/m20_roundtrip.md"))["contents"] == "v2"
    (PROJECT_ROOT / "test_output" / "m20_roundtrip.md").unlink()
    print("PASS  write -> read round-trip, overwrite works, cleanup done")


def test_sandbox_and_denials():
    escape = json.loads(write_file("../outside_workspace.txt", "nope"))
    assert escape["status"] == "error" and "escapes" in escape["error"], escape
    assert not (PROJECT_ROOT.parent / "outside_workspace.txt").exists()

    env = json.loads(write_file(".env", "LLM_API_KEY=pwned"))
    assert env["status"] == "error", env
    key = json.loads(write_file("sub/id_rsa.pem", "pwned"))
    assert key["status"] == "error", key

    big = json.loads(write_file("tiny.txt", "x" * 200_001))
    assert big["status"] == "error" and "too large" in big["error"], big
    print("PASS  escapes, secret files, and oversized payloads are refused")


if __name__ == "__main__":
    test_advertised()
    test_round_trip()
    test_sandbox_and_denials()
    print("\nAll Milestone 20 write_file tests passed.")
