# TICKET-111: clamp_quantity() allows quantities above the per-order maximum

## Description
clamp_quantity() is supposed to cap the requested quantity at max_quantity,
but it currently returns the requested value unchanged even when it's
above the maximum.

## Steps to reproduce
```
clamp_quantity(50, 10)
# expected: 10
# actual:   50
```

## Expected behavior
The returned quantity must never exceed max_quantity.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct beyond the single scenario shown above.
