#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import yaml

try:
    import open_clip  # type: ignore[import-untyped]
except Exception as exc:  # pragma: no cover - runtime guard
    raise RuntimeError(
        "Missing dependency 'open_clip_torch'. Install with: "
        "pip install -r apps/backend/requirements-embedding-service.txt"
    ) from exc


@dataclass
class RuntimeConfig:
    model: str
    pretrained: str
    device: str
    l2_normalize: bool
    max_batch_size: int
    model_id: str


@dataclass
class RegistryDefaults:
    model_id: str
    openclip_model: str
    openclip_pretrained: str
    openclip_device: str
    openclip_max_batch: int
    l2_normalize: bool


DEFAULT_MODEL_ID = "ViT-H-14-quickgelu-dfn5b"
DEFAULT_OPENCLIP_MODEL = "ViT-H-14-quickgelu"
DEFAULT_OPENCLIP_PRETRAINED = "dfn5b"
DEFAULT_OPENCLIP_DEVICE = "cpu"
DEFAULT_OPENCLIP_MAX_BATCH = 16
DEFAULT_L2_NORMALIZE = True


class EmbeddingRequest(BaseModel):
    model: str | None = None
    input: str | list[str]


def _as_list(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_device(value: str) -> str:
    requested = str(value or "").strip().lower()
    if requested in {"", "auto"}:
        return "cuda" if torch.cuda.is_available() else "cpu"
    return str(value)


def _build_runtime(config: RuntimeConfig) -> dict[str, Any]:
    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name=config.model,
        pretrained=config.pretrained,
        device=config.device,
    )
    tokenizer = open_clip.get_tokenizer(config.model)
    model.eval()
    return {
        "model": model,
        "tokenizer": tokenizer,
        "preprocess": preprocess,
    }


def build_app(config: RuntimeConfig) -> FastAPI:
    runtime = _build_runtime(config)
    app = FastAPI(title="OpenCLIP Embedding Service", version="1.0.0")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "model_id": config.model_id,
            "model": config.model,
            "pretrained": config.pretrained,
            "device": config.device,
            "l2_normalize": config.l2_normalize,
        }

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": config.model_id,
                    "object": "model",
                    "owned_by": "local",
                }
            ],
        }

    @app.post("/v1/embeddings")
    def embeddings(request: EmbeddingRequest) -> dict[str, Any]:
        texts = _as_list(request.input)
        if not texts:
            raise HTTPException(status_code=400, detail="input must not be empty")
        if len(texts) > config.max_batch_size:
            raise HTTPException(
                status_code=400,
                detail=f"batch too large: {len(texts)} > {config.max_batch_size}",
            )
        if request.model and request.model != config.model_id:
            raise HTTPException(
                status_code=400,
                detail=f"model mismatch: requested '{request.model}', available '{config.model_id}'",
            )

        tokenizer = runtime["tokenizer"]
        model = runtime["model"]
        with torch.no_grad():
            tokens = tokenizer(texts).to(config.device)
            vectors = model.encode_text(tokens)
            vectors = vectors.to(torch.float32)
            if config.l2_normalize:
                vectors = torch.nn.functional.normalize(vectors, dim=-1)

        data: list[dict[str, Any]] = []
        token_count = 0
        for index, vector in enumerate(vectors):
            data.append(
                {
                    "object": "embedding",
                    "index": index,
                    "embedding": vector.detach().cpu().tolist(),
                }
            )
            token_count += int(tokens[index].numel())

        return {
            "object": "list",
            "data": data,
            "model": config.model_id,
            "usage": {"prompt_tokens": token_count, "total_tokens": token_count},
        }

    return app


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_registry_defaults(registry_path: Path) -> RegistryDefaults:
    defaults = RegistryDefaults(
        model_id=DEFAULT_MODEL_ID,
        openclip_model=DEFAULT_OPENCLIP_MODEL,
        openclip_pretrained=DEFAULT_OPENCLIP_PRETRAINED,
        openclip_device=DEFAULT_OPENCLIP_DEVICE,
        openclip_max_batch=DEFAULT_OPENCLIP_MAX_BATCH,
        l2_normalize=DEFAULT_L2_NORMALIZE,
    )
    if not registry_path.exists():
        return defaults
    with registry_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    embedders = data.get("embedders", {})
    if not isinstance(embedders, dict):
        return defaults
    selected: dict[str, Any] | None = None
    fallback_selected: dict[str, Any] | None = None
    for config in embedders.values():
        if isinstance(config, dict) and config.get("enabled"):
            if fallback_selected is None:
                fallback_selected = config
            provider = str(config.get("provider", "")).lower()
            if provider == "openai_compatible":
                selected = config
                break
    if selected is None:
        selected = fallback_selected
    if selected is None:
        return defaults
    model_id = str(selected.get("model", defaults.model_id)).strip() or defaults.model_id
    return RegistryDefaults(
        model_id=model_id,
        openclip_model=str(selected.get("openclip_model", defaults.openclip_model)).strip() or defaults.openclip_model,
        openclip_pretrained=str(selected.get("openclip_pretrained", defaults.openclip_pretrained)).strip()
        or defaults.openclip_pretrained,
        openclip_device=str(selected.get("openclip_device", selected.get("device", defaults.openclip_device))).strip()
        or defaults.openclip_device,
        openclip_max_batch=_to_int(selected.get("openclip_max_batch"), defaults.openclip_max_batch),
        l2_normalize=_to_bool(selected.get("l2_normalize"), defaults.l2_normalize),
    )


