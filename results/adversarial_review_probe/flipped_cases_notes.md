# Cases that flipped from missed (neutral prompt) to caught (adversarial prompt)

## django__django-12273 — claude-sonnet-4-6 adversarially reviews gpt-5's diffs

```
## Analysis

### What the ticket is asking

When a child model instance (using multi-table inheritance) has its primary key reset to `None`, calling `save()` should insert a new row rather than updating the existing one. The bug is that resetting `obj.uid = None` on a `Derived` instance doesn't properly clear the parent link field, so the save still updates the existing record.

### Understanding the fix

The patch modifies `_save_parents` to handle two cases:
1. **New instance** (`_state.adding` is True): If a parent link is set, copy its value to the parent's pk (original behavior).
2. **Existing instance with reset parent pk**: Clear the parent link field too, so the parent save also treats it as an insert.

### Edge cases and problems I see

#### 1. `_state.adding` is not updated when pk is set to None

When a user does `obj.uid = None`, `_state.adding` is **not** automatically set to `True`. The `_state.adding` flag is set during `__init__` and `from_db`, and is only changed to `False` after a successful save. So for an existing object fetched from the DB, `_state.adding` will be `False`.

This means the patch's `else` branch (clearing the parent link) will be triggered when:
- `_state.adding` is `False` (existing object)
- Parent pk attname is `None`

This seems correct for the described bug scenario. But what happens next in `_save_table`? After clearing the parent link, `_save_table` for the parent will do an INSERT (since pk is None), but will `_state.adding` be False causing `_save_table` to attempt an UPDATE first?

Looking at `_save_table`: it checks `force_update`, `force_insert`, and whether `pk_val` is None to decide INSERT vs UPDATE. With pk set to None, it should attempt an INSERT. But `_state.adding` being False could cause issues elsewhere.

#### 2. The child table's own pk is not cleared

In multi-table inheritance, `Derived` has its own row in `derived` table with a OneToOneField back to `item`. The patch clears `field.attname` (the parent link on the child), but the child's own pk (`derived_ptr_id` or similar) — which *is* `field.attname` — gets cleared. The parent's pk (`item.uid`) is already None. So after the patch, both should be None. This seems okay.

But wait — does the child model `Derived` have its own separate pk field? In Django MTI, the child's pk IS the parent link. So `derived.uid` and `derived.item_ptr_id` point to the same underlying value through different attribute names. When `obj.uid = None` is set, what happens to `field.attname`?

`field.attname` for the parent link would be `item_ptr_id`. `parent._meta.pk.attname` would be `uid`. These are different attributes. When the user sets `self.uid = None`, that sets `uid` to None but `item_ptr_id` may still hold the old value. The original code checked if parent pk was None AND parent link was NOT None, then synced them. The new code, in the else branch, clears `item_ptr_id` when parent pk is None — this is the correct fix direction.

#### 3. What if `update_fields` is specified?

If `update_fields` is passed to `save()`, the behavior of `_save_table` may differ. The patch doesn't account for `update_fields` interactions. If `update_fields` doesn't include the pk field, or if it conflicts with the new insert behavior, things could break.

#### 4. The `_state.adding` check is wrong for the normal "new child" case

Consider creating a brand new `Derived()` instance (never saved). `_state.adding` is True. `parent._meta.pk.attname` is None (no pk set). `field.attname` is also None (no parent link). The original condition `getattr(self, field.attname) is not None` would be False, so nothing happens — correct. The new code also falls into the `if self._state.adding:` branch with the inner `if getattr(self, field.attname) is not None:` check — also nothing happens. OK.

#### 5. The case where `_state.adding` is False but pk was never set (shouldn't happen normally)

This edge case seems unlikely in practice.

#### 6. Critical: `_state.adding` remains False throughout

After the patch clears `field.attname`, `_save_table` is called for the parent. Since `_state.adding` is False and pk is None, `_save_table` will check `pk_val is None` which triggers an INSERT. This should work for the parent row.

Then the child's `_save_table` is called. The child's pk (which is the parent link) is now None. But `_state.adding` is still False. Looking at `_save_table` for the non-adding case, it tries to UPDATE first. With pk None, `pk_val is None` → it will force an INSERT. This seems okay.

But **after** the parent INSERT, the new parent pk needs to be propagated back to the child's `field.attname`. Is this handled? After `_save_table` for the parent, the new pk is set on the parent instance, but the child's link field (`field.attname`) was just set to None by the patch and needs to be updated to the newly inserted parent pk. Let me check if this happens...

In the original code flow, after inserting the parent, `_do_insert` returns the new pk and it gets set on the instance. But the child's `field.attname` — which was cleared by the patch — won't automatically get the new parent pk value unless something explicitly sets it.

Actually, looking more carefully: `parent._meta.pk.attname` and `field.attname` — are these always the same field? In MTI, the parent link IS the child's pk, so they could overlap. Let me think...

For `Derived(Item)`, `Derived` has an auto-created `item_ptr = OneToOneField(Item, parent_link=True, primary_key=True)`. So `field` is `item_ptr`, `field.attname` is `item_ptr_id`. `parent._meta.pk.attname` is `uid` (Item's pk). These are DIFFERENT attributes on the Derived instance.

After parent INSERT: new uid is set as `self.uid` (via `parent._meta.pk.attname`). But `self.item_ptr_id` (field.attname) was cleared to None by the patch and is NOT updated with the new uid. Then when `_save_table` for the child runs, it uses `item_ptr_id` as its pk, which is still None — this might actually work for the INSERT, but might not properly link the child row to the parent row.

Wait, actually after `_save_parents` returns for the parent, there's typically sync logic... Let me re-read the original code at line 804:

```python
if (field and getattr(self, parent._meta.pk.attname) is None and
        getattr(self, field.attname) is not None):
    setattr(self, parent._meta.pk.attname, getattr(self, field.attname))
