from pathlib import Path
import sys
import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.telemetry import RequestTimingMiddleware, RequestTrace, current_trace, stage, timed


def test_sync_route_shares_trace_and_exposes_timings():
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.get('/probe')
    def probe():
        with stage('metadata'):
            pass
        return {'ok': True}

    client = TestClient(app)
    first, second = client.get('/probe'), client.get('/probe')
    assert first.headers['x-request-id'] != second.headers['x-request-id']
    assert 'metadata;dur=' in first.headers['server-timing']
    assert 'request;dur=' in first.headers['server-timing']
    assert first.json() == {'ok': True}
    assert current_trace.get() is None


def test_failed_stage_is_counted_and_original_exception_preserved():
    trace = RequestTrace()
    token = current_trace.set(trace)
    @timed('provider')
    def fail():
        raise ValueError('failure')
    try:
        with pytest.raises(ValueError, match='failure'):
            fail()
        assert trace.calls == {'provider': 1}
        assert trace.timings['provider'] >= 0
    finally:
        current_trace.reset(token)


def test_concurrent_requests_have_isolated_stage_maps():
    async def run():
        app = FastAPI()
        app.add_middleware(RequestTimingMiddleware)

        @app.get('/probe/{name}')
        async def probe(name: str):
            with stage(name):
                await asyncio.sleep(0)
            return {'ok': True}

        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            a, b = await asyncio.gather(client.get('/probe/alpha'), client.get('/probe/beta'))
        assert 'alpha;' in a.headers['server-timing'] and 'beta;' not in a.headers['server-timing']
        assert 'beta;' in b.headers['server-timing'] and 'alpha;' not in b.headers['server-timing']
    asyncio.run(run())
