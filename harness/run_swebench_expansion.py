"""Multi-repo SWE-bench Verified real-task pilot — expansion of
run_swebench_pilot.py (Section 5.2's single-repo django probe) to
scikit-learn, matplotlib, astropy, and sympy.

Same 4-step design as run_swebench_pilot.py:
  1. Step A: ground_and_implement() produces a diff via the tool-use loop
     against a plain local checkout of the instance's real repo (no deps,
     no Docker) — repo_dir is now resolved per-instance from its `repo`
     field rather than a single hardcoded django_repo.
  2. Step B: the diff is verified via the real swebench harness (Docker)
     against the harness-ready dataset (swebench_expansion/instances.json,
     pulled from SWE-bench/SWE-bench_Verified, which carries the
     image/eval_script/log_parser/eval_type fields this swebench version's
     make_test_spec() requires — the raw princeton-nlp/SWE-bench_Verified
     mirror does not have these).
  3. The condition's judge call is made, same as the django pilot.
  4. A trace JSON matching the existing schema is written, plus a
     `repo` field for the per-repo breakdown the expansion write-up needs.

Docker images for these repos are pre-pulled with `--platform linux/amd64`
before this script runs (see swebench_expansion/pull_images.sh) — the
installed swebench (5.0.2) calls `client.images.pull(image)` with no
platform argument, which on Apple Silicon resolves to a nonexistent
arm64 manifest for these x86_64-only images (404). Pre-pulling means the
harness's `images.get()` check finds the image already present and never
calls `pull()` at all.

Usage:
    python -m harness.run_swebench_expansion --instances astropy__astropy-12907 \
        --conditions self_verification --run-id smoke_test
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.swebench_expansion")

from . import llm, swebench_agent  # noqa: E402  (must follow load_dotenv)

EXP_DIR = ROOT / "swebench_expansion"
REPOS_DIR = EXP_DIR / "repos"
RESULTS_DIR = ROOT / "results" / "swebench_expansion"
LOGS_DIR = EXP_DIR / "logs" / "run_evaluation"  # matches swebench's relative default

# instance["repo"] (e.g. "astropy/astropy") -> local checkout dir
REPO_DIRS = {
    "scikit-learn/scikit-learn": REPOS_DIR / "scikit-learn_repo",
    "matplotlib/matplotlib": REPOS_DIR / "matplotlib_repo",
    "astropy/astropy": REPOS_DIR / "astropy_repo",
    "sympy/sympy": REPOS_DIR / "sympy_repo",
}

ALL_INSTANCE_IDS = [
    "astropy__astropy-12907",
    "astropy__astropy-13033",
    "astropy__astropy-14369",
    "astropy__astropy-13977",
    "matplotlib__matplotlib-24870",
    "matplotlib__matplotlib-22865",
    "matplotlib__matplotlib-20676",
    "scikit-learn__scikit-learn-13142",
    "scikit-learn__scikit-learn-25102",
    "sympy__sympy-13091",
    "sympy__sympy-17630",
    "sympy__sympy-16597",
    "sympy__sympy-19783",
]
ALL_CONDITIONS = ["self_verification", "execution_aware_judgment"]


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def load_instances() -> dict:
    data = json.loads((EXP_DIR / "instances.json").read_text())
    return {d["instance_id"]: d for d in data}


def checkout(instance: dict, repo_dir: Path) -> None:
    r = _run(["git", "checkout", "--force", "--quiet", instance["base_commit"]], repo_dir)
    if r.returncode != 0:
        raise RuntimeError(f"checkout failed for {instance['instance_id']}: {r.stderr}")
    _run(["git", "clean", "-fdq"], repo_dir)


def step_a(instance: dict, log_path: Path) -> tuple[str | None, "swebench_agent.GroundingResult"]:
    repo_dir = REPO_DIRS[instance["repo"]]
    checkout(instance, repo_dir)
    result = swebench_agent.ground_and_implement(
        instance["problem_statement"], repo_dir, log_path=log_path
    )
    return result.diff, result


def step_b(instance_id: str, diff: str, run_id: str) -> dict:
    """Run the real swebench harness on a single instance's diff. Returns
    dict with fail_to_pass_passed, pass_to_pass_passed, shadow_output."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    pred_path = RESULTS_DIR / f"predictions_{run_id}_{instance_id}.json"
    pred_path.write_text(json.dumps([
        {
            "instance_id": instance_id,
            "model_patch": diff or "",
            "model_name_or_path": "expansion_pilot",
        }
    ]))

    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "-d", str(EXP_DIR / "instances.json"),
        "-p", str(pred_path),
        "-id", run_id,
        "-i", instance_id,
        "--max_workers", "1",
        "-t", "1800",
        "--report_dir", str(EXP_DIR),
    ]
    proc = subprocess.run(cmd, cwd=EXP_DIR, capture_output=True, text=True, env=os.environ)
    log_dir = LOGS_DIR / run_id / "expansion_pilot" / instance_id
    report_path = log_dir / "report.json"
    test_output_path = log_dir / "test_output.txt"

    if not report_path.exists():
        return {
            "fail_to_pass_passed": False,
            "pass_to_pass_passed": False,
            "shadow_output": (
                f"HARNESS DID NOT PRODUCE A REPORT.\nstdout(tail):\n{proc.stdout[-3000:]}\n"
                f"stderr(tail):\n{proc.stderr[-3000:]}"
            ),
            "harness_error": True,
        }

    report = json.loads(report_path.read_text())
    inst_report = report.get(instance_id, {})
    patch_applied = inst_report.get("patch_successfully_applied", False)
    if not patch_applied:
        fail_to_pass_passed = False
        pass_to_pass_passed = False
    else:
        f2p = inst_report.get("tests_status", {}).get("FAIL_TO_PASS", {})
        p2p = inst_report.get("tests_status", {}).get("PASS_TO_PASS", {})
        fail_to_pass_passed = len(f2p.get("failure", [])) == 0 and len(f2p.get("success", [])) > 0
        pass_to_pass_passed = len(p2p.get("failure", [])) == 0

    shadow_output = test_output_path.read_text(errors="replace") if test_output_path.exists() else ""
    if len(shadow_output) > 8000:
        tail = shadow_output[-8000:]
        newline_idx = tail.find("\n")
        if 0 <= newline_idx < 200:
            tail = tail[newline_idx + 1:]
        shadow_output = "...[truncated]...\n" + tail

    return {
        "fail_to_pass_passed": fail_to_pass_passed,
        "pass_to_pass_passed": pass_to_pass_passed,
        "shadow_output": shadow_output,
        "resolved": inst_report.get("resolved", False),
        "harness_error": False,
        "patch_successfully_applied": patch_applied,
        "infra_failure": inst_report.get("infra_failure", False),
    }


