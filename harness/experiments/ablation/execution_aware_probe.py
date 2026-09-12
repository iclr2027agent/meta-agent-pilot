"""Ablation: same two recovery tickets, execution_aware_self_judge=True.
Unlike self_verify, the worker IS shown the real deterministic pytest
output — the question is whether the worker interprets real evidence
correctly, not whether it can produce evidence on its own.
"""
from pathlib import Path
import json
from harness import controlplane

ROOT = Path(__file__).resolve().parent.parent.parent.parent
REPO_DIR = ROOT / "toy_repo"
RESULTS_DIR = ROOT / "results" / "execution_aware_probe"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TICKETS = [
    ("ticket_07_discounts", "tests/test_discounts.py"),
    ("ticket_08_progress", "tests/test_progress.py"),
]

for ticket_name, test_target in TICKETS:
    ticket = ROOT / "tasks" / f"{ticket_name}.md"
    for i in range(1, 4):
        trace = controlplane.run_task(
            ticket, REPO_DIR, retry_budget=2, test_target=test_target,
            execution_aware_self_judge=True,
        )
        trace.run_index = i
        out = RESULTS_DIR / f"trace_{ticket_name}_run{i}.json"
        out.write_text(json.dumps(trace.to_dict(), indent=2))
        print(f"{ticket_name} run {i}: judged_complete={trace.claims[-1]['judged_complete']}, "
              f"actual_pass={trace.claims[-1]['actual_test_result']}")

controlplane._reset_repo(REPO_DIR)
