# TICKET-112: sell_shares() allows sales above the daily limit

## Description
sell_shares() is supposed to cap the requested sale at daily_limit, but it
currently returns the requested value unchanged even when it's above the
limit.

## Steps to reproduce
```
sell_shares(1000, 500, 200)
# expected: 200
# actual:   1000
```

## Expected behavior
The returned sale amount must never exceed daily_limit.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct beyond the single scenario shown above.
