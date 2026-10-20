<#
.SYNOPSIS
    Silent-installs a signed Setup.exe and fails unless the INSTALLED NetSpeedTray.exe is signed.

.DESCRIPTION
    CI only (release.yml) - it installs into Program Files, starts the app, and uninstalls.
    Never run this on a machine whose NetSpeedTray install or settings matter.

    A silent install launches the app (setup.iss [Run], silent-only entries), so every process
    running from the installed path is killed BY PATH before checking and before uninstalling - a
    PyInstaller app is parent + child, and a survivor would keep the files locked.
#>
param(
    [Parameter(Mandatory)] [string] $Setup,
    [int] $TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"

$appDir = Join-Path $env:ProgramFiles "NetSpeedTray"
$exe = Join-Path $appDir "NetSpeedTray.exe"
$uninstaller = Join-Path $appDir "unins000.exe"
$log = Join-Path ([IO.Path]::GetTempPath()) "nst-verify-install.log"

function Stop-InstalledApp {
    # Repeat until the path stays clear for a few seconds: the silent [Run] launch is nowait,
    # so the app can appear after Setup has already exited.
    $quiet = 0
    for ($i = 0; $i -lt 60 -and $quiet -lt 5; $i++) {
        $procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -ieq $exe })
        if ($procs.Count -gt 0) {
            $quiet = 0
            $procs | ForEach-Object { Write-Host "Killing PID $($_.Id) ($($_.Path))"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
        } else {
            $quiet++
        }
        Start-Sleep -Seconds 1
    }
}

function Invoke-AndWait([string] $Path, [string[]] $Arguments) {
    # Start-Process -Wait would also wait for the app Setup launches; WaitForExit waits for Setup only.
    $p = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru
    $null = $p.Handle  # keep the handle so ExitCode is readable after exit
    if (-not $p.WaitForExit($TimeoutSeconds * 1000)) { throw "$Path did not finish in $TimeoutSeconds s" }
    return $p.ExitCode
}

$setupSig = Get-AuthenticodeSignature $Setup
Write-Host "Setup.exe: $($setupSig.Status) - $($setupSig.SignerCertificate.Subject)"
if ($setupSig.Status -ne "Valid") { throw "The installer itself is not validly signed: $($setupSig.Status) ($($setupSig.StatusMessage))" }

if (Test-Path $exe) { throw "$exe already exists before the test install - refusing to verify a stale copy" }

try {
    $code = Invoke-AndWait $Setup @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$log`"")
    if ($code -ne 0) { throw "Silent install exited with code $code" }
    Stop-InstalledApp

    if (-not (Test-Path $exe)) { throw "Silent install reported success but $exe does not exist" }
    $sig = Get-AuthenticodeSignature $exe
    $ver = (Get-Item $exe).VersionInfo
    Write-Host "Installed exe: $exe"
    Write-Host "  Version: $($ver.ProductVersion) (file $($ver.FileVersion))"
    Write-Host "  Signature: $($sig.Status) - $($sig.SignerCertificate.Subject)"
    if (Test-Path $uninstaller) {
        # Informational: Inno generates the uninstaller at compile time and SignPath cannot reach it.
        Write-Host "  unins000.exe signature (expected unsigned): $((Get-AuthenticodeSignature $uninstaller).Status)"
    }
    if (Test-Path (Join-Path $appDir "portable.marker")) { throw "The installed copy contains portable.marker" }
    if ($sig.Status -ne "Valid") { throw "The INSTALLED exe is not validly signed: $($sig.Status) ($($sig.StatusMessage))" }
    if ($env:GITHUB_STEP_SUMMARY) {
        "## Installed exe`n`nProgram Files NetSpeedTray.exe $($ver.ProductVersion): **$($sig.Status)** ($($sig.SignerCertificate.Subject))`n" >> $env:GITHUB_STEP_SUMMARY
    }
}
catch {
    if (Test-Path $log) { Write-Host "--- Setup log (tail) ---"; Get-Content $log -Tail 60 | ForEach-Object { Write-Host $_ } }
    throw
}
finally {
    Stop-InstalledApp
    if (Test-Path $uninstaller) {
        try {
            $code = Invoke-AndWait $uninstaller @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
            Write-Host "Uninstaller exited with code $code"
            # unins000.exe hands off to a temp copy of itself, so wait for the files to actually go.
            for ($i = 0; $i -lt 60 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
            if (Test-Path $exe) { Write-Warning "$exe still present after uninstall" } else { Write-Host "Uninstalled." }
        }
        catch { Write-Warning "Uninstall failed: $($_.Exception.Message)" }
    }
}
