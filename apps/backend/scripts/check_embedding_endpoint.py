#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify embedding endpoint model/dim/normalization.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    parser.add_argument("--base-url-env", default="", help="Read the base URL from this environment variable.")
    parser.add_argument("--model", default="ViT-H-14-quickgelu-dfn5b")
    parser.add_argument("--expected-dim", type=int, default=1024)
    parser.add_argument("--timeout-s", type=float, default=15.0)
    parser.add_argument("--api-key-env", default="", help="Read a Bearer token from this environment variable.")
    parser.add_argument("--skip-norm-check", action="store_true")
    return parser.parse_args()


def vector_norm(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values))


def main() -> int:
    args = parse_args()
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    base_url = os.getenv(args.base_url_env, "").strip() if args.base_url_env else args.base_url
    if args.base_url_env and not base_url:
        print(json.dumps({"ok": False, "reason": "missing_base_url_env", "env": args.base_url_env}))
        return 1
    base_url = base_url.rstrip("/")
    api_key = os.getenv(args.api_key_env, "").strip() if args.api_key_env else ""
    if args.api_key_env and not api_key:
        print(json.dumps({"ok": False, "reason": "missing_api_key_env", "env": args.api_key_env}))
        return 1
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    with httpx.Client(timeout=args.timeout_s, headers=headers) as client:
        models_resp = client.get(f"{base_url}/models")
        models_resp.raise_for_status()
        models = models_resp.json()
        available = {str(item.get("id")) for item in models.get("data", []) if isinstance(item, dict)}

        if args.model not in available:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason": "model_not_listed",
                        "expected_model": args.model,
                        "available_models": sorted(available),
                    },
                    ensure_ascii=False,
                )
            )
            return 1

        payload = {"model": args.model, "input": "nguoi phu nu mac ao do"}
        emb_resp = client.post(f"{base_url}/embeddings", json=payload)
        emb_resp.raise_for_status()
        body = emb_resp.json()
        data = body.get("data") or []
        if not data:
            print(json.dumps({"ok": False, "reason": "empty_embeddings_response"}, ensure_ascii=False))
            return 1

        vector = data[0].get("embedding")
        if not isinstance(vector, list):
            print(json.dumps({"ok": False, "reason": "invalid_embedding_type"}, ensure_ascii=False))
            return 1

        dim = len(vector)
        if dim != args.expected_dim:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason": "dimension_mismatch",
                        "expected_dim": args.expected_dim,
                        "actual_dim": dim,
                    },
                    ensure_ascii=False,
                )
            )
            return 1

        result: dict[str, object] = {
            "ok": True,
            "model": args.model,
            "dim": dim,
        }
        if not args.skip_norm_check:
            norm = vector_norm([float(v) for v in vector])
            result["norm"] = round(norm, 6)
            result["norm_close_to_1"] = abs(norm - 1.0) <= 1e-2

        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
