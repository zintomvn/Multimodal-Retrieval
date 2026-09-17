"""Build the reviewable before/after report from captured raw results."""
import html
import json
from pathlib import Path
import re
import zipfile
from measure_e2e_comparison import OUT, summarize, get

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'output/e2e-comparison-20260917'

def cli_result(name):
    text=(OUT/name).read_text(encoding='utf-8-sig')
    return json.loads(text.split('### Result',1)[1].split('### Ran',1)[0].strip())

def main():
    DEST.mkdir(exist_ok=True)
    raw={name:json.loads((OUT/file).read_text(encoding='utf-8')) for name,file in [
        ('http','http-results.json'),('search','search-results.json'),('media_http','media-http-results.json'),('regressions_initial','regressions.json'),('regression_isolated','regressions-search_reliability.json')]}
    raw.update(browser=cli_result('browser-results.txt'),live_search=cli_result('live-search-browser.txt'),media_browser=cli_result('media-browser.txt'),bootstrap_repeated=cli_result('bootstrap-repeated.txt'))
    raw['readiness']=get(8022,'/api/readyz')[1]
    raw['measured_shas']={'before':'5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f','after':'03d16085fd91ebbf36ec08a55a3657239eeec8eb'}
    raw['csv_exact']={v:(ROOT/f'output/playwright/e2e-{v}-export.download').read_text().strip()=='L21_V001,0' for v in ['before','after']}
    with zipfile.ZipFile(ROOT/'output/playwright/optimization-live-export.zip') as archive:
        raw['zip']={'files':archive.namelist(),'valid':all(archive.read(n).decode().strip()=='L21_V001,0' for n in archive.namelist())}
    samples=raw['search']['samples']
    raw['ranking_parity']=all(samples[i]['frame_ids']==samples[i+1]['frame_ids'] for i in range(0,len(samples),2))
    metrics=[]
    for label,key in [('API gallery (48 frame)','gallery'),('API tìm video (12 frame)','video_lookup')]:
        metrics.append((label,raw['http']['measurements'][key]['before'],raw['http']['measurements'][key]['after']))
    for label,key in [('API context','context'),('API evidence','evidence'),('API gallery, 4 client đồng thời','gallery_concurrency4')]:
        metrics.append((label,raw['media_http'][key]['before'],raw['media_http'][key]['after']))
    metrics.append(('API KIS top 20 (3 query x 3)',raw['search']['summary']['before'],raw['search']['summary']['after']))
    for label,key in [('Web: điều hướng -> 48 card hiện','gallery_ms'),('Web: click -> mở preview','preview_ms')]:
        metrics.append((label,*[summarize(raw['browser']['variants'][v][key]) for v in ['before','after']]))
    metrics.append(('Web: click Search -> kết quả KIS',*[summarize([r['ms'] for r in raw['live_search'] if r['variant']==v and r['flow']=='KIS live search']) for v in ['before','after']]))
    lines=['| Luồng | N / bản | p50 trước | p50 sau | p95 trước | p95 sau | Thay đổi p95 |','|---|---:|---:|---:|---:|---:|---:|']
    for label,b,a in metrics:
        delta=(a['p95_ms']/b['p95_ms']-1)*100
        lines.append(f"| {label} | {b['n']} | {b['p50_ms']:.2f} | {a['p50_ms']:.2f} | {b['p95_ms']:.2f} | {a['p95_ms']:.2f} | {delta:+.1f}% |")
    checklist=[]
    names={'gallery48':'Hiện đủ 48 card','previewFocus':'Focus vào preview','escapeClosesPreview':'Escape đóng preview','selection':'Pick frame','exportDownloaded':'Tải CSV','draftReload':'Giữ draft khi reload','selectionReload':'Giữ frame đã chọn khi reload'}
    for key,label in names.items():checklist.append((label,*[raw['browser']['variants'][v]['checks'][key] for v in ['before','after']]))
    checklist.append(('CSV đúng L21_V001,0',raw['csv_exact']['before'],raw['csv_exact']['after']))
    for label,predicate in [('KIS 3 lần trả 50 kết quả',lambda v:all(r['status']==200 and r['results']==50 for r in raw['live_search'] if r['variant']==v and r['flow']=='KIS live search')),('Video lookup đúng mã',lambda v:next(r['correctVideo'] for r in raw['live_search'] if r['variant']==v and r['flow']=='Video lookup')),('Video thực sự tăng thời gian khi play',lambda v:next(r['playback']['advanced'] for r in raw['media_browser'] if r['variant']==v)),('Có 3 mục OCR / ASR / Caption',lambda v:all(next(r['evidenceSections'] for r in raw['media_browser'] if r['variant']==v).values())),('Không có uncaught JS error trong suite chung',lambda v:not raw['browser']['variants'][v]['pageErrors'])]:
        checklist.append((label,predicate('before'),predicate('after')))
    for i,width in enumerate([390,900,1024,1152,1180,1440]):
        checklist.append((f'Search không bị drawer che ở {width}px',*[raw['browser']['variants'][v]['responsive'][i]['searchUncovered'] for v in ['before','after']]))
    raw['acceptance_checks']=[{'name':n,'before':b,'after':a} for n,b,a in checklist]
    (DEST/'raw-results.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding='utf-8')
    score_before=sum(b for _,b,_ in checklist);score_after=sum(a for _,_,a in checklist)
    sections=[('Kết quả chính',f'''Gallery cải thiện rõ, search KIS chưa nhanh hơn đáng kể. Bộ smoke E2E chung đạt **{score_before}/{len(checklist)} trước -> {score_after}/{len(checklist)} sau**; đây là các tiêu chí kiểm tra cụ thể bên dưới, không phải tỷ lệ hoàn thành toàn bộ 22 mục tối ưu.

Không có thay đổi source ứng dụng trong đợt đo này. Bản trước là main `5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f`; bản sau là `03d16085fd91ebbf36ec08a55a3657239eeec8eb`, nhánh `feat/ui-search-optimization`. Các commit báo cáo/test sau đó không thuộc code được đo.'''),
    ('Số liệu trước / sau','Đơn vị: ms. Số âm là nhanh hơn; số dương là chậm hơn. p95 dùng nearest-rank; mẫu nhỏ chỉ là tín hiệu, chưa đủ để kết luận có ý nghĩa thống kê.\n\n'+'\n'.join(lines)+'''

Thời gian tải CSV: 315 -> 280 ms (chỉ 1 lần / bản, không suy rộng). Video lookup trên UI: 83 -> 73 ms (1 lần / bản). Nội dung CSV hai bản giống hệt `L21_V001,0`.

Gallery API được đo xen kẽ hai bản, đảo thứ tự mỗi lượt; 30 lượt sau lần quan sát đầu. Gallery/count cache của bản sau có TTL 10 giây, gồm cả lượt có thể hết hạn cache. Lần quan sát đầu: 382,38 -> 2254,26 ms; main đã được mở bằng trình duyệt trước đó, nên KHÔNG được dùng cặp này làm số liệu cold-start. Không đo OS/disk cold-start; không xóa cache máy người dùng.

Browser đo production build, gồm mọi lượt trong chuỗi (10 gallery, 5 preview, 3 KIS), không loại outlier. Gallery browser p95 và preview hiện chậm hơn; chỉ p50 gallery cải thiện rõ. Các luồng browser/context/evidence và tải 4 client chạy trước rồi sau, không xen kẽ; nhiễu môi trường/remote vẫn có thể ảnh hưởng.'''),
    ('E2E thật và dữ liệu đúng','Trình duyệt Chromium, API thật, SQLite thật, Elasticsearch thật; không mock trong suite chung.\n\n| Tiêu chí | Trước | Sau |\n|---|---|---|\n'+'\n'.join(f"| {n} | {'PASS' if b else 'FAIL'} | {'PASS' if a else 'FAIL'} |" for n,b,a in checklist)+'''

9/9 cặp KIS API giữ nguyên top 20 và thứ tự. Gallery/lookup giữ nguyên danh sách và thứ tự frame. API KIS: 0/9 lỗi mỗi bản; KIS qua browser: 0/3 lỗi mỗi bản. Tổng HTTP trong các benchmark tuần tự/tải nhỏ: 151 request mỗi bản, 0 lỗi. Con số này không bao gồm traffic media từ browser.

ZIP mới: 3 CSV, cả 3 dòng đúng frame đã chọn, không file rỗng. Đây là test tính năng mới, không có phần trăm cải thiện so với main.

Mở lại cùng preview trong TTL: main gọi lại context + evidence **2 request**, sau tối ưu **0 request**. Preview URL vẫn được gọi; bản sau còn dùng endpoint stream video, nên không được diễn giải là mọi request media đều giảm về 0. Video thật tiến được khoảng 0,56 giây trong kiểm tra play ngắn ở cả hai bản; chưa kiểm tra phát dài/đứt mạng/seek toàn video.'''),
    ('Regression có kiểm soát và độ tin cậy','''4 suite bổ sung dùng API thật đã PASS: workspace, keyboard/layout, responsive/settings/contrast, ZIP. 4 suite có response giả lập đã PASS sau chạy riêng: mất kết nối/stale response/duplicate Enter, QA answer-on-demand, sửa TRAKE, chặn export sai/lỗi. QA và TRAKE ở đây chỉ chứng minh hành vi UI, không chứng minh chất lượng model.

Không giấu lỗi harness: suite mất kết nối timeout 2 lần khi chờ Retry data; sau đó chạy riêng PASS và 5/5 vòng healthy -> outage đều trả đúng lỗi, 0 card, Search disabled. Runner đã tách session từng suite và mở about:blank để tránh startup/state chồng nhau. Chưa có bằng chứng đủ để quy kết lỗi sản phẩm cụ thể; cần giữ test lặp trong CI.

Suite media bổ sung từng mất session CLI 3 lần. Bỏ `new URL()` khỏi callback theo dõi request trong sandbox của CLI thì cùng luồng hoàn tất, gồm playback thật. Đây là vấn đề harness đã sửa; không được tính 3 lần đó là lỗi HTTP ứng dụng.

Hai trường gridRenders... trong dữ liệu QA fixture KHÔNG dùng làm bằng chứng render ở production: instrumentation chỉ tồn tại ở dev. Không công bố INP/LCP/CLS từ lần test này.'''),
    ('Môi trường và giới hạn','''Ngày đo: 17/09/2026. Cùng máy Windows, production Vite build, hai API độc lập tại 8021/8022 và hai web 5174/5175. Dùng SQLite backup một lần rồi copy sang hai DB; 310.301 frame, 873 video. Elasticsearch dùng chung, chỉ đọc: caption 310.212; OCR 291.909; ASR 181.714; tổng 783.835 document. Phiên đang dùng ở 8010/5173 và dự án cổng 8000 được giữ nguyên.

API benchmark: KIS auto, top_k=20; planning/expansion/reranker tắt giống nhau. UI KIS: top_k=50, tắt Expansion và Agent plan bằng Settings, reranker giữ mặc định bật ở cả hai bản. Không lấy hai nhóm này so chéo. HTTP latency bao gồm client + server; không phải thời gian thuần SQL.

Readiness sau tối ưu vẫn degraded: text ready, OpenCLIP endpoint unavailable, SigLIP remote inference chưa xác minh. Vì vậy chỉ kết luận tốc độ/fallback text và tính ổn định top-k trong môi trường này. Chưa có Recall@K/MRR có ground truth hợp lệ để kết luận search chính xác hơn. Không có kết luận về semantic search, QA model thật, TRAKE model thật hay concurrent ingestion. Không nhập lại data, không gọi job ingestion, không benchmark tải ghi lên ES.

N=3/5/9/10 ở một số luồng là ít. Chưa có confidence interval, test mạng WAN/mobile, nhiều người dùng thực tế hoặc soak test. Không dùng p95 mẫu nhỏ để cam kết SLA.'''),
    ('Điểm cần làm tiếp','''1. **Search KIS còn khoảng 5-6 giây**: khôi phục đúng OpenCLIP runtime, đo từng stage embedding/vector/text/rerank, rồi chạy lại golden set có nhãn video + cửa sổ frame. Chưa đạt bằng chứng tăng tốc tổng thể.
2. **Preview/context có dấu hiệu chậm hơn**: profile production khi mở/đóng modal, tách URL/stream/focus/React commit. Đo lại xen kẽ >=30 lượt với cùng media cache; chỉ sửa khi có trace xác định nguyên nhân.
3. **Gallery tail trên browser chưa giảm** dù API rất nhanh: tách tải thumbnail, decode ảnh, JS/layout và chi phí điều hướng; kiểm tra network waterfall và long task.
4. **Ổn định harness**: giữ test lỗi bootstrap theo hai trạng thái phiên mới/khôi phục, chờ hoàn tất điều hướng, tách context. Hai timeout ban đầu cần được theo dõi trong CI.
5. **Bốn acceptance còn mở**: semantic quality, QA thật, TRAKE thật, ingestion đồng thời. Các PASS fixture không đóng được bốn mục này.'''),
    ('Tái chạy và bằng chứng','''Scripts chính: `scripts/prepare_e2e_comparison.py`, `measure_e2e_comparison.py`, `measure_media_comparison.py`, `measure_search_comparison.py`, `check_e2e_comparison.js`, `check_live_search_comparison.js`, `check_live_media_comparison.js`, `run_browser_regressions.py`, `build_e2e_report.py`.

Chuẩn bị baseline worktree ở SHA main nêu trên, cùng dependencies và .env local. Chạy prepare một lần vào thư mục fixture mới; không tái sử dụng DB đã có query-run để tuyên bố snapshot sạch. Không chạy hai bộ benchmark đồng thời. Browser dùng `rtk proxy npx --yes --package @playwright/cli playwright-cli -s=<session riêng> run-code --filename=<script>`; mở about:blank trước, chờ lệnh trước xong.

Dữ liệu tổng hợp: [raw-results.json](raw-results.json). Screenshot và CSV/ZIP: `output/playwright/e2e-*-*.png`, `e2e-before-export.download`, `e2e-after-export.download`, `optimization-live-export.zip`. Các file này ở local, không cần gửi credentials/log backend.

Thời gian đo là kết quả quan sát trên máy này; không lấy số benchmark cũ ở phiên trước ghép với số mới để tính cải thiện.''')]
    md='# E2E và số liệu trước / sau tối ưu\n\n'+'\n\n'.join('## '+title+'\n\n'+content for title,content in sections)+'\n'
    (DEST/'report.md').write_text(md,encoding='utf-8')
    template=Path('C:/Users/Admin/.codex/skills/artifact-style/template.html').read_text(encoding='utf-8')
    head=template.split('<body>',1)[0]
    head=re.sub(r'<title>.*?</title>','<title>E2E trước / sau tối ưu</title>',head,flags=re.S)
    def render(content):
        result=[];table=[]
        for line in content.splitlines()+['']:
            if line.startswith('|'):
                if not re.match(r'^\|[-: |]+\|$',line):table.append([x.strip() for x in line.strip('|').split('|')])
                continue
            if table:
                result.append('<div style="overflow:auto"><table>'+''.join('<tr>'+''.join(f'<{"th" if i==0 else "td"}>{html.escape(cell)}</{"th" if i==0 else "td"}>' for cell in cells)+'</tr>' for i,cells in enumerate(table))+'</table></div>');table=[]
            if line:
                text=html.escape(line)
                text=re.sub(r'\*\*(.*?)\*\*',r'<strong>\1</strong>',text)
                text=re.sub(r'`(.*?)`',r'<code>\1</code>',text)
                result.append('<p>'+text+'</p>')
        return ''.join(result)
    body='<body><main style="max-width:1320px;margin:auto;padding:32px"><p>17/09/2026 // LIVE E2E + PAIRED BENCHMARK</p><h1>E2E trước / sau tối ưu</h1><p>Gallery nhanh hơn rõ. Search chưa cải thiện đáng kể. Có cả số liệu chậm hơn và giới hạn kiểm chứng.</p>'
    body+=''.join(f'<details class="node" open><summary class="row"><span class="lbl">{html.escape(title)}</span></summary><div class="kids">{render(content)}</div></details>' for title,content in sections)
    body+='<p><a href="report.md">Markdown</a> | <a href="raw-results.json">Raw JSON</a></p></main></body></html>'
    (DEST/'report.html').write_text(head+body,encoding='utf-8')
    print(json.dumps({'before_checks':score_before,'after_checks':score_after,'total':len(checklist),'ranking_parity':raw['ranking_parity'],'csv':raw['csv_exact'],'zip':raw['zip']['valid']}))

if __name__=='__main__':main()
