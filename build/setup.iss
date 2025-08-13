#define MyAppName "NetSpeedTray"
#define MyAppVersion "1.1.1.0"
#define MyAppVersionDisplay "1.1.1-beta.4"
#define MyAppPublisher "Erez C137"
#define MyAppURL "https://github.com/erez-c137/NetSpeedTray"
#define MyAppExeName "NetSpeedTray.exe"

[Setup]
AppId={{D3A32B89-C533-4F2C-9F87-23B2395B5B89}}
AppName={#MyAppName}
AppVersion={#MyAppVersionDisplay}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename=NetSpeedTray-{#MyAppVersionDisplay}-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
VersionInfoVersion={#MyAppVersion}
DisableDirPage=auto
UsePreviousAppDir=no
SetupLogging=yes
SignedUninstaller=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start with Windows"; GroupDescription: "Windows Startup"; Flags: unchecked

[Files]
Source: "..\dist\NetSpeedTray.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autostartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
Type: files; Name: "{app}\NetSpeedTray-*-Portable.exe"