# Hướng dẫn gắn model và sửa pipeline

Tài liệu này mô tả cách bỏ model thật vào hệ thống, đổi model, thêm adapter mới và sửa pipeline preprocessing/retrieval trong tương lai.

## 1. Nguyên tắc chung

Hệ thống không gọi checkpoint trực tiếp từ business logic. Mọi model đi qua 3 lớp:

1. **Model files** trong `models/`.
2. **Model registry** trong `configs/model_registry.yaml`.
3. **Adapter code** trong `apps/backend/app/adapters/model_runtime/`.

Nhờ vậy bạn có thể đổi từ mock model sang model thật mà không phải sửa endpoint FastAPI hay frontend.

## 2. Bỏ model vào thư mục nào

Không commit model weights lên Git. Đặt checkpoint local trong `models/`:

```text
models/
├── pe-core-bigg/
│   ├── config.json
│   ├── model.safetensors
│   └── tokenizer.json
├── beit3/
├── paddle-vietocr/
├── whisperx/
└── qwen2.5-vl/
```

Nếu model tải từ Hugging Face cache, bạn có thể đặt `checkpoint_uri` là đường dẫn cache hoặc tên repo nếu máy có network khi chạy.

## 3. Bật/tắt model trong registry

File chính: `configs/model_registry.yaml`.

Ví dụ bật PE-core-BigG và tắt mock embedder:

```yaml
embedders:
  clip_mock:
    task: multimodal_embedding
    provider: mock
    enabled: false

  pe_core_bigg:
    task: multimodal_embedding
    provider: huggingface
    checkpoint_uri: models/pe-core-bigg
    device: cuda:0
    dtype: fp16
    batch_size: 16
    enabled: true
```

Các field quan trọng:

| Field | Ý nghĩa |
| --- | --- |
| `task` | Loại model: `multimodal_embedding`, `ocr`, `asr`, `visual_qa`, `query_expansion`. |
| `provider` | `mock`, `huggingface`, `local`, `openai_compatible`. |
| `checkpoint_uri` | Đường dẫn model local hoặc model id. |
| `device` | `cpu`, `cuda:0`, `cuda:1`. |
| `dtype` | `fp32`, `fp16`, `bf16`, tùy model/GPU. |
| `batch_size` | Batch inference cho worker. |
| `enabled` | Chỉ model `enabled: true` được registry chọn. |

Sau khi đổi registry:

```powershell
docker compose restart backend
```

Nếu đổi embedding/OCR/ASR/caption, cần chạy lại ingest hoặc index để kết quả dùng model mới.

## 4. Thêm adapter model mới

Các interface nằm ở:

```text
apps/backend/app/adapters/model_runtime/base.py
```

Hiện có 3 interface chính:

- `TextImageEmbedder`: dùng cho text/image embedding.
- `QueryExpander`: dùng cho LLM sinh biến thể query.
- `VisualQaModel`: dùng cho QA trên frame/context.

Ví dụ thêm embedder mới:

```python
from app.adapters.model_runtime.base import TextImageEmbedder


class MyEmbedder(TextImageEmbedder):
    def __init__(self, checkpoint_uri: str, device: str) -> None:
        self.checkpoint_uri = checkpoint_uri
        self.device = device
        # load model tại đây

    def embed_text(self, text: str) -> list[float]:
        # tokenize -> model -> normalize vector
        return vector

    def embed_image_uri(self, image_uri: str) -> list[float]:
        # load image -> preprocess -> model -> normalize vector
        return vector
```

Sau đó đăng ký trong `apps/backend/app/modules/models/service.py`. Bản scaffold hiện dùng mock để chạy ngay; khi bạn thêm adapter thật, thay phần khởi tạo:

```python
self.embedder = MyEmbedder(checkpoint_uri=config["checkpoint_uri"], device=config["device"])
```

Nên giữ mock adapter làm fallback để dev UI/API không bị kẹt khi GPU/model lỗi.

## 5. Thay LLM query expansion

Registry có nhóm:

```yaml
llm:
  local_qwen:
    task: query_expansion
    provider: openai_compatible
    base_url: http://localhost:8001/v1
    model: qwen2.5-7b-instruct
    enabled: true
```

Bạn có thể chạy vLLM/Ollama/LM Studio/OpenAI-compatible server riêng, rồi trỏ `base_url` vào đó.

Prompt nên trả JSON:

```json
{
  "variants": [
    "query reformulation 1",
    "query reformulation 2"
  ],
  "temporal_events": [
    "event 1",
    "event 2"
  ],
  "entities": ["object", "place", "color"]
}
```

Khi thêm LLM thật, vẫn giữ nút `MV` trên frontend để bật/tắt nhanh vì query expansion không phải lúc nào cũng cải thiện kết quả.

## 5.1 Embedding service chuẩn với demo (khuyến nghị)

Với bộ demo hiện tại, vector keyframe được tạo bằng:
- `model_name`: `ViT-B-32`
- `pretrained`: `laion2b_s34b_b79k`
- `dim`: `512`
- `l2_normalized`: `true`

Để truy vấn cosine đúng embedding space này, chạy embedding service riêng tại `http://127.0.0.1:8001/v1`:

```bash
cd apps/backend
../../venv/bin/pip install -r requirements-embedding-service.txt
../../venv/bin/python scripts/serve_openclip_embeddings.py
```

