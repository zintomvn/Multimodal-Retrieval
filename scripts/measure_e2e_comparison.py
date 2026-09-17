"""Paired HTTP measurements; stacks prepared by prepare_e2e_comparison.py."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
import urllib.request

OUT = Path(__file__).resolve().parents[1] / 'data/e2e-comparison-20260917'
DATASET = '917efbbe-44b8-4476-98ef-3e7ec7ca7958'

def get(port, path):
    start = time.perf_counter()
    with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=30) as response:
        data = json.load(response)
    return round((time.perf_counter()-start)*1000, 2), data

def summarize(values):
    ordered = sorted(values)
    return {'n':len(values), 'p50_ms':statistics.median(values), 'p95_ms':ordered[math.ceil(.95*len(values))-1], 'min_ms':min(values), 'max_ms':max(values)}

def main():
    paths = {'gallery': f'/api/media/frames?dataset_id={DATASET}&limit=48&present_only=true',
             'video_lookup': f'/api/media/frames?dataset_id={DATASET}&limit=12&present_only=true&video_code=L26_V191&frame_idx=596'}
    report = {'scope':'Paired sequential requests; alternating variant order; production stacks; identical initial DB snapshots; OS cache uncontrolled', 'measurements':{}}
    for name, path in paths.items():
        group = {v:{'samples_ms':[], 'fingerprints':[]} for v in ['before','after']}
        for iteration in range(31):
            variants = [('before',8021),('after',8022)]
            if iteration % 2:
                variants.reverse()
            for label, port in variants:
                ms, payload = get(port,path)
                ids = [r['id'] if 'id' in r else r['frame_id'] for r in payload['frames']]
                digest = hashlib.sha256(json.dumps(ids).encode()).hexdigest()
                if iteration == 0:
                    group[label]['first_observed_ms'] = ms
                else:
                    group[label]['samples_ms'].append(ms)
                group[label]['fingerprints'].append(digest)
                group[label]['frame_count'] = len(ids)
        for label in group:
            group[label].update(summarize(group[label]['samples_ms']))
        group['ordered_frame_parity'] = len(set(group['before']['fingerprints']+group['after']['fingerprints']))==1
        report['measurements'][name] = group
        (OUT/'http-results.json').write_text(json.dumps(report,indent=2))
        print(name,json.dumps({v:{k:x for k,x in d.items() if k not in ['samples_ms','fingerprints']} if isinstance(d,dict) else d for v,d in group.items()}),flush=True)

if __name__ == '__main__':
    main()
