# Meta-agent control-plane pilot

A runtime control-plane harness (planner -> executor -> deterministic
verifier, with bounded retry and escalation) built to test one claim: a
worker's claim of success is a proposal, not evidence — only
execution-grounded verification reliably catches an incomplete fix.
That claim has been tested across six experiments, on a synthetic toy repo,
real GitHub issues (SWE-bench), a second model (GPT-5), a graded spectrum
of evidence quality, an independent off-the-shelf agent framework
(SWE-agent), and genuine cross-model review. All six point the same way.

## Layout

- `toy_repo/` — a small `toylib` package with 16 independent seeded bugs,
  one per module, each with a test file that currently fails. It's its own
  git repo (committed, one commit per bug) so each task run resets to the
  same starting point via `git checkout`.
- `tasks/ticket_0N_*.md` — 16 bug tickets written like real issue reports
  (symptom + repro + expected behavior), not "go fix line 12."
  `tasks/manifest.json` maps each ticket to the test file that verifies it.
- `harness/` — the core control plane and every experiment built on top of
  it (see below).
- `swebench_pilot/` — real SWE-bench task fixtures (`instances.json`, a
  `django_repo` checkout used as the working tree for Step A). The
  `django_repo` clone itself is gitignored (reproducible via `git clone`);
  `instances.json` and the small diagnostic files are tracked.
- `swe_agent_baseline/` — the SWE-agent baseline comparison. `SWE-agent/`
  (the upstream clone) and `venv/` are gitignored (reproducible via
  `git clone` + `pip install -e .`); the one file we actually authored
  (`anthropic_top_p_fix.yaml`, a config override for a Claude/litellm
  incompatibility) is tracked.
- `results/`, `logs/` — every experiment's output (trace JSON, `.traj`
  files, `report.json`, raw test output). **Gitignored** — regenerate by
  re-running the harness; nothing here is hand-authored.

### `harness/` — core

- `harness/llm.py` — multi-provider LLM wrapper (Anthropic + OpenAI).
  Tracks token usage/cost per provider. The only place that talks to a
  model — treats worker output as a proposal only, never trusts claimed
  success without independent verification.
- `harness/controlplane.py` — the control plane itself: workflow state,
  constrained delegation (planner can't write files, executor can't
  declare success), a deterministic transition guard (require a non-empty
  git diff before verification), bounded retry/repair, re-planning, and
  escalation when the retry budget is exhausted. Supports five reviewer
  conditions (trust-claims, self-verification, execution-aware judgment,
  independent review, and parameterized replan threshold) and an optional
  `model` override used by the cross-model work. Every step is logged to a
  per-task trace.
- `harness/swebench_agent.py` / `harness/swebench_agent_openai.py` — Step A
  (the tool-use grounding loop that produces a real diff against a live
  checkout) for the real SWE-bench pilot, Anthropic-native and
  OpenAI-native respectively. Step B (real Docker-based verification) lives
  in `run_swebench_pilot.py` and calls the actual `swebench` harness.
- `harness/run_pilot.py` — runs the full 16-ticket synthetic pilot and
  writes `results/summary.json` / `results/summary_table.md`.
- `harness/run_swebench_pilot.py` — orchestrates the real-task pilot
  (Step A -> Step B -> judge) against real SWE-bench instances.
- `harness/dry_run_check.py` — validates the state machine wiring with a
  scripted fake LLM (no API key, no cost, no real model behavior).

### `harness/experiments/` — everything built on top of the core pilot

- `ablation/` — the verification-condition battery: `ablation_probe.py`
  (no verification / trust-claims), `self_verify_probe.py`,
  `execution_aware_probe.py`, `independent_review_probe.py`,
  `replan_threshold_probe.py` (repair-vs-replan boundary sweep),
  `extended_ablation_probe.py` (extends the four core conditions to two
  more tickets), and `escalation_probe.py` (zero retry budget).
- `cross_model/` — `cross_model_probe.py` replicates the core
  self-verification-vs-execution-aware comparison on GPT-5 instead of
  Claude, across both synthetic tickets and real SWE-bench tasks.
  `cross_model_review_probe.py` goes further: a genuinely different model
  reviewing the other model's diff, reasoning only, no execution
  evidence — isolating reviewer-independence from execution-grounding.
- `evidence_gradient/` — `evidence_gradient_probe.py` tests whether
  reviewer reliability scales with how much execution evidence it sees
  (bare pass/fail -> failing test names -> targeted failure detail -> full
  output), or whether it's a cliff between "nothing" and "everything."

Each probe script pulls its diffs/problem statements from already-existing
traces wherever possible (see each script's own docstring) rather than
re-running task-solving — the point of most of these experiments is a new
*judgment* over data already produced, not new agentic work.

## Running it

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...          # only needed for cross-model work
pip install anthropic openai
pip install -e toy_repo
python3 -m harness.dry_run_check      # free, no API key: validates wiring
python3 -m harness.run_pilot          # the core 16-ticket synthetic pilot
```

Individual experiments run the same way, e.g.
`python3 -m harness.experiments.ablation.self_verify_probe` or
`python3 -m harness.experiments.cross_model.cross_model_review_probe`.
The real SWE-bench pilot additionally needs Docker running and
`DOCKER_DEFAULT_PLATFORM=linux/amd64` on ARM64 Macs (the published
SWE-bench images are x86_64-only).

## What these results can honestly support

The synthetic pilot is 16 single-file toy bugs — enough to demonstrate the
control plane's mechanics work end-to-end on real model output, not enough
on its own to make generalization claims about delegation accuracy,
recovery rate, or cost at the scale of a large benchmark. The real-task,
cross-model, evidence-gradient, SWE-agent-baseline, and cross-model-review
experiments each add a different, independent line of evidence for the
core claim rather than substituting for a larger-N synthetic study.

### Known limitation: D cannot distinguish "well-formed" from "correct"

D (delegation accuracy) is computed as appropriate agent selections / total
agent selections, where "appropriate" means the planner produced a usable
plan and the executor produced a parseable, applied diff — not that the
diff was actually correct. A plan that is well-formed but wrong (e.g. the
naive `min(discount, max_discount)` fix that fails a held-out invariant)
still counts as one appropriate delegation under this definition,
identically to a plan that passed verification on the first try. This is
why D=1.00 recurs across the synthetic pilot regardless of whether the
first attempt actually succeeded — D as currently defined is structurally
incapable of showing anything else. Future work could weight D by
verification outcome rather than well-formedness alone.
