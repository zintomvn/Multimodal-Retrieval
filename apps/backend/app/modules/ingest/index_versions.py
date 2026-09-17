"""Read-only index manifest and explicit, reversible alias promotion.

Use a new alias (e.g. annotations_read); never replace a physical index with an
alias. Import into a versioned physical index, audit the manifest, then promote.
"""
import argparse
import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.core.deps import get_text_client


def manifest(client, index):
    mappings = client.indices.get_mapping(index=index)
    fields = [m.get('mappings', {}).get('properties', {}).get('model_version', {}) for m in mappings.values()]
    model_field = 'model_version.keyword' if any(f.get('type') == 'text' for f in fields) else 'model_version'
    response = client.search(index=index, size=0, track_total_hits=True, request_timeout=10,
        aggs={'sources': {'terms': {'field': 'source_type', 'size': 20}},
              'models': {'terms': {'field': model_field, 'size': 100}}})
    if response.get('timed_out') or response.get('_shards', {}).get('failed', 0):
        raise ValueError('Index coverage could not be verified')
    settings = get_settings()
    return {'index': index, 'documents': response['hits']['total']['value'],
        'sources': {b['key']: b['doc_count'] for b in response['aggregations']['sources']['buckets']},
        'models': {b['key']: b['doc_count'] for b in response['aggregations']['models']['buckets']},
        'registry_sha256': hashlib.sha256(settings.model_registry_path.read_bytes()).hexdigest()}


def promote(client, alias, target, expected_current, expected_manifest):
    if alias == target or alias == expected_current:
        raise ValueError('Read alias must differ from physical index names')
    actual = manifest(client, target)
    if actual != expected_manifest or actual['documents'] <= 0:
        raise ValueError('Target changed or is empty; regenerate and review the manifest')
    if not all(actual['sources'].get(source, 0) > 0 for source in ('ocr', 'asr', 'caption')):
        raise ValueError('Target must contain OCR, ASR and caption coverage')
    if expected_current:
        current = set(client.indices.get_alias(name=alias))
        if current != {expected_current}:
            raise ValueError('Alias changed since review; refusing promotion')
    elif client.indices.exists_alias(name=alias):
        raise ValueError('Alias already exists; provide its expected current index')
    actions = []
    if expected_current:
        actions.append({'remove': {'index': expected_current, 'alias': alias, 'must_exist': True}})
    actions.append({'add': {'index': target, 'alias': alias}})
    client.indices.update_aliases(actions=actions)
    if set(client.indices.get_alias(name=alias)) != {target}:
        raise RuntimeError('Alias verification failed')
    return {'alias': alias, 'previous': expected_current, 'current': target}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index')
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--alias')
    parser.add_argument('--expected-current')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    client = get_text_client().client
    if args.apply:
        if not args.alias:
            parser.error('--apply requires --alias and a reviewed manifest')
        print(json.dumps(promote(client, args.alias, args.index, args.expected_current,
            json.loads(args.manifest.read_text(encoding='utf-8')))))
    else:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest(client, args.index), indent=2), encoding='utf-8')
        print('Read-only manifest written; no alias changed')
