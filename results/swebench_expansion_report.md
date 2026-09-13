# SWE-bench Verified Multi-Repo Expansion — Results

Real-task execution probe expanding Section 5.2's single-repo (django, 4 trap-exhibiting
tasks) probe to scikit-learn, matplotlib, astropy, and sympy. Selection was pre-registered
before any diff generation or judgment — see
[swebench_expansion_selection_log.md](swebench_expansion_selection_log.md) for the full
candidate-by-candidate reasoning. This document reports what happened when the 13 selected
tasks were actually run.

Model: `claude-sonnet-4-6` for diff generation and both judge conditions (no cross-model or
adversarial-review conditions were added — out of scope per the handoff).

## 1. Method recap

For each of the 13 selected instances, both conditions were run:

- **self_verification**: a fresh agent explores the real repo (checked out at the instance's
  `base_commit`, no test execution allowed) and produces a diff; the *same* agent then judges
  its own diff's completeness from the issue text and diff alone, no execution evidence.
- **execution_aware_judgment**: an independent fresh agent run (its own diff-generation
  attempt, not the same diff as the self_verification condition) is judged with the real
  FAIL_TO_PASS/PASS_TO_PASS test output from the swebench harness shown to it.

Each condition gets its **own independent diff-generation run**. This was verified, not
assumed: `run_swebench_pilot.py`'s `run_one()` calls `step_a` (diff generation) fresh for each
`(instance, condition)` pair, and checking the *actual* trace files from the original django
run confirms this is what happened in practice — the `self_verification` and
`execution_aware_judgment` diffs differ in content for every one of the 6 original instances
that produced a diff at all (django-12273/13128/13346/13512/14140 all have two distinct diffs;
django-14238 has `diff: null` in both, the excluded no-diff case). So this expansion did not
change the harness's design — it replicated it faithfully.

**This does mean, however, that Section 5.2's own prose ("a fresh agent produces a diff...
then the diff is judged under two conditions") describes the harness inaccurately** — the
actual code and data show two independently-generated diffs per instance, not one diff judged
twice. That inaccuracy predates this expansion; it should be corrected in the paper's
Section 5.2 text to match what the harness and the original trace files actually show,
independent of anything done here.

Practically, this means the two conditions' ground-truth correctness can differ on the same
instance (and did, once, in this expansion: sympy-19783), and — more importantly for how the
headline numbers should be read — **part of the self-review vs. execution-aware detection-rate
gap could reflect diff-quality variance across independent generation attempts, not purely a
difference in judgment quality**. The two conditions are not reviewing the same artifact. This
is a real limitation of the existing (not newly introduced) design, and it applies equally to
the original 4-task django result, which used the identical per-condition-independent-diff
setup. A stronger design for isolating judgment quality alone would generate one diff per
instance and have both conditions judge that same diff — worth considering for a future
revision of both probes, but changing it now would make this expansion's numbers
non-comparable to Section 5.2's, so it was not done here without checking first.

Ground truth ("real tasks with incorrect first patch") is the swebench harness's real
FAIL_TO_PASS + PASS_TO_PASS results — never shown during self-verification, always shown
during execution-aware judgment.

## 2. Headline results

| Verification | Real tasks with incorrect first patch | Detected as incomplete | Detection rate |
|---|---|---|---|
| Self-review (reasoning only) | 9 / 13 | 5 | **55.6%** |
| Execution evidence | 8 / 13 | 8 | **100.0%** |

(Denominators differ — 9 vs. 8 — because each condition ran its own independent diff attempt;
see Section 1.)

### Per-repo breakdown

| Repo | Self-review: incorrect / detected / rate | Execution-aware: incorrect / detected / rate |
|---|---|---|
| astropy | 3 / 1 / 33.3% | 3 / 3 / 100.0% |
| matplotlib | 2 / 2 / 100.0% | 2 / 2 / 100.0% |
| scikit-learn | 0 / — / n/a (no incorrect patches) | 0 / — / n/a |
| sympy | 4 / 2 / 50.0% | 3 / 3 / 100.0% |