```

This only syncs parent pk FROM field.attname, not the other way. After INSERT of parent row, the new pk (uid) is written to `self.uid`. But `self.item_ptr_id` remains None (cleared by patch). The child save then needs `item_ptr_id` to match the newly created parent uid. This sync is missing.

#### 7. Missing sync after parent INSERT

After the patch clears `field.attname` to None and the parent is inserted (getting a new auto-generated pk written to `parent._meta.pk.attname`), the child's `field.attname` still holds None. The child's `_save_table` would then insert with `item_ptr_id = None`, which would either fail (if there's a FK constraint and the DB doesn't accept NULL) or create an orphaned child row.

This is a **significant bug** in the patch. The fix needs to also update `field.attname` with the newly assigned parent pk after parent insertion.

### Conclusion

The patch has a critical flaw: after clearing `field.attname` (the parent link) and inserting a new parent row (which generates a new pk), the new pk value is not propagated back to `field.attname` before the child table is saved. This would result in either a constraint violation or a broken FK relationship.

INCOMPLETE
```

## django__django-13512 — gpt-5 adversarially reviews claude's diffs

```
Thanks for the patch. The intent (render unescaped non-ASCII in JSONField so Chinese displays properly in the admin) makes sense, and using ensure_ascii=False is the straightforward way to get there. However, there are a few risks and edge cases that this change introduces or leaves unaddressed:

- Scope is broader than the ticket: This changes the behavior of forms.fields.JSONField everywhere, not just in the admin. Any third-party forms, custom front-ends, or tests that assert the ASCII-escaped rendering will change output and may break. If the ticket is admin-specific, consider constraining the change to the admin widget or providing a per-field/per-widget option.

- Charset regressions: With ensure_ascii=False, non-ASCII characters are emitted directly. In deployments that still set a non-UTF-8 DEFAULT_CHARSET (e.g., latin-1), this can cause UnicodeEncodeError on render, whereas the prior ASCII-escaped output would have passed through. Django defaults to UTF-8, but changing behavior in a core field that can cause 500s for non-UTF-8 sites is a notable backward-compatibility risk that needs at least acknowledgement and tests.

- Test coverage: No tests were added/updated. At minimum, add:
  - A form render test verifying that a dict containing '中国' renders as {"key": "中国"} (not \u4e2d\u56fd) in the widget.
  - A bound form round-trip test: render with non-ASCII, submit back, ensure to_python/json.loads handles it and value equality works with has_changed.
  - A regression/compat test for InvalidJSONInput path to ensure raw invalid text (with non-ASCII) is preserved.
  - If this is intended to be global behavior, a test documenting this change in output format (for non-ASCII) so downstream breakages are explicit.

- HTML contexts: JSONField can be rendered with different widgets (e.g., HiddenInput) which places the value into an attribute. Non-ASCII is okay if properly auto-escaped, but it’s worth explicitly verifying that the escaping in attribute contexts and Textarea contexts is correct and no double-escaping or broken markup occurs.

- JSON compliance and edge chars: ensure_ascii=False means characters like U+2028/U+2029 are emitted as-is. Inside a textarea they’re fine, but if anyone embeds the field’s value in a JS context (common in custom templates), those can break scripts. This change makes that pitfall more likely globally. If we proceed, it should be called out in release notes.

- Backwards-compat and docs: Changing the visual representation of JSONField across all forms is a user-visible change. This likely needs a release note and possibly a feature flag or a field/widget kwarg (e.g., allow_unicode=True defaulting to True only in admin) to ease migration.

- Admin-specific polish: If the actual pain point is admin UX, consider also pretty-printing (indent) for readability. That’s orthogonal to this change but often requested alongside.

Given the above, the patch addresses the narrow symptom but introduces broader behavior changes without tests or migration guidance. I can’t rule out regressions in non-UTF-8 environments and non-admin usages.

INCOMPLETE
```

