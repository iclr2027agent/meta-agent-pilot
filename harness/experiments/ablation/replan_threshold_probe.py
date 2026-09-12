"""Same two recovery tickets, replan_threshold=2 instead of the default 1
used everywhere else in the pilot. Attempt 1 becomes a genuine same-plan
repair (executor gets the same plan plus real failure feedback, no
re-planning); attempt 2 would re-plan only if that repair itself fails.
"""
from pathlib import Path
import json
from harness import controlplane

ROOT = Path(__file__).resolve().parent.parent.parent.parent
REPO_DIR = ROOT / "toy_repo"
RESULTS_DIR = ROOT / "results" / "replan_threshold_probe"
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
            replan_threshold=2,
        )
        trace.run_index = i
        out = RESULTS_DIR / f"trace_{ticket_name}_run{i}.json"
        out.write_text(json.dumps(trace.to_dict(), indent=2))
        print(
            f"{ticket_name} run {i}: {trace.final_status}, "
            f"retries={trace.retries_used}, replans={trace.replans_used}"
        )

controlplane._reset_repo(REPO_DIR)
