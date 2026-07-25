$ErrorActionPreference = 'Stop'
$devDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composeFile = Join-Path $devDir 'compose.yaml'
docker compose -f $composeFile exec raspicat-dev /ros_entrypoint.sh bash
if ($LASTEXITCODE -ne 0) { throw 'Could not enter the development container.' }
