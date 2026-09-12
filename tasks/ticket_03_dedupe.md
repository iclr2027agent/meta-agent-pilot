# TICKET-103: dedupe_preserve_order() does not preserve order

## Description
`dedupe_preserve_order()` is documented to preserve the order in which items
first appear, but the returned list order appears arbitrary/inconsistent
between runs.

## Steps to reproduce
```
dedupe_preserve_order([3, 1, 3, 2, 1, 4])  # expected [3, 1, 2, 4]
```

## Expected behavior
Duplicates are removed, and the remaining items appear in the order they
were first seen in the input list.

## Acceptance criteria
- Existing test suite passes (`pytest`).
- No regression to other `toylib` behavior.