def run_one(instance_id: str, condition: str, run_index: int, instances: dict) -> dict:
    instance = instances[instance_id]
    start = time.time()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESULTS_DIR / f"debug_log_{instance_id}_{condition}_run{run_index}.jsonl"
    log_path.write_text("")
    print(f"  (live log: {log_path})", flush=True)

    diff, grounding = step_a(instance, log_path)
    cost = grounding.usage.cost_usd
    diagnostics = {"tool_calls_used": grounding.tool_calls_used, "transcript_len": len(grounding.transcript)}

    if diff is None:
        trace = {
            "instance_id": instance_id,
            "repo": instance["repo"],
            "condition": condition,
            "run_index": run_index,
            "model": llm.MODEL,
            "problem_statement": instance["problem_statement"],
            "diff": None,
            "claimed_success": False,
            "reviewer_reasoning": "grounding loop exhausted tool-call budget without submit_patch",
            "fail_to_pass_passed": False,
            "pass_to_pass_passed": False,
            "shadow_output": "",
            "wall_clock_seconds": round(time.time() - start, 2),
            "estimated_cost_usd": round(cost, 4),
            "diagnostics": diagnostics,
        }
        return trace

    ground_truth = step_b(instance_id, diff, run_id=f"{condition}")

    if condition == "self_verification":
        judged_complete, reasoning, usage2 = swebench_agent.self_verification_judge(
            instance["problem_statement"], diff
        )
    elif condition == "execution_aware_judgment":
        judged_complete, reasoning, usage2 = swebench_agent.execution_aware_judge(
            instance["problem_statement"], diff, ground_truth["shadow_output"]
        )
    else:
        raise ValueError(f"unknown condition {condition!r}")

    cost += usage2.cost_usd

    trace = {
        "instance_id": instance_id,
        "repo": instance["repo"],
        "condition": condition,
        "run_index": run_index,
        "model": llm.MODEL,
        "problem_statement": instance["problem_statement"],
        "diff": diff,
        "claimed_success": judged_complete,
        "reviewer_reasoning": reasoning,
        "fail_to_pass_passed": ground_truth["fail_to_pass_passed"],
        "pass_to_pass_passed": ground_truth["pass_to_pass_passed"],
        "shadow_output": ground_truth["shadow_output"],
        "wall_clock_seconds": round(time.time() - start, 2),
        "estimated_cost_usd": round(cost, 4),
        "diagnostics": {
            **diagnostics,
            "patch_successfully_applied": ground_truth.get("patch_successfully_applied"),
            "infra_failure": ground_truth.get("infra_failure"),
            "harness_error": ground_truth.get("harness_error"),
        },
    }
    return trace


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instances", nargs="+", default=ALL_INSTANCE_IDS)
    parser.add_argument("--conditions", nargs="+", default=ALL_CONDITIONS)
    parser.add_argument("--run-index", type=int, default=1)
    args = parser.parse_args()

    instances = load_instances()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for instance_id in args.instances:
        for condition in args.conditions:
            out_path = RESULTS_DIR / f"trace_{instance_id}_{condition}_run{args.run_index}.json"
            if out_path.exists():
                print(f"=== {instance_id} [{condition}] already done, skipping ===", flush=True)
                continue
            print(f"=== {instance_id} [{condition}] model={llm.MODEL} ===", flush=True)
            trace = run_one(instance_id, condition, args.run_index, instances)
            out_path.write_text(json.dumps(trace, indent=2))
            print(
                f"  -> claimed_success={trace['claimed_success']} "
                f"fail_to_pass_passed={trace['fail_to_pass_passed']} "
                f"pass_to_pass_passed={trace['pass_to_pass_passed']} "
                f"cost=${trace['estimated_cost_usd']:.4f} "
                f"time={trace['wall_clock_seconds']:.1f}s",
                flush=True,
            )


if __name__ == "__main__":
    main()
