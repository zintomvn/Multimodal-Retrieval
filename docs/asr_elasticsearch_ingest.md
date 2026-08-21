# ASR Elasticsearch Ingest

Tai lieu nay dung de nap ASR tu `data/extracted/asr/` vao Elasticsearch local, de backend co the search text song song voi visual CLIP baseline.

## Dau vao

- ASR root mac dinh: `data/extracted/asr/asr_output_corrected_split`
- Moi file batch: `Lxx/asr_segments.jsonl`
- Elasticsearch index mac dinh: `keyframe_annotations`
- Database metadata: lay tu `DATABASE_URL` trong `.env`
- Mapping: moi ASR segment duoc gan vao cac keyframe cung video nam trong khoang thoi gian segment, co padding mac dinh `1.0s`

Moi document Elasticsearch co cac field quan trong:

- `source_type`: `asr`
- `video_id`, `video_code`
- `keyframe_id`, `frame_id`, `frame_idx`, `frame_seconds`, `timestamp_ms`
- `segment_id`, `start_seconds`, `end_seconds`
- `text_value`, `asr_text`, `normalized_asr_text`, `raw_asr_text`

## Chay Elasticsearch

Tu root repo:

```powershell
docker compose up -d elasticsearch
```

`docker-compose.yml` dang dat Elasticsearch heap `-Xms768m -Xmx768m`; muc 384MB bi OOM khi query index metadata + ASR.

Kiem tra ES:

```powershell
Invoke-RestMethod http://localhost:9200
```

## Dry run

Lenh nay chi dem va map thu ASR sang keyframe, khong ghi vao Elasticsearch:

```powershell
python apps/backend/scripts/import_asr_to_elasticsearch.py `
  --elasticsearch-url http://localhost:9200 `
  --batches L21 `
  --limit-videos 2 `
  --dry-run
```

Neu chay script ben trong backend container:

```powershell
docker compose exec backend python scripts/import_asr_to_elasticsearch.py `
  --batches L21 `
  --limit-videos 2 `
  --dry-run
```

## Import that

Import full ASR L21-L30 vao index `keyframe_annotations`:

Khuyen nghi chay trong backend container de dung dung Python dependency `elasticsearch==8.15.1` voi Elasticsearch 8.15 local:

```powershell
docker compose exec backend python scripts/import_asr_to_elasticsearch.py `
  --replace-asr `
  --refresh
```

Neu chay truc tiep tu host, hay dam bao Python dang dung Elasticsearch client 8.x. Neu gap loi `compatible-with=9`, cai lai client:

```powershell
python -m pip install "elasticsearch==8.15.1"
```

```powershell
python apps/backend/scripts/import_asr_to_elasticsearch.py `
  --elasticsearch-url http://localhost:9200 `
  --replace-asr `
  --refresh
```

Gioi han batch neu can:

```powershell
python apps/backend/scripts/import_asr_to_elasticsearch.py `
  --elasticsearch-url http://localhost:9200 `
  --batches L21,L22 `
  --replace-asr `
  --refresh
```

## Verify sau import

Dem so ASR docs:

```powershell
$body = '{"query":{"term":{"source_type":"asr"}}}'
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:9200/keyframe_annotations/_count `
  -ContentType application/json `
  -Body $body
```

Search thu truc tiep tren ASR:

```powershell
$body = @{
  size = 5
  query = @{
    multi_match = @{
      query = "asparagus oil pan"
      fields = @("asr_text^2.5", "normalized_asr_text^2.5", "text_value")
      fuzziness = "AUTO"
    }
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:9200/keyframe_annotations/_search `
  -ContentType application/json `
  -Body $body
```

## Noi vao backend

`docker-compose.yml` da dat:

```yaml
TEXT_SEARCH_BACKEND: elasticsearch
CLIP_EMBEDDING_BASE_URL: http://host.docker.internal:8001/v1
```

Restart backend/web sau khi import:

```powershell
docker compose up -d --build backend web
```

Backend se dung CLIP lam visual baseline va Elasticsearch ASR lam text signal. UI mac dinh search voi `top_k = 50` va bat metadata/text search.
