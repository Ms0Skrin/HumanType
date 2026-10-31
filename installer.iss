; Inno Setup 6 script: packs dist\HumanType into HumanType-Setup-<version>.exe.
; Installs for the current user only (no admin rights), into %LOCALAPPDATA%\Programs\HumanType.
; Build through build.bat, which passes the version; by hand: ISCC /DAppVersion=0.3 installer.iss

#ifndef AppVersion
  #define AppVersion "0.0"
#endif

[Setup]
AppId={{6F3B2A1C-9D4E-4C7B-A8E2-5B1D0C9F7A31}
AppName=HumanType
AppVersion={#AppVersion}
AppPublisher=HumanType
DefaultDirName={localappdata}\Programs\HumanType
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=HumanType-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\HumanType.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The app holds this mutex while it runs: setup asks to close it before replacing files.
AppMutex=HumanTypeTrayApp
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked

[Files]
Source: "dist\HumanType\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\HumanType"; Filename: "{app}\HumanType.exe"
Name: "{userdesktop}\HumanType"; Filename: "{app}\HumanType.exe"; Tasks: desktopicon

[Registry]
; The app writes its autostart entry itself; this only removes it on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "HumanType"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\HumanType.exe"; Description: "Запустить HumanType"; Flags: nowait postinstall skipifsilent
; An update from the app's menu installs silently: start the new version right after.
Filename: "{app}\HumanType.exe"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM HumanType.exe /F"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
; Settings and clipboard history live in %LOCALAPPDATA%\HumanType; they are removed with the app.
Type: filesandordirs; Name: "{localappdata}\HumanType"
