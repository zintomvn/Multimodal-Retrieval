from app.core.read_cache import ReadCache

def test_metadata_cache_scope_expiry_and_failure(monkeypatch):
    import app.core.read_cache as module
    now = [0]
    monkeypatch.setattr(module, 'monotonic', lambda: now[0])
    cache = ReadCache(capacity=2, ttl=10)
    assert cache.get(('db','dataset-A'), lambda:5) == 5
    assert cache.get(('db','dataset-B'), lambda:9) == 9
    assert cache.get(('db','dataset-A'), lambda:7) == 5
    now[0] = 11
    assert cache.get(('db','dataset-A'), lambda:7) == 7
    assert cache.get(('other-db','dataset-A'), lambda:10) == 10
    assert len(cache.values) == 2
