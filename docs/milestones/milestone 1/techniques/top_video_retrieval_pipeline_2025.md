# Pipeline và kỹ thuật Big Data của các đội top đầu trong Video Retrieval

## Phạm vi

Tài liệu này tổng hợp pipeline, model và kỹ thuật xử lý dữ liệu lớn từ các hệ thống nổi bật tại:

- Video Browser Showdown 2025
- Lifelog Search Challenge 2025
- HCMC AI Challenge 2025

Mục tiêu là rút ra các thành phần có thể tích hợp vào một hệ thống multimodal video retrieval thực tế.

---

## 1. Kết luận chính

Các hệ thống đứng đầu không dựa vào một model duy nhất. Pipeline phổ biến thường có dạng:

```text
Video segmentation
→ multimodal feature extraction
→ multiple independent indexes
→ query expansion/decomposition
→ hybrid retrieval
→ rank fusion
→ reranking
→ temporal search
→ relevance feedback
→ interactive browsing UI
```

Những thành phần có tác động lớn nhất:

1. Nhiều index độc lập cho visual, caption, OCR, ASR và metadata.
2. LLM để phân tích, mở rộng và chia nhỏ query.
3. Temporal retrieval cho truy vấn nhiều sự kiện.
4. Region-level hoặc object-level retrieval.
5. Hybrid search kết hợp vector search và lexical search.
6. RRF hoặc weighted rank fusion.
7. Cross-encoder hoặc multimodal LLM reranking.
8. Giao diện hỗ trợ duyệt keyframe, frame lân cận và video proxy.
9. Relevance feedback từ người dùng.
10. Pipeline preprocessing có khả năng batch, resume và versioning.

---

# 2. Video Browser Showdown 2025

VBS 2025 sử dụng gần 4.000 giờ video, gồm nhiều tập dữ liệu khác nhau.

Xếp hạng tổng thể:

1. NII-UIT
2. PraK Tool V3
3. diveXplore
4. Exquisitor

## 2.1. NII-UIT

### Thành phần chính

NII-UIT sử dụng hệ thống multimodal retrieval kết hợp:

- CLIP-based semantic retrieval.
- LLM query expansion.
- Query paraphrasing.
- Query decomposition.
- Stable Diffusion để tạo ảnh từ text query.
- Image-to-image retrieval.
- Dynamic temporal search.
- Nhiều index và nhiều kiểu biểu diễn.

### Pipeline khái quát

```text
Original query
├── CLIP text query
├── LLM paraphrases
├── object/action/attribute subqueries
├── OCR/ASR keyword query
├── generated-image query
└── temporal subevents E1 → E2 → E3
```

Kết quả từ các nhánh được hợp nhất bằng:

- Reciprocal Rank Fusion.
- Weighted rank fusion.
- Score normalization.
- Temporal consistency filtering.

### Bài học áp dụng

Không nên chỉ chạy:

```text
query → text encoder → top-k frames
```

Nên tạo nhiều biến thể của query và retrieve trên nhiều modality.

---

## 2.2. PraK Tool V3

### Thành phần chính

PraK Tool V3 tập trung vào localized retrieval:

- CLIP text-to-image retrieval.
- Image-to-image retrieval.
- Grid-level search.
- Region-level search.
- Local texture retrieval.
- Localized text search.
- Bayesian relevance feedback.

### Region-level index

Nên tạo embedding cho:

```text
frame embedding
object crop embeddings
2×2 grid embeddings
3×3 grid embeddings
OCR bounding-box embeddings
```

### Ví dụ

Query:

> Người mặc áo đỏ đứng bên trái một chiếc xe màu trắng.

Có thể tách thành:

```text
global query: person near white car
left-region query: person wearing red
object filter: person + car
spatial constraint: person.x < car.x
```

Cách này thường tốt hơn việc yêu cầu một global image embedding hiểu đồng thời vật thể, màu sắc và quan hệ không gian.

---

## 2.3. diveXplore

### Offline pipeline

