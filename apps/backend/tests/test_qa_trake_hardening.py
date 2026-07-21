from __future__ import annotations

from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.model_runtime.base import VisualQaModel
from app.adapters.text_search.base import TextHit
from app.db.models import Base, Dataset, Frame, FrameAnnotation, RetrievalResult, Shot, Video
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.schemas import SearchOptions, SearchRequest
from app.modules.retrieval.service import RetrievalService
from tests.fakes import DeterministicEmbedder, ExpandingQueryExpander, HintVisualQaModel


class LongAnswerQaModel(VisualQaModel):
    def answer(self, question: str, evidence_text: str, answer_hint: str | None = None) -> str:
        _ = (question, evidence_text, answer_hint)
        return "  day la  mot  cau tra loi\nrat dai " + ("x" * 140)


class TextClientForQa:
    def search(self, index: str, query: str, top_k: int, boosts: dict[str, float] | None = None) -> list[TextHit]:
        _ = (index, query, top_k, boosts)
        return [TextHit(id="L30_V001_F000010", score=10.0, metadata={"keyframe_id": "L30_V001_F000010", "video_id": "L30_V001"})]

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        _ = (index, documents)
        return len(documents)


class TextClientForTrake:
    def search(self, index: str, query: str, top_k: int, boosts: dict[str, float] | None = None) -> list[TextHit]:
        _ = (index, top_k, boosts)
        mapping: dict[str, list[TextHit]] = {
            "event one": [
                TextHit(id="L30_V001_F000010", score=5.0, metadata={"keyframe_id": "L30_V001_F000010", "video_id": "L30_V001"}),
                TextHit(id="L30_V002_F000015", score=5.0, metadata={"keyframe_id": "L30_V002_F000015", "video_id": "L30_V002"}),
            ],
            "event two": [
                TextHit(id="L30_V001_F000020", score=5.0, metadata={"keyframe_id": "L30_V001_F000020", "video_id": "L30_V001"}),
                TextHit(id="L30_V002_F000025", score=5.0, metadata={"keyframe_id": "L30_V002_F000025", "video_id": "L30_V002"}),
            ],
        }
        return mapping.get(query, [])

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        _ = (index, documents)
        return len(documents)


def _build_db(tmp_path: Path) -> Session:
    db_path = tmp_path / "qa_trake.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _seed_video_with_frames(db: Session, dataset: Dataset, video_id: str, frame_indices: list[int]) -> None:
    video = Video(
        video_id=video_id,
        dataset_id=dataset.dataset_id,
        video_code=video_id,
        video_name=f"{video_id}.mp4",
        uri=f"file:///demo/{video_id}.mp4",
        fps=25.0,
        duration_seconds=40.0,
        duration_ms=40000,
    )
    db.add(video)
    db.flush()
    db.add(
        Shot(
            shot_id=f"{video_id}_S0000",
            video_id=video_id,
            shot_index=0,
            start_frame=0,
            end_frame=2000,
            start_seconds=0.0,
            end_seconds=80.0,
            boundary_threshold=0.3,
        )
    )
    db.flush()

    for frame_idx in frame_indices:
        frame = Frame(
            keyframe_id=f"{video_id}_F{frame_idx:06d}",
            video_id=video_id,
            shot_id=f"{video_id}_S0000",
            frame_idx=frame_idx,
            frame_seconds=frame_idx / 25.0,
            timestamp_ms=int(frame_idx / 25.0 * 1000),
            image_uri=f"/tmp/{video_id}_F{frame_idx:06d}.jpg",
            quality_score=1.0,
        )
        db.add(frame)
        db.flush()
        db.add(
            FrameAnnotation(
                frame_id=frame.keyframe_id,
                kind="MULTIMODAL",
                text_value=f"{video_id} frame {frame_idx}",
                caption=f"caption {frame_idx}",
                detected_objects=["person"],
                json_value={},
            )
        )
    db.commit()


def test_m5_qa_answer_is_postprocessed_to_max_100_chars(tmp_path: Path) -> None:
    db = _build_db(tmp_path)
    dataset = Dataset(dataset_code="qa-hardening", name="qa-hardening", version="v1", root_uri="file:///demo", status="READY")
    db.add(dataset)
    db.commit()
    _seed_video_with_frames(db, dataset, "L30_V001", [10])

    model_registry = ModelRegistryService(
        embedder=DeterministicEmbedder(dim=64),
        query_expander=ExpandingQueryExpander(),
        visual_qa=LongAnswerQaModel(),
    )
    service = RetrievalService(db=db, model_registry=model_registry, vector_client=None, text_client=TextClientForQa())

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="QA",
            query_name="m5-qa-postprocess",
            query_text="nguoi nay dang lam gi",
            top_k=1,
            options=SearchOptions(use_query_expansion=False),
        )
    )

    assert response.results
    answer = response.results[0].answer or ""
    assert len(answer) <= 100
    assert "\n" not in answer
    assert "  " not in answer

    stored = db.query(RetrievalResult).filter(RetrievalResult.query_run_id == response.query_run_id).one()
    assert len(stored.answer or "") <= 100
    db.close()


def test_m5_trake_returns_stable_ordering_metadata(tmp_path: Path) -> None:
    db = _build_db(tmp_path)
    dataset = Dataset(dataset_code="trake-hardening", name="trake-hardening", version="v1", root_uri="file:///demo", status="READY")
    db.add(dataset)
    db.commit()
    _seed_video_with_frames(db, dataset, "L30_V001", [10, 20])
    _seed_video_with_frames(db, dataset, "L30_V002", [15, 25])

    model_registry = ModelRegistryService(
        embedder=DeterministicEmbedder(dim=64),
        query_expander=ExpandingQueryExpander(),
        visual_qa=HintVisualQaModel(),
    )
    service = RetrievalService(db=db, model_registry=model_registry, vector_client=None, text_client=TextClientForTrake())

    request = SearchRequest(
        dataset_id=dataset.dataset_id,
        query_type="TRAKE",
        query_name="m5-trake-stability",
        query_text="event one then event two",
        top_k=2,
        options=SearchOptions(
            use_query_expansion=False,
            temporal_events=["event one", "event two"],
            delta_t_max_ms=120000,
            min_match=2,
        ),
    )

    first = service.search(request)
    second = service.search(request)

    assert first.results and second.results
    assert [result.video_code for result in first.results] == [result.video_code for result in second.results]

    sequence = first.results[0].sequence_frames
    assert sequence[0]["order_index"] == 1
    assert sequence[1]["order_index"] == 2
    assert sequence[1]["delta_from_previous"] > 0

    ordering = first.results[0].score_breakdown["ordering"]
    assert ordering["is_strictly_increasing"] is True
    assert ordering["delta_frames"][0] > 0
    db.close()
