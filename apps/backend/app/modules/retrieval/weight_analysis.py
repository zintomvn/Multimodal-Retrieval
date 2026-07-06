from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from app.core.config import get_settings
from app.modules.retrieval.prompts import (
    WEIGHT_ANALYZER_SYSTEM_PROMPT,
    build_weight_analyzer_user_prompt,
)
from app.modules.retrieval.schemas import QueryType, QueryWeightAnalysisRequest, QueryWeightAnalysisResponse


WEIGHT_KEYS = ("vector_search", "ocr", "caption", "object", "asr", "temporal")
DEFAULT_MODEL_PRIORITY = ("qwen3.6-27b", "gpt_oss_120b")
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

WEIGHT_ALIASES = {
    "vector": "vector_search",
    "vector search": "vector_search",
    "semantic": "vector_search",
    "semantic_search": "vector_search",
    "metadata": "caption",
    "text": "ocr",
    "text_search": "ocr",
    "ocr_text": "ocr",
    "captions": "caption",
    "objects": "object",
    "object_detection": "object",
    "speech": "asr",
    "transcript": "asr",
    "audio": "asr",
    "sequence": "temporal",
    "time": "temporal",
}


class ReasoningClient(Protocol):
    def complete(self, config: "ReasoningModelConfig", system_prompt: str, user_prompt: str) -> tuple[str, dict[str, Any]]:
        ...


@dataclass(frozen=True)
class ReasoningModelConfig:
    alias: str
    provider: str
    model_name: str
    api_key_env: str | None = None
    base_url_env: str | None = None
    supports_reasoning_effort: bool = False