```text
Raw videos
   ↓
TransNetV2 shot-boundary detection
   ↓
Keyframe extraction
   ├── OpenCLIP embeddings
   ├── EasyOCR + CRAFT
   ├── Whisper ASR
   └── video summaries
```

### Model và hạ tầng

- TransNetV2: shot-boundary detection.
- OpenCLIP ViT-H/14: visual và text embedding.
- Whisper: automatic speech recognition.
- EasyOCR + CRAFT: OCR detection và recognition.
- MongoDB: metadata, OCR và speech.
- FAISS: approximate nearest-neighbor retrieval.
- Node.js: query server.
- WebSocket: giao tiếp realtime.

### Kỹ thuật xử lý dữ liệu lớn

diveXplore tách hệ thống thành:

```text
frontend
middleware/query layer
retrieval services
metadata storage
vector indexes
object storage
```

Các request được thực hiện bất đồng bộ và có thể phân phối tới nhiều retrieval server.

### Proxy video

Thay vì stream video gốc hoặc gửi từng ảnh khi người dùng rê timeline, hệ thống tạo:

- video proxy độ phân giải thấp;
- GOP ngắn;
- file nhỏ;
- phù hợp với scrubbing.

Ví dụ:

```text
original video
medium playback video
160×90 low-GOP scrubbing proxy
```

### Chức năng giao diện

- Free-text search.
- OCR search.
- ASR search.
- Metadata filtering.
- Temporal query.
- Keyframe scrubbing.
- Similarity view.
- Shot view.
- Neighboring-frame inspection.
- Video summary mosaic.

### Bài học áp dụng

Trong interactive video retrieval, UX và thời gian xác nhận timestamp quan trọng gần ngang retrieval accuracy.

---

## 2.4. Exquisitor

### Thành phần chính

Exquisitor kết hợp:

- Conversational semantic search.
- CLIP retrieval.
- Positive feedback.
- Negative feedback.
- Iterative reranking.
- Multiple concurrent search sessions.

### Rocchio relevance feedback

Một phiên bản đơn giản:

```text
q_new =
α × q_old
+ β × mean(relevant_embeddings)
- γ × mean(non_relevant_embeddings)
```

Trong đó:

- `q_old`: query embedding hiện tại.
- `relevant_embeddings`: các kết quả được đánh dấu đúng.
- `non_relevant_embeddings`: các kết quả bị đánh dấu sai.
- `q_new`: query embedding cho vòng tiếp theo.

Không nên bỏ hoàn toàn query gốc sau khi nhận feedback.

---

# 3. Lifelog Search Challenge 2025

Các hệ thống đứng đầu:

1. MEMORIA
2. SnapSeek 3.0
3. MemoriEase 3.0

## 3.1. MEMORIA

### Thành phần chính

MEMORIA kết hợp:

- Embedding models.
- Large language models.
- Query optimization.
- Query reformulation.
- Conversational interaction.
- Interactive moment retrieval.

### Bài học áp dụng

LLM không nhất thiết là retrieval engine chính. Vai trò hiệu quả hơn là:

- chuẩn hóa câu hỏi;
- tạo paraphrase;
- trích xuất entity;
- chia query thành nhiều subquery;
- xác định temporal relation;
- sinh metadata filter;
- cải thiện query sau relevance feedback.

---

## 3.2. SnapSeek 3.0

### Thành phần chính

SnapSeek 3.0 tập trung vào:

- Egocentric activity recognition.
- Activity of Daily Living recognition.
- Scene graph generation.
- Entity extraction.
- Relation extraction.
- Multiple embedding models.
- Vector database.
- Interactive retrieval.

### Scene graph

Ví dụ:

```text
(person_1, holding, cup)
(person_1, sitting_on, chair)
(person_1, opposite, person_2)
(person_2, wearing, blue_shirt)
```

Nếu đã có object annotations, có thể nâng cấp thành scene graph:

```json
{
  "entities": [
    "person_1",
    "cup",
    "person_2"
  ],
  "relations": [
    ["person_1", "holding", "cup"],
    ["person_1", "left_of", "person_2"]
  ]
}
```

