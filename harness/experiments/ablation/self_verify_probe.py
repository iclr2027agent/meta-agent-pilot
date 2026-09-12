"""Ablation: same two recovery tickets, self_verify=True (the same worker
re-reads its own diff against the ticket, no test execution). Compares
against trust_claims=True (no check at all) and the main pilot's real
independent verification.
"""
from pathlib import Path
import json
from harness import controlplane

ROOT = Path(__file__).resolve().parent.parent.parent.parent
REPO_DIR = ROOT / "toy_repo"
RESULTS_DIR = ROOT / "results" / "self_verify_probe"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TICKETS = [
    ("ticket_07_discounts", "tests/test_discounts.py"),
    ("ticket_08_progress", "tests/test_progress.py"),
]

for ticket_name, test_target in TICKETS:
    ticket = ROOT / "tasks" / f"{ticket_name}.md"
    for i in range(1, 4):
        trace = controlplane.run_task(
            ticket, REPO_DIR, retry_budget=2, test_target=test_target, self_verify=True
        )
        trace.run_index = i
        out = RESULTS_DIR / f"trace_{ticket_name}_run{i}.json"
        out.write_text(json.dumps(trace.to_dict(), indent=2))
        c = trace.claims[-1]
        print(
            f"{ticket_name} run {i}: final_status={trace.final_status}, "
            f"self_verified={c['self_verified_success']}, shadow_verified={c['shadow_verified_success']}"
        )

controlplane._reset_repo(REPO_DIR)
