from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.modules.retrieval.weight_analysis as weight_analysis_module
from app.modules.retrieval.schemas import QueryWeightAnalysisRequest
from app.modules.retrieval.weight_analysis import QueryWeightAnalyzerService, ReasoningModelConfig


class SequencedReasoningClient:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[str] = []

    def complete(
        self,
        config: ReasoningModelConfig,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        _ = (system_prompt, user_prompt)
        self.calls.append(config.alias)
        if config.alias in self.failures:
            raise RuntimeError(f"{config.alias} unavailable")
        return (
            json.dumps(
                {
                    "weights": {
                        "vector_search": 0.5,
                        "ocr": 0.1,
                        "caption": 0.25,
                        "object": 0.1,
                        "asr": 0.0,
                        "temporal": 0.05,
                    },
                    "expanded_queries": [
                        "blurry low quality frame with hard to recognize object",
                        "motion blur unclear main subject visual search",
                    ],
                    "rationale": {"caption": "visual quality and scene wording matter"},
                }
            ),
            {"usage": {"total_tokens": 42}},
        )


def _models_config(tmp_path: Path) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(
        """
models:
  qwen3.6-27b:
    alias: qwen3.6-27b
    provider: groq
    model_name: qwen/qwen3.6-27b
    api_key_env: GROQ_API_KEY
    supports_reasoning_effort: false
  gpt_oss_120b:
    alias: gpt_oss_120b
    provider: groq
    model_name: openai/gpt-oss-120b
    api_key_env: GROQ_API_KEY
    supports_reasoning_effort: true
""".strip(),
        encoding="utf-8",
    )
    return path


def _service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: SequencedReasoningClient,
) -> QueryWeightAnalyzerService:
    monkeypatch.setattr(
        weight_analysis_module,
        "get_settings",
        lambda: SimpleNamespace(data_root=tmp_path / "data"),
    )
    return QueryWeightAnalyzerService(
        output_root=tmp_path / "analysis",
        reasoning_client=client,
        models_config_path=_models_config(tmp_path),
    )


def test_weight_analyzer_falls_back_from_qwen_to_gpt_oss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = SequencedReasoningClient(failures={"qwen3.6-27b"})
    service = _service(tmp_path, monkeypatch, client)

    response = service.analyze(
        QueryWeightAnalysisRequest(
            query_name="test-kis-blurry-unclear-object",
            query_type="KIS",
            query_text="Find blurry or unclear frames where the main object is hard to recognize",
        )
    )

    assert client.calls == ["qwen3.6-27b", "gpt_oss_120b"]
    assert response.model_alias == "gpt_oss_120b"
    assert len(response.expanded_queries) == 2
    assert Path(response.output_file).exists()


def test_weight_analyzer_marks_heuristic_when_all_models_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = SequencedReasoningClient(failures={"qwen3.6-27b", "gpt_oss_120b"})
    service = _service(tmp_path, monkeypatch, client)

    response = service.analyze(
        QueryWeightAnalysisRequest(
            query_name="test-kis-blurry-unclear-object",
            query_type="KIS",
            query_text="Find blurry or unclear frames where the main object is hard to recognize",
        )
    )

    assert client.calls == ["qwen3.6-27b", "gpt_oss_120b"]
    assert response.model_alias == "heuristic"
    assert response.rationale["attempted_models"] == "qwen3.6-27b, gpt_oss_120b"
    assert "qwen3.6-27b unavailable" in response.rationale["fallback"]
