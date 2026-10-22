; NetSpeedTray Installer Script

#define MyAppName "NetSpeedTray"
#define MyAppPublisher "Erez C137"
#define MyAppURL "https://github.com/erez-c137/NetSpeedTray"
#define MyAppExeName "NetSpeedTray.exe"
; constants.app.LEGACY_MUTEX_NAME - what every build before 2.2.0 holds. Since 2.2.0 the app's own
; mutex is per user (Global\NetSpeedTray_Instance_<SID>), which Setup cannot name, so it also looks
; for the process itself (AnyAppProcess). 2.2.0+ also holds this legacy name while it is free.
#define MyLegacyAppMutex "Global\NetSpeedTray_SingleInstanceMutex"
; constants.app.MUTEX_NAME_PREFIX: + the user's SID = a 2.2.0+ instance's own mutex.
#define MyAppMutexPrefix "Global\NetSpeedTray_Instance_"
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
; An upgrade installs where the app already is (2.2.0). This said "no" from the per-user 1.0.6 era,
; which sent every silent upgrade (in-app, winget, Store) of a custom-folder install to
; {autopf}\NetSpeedTray instead, and left the old copy behind - its pinned shortcut or Run entry then
; started the OLD version against a database the new one had already migrated. Inno reads the
; previous folder from this AppId's HKLM uninstall key; a fresh install still offers the default
; folder (DisableDirPage=auto shows the page then and skips it on an upgrade).
UsePreviousAppDir=yes
SetupLogging=yes
UninstallDisplayName={#MyAppName}
RestartIfNeededByRun=no
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll
; Must match constants.app.LEGACY_MUTEX_NAME so Inno detects a running app on (auto-)upgrade;
; InitializeSetup's IsAppRunning also finds 2.2.0+ instances by process.
AppMutex={#MyLegacyAppMutex}

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
; shell's own (medium) integrity whatever Setup's token is.
;
; There is deliberately NO direct launch when no shell is running (2.2.0). It started the installed
; exe with Setup's own token: in an Intune/SCCM deployment that is SYSTEM in session 0, which then
; held the Global\ mutex so every user's own launch exited quietly until a reboot; and in the in-app
; update path that token is elevated, so a momentarily missing Shell_TrayWnd (Explorer restarting)
; brought the app back as admin. Without a shell the app simply starts the next time the user
; starts it or signs in (LaunchSilentViaShell logs which case applied).
Filename: "{win}\explorer.exe"; Parameters: """{app}\{#MyAppExeName}"""; Flags: nowait runasoriginaluser; Check: LaunchSilentViaShell

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
// support functions. Setup is a 32-bit process, so HWND / LPARAM fit in Longint - and the
// WTSQuerySessionInformationW buffer pointer below is a Longword for the same reason. If a future
// Inno Setup builds a 64-bit Setup, every pointer here must widen; release.yml pins the version.
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
function WinGetCurrentProcessId(): Longword;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
function WinProcessIdToSessionId(dwProcessId: Longword; var pSessionId: Longword): Integer;
  external 'ProcessIdToSessionId@kernel32.dll stdcall';
function WinWTSQuerySessionInformation(hServer: Longword; SessionId: Longword; InfoClass: Integer;
  var ppBuffer: Longword; var pBytesReturned: Longword): Integer;
  external 'WTSQuerySessionInformationW@wtsapi32.dll stdcall';
procedure WinWTSFreeMemory(pMemory: Longword);
  external 'WTSFreeMemory@wtsapi32.dll stdcall';
function WinLstrcpyn(lpString1: string; lpString2: Longword; iMaxLength: Integer): Longword;
  external 'lstrcpynW@kernel32.dll stdcall';
function WinGetUserNameEx(NameFormat: Integer; lpNameBuffer: string; var nSize: Longword): Integer;
  external 'GetUserNameExW@secur32.dll stdcall';
function WinLookupAccountName(lpSystemName: Longword; lpAccountName: string; Sid: AnsiString;
  var cbSid: Longword; ReferencedDomainName: string; var cchReferencedDomainName: Longword;
  var peUse: Integer): Integer;
  external 'LookupAccountNameW@advapi32.dll stdcall';
function WinConvertSidToStringSid(Sid: AnsiString; var StringSid: Longword): Integer;
  external 'ConvertSidToStringSidW@advapi32.dll stdcall';
function WinLstrlen(lpString: Longword): Integer;
  external 'lstrlenW@kernel32.dll stdcall';
function WinLocalFree(hMem: Longword): Longword;
  external 'LocalFree@kernel32.dll stdcall';

// --- Helper Functions ---
function ShellIsRunning(): Boolean;
begin
  Result := FindWindowByClassName('Shell_TrayWnd') <> 0;
end;

// Setup runs where no user is: session 0 (services - Intune's and SCCM's agents run there as SYSTEM)
// or as the SYSTEM account itself. Starting the app from there would run it as SYSTEM, holding the
// Global\ single-instance mutex against every real user until a reboot.
function InServiceContext(): Boolean;
var
  SessionId: Longword;
begin
  SessionId := 0;
  Result := False;
  if WinProcessIdToSessionId(WinGetCurrentProcessId(), SessionId) <> 0 then
    Result := SessionId = 0;
  if CompareText(GetUserNameString(), 'SYSTEM') = 0 then
    Result := True;
end;

// One string of this session's information (WTSQuerySessionInformationW), or '' if unreadable.
// WTS_CURRENT_SERVER_HANDLE = 0, WTS_CURRENT_SESSION = $FFFFFFFF; InfoClass WTSUserName = 5,
// WTSDomainName = 7.
function SessionInfoString(InfoClass: Integer): string;
var
  Buffer: Longword;
  Bytes: Longword;
  Text: string;
begin
  Result := '';
  Buffer := 0;
  Bytes := 0;
  if WinWTSQuerySessionInformation(0, $FFFFFFFF, InfoClass, Buffer, Bytes) = 0 then
    Exit;
  try
    if (Buffer <> 0) and (Bytes > 2) then
    begin
      Text := StringOfChar(' ', Bytes div 2);
      WinLstrcpyn(Text, Buffer, Bytes div 2);
      Result := Trim(Copy(Text, 1, Pos(#0, Text + #0) - 1));
    end;
  finally
    WinWTSFreeMemory(Buffer);
  end;
end;

// DOMAIN\user signed in to this session, or '' if unreadable.
function SessionAccount(): string;
var
  User: string;
begin
  Result := '';
  User := SessionInfoString(5);
  if User <> '' then
    Result := SessionInfoString(7) + '\' + User;
end;

// DOMAIN\user Setup itself runs as (GetUserNameExW, NameSamCompatible = 2), or '' if unreadable.
function SetupAccount(): string;
var
  Size: Longword;
  Text: string;
begin
  Result := '';
  Size := 512;
  Text := StringOfChar(' ', Size);
  if WinGetUserNameEx(2, Text, Size) <> 0 then
    Result := Trim(Copy(Text, 1, Size));
end;

// Setup runs as a DIFFERENT account than the one signed in to this session: a standard user who
// typed an administrator's credentials at the UAC prompt (over-the-shoulder elevation). The in-app
// updater starts Setup already elevated, so Inno's "original user" is that administrator too, and
// handing explorer.exe a launch from the administrator's token is not proven to come back as the
// signed-in user. Not relaunching is the safe answer: the app returns at their next sign-in or from
// the Start menu, never under the administrator's account (which would also write that account's
// %APPDATA%, not theirs). Compared as DOMAIN\user, so two accounts with the same name in different
// domains (a local and a domain "admin") are told apart; both are logged every time.
function SetupRunsAsAnotherUser(): Boolean;
var
  SessionUser, SetupUser: string;
begin
  SessionUser := SessionAccount();
  SetupUser := SetupAccount();
  Log('Session user: [' + SessionUser + ']; Setup runs as: [' + SetupUser + '].');
  Result := (SessionUser <> '') and (SetupUser <> '') and (CompareText(SessionUser, SetupUser) <> 0);
  if (SessionUser = '') or (SetupUser = '') then
    Log('Could not read both accounts; assuming Setup runs as the signed-in user.');
end;

function LaunchSilentViaShell(): Boolean;
begin
  Result := False;
  if not WizardSilent() then
    Exit;
  if SetupRunsAsAnotherUser() then
    Log('Silent install: Setup runs as ' + SetupAccount() + ', not as the user signed in to this session - not starting {#MyAppName} under that account; it starts at their next sign-in or from the Start menu.')
  else if InServiceContext() then
    Log('Silent install: running as SYSTEM or in session 0 - not starting {#MyAppName}; each user starts it at their next sign-in or from the Start menu.')
  else if not ShellIsRunning() then
    Log('Silent install: no shell running - not starting {#MyAppName}; it starts at the next sign-in or from the Start menu.')
  else
  begin
    Result := True;
    Log('Silent install: starting {#MyAppName} through the shell, so it runs unelevated (no Finish page to offer it).');
  end;
end;

function BoolToStr(Value: Boolean): string;
begin
  if Value then
    Result := 'True'
  else
    Result := 'False';
end;

function MutexExists(Name: string): Boolean;
var
  MutexHandle: THandle;
begin
  MutexHandle := OpenMutex($00100000, False, Name);
  Result := MutexHandle <> 0;
  if Result then
    CloseHandle(MutexHandle)
  else
    // ERROR_ACCESS_DENIED: it exists, but its DACL does not admit us (an object only exists to be
    // denied) - e.g. created by an elevated instance or another account.
    Result := DLLGetLastError() = 5;
end;

// The string SID (S-1-5-21-...) of a DOMAIN\user account, or '' if it cannot be resolved.
function AccountSid(Account: string): string;
var
  Sid: AnsiString;
  SidSize, DomainSize: Longword;
  Domain: string;
  Use: Integer;
  StringSid: Longword;
  Len: Integer;
  Text: string;
begin
  Result := '';
  if Account = '' then
    Exit;
  SidSize := 68;                                  // SECURITY_MAX_SID_SIZE
  SetLength(Sid, SidSize);
  DomainSize := 256;
  Domain := StringOfChar(' ', DomainSize);
  Use := 0;
  if WinLookupAccountName(0, Account, Sid, SidSize, Domain, DomainSize, Use) = 0 then
    Exit;
  StringSid := 0;
  if WinConvertSidToStringSid(Sid, StringSid) = 0 then
    Exit;
  try
    Len := WinLstrlen(StringSid);
    if Len > 0 then
    begin
      Text := StringOfChar(' ', Len + 1);
      WinLstrcpyn(Text, StringSid, Len + 1);
      Result := Copy(Text, 1, Len);
    end;
  finally
    WinLocalFree(StringSid);
  end;
end;

// The 2.2.0+ per-user instance mutex of the user signed in to Setup's session
// (constants.app.MUTEX_NAME_PREFIX + their SID). A fallback for when WMI cannot list processes.
function SessionUserInstanceRunning(): Boolean;
var
  Sid: string;
begin
  Result := False;
  Sid := AccountSid(SessionAccount());
  if Sid <> '' then
    Result := MutexExists('{#MyAppMutexPrefix}' + Sid)
  else
    Log('Could not resolve the session user''s SID; relying on the process check.');
end;

// Any NetSpeedTray.exe in ANY session. Since 2.2.0 the app's mutex is per user (its name carries the
// user's SID, which Setup cannot know), and another signed-in user's instance holds the same files
// open. Setup is elevated, so WMI sees every process.
function AnyAppProcess(): Boolean;
var
  Locator, Service, Procs: Variant;
begin
  Result := False;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Procs := Service.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE Name = ''{#MyAppExeName}''');
    Result := Procs.Count > 0;
  except
    Log('Process check failed: ' + GetExceptionMessage);
  end;
end;

function IsAppRunning(): Boolean;
begin
  Result := MutexExists('{#MyLegacyAppMutex}') or SessionUserInstanceRunning() or AnyAppProcess();
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

// UsePreviousAppDir=yes upgrades in place - but the previous folder can be on a drive that is no
// longer there (an unplugged USB disk, a removed or re-lettered volume). A silent upgrade there
// would fail with nothing on screen, so a LOCAL drive that is gone sends it to the default folder.
//
// A NETWORK location is different, and is kept: a mapped drive letter belongs to the user's own
// logon session, so the ELEVATED Setup usually cannot see it even though it is there (and a share
// can be briefly unreachable). Falling back would split the app in two - a new copy in Program
// Files and the old one on the share, still started by the user's pins - the very problem
// UsePreviousAppDir=yes fixed. Keeping it fails loudly instead when the share really is
// unreachable (Setup reports the error; the in-app updater says the update did not complete and
// opens the download page), which the user can act on. Both cases are logged.
// Runs in silent mode too: the wizard form exists, it is just not shown.
var
  MovedFromDir: string;

function PreviousDriveMissing(Dir: string): Boolean;
var
  Drive: string;
begin
  Drive := ExtractFileDrive(Dir);
  Result := (Drive = '') or not DirExists(AddBackslash(Drive));
end;

// Whether the user signed in to this session has mapped Letter (<their hive>\Network\<letter>).
// HKCU is Setup's own account: the session user's hive when they elevated themselves, but the
// ADMINISTRATOR's under an over-the-shoulder elevation (a standard user typing an admin's
// credentials) - so then the session user's hive is read from HKU\<their SID>, which is loaded while
// they are signed in. If their SID cannot be resolved, Setup's own HKCU is the fallback. The hive
// consulted is logged.
function SessionUserMappedDrive(Letter: string): Boolean;
var
  Sid: string;
begin
  if SetupRunsAsAnotherUser() then
  begin
    Sid := AccountSid(SessionAccount());
    if Sid <> '' then
    begin
      Result := RegKeyExists(HKU, Sid + '\Network\' + Letter);
      Log('Mapped-drive check: read HKU\' + Sid + '\Network\' + Letter + ' (the session user''s hive; Setup runs as another account): ' + BoolToStr(Result) + '.');
      Exit;
    end;
    Log('Mapped-drive check: could not resolve the session user''s SID; falling back to Setup''s own HKCU.');
  end;
  Result := RegKeyExists(HKCU, 'Network\' + Letter);
  Log('Mapped-drive check: read HKCU\Network\' + Letter + ' (Setup''s own hive): ' + BoolToStr(Result) + '.');
end;

// A UNC path, or a drive letter the session user has mapped.
function IsNetworkLocation(Dir: string): Boolean;
var
  Drive: string;
begin
  Drive := ExtractFileDrive(Dir);
  Result := (Copy(Drive, 1, 2) = '\\') or
            ((Length(Drive) = 2) and (Drive[2] = ':') and SessionUserMappedDrive(Drive[1]));
end;

procedure InitializeWizard();
var
  Dir, Fallback: string;
begin
  MovedFromDir := '';
  Dir := WizardForm.DirEdit.Text;
  if (Dir <> '') and PreviousDriveMissing(Dir) then
  begin
    if IsNetworkLocation(Dir) then
      Log('The previous install folder ' + Dir + ' is on a network location Setup cannot see right now; keeping it (installing elsewhere would leave two copies).')
    else
    begin
      Fallback := ExpandConstant('{autopf}\{#MyAppName}');
      Log('The previous install folder ' + Dir + ' is on a drive that is not available; installing to ' + Fallback + ' instead.');
      WizardForm.DirEdit.Text := Fallback;
      MovedFromDir := Dir;
    end;
  end;
end;

// Interactive installs say so on the Finish page when the folder moved.
procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and (MovedFromDir <> '') then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
      '{#MyAppName} was installed to ' + WizardDirValue() + ' because its previous folder (' +
      MovedFromDir + ') is on a drive that is not connected. Shortcuts point to the new folder.';
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