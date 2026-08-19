from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .artifact_io import ArtifactStore
from .cloud_sinks.asr_elasticsearch import ElasticsearchAsrSink
from .cloud_sinks.config import SinkConfig
from .cloud_sinks.elasticsearch import ElasticsearchAnnotationSink
from .cloud_sinks.milvus import MilvusEmbeddingSink
from .cloud_sinks.milvus_text import MilvusTextEmbeddingSink, TextMilvusPayload
from .cloud_sinks.postgres import PostgresAnnotationSink
from .extractors.runtime import ModelRuntime
from .gcs_source import FrameItem
from .manifest import frame_item_from_record
from .text_embedding import VietnameseTextEmbedder


LOGGER = logging.getLogger(__name__)
FRAME_IDX_RE = re.compile(r"f(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ArtifactImportOptions:
    """Options for importing feature artifacts into the configured databases."""

    artifact_uris: list[str]
    batch_size: int = 256
    write_pg: bool = True
    write_milvus: bool = True
    write_elasticsearch: bool = True
    dry_run: bool = False
    gcs_credentials_file: str = ""
    gcs_timeout_seconds: float = 60.0
    asr_elasticsearch_index: str = ""
    text_embedding_collection: str = ""
    text_embedding_model_name: str = "dangvantuan/vietnamese-embedding"
    write_text_embeddings: bool = True


def import_feature_artifacts(config: SinkConfig, options: ArtifactImportOptions) -> dict[str, Any]:
    """Load feature JSONL artifacts from GCS/local storage and upsert DB sinks."""
    store = ArtifactStore(credentials_file=options.gcs_credentials_file, timeout_seconds=options.gcs_timeout_seconds)
    files = _expand_artifact_files(store, options.artifact_uris)
    records, asr_records, text_records = _load_records(store, files)
    summary = {
        "artifact_files": files,
        "records": len(records),
        "asr_records": len(asr_records),
        "text_records": len(text_records),
        "pg": 0,
        "milvus": 0,
        "elasticsearch": 0,
        "asr_elasticsearch": 0,
        "text_milvus": 0,
        "dry_run": options.dry_run,
    }
    if options.dry_run:
        return summary

    pg_sink = PostgresAnnotationSink(config) if options.write_pg else None
    es_sink = ElasticsearchAnnotationSink(config) if options.write_elasticsearch else None
    asr_index = options.asr_elasticsearch_index or f"{config.elasticsearch_index}_asr_segments"
    asr_sink = ElasticsearchAsrSink(config, asr_index) if options.write_elasticsearch and asr_records else None
    milvus_sink = MilvusEmbeddingSink(config) if options.write_milvus else None
    text_sink = None
    text_payloads: list[TextMilvusPayload] = []
    if options.write_text_embeddings and text_records:
        text_sink = MilvusTextEmbeddingSink(config, options.text_embedding_collection or "text_embeddings_vietnamese")
        text_embedder = VietnameseTextEmbedder(
            ModelRuntime(
                {
                    "cache_dir": str(Path.home() / ".cache" / "multimodal-retrieval" / "processor-cache"),
                    "device": "auto",
                }
            ),
            {"model_name": options.text_embedding_model_name, "batch_size": 32, "normalize": True},
        )
        texts = [item["text"] for item in text_records]
        vectors = text_embedder.encode(texts)
        for item, vector in zip(text_records, vectors):
            text_payloads.append(
                TextMilvusPayload(
                    id=item["id"],
                    vector=vector.astype(float).tolist(),
                    keyframe_id=item.get("keyframe_id"),
                    video_id=item.get("video_id"),
                    text=item["text"],
                    doc_type=item["doc_type"],
                    model_version=options.text_embedding_model_name,
                    metadata=item.get("metadata") or {},
                )
            )
        summary["text_milvus"] = text_sink.upsert(text_payloads)

    for chunk in _chunks(records, max(1, options.batch_size)):
        frames = [item["frame"] for item in chunk]
        annotations = [item["annotation"] for item in chunk]
        if pg_sink is not None:
            summary["pg"] += pg_sink.upsert(frames, annotations)
        if es_sink is not None:
            summary["elasticsearch"] += es_sink.upsert(frames, annotations)
        if milvus_sink is not None:
            emb_frames: list[FrameItem] = []
            emb_vectors: list[list[float]] = []
            for item in chunk:
                if item["embedding"] is None:
                    continue
                emb_frames.append(item["frame"])
                emb_vectors.append(item["embedding"])
            if emb_frames:
                summary["milvus"] += milvus_sink.upsert(emb_frames, np.asarray(emb_vectors, dtype="float32"))
    if asr_sink is not None:
        for chunk in _chunks(asr_records, max(1, options.batch_size)):
            summary["asr_elasticsearch"] += asr_sink.upsert(chunk)
    return summary


def _expand_artifact_files(store: ArtifactStore, uris: list[str]) -> list[str]:
    files: list[str] = []
    for uri in uris:
        files.extend(_jsonl_files_from_uri(store, uri))
    return sorted(set(files))


def _jsonl_files_from_uri(store: ArtifactStore, uri: str) -> list[str]:
    if str(uri).lower().endswith(".json") and store.exists(uri):
        pointer = store.read_json(uri)
        resolved = _jsonl_uris_from_pointer(pointer)
        if resolved:
            files: list[str] = []
            for resolved_uri in resolved:
                files.extend(store.list_jsonl(resolved_uri))
            return files
    return store.list_jsonl(uri)


def _jsonl_uris_from_pointer(pointer: Any) -> list[str]:
    if not isinstance(pointer, dict):
        return []
    values: list[Any] = [
        pointer.get("annotations_uri"),
        pointer.get("recommended_import_annotations_uri"),
        pointer.get("gcs_annotations"),
    ]
    outputs = pointer.get("outputs") if isinstance(pointer.get("outputs"), dict) else {}
    values.append(outputs.get("gcs_annotations"))

    for key in ("artifacts", "import_artifacts"):
        artifacts = pointer.get(key)
        if not isinstance(artifacts, list):
            continue
        for item in artifacts:
            if isinstance(item, dict):
                values.extend([item.get("uri"), item.get("annotations_uri"), item.get("gcs_annotations")])
            else:
                values.append(item)

    uris: list[str] = []
    for value in values:
        uri = str(value or "").strip()
        if uri and uri.endswith(".jsonl"):
            uris.append(uri)
    return uris


def _load_records(store: ArtifactStore, files: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    merged: dict[str, dict[str, Any]] = {}
    asr_records: list[dict[str, Any]] = []
    text_records: list[dict[str, Any]] = []
    for uri in files:
        for raw in store.read_jsonl(uri):
            if raw.get("schema_version") == "aic.asr_artifact.v1":
                asr_records.extend(_asr_segment_records(raw))
                continue
            frame_raw = raw.get("frame") if isinstance(raw.get("frame"), dict) else raw
            frame = _try_frame_item(frame_raw)
            keyframe_id = frame.keyframe_id if frame is not None else _keyframe_id_from_record(frame_raw)
            if not keyframe_id:
                LOGGER.warning("Skipping annotation without keyframe id or frame location from %s: %s", uri, raw)
                continue
            current = merged.setdefault(
                keyframe_id,
                {
                    "frame": frame,
                    "annotation": {
                        "caption": "",
                        "texts": [],
                        "objects": [],
                        "object_counts": {},
                        "detections": [],
                    },
                    "embedding": None,
                },
            )
            if frame is not None:
                current["frame"] = frame
            current["annotation"] = _merge_annotation(current["annotation"], raw.get("annotation") or raw)
            if isinstance(raw.get("embedding"), list):
                current["embedding"] = raw["embedding"]
    for keyframe_id in sorted(merged):
        item = merged[keyframe_id]
        if item["frame"] is None:
            continue
        text_record = _text_record_from_frame(item["frame"], item["annotation"])
        if text_record["text"]:
            text_records.append(text_record)
    text_records.extend(_text_records_from_asr(asr_records))
    records = [merged[key] for key in sorted(merged) if merged[key]["frame"] is not None]
    return records, asr_records, text_records


def _try_frame_item(record: dict[str, Any]) -> FrameItem | None:
    try:
        return frame_item_from_record(record)
    except Exception:
        return None


def _keyframe_id_from_record(record: dict[str, Any]) -> str:
    raw_id = str(record.get("keyframe_id") or record.get("frame_id") or "").strip()
    if raw_id:
        return raw_id
    video_id = str(record.get("video_id") or "").strip()
    image_name = str(record.get("image_name") or record.get("image_path") or record.get("image_gcs_uri") or "")
    match = FRAME_IDX_RE.search(image_name)
    if not video_id or not match:
        return ""
    return f"{video_id}_F{int(match.group(1)):06d}"


def _asr_segment_records(raw: dict[str, Any]) -> list[dict[str, Any]]:
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
    video_id = str(raw.get("video_id") or source.get("video_id") or "")
    rows: list[dict[str, Any]] = []
    for segment in raw.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "segment_id": str(segment.get("segment_id") or f"{video_id}_ASR_{len(rows):06d}"),
                "video_id": video_id,
                "run_id": str(raw.get("run_id") or ""),
                "stage": str(raw.get("stage") or "asr"),
                "start_seconds": float(segment.get("start_seconds") or 0.0),
                "end_seconds": float(segment.get("end_seconds") or 0.0),
                "text": text,
                "language": str(segment.get("language") or model.get("language") or ""),
                "confidence": segment.get("confidence"),
                "model_name": str(model.get("name") or ""),
                "source_gcs_uri": str(source.get("gcs_uri") or ""),
            }
        )
    return rows


