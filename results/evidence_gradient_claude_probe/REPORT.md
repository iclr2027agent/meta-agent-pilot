# Evidence-Quality Gradient Replication on Claude (Section 5.4 replication)

Replicates the GPT-5 evidence-quality gradient result (`harness/experiments/evidence_gradient/evidence_gradient_probe.py`,
`results/evidence_gradient_probe/`) on Claude as the reviewer model, using the same five-level construction, the same
reviewer system prompt, and — with one flagged exception — the same reuse-existing-data policy for Levels 0 and 4.

Reviewer model for all new calls: `claude-sonnet-4-6` (same model that produced the pre-existing Level 0 / Level 4
traces reused here, so "same reviewer model throughout" holds). New calls were made via the project's own
`harness/llm.py` → real Anthropic API, not fabricated and not routed through GPT-5 anywhere.

## 1. Eligible tasks (14 total)

An eligible task is one where Claude's own self-verification (Level 0, reasoning alone, no test evidence) confidently
claimed a diff was complete when it was not (confirmed by real, deterministic test execution never shown to the
solving agent). All 14 below were checked and confirmed as genuine Level-0 misses before being included — see
`eligible_tasks_and_evidence_levels.json` for the full record.

| # | Task | Run | Source | Level-0 (self-verification) result |
|---|---|---|---|---|
| 1 | ticket_07_discounts | 1 | main pilot ablations (§5.1, discount-cap recovery task) | claimed complete, actually incomplete |
| 2 | ticket_07_discounts | 2 | main pilot ablations | claimed complete, actually incomplete |
| 3 | ticket_07_discounts | 3 | main pilot ablations | claimed complete, actually incomplete |
| 4 | ticket_08_progress | 1 | main pilot ablations (progress-cap recovery task) | claimed complete, actually incomplete |
| 5 | ticket_08_progress | 2 | main pilot ablations | claimed complete, actually incomplete |
| 6 | ticket_08_progress | 3 | main pilot ablations | claimed complete, actually incomplete |
| 7 | ticket_13_eligibility | 1 | main pilot ablations (age-eligibility recovery task) | claimed complete, actually incomplete |
| 8 | ticket_13_eligibility | 2 | main pilot ablations | claimed complete, actually incomplete |
| 9 | ticket_13_eligibility | 3 | main pilot ablations | claimed complete, actually incomplete |
| 10 | ticket_15_timeout | 1 | main pilot ablations (request-timeout recovery task) | claimed complete, actually incomplete |
| 11 | ticket_15_timeout | 2 | main pilot ablations | claimed complete, actually incomplete |
| 12 | ticket_15_timeout | 3 | main pilot ablations | claimed complete, actually incomplete |
| 13 | django\_\_django-13512 | 1 | real-repository probe (§5.2, JSONField unicode display bug) | claimed complete, actually incomplete |
| 14 | django\_\_django-14140 | 1 | real-repository probe (§5.2, `Q.deconstruct()` bug) | claimed complete, actually incomplete |

No real-task-expansion work existed yet at the time this ran (checked: no such directory/results present), and no new
tasks were manufactured — the pool above is exactly what prior, already-completed Claude probes made eligible. This
lands at 14, above the 7 GPT-5 used and within the 10–15 target.

## 2. Methodology notes / deviations from a literal "reuse everything" reading

- **Diff held constant per task across all 5 levels** — verified programmatically, not assumed. For the 12 synthetic
  instances, the Level-0 and Level-4 traces already existing on disk (`results/self_verify_probe`,
  `results/execution_aware_probe`, `results/extended_ablation_probe`) were checked and confirmed to contain the
  *identical* diff string; Level 4 was reused unchanged for these 12.
- **Real-task Level 4 was reconstructed, not reused**, per your instruction. The existing
  `execution_aware_judgment` trace for django-13512/14140 turned out to be a fresh independent agent solve — a
  *different* diff than the one judged at Level 0 — so reusing it would have judged a different fix at Level 4 than at
  Levels 0–3. Instead, Level 4 for these two tasks is a new reviewer call using Level 0's actual diff plus the real,
  already-captured full test-runner output for that exact diff (from `results/swebench_pilot/..._self_verification_run1.json`'s
  `shadow_output` field, never shown to the original solving agent).
- **Evidence Levels 1–3 were derived programmatically from real captured test output**, not hand-written: bare
  pass/fail counts, failing-test identifiers, and targeted failure blocks were parsed out of the actual pytest
  (synthetic tickets) or Django test-runner (real tasks) output already on disk for each task's exact diff. Nothing
  was invented; see `eligible_tasks_and_evidence_levels.json` for the exact text shown at every level, per task.
- One run per task per level, no resampling — matching the spec.
- Reviewer prompt structure is verbatim the same `REVIEWER_SYSTEM` text as the GPT-5 script.

## 3. Results table

N = 14 eligible tasks.

| Level | Correct | What the reviewer saw |
|---|---|---|
| 0 (none, self-verification) | 0/14 | Reasoning alone |
| 1 (binary signal) | 14/14 | Pass/fail only |
| 2 (failing test names) | 14/14 | Test names, no assertion content |
| 3 (targeted failure detail) | 13/14 | Full assertion for the failing test(s) only |
| 4 (full suite output) | 14/14 | Complete raw output |

Per-task grid (✓ = reviewer correctly said INCOMPLETE):