def parse_args(registry_defaults: RegistryDefaults, default_registry_path: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve OpenCLIP as OpenAI-compatible embedding endpoint.")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional path to .env file. Default: auto-detect project .env then .env.example.",
    )
    parser.add_argument(
        "--model-registry",
        default=str(default_registry_path),
        help="Path to model_registry.yaml used for service defaults.",
    )
    parser.add_argument("--host", default=os.getenv("EMBED_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("EMBED_PORT", "8001")))
    parser.add_argument("--model-id", default=registry_defaults.model_id)
    parser.add_argument("--model", default=registry_defaults.openclip_model)
    parser.add_argument("--pretrained", default=registry_defaults.openclip_pretrained)
    parser.add_argument("--device", default=registry_defaults.openclip_device)
    parser.add_argument("--max-batch-size", type=int, default=registry_defaults.openclip_max_batch)
    parser.add_argument("--l2-normalize", dest="l2_normalize", action="store_true")
    parser.add_argument("--no-l2-normalize", dest="l2_normalize", action="store_false")
    parser.set_defaults(l2_normalize=registry_defaults.l2_normalize)
    return parser.parse_args()


def _load_default_env(env_file: str | None) -> None:
    if env_file:
        load_dotenv(env_file, override=False)
        return

    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[3]
    candidates = [repo_root / ".env", repo_root / ".env.example"]
    for candidate in candidates:
        if candidate.exists():
            load_dotenv(candidate, override=False)
            break


def main() -> None:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=None)
    pre_parser.add_argument("--model-registry", default=None)
    pre_args, _ = pre_parser.parse_known_args()

    _load_default_env(os.getenv("EMBED_ENV_FILE"))
    _load_default_env(pre_args.env_file)

    default_registry_path = Path(
        pre_args.model_registry
        or os.getenv("MODEL_REGISTRY_PATH")
        or (_repo_root() / "configs/model_registry.yaml")
    )
    registry_defaults = _load_registry_defaults(default_registry_path)

    args = parse_args(registry_defaults=registry_defaults, default_registry_path=default_registry_path)
    config = RuntimeConfig(
        model=args.model,
        pretrained=args.pretrained,
        device=_resolve_device(args.device),
        l2_normalize=args.l2_normalize,
        max_batch_size=args.max_batch_size,
        model_id=args.model_id,
    )
    app = build_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