def _text_record_from_frame(frame: FrameItem, annotation: dict[str, Any]) -> dict[str, Any]:
    texts = annotation.get("texts") or []
    objects = annotation.get("objects") or []
    caption = str(annotation.get("caption") or "").strip()
    combined = _join_parts([caption, " ".join(str(text) for text in texts), " ".join(str(obj) for obj in objects)])
    return {
        "id": frame.keyframe_id,
        "keyframe_id": frame.keyframe_id,
        "video_id": frame.video_id,
        "doc_type": "frame_text",
        "text": combined,
        "metadata": {
            "caption": caption,
            "texts": texts,
            "objects": objects,
            "object_counts": annotation.get("object_counts") or {},
        },
    }


def _text_records_from_asr(asr_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in asr_records:
        text = str(record.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "id": str(record.get("segment_id") or f"{record.get('video_id') or 'video'}_ASR_{len(rows):06d}"),
                "keyframe_id": None,
                "video_id": str(record.get("video_id") or ""),
                "doc_type": "asr_segment",
                "text": text,
                "metadata": {
                    "start_seconds": record.get("start_seconds"),
                    "end_seconds": record.get("end_seconds"),
                    "language": record.get("language"),
                    "confidence": record.get("confidence"),
                },
            }
        )
    return rows


