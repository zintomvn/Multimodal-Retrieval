from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from sqlalchemy import create_engine, text

from app.adapters.text_search.base import TextHit
from app.core.config import database_connect_args


TOKEN_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)


def _tokens(value: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(value or "") if len(token) > 1]


def _overlap_score(query: str, document: str) -> float:
    q = Counter(_tokens(query))
    d = Counter(_tokens(document))
    if not q or not d:
        return 0.0
    overlap = sum(min(q[token], d[token]) for token in q)
    q_norm = math.sqrt(sum(value * value for value in q.values())) or 1.0
    d_norm = math.sqrt(sum(value * value for value in d.values())) or 1.0
    return overlap / (q_norm * d_norm)


class PostgresTextSearchClient:
    """Text search over Supabase/PostgreSQL frame annotations."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = create_engine(database_url, pool_pre_ping=True, connect_args=database_connect_args(database_url))

    def search(self, index: str, query: str, top_k: int, boosts: dict[str, float] | None = None) -> list[TextHit]:
        _ = index
        query = (query or "").strip()
        if not query:
            return []
        if self.database_url.startswith("sqlite"):
            return self._search_sqlite(query, top_k, boosts or {})
        return self._search_postgres(query, top_k, boosts or {})

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        # The PG ingest sink writes the canonical `frame_annotations` rows.
        # This compatibility hook lets older "text index" code paths run
        # without requiring Elasticsearch.
        _ = (index, documents)
        return len(documents)

    def _search_postgres(self, query: str, top_k: int, boosts: dict[str, float]) -> list[TextHit]:
        ilike_query = f"%{query}%"
        limit = max(1, int(top_k))
        sql = text(
            """
            WITH docs AS (
                SELECT
                    fa.frame_id AS id,
                    fa.frame_id AS keyframe_id,
                    k.video_id AS video_id,
                    k.shot_id AS shot_id,
                    k.frame_seconds AS frame_seconds,
                    COALESCE(fa.caption, '') AS caption,
                    COALESCE(fa.text_value, '') AS text_value,
                    COALESCE(fa.ocr_texts::text, '') AS ocr_texts,
                    COALESCE(fa.detected_objects::text, '') AS detected_objects,
                    COALESCE(fa.object_counts::text, '') AS object_counts,
                    (
                        COALESCE(fa.caption, '') || ' ' ||
                        COALESCE(fa.text_value, '') || ' ' ||
                        COALESCE(fa.ocr_texts::text, '') || ' ' ||
                        COALESCE(fa.detected_objects::text, '') || ' ' ||
                        COALESCE(fa.object_counts::text, '')
                    ) AS search_text,
                    (
                        setweight(to_tsvector('simple', COALESCE(fa.caption, '')), 'A') ||
                        setweight(to_tsvector('simple', COALESCE(fa.ocr_texts::text, '')), 'A') ||
                        setweight(to_tsvector('simple', COALESCE(fa.detected_objects::text, '')), 'B') ||
                        setweight(to_tsvector('simple', COALESCE(fa.text_value, '')), 'C')
                    ) AS document_vector
                FROM frame_annotations fa
                JOIN keyframes k ON k.keyframe_id = fa.frame_id
            ),
            query AS (
                SELECT plainto_tsquery('simple', :query_text) AS tsq
            )
            SELECT
                docs.*,
                ts_rank_cd(docs.document_vector, query.tsq) AS rank_score,
                CASE WHEN docs.search_text ILIKE :ilike_query THEN 0.5 ELSE 0 END AS phrase_score
            FROM docs, query
            WHERE docs.document_vector @@ query.tsq OR docs.search_text ILIKE :ilike_query
            ORDER BY (
                ts_rank_cd(docs.document_vector, query.tsq) +
                CASE WHEN docs.search_text ILIKE :ilike_query THEN 0.5 ELSE 0 END
            ) DESC,
            docs.keyframe_id ASC
            LIMIT :limit
            """
        )
        with self.engine.connect() as connection:
            rows = connection.execute(
                sql,
                {"query_text": query, "ilike_query": ilike_query, "limit": limit},
            ).mappings().all()

        hits: list[TextHit] = []
        for row in rows:
            metadata = dict(row)
            score = float(metadata.pop("rank_score", 0.0) or 0.0) + float(metadata.pop("phrase_score", 0.0) or 0.0)
            score += self._field_boost_score(query, metadata, boosts)
            hits.append(TextHit(id=str(row["id"]), score=score, metadata=metadata))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]

    def _search_sqlite(self, query: str, top_k: int, boosts: dict[str, float]) -> list[TextHit]:
        sql = text(
            """
            SELECT
                fa.frame_id AS id,
                fa.frame_id AS keyframe_id,
                k.video_id AS video_id,
                k.shot_id AS shot_id,
                k.frame_seconds AS frame_seconds,
                COALESCE(fa.caption, '') AS caption,
                COALESCE(fa.text_value, '') AS text_value,
                COALESCE(fa.ocr_texts, '') AS ocr_texts,
                COALESCE(fa.detected_objects, '') AS detected_objects,
                COALESCE(fa.object_counts, '') AS object_counts
            FROM frame_annotations fa
            JOIN keyframes k ON k.keyframe_id = fa.frame_id
            """
        )
        hits: list[TextHit] = []
        with self.engine.connect() as connection:
            for row in connection.execute(sql).mappings():
                metadata = dict(row)
                document = " ".join(
                    str(metadata.get(field) or "")
                    for field in ("caption", "text_value", "ocr_texts", "detected_objects")
                )
                score = _overlap_score(query, document) + self._field_boost_score(query, metadata, boosts)
                if score > 0:
                    hits.append(TextHit(id=str(metadata["id"]), score=score, metadata=metadata))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[: max(1, int(top_k))]

    def _field_boost_score(self, query: str, metadata: dict[str, Any], boosts: dict[str, float]) -> float:
        if not boosts:
            return 0.0
        total = 0.0
        for field, boost in boosts.items():
            value = str(metadata.get(field) or "")
            total += float(boost) * _overlap_score(query, value)
        return total
