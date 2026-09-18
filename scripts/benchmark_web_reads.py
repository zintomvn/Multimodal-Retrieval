"""Read-only local baseline. No model calls, imports, or search history writes."""
import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import urlencode
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8010')
    parser.add_argument('--samples', type=int, default=30)
    parser.add_argument('--video-code', default='L26_V191')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error('--samples must be positive')

    def get(path):
        started = perf_counter()
        with urlopen(args.base_url.rstrip('/') + path, timeout=30) as response:
            payload = json.load(response)
            headers = {key: response.headers.get(key) for key in ('X-Request-ID', 'Server-Timing')}
        return payload, round((perf_counter() - started) * 1000, 2), headers

    datasets, _, _ = get('/api/datasets')
    dataset = next((d for d in datasets['datasets'] if d['status'] == 'READY'), None)
    if not dataset:
        raise SystemExit('No READY dataset; baseline not run.')
    params = {'dataset_id': dataset['id'], 'limit': 48, 'present_only': 'true'}
    paths = {'gallery': '/api/media/frames?' + urlencode(params),
             'video_lookup': '/api/media/frames?' + urlencode({**params, 'limit': 12, 'video_code': args.video_code, 'frame_idx': 596})}
    report = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'dataset_id': dataset['id'],
              'scope': 'HTTP metadata reads only; no retrieval quality or model readiness claim', 'measurements': {}}
    for label, path in paths.items():
        _, cold_ms, _ = get(path)
        samples = [get(path) for _ in range(args.samples)]
        values = sorted(s[1] for s in samples)
        report['measurements'][label] = {
            'first_ms': cold_ms, 'samples': len(values), 'median_ms': statistics.median(values),
            'p95_ms': values[math.ceil(len(values) * .95) - 1], 'warm_ms': values,
            'last_diagnostics': samples[-1][2], 'last_frame_count': len(samples[-1][0]['frames'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
