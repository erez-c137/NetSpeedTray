#define MyAppName "NetSpeedTray"
#define MyAppVersion "1.1.1.0"
#define MyAppVersionDisplay "1.1.1"
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

[Files]
; Grab all files from the output directory of PyInstaller
Source: "..\dist\NetSpeedTray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
Type: files; Name: "{app}\NetSpeedTray-*-Portable.exe"