### Bài học áp dụng

Scene graph đặc biệt hữu ích với các query chứa:

- quan hệ không gian;
- hành động giữa nhiều đối tượng;
- màu sắc và thuộc tính;
- hướng nhìn;
- quan hệ người-vật;
- quan hệ vật-vật.

---

## 3.3. MemoriEase 3.0

### Indexing pipeline

MemoriEase sử dụng:

- CLIP embedding.
- BLIP-2 embedding.
- Elasticsearch.
- Metadata filtering.
- Weighted score fusion.
- Relevance feedback.
- BERT cross-encoder reranking.
- LLM-based RAG question answering.

### Dữ liệu được index

- Image embedding.
- Description.
- Caption.
- Time.
- City/location.
- Weekday/weekend.
- Timeslot.
- Additional metadata.

### Retrieval

Hai loại embedding được retrieve riêng và fusion:

```text
CLIP ranking
+
BLIP-2 ranking
+
metadata filtering
→ weighted fusion
```

### RAG question answering

```text
Question
   ↓
Initial retrieval: 30–50 candidates
   ↓
BERT cross-encoder reranking
   ↓
Top candidate captions + metadata + context
   ↓
LLM answer generation
```

### Hierarchy của dữ liệu

Nên giữ nhiều cấp:

```text
video
  └── event/clip
       └── shot
            └── frame
```

Không nên chỉ lưu event-level document, vì event grouping sai có thể làm mất các frame liên quan.

---

# 4. HCMC AI Challenge 2025

Các đội đứng đầu bảng A:

1. OpenCubee-1
2. OpenCubee-2
3. LunchRetrieval

Thông tin công khai hiện chưa mô tả đầy đủ toàn bộ model, checkpoint và trọng số fusion của các đội đứng đầu. Tuy nhiên, các hệ thống được công bố quanh cuộc thi cho thấy các hướng kỹ thuật rõ ràng.

## 4.1. Conversational video retrieval

Một hướng nổi bật là intelligent search agent:

```text
User request
   ↓
Intent detection
   ↓
Query decomposition
   ↓
Tool/index selection
   ↓
Parallel retrieval
   ↓
Fusion
   ↓
Result explanation
   ↓
User feedback
   ↓
Query refinement
```

Agent nên chọn giữa:

- visual search;
- OCR search;
- ASR search;
- caption search;
- object search;
- temporal search;
- metadata filter;
- image similarity search.

---

## 4.2. MERVIN

### Data preparation

```text
Video
├── TransNetV2
│   └── three keyframes per shot
│       at positions 0.15, 0.50 and 0.85
│
├── YouTube Transcript API
│   └── fallback: Whisper
│
├── transcript grouping
│   └── five neighboring segments
│
└── Gemini 1.5 Flash
    ├── transcript cleanup
    ├── Vietnamese normalization
    └── event/video summarization
```

### Embedding và storage

- PE-Core-bigG-14-448: visual embedding.
- `dangvantuan/vietnamese-embedding`: Vietnamese text embedding.
- Milvus collections:
  - keyframes;
  - transcript segments;
  - video summaries.

### Temporal retrieval

```text
retrieve E1
retrieve E2

same_video(E1, E2)
AND timestamp(E1) < timestamp(E2)
AND timestamp(E2) - timestamp(E1) < max_window
```

### Bài học áp dụng

Nên tách temporal query thành nhiều event và retrieve độc lập trước khi ghép theo timestamp.

---

## 4.3. U-CESE

### DAKE keyframe extraction

DAKE là phương pháp training-free:

1. Sample hoặc encode frame thành JPEG.
2. Đo JPEG file size.
3. Tính mức thay đổi dung lượng giữa các frame.
4. Phát hiện vùng có motion, texture hoặc lighting change.
5. Chọn các frame có aggregated steepness cao.

Ưu điểm:

- không cần neural shot detector;
- chi phí thấp;
- dễ kiểm soát số keyframe;
- phù hợp preprocessing trên dữ liệu lớn.

