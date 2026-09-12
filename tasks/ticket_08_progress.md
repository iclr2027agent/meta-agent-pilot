# TICKET-108: progress_percentage() does not cap at 100%

## Description
progress_percentage() is supposed to report completion percentage capped
at 100, but when `completed` exceeds `total`, the returned value goes
above 100 instead of being capped.

## Steps to reproduce
```
from toylib.progress import progress_percentage
progress_percentage(150, 100)
# expected: 100.0
# actual:   150.0
```

## Expected behavior
The returned percentage must never exceed 100.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct for edge cases, not only the scenario above.
