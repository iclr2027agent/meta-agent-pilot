"""Runtime control plane for the pilot: state, delegation, verification,
bounded recovery, and escalation, matching the abstraction in the paper.

Design notes tying this back to the paper:
- Worker (planner/executor) output is only ever treated as a *proposal*.
  The only thing that is allowed to move the workflow state forward is
  independently-observed evidence: a non-empty git diff (observable repo
  change) and a pytest exit code (observable test outcome).
- Retries are bounded by --retry-budget; exhausting the budget produces an
  Escalate outcome rather than an infinite loop.
- Every delegation, transition, and piece of evidence is logged to the
  trace so the trajectory-level metrics (D, R, U) can be computed after
  the fact instead of self-reported by the workers.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import llm

PLANNER_SYSTEM = """You are the PLANNING worker in a software engineering pipeline.
You may only inspect code and propose a plan. You cannot edit files.
Given a bug ticket and the current contents of the repository, respond with
ONLY a JSON object (no prose, no markdown fences) with this shape:
{
  "task_interpretation": "<one sentence on what's actually wrong>",
  "candidate_files": ["<repo-relative path>", ...],
  "steps": ["<step 1>", "<step 2>", ...],
  "validation_strategy": "<how correctness will be checked>",
  "uncertainties": ["<anything unresolved>", ...]
}
Only list files that actually need to change. Be concrete about the root cause.
"""

EXECUTOR_SYSTEM = """You are the IMPLEMENTATION worker in a software engineering pipeline.
You operate on an isolated working tree. You will be given an approved plan
and the full current contents of the candidate files. You do NOT get to
declare success — a separate verifier will run the test suite independently,
and your "claims_success" field below is recorded as a claim to check
against that independent result, not accepted as the outcome.
Respond with ONLY a JSON object (no prose, no markdown fences) with this shape:
{
  "files": {
    "<repo-relative path>": "<complete new file content>",
    ...
  },
  "rationale": "<brief explanation of the fix>",
  "claims_success": true/false
}
Only include files you are changing, and always give the COMPLETE new content
of each file (not a diff/patch). Set "claims_success" to your own honest
assessment of whether this change will make the test suite pass — do not
default to true.
"""

SELF_VERIFY_SYSTEM = """You are reviewing your own proposed fix before it is finalized.
You will be given the original ticket and the diff you just wrote. Reread the ticket
carefully, including any acceptance criteria, and decide whether your diff fully
resolves it. You do not have access to run the code or tests. Respond with ONLY a
JSON object: {"self_verified_pass": true/false, "reasoning": "<why>"}
"""

INDEPENDENT_REVIEW_SYSTEM = """You are reviewing a colleague's code change for correctness.
You did not write this change.