### ReCap temporal-aware captioning

Đầu vào captioning cho một shot gồm:

- target keyframe;
- neighboring keyframes;
- subtitle đồng thời;
- memory string từ các shot trước.

Memory giúp caption duy trì nhất quán về:

- nhân vật;
- địa điểm;
- ngữ cảnh;
- diễn tiến sự kiện.

### Retrieval stack

- MobileCLIP visual encoder.
- MobileCLIP text encoder.
- Milvus.
- Elasticsearch.
- Whisper.
- Unified Clipping Algorithm.
- Two-pointer temporal grouping.

### Multi-event clip retrieval

Ví dụ query:

> Người đàn ông bước vào cửa hàng, sau đó nói chuyện với nhân viên, cuối cùng rời đi bằng xe máy.

Pipeline:

```text
subquery 1: man enters store
subquery 2: man talks to employee
subquery 3: man leaves by motorcycle
```

Mỗi subquery tạo một tập timestamp. Sau đó dùng temporal grouping để tìm clip ngắn nhất bao phủ nhiều subquery nhất.

---

# 5. Danh sách model và kỹ thuật nên cân nhắc

| Thành phần | Model hoặc kỹ thuật | Vai trò |
|---|---|---|
| Shot detection | TransNetV2 | Phát hiện shot chính xác |
| Keyframe nhẹ | DAKE | Training-free, tiết kiệm GPU |
| Visual embedding mạnh | PE-Core-bigG-14-448 | Keyframe retrieval |
| Visual embedding phổ biến | OpenCLIP ViT-H/14 | Text-image retrieval |
| Visual embedding nhẹ | MobileCLIP | Giảm latency |
| Multimodal ensemble | CLIP + BLIP-2 | Tăng coverage |
| ASR | Whisper hoặc faster-whisper | Speech retrieval |
| OCR detection | CRAFT | Phát hiện vùng chữ |
| OCR recognition | EasyOCR, PARSeq | Nhận dạng text |
| Captioning nhanh | BLIP-2 | Caption keyframe |
| Captioning giàu ngữ cảnh | Gemini hoặc Qwen-VL | Shot-aware captioning |
| Vietnamese embedding | dangvantuan/vietnamese-embedding | Transcript và summary |
| Query expansion | LLM | Paraphrase và decomposition |
| Text-to-image query | Stable Diffusion | Hỗ trợ image-based search |
| Text reranker | BERT cross-encoder | Rerank caption/ASR |
| Visual reranker | Multimodal LLM | Xác minh top candidates |
| Vector database | Milvus hoặc FAISS | ANN retrieval |
| Text search | Elasticsearch hoặc OpenSearch | BM25 và metadata filters |
| Fusion | RRF hoặc weighted RRF | Merge nhiều rankings |
| Feedback | Rocchio hoặc Bayesian feedback | Interactive refinement |
| Temporal grouping | Two-pointer/sliding window | Multi-event retrieval |
| Scene graph | Entity-relation graph | Spatial/action retrieval |

---

# 6. Pipeline đề xuất cho hệ thống

## 6.1. Offline pipeline

```text
Raw videos
   │
   ├── TransNetV2 or DAKE
   │       └── shots + keyframes
   │
   ├── Whisper
   │       └── ASR segments
   │
   ├── OCR
   │       └── text + bounding boxes
   │
   ├── Existing object annotations
   │       └── object, confidence, bbox, attributes
   │
   ├── VLM captioning
   │       ├── frame caption
   │       └── shot caption with temporal context
   │
   ├── Embedding workers
   │       ├── global frame embedding
   │       ├── object/region embeddings
   │       ├── caption embedding
   │       ├── ASR embedding
   │       └── summary embedding
   │
   └── Storage
           ├── Object storage
           ├── Elasticsearch
           ├── Milvus
           └── PostgreSQL or MongoDB
```

## 6.2. Online pipeline

