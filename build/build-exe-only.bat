@echo off
setlocal EnableDelayedExpansion

:: --- Automatically determine project paths (Robust) ---
set "BUILD_DIR=%~dp0"
pushd "%BUILD_DIR%.."
set "ROOT_DIR=%CD%"
popd

:: --- Configuration ---
echo Resolving Python from: "%ROOT_DIR%\.venv\Scripts\python.exe"

"%ROOT_DIR%\.venv\Scripts\python.exe" -c "import sys, os; sys.path.append(os.path.join(r'%ROOT_DIR%', 'src')); import netspeedtray; print(netspeedtray.__version__)" > "%BUILD_DIR%version.tmp"

if not exist "%BUILD_DIR%version.tmp" (
    echo.
    echo ERROR: Could not extract version - file missing.
    exit /b 1
)

set /p VERSION=<"%BUILD_DIR%version.tmp"
del "%BUILD_DIR%version.tmp"

if "%VERSION%"=="" (
    echo.
    echo ERROR: Could not extract version - empty result.
    exit /b 1
)

echo Detected Version: %VERSION%

set "CODENAME=%ROOT_DIR%\src\monitor.py"
set "DIST_DIR=%ROOT_DIR%\dist"
set "OUTPUT_DIR=%BUILD_DIR%exe"
set "LOG_FILE=%BUILD_DIR%build_log.txt"

:: --- Main Script Logic ---
echo Compiling %CODENAME% v%VERSION% (EXE only) > "%LOG_FILE%"
echo Compiling %CODENAME% v%VERSION% (EXE only)
set "total_start_time=%TIME%"

:: Stage 1: Clean Up Previous Builds
echo.
echo Cleaning up previous build artifacts...
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%" 2>nul
if exist "%BUILD_DIR%build" rmdir /s /q "%BUILD_DIR%build" 2>nul
if exist "%OUTPUT_DIR%" rmdir /s /q "%OUTPUT_DIR%" 2>nul

:: Stage 2: Verify Dependencies
echo.
echo Verifying dependencies...
echo Verifying dependencies... >> "%LOG_FILE%"
set "start_time=%TIME%"
if not exist "%CODENAME%" (echo ERROR: monitor.py missing & exit /b 1)
if not exist "%ROOT_DIR%\assets\NetSpeedTray.ico" (echo ERROR: NetSpeedTray.ico missing & exit /b 1)
if not exist "%BUILD_DIR%netspeedtray.spec" (echo ERROR: netspeedtray.spec missing & exit /b 1)
call "%ROOT_DIR%\.venv\Scripts\activate.bat" 2>nul
"%ROOT_DIR%\.venv\Scripts\python.exe" -c "import PyInstaller" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: PyInstaller not installed. Install with: python -m pip install -r dev-requirements.txt & exit /b 1)
set "end_time=%TIME%"
call :log_elapsed "Verifying dependencies" "%start_time%" "%end_time%"

:: Stage 3: Generate Version Info
echo.
echo Generating version info for v%VERSION%...
echo Generating version info... >> "%LOG_FILE%"
set "start_time=%TIME%"
cd /d "%BUILD_DIR%"
"%ROOT_DIR%\.venv\Scripts\python.exe" create_version_info.py "%VERSION%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Failed to generate version info & exit /b 1)
set "end_time=%TIME%"
call :log_elapsed "Generating version info" "%start_time%" "%end_time%"

:: Stage 4: Compile Executable with PyInstaller
echo.
echo Compiling executable...
echo Compiling executable... >> "%LOG_FILE%"
set "start_time=%TIME%"
"%ROOT_DIR%\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --distpath "%DIST_DIR%" netspeedtray.spec >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: PyInstaller compilation failed. Check %LOG_FILE% for details & exit /b 1)
if not exist "%DIST_DIR%\NetSpeedTray\NetSpeedTray.exe" (echo ERROR: Executable not found after compilation & exit /b 1)
set "end_time=%TIME%"
call :log_elapsed "Compiling executable" "%start_time%" "%end_time%"

:: Stage 5: Move Final Executable to Output Directory
echo.
echo Moving executable to output directory...
echo Moving executable to output directory... >> "%LOG_FILE%"
set "start_time=%TIME%"
mkdir "%OUTPUT_DIR%" 2>nul
move "%DIST_DIR%\NetSpeedTray\NetSpeedTray.exe" "%OUTPUT_DIR%\NetSpeedTray-v%VERSION%.exe" > NUL
if errorlevel 1 (echo ERROR: Failed to move executable & exit /b 1)
set "end_time=%TIME%"
call :log_elapsed "Moving executable" "%start_time%" "%end_time%"

:: Stage 6: Final Cleanup - Remove All Traces
echo.
echo Cleaning up build artifacts...
echo Cleaning up build artifacts... >> "%LOG_FILE%"
set "start_time=%TIME%"
cd /d "%ROOT_DIR%"

if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%" 2>nul
if exist "%BUILD_DIR%build" rmdir /s /q "%BUILD_DIR%build" 2>nul
if exist "%ROOT_DIR%\src\__pycache__" rmdir /s /q "%ROOT_DIR%\src\__pycache__" 2>nul
for /r "%ROOT_DIR%\src\netspeedtray" %%i in (__pycache__) do if exist "%%i" rmdir /s /q "%%i" 2>nul
for /r "%ROOT_DIR%\src" %%i in (*.pyc) do if exist "%%i" del /f /q "%%i" 2>nul

set "end_time=%TIME%"
call :log_elapsed "Final cleanup" "%start_time%" "%end_time%"

:: Total Compilation Time
set "total_end_time=%TIME%"
call :log_elapsed "Total Compilation Time" "%total_start_time%" "%total_end_time%" "summary"

echo.
:: --- Final Success Message ---
echo.
echo    BUILD SUCCESSFUL
echo.
echo   Executable created:
echo     %OUTPUT_DIR%\NetSpeedTray-v%VERSION%.exe
echo.
exit /b 0

:: --- SCRIPT FUNCTIONS ---
:log_elapsed
set "stage_name=%~1"
set "start_time_str=%~2"
set "end_time_str=%~3"
set "summary_mode=%~4"

for /f "tokens=*" %%i in ('powershell -Command "(Get-Date '%end_time_str%') - (Get-Date '%start_time_str%') | ForEach-Object { '{0:00}:{1:00}:{2:00}.{3:00}' -f $_.Hours, $_.Minutes, $_.Seconds, $_.Milliseconds }"') do set "elapsed_time=%%i"

for /f "tokens=1-4 delims=:." %%a in ("!elapsed_time!") do (
    set /a "h=100%%a%%100, m=100%%b%%100, s=100%%c%%100, ms=100%%d%%100"
)

set "formatted_elapsed="
if !h! gtr 0 set "formatted_elapsed=!h!h "
if !m! gtr 0 set "formatted_elapsed=!formatted_elapsed!!m!m "
if !s! gtr 0 set "formatted_elapsed=!formatted_elapsed!!s!s "
if !ms! gtr 0 set "formatted_elapsed=!formatted_elapsed!!ms!ms"
if "!formatted_elapsed!"=="" set "formatted_elapsed=0ms"

if defined summary_mode (
    echo.
    echo %stage_name%: !formatted_elapsed!
) else (
    echo %stage_name% completed in !formatted_elapsed!
    echo %stage_name% completed in !formatted_elapsed! >> "%LOG_FILE%"
)
exit /b
