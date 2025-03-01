@echo off
setlocal EnableDelayedExpansion

rem Set version number here
set VERSION=1.0.3
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
if not exist "C:\Program Files\7-Zip\7z.exe" (echo ERROR: 7-Zip not installed & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Verifying dependencies completed in !elapsed!

rem Stage 2: Clean Build Artifacts
echo Cleaning build artifacts...
set "start_time=%TIME%"
if exist "dist" rmdir /s /q "dist" 2>nul
if exist "build" rmdir /s /q "build" 2>nul
if exist "installer" rmdir /s /q "installer" 2>nul
if exist "NetSpeedTray-Latest" rmdir /s /q "NetSpeedTray-Latest" 2>nul
if exist "NetSpeedTray-%VERSION%-Portable.7z" del /f /q "NetSpeedTray-%VERSION%-Portable.7z" 2>nul
if exist "checksums.txt" del /f /q "checksums.txt" 2>nul
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Cleaning build artifacts completed in !elapsed!

rem Stage 3: Compile Executable
echo Compiling executable...
set "start_time=%TIME%"
python -m PyInstaller netspeedtray.spec >nul 2>nul
if errorlevel 1 (echo ERROR: PyInstaller failed & exit /b 1)
if not exist "dist\NetSpeedTray.exe" (echo ERROR: Executable not found after compilation & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Compiling executable completed in !elapsed!

rem Stage 4: Package Portable
echo Packaging portable version...
set "start_time=%TIME%"
cd dist
"C:\Program Files\7-Zip\7z.exe" a "..\NetSpeedTray-%VERSION%-Portable.7z" "NetSpeedTray.exe" >nul 2>nul
cd ..
if errorlevel 1 (echo ERROR: 7z creation failed & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Packaging portable version completed in !elapsed!

rem Stage 5: Generate Installer
echo Generating installer...
set "start_time=%TIME%"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss >nul 2>nul
if errorlevel 1 (echo ERROR: Installer creation failed & exit /b 1)
if not exist "installer\NetSpeedTray-%VERSION%-Setup.exe" (echo ERROR: Setup file not found after compilation & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Generating installer completed in !elapsed!

rem Stage 6: Organize Deliverables
echo Organizing deliverables...
set "start_time=%TIME%"
if not exist "NetSpeedTray-Latest" mkdir "NetSpeedTray-Latest" >nul 2>nul
move "NetSpeedTray-%VERSION%-Portable.7z" "NetSpeedTray-Latest\" >nul 2>nul
if errorlevel 1 (echo ERROR: Moving portable 7z failed & exit /b 1)
move "installer\NetSpeedTray-%VERSION%-Setup.exe" "NetSpeedTray-Latest\" >nul 2>nul
if errorlevel 1 (echo ERROR: Moving setup executable failed & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Organizing deliverables completed in !elapsed!

rem Stage 7: Compute Checksums
echo Computing checksums...
set "start_time=%TIME%"
echo # SHA-256 Checksums > checksums.txt 2>nul
echo. >> checksums.txt 2>nul
echo ## NetSpeedTray-%VERSION%-Portable.7z >> checksums.txt 2>nul
certutil -hashfile "NetSpeedTray-Latest\NetSpeedTray-%VERSION%-Portable.7z" SHA256 | findstr /v "hash" >> checksums.txt 2>nul
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
if exist "build" rmdir /s /q "build" 2>nul
if exist "dist" rmdir /s /q "dist" 2>nul
if exist "installer" rmdir /s /q "installer" 2>nul
if exist "__pycache__" rmdir /s /q "__pycache__" 2>nul
for %%i in (*.pyc) do del /f /q "%%i" 2>nul
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Final cleanup completed in !elapsed!

rem Total Compilation Time
set "total_end_time=%TIME%"
call :time_diff "%total_start_time%" "%total_end_time%"
echo Total Compilation Time: !elapsed!

pause