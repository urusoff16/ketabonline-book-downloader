[Setup]
AppId={{98076396-16D6-40F5-B11D-1456E54E3D17}
AppName=KetabOnline Book Downloader
AppVersion=0.1.0
AppPublisher=urusoff16
AppPublisherURL=https://github.com/urusoff16/ketabonline-book-downloader
DefaultDirName={localappdata}\Programs\KetabOnlineDownloader
DefaultGroupName=KetabOnline Book Downloader
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=KetabOnline-Setup-0.1.0-x64
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\KetabOnline.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\KetabOnline\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\KetabOnline Book Downloader"; Filename: "{app}\KetabOnline.exe"
Name: "{autodesktop}\KetabOnline Book Downloader"; Filename: "{app}\KetabOnline.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\KetabOnline.exe"; Description: "Launch KetabOnline Book Downloader"; Flags: nowait postinstall skipifsilent