## django__django-14140 — claude-sonnet-4-6 adversarially reviews gpt-5's diffs

```
## Analysis

### What the patch does

The patch modifies `Q.deconstruct()` to handle non-subscriptable single children (like `Exists()`) by placing them in `args` instead of trying to subscript them. It also conditionally adds `_connector` to kwargs when the connector is non-default in the single-child branch.

### Issues I'm looking for

#### 1. The `_negated` field is not handled in the single-child non-tuple branch

Look at the `else` branch (multi-child path):
```python
else:
    args = tuple(self.children)
    if self.connector != self.default:
        kwargs = {'_connector': self.connector}
```

Neither the original code nor the patched code handles `_negated` in the multi-child branch — but wait, let me look at this more carefully. The original code only has the `_connector` check for the multi-child branch. Does the original code handle `_negated` for the single-tuple-child case? No — in the original single-child case, `_negated` is never added to kwargs.

But now, the patch's new `else` branch (non-tuple single child) also doesn't handle `_negated`. If someone writes `~Q(Exists(...))`, the `_negated=True` will be lost during deconstruct, meaning the reconstructed Q object won't be negated.

Let me verify: Looking at the original code, the `if len(self.children) == 1` branch produces `kwargs = {child[0]: child[1]}` — no `_negated`. The `else` branch also doesn't add `_negated`. So this is a pre-existing bug. But the patch's new branch also doesn't fix it.

Actually wait — I need to look at the full `deconstruct` method to see if `_negated` is handled elsewhere. The diff only shows the modified section. If the full method handles `_negated` after these branches, then it's fine. But based on what's visible in the diff, neither `_negated` is added here.

#### 2. The `_connector` handling asymmetry

In the patched single-child non-tuple branch:
```python
args = (child,)
if self.connector != self.default:
    kwargs = {'_connector': self.connector}
```

But in the multi-child branch:
```python
args = tuple(self.children)
if self.connector != self.default:
    kwargs = {'_connector': self.connector}
```

These look symmetric. However, in the original single-tuple-child branch, there's NO `_connector` handling:
```python
kwargs = {child[0]: child[1]}
```

This means the original code was already buggy for `Q(x=1, connector='OR')` with a single child — but the patch now correctly adds `_connector` for the non-tuple path while still missing it for the tuple path. This is **asymmetric**: the tuple-child single path still doesn't preserve `_connector`.

This is a pre-existing bug in the tuple path, but the patch makes the behavior inconsistent between tuple and non-tuple single children.

#### 3. The `_negated` handling is missing in both branches of the single-child patch

`Q(Exists(...)).deconstruct()` followed by reconstruction won't preserve negation: `~Q(Exists(...)).deconstruct()` — this is likely missing. However, since the pre-existing code also didn't handle `_negated` in the single tuple child path, this is a pre-existing bug that the patch doesn't fix (and doesn't make worse for tuple children, but also doesn't fix for non-tuple children).

#### 4. Reconstruction correctness

When `Q.__init__` receives a single positional arg that is an `Exists` object, does it reconstruct correctly? The `Q.__init__` likely calls `super().__init__(*args, **kwargs)` which goes to `tree.Node.__init__`. This should work if `Node` accepts children as args.

### Summary of Issues

The most significant issue is: **the `_connector` is handled for non-tuple single children but NOT for tuple single children**, creating an asymmetry. However, this asymmetry is a pre-existing bug being partially fixed.

The more critical observation: **`_negated` is not preserved in the new branch either**, making `~Q(Exists(...))` incorrectly deconstruct (losing the negation). But again this may be a pre-existing bug if the full method doesn't handle `_negated` elsewhere.

Given that the patch doesn't handle the `_connector` for the tuple single-child case (pre-existing but now asymmetric), and potentially doesn't handle `_negated` for the new branch — and considering the ticket explicitly mentions this is the "more backward-compatible approach" that should work — **the patch is incomplete**.

INCOMPLETE
```

