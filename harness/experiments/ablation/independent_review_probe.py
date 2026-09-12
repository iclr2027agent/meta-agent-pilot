"""Fifth ablation condition: independent_review (different-agent framing,
reasoning only) — the missing cell in the 2x2 (same/different agent x
reasoning-only/execution-evidence). Same 4 tasks as the rest of the
ablation battery for direct comparability.
"""
from pathlib import Path
import json
from harness import controlplane

ROOT = Path(__file__).resolve().parent.parent.parent.parent
REPO_DIR = ROOT / "toy_repo"
RESULTS_DIR = ROOT / "results" / "independent_review_probe"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TICKETS = [
    ("ticket_07_discounts", "tests/test_discounts.py"),
    ("ticket_08_progress", "tests/test_progress.py"),
    ("ticket_13_eligibility", "tests/test_eligibility.py"),
    ("ticket_15_timeout", "tests/test_timeout.py"),
]

for ticket_name, test_target in TICKETS:
    ticket = ROOT / "tasks" / f"{ticket_name}.md"
    for i in range(1, 4):
        trace = controlplane.run_task(
            ticket, REPO_DIR, retry_budget=2, test_target=test_target,
            independent_review=True,
        )
        trace.run_index = i
        out = RESULTS_DIR / f"trace_{ticket_name}_run{i}.json"
        out.write_text(json.dumps(trace.to_dict(), indent=2))
        c = trace.claims[-1]
        print(
            f"{ticket_name} run {i}: final_status={trace.final_status}, "
            f"reviewer_judged_complete={c['reviewer_judged_complete']}, "
            f"shadow_verified={c['shadow_verified_success']}"
        )

controlplane._reset_repo(REPO_DIR)
