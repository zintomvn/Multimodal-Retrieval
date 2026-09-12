from __future__ import annotations

import json
import math
from typing import Any

import httpx

from app.adapters.model_runtime.base import QueryExpander, TextImageEmbedder, VisualQaModel


class OpenAICompatibleTextEmbedder(TextImageEmbedder):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 15.0,
        expected_dim: int | None = None,
        l2_normalize: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout_s = timeout_s
        self.expected_dim = expected_dim
        self.l2_normalize = l2_normalize

    def embed_text(self, text: str) -> list[float]:
        values = self.embed_texts([text])
        return values[0] if values else []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = self._post(
            "/embeddings",
            {
                "model": self.model,
                "input": texts,
            },
        )
        data = payload.get("data") or []
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError(
                f"Embedding response count mismatch: expected {len(texts)}, got {len(data) if isinstance(data, list) else 0}."
            )
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0)
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list):
                raise ValueError(f"Embedding response for model '{self.model}' has no vector.")
            values = [float(value) for value in vector]
            if self.expected_dim and len(values) != self.expected_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self.expected_dim}, got {len(values)} for model '{self.model}'."
                )
            vectors.append(self._normalize(values) if self.l2_normalize else values)
        return vectors

    def embed_image_uri(self, image_uri: str) -> list[float]:
        # Fallback to text embedding for URI form. Real multimodal image embedding
        # should be implemented by a dedicated adapter when available.
        return self.embed_text(image_uri)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(f"{self.base_url}{path}", headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def _normalize(self, vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            return vector
        return [value / norm for value in vector]


class OpenAICompatibleQueryExpander(QueryExpander):
    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout_s: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout_s = timeout_s

    def expand(self, query: str, max_variants: int = 5) -> list[str]:
        prompt = (
            "You are a query expansion engine for video retrieval. "
            "Translate Vietnamese visual descriptions into concise English retrieval rewrites. "
            "Preserve exact OCR/on-screen text strings when they are likely useful. "
            "Return a JSON object with key 'variants' as an array of concise English search rewrites. "
            "Do not add explanations."
        )
        payload = self._chat(
            system_prompt=prompt,
            user_prompt=f"query: {query}\nmax_variants: {max_variants}",
        )
        content = self._content(payload)
        variants = self._parse_variants(content)
        if not variants:
            return [query.strip()] if query.strip() else []
        deduped: list[str] = []
        for item in variants:
            value = str(item).strip()
            if value and value not in deduped:
                deduped.append(value)
        return deduped[: max(1, max_variants)]

    def _parse_variants(self, content: str) -> list[str]:
        if not content:
            return []
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and isinstance(parsed.get("variants"), list):
                return [str(item) for item in parsed["variants"]]
        except json.JSONDecodeError:
            pass
        # Fallback: split lines/bullets.
        lines = [line.strip(" -•\t") for line in content.splitlines() if line.strip()]
        return lines

    def _chat(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def _content(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        return str(content or "")


class OpenAICompatibleVisualQaModel(VisualQaModel):
    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout_s: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout_s = timeout_s

    def answer(self, question: str, evidence_text: str, answer_hint: str | None = None) -> str:
        system_prompt = (
            "You are a visual QA assistant for keyframe retrieval. "
            "Answer very briefly, at most one short phrase."
        )
        user_prompt = (
            f"question: {question}\n"
            f"evidence: {evidence_text}\n"
            f"answer_hint: {answer_hint or ''}\n"
            "answer:"
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        return str(message.get("content") or "").strip()
