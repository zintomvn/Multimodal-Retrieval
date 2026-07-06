from __future__ import annotations

import json

from app.modules.retrieval.schemas import QueryType


WEIGHT_ANALYZER_SYSTEM_PROMPT = """You are a routing planner for a multimodal video retrieval system.
Return only one strict JSON object. Do not include markdown, commentary, or hidden reasoning.

Allowed search channels:
- vector_search: visual semantic embedding retrieval over keyframes
- ocr: visible text, signs, subtitles, labels, screen text, scoreboard numbers
- caption: generated visual captions and scene descriptions
- object: detected object/entity labels
- asr: spoken words, narration, dialogue, audio transcript
- temporal: ordered multi-event or sequence constraints

The weights must be non-negative numbers and should sum to 1.0. Prefer vector_search
for ordinary visual scene descriptions. Increase ocr for visible text and questions
asking what text/numbers/colors appear on screen. Increase temporal for TRAKE and
queries with ordered events such as E1/E2/E3, first/then/after. Increase asr when
the query depends on speech, narration, or what someone says.
"""


def build_weight_analyzer_user_prompt(query_text: str, query_type: QueryType) -> str:
    schema = {
        "weights": {
            "vector_search": 0.55,
            "ocr": 0.20,
            "caption": 0.15,
            "object": 0.05,
            "asr": 0.0,
            "temporal": 0.05,
        },
        "expanded_queries": ["concise English retrieval rewrite"],
        "rationale": {
            "vector_search": "why this channel matters",
            "ocr": "why this channel matters",
        },
    }
    return (
        "Analyze this retrieval query and output JSON matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"query_type: {query_type}\n"
        f"query_text: {query_text}\n"
    )