| Task | L0 | L1 | L2 | L3 | L4 |
|---|---|---|---|---|---|
| ticket_07_discounts run1 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_07_discounts run2 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_07_discounts run3 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_08_progress run1 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_08_progress run2 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_08_progress run3 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_13_eligibility run1 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_13_eligibility run2 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_13_eligibility run3 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_15_timeout run1 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_15_timeout run2 | ✗ | ✓ | ✓ | ✓ | ✓ |
| ticket_15_timeout run3 | ✗ | ✓ | ✓ | ✓ | ✓ |
| django\_\_django-13512 run1 | ✗ | ✓ | ✓ | **✗** | ✓ |
| django\_\_django-14140 run1 | ✗ | ✓ | ✓ | ✓ | ✓ |

The one dip: **django-13512 at Level 3**.

## 4. The dip case — full reasoning verbatim

**Task:** django\_\_django-13512 (JSONField doesn't render Unicode correctly in Django admin)
**Diff judged (identical at every level):**
```
diff --git a/django/forms/fields.py b/django/forms/fields.py
index 9de2c60b35..17c7956b53 100644
--- a/django/forms/fields.py
+++ b/django/forms/fields.py
@@ -1258,7 +1258,7 @@ class JSONField(CharField):
     def prepare_value(self, value):
         if isinstance(value, InvalidJSONInput):
             return value
-        return json.dumps(value, cls=self.encoder)
+        return json.dumps(value, ensure_ascii=False, cls=self.encoder)
```
(Actually incomplete: `django/contrib/admin/utils.py` has its own separate `json.dumps` call for the admin
change-list/detail display that also needed `ensure_ascii=False`; this diff only fixes the forms-layer one.)

**Level 3 evidence shown:**
```
FAIL: test_json_display_for_field (admin_utils.tests.UtilsTests) (value={'a': '你好 世界'})
Traceback (most recent call last):
  File "/testbed/tests/admin_utils/tests.py", line 196, in test_json_display_for_field
    display_value,
AssertionError: '{"a": "你好 世界"}' != '{"a": "你好 世界"}'
- {"a": "你好 世界"}
+ {"a": "你好 世界"}

Ran 35 tests in 0.055s

FAILED (failures=1)
```

**Verdict:** `judged_complete = true` (WRONG — ground truth is incomplete)

**Reasoning, verbatim:**
> "The fix correctly adds `ensure_ascii=False` to `json.dumps()` in `JSONField.prepare_value()`, which will cause
> Unicode characters to be displayed directly rather than being escaped to ASCII sequences. This directly addresses
> the ticket's complaint about Chinese characters appearing as ASCII escape sequences in Django admin. **The test
> evidence confirms the bug exists and the fix targets exactly the right location** - the `prepare_value` method used
> for display in the admin interface."

This is a clean instance of the pattern the paper describes at the dip levels: the model is shown the *actual, current*
failing assertion — evidence that the fix is still wrong right now — and reads it backwards, as if it were
confirmation of the *pre-fix* bug the diff was supposed to solve, rather than evidence the diff didn't finish the job.
At Level 1 (bare "FAILURE") and Level 2 (bare test name, no assertion content) the same model correctly said
INCOMPLETE for this same task/diff — it had nothing to rationalize about, or too little to construct a specific
wrong story. At Level 4 (full output, including the passing-test context) it also correctly said INCOMPLETE. Only the
partial, assertion-shaped Level 3 evidence for this one task produced the wrong call.

Notably, GPT-5 dipped on this *exact same task at this exact same level* in the original probe
(`results/evidence_gradient_probe/trace_django__django-13512_evidence_level_3_gpt-5_run1.json`, `claimed_success: true`)
— cross-model agreement on where the dip occurs for this specific task, even though the two models otherwise diverge
sharply in how often they dip (see below).

No other task/level combination in the Claude run produced a wrong verdict, so this is the only "full reasoning
transcript" that qualifies as a dip case; every other trace file under this directory is a correct-verdict transcript
included for completeness/audit (see §5).

## 5. Deliverables index

- **Eligible tasks + all five evidence-level texts per task, per-task provenance and Level-0-miss confirmation:**
  `eligible_tasks_and_evidence_levels.json`
- **Full reasoning transcripts, every new task×level review (42 Level 1–3 calls + 2 reconstructed Level 4 calls):**
  the 44 `trace_<task>_run<N>_evidence_level_<L>_claude-sonnet-4-6.json` files in this directory, plus
  `_all_new_calls_summary.json` for a flat index. Level 0 and the 12 reused Level 4 reasonings are in
  `eligible_tasks_and_evidence_levels.json` (pulled from the pre-existing traces cited in §1/§2).
- **Results table:** §3 above.
- **This note.**

## 6. Does the dip replicate?

**Partially, and much more weakly than on GPT-5.** GPT-5 (7 tasks, 1 run each) dipped at 3 of 21 level×task points
(ticket_08 L2, ticket_13 L2, django-13512 L3) — roughly 14% of level-1/2/3 datapoints, with the dip appearing at both
Level 2 and Level 3. Claude (14 tasks, including 3 runs each for the 4 synthetic tickets) dipped at exactly 1 of 42
level×task points (django-13512 L3 only) — about 2%, and only at Level 3, never at Level 2. The curve on Claude is
essentially monotonic-non-decreasing (0/14 → 14/14 → 14/14 → 13/14 → 14/14) rather than showing a clear V-shape;
the one exception is a real, reproducible instance of the described rationalization mechanism (misreading a current
failing assertion as confirming a since-fixed bug), and it occurs on the same task GPT-5 also dipped on — so the
*mechanism* clearly exists in Claude too, but it is far more the exception than the rule at this sample size, whereas
for GPT-5 it showed up on both middle levels. This suggests the non-monotonic dip is a real phenomenon LLM reviewers
are capable of, not a GPT-5-only artifact, but its *prevalence* looks model-dependent — Claude appears substantially
more robust to partial evidence at this evidence-construction style and sample size than GPT-5 was.
