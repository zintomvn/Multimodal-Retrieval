from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import sys
import numpy as np

PROCESSORS_ROOT = Path(__file__).resolve().parents[1]
if str(PROCESSORS_ROOT) not in sys.path:
    sys.path.insert(0, str(PROCESSORS_ROOT))

from src.artifact_io import ArtifactStore, gcs_client
from src.checkpoint_store import CheckpointStore
from src.cloud_sinks.config import SinkConfig
from src.config import ProcessorSettings
from src.config_paths import DEFAULT_PROCESSOR_CONFIG, resolve_config_path
from src.gcs_source import FrameItem, GCSFrameSource, _shot_index_from_row
from src.ingest_artifacts import ArtifactImportOptions, import_feature_artifacts
from src.manifest import build_gcs_keyframe_manifest, write_manifest_and_shards
from src.notebook_cells import NotebookCellsOptions, NotebookKitOptions, render_notebook_cells, render_notebook_kit
from src.notebook_role_runner import NotebookRoleRunOptions, render_or_run_notebook_role
from src.pipeline_config import load_pipeline_config
from src.processor_doctor import DoctorOptions, run_processor_doctor
from src.reconcile_run import RunReconcileOptions, reconcile_feature_run
from src.role_planner import RolePlanOptions, build_notebook_run_plan
from src.shard_runner import ShardRunOptions, run_feature_shard
from src.smoke_flow import LocalSmokeFlowOptions, run_local_smoke_flow
from extract_gcs_asr import _part_uri as asr_part_uri
from src.extractors.caption import OpenAiCompatibleVlmCaptioner, _env_expanded
from src.extractors.pipeline import FrameFeatureExtractor


