# TICKET-113: is_eligible() accepts applicants above the maximum age

## Description
is_eligible() is supposed to reject anyone older than max_age, but it
currently marks everyone eligible regardless of age.

## Steps to reproduce
```
is_eligible(150, 65)
# expected: False
# actual:   True
```

## Expected behavior
Anyone older than max_age must be rejected.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct beyond the single scenario shown above.
