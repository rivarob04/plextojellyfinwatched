; Inno Setup script for the JellyPlex-Watched desktop app.
; Built by .github/workflows/windows-app.yml:  iscc /DAppVersion=1.0.0 installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E1B7C2A-4F1D-4B8E-9C61-2F7B0A5D3E19}
AppName=JellyPlex-Watched
AppVersion={#AppVersion}
AppPublisher=rivarob04
AppPublisherURL=https://github.com/rivarob04/plextojellyfinwatched
AppSupportURL=https://github.com/rivarob04/plextojellyfinwatched/issues
DefaultDirName={autopf}\JellyPlex-Watched
DefaultGroupName=JellyPlex-Watched
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=JellyPlex-Watched-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\JellyPlex-Watched.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\JellyPlex-Watched\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\JellyPlex-Watched"; Filename: "{app}\JellyPlex-Watched.exe"
Name: "{group}\Open config folder"; Filename: "{userappdata}\JellyPlex-Watched"
Name: "{group}\Uninstall JellyPlex-Watched"; Filename: "{uninstallexe}"
Name: "{autodesktop}\JellyPlex-Watched"; Filename: "{app}\JellyPlex-Watched.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\JellyPlex-Watched.exe"; Description: "{cm:LaunchProgram,JellyPlex-Watched}"; Flags: nowait postinstall skipifsilent

[Dirs]
Name: "{userappdata}\JellyPlex-Watched"
