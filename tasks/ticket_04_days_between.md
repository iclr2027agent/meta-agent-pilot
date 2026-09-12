# TICKET-104: days_between() is off by one day

## Description
`days_between(start, end)` returns a value that's consistently one higher
than expected. Calling it with the same date for `start` and `end` returns
`1` instead of `0`.

## Steps to reproduce
```
days_between(date(2026, 1, 1), date(2026, 1, 1))  # expected 0, actual 1
days_between(date(2026, 1, 1), date(2026, 1, 8))  # expected 7, actual 8
```

## Expected behavior
`days_between` should return the exact number of whole days between the two
dates (i.e. `(end - start).days`).

## Acceptance criteria
- Existing test suite passes (`pytest`).
- No regression to other `toylib` behavior.
