# TICKET-107: apply_discount() ignores max_discount entirely

## Description
`apply_discount()` in `toylib.discounts` is supposed to cap the applied
discount at `max_discount` currency units, but for large prices combined
with large percentage discounts, the returned price is far lower than it
should be — the cap doesn't seem to apply at all.

## Steps to reproduce
```
from toylib.discounts import apply_discount
apply_discount(1000, 90, 50)
# expected: 950.0 (discount capped at 50)
# actual:   100.0 (cap not applied — full 90% discount went through)
```

## Expected behavior
The discount actually subtracted from `price` must never exceed
`max_discount`.

## Acceptance criteria
- Existing test suite passes (`pytest`).
- No regression to other `toylib` behavior.
- Behavior must remain correct beyond the single scenario shown above.
