# orders-api (main snapshot)

A tiny order-service with a **port boundary** for its cache.

- `orders/domain/` depends only on `CachePort` (`orders/domain/ports.py`).
  It never imports `redis`.
- `orders/adapters/redis_cache.py` implements `CachePort` with all the
  Redis-specific semantics (pipeline, `SET NX EX`, sorted sets).
- `orders/composition.py` is the single place that picks an implementation.

## Run the tests

```
uv run pytest -q
```

## Future task: add an in-memory cache

Create `orders/adapters/memory_cache.py` implementing `CachePort`, then change
the one starred line in `composition.py`. **Two files.** The invariant
"`orders.domain` does not import `redis`" already holds.
