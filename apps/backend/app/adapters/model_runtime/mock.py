from __future__ import annotations

import hashlib
import math
import re

from app.adapters.model_runtime.base import QueryExpander, TextImageEmbedder, VisualQaModel


def _stable_float(seed: str, offset: int) -> float:
    digest = hashlib.sha256(f"{seed}:{offset}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") / 2**32
    return value * 2 - 1


class MockEmbedder(TextImageEmbedder):
    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed_text(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_image_uri(self, image_uri: str) -> list[float]:
        return self._embed(image_uri)

    def _embed(self, seed: str) -> list[float]:
        vector = [_stable_float(seed.lower(), i) for i in range(self.dim)]
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class MockQueryExpander(QueryExpander):
    def expand(self, query: str, max_variants: int = 5) -> list[str]:
        variants = [query.strip()]
        lower = query.lower()
        if "castle" in lower or "bavaria" in lower:
            variants.extend(
                [
                    "Neuschwanstein castle Bavaria Disney logo inspiration",
                    "famous company logo inspired by a German castle",
                ]
            )
        if "helmet" in lower or "cyclist" in lower:
            variants.extend(
                [
                    "cyclists crossing finish line in order",
                    "pink helmet blue helmet red helmet bicycle race finish line",
                ]
            )
        if "exhibition" in lower or "dragon" in lower:
            variants.extend(
                [
                    "royal decorative panel dragon cloud motifs exhibition",
                    "PHU XUAN GIA DINH historical exhibition gate",
                ]
            )
        deduped: list[str] = []
        for item in variants:
            if item and item not in deduped:
                deduped.append(item)
        return deduped[:max_variants]


class MockVisualQaModel(VisualQaModel):
    def answer(self, question: str, evidence_text: str, answer_hint: str | None = None) -> str:
        if answer_hint:
            return answer_hint[:100]
        combined = f"{question} {evidence_text}".lower()
        if "company" in combined and ("castle" in combined or "logo" in combined):
            return "Disney"
        if re.search(r"\bhow many\b|bao nhiêu|mấy", combined):
            numbers = re.findall(r"\b\d+\b", combined)
            return numbers[0] if numbers else "3"
        if "color" in combined or "màu" in combined:
            for color in ["red", "blue", "pink", "green", "yellow", "đỏ", "xanh", "hồng", "vàng"]:
                if color in combined:
                    return color.capitalize()
        return "Không rõ"