## ticket_07_discounts — gpt-5 adversarially reviews claude's diffs

```
The change fixes the obvious happy-path bug by capping the computed percentage discount with min(discount, max_discount). For the specific reproduction case, it now yields 950.0 as expected.

However, there are several edge cases and compatibility concerns that this patch does not address and that I cannot rule out based on the ticket alone:

- Backward compatibility with callers that may have been passing a sentinel to indicate “no cap”
  - If any existing call sites pass max_discount=None (plausible given the prior bug meant the param was ignored), the new min(discount, None) will raise a TypeError. Previously the function would have returned a numeric result. This is a silent behavior change and a likely regression.
  - If callers pass math.inf to mean “no cap,” min(discount, inf) is fine; but this convention isn’t documented.

- Negative max_discount produces nonsensical and unsafe results
  - Example: apply_discount(1000, 90, -50) -> discount=900; min(900, -50) = -50; return 1050. That increases the price, which contradicts the function’s intent and the ticket’s “discount actually subtracted” language.
  - At minimum, effective_max should be clamped to >= 0: cap = max(0.0, max_discount).

- Negative percentage or negative computed discount
  - With pct < 0, the function increases the price (e.g., apply_discount(100, -10, 50) -> 110). The current code already had this behavior, but the ticket’s domain (“apply a percentage discount”) strongly suggests discounts should not be negative. If negative discounts are not intended, computed discount should be clamped at >= 0 to avoid price increases and keep the “never exceeds max_discount” invariant meaningful: computed = max(0.0, price * pct / 100.0).
  - If surcharges (negative discounts) are intentionally allowed, the “cap” semantics for a negative discount are undefined in the current patch. Capping with min mixes signs and can invert intent. You’d need a clear rule (e.g., cap applies only to positive discounts; negative discounts bypass cap or use a separate max_surcharge).

- Non-finite and invalid inputs
  - pct or price being NaN propagates to NaN; min(NaN, max_discount) returns NaN in practice, leading to returning NaN. That’s rarely desirable in a pricing function. Consider validating finiteness and raising a ValueError for NaN.
  - Very large pct (e.g., inf) is partially handled (min(inf, cap) -> cap), but again, explicit validation would make behavior intentional.

- Result below zero
  - The function can still return a negative final price when max_discount > price. The ticket doesn’t forbid this; if library policy is “prices never go below 0,” an additional clamp is needed. If not, this is likely acceptable as-is, but it’s worth confirming.

- Documentation/contract clarity
  - The function does not document the allowed ranges or special values for pct and max_discount. Given the above, callers need clarity (e.g., “pct must be finite; max_discount must be a non-negative, finite number; None is not allowed” or, if None/inf/negatives are supported, specify exact semantics).

Recommended changes to make the fix robust:
- Input validation:
  - Ensure price and pct are finite numbers; reject NaN.
  - Either explicitly support max_discount=None as “no cap” (cap = +inf) for backward compatibility, or raise a clear ValueError if None is passed.
  - Clamp effective_max = max(0.0, max_discount) to prevent price increases from a negative cap.
- Decide and document whether negative pct is allowed:
  - If not allowed: clamp computed discount to >= 0 or raise on negative pct.
  - If allowed (surcharge): define how, if at all, max_discount applies to negative discounts.
- Consider tests for:
  - Negative max_discount.
  - max_discount=None (if supported).
  - Negative pct behavior.
  - NaN/inf inputs.
  - Extreme pct with cap.
  - Discount larger than price.

Given these unaddressed cases and potential regressions (notably None for max_discount and negative caps), I cannot confidently say this change is safe across alternate code paths and hidden invariants.

INCOMPLETE
```

## ticket_08_progress — gpt-5 adversarially reviews claude's diffs

