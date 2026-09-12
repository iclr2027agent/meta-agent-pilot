"""Genuine cross-model review: does an actually different model, reasoning
only (no execution evidence), catch what the same model's own self-verification
missed? This isolates independence-of-reviewer from execution-grounding,
which the paper has already tested twice (self_verification vs.
execution_aware_judgment, and the evidence-quality gradient). Every prior
verification condition had the same model reviewing its own work, whether
framed as itself or as "a colleague" - this is the first time a genuinely
different model sits in the reviewer seat.

No new task-solving: every diff and problem_statement here is pulled
unchanged from an existing self-verification trace (the exact file is
named per-task below, to avoid ambiguity across the several diff variants
that have appeared for some real tasks over the course of this project).
Only the reviewer call is new, and it is strictly reasoning-only - no
shadow output, no test names, nothing from the evidence-gradient work.

Population is asymmetric by design: a direction only includes a task where
the reviewed model's own self-verification was genuinely wrong (claimed
success, actually failed) - otherwise there's no wrong-to-right transition
for cross-model review to demonstrate value on. See module-level task
lists below for the exact exclusions and why.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from harness import llm

ROOT = Path(__file__).resolve().parent.parent.parent.parent
TASKS_DIR = ROOT / "tasks"
CROSS_MODEL_DIR = ROOT / "results" / "cross_model_gpt5_probe"
RESULTS_DIR = ROOT / "results" / "cross_model_review_probe"

CLAUDE_MODEL = "claude-sonnet-4-6"
GPT5_MODEL = "gpt-5"
GPT5_MAX_TOKENS = 8000  # gpt-5 spends hidden reasoning tokens; needs headroom
CLAUDE_MAX_TOKENS = 4000

REVIEWER_SYSTEM = """You are reviewing a colleague's code change for correctness.

Based on your reasoning alone (no test execution available), does this
change fully and correctly resolve the ticket? Respond with ONLY a JSON
object: {"judged_complete": true/false, "reasoning": "<why>"}
"""

# (instance_id, domain, source_trace_path) - the exact trace each diff is
# pulled from, per the spec's sourcing table. domain picks how
# problem_statement/diff are extracted from that trace.

# GPT-5 reviews Claude's diffs. Excludes django__django-13346 (Claude's own
# self-verification was already correct there) and django__django-12273
# (Claude's self-verification diff was a degenerate no-op, not a fair test).
GPT5_REVIEWS_CLAUDE = [
    ("ticket_07_discounts", "synthetic", "results/self_verify_probe/trace_ticket_07_discounts_run1.json"),
    ("ticket_08_progress", "synthetic", "results/self_verify_probe/trace_ticket_08_progress_run1.json"),
    ("ticket_13_eligibility", "synthetic", "results/extended_ablation_probe/trace_ticket_13_eligibility_self_verification_run1.json"),
    ("ticket_15_timeout", "synthetic", "results/extended_ablation_probe/trace_ticket_15_timeout_self_verification_run1.json"),
    ("django__django-13512", "real", "results/swebench_pilot/trace_django__django-13512_self_verification_run1.json"),
    ("django__django-14140", "real", "results/swebench_pilot/trace_django__django-14140_self_verification_run1.json"),
]

# Claude reviews GPT-5's diffs. Excludes django__django-13346 only (GPT-5's
# self-verification was already correct there too).
CLAUDE_REVIEWS_GPT5 = [
    ("ticket_07_discounts", "synthetic", "results/cross_model_gpt5_probe/trace_ticket_07_discounts_self_verification_gpt-5_run1.json"),
    ("ticket_08_progress", "synthetic", "results/cross_model_gpt5_probe/trace_ticket_08_progress_self_verification_gpt-5_run1.json"),
    ("ticket_13_eligibility", "synthetic", "results/cross_model_gpt5_probe/trace_ticket_13_eligibility_self_verification_gpt-5_run1.json"),
    ("ticket_15_timeout", "synthetic", "results/cross_model_gpt5_probe/trace_ticket_15_timeout_self_verification_gpt-5_run1.json"),
    ("django__django-12273", "real", "results/cross_model_gpt5_probe/trace_django__django-12273_self_verification_gpt-5_run1.json"),
    ("django__django-13512", "real", "results/cross_model_gpt5_probe/trace_django__django-13512_self_verification_gpt-5_run1.json"),
    ("django__django-14140", "real", "results/cross_model_gpt5_probe/trace_django__django-14140_self_verification_gpt-5_run1.json"),
]


def _load_source(instance_id: str, domain: str, source_path: str) -> tuple[str, str, bool]:
    """Returns (problem_statement, diff, fail_to_pass_passed) pulled
    unchanged from the named source trace - the same materials that
    trace's own self-verification call was shown."""
    trace = json.loads((ROOT / source_path).read_text())
    if domain == "synthetic":
        problem_statement = (TASKS_DIR / f"{instance_id}.md").read_text()
        diff = next(s["detail"] for s in trace["steps"] if s["stage"] == "diff")
        actual_pass = trace["claims"][-1]["shadow_verified_success"]
        return problem_statement, diff, bool(actual_pass)
    else:
        return trace["problem_statement"], trace["diff"], bool(trace["fail_to_pass_passed"])


def _run_direction(tasks: list[tuple[str, str, str]], worker_model: str, reviewer_model: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    reviewer_max_tokens = GPT5_MAX_TOKENS if reviewer_model == GPT5_MODEL else CLAUDE_MAX_TOKENS
    for instance_id, domain, source_path in tasks:
        problem_statement, diff, fail_to_pass_passed = _load_source(instance_id, domain, source_path)
        user_input = f"Ticket:\n{problem_statement}\n\nProposed diff:\n{diff}"

        start = time.time()
        raw, usage = llm.call_llm(
            REVIEWER_SYSTEM, user_input, max_tokens=reviewer_max_tokens, model=reviewer_model
        )
        try:
            result = llm.extract_json(raw)
            judged_complete = bool(result.get("judged_complete", True))
            reasoning = result.get("reasoning", "")
        except ValueError as e:
            judged_complete = False
            reasoning = f"JUDGE_PARSE_FAILED: {e}"

        trace = {
            "instance_id": instance_id,
            "condition": "cross_model_review",
            "worker_model": worker_model,
            "reviewer_model": reviewer_model,
            "run_index": 1,
            "problem_statement": problem_statement,
            "diff": diff,
            "claimed_success": judged_complete,
            "reviewer_reasoning": reasoning,
            "fail_to_pass_passed": fail_to_pass_passed,
            "wall_clock_seconds": round(time.time() - start, 2),
            "estimated_cost_usd": round(usage.cost_usd, 4),
        }
        out_path = RESULTS_DIR / f"trace_{instance_id}_cross_model_review_worker-{worker_model}_reviewer-{reviewer_model}_run1.json"
        out_path.write_text(json.dumps(trace, indent=2))
        print(
            f"{instance_id} [worker={worker_model} reviewer={reviewer_model}]: "
            f"claimed_success={judged_complete} (actual fail_to_pass_passed={fail_to_pass_passed}) "
            f"cost=${usage.cost_usd:.4f}",
            flush=True,
        )


def run_all() -> None:
    _run_direction(GPT5_REVIEWS_CLAUDE, worker_model="claude", reviewer_model=GPT5_MODEL)
    _run_direction(CLAUDE_REVIEWS_GPT5, worker_model="gpt-5", reviewer_model=CLAUDE_MODEL)


if __name__ == "__main__":
    run_all()
