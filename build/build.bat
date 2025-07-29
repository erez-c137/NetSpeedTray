@echo off
setlocal EnableDelayedExpansion

rem Set version number here (sync with __init__.py and setup.iss)
set VERSION=1.0.5

rem Get the parent directory of build.bat as an absolute path
pushd %~dp0..
set ROOT_DIR=%CD%
popd

set CODENAME=%ROOT_DIR%\src\monitor.py
set DIST_DIR=%ROOT_DIR%\dist
set TEMP_BUILD_DIR=%ROOT_DIR%\build_temp
set LOG_FILE=%ROOT_DIR%\build_log.txt

echo Compiling %CODENAME% v%VERSION% > "%LOG_FILE%"
echo Compiling %CODENAME% v%VERSION%

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
if not exist "%CODENAME%" (echo ERROR: monitor.py missing & exit /b 1)
if not exist "%ROOT_DIR%\assets\NetSpeedTray.ico" (echo ERROR: NetSpeedTray.ico missing & exit /b 1)
if not exist "%ROOT_DIR%\build\netspeedtray.spec" (echo ERROR: netspeedtray.spec missing & exit /b 1)
if not exist "%ROOT_DIR%\build\setup.iss" (echo ERROR: setup.iss missing & exit /b 1)
if not exist "%ROOT_DIR%\build\version_info.txt" (echo ERROR: version_info.txt missing & exit /b 1)
if not exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (echo ERROR: Inно Setup 6 not installed & exit /b 1)
if not exist "C:\Program Files\7-Zip\7z.exe" (echo ERROR: 7-Zip not installed & exit /b 1)
call "%ROOT_DIR%\.venv\Scripts\activate.bat" 2>nul
"%ROOT_DIR%\.venv\Scripts\python.exe" -c "import PyQt6, psutil, win32api, matplotlib, numpy, signal" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Required Python packages missing & exit /b 1)
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
if errorlevel 1 (echo ERROR: PyInstaller failed & exit /b 1)
if not exist "%DIST_DIR%\NetSpeedTray.exe" (echo ERROR: Executable not found after compilation & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Compiling executable completed in !elapsed!
echo Compiling executable completed in !elapsed! >> "%LOG_FILE%"

rem Stage 3: Generate Installer
echo Generating installer...
echo Generating installer... >> "%LOG_FILE%"
set "start_time=%TIME%"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "%ROOT_DIR%\build\setup.iss" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Installer creation failed & exit /b 1)
if not exist "%ROOT_DIR%\build\installer\NetSpeedTray-%VERSION%-Setup.exe" (echo ERROR: Setup file not found after compilation & exit /b 1)
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Generating installer completed in !elapsed!
echo Generating installer completed in !elapsed! >> "%LOG_FILE%"

rem Stage 4: Package Final Release Files
echo Packaging release files...
echo Packaging release files... >> "%LOG_FILE%"
set "start_time=%TIME%"
set "RELEASE_DIR=%DIST_DIR%\NetSpeedTray-%VERSION%"
if not exist "!RELEASE_DIR!" mkdir "!RELEASE_DIR!" >> "%LOG_FILE%" 2>&1
rem Move the compiled EXE and the new installer into the release folder
move "%DIST_DIR%\NetSpeedTray.exe" "!RELEASE_DIR!\NetSpeedTray.exe" >> "%LOG_FILE%" 2>&1
move "%ROOT_DIR%\build\installer\NetSpeedTray-%VERSION%-Setup.exe" "!RELEASE_DIR!\" >> "%LOG_FILE%" 2>&1
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Packaging release files completed in !elapsed!
echo Packaging release files completed in !elapsed! >> "%LOG_FILE%"

rem Stage 5: Compute Checksums
echo Computing checksums...
echo Computing checksums... >> "%LOG_FILE%"
set "start_time=%TIME%"
set "CHECKSUM_FILE=%RELEASE_DIR%\checksums.txt"
echo # SHA-256 Checksums > "!CHECKSUM_FILE!"
echo. >> "!CHECKSUM_FILE!"
echo ## NetSpeedTray.exe (Portable) >> "!CHECKSUM_FILE!"
certutil -hashfile "!RELEASE_DIR!\NetSpeedTray.exe" SHA256 | findstr /v "hash" >> "!CHECKSUM_FILE!"
echo. >> "!CHECKSUM_FILE!"
echo ## NetSpeedTray-%VERSION%-Setup.exe >> "!CHECKSUM_FILE!"
certutil -hashfile "!RELEASE_DIR!\NetSpeedTray-%VERSION%-Setup.exe" SHA256 | findstr /v "hash" >> "!CHECKSUM_FILE!"
set "end_time=%TIME%"
call :time_diff "%start_time%" "%end_time%"
echo Computing checksums completed in !elapsed!
echo Computing checksums completed in !elapsed! >> "%LOG_FILE%"

rem Stage 6: Final Cleanup (Restored to your original, working logic)
echo Final cleanup...
echo Final cleanup... >> "%LOG_FILE%"
set "start_time=%TIME%"
cd /d "%ROOT_DIR%"
if exist "%TEMP_BUILD_DIR%" (rmdir /s /q "%TEMP_BUILD_DIR%" 2>nul)
if exist "%ROOT_DIR%\installer" (rmdir /s /q "%ROOT_DIR%\installer" 2>nul)
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

pause