```
Reasoning:
- The change fixes the obvious overshoot by capping at 100.0 for ordinary positive int inputs, but there are several edge cases and potential regressions unaddressed:
- Lower bound not clamped: For negative completed, the function can return negative percentages. Given the domain “progress,” callers typically expect a 0–100 range. The ticket’s language implies capping at 100 but doesn’t discuss the lower bound; still, “edge cases” acceptance suggests this should be considered.
- Type-mixing regression: Using min(pct, 100.0) may break for non-float numeric types that previously worked:
  - Decimal: If callers pass Decimal inputs, pct will be a Decimal, and comparing a Decimal with a float in min raises TypeError. Previously, returning pct worked fine for Decimal use.
  - Numpy/pandas vectorized inputs: If pct is a numpy array or pandas Series (some projects allow vectorized progress calc), Python’s built-in min on an array and a scalar raises a ValueError due to ambiguous truth value. The original implementation would have returned the array without error.
- Negative or zero totals:
  - total == 0 path remains returning 0.0 regardless of completed; depending on domain expectations, some systems prefer 100% when completed > 0 and total == 0 (unknown total). If that’s an established invariant in toylib, this is fine; if not, this may be a missed edge case.
  - total < 0 yields negative or otherwise nonsensical percentages; there is no validation or normalization. The ticket didn’t constrain inputs to non-negative, but in a progress context, guarding or clamping would likely be expected.
- Extremely large integers: Division may raise OverflowError when converting ints to float, which is pre-existing. Not a regression, but worth considering if the function is supposed to be robust.

Net: Fix addresses the specific example but risks regressions with Decimal and vector inputs and leaves lower-bound/negative-total edge cases undefined, which are likely relevant in a progress domain.

INCOMPLETE
```

## ticket_08_progress — claude-sonnet-4-6 adversarially reviews gpt-5's diffs

```
## Adversarial Review

### What the patch does
Removes the bug comment and adds `min(pct, 100.0)` to cap the result at 100. This directly addresses the reported symptom.

---

### Edge cases and unhandled scenarios

**1. Negative `completed` (completed < 0)**
The patch only caps the *upper* bound. If `completed` is negative (e.g., `progress_percentage(-10, 100)`), the function returns `-10.0`. The ticket says "the returned percentage must never exceed 100" but a symmetric invariant — that progress is never *below* 0 — is strongly implied by the domain (progress percentages are conventionally in [0, 100]). The acceptance criteria says "behavior must remain correct for edge cases, not only the scenario above," which suggests this should be considered.

**2. Negative `total` (total < 0)**
If `total` is negative, e.g., `progress_percentage(50, -100)`, the result is `-50.0`. This is nonsensical. The patch does not guard against this.

**3. `completed = 0, total = 0`**
Already handled by the `total == 0` guard, returns `0.0`. Fine.

**4. `completed = 0, total > 0`**
Returns `0.0`. Fine.

**5. Floating-point `completed` or `total`**
The type hints say `int`, but Python doesn't enforce this. Not a new issue introduced by the patch, so not a regression.

**6. The `total == 0` branch with non-zero `completed`**
Returns `0.0` regardless of `completed`. This is a pre-existing design choice; the patch doesn't change it, so it's not a regression introduced here — but it's worth noting that `progress_percentage(150, 0)` returning `0.0` is arguably wrong too.

---

### Most critical gap
The ticket's acceptance criteria explicitly calls out "edge cases, not only the scenario above." The patch only addresses the upper-bound cap. A lower-bound cap of `0.0` (i.e., `max(0.0, min(pct, 100.0))`) is the standard expected behavior for a progress percentage and is left unaddressed. Negative inputs produce negative percentages, which violates the spirit (and arguably the letter) of the ticket's requirements.

---

INCOMPLETE
```

## ticket_13_eligibility — gpt-5 adversarially reviews claude's diffs

