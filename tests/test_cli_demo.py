"""Smoke test for the `agentmesh demo` walkthrough — it must run end-to-end
with zero API keys/network calls and exercise the real governance classes."""

from __future__ import annotations

import argparse
import io
import contextlib

from agentmesh.cli import cmd_demo


def test_demo_runs_without_error_and_covers_all_four_steps():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_demo(argparse.Namespace())
    output = buf.getvalue()

    assert "[1/4]" in output and "[EMAIL]" in output and "[SSN]" in output
    assert "[2/4]" in output and "Blocked" in output
    assert "[3/4]" in output and "budget exceeded" in output
    assert "[4/4]" in output and "Audit chain" in output
    assert "Demo complete" in output
