<#
.SYNOPSIS
    Compiles the Inno installer from dist\NetSpeedTray, the way build.bat's Stage 4 does.

.DESCRIPTION
    release.yml runs build.bat with NST_SKIP_INSTALLER=1, swaps the SignPath-signed
    NetSpeedTray.exe into dist\NetSpeedTray, then calls this. The version and the ISCC defines
    are derived exactly as build.bat derives them (same Python one-liner, same
    create_version_info.py --numeric, same /DAppVersion and /DAppVersionNumeric), and the
    installer lands where build.bat puts it: dist\NetSpeedTray-<version>\. Keep the two in sync.

    Returns the installer's full path (ISCC's own output goes to the host, not the pipeline).
#>
param(
    [string] $RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string] $Python = "",
    [string] $Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"

if (-not $Python) { $Python = Join-Path $RootDir ".venv\Scripts\python.exe" }
$buildDir = Join-Path $RootDir "build"
$payload = Join-Path $RootDir "dist\NetSpeedTray"

foreach ($p in @($Python, $Iscc, (Join-Path $buildDir "setup.iss"), (Join-Path $payload "NetSpeedTray.exe"))) {
    if (-not (Test-Path $p)) { throw "Missing: $p" }
}
# The marker belongs only in the portable zip; an installed copy carrying it takes the portable
# update path. build.bat's NST_SKIP_INSTALLER mode removes it after zipping.
if (Test-Path (Join-Path $payload "portable.marker")) { throw "portable.marker is still in $payload" }

$srcDir = Join-Path $RootDir "src"
$version = (& $Python -c "import sys, os; sys.path.append(os.path.join(r'$srcDir')); import netspeedtray; print(netspeedtray.__version__)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Could not extract version" }
$numeric = (& $Python (Join-Path $buildDir "create_version_info.py") $version --numeric).Trim()
if ($LASTEXITCODE -ne 0 -or -not $numeric) { throw "Could not derive numeric version" }
Write-Host "Version: $version (installer metadata: $numeric)"

& $Iscc "/DAppVersion=$version" "/DAppVersionNumeric=$numeric" (Join-Path $buildDir "setup.iss") | Out-Host
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }

$name = "NetSpeedTray-$version-x64-Setup.exe"
$built = Join-Path $buildDir "installer\$name"
if (-not (Test-Path $built)) { throw "Setup file not found after compilation: $built" }

$releaseDir = Join-Path $RootDir "dist\NetSpeedTray-$version"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
Move-Item $built (Join-Path $releaseDir $name) -Force
Remove-Item (Join-Path $buildDir "installer") -Recurse -Force -ErrorAction SilentlyContinue

Join-Path $releaseDir $name
