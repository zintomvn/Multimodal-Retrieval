param(
  [string]$ApiBase = "http://localhost:8000",
  [string]$DatasetId = "",
  [string]$Mode = "demo"
)

$body = @{
  dataset_id = if ($DatasetId -eq "") { $null } else { $DatasetId }
  mode = $Mode
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "$ApiBase/api/ingest/jobs" -ContentType "application/json" -Body $body
