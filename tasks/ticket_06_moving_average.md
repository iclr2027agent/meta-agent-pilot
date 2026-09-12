# TICKET-106: moving_average returns inflated values for narrow windows

## Description
`moving_average()` in `toylib.statutils` is supposed to return the simple
moving average over consecutive windows of a given size, but the values it
returns are consistently too high whenever the window is smaller than the
full length of the input list.

## Steps to reproduce
```
from toylib.statutils import moving_average
moving_average([1, 2, 3, 4, 5], 2)
# expected: [1.5, 2.5, 3.5, 4.5]
# actual:   [3.0, 4.5, 6.0, 4.5]
```

## Expected behavior
Each element of the result should be the average of exactly `window`
consecutive values ending at that position — no more, no fewer. A window
of size `w` starting at index `i` must average exactly `values[i:i+w]`.

## Acceptance criteria
- Existing test suite passes (`pytest`).
- No regression to other `toylib` behavior.
- Behavior must remain correct for edge cases (e.g. very small or very
  large window sizes relative to the input), not only the scenario shown
  above.
