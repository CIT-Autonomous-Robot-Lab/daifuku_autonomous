param(
    [string]$PodmanConnection = 'podman-hyperv-root',
    [string]$PodmanPipe = 'podman-hyperv'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

Set-DockerToPodman -Connection $PodmanConnection -Pipe $PodmanPipe

docker compose -f $RaspicatComposeFile exec raspicat-dev /ros_entrypoint.sh bash
if ($LASTEXITCODE -ne 0) { throw 'Could not enter the development container.' }