```text
User query
   │
   ├── LLM query parser
   │       ├── entities
   │       ├── actions
   │       ├── attributes and colors
   │       ├── spatial relations
   │       ├── OCR/ASR phrases
   │       ├── metadata filters
   │       └── temporal events
   │
   ├── Parallel retrieval
   │       ├── visual ANN
   │       ├── region/object ANN
   │       ├── caption ANN
   │       ├── BM25 OCR
   │       ├── BM25 ASR
   │       └── annotation filters
   │
   ├── RRF or weighted fusion
   │
   ├── group by shot/video
   │
   ├── temporal clip construction
   │
   ├── cross-encoder or MLLM reranking
   │
   └── result UI
           ├── keyframe grid
           ├── neighboring shots
           ├── proxy scrubbing
           ├── relevance feedback
           └── precise timestamp submission
```

---

# 7. Elasticsearch document design

Mỗi document nên đại diện cho một shot hoặc retrieval segment.

```json
{
  "video_id": "V001",
  "shot_id": "V001_S0042",
  "start_time": 123.4,
  "end_time": 130.8,

  "keyframe_ids": [
    "F3702",
    "F3813",
    "F3924"
  ],

  "caption": "A man wearing a red shirt stands beside a white car.",
  "asr": "Transcript text at this segment.",
  "ocr_text": [
    "STORE",
    "SALE"
  ],

  "objects": [
    {
      "object_id": "person_1",
      "label": "person",
      "confidence": 0.94,
      "bbox": {
        "x1": 0.12,
        "y1": 0.20,
        "x2": 0.38,
        "y2": 0.91
      },
      "attributes": [
        "red shirt"
      ]
    }
  ],

  "relations": [
    {
      "subject": "person_1",
      "predicate": "left_of",
      "object": "car_1"
    }
  ],

  "source": "HTV",
  "program": "Program name",
  "published_at": "2025-01-01T00:00:00Z",

  "embedding_ids": {
    "visual": "milvus_visual_001",
    "caption": "milvus_caption_001",
    "asr": "milvus_asr_001"
  },

  "pipeline_version": "v3"
}
```

## Elasticsearch mapping gợi ý

- `video_id`, `shot_id`, `source`: `keyword`.
- `caption`, `asr`, `ocr_text`: `text`.
- `objects`: `nested`.
- `objects.label`: `keyword`.
- `objects.attributes`: `text` và `keyword`.
- `relations`: `nested`.
- `start_time`, `end_time`: `float`.
- `published_at`: `date`.
- Bounding box coordinates: `float`.

---

# 8. Thiết kế vector collections

Không nên nhét mọi embedding vào một collection.

```text
visual_global
visual_regions
visual_objects
captions
asr_segments
ocr_regions
video_summaries
```

Lợi ích:

- dimension riêng;
- metric riêng;
- index configuration riêng;
- thay model độc lập;
- scale độc lập;
- benchmark từng modality;
- không phải re-index toàn bộ hệ thống.

Metadata tối thiểu của mỗi vector:

```json
{
  "vector_id": "visual_001",
  "video_id": "V001",
  "shot_id": "V001_S0042",
  "frame_id": "F3813",
  "timestamp": 127.1,
  "model_name": "PE-Core-bigG-14-448",
  "model_version": "2025-01",
  "pipeline_version": "v3"
}
```

---

# 9. Rank fusion

## 9.1. Reciprocal Rank Fusion

```text
RRF_score(d) = Σ 1 / (k + rank_i(d))
```

Trong đó:

- `rank_i(d)` là thứ hạng document trong index thứ `i`.
- `k` thường dùng để giảm ảnh hưởng quá lớn của top rank.
- RRF không cần các score từ nhiều model phải có cùng phân phối.

## 9.2. Weighted RRF

```text
score(d) =
w_visual × RRF_visual(d)
+ w_caption × RRF_caption(d)
+ w_asr × RRF_asr(d)
+ w_ocr × RRF_ocr(d)
+ w_object × RRF_object(d)
```

Trọng số có thể thay đổi theo query intent.

Ví dụ:

