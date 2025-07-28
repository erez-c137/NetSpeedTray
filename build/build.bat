@echo off
setlocal EnableDelayedExpansion

rem Set version number here (sync with __init__.py and setup.iss)
set VERSION=1.0.5-Beta2

rem Get the parent directory of build.bat as an absolute path
pushd %~dp0..
set ROOT_DIR=%CD%
popd

set CODENAME=%ROOT_DIR%\src\monitor.py
set DIST_DIR=%ROOT_DIR%\dist
set TEMP_BUILD_DIR=%ROOT_DIR%\build_temp
set LOG_FILE=%ROOT_DIR%\build_log.txt

echo Compiling %CODENAME% v%VERSION%
echo Compiling %CODENAME% v%VERSION% >> "%LOG_FILE%"

goto :main

:time_diff
set "start=%~1"
set "end=%~2"
rem Normalize time format (pad hours if needed)
for /f "tokens=1-4 delims=:." %%a in ("%start%") do (
    set "start_hh=%%a" & set "start_mm=%%b" & set "start_ss=%%c" & set "start_ms=%%d"
    if "!start_hh:~0,1!"==" " set "start_hh=0!start_hh:~1!"
)
for /f "tokens=1-4 delims=:." %%a in ("%end%") do (
    set "end_hh=%%a" & set "end_mm=%%b" & set "end_ss=%%c" & set "end_ms=%%d"
    if "!end_hh:~0,1!"==" " set "end_hh=0!end_hh:~1!"
)
set /a "start_secs=start_hh*3600 + start_mm*60 + start_ss" 2>nul
set /a "start_ms=start_ms" 2>nul || set "start_ms=0"
set /a "end_secs=end_hh*3600 + end_mm*60 + end_ss" 2>nul
set /a "end_ms=end_ms" 2>nul || set "end_ms=0"
set /a "diff_secs=end_secs - start_secs"
set /a "diff_ms=end_ms - start_ms"
if !diff_ms! lss 0 (
    set /a "diff_secs-=1"
    set /a "diff_ms+=100"
)
if !diff_secs! lss 0 (
    set /a "diff_secs+=86400"
)
set /a "hours=diff_secs/3600"
set /a "mins=(diff_secs%%3600)/60"
set /a "secs=diff_secs%%60"
set "elapsed="
if !hours! gtr 0 set "elapsed=!hours!h "
if !mins! gtr 0 set "elapsed=!elapsed!!mins!m "
if !secs! gtr 0 set "elapsed=!elapsed!!secs!s "
if !diff_ms! gtr 0 set "elapsed=!elapsed!!diff_ms!ms"
if "!elapsed!"=="" set "elapsed=0ms"
exit /b

:main
set "total_start_time=%TIME%"

rem Stage 1: Verify Dependencies
echo Verifying dependencies...
echo Verifying dependencies... >> "%LOG_FILE%"
set "start_time=%TIME%"
if not exist "%CODENAME%" (echo ERROR: monitor.py missing & echo ERROR: monitor.py missing >> "%LOG_FILE%" & exit /b 1)
if not exist "%ROOT_DIR%\assets\NetSpeedTray.ico" (echo ERROR: NetSpeedTray.ico missing & echo ERROR: NetSpeedTray.ico missing >> "%LOG_FILE%" & exit /b 1)
if not exist "%ROOT_DIR%\build\netspeedtray.spec" (echo ERROR: netspeedtray.spec missing & echo ERROR: netspeedtray.spec missing >> "%LOG_FILE%" & exit /b 1)
if not exist "%ROOT_DIR%\build\setup.iss" (echo ERROR: setup.iss missing & echo ERROR: setup.iss missing >> "%LOG_FILE%" & exit /b 1)
if not exist "%ROOT_DIR%\build\version_info.txt" (echo ERROR: version_info.txt missing & echo ERROR: version_info.txt missing >> "%LOG_FILE%" & exit /b 1)
if not exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (echo ERROR: Inno Setup 6 not installed & echo ERROR: Inno Setup 6 not installed >> "%LOG_FILE%" & exit /b 1)
if not exist "C:\Program Files\7-Zip\7z.exe" (echo ERROR: 7-Zip not installed & echo ERROR: 7-Zip not installed >> "%LOG_FILE%" & exit /b 1)
call "%ROOT_DIR%\.venv\Scripts\activate.bat" 2>nul
"%ROOT_DIR%\.venv\Scripts\python.exe" -c "import PyQt6, psutil, win32api, matplotlib, numpy, signal" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Required Python packages missing & echo ERROR: Required Python packages missing >> "%LOG_FILE%" & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Verifying dependencies completed in !elapsed!
echo Verifying dependencies completed in !elapsed! >> "%LOG_FILE%"

