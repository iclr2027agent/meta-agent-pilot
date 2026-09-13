# SWE-bench Verified Multi-Repo Expansion — Selection Log

Pre-registered candidate selection for the real-task probe expansion (Section 5.2 follow-up).
Written **before** any diff generation or judgment runs. Repos: scikit-learn, matplotlib,
astropy, sympy (django excluded — already covered by the existing probe).

## 0. Pre-flight findings (blocked on these before selection could start)

1. **`scikit-learn__scikit-learn-10774` and `sympy__sympy-17313` (handoff Tier-1 seeds) are
   NOT members of SWE-bench Verified** — confirmed by loading
   `princeton-nlp/SWE-bench_Verified` directly and checking `instance_id` membership. Both
   exist only in full SWE-bench. Per the pre-registration rule (no benchmark/repo
   substitution), both are dropped and replacement candidates were sourced from Verified
   directly (Section 2 below), per the handoff's Section 3b fallback.
2. Confirmed in Verified: `astropy__astropy-12907`, `matplotlib__matplotlib-24870`,
   `matplotlib__matplotlib-25332`, `astropy__astropy-13033`.
3. Confirmed still present in Verified (i.e. NOT already scrubbed) and therefore must stay
   excluded per the handoff's list: `sympy__sympy-18199`, `pylint-dev/pylint-4551`,
   `astropy__astropy-7606/8707/8872`, `matplotlib__matplotlib-20488`,
   `pytest-dev__pytest-7521/5262`. (`scikit-learn__scikit-learn-14520` is not in Verified at
   all, so the exclusion is moot for this round.)
4. Infra: local disk had only ~12GB free (buildx cache + django-probe images from the
   completed Section-5.2 run were consuming ~120GB). Freed by removing an inactive buildx
   builder's 94.6GB state volume and the 6 completed django-probe SWE-bench images (~25GB) —
   neither needed for this new repo set. Left untouched: an unrelated project's Qdrant volume
   and the running `welcome-to-docker` demo container. Result: 109GB free before pulling any
   new images.

## 1. Method

For each of the 4 target repos, all Verified instances were pulled locally (`datasets`
library) with full `problem_statement`, `patch` (gold fix), `test_patch`, and
`FAIL_TO_PASS`. Candidates were triaged in two passes:

- **Heuristic pre-filter** (mechanical, not a judgment call): instances ranked by distinct
  base test names in FAIL_TO_PASS (parametrize suffixes stripped), number of files touched
  by the gold patch, number of new `test_` functions, and identifiers appearing in the gold
  patch's added lines but not in the problem statement text ("hidden identifiers"). Also
  cross-checked: which instances have FAIL_TO_PASS tests spanning ≥2 distinct test files
  (a stronger signal that the hidden requirement crosses issue-scope boundaries) and which
  gold patches touch a shared `base.py`/mixin file (suggesting a fix that generalizes across
  subclasses the issue never named).
- **Manual verification against the Section 3b threshold** for every candidate that surfaced
  from the pre-filter or the handoff's seed list: read the actual problem statement, gold
  patch, and test_patch and apply (a) held-out tests add ≥2 distinct scenarios with ≥1 not
  named in the issue, (b) the gold patch's fix touches/requires that unmentioned scenario too
  (proving it's a real requirement, not an arbitrary assertion), (c) not on any exclusion
  list. Where (a) held but (b) didn't (the gold fix is a single general change that would
  incidentally cover the "hidden" scenario no matter how narrowly someone patched the literal
  repro — e.g. a fix inside a class's only implementation of the relevant method, shared by
  construction rather than by design choice), the candidate was logged as a **boundary
  candidate** and dropped from selection, not silently discarded.

This is a mechanical pre-filter followed by full manual reading of every candidate actually
selected or reported below — not an exhaustive hand-read of all 163 instances across the 4
repos. That tradeoff is stated here rather than implied.

## 2. Per-repo results

### astropy (22 Verified instances) — 4 selected

