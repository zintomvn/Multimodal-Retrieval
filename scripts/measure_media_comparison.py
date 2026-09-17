"""Paired context/evidence reads plus a small four-client gallery load check."""
from concurrent.futures import ThreadPoolExecutor
import json
from measure_e2e_comparison import OUT, DATASET, get, summarize

def main():
    _,gallery=get(8022,f'/api/media/frames?dataset_id={DATASET}&limit=1&present_only=true')
    frame=gallery['frames'][0]
    paths={'context':f'/api/media/frames/{frame["id"]}/context',
           'evidence':f'/api/media/videos/{frame["video_id"]}/evidence?seconds=0&frame_id={frame["id"]}'}
    report={}
    for name,path in paths.items():
        report[name]={}
        for variant,port in [('before',8021),('after',8022)]:
            samples=[get(port,path)[0] for _ in range(20)]
            report[name][variant]={**summarize(samples),'samples_ms':samples}
    path=f'/api/media/frames?dataset_id={DATASET}&limit=48&present_only=true'
    report['gallery_concurrency4']={}
    for variant,port in [('before',8021),('after',8022)]:
        with ThreadPoolExecutor(max_workers=4) as pool:
            samples=list(pool.map(lambda _:get(port,path)[0],range(40)))
        report['gallery_concurrency4'][variant]={**summarize(samples),'samples_ms':samples,'errors':0}
    (OUT/'media-http-results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:{v:{a:b for a,b in x.items() if a!='samples_ms'} for v,x in d.items()} for k,d in report.items()}))

if __name__=='__main__':main()
