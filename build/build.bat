@echo off
setlocal EnableDelayedExpansion

:: --- Configuration ---
set "VERSION=1.0.7"

:: --- Automatically determine project paths (Portable & Robust) ---
set "BUILD_DIR=%~dp0"
for %%i in ("%BUILD_DIR%..") do set "ROOT_DIR=%%~fi"

set "CODENAME=%ROOT_DIR%\src\monitor.py"
set "DIST_DIR=%ROOT_DIR%\dist"
set "INSTALLER_DIR=%BUILD_DIR%installer"
set "LOG_FILE=%BUILD_DIR%build_log.txt"

:: --- Main Script Logic ---
echo Compiling %CODENAME% v%VERSION% > "%LOG_FILE%"
echo Compiling %CODENAME% v%VERSION%
set "total_start_time=%TIME%"

:: Stage 1: Clean Up
echo.
echo Cleaning up previous build artifacts...
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%" 2>nul
if exist "%BUILD_DIR%build" rmdir /s /q "%BUILD_DIR%build" 2>nul
if exist "%INSTALLER_DIR%" rmdir /s /q "%INSTALLER_DIR%" 2>nul

:: Stage 2: Verify Dependencies
echo.
echo Verifying dependencies...
echo Verifying dependencies... >> "%LOG_FILE%"
set "start_time=%TIME%"
if not exist "%CODENAME%" (echo ERROR: monitor.py missing & goto :fail)
if not exist "%ROOT_DIR%\assets\NetSpeedTray.ico" (echo ERROR: NetSpeedTray.ico missing & goto :fail)
if not exist "%BUILD_DIR%NetSpeedTray.spec" (echo ERROR: netspeedtray.spec missing & goto :fail)
if not exist "%BUILD_DIR%setup.iss" (echo ERROR: setup.iss missing & goto :fail)
if not exist "%BUILD_DIR%version_info.txt" (echo ERROR: version_info.txt missing & goto :fail)
if not exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (echo ERROR: Inno Setup 6 not installed & goto :fail)
call "%ROOT_DIR%\.venv\Scripts\activate.bat" 2>nul
"%ROOT_DIR%\.venv\Scripts\python.exe" -c "import PyQt6, psutil, win32api, matplotlib, numpy, signal" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Required Python packages missing & goto :fail)
set "end_time=%TIME%"
call :log_elapsed "Verifying dependencies" "%start_time%" "%end_time%"

:: Stage 3: Compile Executable
echo.
echo Compiling executable...
echo Compiling executable... >> "%LOG_FILE%"
set "start_time=%TIME%"
cd /d "%BUILD_DIR%"
pyinstaller --noconfirm --distpath "%DIST_DIR%" NetSpeedTray.spec >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: PyInstaller failed. Check %LOG_FILE% & goto :fail)
if not exist "%DIST_DIR%\NetSpeedTray.exe" (echo ERROR: Executable not found after compilation & goto :fail)
set "end_time=%TIME%"
call :log_elapsed "Compiling executable" "%start_time%" "%end_time%"

:: Stage 4: Generate Installer
echo.
echo Generating installer...
echo Generating installer... >> "%LOG_FILE%"
set "start_time=%TIME%"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "%BUILD_DIR%setup.iss" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (echo ERROR: Installer creation failed. Check %LOG_FILE% & goto :fail)
if not exist "%INSTALLER_DIR%\NetSpeedTray-%VERSION%-Setup.exe" (echo ERROR: Setup file not found after compilation & goto :fail)
set "end_time=%TIME%"
call :log_elapsed "Generating installer" "%start_time%" "%end_time%"

:: Stage 5: Package Final Release Files
echo.
echo Packaging release files...
echo Packaging release files... >> "%LOG_FILE%"
set "start_time=%TIME%"
set "RELEASE_DIR=%DIST_DIR%\NetSpeedTray-%VERSION%"
if not exist "!RELEASE_DIR!" mkdir "!RELEASE_DIR!"
move "%DIST_DIR%\NetSpeedTray.exe" "!RELEASE_DIR!\NetSpeedTray-%VERSION%-Portable.exe" > NUL
move "%INSTALLER_DIR%\NetSpeedTray-%VERSION%-Setup.exe" "!RELEASE_DIR!\" > NUL
set "end_time=%TIME%"
call :log_elapsed "Packaging release files" "%start_time%" "%end_time%"

:: Stage 6: Compute Checksums
echo.
echo Computing checksums...
echo Computing checksums... >> "%LOG_FILE%"
set "start_time=%TIME%"
set "CHECKSUM_FILE=%RELEASE_DIR%\checksums.txt"
(
    echo # SHA-256 Checksums for v%VERSION%
    echo.
    echo ### NetSpeedTray-%VERSION%-Portable.exe
    certutil -hashfile "!RELEASE_DIR!\NetSpeedTray-%VERSION%-Portable.exe" SHA256 | findstr /v "hash"
    echo.
    echo ### NetSpeedTray-%VERSION%-Setup.exe
    certutil -hashfile "!RELEASE_DIR!\NetSpeedTray-%VERSION%-Setup.exe" SHA256 | findstr /v "hash"
) > "!CHECKSUM_FILE!"
set "end_time=%TIME%"
call :log_elapsed "Computing checksums" "%start_time%" "%end_time%"

:: Stage 7: Final Cleanup
echo.
echo Final cleanup...
echo Final cleanup... >> "%LOG_FILE%"
set "start_time=%TIME%"
cd /d "%ROOT_DIR%"
if exist "%ROOT_DIR%\build\build" rmdir /s /q "%ROOT_DIR%\build\build" 2>nul
if exist "%INSTALLER_DIR%" rmdir /s /q "%INSTALLER_DIR%" 2>nul
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
echo   Release files are located at:
echo     %RELEASE_DIR%\
echo.
goto :end

:fail
echo.
echo !   BUILD FAILED. Check %LOG_FILE% for details.     !
echo.
goto :end

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

:end
pause