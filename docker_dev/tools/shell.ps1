$ErrorActionPreference = 'Stop'
$composeFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'compose.yaml'
docker compose -f $composeFile exec raspicat-dev /ros_entrypoint.sh bash
if ($LASTEXITCODE -ne 0) { throw 'Could not enter the development container.' }
