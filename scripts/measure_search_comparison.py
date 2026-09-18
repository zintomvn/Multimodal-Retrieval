"""Measure identical live KIS requests with optional paid planning disabled."""
import json
from pathlib import Path
import time
import httpx
from measure_e2e_comparison import OUT, DATASET, summarize

def main():
    report={'scope':'KIS auto; planning, expansion, reranker disabled identically; semantic runtime availability unchanged; no labeled relevance claim','samples':[]}
    with httpx.Client(timeout=45) as client:
        for query in ['PHU XUAN GIA DINH','thành phố','Việt Nam']:
            for repeat in range(3):
                order=[('before',8021),('after',8022)]
                if repeat%2: order.reverse()
                for label,port in order:
                    started=time.perf_counter()
                    row={'variant':label,'query':query,'iteration':repeat}
                    try:
                        response=client.post(f'http://127.0.0.1:{port}/api/retrieval/search',json={
                            'dataset_id':DATASET,'query_name':'paired-e2e','query_text':query,'query_type':'KIS','top_k':20,
                            'options':{'use_agent_query_planning':False,'use_query_expansion':False,'use_reranker':False}})
                        row['status']=response.status_code
                        if response.is_success:
                            payload=response.json()
                            row['frame_ids']=[r['frame_id'] for r in payload['results']]
                            row['source_status']=payload.get('normalized_query',{}).get('source_status')
                        else: row['error']='HTTP '+str(response.status_code)
                    except httpx.HTTPError as exc:
                        row['error']=type(exc).__name__
                    row['ms']=round((time.perf_counter()-started)*1000,2)
                    report['samples'].append(row)
                    (OUT/'search-results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
                    print(label,repeat,round(row['ms']),row.get('status',row.get('error')),len(row.get('frame_ids',[])),flush=True)
    report['summary']={label:{**summarize([r['ms'] for r in report['samples'] if r['variant']==label]),'errors':sum('error' in r for r in report['samples'] if r['variant']==label)} for label in ['before','after']}
    (OUT/'search-results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')

if __name__=='__main__':main()
