from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
PROCESSORS_ROOT = REPO_ROOT / "scripts" / "processors"

for path in (BACKEND_ROOT, PROCESSORS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.core.config import get_settings  # noqa: E402
from src.artifact_io import gcs_client  # noqa: E402


DEFAULT_BATCHES = "L21,L22,L23,L24,L25,L26,L27,L28,L29,L30"
DEFAULT_COLLECTION = "keyframe_embeddings_clip_vith14_quickgelu_dfn5b_v2"
DEFAULT_DATASET_ID = "ai_challenge_2025"
DEFAULT_EXTRACTOR_VERSION = "clip-vit-h14-quickgelu-dfn5b-v2"
DEFAULT_FRAME_PROFILE = "autoshot_v1"
DEFAULT_GCS_FEATURE_PREFIX = "features/extractors"
DEFAULT_MODEL_KEY = "clip_vith14_quickgelu_dfn5b_v2"
DEFAULT_MODEL_NAME = "ViT-H-14-quickgelu-dfn5b"
DEFAULT_EMBEDDING_FAMILY = "clip"

FRAME_IDX_RE = re.compile(r"(?:^|[_-])f(?P<frame_idx>\d{3,})(?:\D|$)", re.IGNORECASE)


@dataclass(frozen=True)
class VideoArtifact:
    batch_id: str
    video_id: str
    embedding_key: str
    map_key: str
    bucket_name: str = ""

    @property
    def embedding_uri(self) -> str:
        return f"gs://{self.bucket_name}/{self.embedding_key}"

    @property
    def map_uri(self) -> str:
        return f"gs://{self.bucket_name}/{self.map_key}"


@dataclass(frozen=True)
class CanonicalFrame:
    keyframe_id: str
    frame_idx: int
    timestamp_ms: int


def split_batches(raw: str) -> list[str]:
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import GCS .npy keyframe embeddings plus map-keyframes CSV into Zilliz/Milvus."
    )
    parser.add_argument("--batches", default=DEFAULT_BATCHES)
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    parser.add_argument("--frame-profile", default=DEFAULT_FRAME_PROFILE)
    parser.add_argument("--extractor-version", default=DEFAULT_EXTRACTOR_VERSION)
    parser.add_argument("--gcs-feature-prefix", default=DEFAULT_GCS_FEATURE_PREFIX)
    parser.add_argument("--gcs-bucket", default="")
    parser.add_argument("--gcs-credentials-file", default="")
    parser.add_argument("--gcs-timeout", type=float, default=60.0)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--model-key", default=DEFAULT_MODEL_KEY)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--embedding-family", default=DEFAULT_EMBEDDING_FAMILY)
    parser.add_argument("--embedding-space", default="")
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--max-videos", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop the target collection before import. Use only when rebuilding this collection.",
    )
    parser.add_argument(
        "--no-flush",
        action="store_true",
        help="Skip explicit Milvus flush after import.",
    )
    parser.add_argument(
        "--bridge-db-keyframes",
        action="store_true",
        help="Attach canonical DB keyframe IDs by ordinal-ratio mapping before upsert.",
    )
    return parser.parse_args()


def feature_root(args: argparse.Namespace, batch_id: str) -> str:
    return (
        f"{args.gcs_feature_prefix.strip('/')}/"
        f"dataset={args.dataset_id}/"
        f"batch={batch_id}/"
        f"frame_profile={args.frame_profile}/"
        f"extractor=vector-embedding/"
        f"extractor_version={args.extractor_version}"
    )


def list_video_artifacts(client: Any, bucket_name: str, args: argparse.Namespace, batch_id: str) -> list[VideoArtifact]:
    root = feature_root(args, batch_id)
    embeddings: dict[str, str] = {}
    maps: dict[str, str] = {}
    for blob in client.list_blobs(bucket_name, prefix=f"{root}/", timeout=args.gcs_timeout):
        name = blob.name
        path = PurePosixPath(name)
        video_id = path.stem
        if name.endswith(".npy") and "/embeddings/" in name:
            embeddings[video_id] = name
        elif name.endswith(".csv") and "/map-keyframes/" in name:
            maps[video_id] = name

    missing_maps = sorted(set(embeddings) - set(maps))
    missing_embeddings = sorted(set(maps) - set(embeddings))
    if missing_maps or missing_embeddings:
        raise RuntimeError(
            json.dumps(
                {
                    "batch_id": batch_id,
                    "missing_maps": missing_maps,
                    "missing_embeddings": missing_embeddings,
                },
                ensure_ascii=False,
            )
        )

    return [
        VideoArtifact(
            batch_id=batch_id,
            video_id=video_id,
            embedding_key=embeddings[video_id],
            map_key=maps[video_id],
            bucket_name=bucket_name,
        )
        for video_id in sorted(embeddings)
    ]


