# TICKET-115: set_timeout() allows durations above the maximum

## Description
set_timeout() is supposed to cap the requested duration at max_timeout,
but it currently returns durations above that cap unchanged.

## Steps to reproduce
```
set_timeout(600, 300)
# expected: 300
# actual:   600
```

## Expected behavior
The returned timeout must never exceed max_timeout.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct beyond the single scenario shown above.
