"""Run existing browser regressions on the isolated optimized production stack."""
import json
import argparse
from pathlib import Path
import shutil
import subprocess
from measure_e2e_comparison import OUT

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only')
    args=parser.parse_args()
    rows=[]
    for name,scope in [('workspace','live'),('keyboard_layout','live'),('responsive_controls','live'),('export_zip','live'),('search_reliability','controlled failures'),('qa_workflow','mock model'),('trake_editor','mock retrieval'),('export_validation','controlled failures')]:
        if args.only and name!=args.only: continue
        source=(ROOT/f'scripts/check_{name}.js').read_text(encoding='utf-8')
        target=OUT/f'check_{name}.js'
        target.write_text(source.replace('127.0.0.1:5173','127.0.0.1:5175').replace('127.0.0.1:8010','127.0.0.1:8022'),encoding='utf-8')
        command=[shutil.which('npx.cmd'),'--yes','--package','@playwright/cli','playwright-cli',f'-s=regression-{name}']
        subprocess.run([*command,'open','about:blank'],cwd=ROOT,capture_output=True,check=True,timeout=60)
        try:
            result=subprocess.run([*command,'run-code',f'--filename={target}'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120)
        finally:
            subprocess.run([*command,'close'],cwd=ROOT,capture_output=True,timeout=30)
        (OUT/f'{name}-result.txt').write_text(result.stdout,encoding='utf-8')
        passed=result.returncode==0 and '### Error' not in result.stdout and '### Result' in result.stdout
        row={'name':name,'scope':scope,'pass':passed}
        if passed:
            raw=result.stdout.split('### Result',1)[1].split('### Ran',1)[0].strip()
            try:row['result']=json.loads(raw)
            except ValueError:row['result']=raw
        rows.append(row)
        (OUT/('regressions-'+args.only+'.json' if args.only else 'regressions.json')).write_text(json.dumps(rows,indent=2),encoding='utf-8')
        print(name,passed,flush=True)

if __name__=='__main__':main()