def read_csv_rows(client: Any, bucket_name: str, key: str, timeout: float) -> list[dict[str, str]]:
    text = client.bucket(bucket_name).blob(key).download_as_text(encoding="utf-8-sig", timeout=timeout)
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def read_npy(client: Any, bucket_name: str, key: str, timeout: float) -> np.ndarray:
    payload = client.bucket(bucket_name).blob(key).download_as_bytes(timeout=timeout)
    vectors = np.load(io.BytesIO(payload), allow_pickle=False)
    if vectors.ndim != 2:
        raise ValueError(f"Expected a 2D numpy array for gs://{bucket_name}/{key}, got shape={vectors.shape}")
    return np.asarray(vectors, dtype=np.float32)


def int_or_default(raw: Any, default: int) -> int:
    if raw in (None, ""):
        return default
    return int(float(raw))


def int_or_none(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return None


def frame_idx_from_token(raw: Any) -> int | None:
    value = str(raw or "").strip()
    if not value:
        return None
    match = FRAME_IDX_RE.search(value)
    if not match:
        return None
    return int(match.group("frame_idx"))


def parsed_frame_idx(row: dict[str, str]) -> int | None:
    for key in ("canonical_frame_idx", "frame_idx", "source_frame_idx"):
        parsed = int_or_none(row.get(key))
        if parsed is not None:
            return parsed
    for key in ("keyframe_id", "frame_filename", "image_rel_path"):
        parsed = frame_idx_from_token(row.get(key))
        if parsed is not None:
            return parsed
    return None


def canonical_keyframe_id(video_id: str, frame_idx: int) -> str:
    return f"{video_id}_F{frame_idx:06d}"


def load_canonical_frames(video_ids: list[str]) -> dict[str, list[CanonicalFrame]]:
    from app.db.models import Frame
    from app.db.session import SessionLocal

    if not video_ids:
        return {}
    frames_by_video: dict[str, list[CanonicalFrame]] = {}
    with SessionLocal() as db:
        rows = (
            db.query(Frame.video_id, Frame.keyframe_id, Frame.frame_idx, Frame.timestamp_ms)
            .filter(Frame.video_id.in_(sorted(set(video_ids))))
            .order_by(Frame.video_id.asc(), Frame.frame_idx.asc(), Frame.keyframe_id.asc())
            .all()
        )
    for video_id, keyframe_id, frame_idx, timestamp_ms in rows:
        frames_by_video.setdefault(str(video_id), []).append(
            CanonicalFrame(
                keyframe_id=str(keyframe_id),
                frame_idx=int(frame_idx),
                timestamp_ms=int(timestamp_ms or 0),
            )
        )
    return frames_by_video


def canonical_frame_for_vector(
    canonical_frames: list[CanonicalFrame],
    vector_count: int,
    vector_index: int,
) -> CanonicalFrame | None:
    if not canonical_frames:
        return None
    if len(canonical_frames) == 1 or vector_count <= 1:
        return canonical_frames[0]
    ordinal = round(vector_index * (len(canonical_frames) - 1) / max(1, vector_count - 1))
    ordinal = min(max(ordinal, 0), len(canonical_frames) - 1)
    return canonical_frames[ordinal]


def row_payload(
    artifact: VideoArtifact,
    row: dict[str, str],
    vector: np.ndarray,
    index: int,
    dataset_id: str,
    frame_profile: str,
    model_key: str,
    model_name: str,
    model_version: str,
    embedding_family: str,
    embedding_space: str,
    canonical_frame: CanonicalFrame | None = None,
    vector_count: int | None = None,
) -> dict[str, Any]:
    map_n = int_or_default(row.get("n"), index + 1)
    keyframe_number = int_or_default(row.get("keyframe_number"), map_n)
    frame_filename = str(row.get("frame_filename") or "").strip()
    image_rel_path = str(row.get("image_rel_path") or "").strip()
    if not image_rel_path and frame_filename:
        image_rel_path = f"{artifact.video_id}/{frame_filename}"
    original_keyframe_id = str(row.get("keyframe_id") or "").strip() or f"{artifact.video_id}_{keyframe_number:03d}"
    source_frame_idx = parsed_frame_idx(row)
    if canonical_frame is not None:
        keyframe_id = canonical_frame.keyframe_id
        frame_idx = canonical_frame.frame_idx
        frame_idx_source = "canonical_db_ordinal_ratio"
        canonical_mapping = "ordinal_ratio_to_db_frames"
    elif source_frame_idx is not None:
        frame_idx = source_frame_idx
        keyframe_id = canonical_keyframe_id(artifact.video_id, frame_idx)
        frame_idx_source = "source_keyframe_filename"
        canonical_mapping = "parsed_from_source_keyframe_id"
    else:
        keyframe_id = original_keyframe_id
        frame_idx = keyframe_number
        frame_idx_source = "keyframe_number"
        canonical_mapping = ""
    payload = {
        "id": original_keyframe_id,
        "vector": vector.astype(float).tolist(),
        "keyframe_id": keyframe_id,
        "original_keyframe_id": original_keyframe_id,
        "video_id": artifact.video_id,
        "batch_id": artifact.batch_id,
        "dataset_id": dataset_id,
        "frame_profile": frame_profile,
        "map_n": map_n,
        "embedding_index_0": index,
        "keyframe_number": keyframe_number,
        "video_vector_count": int(vector_count or 0),
        "frame_idx": frame_idx,
        "frame_idx_source": frame_idx_source,
        "frame_filename": frame_filename,
        "image_rel_path": image_rel_path,
        "model_key": model_key,
        "model_name": model_name,
        "model_version": model_version,
        "embedding_family": embedding_family,
        "embedding_space": embedding_space or model_key,
        "embedding_dim": int(vector.shape[0]),
        "extractor": "vector-embedding",
        "extractor_version": model_version,
        "source_embedding_uri": artifact.embedding_uri,
        "source_map_uri": artifact.map_uri,
    }
    if canonical_frame is not None or source_frame_idx is not None:
        payload.update(
            {
                "canonical_keyframe_id": keyframe_id,
                "canonical_frame_idx": frame_idx,
                "canonical_mapping": canonical_mapping,
            }
        )
    if canonical_frame is not None:
        payload["canonical_timestamp_ms"] = canonical_frame.timestamp_ms
    return payload


def chunks(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def ensure_collection(client: Any, collection_name: str, dim: int, drop_existing: bool) -> None:
    from pymilvus import DataType, MilvusClient

    if client.has_collection(collection_name=collection_name):
        if drop_existing:
            client.drop_collection(collection_name=collection_name)
        else:
            existing_dim = collection_dim(client.describe_collection(collection_name=collection_name))
            if existing_dim is not None and existing_dim != dim:
                raise ValueError(
                    f"Collection {collection_name!r} has dim={existing_dim}, but artifact dim={dim}. "
                    "Use another collection or rebuild it."
                )
            return

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
    index_params = MilvusClient.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)


def collection_dim(description: dict[str, Any]) -> int | None:
    for field in description.get("fields", []):
        if field.get("name") != "vector":
            continue
        params = field.get("params") or {}
        dim = params.get("dim") or field.get("dim")
        return int(dim) if dim is not None else None
    return None


def milvus_client(settings: Any):
    from pymilvus import MilvusClient

    kwargs: dict[str, str] = {"uri": settings.milvus_uri}
    if settings.milvus_token:
        kwargs["token"] = settings.milvus_token
    return MilvusClient(**kwargs)


def import_artifacts(
    storage_client: Any,
    milvus: Any | None,
    bucket_name: str,
    artifacts: list[VideoArtifact],
    args: argparse.Namespace,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "collection": args.collection,
        "dataset_id": args.dataset_id,
        "extractor_version": args.extractor_version,
        "frame_profile": args.frame_profile,
        "model_key": args.model_key,
        "model_name": args.model_name,
        "embedding_family": args.embedding_family,
        "embedding_space": args.embedding_space or args.model_key,
        "dry_run": bool(args.dry_run),
        "videos": 0,
        "vectors": 0,
        "dimension": None,
        "batches": {},
        "mismatches": [],
        "bridge": {
            "enabled": bool(args.bridge_db_keyframes),
            "videos_with_db_frames": 0,
            "videos_missing_db_frames": [],
            "vectors_mapped": 0,
        },
    }
    collection_ready = False
    bridge_frames = load_canonical_frames([artifact.video_id for artifact in artifacts]) if args.bridge_db_keyframes else {}
    if args.bridge_db_keyframes:
        summary["bridge"]["videos_with_db_frames"] = len(bridge_frames)

    for artifact in artifacts:
        map_rows = read_csv_rows(storage_client, bucket_name, artifact.map_key, args.gcs_timeout)
        vectors = read_npy(storage_client, bucket_name, artifact.embedding_key, args.gcs_timeout)
        if len(map_rows) != int(vectors.shape[0]):
            summary["mismatches"].append(
                {
                    "video_id": artifact.video_id,
                    "map_rows": len(map_rows),
                    "vectors": int(vectors.shape[0]),
                    "embedding_uri": artifact.embedding_uri,
                    "map_uri": artifact.map_uri,
                }
            )
            continue

        dim = int(vectors.shape[1])
        if summary["dimension"] is None:
            summary["dimension"] = dim
        elif summary["dimension"] != dim:
            raise ValueError(f"Mixed vector dimensions: {summary['dimension']} and {dim}")

        if milvus is not None and not collection_ready:
            ensure_collection(milvus, args.collection, dim, args.drop_existing)
            collection_ready = True

        batch_summary = summary["batches"].setdefault(artifact.batch_id, {"videos": 0, "vectors": 0})
        summary["videos"] += 1
        summary["vectors"] += int(vectors.shape[0])
        batch_summary["videos"] += 1
        batch_summary["vectors"] += int(vectors.shape[0])

        if milvus is None:
            continue

        canonical_frames = bridge_frames.get(artifact.video_id, [])
        if args.bridge_db_keyframes and not canonical_frames:
            summary["bridge"]["videos_missing_db_frames"].append(artifact.video_id)
        elif canonical_frames:
            summary["bridge"]["vectors_mapped"] += int(vectors.shape[0])

        payloads = [
            row_payload(
                artifact=artifact,
                row=row,
                vector=vector,
                index=index,
                dataset_id=args.dataset_id,
                frame_profile=args.frame_profile,
                model_key=args.model_key,
                model_name=args.model_name,
                model_version=args.extractor_version,
                embedding_family=args.embedding_family,
                embedding_space=args.embedding_space or args.model_key,
                canonical_frame=canonical_frame_for_vector(canonical_frames, int(vectors.shape[0]), index),
                vector_count=int(vectors.shape[0]),
            )
            for index, (row, vector) in enumerate(zip(map_rows, vectors))
        ]
        for batch in chunks(payloads, max(1, args.batch_size)):
            milvus.upsert(collection_name=args.collection, data=batch)

    if milvus is not None and not args.no_flush and summary["vectors"] > 0:
        milvus.flush(collection_name=args.collection)

    return summary


def main() -> None:
    args = parse_args()
    settings = get_settings()
    bucket_name = args.gcs_bucket or settings.gcs_bucket
    credentials_file = args.gcs_credentials_file or settings.gcs_credentials_file
    if not bucket_name:
        raise RuntimeError("Set --gcs-bucket or GCS_BUCKET.")

    storage_client = gcs_client(credentials_file)
    artifacts: list[VideoArtifact] = []
    for batch_id in split_batches(args.batches):
        artifacts.extend(list_video_artifacts(storage_client, bucket_name, args, batch_id))
    if args.max_videos > 0:
        artifacts = artifacts[: args.max_videos]

    milvus = None if args.dry_run else milvus_client(settings)
    summary = import_artifacts(storage_client, milvus, bucket_name, artifacts, args)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
