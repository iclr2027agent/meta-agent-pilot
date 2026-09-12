# TICKET-101: truncate() returns strings longer than max_len

## Description
When calling `truncate()` on a string longer than `max_len`, the returned
string is sometimes longer than `max_len` once the suffix is included. Callers
that rely on the output fitting a fixed-width field (e.g. a UI label) are
seeing overflow.

## Steps to reproduce
```
truncate("hello world", 8)  # expected length 8, actual length 9
```

## Expected behavior
The returned string, including the suffix, should never exceed `max_len`
characters.

## Acceptance criteria
- Existing test suite passes (`pytest`).
- No regression to other `toylib` behavior.
