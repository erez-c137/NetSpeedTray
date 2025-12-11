; NetSpeedTray Installer Script

#define MyAppName "NetSpeedTray"
#define MyAppPublisher "Erez C137"
#define MyAppURL "https://github.com/erez-c137/NetSpeedTray"
#define MyAppExeName "NetSpeedTray.exe"
#define MyAppMutex "Global\NetSpeedTray_Single_Instance_Mutex"
#define MyAppId "{{D3A32B89-C533-4F2C-9F87-23B2395B5B89}}"

; --- DYNAMIC VERSIONING ---
; If AppVersion is NOT defined (e.g., manual compile without build.bat), use a default.
; When running via build.bat, the /DAppVersion="x.x.x" flag overrides this.
#ifndef AppVersion
  #define AppVersion "0.0.0" 
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}

; Uses the version passed from build.bat (e.g., 1.1.8)
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

; Filename will now be NetSpeedTray-1.1.8-Setup
OutputBaseFilename=NetSpeedTray-{#AppVersion}-x64-Setup

; This sets the Windows Property "File Version"
VersionInfoVersion={#AppVersion}

DisableDirPage=auto
UsePreviousAppDir=no
SetupLogging=yes
UninstallDisplayName={#MyAppName}
RestartIfNeededByRun=no
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll

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
function FindWindow(lpClassName, lpWindowName: string): HWND;
  external 'FindWindowW@user32.dll stdcall';
function PostMessage(hWnd: HWND; Msg: Cardinal; wParam, lParam: Longint): BOOL;
  external 'PostMessageW@user32.dll stdcall';
const
  WM_CLOSE = $0010;  

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
  
  Hwnd := FindWindow('', 'NetSpeedTrayHidden');
  if Hwnd <> 0 then
  begin
    Log('Found NetSpeedTray window (child process), sending WM_CLOSE...');
    PostMessage(Hwnd, WM_CLOSE, 0, 0);
    
    WaitCount := 0;
    while (WaitCount < 10) and (FindWindow('', 'NetSpeedTrayHidden') <> 0) do
    begin
      Sleep(500);
      WaitCount := WaitCount + 1;
    end;
    
    if IsAppRunning() or (FindWindow('', 'NetSpeedTrayHidden') <> 0) then
    begin
      Log('Graceful close incomplete (parent/child lingering), using taskkill on EXE/tree...');
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

function InitializeSetup(): Boolean;
begin
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