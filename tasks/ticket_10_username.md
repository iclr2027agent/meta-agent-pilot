# TICKET-110: is_username_taken() misses case-insensitive matches

## Description
is_username_taken() is supposed to detect usernames that are already
registered, but it currently misses matches that differ only in case.

## Steps to reproduce
```
is_username_taken('Alice', ['alice', 'bob'])
# expected: True
# actual:   False
```

## Expected behavior
'Alice' should be detected as already taken given the existing username
'alice'.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct beyond the single scenario shown above.