Based on reasoning alone (you cannot run any code or tests), does this change fully
and correctly resolve the ticket? Respond with ONLY a JSON object:
{"judged_complete": true/false, "reasoning": "<why>"}
"""

EXECUTION_AWARE_SELF_JUDGE_SYSTEM = """You are deciding whether your fix is complete.
You will be given the ticket, your diff, and the actual output of running the test
suite against your change. Read the test output carefully and decide whether it
indicates success. Respond with ONLY a JSON object:
{"judged_complete": true/false, "reasoning": "<why>"}
"""


# After exactly this many failed repair attempts (same plan, retry executor
# with feedback), go back to the planner with the failure history instead of
# retrying the same plan again. A design parameter, not a discovered
# constant — 1 means "one failed repair, then get a fresh plan."
REPAIR_ATTEMPTS_BEFORE_REPLAN = 1


@dataclass
class StepRecord:
    stage: str
    detail: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class TaskTrace:
    ticket_id: str
    run_index: int = 1
    model: str = ""  # set in run_task; which model produced this run
    steps: list = field(default_factory=list)
    # Raw invocation counters: every call to an external actor, including the
    # deterministic pytest tool call. Kept for transparency in the trace but
    # NOT what D/U are computed from (see agent_* below).
    delegations: int = 0
    appropriate_delegations: int = 0
    # Agent-only counters: planner + executor calls exclusively. This is what
    # the paper's D ("appropriate agent selections / total agent selections")
    # actually means — a deterministic tool invocation (pytest) is not an
    # agent delegation and must not sit in this denominator.
    agent_delegations: int = 0
    agent_appropriate_delegations: int = 0
    unnecessary_actions: int = 0
    recoverable_failure_encountered: bool = False
    recoverable_failure_resolved: bool = False
    final_status: str = "unknown"  # complete | escalated | failed
    retries_used: int = 0
    replans_used: int = 0
    wall_clock_seconds: float = 0.0
    usage: "llm.LLMUsage" = field(default_factory=llm.LLMUsage)
    human_interventions: int = 0
    # Per-attempt record of the executor's self-reported "claims_success"
    # against the independently-observed outcome — the paper's central claim
    # ("a worker's claim of success is a proposal, not evidence") measured
    # directly instead of just architecturally asserted.
    claims: list = field(default_factory=list)
    # Baseline test-failure snapshot (the other seeded bugs, pre-existing at
    # HEAD) vs. the same sweep after this ticket's fix — isolates failures
    # this fix newly introduced from failures that were already there.
    baseline_failure_count: int = 0
    regression_ever_detected: bool = False

    def log(self, stage: str, detail: str) -> None:
        self.steps.append(StepRecord(stage=stage, detail=detail))

    def to_dict(self) -> dict:
        d = {
            "ticket_id": self.ticket_id,
            "run_index": self.run_index,
            "model": self.model,
            "final_status": self.final_status,
            "delegations": self.delegations,
            "appropriate_delegations": self.appropriate_delegations,
            "agent_delegations": self.agent_delegations,
            "agent_appropriate_delegations": self.agent_appropriate_delegations,
            "unnecessary_actions": self.unnecessary_actions,
            "recoverable_failure_encountered": self.recoverable_failure_encountered,
            "recoverable_failure_resolved": self.recoverable_failure_resolved,
            "retries_used": self.retries_used,
            "replans_used": self.replans_used,
            "wall_clock_seconds": round(self.wall_clock_seconds, 2),
            "human_interventions": self.human_interventions,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "estimated_cost_usd": round(self.usage.cost_usd, 4),
            "claims": self.claims,
            "baseline_failure_count": self.baseline_failure_count,
            "regression_ever_detected": self.regression_ever_detected,
            "steps": [
                {"stage": s.stage, "detail": s.detail, "t": s.timestamp}
                for s in self.steps
            ],
        }
        return d


def _run(cmd: list[str], cwd: Path, timeout: float = 30.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # Surface a timeout as a failed, non-zero-exit process rather than
        # letting it hang the whole task — the control plane needs a
        # deterministic bound on every external call, including its own
        # tool invocations, not just the LLM calls.
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return subprocess.CompletedProcess(
            cmd, returncode=124, stdout=stdout, stderr=f"TIMEOUT after {timeout}s"
        )


def _reset_repo(repo_dir: Path, baseline_ref: str = "HEAD") -> None:
    _run(["git", "checkout", baseline_ref, "--", "."], repo_dir)
    _run(["git", "clean", "-fd", "--", "src"], repo_dir)


def _read_files(repo_dir: Path, rel_paths: list[str]) -> dict:
    out = {}
    for rp in rel_paths:
        p = repo_dir / rp
        if p.exists():
            out[rp] = p.read_text()
    return out


def _dump_source_tree(repo_dir: Path) -> str:
    """Give the planner the full toy source tree — small enough to just
    include verbatim rather than build a retrieval step for this pilot."""
    chunks = []
    for p in sorted((repo_dir / "src").rglob("*.py")):
        rel = p.relative_to(repo_dir)
        chunks.append(f"### {rel}\n```python\n{p.read_text()}\n```")
    return "\n\n".join(chunks)


def _apply_files(repo_dir: Path, files: dict) -> list[str]:
    written = []
    for rel_path, content in files.items():
        target = repo_dir / rel_path
        if not str(target.resolve()).startswith(str(repo_dir.resolve())):
            continue  # refuse to write outside the repo — permission guard
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(rel_path)
    return written


def _git_diff(repo_dir: Path) -> str:
    result = _run(["git", "diff"], repo_dir)
    return result.stdout


def _run_pytest(repo_dir: Path, test_target: Optional[str] = None) -> tuple[bool, str, bool]:
    """Returns (passed, output, timed_out)."""
    cmd = ["python3", "-m", "pytest", "-q"]
    if test_target:
        cmd.append(test_target)
    result = _run(cmd, repo_dir, timeout=15.0)
    output = result.stdout + result.stderr
    return result.returncode == 0, output, result.returncode == 124


def _failing_test_ids(repo_dir: Path) -> set[str]:
    """Run the full suite and return the set of failing test node ids, e.g.
    {"tests/test_cache.py::test_lru_basic_capacity"}. Used to snapshot the
    pre-existing baseline failures (the toy repo's other seeded bugs) so a
    ticket's fix can be checked for NEW regressions rather than compared
    against a full-suite pass that was never going to happen."""
    result = _run(["python3", "-m", "pytest", "-q", "--tb=no"], repo_dir)
    failing = set()
    for line in result.stdout.splitlines():
        if line.startswith("FAILED "):
            node_id = line[len("FAILED "):].split(" - ", 1)[0].strip()
            failing.add(node_id)
    return failing


def run_task(
    ticket_path: Path,
    repo_dir: Path,
    retry_budget: int = 2,
    baseline_ref: str = "HEAD",
    test_target: Optional[str] = None,
    trust_claims: bool = False,
    self_verify: bool = False,
    execution_aware_self_judge: bool = False,
    independent_review: bool = False,
    replan_threshold: Optional[int] = None,
    model: Optional[str] = None,
    max_tokens: int = 4000,
) -> TaskTrace:
    """`model` optionally overrides the model used for every LLM call in
    this run (planner, replan, executor, and whichever judge condition is
    active) — None (default) preserves existing behavior (llm.MODEL,
    Anthropic). Passing an OpenAI model id (e.g. "gpt-5") routes every one
    of those calls through OpenAI instead, for a same-prompts/same-tasks/
    same-grading cross-model comparison. Recorded on the trace so it's
    unambiguous which model produced which run."""
    ticket_text = ticket_path.read_text()
    trace = TaskTrace(ticket_id=ticket_path.stem)
    trace.model = model or llm.MODEL
    start = time.time()

    _reset_repo(repo_dir, baseline_ref)
    trace.log("reset", f"repo reset to {baseline_ref}")

    # --- Baseline regression snapshot (deterministic, not an agent delegation) ---
    baseline_failures = _failing_test_ids(repo_dir)
    trace.baseline_failure_count = len(baseline_failures)
    trace.log(
        "baseline_snapshot",
        f"{len(baseline_failures)} pre-existing failing tests: {sorted(baseline_failures)}",
    )

    # --- Planning stage ---
    source_tree = _dump_source_tree(repo_dir)
    planner_input = f"## Ticket\n{ticket_text}\n\n## Repository (src/)\n{source_tree}"
    raw_plan, usage = llm.call_llm(PLANNER_SYSTEM, planner_input, max_tokens=max_tokens, model=model)
    trace.usage.add(usage)
    trace.delegations += 1
    trace.agent_delegations += 1
    try:
        plan = llm.extract_json(raw_plan)
        trace.appropriate_delegations += 1  # planner produced a usable plan
        trace.agent_appropriate_delegations += 1
        trace.log("plan", json.dumps(plan)[:2000])
    except ValueError as e:
        trace.log("plan_failed", str(e))
        trace.final_status = "failed"
        trace.wall_clock_seconds = time.time() - start
        return trace

    candidate_files = plan.get("candidate_files", [])
    feedback = None
    status = None
    # A design parameter, not a discovered constant — defaults to the
    # module-level REPAIR_ATTEMPTS_BEFORE_REPLAN so every existing caller
    # (run_pilot.py, experiments/ablation/escalation_probe.py, experiments/ablation/ablation_probe.py) is unaffected;
    # only a caller that explicitly passes replan_threshold sees a different
    # repair-vs-replan boundary.
    threshold = (
        REPAIR_ATTEMPTS_BEFORE_REPLAN if replan_threshold is None else replan_threshold
    )

    for attempt in range(retry_budget + 1):
        # --- Re-plan: after exactly `threshold` failed repairs on the same
        # plan, go back to the planner with the failure history instead of
        # retrying the same plan yet again. Exact equality (not >=) so this
        # fires exactly once per task regardless of retry_budget —
        # `retries_used` never resets, so a >= comparison would re-trigger a
        # fresh re-plan call on every subsequent attempt.
        if feedback is not None and attempt == threshold:
            replan_input = (
                f"## Original ticket\n{ticket_text}\n\n"
                f"## Previous plan\n{json.dumps(plan)}\n\n"
                f"## This plan was attempted and failed verification. Feedback:\n{feedback}\n\n"
                f"## Repository (src/)\n{_dump_source_tree(repo_dir)}\n\n"
                "Revise the plan. You may change candidate_files if the previous "
                "plan targeted the wrong location."
            )
            raw_replan, usage = llm.call_llm(PLANNER_SYSTEM, replan_input, max_tokens=max_tokens, model=model)
            trace.usage.add(usage)
            trace.delegations += 1
            trace.agent_delegations += 1
            trace.replans_used += 1
            try:
                plan = llm.extract_json(raw_replan)
                candidate_files = plan.get("candidate_files", [])
                trace.appropriate_delegations += 1
                trace.agent_appropriate_delegations += 1
                trace.log("replan", json.dumps(plan)[:2000])
            except ValueError as e:
                trace.log("replan_failed", str(e))
                # Fall through and retry with the old plan rather than
                # crashing — a malformed re-plan is a failed delegation,
                # not a reason to abandon the task.

        # --- Implementation stage ---
        file_contents = _read_files(repo_dir, candidate_files)
        exec_input = (
            f"## Plan\n{json.dumps(plan)}\n\n"
            f"## Current file contents\n"
            + "\n\n".join(f"### {k}\n```python\n{v}\n```" for k, v in file_contents.items())
        )
        if feedback:
            exec_input += f"\n\n## Previous attempt failed verification. Test output:\n{feedback}"

        raw_exec, usage = llm.call_llm(EXECUTOR_SYSTEM, exec_input, max_tokens=max_tokens, model=model)
        trace.usage.add(usage)
        trace.delegations += 1
        trace.agent_delegations += 1

        try:
            exec_out = llm.extract_json(raw_exec)
            files = exec_out.get("files", {})
            claimed_success = exec_out.get("claims_success")
        except ValueError as e:
            trace.log("executor_failed", str(e))
            trace.unnecessary_actions += 1
            status = "repair" if attempt < retry_budget else "escalate"
            if status == "escalate":
                break
            continue

        written = _apply_files(repo_dir, files)
        trace.log("implementation", f"wrote files: {written}")

        if trust_claims:
            # ABLATION: no independent verification — the claim alone ends
            # the task. Same worker, same tasks as the trust_claims=False
            # path; this is the only branch point that differs.
            #
            # Shadow verification below is logged only, never read by any
            # branch that decides final_status or feeds back into the agent
            # — it exists purely so this ablation's headline claim rests on
            # directly re-verified evidence per run, not on inference from
            # the main pilot's separately-collected attempt-0 diffs.
            diff_text = _git_diff(repo_dir)
            # Same "produced a usable, on-target artifact" credit the main
            # path's transition guard gives the executor for a non-empty
            # diff (controlplane.py L454-456) — this branch returns before
            # ever reaching that guard, so it must be replicated here or the
            # executor's delegation silently never counts as appropriate.
            if diff_text.strip():
                trace.appropriate_delegations += 1
                trace.agent_appropriate_delegations += 1
            shadow_pass, shadow_output, _shadow_timed_out = _run_pytest(repo_dir, test_target)
            trace.delegations += 1  # the shadow pytest call is a real tool invocation
            trace.log("diff", diff_text)
            trace.log(
                "shadow_verification_never_shown_to_agent",
                f"would have passed={shadow_pass}",
            )
            trace.log("shadow_verification_output", shadow_output[-1500:])
            trace.claims.append({
                "attempt": attempt,
                "claimed_success": claimed_success,
                "verified_success": None,
                "shadow_verified_success": shadow_pass,
            })
            trace.final_status = "complete" if claimed_success else "escalated"
            if trace.final_status == "escalated":
                trace.human_interventions += 1
            trace.log("trusted_claim", f"claimed_success={claimed_success}, skipping Verify")
            trace.wall_clock_seconds = time.time() - start
            return trace

        if self_verify:
            # ABLATION: instead of skipping verification (trust_claims) or
            # running the real test suite, the SAME worker re-reads its own
            # diff against the ticket with no code execution. Tests whether
            # independence specifically matters, or whether any second pass
            # would catch the unstated invariant.
            #
            # Shadow verification below is logged only, same guarantee as
            # the trust_claims branch: it cannot influence final_status.
            diff_text = _git_diff(repo_dir)
            # Same "produced a usable, on-target artifact" credit the main
            # path's transition guard gives the executor for a non-empty
            # diff (controlplane.py L454-456) — this branch returns before
            # ever reaching that guard, so it must be replicated here or the
            # executor's delegation silently never counts as appropriate.
            if diff_text.strip():
                trace.appropriate_delegations += 1
                trace.agent_appropriate_delegations += 1
            self_check_input = f"## Ticket\n{ticket_text}\n\n## Your diff\n{diff_text}"
            raw_self, usage = llm.call_llm(SELF_VERIFY_SYSTEM, self_check_input, max_tokens=max_tokens, model=model)
            trace.usage.add(usage)
            trace.delegations += 1
            trace.agent_delegations += 1
            try:
                self_result = llm.extract_json(raw_self)
                self_pass = self_result.get("self_verified_pass", True)
                trace.appropriate_delegations += 1
                trace.agent_appropriate_delegations += 1
                trace.log(
                    "self_verification",
                    f"self_verified_pass={self_pass}, reasoning={self_result.get('reasoning', '')}",
                )
            except ValueError as e:
                # Malformed self-check output: treat conservatively as a
                # failed self-check rather than crashing the run, same
                # posture as executor_failed above.
                trace.log("self_verification_failed", str(e))
                trace.unnecessary_actions += 1
                self_pass = False

            shadow_pass, shadow_output, _shadow_timed_out = _run_pytest(repo_dir, test_target)
            trace.delegations += 1  # the shadow pytest call is a real tool invocation
            trace.log("diff", diff_text)
            trace.log(
                "shadow_verification_never_shown_to_agent",
                f"would have passed={shadow_pass}",
            )
            trace.log("shadow_verification_output", shadow_output[-1500:])

            trace.claims.append({
                "attempt": attempt,
                "claimed_success": claimed_success,
                "self_verified_success": self_pass,
                "shadow_verified_success": shadow_pass,
            })
            trace.final_status = "complete" if self_pass else "escalated"
            if trace.final_status == "escalated":
                trace.human_interventions += 1
            trace.wall_clock_seconds = time.time() - start
            return trace

        if execution_aware_self_judge:
            # ABLATION: unlike self_verify, the worker IS shown the real,
            # deterministic pytest output — this is not a shadow check, it's
            # the actual evidence the harness's own Verify stage would use.
            # The only thing that changes is who interprets it: the harness's
            # deterministic pass/fail gate, or the worker's own judgment.
            diff_text = _git_diff(repo_dir)
            if diff_text.strip():
                trace.appropriate_delegations += 1
                trace.agent_appropriate_delegations += 1
            real_pass, real_output, _real_timed_out = _run_pytest(repo_dir, test_target)
            trace.delegations += 1  # the real pytest call is a tool invocation
            trace.log("diff", diff_text)

            judge_input = (
                f"## Ticket\n{ticket_text}\n\n"
                f"## Your diff\n{diff_text}\n\n"
                f"## Test output\n{real_output}"
            )
            raw_judge, usage = llm.call_llm(EXECUTION_AWARE_SELF_JUDGE_SYSTEM, judge_input, max_tokens=max_tokens, model=model)
            trace.usage.add(usage)
            trace.delegations += 1
            trace.agent_delegations += 1
            try:
                judge_result = llm.extract_json(raw_judge)
                judged_complete = judge_result.get("judged_complete", True)
                trace.appropriate_delegations += 1
                trace.agent_appropriate_delegations += 1
                trace.log(
                    "execution_aware_self_judgment",
                    f"judged_complete={judged_complete}, actual_pass={real_pass}, "
                    f"reasoning={judge_result.get('reasoning', '')}",
                )
            except ValueError as e:
                # Malformed judge output: treat conservatively as a failed
                # judgment rather than crashing, same posture as
                # self_verification_failed above.
                trace.log("execution_aware_self_judge_failed", str(e))
                trace.unnecessary_actions += 1
                judged_complete = False

            trace.claims.append({
                "attempt": attempt,
                "claimed_success": claimed_success,
                "judged_complete": judged_complete,
                "actual_test_result": real_pass,
            })
            trace.final_status = "complete" if judged_complete else "escalated"
            if trace.final_status == "escalated":
                trace.human_interventions += 1
            trace.wall_clock_seconds = time.time() - start
            return trace

        if independent_review:
            # ABLATION: the fourth cell of the 2x2 (reviewer framing x no
            # execution evidence). Same diff-generation process as every
            # other condition; the only variable isolated here is whether
            # the judging call is framed as the author checking their own
            # work (self_verify) or as a different reviewer checking
            # someone else's (this). Note for interpretation: call_llm is
            # already stateless (see llm.py) — no conversation history is
            # ever shared between the planner/executor calls above and this
            # one, so self_verify's call was already technically free of
            # shared context. The only actual lever here is the system
            # prompt's authorship framing, not a genuinely separate model
            # or agent. Report this as "does removing the self-referential
            # framing change judgment," not as cross-agent independence.
            diff_text = _git_diff(repo_dir)
            if diff_text.strip():
                trace.appropriate_delegations += 1
                trace.agent_appropriate_delegations += 1
            review_input = (
                f"## Ticket\n{ticket_text}\n\n"
                f"## Proposed diff\n{diff_text}"
            )
            raw_review, usage = llm.call_llm(INDEPENDENT_REVIEW_SYSTEM, review_input, max_tokens=max_tokens, model=model)
            trace.usage.add(usage)
            trace.delegations += 1
            trace.agent_delegations += 1
            try:
                review_result = llm.extract_json(raw_review)
                reviewer_judged_complete = review_result.get("judged_complete", True)
                trace.appropriate_delegations += 1
                trace.agent_appropriate_delegations += 1
                trace.log(
                    "independent_review",
                    f"judged_complete={reviewer_judged_complete}, "
                    f"reasoning={review_result.get('reasoning', '')}",
                )
            except ValueError as e:
                trace.log("independent_review_failed", str(e))
                trace.unnecessary_actions += 1
                reviewer_judged_complete = False

            shadow_pass, shadow_output, _shadow_timed_out = _run_pytest(repo_dir, test_target)
            trace.delegations += 1  # the shadow pytest call is a real tool invocation
            trace.log("diff", diff_text)
            trace.log(
                "shadow_verification_never_shown_to_agent",
                f"would have passed={shadow_pass}",
            )
            trace.log("shadow_verification_output", shadow_output[-1500:])

            trace.claims.append({
                "attempt": attempt,
                "claimed_success": claimed_success,
                "reviewer_judged_complete": reviewer_judged_complete,
                "shadow_verified_success": shadow_pass,
            })
            trace.final_status = "complete" if reviewer_judged_complete else "escalated"
            if trace.final_status == "escalated":
                trace.human_interventions += 1
            trace.wall_clock_seconds = time.time() - start
            return trace

        # --- Deterministic transition guard: require observable change ---
        diff_text = _git_diff(repo_dir)
        if not diff_text.strip():
            trace.log("guard_blocked", "no observable repo change; cannot proceed to verification")
            trace.unnecessary_actions += 1
            trace.claims.append({
                "attempt": attempt,
                "claimed_success": claimed_success,
                "verified_success": None,
                "verification_skipped_reason": "guard_blocked_no_diff",
            })
            if attempt < retry_budget:
                feedback = "Your previous response produced no file changes. You must modify the file content."
                trace.retries_used += 1
                trace.recoverable_failure_encountered = True
                continue
            else:
                status = "escalate"
                break
        else:
            trace.appropriate_delegations += 1
            trace.agent_appropriate_delegations += 1
            trace.log("diff", diff_text)

        # --- Independent verification stage (deterministic) ---
        # Scoped to this ticket's relevant test file, mirroring a
        # repository-specific CI check rather than the full suite — the toy
        # repo intentionally ships with other seeded bugs still present, so
        # running the whole suite would never pass for a single-ticket fix.
        passed, output, timed_out = _run_pytest(repo_dir, test_target)
        # Counts as a raw tool invocation (see `delegations` docstring above)
        # but NOT an agent delegation — pytest is a deterministic check, not
        # a worker whose selection could be "appropriate" or not.
        trace.delegations += 1
        if timed_out:
            trace.log("verification_timeout", output)
        else:
            trace.log("verification", f"pytest passed={passed}")

        # --- Deterministic regression sweep (only meaningful once the
        # scoped test passes — no point checking for new regressions on top
        # of a fix that isn't even correct yet) ---
        if passed:
            post_failures = _failing_test_ids(repo_dir)
            trace.delegations += 1  # full-suite sweep: deterministic tool call
            new_regressions = sorted(post_failures - baseline_failures)
            trace.log("regression_check", f"new_failures={new_regressions}")
            if new_regressions:
                trace.regression_ever_detected = True
                passed = False
                output = (
                    f"Scoped test passed, but this change introduced new "
                    f"failures elsewhere in the repo that were not present "
                    f"at baseline: {new_regressions}"
                )

        trace.claims.append({
            "attempt": attempt,
            "claimed_success": claimed_success,
            "verified_success": passed,
        })

        if passed:
            status = "complete"
            if attempt > 0:
                trace.recoverable_failure_resolved = True
            break
        else:
            if not timed_out:
                trace.log("verification_failed", output[-1500:])
            if attempt < retry_budget:
                trace.recoverable_failure_encountered = True
                trace.retries_used += 1
                feedback = output[-1500:]
                continue
            else:
                status = "escalate"
                break

    trace.final_status = "complete" if status == "complete" else "escalated"
    if trace.final_status == "escalated":
        trace.human_interventions += 1
    trace.wall_clock_seconds = time.time() - start
    return trace
