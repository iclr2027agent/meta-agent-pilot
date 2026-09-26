# Meta-agent control-plane pilot

A runtime control-plane harness (planner -> executor -> deterministic
verifier, with bounded retry and escalation) built to test one claim: a
worker's claim of success is a proposal, not evidence — only
execution-grounded verification reliably catches an incomplete fix.
That claim has been tested across seven experiments, on a synthetic toy
repo, real GitHub issues (SWE-bench, django), a second model (GPT-5), a
graded spectrum of evidence quality, an independent off-the-shelf agent
framework (SWE-agent), genuine cross-model review, and a multi-repository
expansion of the real-task probe (scikit-learn, matplotlib, astropy,
sympy). All seven point the same way: reasoning alone, under a neutral
review prompt, misses incomplete fixes that execution evidence catches.

An eighth experiment complicates that picture rather than confirming it:
an adversarial-prompt re-review of the cross-model data (same model, same
diff, reasoning only, no execution, but told to actively hunt for gaps)
caught 8 of the 13 diffs the neutral prompt missed entirely — a
substantial improvement from prompting alone, asymmetric by reviewer
(GPT-5 reviewing Claude's diffs: 5/6 caught; Claude reviewing GPT-5's
diffs: 3/7 caught). See `results/adversarial_review_probe/pattern_note.md`
for the full breakdown. This doesn't overturn the core claim — reasoning
still fell short of execution evidence's 100% — but it means "reasoning
alone can't catch it" needs the qualifier "under a neutral prompt."

A ninth experiment complicates the SWE-agent-baseline comparison
specifically: that comparison originally let our harness's
`execution_aware_judgment` see the real, held-out test assertions
SWE-bench uses for grading, while SWE-agent — working from a plain local
checkout — structurally couldn't. Re-running the judge (same 3 diffs, same
problem statements, no new Step A/B work) with that held-out signal
withheld — leaving only what a local `git diff` + locally-runnable
regression tests would show — drops its catch rate on those 3 diffs from
3/3 to 1/3, identical to plain `self_verification`'s 1/3 on the same diffs.
The two false-accepts reason the same way SWE-agent's own self-testing did
when it missed the same instances: a clean local regression run read as
confirmation of a fix that a file it never touched (or an assertion it
never saw) still failed. See
`results/swe_agent_baseline_visibility_matched/report.md` for the full
breakdown. This means the SWE-agent-baseline comparison shouldn't be read
as "the control-plane architecture beats SWE-agent" without a visibility
qualifier: under matched blindness to the held-out assertions, the
architectural difference contributes nothing measurable on this sample —
it's the held-out execution evidence itself doing the work, not the
architecture independent of what evidence it's given.

A tenth experiment revisits the evidence-quality gradient's claim that the
dip's *location* (Level 2 for GPT-5, Level 3 for Claude) is model-dependent
rather than the same phenomenon landing at different levels by chance —
previously argued by eyeballing two separate tables' confidence intervals,
not by a real test. Pooling every replicate trial for both models (147
trials across 13 underlying tasks, adding fresh Level 1-4 replication on
Claude's two real-repo tasks to match GPT-5's existing 3-per-level design)
and testing the level×model interaction directly: a mixed-effects logistic
fit is directionally consistent with the claim (GPT-5 relatively worse at
Level 2, relatively better at Level 3 — the opposite of Claude's pattern),
but a permutation test on the same interaction is not significant at
conventional thresholds (p ≈ 0.06-0.07 across two reasonable test
statistics). See `results/evidence_gradient_pooled_reanalysis_report.md`
for the full analysis and the exact rewrite this implies for the paper's
Section 9: the dip's *existence* in both models is solid (every replicated
cell shows it); its *location* being model-dependent is suggestive, not
established, at this sample size.

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
- `swebench_pilot/` — real SWE-bench task fixtures for the original
  single-repo (django) probe (`instances.json`, a `django_repo` checkout
  used as the working tree for Step A). The `django_repo` clone itself is
  gitignored (reproducible via `git clone`); `instances.json` and the small
  diagnostic files are tracked.
- `swebench_expansion/` — the multi-repo expansion of the real-task probe
  to scikit-learn, matplotlib, astropy, and sympy (see
  `results/swebench_expansion_selection_log.md` for the pre-registered
  candidate selection and `results/swebench_expansion_report.md` for
  results). `repos/` (the four cloned checkouts), `logs/` (harness run
  logs), `smoke_test/`, and `full_run.log` are gitignored, same rationale
  as `django_repo/`; `instances.json` (pulled from `SWE-bench/SWE-bench_Verified`
  with the fields this swebench version's harness requires) and
  `pull_images.sh` (pre-pulls the 13 instance Docker images with the
  correct platform — see the report's Section 1 for why that's needed on
  Apple Silicon) are tracked.
- `swe_agent_baseline/` — the SWE-agent baseline comparison. `SWE-agent/`
  (the upstream clone) and `venv/` are gitignored (reproducible via
  `git clone` + `pip install -e .`); the one file we actually authored
  (`anthropic_top_p_fix.yaml`, a config override for a Claude/litellm
  incompatibility) is tracked. `results/swe_agent_baseline_visibility_matched/`
  is the follow-up correction re-judging the same diffs under SWE-agent's
  own (blinder) evidence visibility — see the ninth-experiment paragraph
  above and that directory's `report.md`.
- `results/` — every experiment's output (trace JSON, `.traj` files,
  `report.json`, raw test output, selection/results write-ups). Tracked.
- `logs/` — harness run logs. **Gitignored** — regenerate by re-running the
  harness; nothing here is hand-authored.

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
  (Step A -> Step B -> judge) against real SWE-bench django instances.