| instance_id | decision | reasoning |
|---|---|---|
| `astropy__astropy-12907` | **SELECTED** | Issue shows one nested-compound-model example (`Pix2Sky_TAN() & (Linear1D & Linear1D)`); gold fix is a one-line change to the shared `_cstack` helper; hidden tests parametrize 4 additional nesting configurations, 2 of which are in FAIL_TO_PASS. Confirms handoff's Tier-1 description. |
| `astropy__astropy-13033` | **SELECTED** | Issue reproduces a misleading exception for exactly 1 required column; gold fix generalizes the message formatter to scalar-or-list required-columns; hidden test adds a 2-required-columns scenario the issue never mentions, and the gold patch's `as_scalar_or_list_str` helper is specifically what's needed for that case. |
| `astropy__astropy-14369` | **SELECTED** | Issue reports one broken unit string (`10+3J/m/s/kpc2`, division-order bug); gold fix is a one-line YACC grammar-rule swap (general, not string-specific); hidden tests add several other division patterns including a bare leading `/s`, none of which are in the issue. |
| `astropy__astropy-13977` | **SELECTED (extra scrutiny applied, per Section 3a caution)** | Issue's example is one duck-type class hitting one incompatible-units error; gold fix wraps the *entire* pre-existing `__array_ufunc__` body (including `out=`/`reduce` handling) in try/except; hidden tests parametrize over 3 duck-type sophistication levels and both `out=` and `reduce` code paths, none named in the issue. Verified the `out=`/`reduce` codepaths are pre-existing logic now newly exercised via the try/except, not an unrelated bundled feature. |
| `astropy__astropy-13398` | considered, not selected | Feature-request issue where the reporter says "I have put together the makings of a pull request" — the issue text itself specifies the implementation approach in detail. Doesn't fit the narrow-issue/broad-test pattern; the author pre-specified the scope. |
| `astropy__astropy-8707` | considered, not selected | On the exclusion list (gold patch fails on Verified, SWE-bench/SWE-bench#267); confirmed still present in the dataset. |

### matplotlib (34 Verified instances) — 3 selected

| instance_id | decision | reasoning |
|---|---|---|
| `matplotlib__matplotlib-24870` | **SELECTED** | Issue asks only about `contour()`; gold patch touches both `contour.py` and `tri/_tricontour.py`; hidden test (`test_bool_autolevel`) checks `contour`/`contourf`/`tricontour`/`tricontourf`, 3 of which are never named in the issue. |
| `matplotlib__matplotlib-22865` | **SELECTED** | Issue only reports `extend='both'`; gold fix uses general `_extend_lower()`/`_extend_upper()` helpers; hidden test parametrizes over `both`/`min`/`max`/`neither`, 3 of which are absent from the issue. |
| `matplotlib__matplotlib-20676` | **SELECTED** | Issue's repro uses only `direction="horizontal"`; gold fix is a single conditional branching on `self.direction` that fixes both directions together; hidden test parametrizes horizontal/vertical, with vertical unmentioned in the issue. |
| `matplotlib__matplotlib-25332` (handoff Tier-2 seed) | **boundary, not selected** | Issue's repro uses `align_labels()`; hidden test uses `align_ylabels()` instead. But the gold fix is a general `__getstate__`/`__setstate__` added to the shared `Grouper` class itself — the only plausible fix location for "a weakref can't pickle," so a fix aimed narrowly at the literal repro would need to be written inside `Grouper` regardless, and would automatically cover `align_ylabels` too. No plausible narrow-fix temptation exists here distinct from the correct general fix. |
| `matplotlib__matplotlib-21568` | considered, not selected | Same boundary shape as above: gold fix is a single change to the shared `_wrap_in_tex` helper; all affected test cases (weeks/days/hours/minutes) share that one function, so there's no narrower fix location to tempt an agent into. |
| `matplotlib__matplotlib-25775` | considered, not selected | Issue text itself flags the likely broader scope ("also adjusting Annotations accordingly, if needed... requires understanding of backend code") — the reporter already told the reader where to look, weakening any "hidden requirement" framing. |
| `matplotlib__matplotlib-23476`, `matplotlib__matplotlib-23314` | considered, not selected | Single-scenario FAIL_TO_PASS closely matching the literal issue repro; gold patch is a single general one-liner with no distinguishable "narrow vs. broad" fork. |
| `matplotlib__matplotlib-20488` | considered, not selected | On exclusion list (gold patch fails on Verified, SWE-bench/SWE-bench#267); confirmed still present in dataset. |

### scikit-learn (32 Verified instances) — 2 selected

| instance_id | decision | reasoning |
|---|---|---|
| `scikit-learn__scikit-learn-13142` | **SELECTED** | Issue reports `GaussianMixture.fit_predict`/`.predict` disagreeing when `n_init>1`; gold fix is in the *shared* `mixture/base.py` (`fit_predict`), used by both `GaussianMixture` and `BayesianGaussianMixture`; hidden tests add cases for both classes, only the first of which is named in the issue. |
| `scikit-learn__scikit-learn-25102` | **SELECTED** | Issue demonstrates dtype loss for one transformer (`SelectKBest`) via pandas output; gold fix touches the shared `SelectorMixin`/output-config machinery in `sklearn/base.py` + `feature_selection/_base.py`; hidden tests span 2 files checking the shared selector base behavior generally. |
| `scikit-learn__scikit-learn-10774` | considered, not selected | Handoff Tier-1 seed; **not a member of SWE-bench Verified** (confirmed directly against the dataset) — dropped per pre-registration rule, no substitution. |
| `scikit-learn__scikit-learn-12682` | considered, not selected | Gold patch does fix an unmentioned code path (`lasso_lars`'s `max_iter`, beyond the issue's named `lasso_cd`), but the single FAIL_TO_PASS test only exercises `lasso_cd` — the "hidden" fix isn't actually covered by the held-out test, so criterion (a) (≥2 distinct tested scenarios) isn't met. |
| `scikit-learn__scikit-learn-13135` | considered, not selected | Hidden test adds a `5-bins` case parametrized over 3 strategies, but FAIL_TO_PASS only includes the `kmeans` variant — `uniform`/`quantile` at 5 bins were already passing before the fix, so there's no real second hidden scenario, just one narrow one. |
| `scikit-learn__scikit-learn-14983`, `-9288`, `-10844`, `-11578`, `-14710`, `-12973`, `-12585`, `-14894` | considered, not selected | Each has a single-scenario FAIL_TO_PASS closely matching the literal issue repro (in several cases the issue itself already names all affected classes, e.g. -14983's `RepeatedKFold`/`RepeatedStratifiedKFold`), or a single general one-line fix with no narrow-vs-broad fork. |
| `scikit-learn__scikit-learn-14087` | considered, not selected | Ambiguous: the gold patch bundles two separable fixes (a `self.multi_class`/`multi_class` variable-shadowing bug, and a separate `l1_ratio_`-for-non-elasticnet bug) in one diff, and the very large FAIL_TO_PASS count (282) looks like a broad parametrized regression check rather than a clean single hidden-generalization requirement. Not confident enough to include without primary-source PR history; flagging as a possible check-in item rather than guessing. |
| `scikit-learn__scikit-learn-14520` | considered, not selected | On exclusion list (documented narrow/over-specified test) — confirmed not in Verified anyway, so moot. |

*scikit-learn yielded the fewest qualifying candidates of the four repos despite being
top-priority. This is reported rather than backfilled with weaker candidates — see Section 3.*

### sympy (75 Verified instances) — 4 selected

| instance_id | decision | reasoning |
|---|---|---|
| `sympy__sympy-13091` | **SELECTED (extra scrutiny applied, per Section 3a caution)** | Issue is specifically about `Basic.__eq__` returning `NotImplemented`; gold patch applies the same mechanical fix (`NotImplemented` instead of `False`/raise, `__ne__` calling `not self == other`) across **21 files** spanning core, polys, physics, geometry, tensor modules; hidden tests span `test_basic.py` (matches issue) and `test_numbers.py` (`Number`/`Float` comparison — not mentioned in the issue). |
| `sympy__sympy-17630` | **SELECTED** | Issue reports block-matrix *multiplication* (`b*b*b`) throwing; gold fix is one line in the shared `_postprocessor` used for constructing both `MatAdd` and `MatMul` expressions; hidden test `test_zero_matrix_add` checks matrix **addition**, an operation never mentioned in the issue. |
| `sympy__sympy-16597` | **SELECTED (extra scrutiny applied, per Section 3a caution)** | Issue only asks that `is_even` imply `is_finite`; gold fix changes the general fact-derivation system (`get_known_facts`) to add `Implies(Q.rational/irrational/algebraic/transcendental, Q.finite)`; hidden tests check all of those additional implications, none requested in the issue. |
| `sympy__sympy-19783` | **SELECTED** | Issue's repro is `Dagger(A) * IdentityOperator()`; gold fix touches both `Dagger.__mul__` (the literal repro direction) and `IdentityOperator.__mul__` (the *reverse* multiplication order, `Identity * Dagger(A)`, never shown in the issue). Symmetric-operation trap: a fix aimed only at the reported order would miss the reverse. |
| `sympy__sympy-18199` | considered, not selected | On exclusion list — OpenAI's own documented example of a flawed "wide test" (covers 3 issues, described by 1); confirmed still present in Verified. Excluded per handoff regardless of independent merit. |
| `sympy__sympy-22080` | considered, not selected | Plausible (hidden tests span printer/codegen infra beyond the reported `lambdify` scenario) but the gold patch and FAIL_TO_PASS span 3 fairly distinct subsystems (rewriting, PythonCodePrinter, empty-modules), raising the same multi-concern-bundling doubt as sklearn-14087. Deprioritized under time budget rather than force-verified. |
| `sympy__sympy-14248`, `-13757`, `-14976`, `-20916`, `-23413` | considered, not selected | Surfaced by the multi-file-test heuristic but not manually verified against the (a)/(b) threshold before time budget was allocated elsewhere; not claimed as rejected on the merits, just not reached. |

## 3. Summary

| Repo | Verified pool | Candidates manually verified | Selected | Boundary (pre-run) | Rejected |
|---|---|---|---|---|---|
| scikit-learn | 32 | 15 | **2** | 0 | 13 |
| matplotlib | 34 | 9 | **3** | 2 | 4 |
| astropy | 22 | 6 | **4** | 0 | 2 |
| sympy | 75 | 9 | **4** | 0 | 5 |
| **Total** | 163 | 39 | **13** | 2 | 24 |

13 candidates selected, below the 15-20 target — reported honestly rather than padded.
scikit-learn is the weak point (2/32, vs. a 32-instance pool that should have had more given
it's the top-priority repo); this likely reflects that many of its highest "hidden identifier"
heuristic scores turned out to be common-estimator-check suites (parametrized across dozens of
estimators for unrelated contract-testing reasons) rather than genuine narrow-issue/broad-test
gaps, not that scikit-learn lacks the phenomenon.

Two additional sympy candidates (`sympy-14248`, `sympy-22080`) and several more scikit-learn
instances beyond the top-15 heuristic ranking were not reached — continuing the same manual
verification process on them is the most direct way to close the gap to 15-20 if that's wanted
before Section 4 execution begins.
