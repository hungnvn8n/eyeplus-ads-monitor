; Inno Setup script — tạo installer .exe cho Windows
; Cài Inno Setup: https://jrsoftware.org/isdl.php (free)
; Build installer: compile file này (mở trong Inno Setup Compiler, F9)
; Hoặc command line: iscc.exe win_installer.iss
;
; Yêu cầu: build dist\EyePlusAds.exe trước qua build_win.bat
; Output: installer\EyePlusAds-Setup-1.0.0.exe

#define MyAppName "EyePlus Ads"
#define MyAppVersion "1.0.23"
#define MyAppPublisher "Eye Plus"
#define MyAppURL "https://eyeplus.vn"
#define MyAppExeName "EyePlusAds.exe"

[Setup]
AppId={{4F8C2A1B-E5D6-4B7A-9F1E-3A8D5C7B2E9F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\EyePlusAds
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
InfoBeforeFile=
InfoAfterFile=
OutputDir=..\installer
OutputBaseFilename=EyePlusAds-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "vietnamese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo shortcut trên Desktop"; GroupDescription: "Tùy chọn:"
Name: "startupicon"; Description: "Tự chạy khi khởi động Windows"; GroupDescription: "Tùy chọn:"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\DIST_README.txt"; DestDir: "{app}"; DestName: "HƯỚNG DẪN.txt"; Flags: ignoreversion isreadme
Source: "..\assets\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Hướng dẫn"; Filename: "{app}\HƯỚNG DẪN.txt"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autostartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Mở {#MyAppName} ngay"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Không xóa user data khi uninstall (giữ .env, cache, log)
; User muốn xóa data: rm -rf %APPDATA%\EyePlusAds
