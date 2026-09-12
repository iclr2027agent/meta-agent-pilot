# TICKET-105: LRUCache evicts recently-used entries

## Description
`LRUCache` is evicting entries that were just read via `get()`, even though
they should count as recently used and be protected from eviction. It seems
like reading a key isn't updating its recency, only writing one does.

## Steps to reproduce
```
cache = LRUCache(capacity=2)
cache.put("a", 1)
cache.put("b", 2)
cache.get("a")       # "a" should now be most-recently-used
cache.put("c", 3)    # should evict "b" (least recently used), not "a"
cache.get("a")       # expected 1, actual None
```

## Expected behavior
Reading a key via `get()` should mark it as most-recently-used, same as
`put()` does, so eviction always removes the least-recently-*accessed* key.

## Acceptance criteria
- Existing test suite passes (`pytest`).
- No regression to other `toylib` behavior.
