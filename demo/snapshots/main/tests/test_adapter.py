"""The Redis specifics (batching via pipeline) live in the adapter, not the
domain. This test pins that."""

from unittest.mock import patch

from orders.adapters.redis_cache import RedisCache


def test_get_many_uses_a_single_pipeline(client) -> None:
    client.set("price:A", "10")
    client.set("price:B", "20")
    cache = RedisCache(client)
    with patch.object(client, "pipeline", wraps=client.pipeline) as spy:
        result = cache.get_many(["price:A", "price:B"])
    assert result == ["10", "20"]
    spy.assert_called_once()  # one round trip for all keys
