"""Parameters for the Kaggle AutoShot -> GCS keyframe notebook.

Edit this file first when running locally, or edit the generated copy at
``/kaggle/working/video_to_frame_gcs_params.py`` when running on Kaggle.
Do not put service-account JSON content in this file; use Kaggle Secrets or
environment variables instead.
"""

# Kaggle input.
INPUT_ROOT = ""  # Empty = auto-detect /kaggle/input/ai-challenge-2025.
EXPECTED_BATCHES = [
    "L21",
    "L22",
    "L23",
    "L24",
    "L25",
    "L26",
    "L27",
    "L28",
    "L29",
    "L30",
]
BATCH_REGEX = r"(?i)(?:^|[/_\\-])(?:videos?_)?([A-Z]\d{2})(?:[_/\\-]|$)"
VIDEO_EXTENSIONS = [".mp4", ".avi", ".mov", ".mkv", ".webm"]

# Dataset/model metadata used in GCS paths and CSV rows.
DATASET_ID = "ai_challenge_2025"
SOURCE_VERSION = "kaggle_current"
PROFILE_VERSION = "autoshot_v1"
RAW_PREFIX = "raw/source=kaggle"
RAW_RELATIVE_PATH_PREFIX = "ai-challenge-2025"  # Set "" if raw GCS objects omit this folder.
KEYFRAMES_PREFIX = "processed/keyframes"
MANIFESTS_PREFIX = "processed/keyframes_manifests"
RAW_VIDEO_URI_MODE = "gcs_expected"  # "gcs_expected" or "kaggle".

# GCS credentials. Empty values are resolved from env vars or Kaggle Secrets.
GCS_BUCKET = ""
GCS_BUCKET_SECRET_NAME = "GCS_BUCKET"
GCS_CREDENTIALS_FILE = ""
GCS_CREDENTIALS_JSON_SECRET_NAME = "GCS_CREDENTIALS_JSON"

# AutoShot runtime.
AUTOSHOT_REPO_DIR = "/kaggle/working/AutoShot"
AUTOSHOT_REPO_URL = "https://github.com/wentaozhu/AutoShot.git"
AUTO_CLONE_AUTOSHOT = True
CHECKPOINT_PATH = "/kaggle/input/models/khngxuninh/autoshot/pytorch/default/1/ckpt_0_200_0.pth"
DEVICE = "auto"  # "auto", "cpu", "cuda", or "cuda:0".
THRESHOLD = 0.296
MIN_SHOT_LEN = 5
JPEG_QUALITY = 95

# Local Kaggle output.
RUN_DIR = "/kaggle/working/frame_extraction_runs"
SCRATCH_DIR = "/kaggle/working/autoshot_scratch"
CLEANUP_LOCAL_IMAGES_AFTER_UPLOAD = True

# Upload behavior.
UPLOAD_TO_GCS = True
UPLOAD_RUN_ARTIFACTS = True
SKIP_EXISTING = True
OVERWRITE = False

# Progress/logging.
USE_TQDM = True
VERBOSE = False
LOG_EVERY_VIDEO = True

# Notebook cells.
DRY_RUN_BATCHES = "all"
DRY_RUN_MAX_VIDEOS = 20
DEMO_BATCHES = "L21"
DEMO_MAX_VIDEOS = 2
FULL_BATCHES = "all"
FULL_MAX_VIDEOS = None

# Safety guard for the final full-run notebook cell.
CONFIRM_FULL_RUN = ""  # Set to "RUN_FULL_DATASET" before executing full run.
