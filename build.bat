@echo off
echo Building NetSpeedTray...

rem Set version number here
set VERSION=1.0.2

rem Check for required files
if not exist "NetSpeedTray.py" (
    echo ERROR: NetSpeedTray.py is missing
    exit /b 1
)
if not exist "NetSpeedTray.ico" (
    echo ERROR: NetSpeedTray.ico is missing
    exit /b 1
)
if not exist "netspeedtray.spec" (
    echo ERROR: netspeedtray.spec is missing
    exit /b 1
)
if not exist "installer.iss" (
    echo ERROR: installer.iss is missing
    exit /b 1
)
if not exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    echo ERROR: Inno Setup 6 is not installed at "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    echo Please install Inno Setup 6 from https://jrsoftware.org/isinfo.php
    exit /b 1
)

rem Clean previous builds
echo Cleaning previous builds...
rmdir /s /q "dist" 2>nul
rmdir /s /q "build" 2>nul
rmdir /s /q "installer" 2>nul
rmdir /s /q "NetSpeedTray-Portable" 2>nul
del /f /q "NetSpeedTray-%VERSION%-Portable.zip" 2>nul
del /f /q "checksums.txt" 2>nul
rmdir /s /q "NetSpeedTray-Latest" 2>nul

rem Build executable with PyInstaller
echo Building executable...
python -m PyInstaller netspeedtray.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed
    exit /b 1
)

rem Copy NetSpeedTray.ico to dist folder for installer
echo Copying NetSpeedTray.ico to dist...
copy "NetSpeedTray.ico" "dist\NetSpeedTray.ico"
if errorlevel 1 (
    echo ERROR: Failed to copy NetSpeedTray.ico to dist
    exit /b 1
)

rem Create portable version (no ICO file)
echo Creating portable version...
mkdir NetSpeedTray-Portable
copy "dist\NetSpeedTray.exe" "NetSpeedTray-Portable\"
if errorlevel 1 (
    echo ERROR: Failed to create portable version
    exit /b 1
)

rem Create ZIP file for portable version with version number
echo Creating portable ZIP...
powershell Compress-Archive -Path "NetSpeedTray-Portable\*" -DestinationPath "NetSpeedTray-%VERSION%-Portable.zip" -Force
if errorlevel 1 (
    echo ERROR: Failed to create portable ZIP
    exit /b 1
)

rem Clean up portable folder
echo Cleaning up temporary portable folder...
rmdir /s /q "NetSpeedTray-Portable"
if errorlevel 1 (
    echo WARNING: Failed to delete temporary portable folder
)

rem Create installer
echo Creating installer...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
if errorlevel 1 (
    echo ERROR: Installer creation failed
    exit /b 1
)

rem Create output folder
echo Creating output folder NetSpeedTray-Latest...
mkdir NetSpeedTray-Latest
if errorlevel 1 (
    echo ERROR: Failed to create NetSpeedTray-Latest folder
    exit /b 1
)

rem Move outputs to NetSpeedTray-Latest (only ZIP and installer)
echo Moving outputs to NetSpeedTray-Latest...
move "NetSpeedTray-%VERSION%-Portable.zip" "NetSpeedTray-Latest\"
move "installer\NetSpeedTray-%VERSION%-Setup.exe" "NetSpeedTray-Latest\"
if errorlevel 1 (
    echo ERROR: Failed to move outputs to NetSpeedTray-Latest
    exit /b 1
)

rem Generate checksums
echo.
echo Generating checksums...
echo # SHA-256 Checksums > checksums.txt
echo. >> checksums.txt
echo ## NetSpeedTray-%VERSION%-Portable.zip >> checksums.txt
certutil -hashfile "NetSpeedTray-Latest\NetSpeedTray-%VERSION%-Portable.zip" SHA256 | findstr /v "hash" >> checksums.txt
echo. >> checksums.txt
echo ## NetSpeedTray-%VERSION%-Setup.exe >> checksums.txt
certutil -hashfile "NetSpeedTray-Latest\NetSpeedTray-%VERSION%-Setup.exe" SHA256 | findstr /v "hash" >> checksums.txt
if errorlevel 1 (
    echo ERROR: Failed to generate checksums
    exit /b 1
)

rem Move checksums to NetSpeedTray-Latest
move "checksums.txt" "NetSpeedTray-Latest\"
if errorlevel 1 (
    echo ERROR: Failed to move checksums.txt to NetSpeedTray-Latest
    exit /b 1
)

rem Cleanup stage
echo.
echo Performing cleanup...
rmdir /s /q "build" 2>nul
rmdir /s /q "dist" 2>nul
rmdir /s /q "installer" 2>nul
if errorlevel 1 (
    echo WARNING: Some cleanup steps may have failed
)

echo.
echo Build completed successfully!
echo Portable ZIP: NetSpeedTray-Latest\NetSpeedTray-%VERSION%-Portable.zip
echo Installer: NetSpeedTray-Latest\NetSpeedTray-%VERSION%-Setup.exe
echo Checksums: NetSpeedTray-Latest\checksums.txt
pause
