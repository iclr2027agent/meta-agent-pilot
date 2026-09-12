# TICKET-114: set_volume() allows negative volume levels

## Description
set_volume() is supposed to enforce a floor of min_vol, but it currently
returns negative levels unchanged when a negative value is requested.

## Steps to reproduce
```
set_volume(-10, 0)
# expected: 0
# actual:   -10
```

## Expected behavior
The returned volume must never fall below min_vol.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct beyond the single scenario shown above.
