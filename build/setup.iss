; NetSpeedTray Installer Script
; Version 1.1.7

#define MyAppName "NetSpeedTray"
#define MyAppVersion "1.1.7.0"
#define MyAppVersionDisplay "1.1.7"
#define MyAppPublisher "Erez C137"
#define MyAppURL "https://github.com/erez-c137/NetSpeedTray"
#define MyAppExeName "NetSpeedTray.exe"
#define MyAppMutex "Global\NetSpeedTray_Single_Instance_Mutex"
#define MyAppId "{{D3A32B89-C533-4F2C-9F87-23B2395B5B89}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersionDisplay}
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
OutputBaseFilename=NetSpeedTray-{#MyAppVersionDisplay}-Setup
VersionInfoVersion={#MyAppVersion}
AllowNoIcons=yes
DisableDirPage=auto
UsePreviousAppDir=no
SetupLogging=yes
UninstallDisplayName={#MyAppName}
RestartIfNeededByRun=no
; Enable auto-force close for upgrades (graceful first via Restart Manager, then kill)
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll
; SignedUninstaller=yes ; Uncomment when you have a code signing certificate

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\NetSpeedTray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; --- UninstallDelete section ---
[UninstallDelete]
; This section provides explicit instructions for the uninstaller.
; It ensures that orphaned files from previous versions are removed.
Type: files; Name: "{autodesktop}\{#MyAppName}.lnk"

[Code]
var
  DeleteUserData: Boolean;

// --- External Windows API Function Prototypes ---
function OpenMutex(dwDesiredAccess: LongWord; bInheritHandle: Boolean; lpName: string): THandle;
  external 'OpenMutexW@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function FindWindow(lpClassName, lpWindowName: string): HWND;
  external 'FindWindowW@user32.dll stdcall';
function PostMessage(hWnd: HWND; Msg: Cardinal; wParam, lParam: Longint): BOOL;
  external 'PostMessageW@user32.dll stdcall';
const
  WM_CLOSE = $0010;  // WinAPI constant for close message

// --- Helper Functions ---
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

function CloseNetSpeedTray(): Boolean;
var
  ResultCode: Integer;
  Hwnd: HWND;
  WaitCount: Integer;
  KillAttempts: Integer;
begin
  Result := True;
  
  if not IsAppRunning() then
    Exit;
    
  Log('NetSpeedTray is running (likely two processes), attempting to close gracefully...');
  
  // Find the main window (on the child process) by its hidden title
  Hwnd := FindWindow('', 'NetSpeedTrayHidden');
  if Hwnd <> 0 then
  begin
    Log('Found NetSpeedTray window (child process), sending WM_CLOSE...');
    PostMessage(Hwnd, WM_CLOSE, 0, 0);
    
    // Wait up to 5 seconds for graceful close (check every 500ms)
    WaitCount := 0;
    while (WaitCount < 10) and (FindWindow('', 'NetSpeedTrayHidden') <> 0) do
    begin
      Sleep(500);
      WaitCount := WaitCount + 1;
    end;
    
    // If still running (parent may linger), force kill the EXE and tree
    if IsAppRunning() or (FindWindow('', 'NetSpeedTrayHidden') <> 0) then
    begin
      Log('Graceful close incomplete (parent/child lingering), using taskkill on EXE/tree...');
      KillAttempts := 0;
      while (KillAttempts < 3) and IsAppRunning() do
      begin
        if Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#MyAppExeName}" /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
        begin
          Log('taskkill (attempt ' + IntToStr(KillAttempts + 1) + ') executed with exit code: ' + IntToStr(ResultCode));
          Sleep(2000);  // Extra wait for parent to exit after child
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
    end
    else
    begin
      Log('NetSpeedTray closed gracefully');
    end;
  end
  else
  begin
    Log('Could not find NetSpeedTray window, falling back to taskkill on EXE/tree...');
    KillAttempts := 0;
    while (KillAttempts < 3) and IsAppRunning() do
    begin
      if Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#MyAppExeName}" /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
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
end;

// --- Installer/Uninstaller Event Functions ---
function InitializeSetup(): Boolean;
begin
  // No early close; CloseApplications=force handles during file replace for upgrades
  // But if running and prompt needed (rare), handle here
  if IsAppRunning() then
  begin
    if MsgBox('{#MyAppName} is currently running and needs to be closed to continue installation.'#13#10#13#10'Click OK to automatically close it, or Cancel to exit the installer.', mbConfirmation, MB_OKCANCEL) = IDOK then
    begin
      if not CloseNetSpeedTray() then
      begin
        MsgBox('Failed to close {#MyAppName}.'#13#10'Please close it manually and try again.', mbError, MB_OK);
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
    if MsgBox('{#MyAppName} is currently running and needs to be closed to continue uninstallation.'#13#10#13#10'Click OK to automatically close it, or Cancel to exit the uninstaller.', mbConfirmation, MB_OKCANCEL) = IDOK then
    begin
      if not CloseNetSpeedTray() then
      begin
        MsgBox('Failed to close {#MyAppName}.'#13#10'Please close it manually and try again.', mbError, MB_OK);
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