$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$env:HF_HOME = Join-Path $repoRoot '.cache/huggingface'
$env:HF_HUB_DISABLE_XET = '1'
$env:OMP_NUM_THREADS = '4'
$env:MKL_NUM_THREADS = '4'
& "$repoRoot/.venv/Scripts/python.exe" apps/backend/scripts/serve_openclip_embeddings.py --host 0.0.0.0 --port 8003 --device cpu --model ViT-SO400M-16-SigLIP2-384 --pretrained webli --model-id ViT-SO400M-16-SigLIP2-384-webli
exit $LASTEXITCODE
