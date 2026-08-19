# AIC AI 2026 — Cloud Storage Structure

```text
AIC_AI_2026/
└── features/
|    └── {dataset}/
|        └── {frame_profile}/
|            └── {batch}/
|                └── {video_id}/
|                    └── {extractor_type}/
|                        └── {extractor_version}/
|                            └── {model}/
|                                └── runs/
|                                    └── {run_id}/
|
└── logs/
└── processed/
└── raw/
```

Example:
```text
AIC_AI_2026/
└── features/
    └── ai_challenge_2025/
        └── autoshot_v1/
            └── L30/
                └── L30_V001/
                    ├── captioning/
                    │   └── fe-captioning-v2.2//
                    │       └── qwen2.5-vl-3b-instruct/
                    │           └── runs/
                    │               └── run_20260805_143000/
                    |
                    │
                    ├── ocr/
                    │   └── fe-ocr-v1.0/
                    │       └── paddleocr-v6/
                    │           └── runs/
                    │               └── run_20260805_144500/
                    |
                    |
                    ├── object_detection/
                    │   └── fe-object-detection-v1.0/
                    │       └── yolo26x/
                    │           └── runs/
                    │               └── run_20260805_150000/
                    │
                    └── vector_embedding/
                        └── fe-vector-embedding-v1.0/
                            └── siglip2-base-patch16-256/
                                └── runs/
                                    └── run_20260805_153000/

```