"""Sanity-check the control plane end-to-end using a scripted fake LLM
(no API key needed). This does NOT validate real model behavior — it only
proves the state machine, file writes, git-diff guard, pytest verification,
regression sweep, retry/re-plan/escalation logic, timeout handling, and
trace/metric computation are wired correctly.

Run: python3 -m harness.dry_run_check
"""
from __future__ import annotations

import json
from pathlib import Path

from . import controlplane, llm

ROOT = Path(__file__).resolve().parent.parent
REPO_DIR = ROOT / "toy_repo"

_call_count = {"n": 0}


def _script_llm(script: list[str]):
    """Build a fake call_llm that returns `script` entries in call order,
    repeating the last entry if more calls happen than scripted responses."""
    _call_count["n"] = 0

    def fake(system: str, user: str, max_tokens: int = 4000, timeout: float = 90.0, model: str | None = None):
        idx = _call_count["n"]
        _call_count["n"] += 1
        text = script[min(idx, len(script) - 1)]
        return text, llm.LLMUsage(input_tokens=100, output_tokens=100, calls=1)

    return fake


PLANNER_PLAN = json.dumps({
    "task_interpretation": "is_prime(1) returns True",
    "candidate_files": ["src/toylib/mathutils.py"],
    "steps": ["Fix boundary condition for n < 2"],
    "validation_strategy": "run pytest",
    "uncertainties": [],
})

EXECUTOR_NOOP = json.dumps({
    # No-op on purpose: writes back the exact original buggy content, so the
    # git-diff guard sees no observable change and blocks before verification.
    "files": {
        "src/toylib/mathutils.py": Path(REPO_DIR / "src/toylib/mathutils.py").read_text()
    },
    "rationale": "no-op on purpose to exercise the guard/repair path",
    "claims_success": True,
})

PLANNER_REPLAN = json.dumps({
    "task_interpretation": "is_prime(1) returns True; first attempt made no change",
    "candidate_files": ["src/toylib/mathutils.py"],
    "steps": ["Actually edit the boundary condition this time"],
    "validation_strategy": "run pytest",
    "uncertainties": [],
})

EXECUTOR_CORRECT = json.dumps({
    "files": {
        "src/toylib/mathutils.py": (
            "def is_prime(n: int) -> bool:\n"
            "    if n < 2:\n"
            "        return False\n"
            "    for i in range(2, int(n ** 0.5) + 1):\n"
            "        if n % i == 0:\n"
            "            return False\n"
            "    return True\n\n\n"
            "def gcd(a: int, b: int) -> int:\n"
            "    while b:\n"
            "        a, b = b, a % b\n"
            "    return abs(a)\n"
        )
    },
    "rationale": "n < 2 now always returns False",
    "claims_success": True,
})


def check_guard_repair_replan_complete():
    """Call sequence: planner -> executor(no-op, guard-blocked) ->
    [re-plan] -> executor(correct) -> verify -> complete. Exercises the
    guard, the re-plan branch (exactly once, per REPAIR_ATTEMPTS_BEFORE_REPLAN),
    and independent verification together."""
    llm.call_llm = _script_llm([PLANNER_PLAN, EXECUTOR_NOOP, PLANNER_REPLAN, EXECUTOR_CORRECT])
    ticket = ROOT / "tasks" / "ticket_02_is_prime.md"
    trace = controlplane.run_task(
        ticket, REPO_DIR, retry_budget=2, test_target="tests/test_mathutils.py"
    )
    controlplane._reset_repo(REPO_DIR)
    print(json.dumps(trace.to_dict(), indent=2))
    assert trace.final_status == "complete", "expected the scripted repair path to succeed"
    assert trace.retries_used >= 1, "expected at least one repair cycle to have been exercised"
    assert trace.replans_used >= 1, "expected the re-plan branch to have fired"
    print("OK: guard -> re-plan -> verify -> complete path all wired correctly.\n")


def check_verification_timeout():
    """A scripted 'fix' that makes the target module hang on import (so
    pytest itself hangs collecting it). Confirms the subprocess timeout
    catches this deterministically and the task escalates instead of
    blocking forever — patches _run_pytest's timeout down to 2s just for
    this check so it doesn't eat the real 15s production timeout."""
    original_run_pytest = controlplane._run_pytest

    def fast_timeout_run_pytest(repo_dir, test_target=None):
        cmd = ["python3", "-m", "pytest", "-q"]
        if test_target:
            cmd.append(test_target)
        result = controlplane._run(cmd, repo_dir, timeout=2.0)
        output = result.stdout + result.stderr
        return result.returncode == 0, output, result.returncode == 124

    controlplane._run_pytest = fast_timeout_run_pytest
    try:
        hang_fix = json.dumps({
            "files": {
                "src/toylib/mathutils.py": (
                    "import time\n"
                    "time.sleep(30)\n\n"
                    "def is_prime(n: int) -> bool:\n"
                    "    return False\n\n\n"
                    "def gcd(a: int, b: int) -> int:\n"
                    "    return 0\n"
                )
            },
            "rationale": "deliberately hangs on import to test the timeout guard",
            "claims_success": True,
        })
        llm.call_llm = _script_llm([PLANNER_PLAN, hang_fix])
        ticket = ROOT / "tasks" / "ticket_02_is_prime.md"
        # retry_budget=0: one attempt, times out, no room left to repair/replan.
        trace = controlplane.run_task(
            ticket, REPO_DIR, retry_budget=0, test_target="tests/test_mathutils.py"
        )
        controlplane._reset_repo(REPO_DIR)
        stages = [s["stage"] for s in trace.to_dict()["steps"]]
        assert "verification_timeout" in stages, f"expected a verification_timeout step, got {stages}"
        assert trace.final_status == "escalated", (
            f"expected escalation after a timeout with no retry budget left, got {trace.final_status}"
        )
        print("OK: a hung verification is caught as a deterministic timeout and the task escalates "
              "rather than hanging.\n")
    finally:
        controlplane._run_pytest = original_run_pytest


def check_run_timeout_mechanism():
    """Fast, direct unit check of the _run() subprocess timeout itself,
    independent of the full task pipeline."""
    result = controlplane._run(["python3", "-c", "import time; time.sleep(5)"], REPO_DIR, timeout=1.0)
    assert result.returncode == 124, f"expected returncode 124 on timeout, got {result.returncode}"
    assert "TIMEOUT" in result.stderr
    print("OK: _run() subprocess timeout returns a deterministic non-zero result instead of hanging.\n")


def main():
    check_run_timeout_mechanism()
    check_verification_timeout()
    check_guard_repair_replan_complete()


if __name__ == "__main__":
    main()
