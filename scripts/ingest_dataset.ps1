param(
  [string]$ApiBase = "http://localhost:8000",
  [string]$ManifestPath = "configs/dataset_manifest.example.yaml",
  [string]$Mode = "mock"
)

$body = @{
  manifest_path = $ManifestPath
  mode = $Mode
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "$ApiBase/api/ingest/jobs" -ContentType "application/json" -Body $body
