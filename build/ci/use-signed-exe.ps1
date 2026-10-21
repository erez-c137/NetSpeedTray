<#
.SYNOPSIS
    Copies the SignPath-signed NetSpeedTray.exe from the signed portable zip into dist\NetSpeedTray,
    so the installer compiled next packs a signed exe.

.DESCRIPTION
    Only the exe is taken. Before copying it checks that the signed zip's payload is the same file
    set as dist\NetSpeedTray (portable.marker is the zip's only extra), that the signed exe carries
    the same version resource, and that its Authenticode signature is Valid. Afterwards it checks
    the copied exe again. Any mismatch fails the release before an installer is built.
#>
param(
    [Parameter(Mandatory)] [string] $SignedPortableDir,
    [string] $RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

$payload = Join-Path $RootDir "dist\NetSpeedTray"
$target = Join-Path $payload "NetSpeedTray.exe"
if (-not (Test-Path $target)) { throw "Missing $target - was build.bat run with NST_SKIP_INSTALLER=1?" }

$zips = @(Get-ChildItem $SignedPortableDir -Recurse -Filter "NetSpeedTray-Portable-*.zip")
if ($zips.Count -ne 1) { throw "Expected one signed portable zip in $SignedPortableDir, found $($zips.Count)" }

# Extract under dist\ (not %TEMP%, which can be an 8.3 short path) so both trees compare by the same prefix.
$extract = Join-Path $RootDir "dist\_signed-portable-extract"
Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $zips[0].FullName -DestinationPath $extract
try {
    $exes = @(Get-ChildItem $extract -Recurse -Filter "NetSpeedTray.exe")
    if ($exes.Count -ne 1) { throw "Expected one NetSpeedTray.exe in the signed zip, found $($exes.Count)" }
    $signedExe = $exes[0].FullName
    $zipRoot = $exes[0].DirectoryName

    # Same file set as the installer payload, apart from the marker: the premise of signing once.
    $rel = { param($base) Get-ChildItem $base -Recurse -File -Force | ForEach-Object { $_.FullName.Substring($base.Length).TrimStart('\') } | Sort-Object }
    $zipFiles = @(& $rel $zipRoot | Where-Object { $_ -ne "portable.marker" })
    $distFiles = @(& $rel $payload)
    $diff = @(Compare-Object $zipFiles $distFiles)
    if ($diff.Count -gt 0) {
        $diff | ForEach-Object { Write-Host "  $($_.SideIndicator) $($_.InputObject)" }
        throw "The signed portable zip and dist\NetSpeedTray differ (<= zip only, => dist only)"
    }
    Write-Host "Signed zip payload matches dist\NetSpeedTray ($($distFiles.Count) files)."

    $signedVer = (Get-Item $signedExe).VersionInfo
    $distVer = (Get-Item $target).VersionInfo
    if ($signedVer.FileVersion -ne $distVer.FileVersion -or $signedVer.ProductVersion -ne $distVer.ProductVersion) {
        throw "Version mismatch: signed $($signedVer.ProductVersion)/$($signedVer.FileVersion) vs dist $($distVer.ProductVersion)/$($distVer.FileVersion)"
    }

    $sig = Get-AuthenticodeSignature $signedExe
    Write-Host "Signed exe from the zip: $($sig.Status) - $($sig.SignerCertificate.Subject)"
    if ($sig.Status -ne "Valid") { throw "NetSpeedTray.exe from the signed zip is not validly signed: $($sig.Status) ($($sig.StatusMessage))" }

    Copy-Item $signedExe $target -Force
}
finally {
    Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
}

$sig = Get-AuthenticodeSignature $target
if ($sig.Status -ne "Valid") { throw "$target is not validly signed after the copy: $($sig.Status)" }
Write-Host "dist\NetSpeedTray\NetSpeedTray.exe is now signed ($($sig.Status), $($sig.SignerCertificate.Subject))."
