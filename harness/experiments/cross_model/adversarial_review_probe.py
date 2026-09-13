"""Adversarial-prompt re-review of the 13 genuine cross-model review diffs
(cross_model_review_probe.py, Section 5.6). Every one of those 13 reviews
used a neutral "does this diff correctly resolve the ticket?" prompt and
wrongly approved a diff later shown (via real test execution) to be
incomplete. This module asks whether an explicitly adversarial reviewer
prompt - same model, same diff, reasoning only, no execution - catches what
the neutral prompt missed.

No new diffs are generated and no coding agent runs here. Each of the 13
source traces already carries the exact problem_statement/diff pair its own
neutral review was shown; this module re-sends that same pair to the same
reviewer model under the new prompt. Single fixed prompt, one call per diff,
13 calls total - no variants, no resampling.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from harness import llm

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SOURCE_DIR = ROOT / "results" / "cross_model_review_probe"
RESULTS_DIR = ROOT / "results" / "adversarial_review_probe"

GPT5_MODEL = "gpt-5"
CLAUDE_MODEL = "claude-sonnet-4-6"
GPT5_MAX_TOKENS = 8000  # matches cross_model_review_probe.py: gpt-5 spends hidden reasoning tokens
CLAUDE_MAX_TOKENS = 4000

ADVERSARIAL_PROMPT = """You are reviewing a patch submitted by another engineer as a completion of the
attached ticket. Assume this patch may contain a subtle, incomplete fix — this
is common. Act as an adversarial reviewer: actively search for unhandled edge
cases, alternate code paths, hidden invariants implied by the ticket's domain
(not just its literal example), and assumptions the diff makes that the ticket
does not explicitly rule out. Do not approve the patch unless you can
affirmatively rule these out.

Ticket:
{ticket}

Diff:
{diff}

