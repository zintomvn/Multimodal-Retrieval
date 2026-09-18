"""Cached, read-only dependency probes. Configuration alone never means ready."""
from threading import Lock
from time import monotonic, time
from urllib.parse import urlparse

import httpx
from sqlalchemy import text

from app.core.budget import bounded_call

_lock = Lock()
_cached = None
_expires = 0.0


def probe(function):
    try:
        return {'status': 'ready', **bounded_call(function, max_seconds=3)}
    except Exception as exc:
        # SDK errors may contain credentials/URLs; expose only the exception type.
        return {'status': 'unavailable', 'reason': type(exc).__name__}


def readiness():
    global _cached, _expires
    with _lock:
        if _cached is not None and monotonic() < _expires:
            return _cached
        from app.db.session import SessionLocal
        from app.core.deps import get_text_client, get_vector_client, get_model_registry_service
        from app.core.index_catalog import annotation_index

        def metadata():
            with SessionLocal() as db:
                db.execute(text('SELECT 1'))
                count = db.execute(text('SELECT COUNT(*) FROM datasets')).scalar()
                return {'datasets': count, 'status': 'ready' if count else 'empty'}

        def annotations():
            adapter = get_text_client()
            if hasattr(adapter, 'client'):
                response = adapter.client.search(index=annotation_index(), size=0, request_timeout=2,
                    aggs={'sources': {'terms': {'field': 'source_type', 'size': 10}}}, track_total_hits=True)
                count = response['hits']['total']['value']
                return {'status': 'ready' if count else 'empty', 'documents': count,
                        'sources': {b['key']: b['doc_count'] for b in response.get('aggregations', {}).get('sources', {}).get('buckets', [])}}
            adapter.search(annotation_index(), '__readiness_probe__', 1)
            return {'status': 'reachable', 'reason': 'Coverage not verified'}

        checks = {'metadata': probe(metadata), 'text': probe(annotations)}
        try:
            registry = get_model_registry_service()
            for name, config in registry.registry.get('embedders', {}).items():
                if not config.get('enabled'):
                    continue
                def embedding(name=name):
                    model = registry.embedder_for(name)
                    # Listing models is read-only, unlike running paid inference.
                    base = getattr(model, 'base_url', '')
                    if not base:
                        return {'status': 'unverified', 'reason': 'Local model has no readiness probe'}
                    if urlparse(base).hostname not in {'localhost', '127.0.0.1', '::1'}:
                        return {'status': 'unverified', 'reason': 'Remote inference not invoked by readiness'}
                    response = httpx.get(base + '/models', timeout=2)
                    response.raise_for_status()
                    ids = [item.get('id') for item in response.json().get('data', [])]
                    return {'status': 'reachable' if getattr(model, 'model', '') in ids else 'unverified',
                            'reason': 'Model listing checked; inference and dimensions require preflight'}
                checks['embedding:' + name] = probe(embedding)
                collection = config.get('collection')
                if collection:
                    def vector(collection=collection, config=config):
                        adapter = get_vector_client()
                        adapter._ensure_client()
                        description = adapter.client.describe_collection(collection_name=collection, timeout=2)
                        dims = [int(field.get('params', {}).get('dim', 0)) for field in description.get('fields', []) if field.get('name') == 'vector']
                        if config.get('dim') and dims != [int(config['dim'])]:
                            return {'status': 'unavailable', 'reason': 'Vector dimension mismatch'}
                        return {'collection': collection, 'status': 'reachable', 'reason': 'Schema checked; search preflight required'}
                    checks['vector:' + name] = probe(vector)
        except Exception as exc:
            checks['models'] = {'status': 'unavailable', 'reason': type(exc).__name__}
        usable = checks['metadata']['status'] == 'ready' and checks['text']['status'] == 'ready'
        status = 'ready' if all(c['status'] == 'ready' for c in checks.values()) else 'degraded' if usable else 'unavailable'
        _cached = {'status': status, 'checked_at': time(), 'cache_seconds': 15, 'checks': checks}
        _expires = monotonic() + 15
        return _cached
