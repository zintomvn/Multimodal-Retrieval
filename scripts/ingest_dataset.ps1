param(
  [string]$ApiBase = "http://localhost:8000",
  [string]$ManifestPath = "",
  [string]$Mode = "demo"
)

if ($ManifestPath -eq "") {
  throw "Set -ManifestPath to a real dataset manifest path."
}

$body = @{
  manifest_path = $ManifestPath
  mode = $Mode
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "$ApiBase/api/ingest/jobs" -ContentType "application/json" -Body $body
