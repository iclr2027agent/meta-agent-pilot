"""SWE-bench real-task pilot orchestrator.

For each (instance, condition):
  1. Step A: ground_and_implement() produces a diff via the tool-use loop
     against a plain local checkout (no deps, no Docker).
  2. Step B: the diff is verified for real via the swebench harness
     (Docker) to get ground-truth FAIL_TO_PASS/PASS_TO_PASS results and the
     real test output.
  3. The condition's judge call is made:
       - self_verification: sees only (problem_statement, diff)
       - execution_aware_judgment: sees (problem_statement, diff, real
         test output from step 2)
  4. A trace JSON matching the project's existing schema is written.

Usage:
    python -m harness.run_swebench_pilot --instances django__django-12273 \
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

from . import llm, swebench_agent, swebench_agent_openai

ROOT = Path(__file__).resolve().parent.parent
PILOT_DIR = ROOT / "swebench_pilot"
REPO_DIR = PILOT_DIR / "django_repo"
RESULTS_DIR = ROOT / "results" / "swebench_pilot"
LOGS_DIR = PILOT_DIR / "logs" / "run_evaluation"  # matches swebench's relative default

ALL_INSTANCE_IDS = [
    "django__django-12273",
    "django__django-14140",
    "django__django-13346",
    "django__django-13512",
    "django__django-14238",
]
ALL_CONDITIONS = ["self_verification", "execution_aware_judgment"]


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def load_instances() -> dict:
    data = json.loads((PILOT_DIR / "instances.json").read_text())
    return {d["instance_id"]: d for d in data}


def checkout(instance: dict) -> None:
    r = _run(["git", "checkout", "--force", "--quiet", instance["base_commit"]], REPO_DIR)
    if r.returncode != 0:
        raise RuntimeError(f"checkout failed for {instance['instance_id']}: {r.stderr}")
    # drop any stray untracked files from a previous run's failed patch attempt
    _run(["git", "clean", "-fdq"], REPO_DIR)


def step_a(
    instance: dict, log_path: Path, model: str | None = None
) -> tuple[str | None, "swebench_agent.GroundingResult"]:
    checkout(instance)
    if model is not None and llm._is_openai_model(model):
        result = swebench_agent_openai.ground_and_implement(
            instance["problem_statement"], REPO_DIR, log_path=log_path, model=model
        )
    else:
        result = swebench_agent.ground_and_implement(
            instance["problem_statement"], REPO_DIR, log_path=log_path
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
            "model_name_or_path": "pilot",
        }
    ]))

    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "-d", str(PILOT_DIR / "instances.json"),
        "-p", str(pred_path),
        "-id", run_id,
        "-i", instance_id,
        "--max_workers", "1",
        "-t", "1800",
        "--report_dir", str(PILOT_DIR),
    ]
    # This Mac is arm64; the published swebench images are x86_64-only, so
    # Docker must be told to pull/run the amd64 variant under emulation.
    env = {**os.environ, "DOCKER_DEFAULT_PLATFORM": "linux/amd64"}
    proc = subprocess.run(cmd, cwd=PILOT_DIR, capture_output=True, text=True, env=env)
    log_dir = LOGS_DIR / run_id / "pilot" / instance_id
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
    # If the patch never applied (or the patched code broke test collection
    # entirely, e.g. a NameError at import time), swebench's report has no
    # "tests_status" key at all — no tests ever actually ran. Treat both as
    # False in that case rather than defaulting pass_to_pass_passed to True
    # from an empty (never-populated) failure list, which would misreport
    # "no regressions" when in truth nothing was ever verified.
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
        # Break at a line boundary rather than an arbitrary character
        # count, so whoever reads this (model or human) doesn't start on a
        # truncated mid-word fragment that could be misread as garbled or
        # corrupted output rather than a normal truncation.
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


def run_one(
    instance_id: str, condition: str, run_index: int, instances: dict, model: str | None = None
) -> dict:
    instance = instances[instance_id]
    start = time.time()
    effective_model = model or llm.MODEL

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESULTS_DIR / f"debug_log_{instance_id}_{condition}_run{run_index}.jsonl"
    log_path.write_text("")  # truncate/create fresh so a stale prior run's log can't be mistaken for this one
    print(f"  (live log: {log_path})", flush=True)

    diff, grounding = step_a(instance, log_path, model=model)
    cost = grounding.usage.cost_usd
    diagnostics = {"tool_calls_used": grounding.tool_calls_used, "transcript_len": len(grounding.transcript)}

    if diff is None:
        # Real, loggable outcome: the model never submitted a patch within
        # budget. No diff to verify or judge.
        trace = {
            "instance_id": instance_id,
            "condition": condition,
            "run_index": run_index,
            "model": effective_model,
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

    # gpt-5 spends hidden reasoning tokens before any visible output, which
    # can consume the entire default 4000-token budget on a judge call over
    # a large real diff/test-output and leave nothing for the JSON answer
    # (observed directly on the 12273 smoke test: an empty response that
    # failed to parse). Anthropic's models don't have this failure mode, so
    # only widen the budget on the OpenAI path.
    judge_max_tokens = 8000 if model is not None and llm._is_openai_model(model) else 4000

    if condition == "self_verification":
        judged_complete, reasoning, usage2 = swebench_agent.self_verification_judge(
            instance["problem_statement"], diff, model=model, max_tokens=judge_max_tokens
        )
    elif condition == "execution_aware_judgment":
        judged_complete, reasoning, usage2 = swebench_agent.execution_aware_judge(
            instance["problem_statement"], diff, ground_truth["shadow_output"], model=model, max_tokens=judge_max_tokens
        )
    else:
        raise ValueError(f"unknown condition {condition!r}")

    cost += usage2.cost_usd

    trace = {
        "instance_id": instance_id,
        "condition": condition,
        "run_index": run_index,
        "model": effective_model,
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
    parser.add_argument(
        "--model", default=None,
        help="Override the model (e.g. gpt-5 for the cross-model comparison). "
             "Default (None) preserves existing Claude-only behavior.",
    )
    parser.add_argument(
        "--results-dir", default=None,
        help="Override the output directory. Defaults to results/swebench_pilot, "
             "or results/cross_model_gpt5_probe when --model is set, to avoid "
             "colliding with the existing Claude-only traces.",
    )
    global RESULTS_DIR
    args = parser.parse_args()

    instances = load_instances()
    if args.results_dir is not None:
        results_dir = Path(args.results_dir)
    elif args.model is not None:
        results_dir = ROOT / "results" / "cross_model_gpt5_probe"
    else:
        results_dir = RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR = results_dir

    for instance_id in args.instances:
        for condition in args.conditions:
            print(f"=== {instance_id} [{condition}] model={args.model or llm.MODEL} ===", flush=True)
            trace = run_one(instance_id, condition, args.run_index, instances, model=args.model)
            model_suffix = f"_{args.model}" if args.model else ""
            out_path = results_dir / f"trace_{instance_id}_{condition}{model_suffix}_run{args.run_index}.json"
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
