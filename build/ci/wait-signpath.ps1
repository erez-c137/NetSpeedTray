<#
.SYNOPSIS
    Waits for ONE SignPath signing request, then downloads and expands the signed artifact.

.DESCRIPTION
    Used by release.yml once per request (portable first, then the installer built from the
    portable's signed exe). Polls the SignPath status API at a fixed 20 s - the official action's
    own wait backs off to ~20 min between polls, so a late approval sat unnoticed (2.1.6 timed
    out). The approval link goes to the run's step summary so approving is one click away.

    Needs SIGNPATH_API_TOKEN and SIGNPATH_ORG in the environment.
#>
param(
    [Parameter(Mandatory)] [string] $RequestId,
    [Parameter(Mandatory)] [string] $Label,
    [Parameter(Mandatory)] [string] $OutDir,
    [string] $WebUrl = "",
    [int] $TimeoutMinutes = 30,
    [int] $PollSeconds = 20,
    [string] $ApiBase = "https://app.signpath.io/Api/v1"
)

$ErrorActionPreference = "Stop"

if (-not $env:SIGNPATH_API_TOKEN) { throw "SIGNPATH_API_TOKEN is not set" }
if (-not $env:SIGNPATH_ORG) { throw "SIGNPATH_ORG is not set" }

if ($env:GITHUB_STEP_SUMMARY) {
    "## Signing request - $Label`n`nApprove: $WebUrl`n" >> $env:GITHUB_STEP_SUMMARY
}
Write-Host "Waiting for SignPath request $RequestId ($Label): $WebUrl"

$api = "$ApiBase/$env:SIGNPATH_ORG/SigningRequests"
$headers = @{ Authorization = "Bearer $env:SIGNPATH_API_TOKEN" }
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)

while ($true) {
    $st = $null
    try { $st = Invoke-RestMethod -Uri "$api/$RequestId/Status" -Headers $headers }
    catch { Write-Host "$Label : status call failed ($($_.Exception.Message)), retrying" }

    if ($st) {
        if ($st.isFinalStatus) {
            if ($st.status -ne "Completed") { throw "$Label signing request $RequestId ended as $($st.status)" }
            break
        }
        Write-Host "$Label : $($st.status)"
    }
    if ((Get-Date) -gt $deadline) { throw "Timed out after $TimeoutMinutes min waiting for $Label ($WebUrl)" }
    Start-Sleep -Seconds $PollSeconds
}

$zip = "$OutDir.zip"
Invoke-WebRequest -Uri "$api/$RequestId/SignedArtifact" -Headers $headers -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $OutDir -Force
Remove-Item $zip
Write-Host "$Label : signed and downloaded"
Get-ChildItem $OutDir -Recurse -File | ForEach-Object { Write-Host "  $($_.FullName)" }
