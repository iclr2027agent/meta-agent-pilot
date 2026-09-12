# TICKET-109: Inventory.reserve() leaves stock negative instead of unchanged

## Description
`Inventory.reserve()` is supposed to leave stock unchanged when there isn't
enough of it to satisfy the reservation, but it currently mutates stock
before checking availability — an insufficient reservation still decrements
stock, and can drive it negative.

## Steps to reproduce
```
from toylib.inventory import Inventory
inv = Inventory({'a': 2})
inv.reserve('a', 5)
# expected: returns False, inv.stock['a'] stays 2
# actual:   returns False, inv.stock['a'] becomes -3
```

## Expected behavior
A reservation that cannot be satisfied must return False and leave stock
completely unchanged.

## Acceptance criteria
- Existing test suite passes (pytest).
- No regression to other toylib behavior.
- Behavior must remain correct beyond the single scenario shown above.