rem Stage 2: Compile Executable
echo Compiling executable...
echo Compiling executable... >> "%LOG_FILE%"
set "start_time=%TIME%"
cd /d "%ROOT_DIR%\build"
"%ROOT_DIR%\.venv\Scripts\python.exe" -m PyInstaller --clean --distpath "%DIST_DIR%" netspeedtray.spec >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: PyInstaller failed & echo ERROR: PyInstaller failed >> "%LOG_FILE%" & exit /b 1)
if not exist "%DIST_DIR%\NetSpeedTray.exe" (echo ERROR: Executable not found after compilation & echo ERROR: Executable not found after compilation >> "%LOG_FILE%" & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Compiling executable completed in !elapsed!
echo Compiling executable completed in !elapsed! >> "%LOG_FILE%"

rem Stage 3: Package Portable
echo Packaging portable version...
echo Packaging portable version... >> "%LOG_FILE%"
set "start_time=%TIME%"
if not exist "%DIST_DIR%\NetSpeedTray-%VERSION%" mkdir "%DIST_DIR%\NetSpeedTray-%VERSION%" >> "%LOG_FILE%" 2>&1
move "%DIST_DIR%\NetSpeedTray.exe" "%DIST_DIR%\NetSpeedTray-%VERSION%\NetSpeedTray-%VERSION%-Portable.exe" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Moving portable EXE failed & echo ERROR: Moving portable EXE failed >> "%LOG_FILE%" & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Packaging portable version completed in !elapsed!
echo Packaging portable version completed in !elapsed! >> "%LOG_FILE%"

rem Stage 4: Generate Installer
echo Generating installer...
echo Generating installer... >> "%LOG_FILE%"
set "start_time=%TIME%"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "%ROOT_DIR%\build\setup.iss" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Installer creation failed & echo ERROR: Installer creation failed >> "%LOG_FILE%" & exit /b 1)
if not exist "%ROOT_DIR%\build\installer\NetSpeedTray-%VERSION%-Setup.exe" (echo ERROR: Setup file not found after compilation & echo ERROR: Setup file not found after compilation >> "%LOG_FILE%" & exit /b 1)
if not exist "%DIST_DIR%\NetSpeedTray-%VERSION%" mkdir "%DIST_DIR%\NetSpeedTray-%VERSION%" >> "%LOG_FILE%" 2>&1
move "%ROOT_DIR%\build\installer\NetSpeedTray-%VERSION%-Setup.exe" "%DIST_DIR%\NetSpeedTray-%VERSION%\" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Moving installer failed & echo ERROR: Moving installer failed >> "%LOG_FILE%" & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Generating installer completed in !elapsed!
echo Generating installer completed in !elapsed! >> "%LOG_FILE%"

rem Stage 5: Compute Checksums
echo Computing checksums...
echo Computing checksums... >> "%LOG_FILE%"
set "start_time=%TIME%"
echo # SHA-256 Checksums > "%ROOT_DIR%\build\checksums.txt"
echo # SHA-256 Checksums >> "%LOG_FILE%"
echo. >> "%ROOT_DIR%\build\checksums.txt"
echo. >> "%LOG_FILE%"
echo ## NetSpeedTray-%VERSION%-Portable.exe >> "%ROOT_DIR%\build\checksums.txt"
echo ## NetSpeedTray-%VERSION%-Portable.exe >> "%LOG_FILE%"
certutil -hashfile "%DIST_DIR%\NetSpeedTray-%VERSION%\NetSpeedTray-%VERSION%-Portable.exe" SHA256 | findstr /v "hash" >> "%ROOT_DIR%\build\checksums.txt"
certutil -hashfile "%DIST_DIR%\NetSpeedTray-%VERSION%\NetSpeedTray-%VERSION%-Portable.exe" SHA256 | findstr /v "hash" >> "%LOG_FILE%"
echo. >> "%ROOT_DIR%\build\checksums.txt"
echo. >> "%LOG_FILE%"
echo ## NetSpeedTray-%VERSION%-Setup.exe >> "%ROOT_DIR%\build\checksums.txt"
echo ## NetSpeedTray-%VERSION%-Setup.exe >> "%LOG_FILE%"
certutil -hashfile "%DIST_DIR%\NetSpeedTray-%VERSION%\NetSpeedTray-%VERSION%-Setup.exe" SHA256 | findstr /v "hash" >> "%ROOT_DIR%\build\checksums.txt"
certutil -hashfile "%DIST_DIR%\NetSpeedTray-%VERSION%\NetSpeedTray-%VERSION%-Setup.exe" SHA256 | findstr /v "hash" >> "%LOG_FILE%"
if errorlevel 1 (echo ERROR: Checksum generation failed & echo ERROR: Checksum generation failed >> "%LOG_FILE%" & exit /b 1)
move "%ROOT_DIR%\build\checksums.txt" "%DIST_DIR%\NetSpeedTray-%VERSION%\" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Moving checksums failed & echo ERROR: Moving checksums failed >> "%LOG_FILE%" & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Computing checksums completed in !elapsed!
echo Computing checksums completed in !elapsed! >> "%LOG_FILE%"

rem Stage 6: Final Cleanup
echo Final cleanup...
echo Final cleanup... >> "%LOG_FILE%"
set "start_time=%TIME%"
if exist "%TEMP_BUILD_DIR%" (rmdir /s /q "%TEMP_BUILD_DIR%" 2>nul)
if exist "%ROOT_DIR%\build\installer" (rmdir /s /q "%ROOT_DIR%\build\installer" 2>nul)
if exist "%ROOT_DIR%\build" (
    for /d %%i in ("%ROOT_DIR%\build\*") do (
        rmdir /s /q "%%i" 2>nul
    )
)
if exist "%ROOT_DIR%\src\__pycache__" (rmdir /s /q "%ROOT_DIR%\src\__pycache__" 2>nul)
if exist "%ROOT_DIR%\src\netspeedtray\__pycache__" (rmdir /s /q "%ROOT_DIR%\src\netspeedtray\__pycache__" 2>nul)
for /r "%ROOT_DIR%\src\netspeedtray" %%i in (.) do (
    if exist "%%i\__pycache__" (rmdir /s /q "%%i\__pycache__" 2>nul)
)
for /r "%ROOT_DIR%\src" %%i in (*.pyc) do if exist "%%i" (del /f /q "%%i" 2>nul)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Final cleanup completed in !elapsed!
echo Final cleanup completed in !elapsed! >> "%LOG_FILE%"

rem Total Compilation Time
set "total_end_time=%TIME%"
call :time_diff "%total_start_time%" "%total_end_time%"
echo Total Compilation Time: !elapsed!
echo Total Compilation Time: !elapsed! >> "%LOG_FILE%"

pause