class OpenAICompatibleReasoningClient:
    def __init__(self, timeout_s: float = 30.0) -> None:
        self.timeout_s = timeout_s

    def complete(self, config: ReasoningModelConfig, system_prompt: str, user_prompt: str) -> tuple[str, dict[str, Any]]:
        api_key = os.getenv(config.api_key_env or "")
        if not api_key:
            raise RuntimeError(f"Missing API key env var: {config.api_key_env or '<unset>'}")

        base_url = self._base_url(config)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload: dict[str, Any] = {
            "model": config.model_name,
            "temperature": 0.1,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if config.supports_reasoning_effort:
            payload["reasoning_effort"] = "medium"

        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else []
        if not choices:
            return "", body if isinstance(body, dict) else {}
        message = (choices[0] or {}).get("message") or {}
        return str(message.get("content") or ""), body

    def _base_url(self, config: ReasoningModelConfig) -> str:
        if config.base_url_env:
            value = os.getenv(config.base_url_env, "").strip()
            if value:
                return value.rstrip("/")
        if config.provider == "groq":
            return DEFAULT_GROQ_BASE_URL
        raise RuntimeError(f"Missing base URL env var: {config.base_url_env or '<unset>'}")


class QueryWeightAnalyzerService:
    def __init__(
        self,
        *,
        output_root: Path | None = None,
        reasoning_client: ReasoningClient | None = None,
        models_config_path: Path | None = None,
    ) -> None:
        self.settings = get_settings()
        self.output_root = output_root or self.settings.data_root / "query_weight_analysis"
        self.reasoning_client = reasoning_client or OpenAICompatibleReasoningClient()
        self.models_config_path = models_config_path or self._default_models_config_path()

    def analyze(self, request: QueryWeightAnalysisRequest) -> QueryWeightAnalysisResponse:
        query_text = request.query_text.strip()
        if not query_text:
            raise ValueError("query_text must not be empty")

        model_config = self._select_model(request.model_alias)
        model_alias = model_config.alias if model_config else "heuristic"
        model_error: str | None = None
        model_payload: dict[str, Any] | None = None

        if model_config is not None:
            try:
                system_prompt = WEIGHT_ANALYZER_SYSTEM_PROMPT
                user_prompt = build_weight_analyzer_user_prompt(query_text=query_text, query_type=request.query_type)
                content, usage_payload = self.reasoning_client.complete(model_config, system_prompt, user_prompt)
                parsed = parse_model_json(content)
                model_payload = self._coerce_payload(parsed, query_text)
                self._log_langsmith(
                    request=request,
                    model_alias=model_alias,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    raw_content=content,
                    raw_payload=usage_payload,
                    parsed_payload=model_payload,
                    error=None,
                )
                self._log_mlflow(
                    request=request,
                    model_alias=model_alias,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    raw_content=content,
                    raw_payload=usage_payload,
                    parsed_payload=model_payload,
                    error=None,
                )
            except Exception as exc:  # noqa: BLE001 - fallback is intentional for runtime model instability.
                model_error = str(exc)

        if model_payload is None:
            model_payload = heuristic_weight_payload(query_text=query_text, query_type=request.query_type)
            if model_error:
                model_payload["rationale"]["fallback"] = model_error

        response = self._build_response(
            request=request,
            model_alias=model_alias,
            payload=model_payload,
        )
        self._write_response(response)
        return response

    def _coerce_payload(self, payload: dict[str, Any], query_text: str) -> dict[str, Any]:
        weights_raw = payload.get("weights") if isinstance(payload, dict) else None
        if not isinstance(weights_raw, dict):
            raise ValueError("Model output missing weights object")
        weights = normalize_weight_dict(weights_raw)
        expanded_raw = payload.get("expanded_queries") if isinstance(payload, dict) else None
        expanded_queries = _clean_string_list(expanded_raw)
        if not expanded_queries:
            expanded_queries = [query_text]

        rationale_raw = payload.get("rationale") if isinstance(payload, dict) else None
        rationale = {
            str(key): str(value)
            for key, value in (rationale_raw or {}).items()
            if str(key).strip() and str(value).strip()
        } if isinstance(rationale_raw, dict) else {}

        return {
            "weights": weights,
            "expanded_queries": expanded_queries[:8],
            "rationale": rationale,
        }

    def _build_response(
        self,
        request: QueryWeightAnalysisRequest,
        model_alias: str,
        payload: dict[str, Any],
    ) -> QueryWeightAnalysisResponse:
        weights = normalize_weight_dict(payload.get("weights"))
        response = QueryWeightAnalysisResponse(
            query_name=request.query_name,
            query_type=request.query_type,
            query_text=request.query_text.strip(),
            model_alias=model_alias,
            weights=weights,
            expanded_queries=_clean_string_list(payload.get("expanded_queries")) or [request.query_text.strip()],
            resolved_profile=resolved_profile_from_weights(weights),
            rationale={
                str(key): str(value)
                for key, value in (payload.get("rationale") or {}).items()
                if str(key).strip() and str(value).strip()
            },
            output_file=self._display_output_path(request),
        )
        return response

    def _write_response(self, response: QueryWeightAnalysisResponse) -> None:
        path = self._resolve_output_path(response.output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(response.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)

    def _display_output_path(self, request: QueryWeightAnalysisRequest) -> str:
        filename = self._output_filename(request)
        if self.output_root == self.settings.data_root / "query_weight_analysis":
            return f"data/query_weight_analysis/{filename}"
        return str(self.output_root / filename)

    def _resolve_output_path(self, output_file: str) -> Path:
        if output_file.startswith("data/query_weight_analysis/"):
            return self.output_root / Path(output_file).name
        return Path(output_file)

    def _output_filename(self, request: QueryWeightAnalysisRequest) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        label = request.query_name or "query"
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-") or "query"
        return f"{timestamp}-{safe_label}-{uuid.uuid4().hex[:8]}.json"

    def _select_model(self, requested_alias: str | None) -> ReasoningModelConfig | None:
        models = self._load_models()
        if requested_alias and requested_alias in models:
            return models[requested_alias]
        for alias in DEFAULT_MODEL_PRIORITY:
            if alias in models:
                return models[alias]
        return None

    def _load_models(self) -> dict[str, ReasoningModelConfig]:
        if not self.models_config_path.exists():
            return {}
        with self.models_config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        models_raw = raw.get("models", {})
        if not isinstance(models_raw, dict):
            return {}
        models: dict[str, ReasoningModelConfig] = {}
        for alias, config in models_raw.items():
            if not isinstance(config, dict):
                continue
            model_name = str(config.get("model_name") or "").strip()
            if not model_name:
                continue
            resolved_alias = str(config.get("alias") or alias)
            models[resolved_alias] = ReasoningModelConfig(
                alias=resolved_alias,
                provider=str(config.get("provider") or "openai_compatible"),
                model_name=model_name,
                api_key_env=str(config.get("api_key_env") or "") or None,
                base_url_env=str(config.get("base_url_env") or "") or None,
                supports_reasoning_effort=bool(config.get("supports_reasoning_effort", False)),
            )
        return models

    def _default_models_config_path(self) -> Path:
        return Path(__file__).resolve().parents[5] / "notebooks" / "agent" / "experiments" / "h1" / "config" / "models.yaml"

    def _log_langsmith(
        self,
        *,
        request: QueryWeightAnalysisRequest,
        model_alias: str,
        system_prompt: str,
        user_prompt: str,
        raw_content: str,
        raw_payload: dict[str, Any],
        parsed_payload: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        if os.getenv("LANGSMITH_TRACING", "").lower() not in {"1", "true", "yes", "on"}:
            return
        if not os.getenv("LANGSMITH_API_KEY"):
            return
        try:
            from langsmith import Client  # type: ignore

            project_name = os.getenv("LANGSMITH_PROJECT", "multimodal-retrieval-weight-analyzer")
            client = Client()
            client.create_run(
                name="query_weight_analysis",
                run_type="llm",
                project_name=project_name,
                inputs={
                    "query_text": request.query_text,
                    "query_type": request.query_type,
                    "query_name": request.query_name,
                    "model_alias": model_alias,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                outputs={
                    "raw_content": raw_content,
                    "raw_payload": raw_payload,
                    "parsed_payload": parsed_payload,
                },
                error=error,
            )
        except Exception:
            return

    def _log_mlflow(
        self,
        *,
        request: QueryWeightAnalysisRequest,
        model_alias: str,
        system_prompt: str,
        user_prompt: str,
        raw_content: str,
        raw_payload: dict[str, Any],
        parsed_payload: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "").strip()
        experiment_name = os.getenv("MLFLOW_EXPERIMENT_NAME", "multimodal-retrieval-weight-analyzer").strip()
        if not tracking_uri and not experiment_name:
            return
        try:
            import mlflow  # type: ignore

            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(experiment_name or "multimodal-retrieval-weight-analyzer")
            with mlflow.start_run(run_name="query_weight_analysis"):
                mlflow.log_params(
                    {
                        "query_type": request.query_type,
                        "query_name": request.query_name or "",
                        "model_alias": model_alias,
                    }
                )
                mlflow.log_dict(
                    {
                        "query_text": request.query_text,
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                    },
                    "inputs.json",
                )
                mlflow.log_dict(
                    {
                        "raw_content": raw_content,
                        "raw_payload": raw_payload,
                        "parsed_payload": parsed_payload,
                        "error": error,
                    },
                    "outputs.json",
                )
        except Exception:
            return


def parse_model_json(content: str) -> dict[str, Any]:
    if not content or not content.strip():
        raise ValueError("Model returned empty content")
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Model output must be a JSON object")
    return parsed


def normalize_weight_dict(raw: Any) -> dict[str, float]:
    weights = {key: 0.0 for key in WEIGHT_KEYS}
    if isinstance(raw, dict):
        for raw_key, raw_value in raw.items():
            key = _normalize_weight_key(str(raw_key))
            if key not in weights:
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                value = 0.0
            weights[key] = max(0.0, min(1.0, value))

    total = sum(weights.values())
    if total <= 0:
        weights["vector_search"] = 1.0
        total = 1.0

    normalized = {key: weights[key] / total for key in WEIGHT_KEYS}
    rounded = {key: round(value, 6) for key, value in normalized.items()}
    delta = round(1.0 - sum(rounded.values()), 6)
    if delta:
        rounded["vector_search"] = round(rounded["vector_search"] + delta, 6)
    return rounded


def resolved_profile_from_weights(weights: dict[str, float]) -> dict[str, float]:
    semantic_weight = float(weights.get("vector_search", 0.0))
    temporal_weight = float(weights.get("temporal", 0.0))
    metadata_weight = max(0.0, 1.0 - semantic_weight - temporal_weight)
    return {
        "semantic_weight": round(semantic_weight, 6),
        "metadata_weight": round(metadata_weight, 6),
        "temporal_weight": round(temporal_weight, 6),
    }


def heuristic_weight_payload(query_text: str, query_type: QueryType) -> dict[str, Any]:
    query = _fold_text(query_text)
    if query_type == "TRAKE":
        weights = {"vector_search": 0.45, "ocr": 0.05, "caption": 0.15, "object": 0.05, "asr": 0.0, "temporal": 0.30}
    elif query_type == "QA":
        weights = {"vector_search": 0.45, "ocr": 0.25, "caption": 0.20, "object": 0.05, "asr": 0.05, "temporal": 0.0}
    else:
        weights = {"vector_search": 0.65, "ocr": 0.10, "caption": 0.15, "object": 0.10, "asr": 0.0, "temporal": 0.0}

    rationale = {"vector_search": "default visual retrieval signal"}

    if _contains_any(query, ["chu", "text", "logo", "bien", "bang", "hien thi", "scoreboard", "so diem", '"', "'"]):
        weights["ocr"] += 0.20
        weights["caption"] += 0.05
        rationale["ocr"] = "query mentions visible text, labels, numbers, or screen content"

    if _contains_any(query, ["mau gi", "color", "rem", "ao mau", "mau sac", "hoi"]):
        weights["caption"] += 0.15
        weights["ocr"] += 0.05
        rationale["caption"] = "question needs visual attributes described by captions"

    if _contains_any(query, ["noi", "ke", "am thanh", "giong", "phat bieu", "hoi thoai", "tra loi"]):
        weights["asr"] += 0.15
        rationale["asr"] = "query may depend on spoken narration or dialogue"

    if query_type == "TRAKE" or re.search(r"\be\d+\b", query) or _contains_any(query, ["roi", "sau do", "tiep theo", "dau tien", "cuoi cung", "chuoi"]):
        weights["temporal"] += 0.20
        rationale["temporal"] = "query contains ordered events or temporal sequence constraints"

    if _contains_any(query, ["nguoi", "xe", "ao", "non", "ban", "cua", "hoc sinh", "giao vien"]):
        weights["object"] += 0.05
        rationale["object"] = "query includes recognizable objects or entities"

    normalized_weights = normalize_weight_dict(weights)
    return {
        "weights": normalized_weights,
        "expanded_queries": [query_text.strip()],
        "rationale": rationale,
        "resolved_profile": resolved_profile_from_weights(normalized_weights),
    }


def _normalize_weight_key(key: str) -> str:
    normalized = key.strip().lower().replace("-", "_")
    normalized = normalized.replace(" ", "_")
    if normalized in WEIGHT_KEYS:
        return normalized
    return WEIGHT_ALIASES.get(key.strip().lower(), WEIGHT_ALIASES.get(normalized, normalized))


def _clean_string_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value and value not in values:
            values.append(value)
    return values


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _fold_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    without_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return without_marks.replace("đ", "d").replace("Đ", "D").lower()