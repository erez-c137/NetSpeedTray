; NetSpeedTray Installer Script

#define MyAppName "NetSpeedTray"
#define MyAppPublisher "Erez C137"
#define MyAppURL "https://github.com/erez-c137/NetSpeedTray"
#define MyAppExeName "NetSpeedTray.exe"
#define MyAppMutex "Global\NetSpeedTray_SingleInstanceMutex"
#define MyAppId "{{D3A32B89-C533-4F2C-9F87-23B2395B5B89}}"
; Graceful-shutdown channel - a wire protocol with the running app. Must match
; constants.app.SHUTDOWN_MESSAGE_NAME / SHUTDOWN_WINDOW_PROP (a unit test pins both).
#define ShutdownMessageName "NetSpeedTray_Shutdown_v1"
#define ShutdownWindowProp "NetSpeedTray_ShutdownTarget_v1"
; Seconds Setup waits for the running app to exit after asking it to quit, before taskkill.
; Mirrors constants.app.SHUTDOWN_DEFAULT_TIMEOUT_S: must outlast the app's own worst clean quit
; (1.5 s latency-probe join + the 15 s DB_SHUTDOWN_WAIT_MS bound when quit lands mid-migration, after
; which the app exits hard by itself). A normal quit returns in ~1 s; this is only the ceiling.
#define ShutdownTimeoutSec "30"

; --- DYNAMIC VERSIONING ---
; If AppVersion is NOT defined (e.g., manual compile without build.bat), use a default.
; When running via build.bat, the /DAppVersion="x.x.x" flag overrides this.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

; VersionInfoVersion only accepts dot-separated integers, so a prerelease AppVersion like
; "2.2.0-beta.1" aborts the compile ("Value of [Setup] section directive VersionInfoVersion is
; invalid"). build.bat passes /DAppVersionNumeric with the numeric quad derived by
; create_version_info.py --numeric ("2.2.0-beta.1" -> "2.2.0.1001", final "2.2.0" -> "2.2.0.10000",
; so a final outranks its betas numerically too - see that script); AppVersion keeps the display
; string for filenames and the free-text version fields. On a manual compile without the define,
; fall back to AppVersion itself when it is numeric-only (Inno pads it to x.y.z.0, which does NOT
; follow that ordering - a manual compile is for local testing only), else to a recognisable placeholder.
#ifndef AppVersionNumeric
  #if Pos("-", AppVersion) == 0
    #define AppVersionNumeric AppVersion
  #else
    #define AppVersionNumeric "0.0.0.0"
  #endif
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}

