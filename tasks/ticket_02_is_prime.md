# TICKET-102: is_prime(1) incorrectly returns True

## Description
`is_prime(1)` returns `True`. By definition, 1 is not a prime number. This is
causing incorrect results in downstream code that filters "prime-only" lists.

## Steps to reproduce
```
is_prime(1)  # expected False, actual True
```

## Expected behavior
`is_prime` should return `False` for all n < 2.

## Acceptance criteria
- Existing test suite passes (`pytest`).
- No regression to other `toylib` behavior.
