@echo off
setlocal EnableDelayedExpansion

rem Set version number here
set VERSION=1.0.2
set CODENAME=NetSpeedTray.py

echo Compiling %CODENAME% v%VERSION%

goto :main

:time_diff
set "start=%~1"
set "end=%~2"
set "start_hh=%start:~0,2%"
set "start_mm=%start:~3,2%"
set "start_ss=%start:~6,2%"
set "start_ms=%start:~9,2%"
set /a "start_secs=start_hh*3600 + start_mm*60 + start_ss" 2>nul
set /a "start_ms=start_ms" 2>nul || set "start_ms=0"
set "end_hh=%end:~0,2%"
set "end_mm=%end:~3,2%"
set "end_ss=%end:~6,2%"
set "end_ms=%end:~9,2%"
set /a "end_secs=end_hh*3600 + end_mm*60 + end_ss" 2>nul
set /a "end_ms=end_ms" 2>nul || set "end_ms=0"
if %end_secs% lss %start_secs% set /a "end_secs+=86400"
set /a "diff_secs=end_secs - start_secs"
set /a "diff_ms=end_ms - start_ms"
if %diff_ms% lss 0 (
    set /a "diff_secs-=1"
    set /a "diff_ms+=100"
)
set /a "hours=diff_secs/3600"
set /a "mins=(diff_secs%%3600)/60"
set /a "secs=diff_secs%%60"
set "elapsed="
if %hours% gtr 0 set "elapsed=!hours!h "
if %mins% gtr 0 set "elapsed=!elapsed!!mins!m "
if %secs% gtr 0 set "elapsed=!elapsed!!secs!s "
if %diff_ms% gtr 0 set "elapsed=!elapsed!!diff_ms!ms"
if "!elapsed!"=="" set "elapsed=0ms"
exit /b

:main
set "total_start_time=%TIME%"

rem Stage 1: Verify Dependencies
echo Verifying dependencies...
set "start_time=%TIME%"
if not exist "NetSpeedTray.py" (echo ERROR: NetSpeedTray.py missing & exit /b 1)
if not exist "NetSpeedTray.ico" (echo ERROR: NetSpeedTray.ico missing & exit /b 1)
if not exist "netspeedtray.spec" (echo ERROR: netspeedtray.spec missing & exit /b 1)
if not exist "installer.iss" (echo ERROR: installer.iss missing & exit /b 1)
if not exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (echo ERROR: Inno Setup 6 not installed & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Verifying dependencies completed in !elapsed!

rem Stage 2: Clean Build Artifacts
echo Cleaning build artifacts...
set "start_time=%TIME%"
rmdir /s /q "dist" 2>nul
rmdir /s /q "build" 2>nul
rmdir /s /q "installer" 2>nul
rmdir /s /q "NetSpeedTray-Portable" 2>nul
del /f /q "NetSpeedTray-%VERSION%-Portable.zip" 2>nul
del /f /q "checksums.txt" 2>nul
rmdir /s /q "NetSpeedTray-Latest" 2>nul
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Cleaning build artifacts completed in !elapsed!

rem Stage 3: Compile Executable
echo Compiling executable...
set "start_time=%TIME%"
python -m PyInstaller netspeedtray.spec >nul 2>nul
if errorlevel 1 (echo ERROR: PyInstaller failed & exit /b 1)
copy "NetSpeedTray.ico" "dist\NetSpeedTray.ico" >nul 2>nul
if errorlevel 1 (echo ERROR: Icon copy failed & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Compiling executable completed in !elapsed!

rem Stage 4: Package Portable
echo Packaging portable version...
set "start_time=%TIME%"
mkdir NetSpeedTray-Portable >nul 2>nul
copy "dist\NetSpeedTray.exe" "NetSpeedTray-Portable\" >nul 2>nul
if errorlevel 1 (echo ERROR: Portable copy failed & exit /b 1)
powershell Compress-Archive -Path "NetSpeedTray-Portable\*" -DestinationPath "NetSpeedTray-%VERSION%-Portable.zip" -Force >nul 2>nul
if errorlevel 1 (echo ERROR: ZIP creation failed & exit /b 1)
rmdir /s /q "NetSpeedTray-Portable" 2>nul
if errorlevel 1 (echo WARNING: Temp folder cleanup failed)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Packaging portable version completed in !elapsed!

rem Stage 5: Generate Installer
echo Generating installer...
set "start_time=%TIME%"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss >nul 2>nul
if errorlevel 1 (echo ERROR: Installer failed & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Generating installer completed in !elapsed!

rem Stage 6: Organize Deliverables
echo Organizing deliverables...
set "start_time=%TIME%"
mkdir NetSpeedTray-Latest >nul 2>nul
move "NetSpeedTray-%VERSION%-Portable.zip" "NetSpeedTray-Latest\" >nul 2>nul
move "installer\NetSpeedTray-%VERSION%-Setup.exe" "NetSpeedTray-Latest\" >nul 2>nul
if errorlevel 1 (echo ERROR: Moving outputs failed & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Organizing deliverables completed in !elapsed!

rem Stage 7: Compute Checksums
echo Computing checksums...
set "start_time=%TIME%"
echo # SHA-256 Checksums > checksums.txt 2>nul
echo. >> checksums.txt 2>nul
echo ## NetSpeedTray-%VERSION%-Portable.zip >> checksums.txt 2>nul
certutil -hashfile "NetSpeedTray-Latest\NetSpeedTray-%VERSION%-Portable.zip" SHA256 | findstr /v "hash" >> checksums.txt 2>nul
echo. >> checksums.txt 2>nul
echo ## NetSpeedTray-%VERSION%-Setup.exe >> checksums.txt 2>nul
certutil -hashfile "NetSpeedTray-Latest\NetSpeedTray-%VERSION%-Setup.exe" SHA256 | findstr /v "hash" >> checksums.txt 2>nul
if errorlevel 1 (echo ERROR: Checksum generation failed & exit /b 1)
move "checksums.txt" "NetSpeedTray-Latest\" >nul 2>nul
if errorlevel 1 (echo ERROR: Moving checksums failed & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Computing checksums completed in !elapsed!

rem Stage 8: Final Cleanup
echo Final cleanup...
set "start_time=%TIME%"
rmdir /s /q "build" 2>nul
rmdir /s /q "dist" 2>nul
rmdir /s /q "installer" 2>nul
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Final cleanup completed in !elapsed!

rem Total Compilation Time
set "total_end_time=%TIME%"
call :time_diff "%total_start_time%" "%total_end_time%"
echo Total Compilation Time: !elapsed!

pause
