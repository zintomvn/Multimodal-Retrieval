@echo off
cd /d "%~dp0apps\backend"

set ENV=local
set MOCK_MODE=true
set DATABASE_URL=sqlite:///./data/dev.db
set MODEL_REGISTRY_PATH=../../configs/model_registry.yaml
set RETRIEVAL_PROFILES_PATH=../../configs/retrieval_profiles.yaml
set DATA_ROOT=../../data

set LANGSMITH_TRACING=true
set LANGSMITH_API_KEY=lsv2_xxx
set LANGSMITH_PROJECT=multimodal-retrieval

"%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8010