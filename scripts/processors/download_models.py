from __future__ import annotations

import argparse
import logging
from pathlib import Path

from extractors import FrameFeatureExtractor
from pipeline_config import apply_cli_overrides, load_pipeline_config


DEFAULT_CONFIG = Path(__file__).resolve().with_name("processor_config.yaml")
LOGGER = logging.getLogger("model_downloader")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for model warmup/download."""
    parser = argparse.ArgumentParser(description="Download and cache enabled processor models.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="YAML config path.")
    parser.add_argument("--profile", default="smoke", help="Config profile to merge.")
    parser.add_argument("--features", default="", help="Optional comma-separated feature override.")
    parser.add_argument("--device", default="", help="auto, cpu, cuda, cuda:0...")
    parser.add_argument("--model-cache-dir", default="", help="Local model cache directory override.")
    parser.add_argument("--log-file", default="", help="Optional log path override.")
    return parser.parse_args()


def setup_logging(log_file: str) -> None:
    """Configure console and optional file logging."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=handlers,
    )


def main() -> None:
    """Warm up all enabled models in the selected config profile."""
    args = parse_args()
    config = apply_cli_overrides(load_pipeline_config(args.config, args.profile), vars(args))
    log_file = str(config.raw.get("run", {}).get("log_file", "")) if not args.log_file else args.log_file
    setup_logging(log_file)
    LOGGER.info("Using config=%s profile=%s features=%s", args.config, args.profile, sorted(config.enabled_features))
    extractor = FrameFeatureExtractor(config.raw.get("models", {}))
    extractor.warmup()
    LOGGER.info("Model warmup complete. Cache dir: %s", config.raw.get("models", {}).get("cache_dir"))


if __name__ == "__main__":
    main()

