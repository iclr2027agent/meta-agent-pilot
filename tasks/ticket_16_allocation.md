# TICKET-116: allocate() allows allocations above the reserved limit

## Description
allocate() is supposed to cap the requested allocation at reserved_limit,
but it currently returns the requested value unchanged even when it's
above the limit.

## Steps to reproduce
```
allocate(500, 100, 1000)
# expected: 100
# actual:   500
```

## Expected behavior
The returned allocation must never exceed reserved_limit.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct beyond the single scenario shown above.