Execution-aware judgment hit 100% detection in every repo where there was anything to detect.
Self-review's miss rate is concentrated in astropy (2 of 3 incorrect patches missed) and sympy
(2 of 4 missed) — the two repos this project's own pre-registration (Section 3a of the
selection log) flagged for extra scrutiny going in, for an unrelated reason (their held-out
tests skew toward being too weak, per UTBoost's audit) — the fact that they also show the
strongest self-review misses here is a new observation, not something that was predicted.
matplotlib's incorrect patches were both self-caught by reasoning alone (see Section 4).
scikit-learn produced no incorrect patches at all across either condition — both selected
tasks were resolved cleanly on every attempt (see Section 4, "boundary" cases).

## 3. Per-task detail

| Instance | Self-review: diff correct? | Self-review verdict | Exec-aware: diff correct? | Exec-aware verdict |
|---|---|---|---|---|
| astropy-12907 | ✅ resolved | ✅ complete (correct) | ✅ resolved | ✅ complete (correct) |
| astropy-13033 | ❌ incorrect | ❌ **complete (MISS)** | ❌ incorrect | ✅ incomplete (correct) |
| astropy-13977 | ❌ incorrect | ❌ **complete (MISS)** | ❌ incorrect | ✅ incomplete (correct) |
| astropy-14369 | ❌ incorrect | ✅ incomplete (correct) | ❌ incorrect | ✅ incomplete (correct) |
| matplotlib-20676 | ❌ incorrect | ✅ incomplete (correct) | ❌ incorrect | ✅ incomplete (correct) |
| matplotlib-22865 | ✅ resolved | ✅ complete (correct) | ✅ resolved | ✅ complete (correct) |
| matplotlib-24870 | ❌ incorrect | ✅ incomplete (correct) | ❌ incorrect | ✅ incomplete (correct) |
| scikit-learn-13142 | ✅ resolved | ✅ complete (correct) | ✅ resolved | ✅ complete (correct) |
| scikit-learn-25102 | ✅ resolved | ❌ **incomplete (false alarm)** | ✅ resolved | ✅ complete (correct) |
| sympy-13091 | ❌ incorrect | ✅ incomplete (correct) | ❌ incorrect | ✅ incomplete (correct) |
| sympy-16597 | ❌ incorrect | ❌ **complete (MISS)** | ❌ incorrect | ✅ incomplete (correct) |
| sympy-17630 | ❌ incorrect | ❌ **complete (MISS)** | ❌ incorrect | ✅ incomplete (correct) |
| sympy-19783 | ❌ incorrect | ✅ incomplete (correct) | ✅ resolved | ✅ complete (correct) |

Bolded outcomes are the two error modes worth naming explicitly:

- **Miss** (4 instances: astropy-13033, astropy-13977, sympy-16597, sympy-17630) — self-review
  judged an actually-incorrect patch complete. This is the paper's central phenomenon,
  reproduced on 4 of 13 new tasks across 3 repos beyond django.
- **False alarm** (1 instance: scikit-learn-25102, self-review only) — the opposite error:
  self-review judged an actually-*correct* patch incomplete. Worth reporting since it's real
  data, but it is not the phenomenon under study (over-caution, not overconfidence), and
  execution-aware judgment on the same instance correctly recognized the patch as complete.

## 4. Boundary and excluded-outcome notes

Per the original probe's precedent (django-13128 boundary, django-14238 excluded), every
non-trap outcome is reported here rather than folded silently into the headline numbers.

- **Boundary — fix was complete on the first attempt, both conditions, no incompleteness to
  detect at all**: `astropy-12907`, `matplotlib-22865`, `scikit-learn-13142`. These 3 tasks
  did not exhibit the trap in this run: the agent's diff-generation attempt resolved the issue
  correctly every time it was tried, so neither judge condition had a real gap to fall into.
  This does not mean these tasks *can't* exhibit the trap (a different diff attempt might), but
  it wasn't observed here.
- **Self-correction — the diff was genuinely incomplete, and self-review (reasoning alone,
  no execution evidence) still caught it**: `astropy-14369`, `matplotlib-20676`,
  `matplotlib-24870`, `sympy-13091`, `sympy-19783` (self-review condition only, for
  `sympy-19783` — its execution-aware condition's independent diff attempt happened to
  resolve correctly). 5 instances. This is a real, honest outcome that doesn't fit either of
  the original probe's two non-trap categories (boundary / excluded) — it's neither "the fix
  was complete" nor "no diff was produced." It shows self-review is not powerless in general;
  it failed specifically on 4 of the 9 instances where it had an actually-incorrect diff to
  judge, not on all of them.
- **False alarm**: `scikit-learn-25102` (self-review only) — see Section 3.
- **Excluded (agent failed to produce a diff)**: none. All 26 diff-generation attempts
  (13 instances × 2 conditions) produced a submitted diff within budget — unlike the original
  probe's `django-14238`, no instance in this expansion hit the tool-call/turn budget without
  submitting.

## 5. Cost and wall-clock

Total across all 26 diff-generation + judgment runs (one API-key credit exhaustion mid-run
required a pause and resume — see the debug logs for the exact interruption point; no runs
were re-attempted beyond the interrupted one, which resumed cleanly since traces are written
to disk immediately per task):

- **Total estimated cost**: $7.55
- **Total wall-clock**: 50.5 minutes (sum of per-task times; tasks ran sequentially, not in
  parallel, so this is also the real elapsed compute time)
- Cost per task ranged from $0.037 (a short grounding run) to $1.09 (sympy-13091's
  self-verification, the most textually complex candidate — a 21-file gold-patch scope).

## 6. Selection-to-outcome attrition

| Stage | Count |
|---|---|
| Verified instances screened in detail (Section 3b of selection log) | 39 |
| Selected (pre-registered) | 13 |
| Diff-generation attempts run | 26 (13 × 2 conditions) |
| Attempts that failed to produce any diff | 0 |
| Instances showing the trap (self-review miss) | 4 |
| Instances resolved cleanly on first try (boundary) | 3 |
| Instances where self-review caught its own incomplete fix unaided | 5 |
| Instances with a false alarm (correct patch, wrongly flagged) | 1 |

## 7. Summary

13 pre-registered tasks across 4 repositories (astropy, matplotlib, scikit-learn, sympy) were
run through the full diff-generation → self-review vs. execution-aware-judgment → real-test
pipeline, for 26 total attempts. Pooled, self-review caught only 5 of 9 actually-incorrect
patches (55.6%) while execution-aware judgment caught 8 of 8 (100%) — the same qualitative gap
reported in the original single-repo (django) probe, now observed across 3 of 4 new repos
(astropy: 33.3% vs. 100%; sympy: 50.0% vs. 100%; matplotlib: 100% vs. 100%, no gap observed).
scikit-learn produced no incorrect patches at all in this run, so it contributes no evidence
either way. The pattern is not uniform across repos — matplotlib's 2 incorrect patches were
both self-caught by reasoning alone, and scikit-learn showed nothing to catch — so this data
supports "the phenomenon generalizes beyond django, but is not repo-uniform," not "the
phenomenon is repo-independent." Framing that claim for the manuscript is left to whoever
integrates this into the paper, per the handoff's non-goals.

One methodological caveat applies to this result and equally to the original Section 5.2
result, since both use the same harness design (see Section 1): each condition judges its own
independently-generated diff rather than both conditions judging one shared diff, so part of
the measured gap could reflect diff-quality variance across generation attempts rather than
judgment-quality alone. This is a pre-existing property of the harness, not something this
expansion changed, but it should be stated alongside the headline numbers wherever they're
used, and Section 5.2's own prose describing "the diff... judged under two conditions" should
be corrected to match what the harness (and its original trace data) actually did.
