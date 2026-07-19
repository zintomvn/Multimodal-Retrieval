# Cloud Media Preview Frontend Report

## Summary

The frontend now displays search result frames from cloud media URLs instead of relying only on the backend thumbnail proxy. Each result card also includes a `Video` button that opens an in-app video preview for the result video, starting near the matched frame timestamp when available.

## Changed Files

| File                                            | Purpose                                                                                                       |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `apps/backend/app/modules/media/urls.py`        | Shared GCS URL helpers for `gs://...` URIs and bare object keys.                                              |
| `apps/backend/app/modules/retrieval/schemas.py` | Added cloud media fields to `ResultItem`.                                                                     |
| `apps/backend/app/modules/retrieval/service.py` | Returns frame image metadata and a video preview endpoint in search results.                                  |
| `apps/backend/app/modules/media/router.py`      | Adds cache headers, cloud-aware context frame image metadata, and `/api/media/videos/{video_id}/preview`.     |
| `apps/web/src/api/client.ts`                    | Resolves `http(s)`, backend paths, `gs://...`, and GCS object keys into browser-loadable URLs.                |
| `apps/web/src/types.ts`                         | Adds image/video media fields to frontend result and context types.                                           |
| `apps/web/src/App.tsx`                          | Adds cloud image fallback loading, first-result image warmup, per-card video button, and video preview modal. |
| `apps/web/src/styles.css`                       | Adds card action layout, image fallback placeholder, and video modal styles.                                  |
| `apps/web/index.html`                           | Adds preconnect and DNS-prefetch hints for `storage.googleapis.com`.                                          |

## Image Loading Strategy

Search result cards now try media in this order:

1. `image_url`
2. `thumbnail_url`
3. `image_uri`
4. `image_storage_key`

The frontend converts supported GCS values into browser URLs:

- `https://...` is used directly.
- `/api/...` is prefixed with `VITE_API_BASE_URL`.
- `gs://bucket/path/to/frame.jpg` becomes `https://storage.googleapis.com/bucket/path/to/frame.jpg`.
- Bare object keys can be resolved when `VITE_GCS_BUCKET` or `VITE_GCS_PUBLIC_BASE_URL` is configured.

If the fastest cloud URL fails, the image component automatically falls back to the next candidate, usually the backend thumbnail endpoint. This keeps public buckets fast while preserving compatibility with private buckets and presigned redirects.

## Performance Optimizations

- Direct cloud URLs avoid routing every image through FastAPI when `image_url` is available.
- The first 12 result images are warmed up with `new Image()` after a search.
- Above-the-fold images use `loading="eager"` and `fetchPriority="high"`.
- Remaining result and context images use lazy loading and async decoding.
- `index.html` preconnects to `https://storage.googleapis.com` to reduce connection setup time.
- Backend thumbnail and video responses include `Cache-Control: public, max-age=3600`.

## Video Preview

Each result card now has a `Video` button below the card content. The button opens a modal with an HTML5 video player.

The modal source uses:

```text
/api/media/videos/{video_id}/preview
```

The backend endpoint resolves media in this order:

1. Local video file under `DATA_ROOT`, if available.
2. Direct `http(s)` video URL.
3. GCS signed URL when `STORAGE_PROVIDER=gcs` and credentials are configured.
4. Public GCS URL as a best-effort fallback.

When `timestamp_ms` is present, the frontend appends a media fragment like `#t=12.40` so the video opens near the matched frame. This is intended for visually checking whether the returned frame belongs to the expected video.

## Environment Variables

Recommended frontend variables:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_GCS_BUCKET=your-gcs-bucket
VITE_GCS_PUBLIC_BASE_URL=https://storage.googleapis.com/your-gcs-bucket
```

Recommended backend variables for GCS private buckets:

```bash
STORAGE_PROVIDER=gcs
GCS_BUCKET=your-gcs-bucket
GCS_CREDENTIALS_FILE=/path/to/service-account.json
GCS_PUBLIC_URL=https://storage.googleapis.com/your-gcs-bucket
```

For public GCS buckets, `GCS_PUBLIC_URL` and `VITE_GCS_PUBLIC_BASE_URL` give the fastest path. For private buckets, the backend thumbnail/video fallback remains available through signed URLs.

## Validation

Commands run:

```bash
npm run build
```

Result: frontend TypeScript and Vite production build passed.

```bash
DATABASE_URL=sqlite:///./data/test_media_retrieval.sqlite3 pytest apps/backend/tests/test_retrieval_pipeline.py apps/backend/tests/test_qa_trake_hardening.py
```

Result: 6 backend retrieval tests passed.

```bash
python -m py_compile apps/backend/app/modules/media/router.py apps/backend/app/modules/media/urls.py apps/backend/app/modules/retrieval/service.py apps/backend/app/modules/retrieval/schemas.py
```

Result: backend changed files compiled successfully.

## Notes

- Direct GCS image loading is fastest when the bucket or objects are public, or when `image_url` already stores a signed/public URL.
- If direct cloud images fail because the bucket is private, the frontend falls back to the backend thumbnail endpoint.
- The video preview is intentionally modal-based so the search result list and selected rows remain visible after closing the player.
