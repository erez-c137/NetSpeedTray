@echo off
echo Building NetSpeedTray...

rem Define version from MyAppVersion in installer.iss
set "VERSION=1.0.2-beta4"
set "OUTPUT_DIR=NetSpeedTray-%VERSION%"

rem Check for required files
if not exist "LICENSE" (
    echo ERROR: LICENSE file is missing
    exit /b 1
)
if not exist "README.md" (
    echo ERROR: README.md file is missing
    exit /b 1
)
if not exist "network-monitor.spec" (
    echo ERROR: network-monitor.spec file is missing
    exit /b 1
)
if not exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    echo ERROR: Inno Setup is not installed
    exit /b 1
)

rem Clean previous builds
echo Cleaning previous builds...
rmdir /s /q "dist" 2>nul
rmdir /s /q "build" 2>nul
rmdir /s /q "installer" 2>nul
rmdir /s /q "NetSpeedTray-Portable" 2>nul
rmdir /s /q "%OUTPUT_DIR%" 2>nul
del /f /q "NetSpeedTray-Portable.zip" 2>nul

rem Build executable
echo Building executable...
python -m PyInstaller network-monitor.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed
    exit /b 1
)

rem Create output directory
echo Creating output directory...
mkdir "%OUTPUT_DIR%" 2>nul

rem Create portable version
echo Creating portable version...
mkdir "%OUTPUT_DIR%\NetSpeedTray-Portable" 2>nul
copy dist\NetSpeedTray.exe "%OUTPUT_DIR%\NetSpeedTray-Portable\" || (
    echo ERROR: Failed to copy portable executable
    exit /b 1
)
copy README.md "%OUTPUT_DIR%\NetSpeedTray-Portable\" || (
    echo ERROR: Failed to copy README.md
    exit /b 1
)
copy LICENSE "%OUTPUT_DIR%\NetSpeedTray-Portable\" || (
    echo ERROR: Failed to copy LICENSE
    exit /b 1
)

rem Create ZIP file for portable version
echo Creating portable ZIP...
powershell Compress-Archive -Path "%OUTPUT_DIR%\NetSpeedTray-Portable\*" -DestinationPath "%OUTPUT_DIR%\NetSpeedTray-Portable.zip" -Force
if errorlevel 1 (
    echo ERROR: Failed to create portable ZIP
    exit /b 1
)

rem Create installer
echo Creating installer...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
if errorlevel 1 (
    echo ERROR: Installer creation failed
    exit /b 1
)

rem Move installer to output directory
move "installer\NetSpeedTray-%VERSION%-Setup.exe" "%OUTPUT_DIR%\NetSpeedTray-%VERSION%-Setup.exe" || (
    echo ERROR: Failed to move installer
    exit /b 1
)

rem Generate checksums
echo.
echo Generating checksums...
echo # SHA-256 Checksums > "%OUTPUT_DIR%\checksums.txt"
echo. >> "%OUTPUT_DIR%\checksums.txt"
echo ## NetSpeedTray-Portable.zip >> "%OUTPUT_DIR%\checksums.txt"
certutil -hashfile "%OUTPUT_DIR%\NetSpeedTray-Portable.zip" SHA256 | findstr /v "hash" >> "%OUTPUT_DIR%\checksums.txt" || (
    echo ERROR: Failed to generate checksum for portable ZIP
)
echo. >> "%OUTPUT_DIR%\checksums.txt"
echo ## NetSpeedTray-%VERSION%-Setup.exe >> "%OUTPUT_DIR%\checksums.txt"
certutil -hashfile "%OUTPUT_DIR%\NetSpeedTray-%VERSION%-Setup.exe" SHA256 | findstr /v "hash" >> "%OUTPUT_DIR%\checksums.txt" || (
    echo ERROR: Failed to generate checksum for installer
)

echo.
echo Build completed successfully!
echo Portable version: %OUTPUT_DIR%\NetSpeedTray-Portable\NetSpeedTray.exe
echo Portable ZIP: %OUTPUT_DIR%\NetSpeedTray-Portable.zip
echo Installer: %OUTPUT_DIR%\NetSpeedTray-%VERSION%-Setup.exe
echo Checksums: %OUTPUT_DIR%\checksums.txt