Gợi ý vận hành:
- Đặt `EMBED_HOST`, `EMBED_PORT` trong `.env` root nếu cần đổi host/port.
- Đặt `openclip_model`, `openclip_pretrained`, `openclip_device`, `openclip_max_batch` trong `configs/model_registry.yaml`.
- Script tự load `.env` (fallback `.env.example`) để tìm `MODEL_REGISTRY_PATH`.
- Có thể override bằng `--env-file /path/to/file.env`.

Kiểm tra endpoint trước khi chạy retrieval:

```bash
../../venv/bin/python scripts/check_embedding_endpoint.py \
  --base-url http://127.0.0.1:8001/v1 \
  --model ViT-B-32-laion2b_s34b_b79k \
  --expected-dim 512
```

Nếu pass, giữ `configs/model_registry.yaml` như sau:

```yaml
embedders:
  openai_embedding:
    provider: openai_compatible
    base_url: http://localhost:8001/v1
    model: ViT-B-32-laion2b_s34b_b79k
    openclip_model: ViT-B-32
    openclip_pretrained: laion2b_s34b_b79k
    openclip_device: cpu
    openclip_max_batch: 32
    dim: 512
    l2_normalize: true
    enabled: true
```

## 6. Sửa pipeline preprocessing

Pipeline stage list nằm ở:

```text
apps/backend/app/modules/ingest/pipeline.py
```

Thứ tự mặc định:

```python
DEFAULT_STAGE_ORDER = [
    "dataset_scan",
    "shot_detection",
    "keyframe_extraction",
    "frame_dedup",
    "ocr",
    "asr",
    "object_detection",
    "captioning",
    "scene_classification",
    "embedding",
    "event_segmentation",
    "milvus_index",
    "text_index",
]
```

Muốn thêm stage mới, ví dụ reranker/offline object attributes:

1. Tạo class implement `PipelineStage`.
2. Thêm tên stage vào `DEFAULT_STAGE_ORDER`.
3. Lưu output vào PostgreSQL/MinIO/Milvus/Elasticsearch tùy loại dữ liệu.
4. Ghi `model_version` hoặc `pipeline_version`.
5. Chạy lại ingest/index.

Ví dụ:

```python
class AttributeDetectionStage:
    name = "attribute_detection"

    def run(self, context: PipelineContext) -> PipelineContext:
        # read keyframes, run model, write frame_annotations(kind="TAG")
        context.stats[self.name] = detected_count
        return context
```

## 7. Sửa retrieval pipeline

Retrieval core nằm ở:

```text
apps/backend/app/modules/retrieval/service.py
apps/backend/app/modules/temporal/ats.py
```

Các điểm thường cần chỉnh:

| Mục | File | Khi nào sửa |
| --- | --- | --- |
| Query parser | `retrieval/service.py` | Thêm rule tách event tiếng Việt, detect màu/số/named entity. |
| Score fusion | `retrieval/service.py` | Đổi trọng số semantic/OCR/ASR/object/caption. |
| ATS | `temporal/ats.py` | Đổi constraint thứ tự, khoảng cách, partial match. |
| QA answer | `model_runtime/*`, `retrieval/service.py` | Thêm VLM thật hoặc answer normalizer. |
| Submission format | `submissions/service.py` | Nếu BTC đổi format CSV. |

Các trọng số runtime nằm trong:

```text
configs/retrieval_profiles.yaml
```

Ví dụ tăng OCR cho query có nhiều chữ:

```yaml
competition_default:
  semantic_weight: 0.50
  metadata_weight: 0.35
  metadata:
    ocr_boost: 4.0
```

## 8. Re-index sau khi đổi model

Khi đổi các model sau, phải re-index:

- Embedding model.
- OCR/ASR.
- Object detector.
- Caption/scene model.
- Event segmentation.

Chạy mock ingest:

```powershell
.\scripts\ingest_dataset.ps1 -Mode mock
```

Khi đã có pipeline thật, tạo job:

```powershell
.\scripts\ingest_dataset.ps1 -ManifestPath configs\dataset_manifest.example.yaml -Mode full
```

Trong bản scaffold, job `full` được queue placeholder. Khi bạn implement worker thật, giữ API này để frontend/scripts không đổi.

## 9. Kiểm tra sau khi thay model

Checklist:

1. `GET /api/models` thấy model mới `enabled: true`.
2. Ingest job chạy xong, `index_builds` có version mới.
3. Search thử KIS/QA/TRAKE trên mock query.
4. Kiểm tra top result có `score_breakdown`.
5. Export submission ZIP và validate.
6. Nếu có ground truth local, chạy:

```powershell
python scripts\benchmark_retrieval.py --submission-dir data\submissions\<id>\submission --ground-truth data\mock\ground_truth.json
```

## 10. Gợi ý nâng cấp model theo thứ tự

1. **Embedding thật trước**: PE/OpenCLIP/SigLIP/BEiT-3 vào Milvus, vì ảnh hưởng lớn nhất đến KIS.
2. **OCR tiếng Việt**: PaddleOCR + VietOCR/correction giúp query có chữ trên màn hình.
3. **ASR**: WhisperX giúp QA và video có lời thoại.
4. **VLM QA**: Qwen2.5-VL/LLaVA để trả answer ngắn.
5. **Reranker**: cross-encoder/VLM rerank top 100-300 để đẩy đáp án lên R@1/R@5.
6. **ATS tuning**: chỉnh `delta_t_max_ms`, `min_match_ratio` theo TRAKE thực tế.