```
Reasoning:
- The change fixes the literal bug for the provided example: age > max_age now returns False. Equality boundary age == max_age returns True, which matches “above max_age is rejected.”
- However, several edge cases and possible regressions are unaddressed:
  - Type robustness/regressions:
    - Previously, any inputs returned True; now non-comparable inputs (e.g., age or max_age is None, a string, a date, or a custom object) will raise TypeError. This is a behavioral change that could break callers and violate “No regression to other toylib behavior” unless tests explicitly forbid such inputs.
    - numpy inputs: age/max_age as numpy arrays produce an array result; returning that instead of a Python bool can cause downstream truth-value errors. Even numpy scalar types will return numpy.bool_, not strictly bool, potentially surprising.
    - NaN handling: float('nan') <= max_age is False, which changes prior behavior (previously True). If NaN can surface from upstream parsing, this is an untested change.
  - Domain validation not addressed:
    - Negative ages are treated as eligible if max_age is non-negative; if negative ages should be invalid/rejected, the function should enforce age >= 0 (ticket doesn’t say, but this is a common invariant).
    - Negative max_age would reject almost all ages; if max_age is expected to be ≥ 0, this should be validated or documented.
  - Return type consistency: The expression may return non-bool truthy types (e.g., numpy.bool_), which could violate expectations that this function returns a Python bool.
- No tests were added for boundary and edge cases (equal-to boundary, just over, negative ages, floats, NaN, non-numeric inputs). Without them, we can’t affirm “Behavior must remain correct beyond the single scenario” or guarantee no regressions to broader toylib behavior.

Verdict:
INCOMPLETE
```

## ticket_15_timeout — gpt-5 adversarially reviews claude's diffs

```
The change from returning the requested value to using min(requested, max_timeout) does address the exact repro case. However, it assumes happy-path numeric inputs of the same, comparable type and introduces several potential regressions and edge-case gaps that the ticket does not explicitly rule out:

1) Type comparability and None
- If either argument can be None (often used as “no cap” or “unset”), min(requested, None) raises TypeError. Previously, set_timeout would have returned requested unchanged.
- If callers ever pass heterogeneous but previously tolerated types (e.g., int for requested and datetime.timedelta/Decimal/str for max_timeout), this now raises TypeError instead of passing requested through. That’s a behavioral change and potential regression.

2) NaN handling
- With float NaN, Python’s min has surprising results:
  - min(requested, float('nan')) returns NaN, because requested < NaN is False, so min returns the second argument. Prior behavior would have returned requested (non-NaN) unchanged if max_timeout was NaN.
  - If requested is NaN and max_timeout is finite, min returns max_timeout (finite). Previously it would have returned NaN (pass-through). Either way, current change alters semantics around NaN without tests or clarification.
- Returning NaN as a timeout is almost certainly invalid; this patch can now surface NaN where it did not before.

3) Negative values
- If max_timeout can be negative (misconfiguration), min will return a negative timeout even when requested is positive. Previously, requested would have passed through (possibly positive), so this is a change. If the domain requires non-negative timeouts (very common), you likely want a lower clamp at 0 as well, or at least to validate and reject negative max_timeout.
- If requested is negative, min leaves it negative; ticket requirements don’t forbid this, but domain invariants might. At minimum, tests should specify expected behavior for negative inputs.

4) Mixed time types and units
- If the library sometimes accepts datetime.timedelta for timeouts, min works only when both operands are timedelta. Mixed types (e.g., requested as timedelta, max_timeout as int seconds) will TypeError. Prior implementation returned requested without error.
- If the domain wants to support both numerics and timedeltas, coercion or explicit handling is needed.

5) Return type stability
- This change can now return max_timeout’s type (e.g., Decimal, timedelta) when clamping, whereas previously the value was always the requested type. That may be intended, but if callers expect a specific type derived from requested, it’s a breaking change.

6) Extremes and infinities
- float('inf') for requested clamps to max_timeout: fine.
- If max_timeout is -inf, min returns -inf (likely invalid). Previously the function would have returned requested.

Recommendations
- Clarify and test the function’s contract:
  - Are None values allowed? If so, define whether None means “no cap” and preserve pass-through.
  - Define behavior for negative values; add lower clamp to 0 if non-negative timeouts are required.
  - Define accepted types (ints/floats/Decimal/timedelta). If mixed types are possible, either coerce to a common unit or preserve pass-through for incomparable types.
  - Define behavior for NaN and infinities; at minimum, treat NaN max_timeout as “no cap” to avoid returning NaN.
- Implementation sketch that avoids regressions while adding the cap:
  - If max_timeout is None: return requested (no cap).
  - If either is NaN: return requested (preserve prior pass-through semantics).
  - Try to compare requested <= max_timeout; on TypeError (incomparable types), return requested (preserve prior behavior).
  - Otherwise, return requested if within cap, else max_timeout.
  - Optionally clamp to a lower bound (e.g., 0) if the domain requires non-negative timeouts.

Given these unresolved edge cases and potential regressions, the single-line min() change is not sufficient without clarifying the contract and adding tests to lock in the intended behavior.

INCOMPLETE
```
