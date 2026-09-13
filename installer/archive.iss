#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef Payload
  #error Payload is required
#endif
#ifndef Output
  #error Output is required
#endif
[Setup]
AppId={{B844DD59-3BCE-4320-9DBA-53D7085CD773}
AppName=Архив Chatterino by Mist1X
AppVersion={#AppVersion}
AppPublisher=Mist1X
AppPublisherURL=https://github.com/Mist10X/Chatterino-Archive-by-Mist1X
AppSupportURL=https://github.com/Mist10X/Chatterino-Archive-by-Mist1X/issues
AppUpdatesURL=https://github.com/Mist10X/Chatterino-Archive-by-Mist1X/releases
DefaultDirName={localappdata}\Programs\ChatterinoArchive-by-Mist1X
DefaultGroupName=Архив Chatterino by Mist1X
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupLogging=yes
Compression=lzma2
SolidCompression=yes
OutputDir={#Output}
OutputBaseFilename=ChatterinoArchive-Setup-{#AppVersion}-x64
UninstallFilesDir={app}\uninstall
UninstallDisplayIcon={app}\app\ChatterinoArchive-by-Mist1X.exe
CloseApplications=yes
CloseApplicationsFilter=ChatterinoArchive-by-Mist1X.exe
RestartApplications=no
LicenseFile=..\LICENSE
VersionInfoVersion={#AppVersion}.0
[Languages]
Name: russian; MessagesFile: compiler:Languages\Russian.isl
Name: english; MessagesFile: compiler:Default.isl
[Tasks]
Name: desktopicon; Description: Создать ярлык на рабочем столе; Flags: unchecked
[Files]
Source: {#Payload}\*; DestDir: {app}\app; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: {group}\Архив Chatterino by Mist1X; Filename: {app}\app\ChatterinoArchive-by-Mist1X.exe; WorkingDir: {app}\app
Name: {autodesktop}\Архив Chatterino by Mist1X; Filename: {app}\app\ChatterinoArchive-by-Mist1X.exe; WorkingDir: {app}\app; Tasks: desktopicon
[Run]
Filename: {app}\app\ChatterinoArchive-by-Mist1X.exe; Description: Запустить архив; Flags: nowait postinstall skipifsilent runasoriginaluser
