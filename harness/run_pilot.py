"""Run the full 5-task pilot, each ticket repeated RUNS_PER_TICKET times to
account for LLM output stochasticity, and produce results/summary.json +
results/summary_table.md.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    cd pilot
    python3 -m harness.run_pilot

Requires: pip install anthropic (and the toy_repo installed editable —
see toy_repo/pyproject.toml).
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from . import controlplane

ROOT = Path(__file__).resolve().parent.parent
REPO_DIR = ROOT / "toy_repo"
TASKS_DIR = ROOT / "tasks"
RESULTS_DIR = ROOT / "results"

RUNS_PER_TICKET = 3


def main(retry_budget: int = 2, runs_per_ticket: int = RUNS_PER_TICKET) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    # Clear stale output from any previous run so old and new trace files
    # never mix (e.g. a previous run's file layout or a different
    # runs_per_ticket leaving orphaned traces behind).
    for f in RESULTS_DIR.glob("*"):
        if f.is_file():
            f.unlink()
    tickets = sorted(TASKS_DIR.glob("ticket_*.md"))
    if not tickets:
        raise SystemExit(f"No tickets found in {TASKS_DIR}")
    manifest = json.loads((TASKS_DIR / "manifest.json").read_text())

    traces = []
    for ticket in tickets:
        test_target = manifest.get(ticket.stem)
        for run_idx in range(1, runs_per_ticket + 1):
            print(f"=== Running {ticket.name} (run {run_idx}/{runs_per_ticket}) ===")
            trace = controlplane.run_task(
                ticket, REPO_DIR, retry_budget=retry_budget, test_target=test_target
            )
            trace.run_index = run_idx
            traces.append(trace)
            out_path = RESULTS_DIR / f"trace_{trace.ticket_id}_run{run_idx}.json"
            out_path.write_text(json.dumps(trace.to_dict(), indent=2))
            print(f"  -> {trace.final_status} in {trace.wall_clock_seconds:.1f}s "
                  f"(${trace.usage.cost_usd:.4f}, {trace.retries_used} retries)")

    # Reset repo to baseline once more so the pilot is idempotent/re-runnable.
    controlplane._reset_repo(REPO_DIR)

    n = len(traces)
    n_success = sum(1 for t in traces if t.final_status == "complete")
    times = [t.wall_clock_seconds for t in traces]
    human_actions = [t.human_interventions for t in traces]
    costs = [t.usage.cost_usd for t in traces]
    success_costs = [t.usage.cost_usd for t in traces if t.final_status == "complete"]

    # D and U are computed over agent delegations only (planner + executor
    # calls). The deterministic pytest verification call is a raw tool
    # invocation, tracked separately in `delegations`/`total_tool_invocations`
    # for transparency, but it is not an "agent selection" under the paper's
    # own definition of D and must not sit in either metric's denominator.
    total_tool_invocations = sum(t.delegations for t in traces)
    total_agent_delegations = sum(t.agent_delegations for t in traces)
    total_agent_appropriate = sum(t.agent_appropriate_delegations for t in traces)
    total_recoverable = sum(1 for t in traces if t.recoverable_failure_encountered)
    total_recovered = sum(1 for t in traces if t.recoverable_failure_resolved)
    total_unnecessary = sum(t.unnecessary_actions for t in traces)
    total_regressions_detected = sum(1 for t in traces if t.regression_ever_detected)
    total_replans = sum(t.replans_used for t in traces)

    # Claimed-success vs. independently-verified-success: the paper's central
    # thesis ("a worker's claim of success is a proposal, not evidence")
    # measured directly. Only claims that were actually checked against a
    # real pytest result count — a guard-blocked attempt never reached
    # verification, so it has no ground truth to compare against.
    all_claims = [c for t in traces for c in t.claims]
    checked_claims = [c for c in all_claims if c["verified_success"] is not None]
    unset_claims = [c for c in checked_claims if c["claimed_success"] is None]
    comparable_claims = [c for c in checked_claims if c["claimed_success"] is not None]
    claim_mismatches = [
        c for c in comparable_claims if c["claimed_success"] != c["verified_success"]
    ]

    by_ticket = defaultdict(list)
    for t in traces:
        by_ticket[t.ticket_id].append(t)
    per_ticket = []
    for ticket_id, ts in sorted(by_ticket.items()):
        ts_times = [t.wall_clock_seconds for t in ts]
        ts_costs = [t.usage.cost_usd for t in ts]
        per_ticket.append({
            "ticket_id": ticket_id,
            "runs": len(ts),
            "success_rate": sum(1 for t in ts if t.final_status == "complete") / len(ts),
            "median_time_seconds": statistics.median(ts_times),
            "mean_cost_usd": statistics.mean(ts_costs),
            "total_retries": sum(t.retries_used for t in ts),
            "any_recoverable_failure": any(t.recoverable_failure_encountered for t in ts),
        })

    summary = {
        "n_tasks": n,
        "runs_per_ticket": runs_per_ticket,
        "success": n_success,
        "success_rate": n_success / n,
        "median_time_seconds": statistics.median(times),
        "mean_human_actions": statistics.mean(human_actions),
        "cost_per_success_usd": (
            sum(success_costs) / len(success_costs) if success_costs else None
        ),
        "total_cost_usd": sum(costs),
        "total_tool_invocations": total_tool_invocations,
        "total_agent_delegations": total_agent_delegations,
        "delegation_accuracy_D": (
            total_agent_appropriate / total_agent_delegations if total_agent_delegations else None
        ),
        "recovery_rate_R": (
            total_recovered / total_recoverable if total_recoverable else None
        ),
        "unnecessary_action_rate_U": (
            total_unnecessary / total_agent_delegations if total_agent_delegations else None
        ),
        "regressions_detected": total_regressions_detected,
        "total_replans": total_replans,
        "claims_checked": len(checked_claims),
        "claims_with_no_self_report": len(unset_claims),
        "claim_verified_mismatches": len(claim_mismatches),
        "claim_verified_mismatch_rate": (
            len(claim_mismatches) / len(comparable_claims) if comparable_claims else None
        ),
        "per_ticket": per_ticket,
        "per_run": [t.to_dict() for t in traces],
    }
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

    table = [
        "| Ticket | Run | Status | Time (s) | Retries | Re-plans | Cost ($) |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in traces:
        table.append(
            f"| {t.ticket_id} | {t.run_index} | {t.final_status} | {t.wall_clock_seconds:.1f} "
            f"| {t.retries_used} | {t.replans_used} | {t.usage.cost_usd:.4f} |"
        )
    table.append("")
    table.append("| Ticket | Runs | Success rate | Median time (s) | Mean cost ($) |")
    table.append("|---|---|---|---|---|")
    for pt in per_ticket:
        table.append(
            f"| {pt['ticket_id']} | {pt['runs']} | {pt['success_rate']*100:.0f}% "
            f"| {pt['median_time_seconds']:.1f} | {pt['mean_cost_usd']:.4f} |"
        )
    table.append("")
    table.append(
        f"**Success: {n_success}/{n} ({summary['success_rate']*100:.1f}%)** across "
        f"{len(tickets)} tickets x {runs_per_ticket} runs  "
    )
    table.append(f"Median time: {summary['median_time_seconds']:.1f}s  ")
    table.append(f"Mean human actions: {summary['mean_human_actions']:.2f}  ")
    if summary["cost_per_success_usd"] is not None:
        table.append(f"Cost/success: ${summary['cost_per_success_usd']:.4f}  ")
    table.append(
        f"D (delegation accuracy, agent-only): {summary['delegation_accuracy_D']:.2f}  "
        if summary["delegation_accuracy_D"] is not None else "D: n/a  "
    )
    table.append(
        f"R (recovery rate): {summary['recovery_rate_R']:.2f}  "
        if summary["recovery_rate_R"] is not None else "R: n/a (no recoverable failures encountered)  "
    )
    table.append(
        f"U (unnecessary-action rate, agent-only): {summary['unnecessary_action_rate_U']:.2f}  "
        if summary["unnecessary_action_rate_U"] is not None else "U: n/a  "
    )
    table.append(f"Regressions detected (new failures beyond baseline): {summary['regressions_detected']}  ")
    table.append(f"Re-plans used: {summary['total_replans']}  ")
    table.append(
        f"Claim/verification mismatch: {summary['claim_verified_mismatches']}/"
        f"{len(comparable_claims)} checked claims "
        f"({summary['claim_verified_mismatch_rate']*100:.1f}%)  "
        if summary["claim_verified_mismatch_rate"] is not None
        else f"Claim/verification mismatch: n/a ({summary['claims_with_no_self_report']} claims had no self-report)  "
    )
    (RESULTS_DIR / "summary_table.md").write_text("\n".join(table))
    print("\n".join(table))


if __name__ == "__main__":
    main()