class ProcessorPipelineTests(unittest.TestCase):
    def test_legacy_processor_config_path_resolves_to_new_yaml(self) -> None:
        legacy_path = PROCESSORS_ROOT / "processor_config.yaml"
        self.assertEqual(resolve_config_path(legacy_path), DEFAULT_PROCESSOR_CONFIG)
        self.assertTrue(DEFAULT_PROCESSOR_CONFIG.exists())

    def test_visual_embedding_profiles_use_requested_models(self) -> None:
        default_config = load_pipeline_config(str(DEFAULT_PROCESSOR_CONFIG), profile="embedding_only")
        default_embedding = default_config.raw["models"]["embedding"]
        self.assertEqual(default_embedding["provider"], "openclip")
        self.assertEqual(default_embedding["model_name"], "hf-hub:timm/PE-Core-bigG-14-448")
        self.assertEqual(default_embedding["pretrained"], "")
        self.assertEqual(default_config.raw["sinks"]["milvus_collection"], "keyframe_embeddings_pe_core_bigG_14_448")
        self.assertEqual(default_config.raw["sinks"]["model_version"], "pe-core-bigG-14-448")

        primary = load_pipeline_config(str(DEFAULT_PROCESSOR_CONFIG), profile="visual_primary_pe_core").raw
        primary_embedding = primary["models"]["embedding"]
        self.assertTrue(primary_embedding["enabled"])
        self.assertEqual(primary_embedding["model_name"], "hf-hub:timm/PE-Core-bigG-14-448")
        self.assertEqual(primary_embedding["pretrained"], "")
        self.assertFalse(primary["models"]["caption"]["enabled"])

        secondary = load_pipeline_config(str(DEFAULT_PROCESSOR_CONFIG), profile="visual_secondary_openclip_vith14").raw
        secondary_embedding = secondary["models"]["embedding"]
        self.assertTrue(secondary_embedding["enabled"])
        self.assertEqual(secondary_embedding["model_name"], "ViT-H-14")
        self.assertEqual(secondary_embedding["pretrained"], "laion2b_s32b_b79k")
        self.assertFalse(secondary["models"]["caption"]["enabled"])

    def test_blip2_profile_is_caption_verification_only(self) -> None:
        config = load_pipeline_config(str(DEFAULT_PROCESSOR_CONFIG), profile="blip2_caption_verification")
        self.assertEqual(config.enabled_features, {"caption"})
        self.assertFalse(config.raw["models"]["embedding"]["enabled"])
        caption = config.raw["models"]["caption"]
        self.assertEqual(caption["provider"], "blip2")
        self.assertEqual(caption["model_class"], "Blip2ForConditionalGeneration")
        self.assertEqual(caption["model_name"], "Salesforce/blip2-opt-2.7b")

    def test_artifact_store_reads_utf8_sig_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jsonl"
            path.write_bytes("\ufeff".encode("utf-8") + json.dumps({"a": 1}).encode("utf-8") + b"\n")
            store = ArtifactStore()
            rows = store.read_jsonl(str(path))
            self.assertEqual(rows, [{"a": 1}])

    def test_gcs_client_can_use_service_account_json_env(self) -> None:
        payload = {"type": "service_account", "project_id": "demo"}
        with mock.patch.dict("os.environ", {"GCS_SERVICE_ACCOUNT_JSON": json.dumps(payload)}, clear=False), mock.patch(
            "google.cloud.storage.Client.from_service_account_info",
            return_value="client-from-info",
        ) as from_info:
            self.assertEqual(gcs_client(), "client-from-info")
        from_info.assert_called_once_with(payload)

    def test_checkpoint_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(root_uri=str(Path(tmp) / "checkpoints"), run_id="run-1", lease_ttl_seconds=60)
            claimed = store.try_claim("ocr", "shard-00000", "worker-a")
            self.assertTrue(claimed.claimed)
            store.save("ocr", "shard-00000", {"next_index": 3, "processed": 3, "failed": 0})
            data = store.load("ocr", "shard-00000")
            self.assertEqual(data["next_index"], 3)
            store.mark_complete("ocr", "shard-00000", {"processed": 3})
            self.assertTrue(store.is_complete("ocr", "shard-00000"))

    def test_checkpoint_blocks_stale_worker_after_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(root_uri=str(Path(tmp) / "checkpoints"), run_id="run-1", lease_ttl_seconds=60)
            self.assertTrue(store.try_claim("ocr", "shard-00000", "worker-a").claimed)
            lease_uri = store.lease_uri("ocr", "shard-00000")
            store.store.write_json(lease_uri, {"worker_id": "worker-b", "expires_at_epoch": 9999999999})
            self.assertFalse(store.can_write("ocr", "shard-00000", "worker-a"))
            with self.assertRaises(RuntimeError):
                store.heartbeat("ocr", "shard-00000", "worker-a")

    def test_manifest_and_shard_write_local(self) -> None:
        rows = [
            {
                "dataset_id": "ai_challenge_2025",
                "batch": "L21",
                "video_id": "L21_V001",
                "keyframe_id": "L21_V001_F000001",
                "frame_idx": 1,
                "frame_seconds": 0.0,
                "frame_type": "middle",
                "gcs_uri": "gs://bucket/path.jpg",
                "bucket": "bucket",
                "blob_name": "path.jpg",
                "image_name": "path.jpg",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            summary = write_manifest_and_shards(rows, output_root=tmp, run_id="run-1", frames_per_shard=1)
            self.assertEqual(summary["frames"], 1)
            self.assertEqual(summary["num_shards"], 1)
            self.assertTrue(Path(summary["manifest_uri"]).exists())
            self.assertTrue(Path(summary["summary_uri"]).exists())

    def test_manifest_shot_segments_enrichment_branch(self) -> None:
        fake_frame = FrameItem(
            bucket="bucket",
            blob_name="processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/video_id=L21_V001/shot_0000_middle_f000001.jpg",
            video_id="L21_V001",
            image_name="shot_0000_middle_f000001.jpg",
            frame_idx=1,
            frame_seconds=1.48,
            fps=25.0,
            shot_index=0,
            frame_type="middle",
        )

        fake_source = mock.Mock()
        fake_source.list_frames.return_value = [fake_frame]

        with mock.patch("src.manifest.GCSFrameSource", return_value=fake_source):
            rows = build_gcs_keyframe_manifest(
                bucket_name="bucket",
                prefixes=["processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1"],
                dataset_id="ai_challenge_2025",
                shot_segments_path="gs://bucket/manifests/shot_segments.csv",
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["frame_seconds"], 1.48)
        fake_source.list_frames.assert_called_once()
        self.assertEqual(fake_source.list_frames.call_args.kwargs["shot_segments_path"], "gs://bucket/manifests/shot_segments.csv")

    def test_shot_index_parsing_from_shot_segments_row(self) -> None:
        self.assertEqual(_shot_index_from_row({"shot_id_local": "7"}), 7)
        self.assertEqual(_shot_index_from_row({"shot_id": "L21_V001_S0008"}), 8)
        self.assertEqual(_shot_index_from_row({"shot_id": "shot_0012"}), 12)
        self.assertIsNone(_shot_index_from_row({"shot_id": ""}))

    def test_shot_metadata_loader_accepts_video_path(self) -> None:
        fake = object.__new__(GCSFrameSource)
        csv_text = (
            "video_name,video_path,shot_id,frame_idx,frame_sec,image_path\n"
            "L21_V001.mp4,/tmp/L21_V001.mp4,0,1,1.48,/tmp/frames/L21_V001/shot_0000_middle_f000001.jpg\n"
        )
        with mock.patch.object(GCSFrameSource, "_read_text_path", return_value=csv_text):
            rows = GCSFrameSource._load_shot_metadata(fake, "gs://bucket/shot_segments.csv")
        self.assertIn(("L21_V001", "shot_0000_middle_f000001.jpg"), rows)

    def test_asr_output_path_includes_shard(self) -> None:
        uri = asr_part_uri("gs://bucket/features", "run-1", "asr", "raw_source-kaggle_dataset-ai", "worker-a", "attempt-1", 3)
        self.assertIn("shard_id=raw_source-kaggle_dataset-ai", uri)
        self.assertIn("worker_id=worker-a", uri)
        self.assertIn("attempt_id=attempt-1", uri)
        self.assertTrue(uri.endswith("part-00003.jsonl"))

    def test_role_planner_renders_notebook_commands(self) -> None:
        plan = build_notebook_run_plan(
            RolePlanOptions(
                run_id="run-1",
                batches="L21,L26",
                shard_root="gs://bucket/manifests/run_id=run-1/shards",
                num_shards=2,
                max_shards_per_role=1,
            )
        )
        self.assertEqual(plan["batches"], ["L21"])
        self.assertEqual(plan["discovery"]["frames_per_shard"], 128)
        self.assertEqual(len(plan["shards"]), 2)
        self.assertTrue(any(role["role_id"] == "kaggle_l21_primary_visual" for role in plan["machine_roles"]))
        self.assertTrue(any("extract_gcs_asr.py" in role["command_template"] for role in plan["machine_roles"]))
        asr_role = next(role for role in plan["machine_roles"] if role["stage"] == "asr")
        self.assertEqual(asr_role["shards_to_run"], [])
        self.assertIn("raw/source=kaggle", asr_role["raw_video_prefix"])
        self.assertTrue(any(item["name"] == "text_metadata_to_supabase_elasticsearch_text_milvus" for item in plan["imports"]))

    def test_role_planner_expands_asr_and_labels_imports_for_multi_batch(self) -> None:
        plan = build_notebook_run_plan(
            RolePlanOptions(
                run_id="run-all",
                batches="L21,L22,L26,L30",
                shard_root="gs://bucket/manifests/run_id=run-all/shards",
                num_shards=2,
                max_shards_per_role=1,
            )
        )
        self.assertEqual(plan["batches"], ["L21", "L22", "L30"])
        asr_roles = [role for role in plan["machine_roles"] if role["stage"] == "asr"]
        primary_role = next(role for role in plan["machine_roles"] if role["role_id"] == "kaggle_l21_primary_visual")
        self.assertEqual(primary_role["batch_scope"], ["L21", "L22", "L30"])
        self.assertEqual([role["role_id"] for role in asr_roles], ["colab_l21_asr", "colab_l22_asr", "colab_l30_asr"])
        self.assertEqual([role["batch_scope"] for role in asr_roles], [["L21"], ["L22"], ["L30"]])
        self.assertTrue(all(f"batch={role['batch']}" in role["raw_video_prefix"] for role in asr_roles))
        self.assertTrue(all("ai_challenge_2025_mvp_l21_l22_l30" in item["command"] for item in plan["imports"]))

    def test_notebook_role_runner_renders_one_shard_command(self) -> None:
        report = render_or_run_notebook_role(
            NotebookRoleRunOptions(
                role_id="kaggle_l21_primary_visual",
                run_id="run-1",
                shard_root="gs://bucket/manifests/run_id=run-1/shards",
                num_shards=2,
                shard_index=1,
            )
        )
        self.assertFalse(report["execute"])
        self.assertEqual(len(report["commands"]), 1)
        self.assertIn("shard-00001.jsonl", report["commands"][0])
        self.assertIn("--profile visual_primary_pe_core", report["doctor_command"])
        self.assertIn("--features embedding", report["doctor_command"])

    def test_notebook_role_runner_renders_expanded_asr_batch(self) -> None:
        report = render_or_run_notebook_role(
            NotebookRoleRunOptions(
                role_id="colab_l22_asr",
                run_id="run-all",
                batches="L21,L22,L26",
                shard_root="gs://bucket/manifests/run_id=run-all/shards",
                num_shards=2,
            )
        )
        self.assertEqual(len(report["commands"]), 1)
        self.assertIn("batch=L22", report["commands"][0])
        self.assertIn("--features asr", report["doctor_command"])
        self.assertIn("--profile smoke", report["doctor_command"])

    def test_notebook_cells_render_copy_ready_kaggle_role(self) -> None:
        report = render_notebook_cells(
            NotebookCellsOptions(
                role_id="kaggle_l21_primary_visual",
                run_id="run-1",
                shard_root="gs://bucket/manifests/run_id=run-1/shards",
                num_shards=1,
            )
        )
        titles = [cell["title"] for cell in report["cells"]]
        markdown = report["markdown"]
        self.assertEqual(report["runtime"], "kaggle")
        self.assertEqual(titles, ["Role", "Install", "Environment", "Doctor", "Run role", "Resume"])
        self.assertIn("cd /kaggle/working/Multimodal-Retrieval", markdown)
        self.assertIn("GCS_SERVICE_ACCOUNT_JSON", markdown)
        self.assertIn("doctor --runtime kaggle --features embedding", markdown)
        self.assertIn("run-feature-shard", markdown)
        self.assertIn("shard-00000.jsonl", markdown)

    def test_notebook_cells_render_colab_asr_requirements(self) -> None:
        report = render_notebook_cells(
            NotebookCellsOptions(
                role_id="colab_l22_asr",
                run_id="run-all",
                batches="L21,L22,L26",
                shard_root="gs://bucket/manifests/run_id=run-all/shards",
                num_shards=1,
            )
        )
        markdown = report["markdown"]
        self.assertEqual(report["runtime"], "colab")
        self.assertEqual(report["stage"], "asr")
        self.assertIn("cd /content/Multimodal-Retrieval", markdown)
        self.assertIn("requirements-asr.txt", markdown)
        self.assertIn("batch=L22", markdown)
        self.assertIn("raw-video prefix", markdown)

    def test_notebook_kit_writes_role_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = render_notebook_kit(
                NotebookKitOptions(
                    run_id="kit-run",
                    output_dir=str(Path(tmp) / "kit"),
                    batches="L21,L22,L26",
                    shard_root="gs://bucket/manifests/run_id=kit-run/shards",
                    num_shards=1,
                    stage_filter="visual_primary,asr",
                )
            )
            output_dir = Path(report["output_dir"])
            role_ids = {item["role_id"] for item in report["roles"]}
            self.assertTrue((output_dir / "README.md").exists())
            self.assertTrue((output_dir / "plan.json").exists())
            self.assertIn("kaggle_l21_primary_visual", role_ids)
            self.assertIn("colab_l21_asr", role_ids)
            self.assertIn("colab_l22_asr", role_ids)
            self.assertNotIn("colab_l26_asr", role_ids)
            self.assertIn("Run role", (output_dir / "kaggle_l21_primary_visual.md").read_text(encoding="utf-8"))
            self.assertIn("Import commands", (output_dir / "README.md").read_text(encoding="utf-8"))

    def test_notebook_role_runner_stops_when_doctor_fails(self) -> None:
        with mock.patch(
            "src.notebook_role_runner.subprocess.run",
            return_value=subprocess.CompletedProcess(args="doctor", returncode=2, stdout='{"ok": false}', stderr=""),
        ) as run_mock:
            report = render_or_run_notebook_role(
                NotebookRoleRunOptions(
                    role_id="kaggle_l21_primary_visual",
                    run_id="run-1",
                    shard_root="gs://bucket/manifests/run_id=run-1/shards",
                    num_shards=1,
                    execute=True,
                    doctor_first=True,
                )
            )
        self.assertFalse(report["ok"])
        self.assertEqual(run_mock.call_count, 1)
        self.assertEqual(len(report["executed"]), 1)
        self.assertEqual(len(report["skipped_commands"]), 1)
        self.assertIn("run-feature-shard", report["skipped_commands"][0])

    def test_local_smoke_flow_runs_end_to_end(self) -> None:
        summary = run_local_smoke_flow(
            LocalSmokeFlowOptions(
                run_id="smoke-local-test",
                frame_count=2,
                frames_per_shard=1,
            )
        )
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["manifest"]["frames"], 2)
        self.assertTrue(summary["reconcile"]["ok"])
        self.assertEqual(summary["import_dry_run"]["records"], 2)
        self.assertEqual(summary["import_dry_run"]["asr_records"], 2)
        self.assertEqual(summary["import_dry_run"]["text_records"], 4)
        self.assertTrue(summary["doctors"]["kaggle"]["ok"])
        self.assertTrue(summary["doctors"]["colab_caption"]["ok"])
        self.assertTrue(summary["doctors"]["importer"]["ok"])
        self.assertTrue(summary["role_render"]["ok"])

    def test_local_smoke_flow_supports_multiple_batches(self) -> None:
        summary = run_local_smoke_flow(
            LocalSmokeFlowOptions(
                run_id="smoke-local-multi",
                batches="L21,L22",
                frame_count=1,
                frames_per_shard=1,
            )
        )
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["manifest"]["frames"], 2)
        self.assertEqual(summary["import_dry_run"]["records"], 2)
        self.assertEqual(summary["import_dry_run"]["asr_records"], 4)
        self.assertEqual(summary["import_dry_run"]["text_records"], 6)

    def test_processor_cli_doctor_exits_nonzero_when_not_ready(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROCESSORS_ROOT / "processor_cli.py"),
                "--profile",
                "smoke",
                "doctor",
                "--runtime",
                "colab",
                "--features",
                "not_a_real_feature",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('"ok": false', completed.stdout)
        self.assertIn("not_a_real_feature", completed.stdout)

    def test_vlm_captioner_receives_shot_context(self) -> None:
        config = load_pipeline_config(str(DEFAULT_PROCESSOR_CONFIG), profile="shot_context_caption_qwen_vl")
        extractor = FrameFeatureExtractor(config.raw.get("models", {}))
        self.assertIsInstance(extractor.captioner, OpenAiCompatibleVlmCaptioner)
        prompt = extractor.captioner._prompt({"video_id": "L21_V001", "frame_idx": 123, "frame_seconds": 4.92})
        self.assertIn("video_id=L21_V001", prompt)
        self.assertIn("frame_idx=123", prompt)

    def test_unresolved_vlm_endpoint_env_collapses_to_empty(self) -> None:
        self.assertEqual(_env_expanded("${THIS_PROCESSOR_ENV_SHOULD_NOT_EXIST}"), "")

    def test_processor_doctor_reports_runtime_readiness(self) -> None:
        settings = ProcessorSettings(
            database_url="postgresql+psycopg://user:pass@host/db",
            gcs_bucket="bucket",
            gcs_credentials_file="",
            gcs_public_url="",
            milvus_uri="https://milvus.example",
            milvus_token="token",
            elasticsearch_url="https://es.example",
        )
        report = run_processor_doctor(
            settings,
            DoctorOptions(runtime="kaggle", profile="smoke", config_path=str(DEFAULT_PROCESSOR_CONFIG)),
        )
        self.assertEqual(report["runtime"], "kaggle")
        self.assertEqual(report["features"], ["embedding", "objects"])
        self.assertTrue(report["feature_config"]["ok"])
        self.assertTrue(any(item["name"] == "gcs_bucket" and item["ok"] for item in report["env"]))
        self.assertTrue(report["profile_config"]["ok"])

    def test_processor_doctor_can_probe_gcs(self) -> None:
        settings = ProcessorSettings(
            database_url="postgresql+psycopg://user:pass@host/db",
            gcs_bucket="bucket",
            gcs_credentials_file="",
            gcs_public_url="",
            milvus_uri="https://milvus.example",
            milvus_token="token",
            elasticsearch_url="https://es.example",
        )
        fake_client = mock.MagicMock()
        fake_client.list_blobs.return_value = [object()]
        with mock.patch("google.cloud.storage.Client", return_value=fake_client):
            report = run_processor_doctor(
                settings,
                DoctorOptions(
                    runtime="kaggle",
                    profile="smoke",
                    config_path=str(DEFAULT_PROCESSOR_CONFIG),
                    check_gcs=True,
                    bucket="bucket",
                    gcs_prefix="processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1",
                ),
            )
        self.assertTrue(report["gcs"]["checked"])
        self.assertTrue(report["gcs"]["ok"])
        self.assertEqual(report["gcs"]["sample_count"], 1)
        fake_client.list_blobs.assert_called_once()

    def test_importer_doctor_rejects_sqlite_fallback(self) -> None:
        settings = ProcessorSettings(
            database_url="sqlite:///./data/dev.db",
            gcs_bucket="bucket",
            gcs_credentials_file="",
            gcs_public_url="",
            milvus_uri="http://localhost:19530",
            milvus_token="",
            elasticsearch_url="http://localhost:9200",
        )
        report = run_processor_doctor(
            settings,
            DoctorOptions(runtime="importer", profile="full_text_features", config_path=str(DEFAULT_PROCESSOR_CONFIG)),
        )
        db_row = next(item for item in report["env"] if item["name"] == "database_url")
        milvus_row = next(item for item in report["env"] if item["name"] == "milvus_uri")
        elasticsearch_row = next(item for item in report["env"] if item["name"] == "elasticsearch_url")
        self.assertFalse(db_row["ok"])
        self.assertTrue(milvus_row["ok"])
        self.assertTrue(elasticsearch_row["ok"])
        self.assertIn("local Milvus", milvus_row["warning"])
        self.assertIn("local Elasticsearch", elasticsearch_row["warning"])
        self.assertFalse(report["ok"])

    def test_colab_doctor_rejects_python_313_plus_for_ocr_stack(self) -> None:
        settings = ProcessorSettings(
            database_url="postgresql+psycopg://user:pass@host/db",
            gcs_bucket="bucket",
            gcs_credentials_file="",
            gcs_public_url="",
            milvus_uri="http://localhost:19530",
            milvus_token="",
            elasticsearch_url="http://localhost:9200",
        )
        with mock.patch("src.processor_doctor.sys.version_info", (3, 13, 0)):
            report = run_processor_doctor(
                settings,
                DoctorOptions(runtime="colab", profile="shot_context_caption_qwen_vl", config_path=str(DEFAULT_PROCESSOR_CONFIG)),
            )
        self.assertFalse(report["python_compatibility"]["ok"])
        self.assertFalse(report["ok"])

    def test_colab_doctor_caption_feature_does_not_check_ocr_stack(self) -> None:
        settings = ProcessorSettings(
            database_url="postgresql+psycopg://user:pass@host/db",
            gcs_bucket="bucket",
            gcs_credentials_file="",
            gcs_public_url="",
            milvus_uri="http://localhost:19530",
            milvus_token="",
            elasticsearch_url="http://localhost:9200",
        )
        with mock.patch("src.processor_doctor.sys.version_info", (3, 14, 0)):
            report = run_processor_doctor(
                settings,
                DoctorOptions(
                    runtime="colab",
                    profile="shot_context_caption_qwen_vl",
                    features="caption",
                    config_path=str(DEFAULT_PROCESSOR_CONFIG),
                ),
            )
        package_names = {item["module"] for item in report["packages"]}
        self.assertEqual(report["features"], ["caption"])
        self.assertTrue(report["python_compatibility"]["ok"])
        self.assertNotIn("easyocr", package_names)
        self.assertNotIn("faster_whisper", package_names)

    def test_reconcile_run_checks_artifacts_and_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rows = [
                {
                    "dataset_id": "ai_challenge_2025",
                    "batch": "L21",
                    "video_id": "L21_V001",
                    "keyframe_id": "L21_V001_F000001",
                    "frame_idx": 1,
                    "frame_seconds": 0.0,
                    "frame_type": "middle",
                    "gcs_uri": "gs://bucket/path.jpg",
                    "bucket": "bucket",
                    "blob_name": "path.jpg",
                    "image_name": "path.jpg",
                }
            ]
            manifest = write_manifest_and_shards(rows, output_root=str(tmp_path / "manifest"), run_id="run-1", frames_per_shard=1)
            checkpoint = CheckpointStore(root_uri=str(tmp_path / "ckpt"), run_id="run-1", lease_ttl_seconds=60)
            artifact_root = tmp_path / "features"
            for stage in ["visual_primary", "visual_secondary", "objects", "ocr", "caption"]:
                checkpoint.try_claim(stage, "shard-00000", "worker-a")
                checkpoint.mark_complete(stage, "shard-00000", {"worker_id": "worker-a", "processed": 1})
                part = artifact_root / "run_id=run-1" / f"stage={stage}" / "shard_id=shard-00000" / "part-00000.jsonl"
                part.parent.mkdir(parents=True, exist_ok=True)
                part.write_text(json.dumps({"schema_version": "aic.feature_artifact.v1", "frame": rows[0]}) + "\n", encoding="utf-8")
            asr_part = artifact_root / "run_id=run-1" / "stage=asr" / "shard_id=raw" / "part-00000.jsonl"
            asr_part.parent.mkdir(parents=True, exist_ok=True)
            asr_part.write_text(
                json.dumps({"schema_version": "aic.asr_artifact.v1", "segments": [{"text": "xin chao"}]}) + "\n",
                encoding="utf-8",
            )
            report = reconcile_feature_run(
                RunReconcileOptions(
                    run_id="run-1",
                    manifest_summary_uri=manifest["summary_uri"],
                    feature_output_prefix=str(artifact_root),
                    checkpoint_root=str(tmp_path / "ckpt"),
                )
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["manifest_frames"], 1)
            self.assertTrue(all(stage["artifact_files"] == 1 for stage in report["stages"]))

    def test_importer_dry_run_handles_asr_and_frame_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            frame_artifact = tmp_path / "features" / "dataset=ai_challenge_2025" / "frame_profile=autoshot_v1" / "feature_profile=mvp_v1" / "run_id=run-1" / "stage=objects" / "shard_id=shard-00000" / "part-00000.jsonl"
            asr_artifact = tmp_path / "features" / "dataset=ai_challenge_2025" / "frame_profile=autoshot_v1" / "feature_profile=mvp_v1" / "run_id=run-1" / "stage=asr" / "shard_id=raw_source-kaggle_dataset-ai_challenge_2025_batch-L21" / "part-00000.jsonl"
            frame_rows = [
                {
                    "schema_version": "aic.feature_artifact.v1",
                    "run_id": "run-1",
                    "stage": "objects",
                    "frame": {
                        "bucket": "bucket",
                        "blob_name": "path.jpg",
                        "gcs_uri": "gs://bucket/path.jpg",
                        "video_id": "L21_V001",
                        "keyframe_id": "L21_V001_F000001",
                        "shot_id": "L21_V001_S0000",
                        "shot_index": 0,
                        "image_name": "path.jpg",
                        "frame_idx": 1,
                        "frame_seconds": 0.0,
                        "frame_type": "middle",
                    },
                    "annotation": {
                        "caption": "xin chao",
                        "texts": ["FANA"],
                        "objects": ["person"],
                        "object_counts": {"person": 1},
                        "detections": [],
                    },
                    "embedding": [0.1, 0.2, 0.3],
                    "status": "ok",
                }
            ]
            asr_rows = [
                {
                    "schema_version": "aic.asr_artifact.v1",
                    "run_id": "run-1",
                    "stage": "asr",
                    "video_id": "L21_V001",
                    "source": {"gcs_uri": "gs://bucket/video.mp4", "video_id": "L21_V001"},
                    "segments": [
                        {
                            "segment_id": "L21_V001_ASR_000001",
                            "start_seconds": 1.0,
                            "end_seconds": 2.0,
                            "text": "xin chao viet nam",
                            "language": "vi",
                            "confidence": 0.9,
                        }
                    ],
                    "model": {"name": "faster-whisper-small", "language": "vi"},
                }
            ]
            frame_artifact.parent.mkdir(parents=True, exist_ok=True)
            asr_artifact.parent.mkdir(parents=True, exist_ok=True)
            frame_artifact.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in frame_rows) + "\n", encoding="utf-8")
            asr_artifact.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in asr_rows) + "\n", encoding="utf-8")

            config = SinkConfig(
                database_url="sqlite:///./data/dev.db",
                milvus_uri="http://localhost:19530",
                milvus_token="",
                elasticsearch_url="http://localhost:9200",
                dataset_code="ai_challenge_2025_mvp_l21",
                dataset_name="ai-challenge-2025-mvp-l21",
                dataset_version="mvp_v1",
                dataset_root_uri="gs://bucket/processed/keyframes",
                gcs_public_url="",
                milvus_collection="keyframe_embeddings",
                elasticsearch_index="keyframe_annotations",
                model_version="mvp_v1",
                write_pg=False,
                write_milvus=False,
                write_elasticsearch=False,
                dry_run=True,
            )
            summary = import_feature_artifacts(
                config,
                ArtifactImportOptions(
                    artifact_uris=[str(tmp_path)],
                    write_pg=False,
                    write_milvus=False,
                    write_elasticsearch=False,
                    dry_run=True,
                ),
            )
            self.assertEqual(summary["records"], 1)
            self.assertEqual(summary["asr_records"], 1)
            self.assertEqual(summary["text_records"], 2)

    def test_run_feature_shard_dry_run_local_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shard = tmp_path / "shard-00000.jsonl"
            shard.write_text(
                json.dumps(
                    {
                        "dataset_id": "ai_challenge_2025",
                        "batch": "L21",
                        "video_id": "L21_V001",
                        "keyframe_id": "L21_V001_F000001",
                        "frame_idx": 1,
                        "frame_seconds": 0.0,
                        "frame_type": "middle",
                        "gcs_uri": "gs://bucket/path.jpg",
                        "bucket": "bucket",
                        "blob_name": "path.jpg",
                        "image_name": "path.jpg",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_pipeline_config(str(DEFAULT_PROCESSOR_CONFIG), profile="smoke")
            summary = run_feature_shard(
                config,
                ShardRunOptions(
                    stage="smoke",
                    shard_uri=str(shard),
                    output_prefix=str(tmp_path / "out"),
                    checkpoint_root=str(tmp_path / "ckpt"),
                    run_id="run-1",
                    dry_run=True,
                ),
            )
            self.assertEqual(summary["status"], "dry_run")
            self.assertEqual(summary["rows"], 1)

    def test_run_feature_shard_resumes_from_checkpoint(self) -> None:
        class FakeSource:
            def download_to(self, item, path) -> None:  # noqa: ANN001 - small inline fake for resume smoke.
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fake", encoding="utf-8")

        class FakeExtractor:
            def __init__(self, _models: dict[str, object]) -> None:
                pass

            def warmup(self) -> None:
                return None

            def process_batch(self, local_paths, contexts=None):  # noqa: ANN001 - inline fake for resume smoke.
                annotations = [
                    {
                        "caption": f"caption-{index}",
                        "texts": [f"text-{index}"],
                        "objects": ["person"],
                        "object_counts": {"person": 1},
                        "detections": [],
                    }
                    for index, _ in enumerate(local_paths)
                ]
                embeddings = np.asarray([[1.0, 2.0, 3.0] for _ in local_paths], dtype="float32")
                return annotations, embeddings, {"load": 0.0, "infer": 0.0}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shard = tmp_path / "shard-00000.jsonl"
            shard.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "dataset_id": "ai_challenge_2025",
                            "batch": "L21",
                            "video_id": "L21_V001",
                            "keyframe_id": f"L21_V001_F{index:06d}",
                            "frame_idx": index,
                            "frame_seconds": float(index - 1),
                            "frame_type": "middle",
                            "gcs_uri": f"gs://bucket/path-{index}.jpg",
                            "bucket": "bucket",
                            "blob_name": f"path-{index}.jpg",
                            "image_name": f"path-{index}.jpg",
                        },
                        ensure_ascii=False,
                    )
                    for index in (1, 2)
                )
                + "\n",
                encoding="utf-8",
            )
            checkpoint_root = tmp_path / "ckpt"
            output_prefix = tmp_path / "out"
            checkpoint = CheckpointStore(root_uri=str(checkpoint_root), run_id="run-1", lease_ttl_seconds=60)
            self.assertTrue(checkpoint.try_claim("objects", "shard-00000", "worker-a").claimed)
            existing_part = output_prefix / "run_id=run-1" / "stage=objects" / "shard_id=shard-00000" / "worker_id=worker-a" / "attempt_id=attempt-0" / "part-00000.jsonl"
            existing_part.parent.mkdir(parents=True, exist_ok=True)
            existing_part.write_text(
                json.dumps(
                    {
                        "schema_version": "aic.feature_artifact.v1",
                        "run_id": "run-1",
                        "stage": "objects",
                        "shard_id": "shard-00000",
                        "worker_id": "worker-a",
                        "attempt_id": "attempt-0",
                        "part_index": 0,
                        "record_index": 0,
                        "created_at": "2026-07-23T00:00:00Z",
                        "frame": {
                            "dataset_id": "ai_challenge_2025",
                            "batch": "L21",
                            "video_id": "L21_V001",
                            "keyframe_id": "L21_V001_F000001",
                            "frame_idx": 1,
                            "frame_seconds": 0.0,
                            "frame_type": "middle",
                            "gcs_uri": "gs://bucket/path-1.jpg",
                            "bucket": "bucket",
                            "blob_name": "path-1.jpg",
                            "image_name": "path-1.jpg",
                        },
                        "annotation": {
                            "caption": "caption-0",
                            "texts": ["text-0"],
                            "objects": ["person"],
                            "object_counts": {"person": 1},
                            "detections": [],
                        },
                        "embedding": [1.0, 2.0, 3.0],
                        "timings_seconds": {"load": 0.0, "infer": 0.0},
                        "status": "ok",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            checkpoint.save(
                "objects",
                "shard-00000",
                {
                    "worker_id": "worker-a",
                    "attempt_id": "attempt-0",
                    "next_index": 1,
                    "next_part_index": 1,
                    "processed": 1,
                    "failed": 0,
                    "rows": 2,
                    "output_parts": [str(existing_part)],
                },
            )
            config = load_pipeline_config(str(DEFAULT_PROCESSOR_CONFIG), profile="smoke")
            with mock.patch("src.shard_runner._source_for_rows", return_value=FakeSource()), mock.patch(
                "src.shard_runner.FrameFeatureExtractor", FakeExtractor
            ):
                summary = run_feature_shard(
                    config,
                    ShardRunOptions(
                        stage="objects",
                        shard_uri=str(shard),
                        output_prefix=str(output_prefix),
                        checkpoint_root=str(checkpoint_root),
                        run_id="run-1",
                        worker_id="worker-a",
                        batch_size=1,
                    ),
                )
                rerun = run_feature_shard(
                    config,
                    ShardRunOptions(
                        stage="objects",
                        shard_uri=str(shard),
                        output_prefix=str(output_prefix),
                        checkpoint_root=str(checkpoint_root),
                        run_id="run-1",
                        worker_id="worker-a",
                        batch_size=1,
                    ),
                )
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["processed"], 2)
            self.assertEqual(summary["next_index"], 2)
            self.assertEqual(len(summary["output_parts"]), 2)
            self.assertEqual(rerun["status"], "already_complete")


if __name__ == "__main__":
    unittest.main()
