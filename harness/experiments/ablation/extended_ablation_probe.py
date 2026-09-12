"""Extend the four ablation conditions (no_verification, self_verification,
execution_aware_judge, repair_threshold) from tickets 07/08 to tickets 13
and 15 — the clean single-mechanism non-negativity case and the
minimum-positive-integer case, so the ablation battery spans all three
mechanism types the paper claims (piggyback: 07; mixed ceiling/floor: 08;
pure non-negativity: 13; minimum-positive-integer: 15), not just two of
them.
"""
from pathlib import Path
import json
from harness import controlplane

ROOT = Path(__file__).resolve().parent.parent.parent.parent
REPO_DIR = ROOT / "toy_repo"
RESULTS_DIR = ROOT / "results" / "extended_ablation_probe"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TICKETS = [
    ("ticket_13_eligibility", "tests/test_eligibility.py"),
    ("ticket_15_timeout", "tests/test_timeout.py"),
]

CONDITIONS = [
    ("no_verification",       dict(trust_claims=True)),
    ("self_verification",     dict(self_verify=True)),
    ("execution_aware_judge", dict(execution_aware_self_judge=True)),
    ("repair_threshold",      dict(replan_threshold=2)),
]

for ticket_name, test_target in TICKETS:
    ticket = ROOT / "tasks" / f"{ticket_name}.md"
    for cond_name, kwargs in CONDITIONS:
        for i in range(1, 4):
            trace = controlplane.run_task(
                ticket, REPO_DIR, retry_budget=2, test_target=test_target, **kwargs
            )
            trace.run_index = i
            out = RESULTS_DIR / f"trace_{ticket_name}_{cond_name}_run{i}.json"
            out.write_text(json.dumps(trace.to_dict(), indent=2))
            print(f"{ticket_name} [{cond_name}] run {i}: final_status={trace.final_status}")

controlplane._reset_repo(REPO_DIR)
