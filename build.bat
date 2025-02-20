@echo off
echo Building NetSpeedTray...

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
del /f /q "NetSpeedTray-Portable.zip" 2>nul

rem Build executable
echo Building executable...
python -m PyInstaller network-monitor.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed
    exit /b 1
)

rem Create portable version
echo Creating portable version...
mkdir NetSpeedTray-Portable
copy dist\NetSpeedTray.exe NetSpeedTray-Portable\
copy README.md NetSpeedTray-Portable\
copy LICENSE NetSpeedTray-Portable\

rem Create ZIP file for portable version
echo Creating portable ZIP...
powershell Compress-Archive -Path NetSpeedTray-Portable\* -DestinationPath NetSpeedTray-Portable.zip -Force

rem Create installer
echo Creating installer...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
if errorlevel 1 (
    echo ERROR: Installer creation failed
    exit /b 1
)

rem Generate checksums
echo.
echo Generating checksums...
echo # SHA-256 Checksums > checksums.txt
echo. >> checksums.txt
echo ## NetSpeedTray-Portable.zip >> checksums.txt
certutil -hashfile NetSpeedTray-Portable.zip SHA256 | findstr /v "hash" >> checksums.txt
echo. >> checksums.txt
echo ## NetSpeedTray-Setup.exe >> checksums.txt
certutil -hashfile installer\NetSpeedTray-Setup.exe SHA256 | findstr /v "hash" >> checksums.txt

echo.
echo Build completed successfully!
echo Portable version: NetSpeedTray-Portable\NetSpeedTray.exe
echo Portable ZIP: NetSpeedTray-Portable.zip
echo Installer: installer\NetSpeedTray-Setup.exe
echo Checksums: checksums.txt