# orders-api (coupling snapshot)

The same order-service, but the cache was inlined "for performance". Each of the
four domain modules talks to Redis directly:

- `pricing.py` — `pipeline()` to fetch all prices in one round trip
- `inventory.py` — `SET NX EX` for a self-expiring reservation hold
- `promotions.py` — a sorted set (`ZADD` / `ZRANGEBYSCORE`)
- `order_service.py` — catches `redis.exceptions.ConnectionError`

There is no `CachePort`; `config.py` parses the Redis URL directly.

## Run the tests

```
uv run pytest -q
```

## Future task: add an in-memory cache

The invariant "`orders.domain` does not import `redis`" is violated in four
places. Satisfying it means stripping Redis out of all four modules, inventing a
new abstraction, rewiring composition, and fixing the Redis-specific tests
(pipeline call counts, TTL values, sorted-set ordering). **Six to nine files.**
