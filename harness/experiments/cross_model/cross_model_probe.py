"""Cross-model replication of the core self-verification vs. execution-aware
judgment comparison, using GPT-5 instead of Claude — same prompts, same
tasks, same grading (SWE-bench harness / synthetic repo tests unchanged),
only the model identifier differs. Covers:

  - 4 synthetic recovery tasks (discount, progress, eligibility, timeout)
    already used in the extended ablation battery — reuses controlplane.py
    unchanged, just passes model="gpt-5".
  - 4 confirmed real trap-exhibiting SWE-bench tasks (12273, 13346, 13512,
    14140) — requires an OpenAI-native tool-calling grounding loop (Step A),
    since Anthropic's and OpenAI's tool-use APIs are shaped differently;
    Step B (real Docker verification) is completely unchanged.

Each condition per task, 1 run, matching the existing trace schema with an
added "model" field so it's unambiguous which model produced which run.
"""
from __future__ import annotations

import json
from pathlib import Path

from harness import controlplane

ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOY_REPO = ROOT / "toy_repo"
TASKS_DIR = ROOT / "tasks"
RESULTS_DIR = ROOT / "results" / "cross_model_gpt5_probe"

MODEL = "gpt-5"
MAX_TOKENS = 8000  # generous headroom for gpt-5's hidden reasoning tokens

SYNTHETIC_TASKS = [
    ("ticket_07_discounts", "tests/test_discounts.py"),
    ("ticket_08_progress", "tests/test_progress.py"),
    ("ticket_13_eligibility", "tests/test_eligibility.py"),
    ("ticket_15_timeout", "tests/test_timeout.py"),
]

CONDITIONS = [
    ("self_verification", dict(self_verify=True)),
    ("execution_aware_judgment", dict(execution_aware_self_judge=True)),
]


def run_synthetic() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for ticket_name, test_target in SYNTHETIC_TASKS:
        for cond_name, kwargs in CONDITIONS:
            trace = controlplane.run_task(
                TASKS_DIR / f"{ticket_name}.md",
                TOY_REPO,
                retry_budget=2,
                test_target=test_target,
                model=MODEL,
                max_tokens=MAX_TOKENS,
                **kwargs,
            )
            trace.run_index = 1
            out = RESULTS_DIR / f"trace_{ticket_name}_{cond_name}_{MODEL}_run1.json"
            out.write_text(json.dumps(trace.to_dict(), indent=2))
            print(
                f"{ticket_name} [{cond_name}] model={MODEL}: "
                f"final_status={trace.final_status} claims={trace.claims} "
                f"cost=${trace.usage.cost_usd:.4f}",
                flush=True,
            )
    controlplane._reset_repo(TOY_REPO)


if __name__ == "__main__":
    run_synthetic()
