

#define MyAppName "NetSpeedTray"
#define MyAppVersion "1.0.5.2"
#define MyAppVersionDisplay "1.0.5-Beta2"
#define MyAppPublisher "Erez C137"
#define MyAppURL "https://github.com/erez-c137/NetSpeedTray"
#define MyAppExeName "NetSpeedTray-" + MyAppVersionDisplay + "-Portable.exe"

[Setup]
AppId={{D3A32B89-C533-4F2C-9F87-23B2395B5B89}}
AppName={#MyAppName}
AppVersion={#MyAppVersionDisplay}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={userappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename=NetSpeedTray-{#MyAppVersionDisplay}-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start with Windows"; GroupDescription: "Windows Startup"; Flags: unchecked

[Files]
Source: "..\dist\NetSpeedTray-{#MyAppVersionDisplay}\NetSpeedTray-{#MyAppVersionDisplay}-Portable.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\NetSpeedTray.ico"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]

Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autostartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\NetSpeedTray.ico"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent