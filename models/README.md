# Local Models

Đặt checkpoint model thật trong thư mục này. Không commit model weights lên Git.

Xem hướng dẫn trong `docs/model_pipeline_guide.md`

Ví dụ:

```text
models/
├── pe-core-bigg/
├── beit3/
├── paddle-vietocr/
├── whisperx/
└── qwen2.5-vl/
```

Sau khi copy model, cập nhật `configs/model_registry.yaml` và chạy lại ingest/index.
