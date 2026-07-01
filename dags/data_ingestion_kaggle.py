"""Cloud Composer / Airflow skeleton for Kaggle-first GCS ingestion.

The DAG delegates ingestion to scripts/upload_kaggle_to_gcs.py so Kaggle
Notebook runs and Composer runs share the same manifest and metric format.

Composer deployment assumptions:
  - The repository is available on the worker filesystem.
  - GCS auth is provided by Workload Identity/ADC or environment secrets.
  - Kaggle data has already been made available on the worker input path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from airflow.decorators import dag, task
from airflow.models.param import Param


REPO_ROOT = Path(os.environ.get("INGESTION_REPO_ROOT", "/home/airflow/gcs/data/Multimodal-Retrieval"))
SCRIPT_PATH = Path(os.environ.get("KAGGLE_INGEST_SCRIPT", REPO_ROOT / "scripts" / "upload_kaggle_to_gcs.py"))
CONFIG_PATH = Path(os.environ.get("INGESTION_CONFIG_PATH", REPO_ROOT / "configs" / "data_ingestion_sources.yaml"))
RUN_DIR = Path(os.environ.get("INGESTION_RUN_DIR", "/home/airflow/gcs/data/ingestion_runs"))


@dag(
    dag_id="data_ingestion_kaggle_to_gcs",
    start_date=datetime(2026, 7, 1),
    schedule=None,
    catchup=False,
    tags=["ingestion", "kaggle", "gcs"],
    params={
        "source_id": Param("l21_l30_ai_challenge_2025", type="string"),
        "batches": Param("L21", type="string"),
        "input_root": Param("", type="string"),
        "env_file": Param("", type="string"),
        "gcs_bucket": Param("", type="string"),
        "gcs_prefix": Param("", type="string"),
        "workers": Param(4, type="integer", minimum=1),
        "max_files": Param(0, type="integer", minimum=0),
        "dry_run": Param(True, type="boolean"),
        "skip_existing": Param(True, type="boolean"),
        "upload_run_artifacts": Param(True, type="boolean"),
    },
)
def data_ingestion_kaggle_to_gcs():
    @task
    def build_command(**context) -> list[str]:
        params = context["params"]
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--config",
            str(CONFIG_PATH),
            "--source-id",
            str(params["source_id"]),
            "--batches",
            str(params["batches"]),
            "--workers",
            str(params["workers"]),
            "--run-dir",
            str(RUN_DIR),
            "--no-progress",
        ]

        if params.get("input_root"):
            command.extend(["--input-root", str(params["input_root"])])
        if params.get("env_file"):
            command.extend(["--env-file", str(params["env_file"])])
        if params.get("gcs_bucket"):
            command.extend(["--gcs-bucket", str(params["gcs_bucket"])])
        if params.get("gcs_prefix"):
            command.extend(["--gcs-prefix", str(params["gcs_prefix"])])
        if int(params.get("max_files") or 0) > 0:
            command.extend(["--max-files", str(params["max_files"])])
        if bool(params.get("dry_run")):
            command.append("--dry-run")
        if not bool(params.get("skip_existing")):
            command.append("--no-skip-existing")
        if not bool(params.get("upload_run_artifacts")):
            command.append("--no-upload-run-artifacts")

        return command

    @task
    def run_ingestion(command: list[str]) -> None:
        print("Running:", " ".join(command))
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"Ingestion command failed with exit code {completed.returncode}")

    run_ingestion(build_command())


data_ingestion_kaggle_to_gcs()
