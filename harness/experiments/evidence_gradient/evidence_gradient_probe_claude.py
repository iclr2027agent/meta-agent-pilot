"""Evidence-quality gradient probe, Claude replication.

Same construction as evidence_gradient_probe.py (the GPT-5 version), run
against Claude as reviewer instead. For 12 of 14 tasks, Levels 0 and 4 are
reused unchanged from existing traces (self-verification and
execution-aware-judgment probes already run on Claude); only Levels 1-3 are
new calls. For the 2 real-repo tasks (django-13512, django-14140), the
existing execution-aware-judgment trace judged a DIFFERENT diff than Level 0
(a fresh independent solve, not a re-judgment of the same fix), so Level 4
for those two is reconstructed here as a fresh call using Level 0's actual
diff plus the real, already-captured full raw test output for that exact
diff -- keeping the diff constant across all 5 levels, as the design requires.

No new task-solving. All evidence text is derived from real, previously
captured test-execution output (pytest for the synthetic toy_repo tickets,
Django's test runner for the two real tasks) -- never fabricated.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from harness import llm

ROOT = Path(__file__).resolve().parent.parent.parent.parent
RESULTS_DIR = ROOT / "results" / "evidence_gradient_claude_probe"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

MODEL = "claude-sonnet-4-6"

REVIEWER_SYSTEM = """You are reviewing a colleague's code change for correctness.

Based on the ticket, the proposed diff, and the test evidence provided, decide
whether this change fully and correctly resolves the ticket. Respond with
ONLY a JSON object: {"judged_complete": true/false, "reasoning": "<why>"}
"""


def run_one(instance_id: str, run: int, level: int, problem_statement: str, diff: str, evidence_text: str) -> dict:
    user_input = (
        f"Ticket:\n{problem_statement}\n\n"
        f"Proposed diff:\n{diff}\n\n"
        f"Test evidence:\n{evidence_text}"
    )
    start = time.time()
    raw, usage = llm.call_llm(REVIEWER_SYSTEM, user_input, max_tokens=2000, model=MODEL)
    try:
        result = llm.extract_json(raw)
        judged_complete = bool(result.get("judged_complete", True))
        reasoning = result.get("reasoning", "")
    except ValueError as e:
        judged_complete = False
        reasoning = f"JUDGE_PARSE_FAILED: {e}; raw={raw[:500]!r}"

    trace = {
        "instance_id": instance_id,
        "run_index": run,
        "condition": f"evidence_level_{level}",
        "model": MODEL,
        "problem_statement": problem_statement,
        "diff": diff,
        "evidence_shown": evidence_text,
        "claimed_success": judged_complete,
        "reviewer_reasoning": reasoning,
        "ground_truth_incomplete": True,
        "correct": (judged_complete is False),
        "wall_clock_seconds": round(time.time() - start, 2),
        "estimated_cost_usd": round(usage.cost_usd, 4),
    }
    out_path = RESULTS_DIR / f"trace_{instance_id}_run{run}_evidence_level_{level}_{MODEL}.json"
    out_path.write_text(json.dumps(trace, indent=2))
    print(
        f"{instance_id} run{run} [level {level}]: judged_complete={judged_complete} "
        f"(correct={trace['correct']}) cost=${usage.cost_usd:.4f}",
        flush=True,
    )
    return trace


def main():
    synthetic = json.loads(Path("/tmp/synthetic_evidence.json").read_text())
    real = json.loads(Path("/tmp/real_evidence.json").read_text())

    all_results = []

    for key, t in synthetic.items():
        for level in (1, 2, 3):
            r = run_one(t["task_id"], t["run"], level, t["problem_statement"], t["diff"], t["evidence_levels"][str(level)])
            all_results.append(r)

    for iid, t in real.items():
        for level in (1, 2, 3, 4):
            r = run_one(t["task_id"], t["run"], level, t["problem_statement"], t["diff"], t["evidence_levels"][str(level)])
            all_results.append(r)

    Path(RESULTS_DIR / "_all_new_calls_summary.json").write_text(json.dumps(all_results, indent=2))
    print(f"\nTotal new reviewer calls: {len(all_results)}")


if __name__ == "__main__":
    main()
