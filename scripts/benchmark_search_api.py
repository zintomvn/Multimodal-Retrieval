"""Reproducible read/search benchmark against reviewed golden query JSON.

Search creates query-run records. No model calls unless --models is supplied.
Input: [{"id":"ocr-1","query":"...","source":"ocr","expected_frames":["..."]}].
"""
import argparse
import json
import statistics
from pathlib import Path
from time import perf_counter
import httpx


def evaluate(result_ids, expected):
    expected = set(expected)
    matched = expected.intersection(result_ids)
    first = next((i for i, value in enumerate(result_ids, 1) if value in expected), None)
    return {'recall': len(matched)/len(expected) if expected else None,
            'reciprocal_rank': 1/first if first else 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('golden', type=Path)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--base', default='http://127.0.0.1:8010')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', action='store_true')
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    cases = json.loads(args.golden.read_text(encoding='utf-8'))
    if not cases or any(not c.get('expected_frames') for c in cases):
        parser.error('Golden queries and expected_frames must be nonempty')
    rows = []
    with httpx.Client(timeout=45) as client:
        for case in cases:
            for iteration in range(args.repeat):
                start = perf_counter()
                response = client.post(args.base+'/api/retrieval/search', json={
                    'dataset_id': args.dataset, 'query_name': 'benchmark-'+case['id'],
                    'query_text': case['query'], 'query_type': case.get('type', 'KIS'), 'top_k': 20,
                    'options': {'source_mode': case.get('source', 'auto'), 'defer_qa': True,
                        'use_agent_query_planning': args.models, 'use_query_expansion': args.models,
                        'use_reranker': args.models, 'temporal_events': case.get('events', [])}})
                elapsed = (perf_counter()-start)*1000
                if not response.is_success:
                    rows.append({'id':case['id'], 'iteration':iteration, 'ms':round(elapsed,2),
                        'http_status':response.status_code, 'error':response.json(), 'recall':0, 'reciprocal_rank':0})
                    continue
                payload = response.json()
                ids = [r['frame_id'] for r in payload['results']]
                rows.append({'id': case['id'], 'iteration': iteration, 'ms': round(elapsed, 2),
                    'request_id': response.headers.get('x-request-id'), 'timing': response.headers.get('server-timing'),
                    'source_status': payload['normalized_query'].get('source_status'),
                    **evaluate(ids, case['expected_frames'])})
    times = sorted(row['ms'] for row in rows)
    report = {'scope': 'Golden labels supplied by operator; fixture results are not real semantic recall',
        'p50_ms': statistics.median(times), 'p95_ms': times[min(len(times)-1, int(len(times)*.95))],
        'mean_recall_at_20': statistics.mean(r['recall'] for r in rows if r['recall'] is not None),
        'mrr_at_20': statistics.mean(r['reciprocal_rank'] for r in rows),
        'error_rate': sum('error' in row for row in rows)/len(rows), 'samples': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'samples'}))


if __name__ == '__main__':
    main()
