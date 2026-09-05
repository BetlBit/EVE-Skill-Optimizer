#define MyAppName "EVE Skill Optimizer"
#ifndef MyAppVersion
#define MyAppVersion "0.9.10"
#endif
#define MyAppPublisher "4CRABS"
#define MyAppExeName "EVE Skill Optimizer.exe"

[Setup]
AppId={{F1D8D6A0-8F72-48E2-AE10-E7C31E8C07C1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\EVE Skill Optimizer
DefaultGroupName=EVE Skill Optimizer
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=EVE-Skill-Optimizer-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\EVE Skill Optimizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EVE Skill Optimizer"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\EVE Skill Optimizer"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch EVE Skill Optimizer"; Flags: nowait postinstall skipifsilent

; User/runtime data is intentionally stored outside {app} under
; %LOCALAPPDATA%\EveSkillOptimizer and is preserved on uninstall.