AppVersion={#AppVersion}
AppVerName={#MyAppName} {#AppVersion}

AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64 
PrivilegesRequired=admin
WizardStyle=modern
Compression=lzma
SolidCompression=yes
OutputDir=installer

OutputBaseFilename=NetSpeedTray-{#AppVersion}-x64-Setup
VersionInfoVersion={#AppVersionNumeric}
; Free-text version fields carry the full display string, so a beta installer is identifiable
; as a beta in Properties > Details even though the numeric quad above cannot say so.
VersionInfoTextVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}

DisableDirPage=auto
UsePreviousAppDir=no
SetupLogging=yes
UninstallDisplayName={#MyAppName}
RestartIfNeededByRun=no
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll
; Must match constants.app.MUTEX_NAME so Inno reliably detects the running app on (auto-)upgrade.
AppMutex={#MyAppMutex}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Wipe the previous PyInstaller payload BEFORE copying the new build. Inno's
; 'ignoreversion' copy below overwrites files present in the new build but leaves
; behind anything the new build no longer ships. v1.3.2 dropped many Qt DLLs and
; (accidentally) OpenSSL, so v1.3.1 -> v1.3.2 upgraders kept stale, mismatched
; DLLs in _internal - the cause of the broken SSL updater and a prime suspect for
; "DLL load failed" / "no Qt platform plugin" crashes. Clearing _internal first
; guarantees a consistent payload. User data ({userappdata}\NetSpeedTray) is NOT
; touched by this.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\NetSpeedTray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; The app self-registers its "start with Windows" entry at runtime (StartupManager writes the HKCU Run
; key so the path always matches the actually-installed exe). The installer never creates it - but since
; start-with-Windows is ON by default, we delete the value on UNINSTALL only, so removing the app never
; leaves a dead Run entry pointing at a deleted exe. (uninsdeletevalue does nothing at install time.)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#MyAppName}"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
; A silent install (Microsoft Store, winget install, winget upgrade) shows no Finish page, so the
; entry above never fires and the app is simply not running until the next sign-in - "nothing
; visible happened" (#260) on a new channel. These entries cover the silent path only.
;
; The relaunch must come back UNELEVATED. runasoriginaluser alone is not enough: it means "as whoever
; started Setup", and the in-app updater (2.1.6+) starts Setup already elevated, so the app came back
; as admin and stayed that way until the next sign-in (found verifying 2.1.7). So the launch is handed
; to the running shell: explorer.exe passes it to the user's existing Explorer, which starts it at the
; shell's own (medium) integrity whatever Setup's token is. Only when no shell is running (no
; Shell_TrayWnd - e.g. a headless deployment) does the direct runasoriginaluser launch remain.
Filename: "{win}\explorer.exe"; Parameters: """{app}\{#MyAppExeName}"""; Flags: nowait runasoriginaluser; Check: LaunchSilentViaShell
Filename: "{app}\{#MyAppExeName}"; Flags: nowait runasoriginaluser; Check: LaunchSilentDirect

[UninstallDelete]
Type: files; Name: "{autodesktop}\{#MyAppName}.lnk"

[Code]
var
  DeleteUserData: Boolean;

// --- External Windows API Function Prototypes ---
function OpenMutex(dwDesiredAccess: LongWord; bInheritHandle: Boolean; lpName: string): THandle;
  external 'OpenMutexW@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
// Graceful-shutdown channel (see TryGracefulClose). Win-prefixed so nothing clashes with Inno's own
// support functions. Setup is a 32-bit process, so HWND / LPARAM fit in Longint.
function WinRegisterWindowMessage(lpString: string): Cardinal;
  external 'RegisterWindowMessageW@user32.dll stdcall';
function WinEnumWindows(lpEnumFunc: Longword; lParam: Longint): Integer;
  external 'EnumWindows@user32.dll stdcall';
function WinGetProp(hWnd: HWND; lpString: string): THandle;
  external 'GetPropW@user32.dll stdcall';
function WinGetWindowThreadProcessId(hWnd: HWND; var lpdwProcessId: Longword): Longword;
  external 'GetWindowThreadProcessId@user32.dll stdcall';
function WinPostMessage(hWnd: HWND; Msg: Cardinal; wParam, lParam: Longint): Integer;
  external 'PostMessageW@user32.dll stdcall';
function WinIsWindow(hWnd: HWND): Integer;
  external 'IsWindow@user32.dll stdcall';
function WinOpenProcess(dwDesiredAccess: Longword; bInheritHandle: Integer; dwProcessId: Longword): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WinWaitForSingleObject(hHandle: THandle; dwMilliseconds: Longword): Longword;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function WinGetTickCount(): Longword;
  external 'GetTickCount@kernel32.dll stdcall';

// --- Helper Functions ---
function ShellIsRunning(): Boolean;
begin
  Result := FindWindowByClassName('Shell_TrayWnd') <> 0;
end;

function LaunchSilentViaShell(): Boolean;
begin
  Result := WizardSilent() and ShellIsRunning();
  if Result then
    Log('Silent install: starting {#MyAppName} through the shell, so it runs unelevated (no Finish page to offer it).');
end;

function LaunchSilentDirect(): Boolean;
begin
  Result := WizardSilent() and not ShellIsRunning();
  if Result then
    Log('Silent install: no shell running - starting {#MyAppName} as the original user.');
end;

function BoolToStr(Value: Boolean): string;
begin
  if Value then
    Result := 'True'
  else
    Result := 'False';
end;

function IsAppRunning(): Boolean;
var
  MutexHandle: THandle;
begin
  MutexHandle := OpenMutex($00100000, False, '{#MyAppMutex}');
  if MutexHandle <> 0 then
  begin
    CloseHandle(MutexHandle);
    Result := True;
  end
  else
  begin
    Result := False;
  end;
end;

// Windows marked by the running app as its shutdown target, filled by CollectShutdownTarget.
var
  ShutdownWnds: array of HWND;
  ShutdownPids: array of Longword;

// EnumWindows callback: record every top-level window carrying the app's shutdown property.
function CollectShutdownTarget(Wnd: HWND; Param: Longint): Integer;
var
  Pid: Longword;
  N: Integer;
begin
  Result := 1;  // keep enumerating
  if WinGetProp(Wnd, '{#ShutdownWindowProp}') <> 0 then
  begin
    Pid := 0;
    WinGetWindowThreadProcessId(Wnd, Pid);
    N := GetArrayLength(ShutdownWnds);
    SetArrayLength(ShutdownWnds, N + 1);
    SetArrayLength(ShutdownPids, N + 1);
    ShutdownWnds[N] := Wnd;
    ShutdownPids[N] := Pid;
  end;
end;

// Graceful close: post the app's registered shutdown message to the window it marked, so it quits
// through its own tray-Exit path (threads stopped, history DB flushed, config saved), then wait up to
// ShutdownTimeoutSec for each such process to exit.
//
// Setup sends the message itself and never runs the installed exe. Setup runs elevated (the in-app
// updater even starts it elevated, so "the original user" is an admin token too), and the install
// folder is only as safe as its ACL: with a custom directory - a new folder under C:\ inherits
// Authenticated Users Modify - any non-admin process could replace NetSpeedTray.exe, and the next
// silent upgrade would run it as admin. Posting a window message executes nothing.
//
// Versions before the channel (<= 2.1.7) mark no window, so nothing is found and the taskkill
// fallback runs straight away, as it always did. No message boxes here: this runs under /VERYSILENT
// for every in-app, Store and winget upgrade.
function TryGracefulClose(): Boolean;
var
  Msg: Cardinal;
  I, Count, Posted: Integer;
  Handles: array of THandle;
  Started, ElapsedMs: Longword;
  AllGone: Boolean;
  WaitCount: Integer;
begin
  Result := False;
  Msg := WinRegisterWindowMessage('{#ShutdownMessageName}');
  if Msg = 0 then
  begin
    Log('Graceful close: RegisterWindowMessage failed (error ' + IntToStr(DLLGetLastError()) + ').');
    Exit;
  end;

  SetArrayLength(ShutdownWnds, 0);
  SetArrayLength(ShutdownPids, 0);
  WinEnumWindows(CreateCallback(@CollectShutdownTarget), 0);
  Count := GetArrayLength(ShutdownWnds);
  if Count = 0 then
  begin
    Log('Graceful close: no window marked for shutdown (a version without the channel, or not up yet).');
    Exit;
  end;

  // Open each process BEFORE posting, so the handle is the instance we asked even if it exits at once.
  SetArrayLength(Handles, Count);
  Posted := 0;
  for I := 0 to Count - 1 do
  begin
    Handles[I] := WinOpenProcess($00100000 {SYNCHRONIZE}, 0, ShutdownPids[I]);
    if WinPostMessage(ShutdownWnds[I], Msg, 0, 0) <> 0 then
    begin
      Posted := Posted + 1;
      Log('Graceful close: asked pid ' + IntToStr(ShutdownPids[I]) + ' (HWND ' + IntToStr(ShutdownWnds[I]) + ') to quit.');
    end
    else
      Log('Graceful close: PostMessage to pid ' + IntToStr(ShutdownPids[I]) + ' failed (error ' + IntToStr(DLLGetLastError()) + ').');
  end;

  AllGone := False;
  Started := WinGetTickCount();
  if Posted > 0 then
  begin
    Log('Graceful close: waiting up to {#ShutdownTimeoutSec} s for the app to exit.');
    repeat
      AllGone := True;
      for I := 0 to Count - 1 do
      begin
        if Handles[I] <> 0 then
        begin
          if WinWaitForSingleObject(Handles[I], 0) <> 0 {WAIT_OBJECT_0} then
            AllGone := False;
        end
        else if WinIsWindow(ShutdownWnds[I]) <> 0 then
          // Could not open the process: an exited process owns no windows, so watch the window.
          AllGone := False;
      end;
      ElapsedMs := WinGetTickCount() - Started;
      if not AllGone then
        Sleep(200);
    until AllGone or (ElapsedMs >= {#ShutdownTimeoutSec} * 1000);
  end;

  for I := 0 to Count - 1 do
    if Handles[I] <> 0 then
      CloseHandle(Handles[I]);

  if not AllGone then
  begin
    Log('Graceful close: app still running after ' + IntToStr((WinGetTickCount() - Started) div 1000) + ' s.');
    Exit;
  end;
  Log('Graceful close: app exited after ' + IntToStr(WinGetTickCount() - Started) + ' ms.');

  // The process has ended; the mutex goes with it, so this loop is only a safety margin.
  WaitCount := 0;
  while IsAppRunning() and (WaitCount < 10) do
  begin
    Sleep(300);
    WaitCount := WaitCount + 1;
  end;
  Result := not IsAppRunning();
end;

function CloseNetSpeedTray(): Boolean;
var
  ResultCode: Integer;
  KillAttempts: Integer;
begin
  Result := True;

  if not IsAppRunning() then
    Exit;

  Log('NetSpeedTray is running, asking it to close gracefully...');
  if TryGracefulClose() then
  begin
    Log('NetSpeedTray closed gracefully');
    Exit;
  end;

  // No /T: it kills the whole process TREE, and when the in-app updater starts this installer the
  // installer can be a descendant of the app - so /T made it kill itself mid-install. /IM alone
  // already ends every process with that name, which is all this ever needed.
  Log('Graceful close did not finish, falling back to taskkill on the EXE...');
  KillAttempts := 0;
  while (KillAttempts < 3) and IsAppRunning() do
  begin
    if Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#MyAppExeName}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      Log('taskkill (attempt ' + IntToStr(KillAttempts + 1) + ') executed with exit code: ' + IntToStr(ResultCode));
      Sleep(2000);
    end
    else
    begin
      Log('Failed to execute taskkill (attempt ' + IntToStr(KillAttempts + 1) + ')');
      Result := False;
      Exit;
    end;
    KillAttempts := KillAttempts + 1;
  end;

  if IsAppRunning() then
  begin
    Log('NetSpeedTray still running after max taskkill attempts');
    Result := False;
  end
  else
  begin
    Log('All NetSpeedTray processes closed');
  end;
end;

function InitializeSetup(): Boolean;
begin
  if IsAppRunning() then
  begin
    // SuppressibleMsgBox, not MsgBox: a plain MsgBox is NOT suppressed by /SUPPRESSMSGBOXES, so a
    // silent install (Microsoft Store, winget upgrade) over a running app hung on this dialog.
    // Silent default = OK = close the app and carry on, which is what an upgrade must do.
    if SuppressibleMsgBox('{#MyAppName} is currently running and needs to be closed to continue installation.'#13#10#13#10'Click OK to automatically close it, or Cancel to exit the installer.', mbConfirmation, MB_OKCANCEL, IDOK) = IDOK then
    begin
      if not CloseNetSpeedTray() then
      begin
        SuppressibleMsgBox('Failed to close {#MyAppName}.'#13#10'Please close it manually and try again.', mbError, MB_OK, IDOK);
        Result := False;
        Exit;
      end;
    end
    else
    begin
      Result := False;
      Exit;
    end;
  end;
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  if IsAppRunning() then
  begin
    if SuppressibleMsgBox('{#MyAppName} is currently running and needs to be closed to continue uninstallation.'#13#10#13#10'Click OK to automatically close it, or Cancel to exit the uninstaller.', mbConfirmation, MB_OKCANCEL, IDOK) = IDOK then
    begin
      if not CloseNetSpeedTray() then
      begin
        SuppressibleMsgBox('Failed to close {#MyAppName}.'#13#10'Please close it manually and try again.', mbError, MB_OK, IDOK);
        Result := False;
        Exit;
      end;
    end
    else
    begin
      Result := False;
      Exit;
    end;
  end;
  
  DeleteUserData := False; 
  if UnInstallSilent() and (ExpandConstant('{param:PURGE|false}') = 'true') then
  begin
    DeleteUserData := True;
  end
  else if not UnInstallSilent() then
  begin
    if MsgBox('Do you want to delete all user settings, history, and log files?'#13#10#13'This action cannot be undone.', mbConfirmation, MB_YESNO) = IDYES then
    begin
      DeleteUserData := True;
    end;
  end;
  
  Result := True;
end;

procedure DeinitializeUninstall();
begin
  if DeleteUserData then
  begin
    Log('User chose to delete personal data. Removing APPDATA directory.');
    DelTree(ExpandConstant('{userappdata}\{#MyAppName}'), True, True, True);
  end
  else
  begin
    Log('User chose to keep personal data.');
  end;
end;