```text
query chứa "biển báo", "logo", "dòng chữ"
→ tăng OCR weight

query chứa lời nói hoặc tên người
→ tăng ASR weight

query chứa màu sắc, đồ vật, cảnh vật
→ tăng visual/object weight

query chứa "trước đó", "sau đó", "cuối cùng"
→ kích hoạt temporal search
```

---

# 10. Temporal retrieval

## 10.1. Hai sự kiện

```text
E1 occurs before E2
```

Điều kiện:

```text
same video
timestamp(E1) < timestamp(E2)
timestamp(E2) - timestamp(E1) ≤ temporal_window
```

## 10.2. Nhiều sự kiện

```text
E1 → E2 → E3
```

Quy trình:

1. Retrieve top timestamps cho từng event.
2. Group theo video.
3. Sort timestamp.
4. Dùng sliding window hoặc two-pointer.
5. Tìm khoảng nhỏ nhất bao phủ nhiều event nhất.
6. Tính temporal score.
7. Fusion với semantic retrieval score.

## 10.3. Temporal score gợi ý

```text
temporal_score =
event_coverage
× order_consistency
× exp(-clip_duration / τ)
```

Trong đó:

- `event_coverage`: số subquery xuất hiện trong clip.
- `order_consistency`: mức đúng thứ tự.
- `clip_duration`: độ dài clip.
- `τ`: hệ số điều chỉnh.

---

# 11. Kỹ thuật Big Data

## 11.1. Batch GPU inference

Tách queue theo workload:

```text
queue:shot_detection
queue:keyframes
queue:ocr
queue:asr
queue:caption
queue:visual_embedding
queue:text_embedding
```

Mỗi worker:

- đọc batch;
- chạy model;
- ghi artifact;
- cập nhật trạng thái;
- retry khi lỗi;
- hỗ trợ resume.

## 11.2. Idempotent jobs

Tạo artifact key:

```text
artifact_key =
hash(
  video_id
  + shot_id
  + model_name
  + model_version
  + preprocessing_version
)
```

Nếu artifact đã tồn tại, worker không cần xử lý lại.

## 11.3. Model versioning

Ví dụ:

```json
{
  "shot_detector": "TransNetV2",
  "shot_detector_version": "v2",
  "visual_model": "PE-Core-bigG-14-448",
  "visual_model_version": "2025-01",
  "caption_model": "Qwen-VL",
  "caption_model_version": "v1",
  "pipeline_version": "v3"
}
```

## 11.4. Blue-green indexing

```text
visual_v1
visual_v2
```

Quy trình:

1. Index model mới vào `visual_v2`.
2. Chạy benchmark.
3. So sánh recall, latency và storage.
4. Chuyển alias sang `visual_v2`.
5. Giữ `visual_v1` để rollback.

## 11.5. Retrieval theo tầng

```text
Stage 1: ANN/BM25 retrieve 1,000–5,000 candidates
Stage 2: fusion and deduplication to 200–500
Stage 3: cross-encoder rerank to 50–100
Stage 4: MLLM verification for top 20–50
```

Không nên dùng multimodal LLM để đánh giá toàn bộ archive.

## 11.6. Hierarchical indexing

```text
video-level summary
clip/event-level caption
shot-level representation
frame-level representation
object/region-level representation
```

Pipeline:

```text
video retrieval
→ clip retrieval
→ shot retrieval
→ precise frame/timestamp retrieval
```

Nên vẫn giữ global frame-level fallback.

## 11.7. Media storage

Nên lưu:

```text
original video
playback video
low-resolution low-GOP proxy
keyframes
object crops
OCR crops
waveform or audio chunks
```

Object storage phù hợp:

- S3.
- MinIO.
- Google Cloud Storage.
- Azure Blob Storage.

---

# 12. Thứ tự triển khai đề xuất

## Giai đoạn 1: Nền tảng

- TransNetV2.
- Ba keyframe mỗi shot.
- faster-whisper.
- OCR.
- PE-Core hoặc OpenCLIP.
- Elasticsearch.
- Milvus.
- RRF.
- Neighbor-frame viewer.