State your reasoning, then give a verdict on its own final line, exactly one
of: COMPLETE or INCOMPLETE."""


def _source_traces() -> list[Path]:
    return sorted(SOURCE_DIR.glob("trace_*_cross_model_review_worker-*_run1.json"))


def _parse_verdict(raw: str) -> str:
    """Returns 'COMPLETE', 'INCOMPLETE', or 'AMBIGUOUS' (final line didn't
    match either token exactly, case-insensitive, punctuation-stripped)."""
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    if not lines:
        return "AMBIGUOUS"
    last = lines[-1].strip().strip(".:*_ ").upper()
    if last == "COMPLETE":
        return "COMPLETE"
    if last == "INCOMPLETE":
        return "INCOMPLETE"
    return "AMBIGUOUS"


def run_all() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    flipped_notes = []
    anomalies = []

    for src_path in _source_traces():
        src = json.loads(src_path.read_text())
        instance_id = src["instance_id"]
        worker_model = src["worker_model"]
        reviewer_model = src["reviewer_model"]
        problem_statement = src["problem_statement"]
        diff = src["diff"]
        fail_to_pass_passed = src["fail_to_pass_passed"]
        neutral_claimed_success = src["claimed_success"]

        direction = (
            f"{reviewer_model} adversarially reviews {worker_model}'s diffs"
        )

        prompt = ADVERSARIAL_PROMPT.format(ticket=problem_statement, diff=diff)
        max_tokens = GPT5_MAX_TOKENS if reviewer_model == GPT5_MODEL else CLAUDE_MAX_TOKENS

        start = time.time()
        raw, usage = llm.call_llm("", prompt, max_tokens=max_tokens, model=reviewer_model)
        verdict = _parse_verdict(raw)
        if verdict == "AMBIGUOUS":
            anomalies.append(
                f"{instance_id} [worker={worker_model} reviewer={reviewer_model}]: "
                f"final line did not parse as COMPLETE/INCOMPLETE. Raw tail: "
                f"{raw.strip()[-200:]!r}"
            )
        caught = verdict == "INCOMPLETE"

        trace = {
            "instance_id": instance_id,
            "condition": "adversarial_cross_model_review",
            "worker_model": worker_model,
            "reviewer_model": reviewer_model,
            "run_index": 1,
            "problem_statement": problem_statement,
            "diff": diff,
            "adversarial_reasoning_raw": raw,
            "adversarial_verdict": verdict,
            "caught": caught,
            "fail_to_pass_passed": fail_to_pass_passed,
            "neutral_claimed_success": neutral_claimed_success,
            "wall_clock_seconds": round(time.time() - start, 2),
            "estimated_cost_usd": round(usage.cost_usd, 4),
        }
        out_path = (
            RESULTS_DIR
            / f"trace_{instance_id}_adversarial_review_worker-{worker_model}_reviewer-{reviewer_model}_run1.json"
        )
        out_path.write_text(json.dumps(trace, indent=2))

        rows.append(
            {
                "direction": direction,
                "task_id": instance_id,
                "worker_model": worker_model,
                "reviewer_model": reviewer_model,
                "verdict": verdict,
                "caught": "Y" if caught else "N",
            }
        )

        # neutral review on this exact diff was always claimed_success=True
        # (wrongly approved, i.e. missed); a flip is any case now caught.
        if caught:
            flipped_notes.append(
                {
                    "instance_id": instance_id,
                    "direction": direction,
                    "reasoning": raw,
                }
            )

        print(
            f"{instance_id} [worker={worker_model} reviewer={reviewer_model}]: "
            f"verdict={verdict} caught={caught} cost=${usage.cost_usd:.4f}",
            flush=True,
        )

    # --- CSV ---
    csv_path = RESULTS_DIR / "results.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["direction", "task_id", "worker_model", "reviewer_model", "verdict", "caught"]
        )
        writer.writeheader()
        writer.writerows(rows)

    # --- summary table ---
    gpt5_rows = [r for r in rows if r["reviewer_model"] == GPT5_MODEL]
    claude_rows = [r for r in rows if r["reviewer_model"] == CLAUDE_MODEL]
    gpt5_caught = sum(1 for r in gpt5_rows if r["caught"] == "Y")
    claude_caught = sum(1 for r in claude_rows if r["caught"] == "Y")
    total_caught = gpt5_caught + claude_caught

    summary_lines = [
        "| Direction | Runs | Outcome |",
        "|---|---|---|",
        f"| GPT-5 adversarially reviews Claude's diffs | {len(gpt5_rows)} | {gpt5_caught}/{len(gpt5_rows)} caught |",
        f"| Claude adversarially reviews GPT-5's diffs | {len(claude_rows)} | {claude_caught}/{len(claude_rows)} caught |",
        f"| Combined | {len(rows)} | {total_caught}/{len(rows)} caught |",
    ]
    (RESULTS_DIR / "summary_table.md").write_text("\n".join(summary_lines) + "\n")

    # --- flipped-case notes (verbatim reasoning for anything caught) ---
    notes_path = RESULTS_DIR / "flipped_cases_notes.md"
    if flipped_notes:
        parts = ["# Cases that flipped from missed (neutral prompt) to caught (adversarial prompt)\n"]
        for n in flipped_notes:
            parts.append(f"## {n['instance_id']} — {n['direction']}\n")
            parts.append("```\n" + n["reasoning"].strip() + "\n```\n")
        notes_path.write_text("\n".join(parts))
    else:
        notes_path.write_text(
            "# Cases that flipped from missed (neutral prompt) to caught (adversarial prompt)\n\n"
            "None. All 13 cases remained missed (verdict COMPLETE) under the adversarial prompt.\n"
        )

    # --- anomalies ---
    anomalies_path = RESULTS_DIR / "anomalies.md"
    if anomalies:
        anomalies_path.write_text("# Anomalies\n\n" + "\n\n".join(anomalies) + "\n")
    else:
        anomalies_path.write_text("# Anomalies\n\nNone encountered.\n")

    print("\n".join(summary_lines))


if __name__ == "__main__":
    run_all()
