#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer


class EmbeddingRequest(BaseModel):
    model: str | None = None
    input: str | list[str]


def build_app(*, model_id: str, device: str, max_batch_size: int) -> FastAPI:
    model = SentenceTransformer(model_id, device=device, trust_remote_code=True)
    app = FastAPI(title="Qwen3-VL Embedding Service", version="1.0.0")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "model": model_id, "device": device}

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": model_id, "object": "model", "owned_by": "local"}]}

    @app.post("/v1/embeddings")
    def embeddings(request: EmbeddingRequest) -> dict[str, Any]:
        texts = [request.input] if isinstance(request.input, str) else list(request.input)
        if not texts:
            raise HTTPException(status_code=400, detail="input must not be empty")
        if len(texts) > max_batch_size:
            raise HTTPException(status_code=400, detail=f"batch too large: {len(texts)} > {max_batch_size}")
        if request.model and request.model != model_id:
            raise HTTPException(status_code=400, detail=f"model mismatch: requested '{request.model}', available '{model_id}'")

        vectors = model.encode(
            [str(text) for text in texts],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        data = [
            {"object": "embedding", "index": index, "embedding": vector.astype("float32").tolist()}
            for index, vector in enumerate(vectors)
        ]
        return {
            "object": "list",
            "data": data,
            "model": model_id,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Qwen3-VL-Embedding through an OpenAI-compatible endpoint.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8004)
    parser.add_argument("--model-id", default="Qwen/Qwen3-VL-Embedding-2B")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--max-batch-size", type=int, default=16)
    args = parser.parse_args()
    app = build_app(model_id=args.model_id, device=args.device, max_batch_size=args.max_batch_size)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