def _merge_annotation(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    payload = right.get("payload") if isinstance(right.get("payload"), dict) else {}
    kind = str(right.get("kind") or right.get("extractor_name") or "").lower()

    caption_value = right.get("caption") or payload.get("caption")
    if not caption_value and "caption" in kind:
        caption_value = right.get("text_value")
    caption = str(caption_value or left.get("caption") or "")

    text_value = right.get("text_value")
    ocr_values = right.get("texts") or right.get("ocr_texts") or payload.get("texts") or payload.get("ocr_texts")
    if not ocr_values and "ocr" in kind:
        ocr_values = text_value
    texts = _merge_list(left.get("texts"), ocr_values)

    object_values = (
        right.get("objects")
        or right.get("detected_objects")
        or payload.get("objects")
        or payload.get("detected_objects")
    )
    if not object_values and "object" in kind:
        object_values = text_value
    objects = _merge_list(left.get("objects"), object_values)

    object_counts = dict(left.get("object_counts") or {})
    for key, value in {**(right.get("object_counts") or {}), **(payload.get("object_counts") or {})}.items():
        object_counts[key] = max(int(object_counts.get(key, 0)), int(value))
    detections = _merge_list(left.get("detections"), right.get("detections") or payload.get("detections"))
    return {
        "caption": caption,
        "texts": texts,
        "objects": objects,
        "object_counts": object_counts,
        "detections": detections,
    }


def _merge_list(left: Any, right: Any) -> list[Any]:
    values: list[Any] = []
    for source in (left, right):
        if isinstance(source, list):
            values.extend(source)
        elif source:
            values.append(source)
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else str(value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _join_parts(parts: list[str]) -> str:
    return " ".join(part.strip() for part in parts if str(part or "").strip())


def _chunks(items: list[dict[str, Any]], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]
