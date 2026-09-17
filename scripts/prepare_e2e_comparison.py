"""Start isolated main/optimized stacks against identical SQLite snapshots.

Local test helper; does not modify the active database or Elasticsearch.
Processes and raw logs are recorded under ignored data/e2e-comparison-20260917.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/e2e-comparison-20260917'
BASE = ROOT.parent / 'Multimodal-Retrieval-baseline-20260917'

def main():
    OUT.mkdir(exist_ok=True)
    source = ROOT / 'data/import-20260916/source/dev_search_local.db'
    before = OUT / 'before.db'
    after = OUT / 'after.db'
    if before.exists() or after.exists():
        raise SystemExit('Fixture DB already exists. Archive the previous test directory before preparing fresh snapshots.')
    for port in [8021,8022,5174,5175]:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',port)) == 0:
                raise SystemExit(f'Test port {port} is already in use; no process was changed.')
    with sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True) as src, sqlite3.connect(before) as dst:
        src.backup(dst)
    shutil.copy2(before, after)
    env = {**os.environ, **{k:v for k,v in dotenv_values(ROOT / '.env').items() if v is not None}}
    for key, value in list(env.items()):
        if key.endswith(('_PATH', '_FILE')) and value and (ROOT / value).is_file():
            env[key] = str((ROOT / value).resolve())
    env.update(SKIP_DB_INIT='true', TEXT_SEARCH_BACKEND='elasticsearch')
    records = []
    for label, repo, api, web in [('before', BASE, 8021, 5174), ('after', ROOT, 8022, 5175)]:
        local = {**env, 'DATABASE_URL': 'sqlite:///' + (OUT / f'{label}.db').as_posix(),
                 'DATA_ROOT': str(OUT / (label+'-artifacts')), 'API_PORT':str(api),
                 'VITE_API_BASE_URL':'', 'VITE_API_PROXY_TARGET':f'http://127.0.0.1:{api}'}
        (OUT / (label+'-artifacts')).mkdir(exist_ok=True)
        node_modules = repo / 'apps/web/node_modules'
        if not node_modules.exists():
            subprocess.run(['cmd','/c','mklink','/J',str(node_modules),str(ROOT/'apps/web/node_modules')],check=True,capture_output=True)
        vite = ROOT/'apps/web/node_modules/vite/bin/vite.js'
        build = OUT / (label+'-web')
        with (OUT/(label+'-build.log')).open('w') as log:
            subprocess.run(['node',str(vite),'build','--outDir',str(build)],cwd=repo/'apps/web',env=local,stdout=log,stderr=log,check=True)
        for kind, command, cwd in [
            ('api',[sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(api)],repo/'apps/backend'),
            ('web',['node',str(vite),'preview','--outDir',str(build),'--host','127.0.0.1','--port',str(web),'--strictPort'],repo/'apps/web')]:
            with (OUT/f'{label}-{kind}.log').open('w') as log:
                proc = subprocess.Popen(command,cwd=cwd,env=local,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            records.append({'variant':label,'kind':kind,'pid':proc.pid,'port':api if kind=='api' else web})
            (OUT/'processes.json').write_text(json.dumps(records,indent=2))
        for attempt in range(60):
            try:
                with urlopen(f'http://127.0.0.1:{web}/api/datasets',timeout=2) as response:
                    assert response.status == 200
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError(f'{label} stack not ready; inspect local logs')
        print(f'{label}: API {api}, production web {web} ready',flush=True)

if __name__ == '__main__':
    main()