## Giai đoạn 2: Tăng độ chính xác

- LLM query decomposition.
- Multi-embedding ensemble.
- Region/object embeddings.
- Temporal search.
- Cross-encoder reranking.
- Relevance feedback.
- Video-summary retrieval.

## Giai đoạn 3: Hướng hệ thống thi đấu

- Intelligent search agent.
- Temporal-aware captioning.
- Scene graph retrieval.
- Text-to-image query.
- Multimodal LLM reranking.
- Multi-event clip construction.
- RAG QA trên candidates.
- Automatic query reformulation.

---

# 13. Cấu hình cân bằng đề xuất

```text
Keyframe extraction
    TransNetV2, three frames per shot

Primary visual embedding
    PE-Core-bigG-14-448

Secondary visual embedding
    OpenCLIP ViT-H/14 or BLIP-2

ASR
    faster-whisper

OCR
    CRAFT + Vietnamese-capable recognizer

Captioning
    Qwen-VL or Gemini with shot context

Vietnamese text embedding
    dangvantuan/vietnamese-embedding

Text and metadata database
    Elasticsearch

Vector database
    Milvus

Fusion
    Weighted Reciprocal Rank Fusion

Reranking
    Cross-encoder followed by optional MLLM

Temporal search
    Subquery retrieval + two-pointer clip grouping

Frontend
    Keyframe grid + neighboring shots + proxy scrubbing
```

---

# 14. Ưu tiên cao nhất cho hệ thống hiện tại

Nếu hệ thống đã có object annotations, nên ưu tiên:

1. Chuẩn hóa annotation thành shot-level document.
2. Tạo global frame embedding.
3. Tạo object crop và region embedding.
4. Index caption, OCR và ASR riêng.
5. Dùng Elasticsearch cho lexical search và filters.
6. Dùng Milvus cho vector retrieval.
7. Hợp nhất kết quả bằng weighted RRF.
8. Thêm LLM query decomposition.
9. Thêm temporal clip construction.
10. Chỉ dùng MLLM ở bước rerank cuối.

Hai cải tiến có khả năng mang lại khác biệt lớn nhất:

- chia query thành nhiều event hoặc modality rồi fusion;
- xây clip từ nhiều tập timestamp thay vì chỉ trả về từng frame độc lập.

---

# 15. Nguồn tham khảo

1. Video Browser Showdown official website  
   https://videobrowsershowdown.org/

2. VBS 2025 systems and evaluation report  
   https://arxiv.org/abs/2509.12000

3. CVPR 2025 Workshop: AI-based Video Content Understanding for Automatic and Interactive Multimedia Retrieval  
   https://openaccess.thecvf.com/content/CVPR2025W/IViSE/papers/Schoeffmann_AI-based_Video_Content_Understanding_for_Automatic_and_Interactive_Multimedia_Retrieval_CVPRW_2025_paper.pdf

4. Lifelog Search Challenge official website  
   https://lifelogsearch.org/

5. LSC 2025 program  
   https://lifelogsearch.org/lsc/program/

6. MemoriEase publications  
   https://doras.dcu.ie/31574/  
   https://doras.dcu.ie/31771/1/3729459.3748689.pdf

7. HCMC AI Challenge 2025 results  
   https://www.uit.edu.vn/bai-viet/uit-lan-thu-4-dang-quang-vo-dich-hoi-thi-thu-thach-tri-tue-nhan-tao-ai-challenge-2025

8. U-CESE video retrieval system  
   https://arxiv.org/html/2605.23274v1

9. MERVIN retrieval system  
   https://www.alphaxiv.org/abs/2605.16120v1

---

## Ghi chú

Một số đội thi không công bố đầy đủ:

- checkpoint cụ thể;
- trọng số fusion;
- index parameters;
- latency;
- hardware;
- ablation study;
- source code.

Do đó, tài liệu phân biệt giữa:

- kỹ thuật được mô tả công khai trong paper hoặc proceedings;
- kiến trúc khuyến nghị được suy ra từ các pattern chung của hệ thống top đầu.