- `harness/run_swebench_expansion.py` — the same Step A -> Step B -> judge
  design, generalized to resolve each instance's repo dynamically
  (scikit-learn, matplotlib, astropy, sympy) instead of a single hardcoded
  checkout; skips any `(instance, condition)` pair that already has a trace
  on disk, so an interrupted run (e.g. an API-credit exhaustion mid-sweep)
  resumes cleanly on re-invocation.
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
  `adversarial_review_probe.py` re-sends the same 13 diff/ticket pairs from
  `cross_model_review_probe.py` under an explicitly adversarial prompt
  (same model, same diff, reasoning only) to test whether prompting alone
  — no execution evidence — recovers what the neutral prompt missed; no
  new diffs are generated, only the reviewer call changes.
- `evidence_gradient/` — `evidence_gradient_probe.py` tests whether
  reviewer reliability scales with how much execution evidence it sees
  (bare pass/fail -> failing test names -> targeted failure detail -> full
  output), or whether it's a cliff between "nothing" and "everything."
  `evidence_gradient_probe_claude.py` is the same construction run with
  Claude as reviewer instead of GPT-5.
- `swe_agent_baseline/` — `visibility_matched_probe.py` re-runs
  `execution_aware_judgment` on the 3 real-task pilot diffs shared with the
  SWE-agent-baseline comparison, replacing the real (held-out-assertion-
  leaking) test output with a synthetic local-test-runner transcript
  containing only the `PASS_TO_PASS` regression result — matching the
  visibility SWE-agent actually had. No new diffs, no Docker re-run, only
  the reviewer's input changes.

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

The real SWE-bench pilots (`run_swebench_pilot.py`, `run_swebench_expansion.py`)
additionally need Docker running, since the published SWE-bench images are
x86_64-only. On ARM64 Macs, setting `DOCKER_DEFAULT_PLATFORM=linux/amd64`
is not sufficient on its own — the installed `swebench` package (5.0.2)
calls the Docker SDK's `client.images.pull(image)` with no explicit
platform argument, which resolves to the host's native arch (arm64) and
404s on these x86_64-only images regardless of that env var. Pre-pull each
instance's image explicitly first:
`docker pull --platform linux/amd64 <image>` (image names are in each
instance's `image` field in `instances.json`; `swebench_expansion/pull_images.sh`
does this for the expansion's 13 instances) — the harness's own
`images.get()` check then finds it already present and never calls
`pull()` at all.

Both real-task orchestrators write each `(instance, condition)` trace to
disk as soon as it completes, so no completed work is lost if a run is
interrupted (API credit exhaustion, a killed process). Only
`run_swebench_expansion.py` additionally skips any pair that already has a
trace on disk, so re-running the same command after an interruption
resumes from where it stopped; `run_swebench_pilot.py` does not have this
check and will redo already-completed pairs if re-invoked.

## What these results can honestly support

The synthetic pilot is 16 single-file toy bugs — enough to demonstrate the
control plane's mechanics work end-to-end on real model output, not enough
on its own to make generalization claims about delegation accuracy,
recovery rate, or cost at the scale of a large benchmark. The real-task,
cross-model, evidence-gradient, SWE-agent-baseline, cross-model-review,
adversarial-review, and multi-repo-expansion experiments each add a
different, independent line of evidence for the core claim rather than
substituting for a larger-N synthetic study.

D as reported (`appropriate_delegations / delegations`) is 1.00 across
every condition because it only asks whether a delegation was well-formed —
a parseable plan, a non-empty diff — not whether the resulting fix was ever
verified correct. `score_dprime.py` re-scores the same trace files against
the ground-truth pass/fail signal already recorded per attempt
(`verified_success`, or the run-blind `shadow_verified_success` /
`actual_test_result` the ablation conditions log without showing the
worker) into a verification-weighted D' (0.5 credit for a well-formed but
wrong attempt that a later attempt in the same run fixed, 0 credit if it
never got fixed) and a U' that flags a same-plan repair (real plan
identity, tracked from the "plan"/"replan" step log, not inferred from
diff similarity) that follows an already-failed attempt and still fails.
D' diverges materially from D=1.00: 0.83 on the main pilot, down to 0.50 on
the trust_claims/self_verify/independent_review ablations and 0.00 on the
execution_aware_self_judge and escalation probes (both single-attempt by
design).

U' stays 0.00 everywhere, but that's only a meaningful check where a
same-plan repair is structurally possible: the default `replan_threshold=1`
always re-plans before the next attempt, so most of the runs above give U'
zero *opportunities* to fire. The one condition that does allow it —
`replan_threshold=2` (attempt 1 reuses attempt 0's plan verbatim after real
failure feedback) — was run on 4 tickets × 3 runs = 12 runs; all 12
same-plan repairs resolved the ticket on the next attempt, giving 12 real
resubmission opportunities and 0 U' hits. So U'=0.00 there is a tested
negative result (the "wasted repair" failure mode wasn't observed in 12
opportunities), not an artifact of the condition being unable to produce
it. Full table and methodology: `results/dprime/summary_table.md`.

Each of the four ablation conditions above (`trust_claims`, `self_verify`,
`execution_aware_self_judge`, `independent_review`) was run on all four
recovery tickets (07/08/13/15), 12 runs each — three of them split across
two directories (`results/<condition>_probe/` for tickets 07/08 plus the
matching condition inside `results/extended_ablation_probe/` for tickets
13/15), which is easy to undercount by scoring only the dedicated
directory. See "Run-count correction" in `results/dprime/summary_table.md`
for how that was checked.
