; LiveLingo 的安裝程式（Inno Setup 6）。
; 用法：先用 PyInstaller 做出 dist\LiveLingo，然後 ISCC /DAppVersion=2.0.0 packaging\installer.iss
; 裝在使用者自己的資料夾，不需要系統管理員權限；會建立桌面和開始功能表捷徑，並登記到「新增或移除程式」。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B1F0E52-7C0D-4B7A-9E5B-2D7C5A3F1E90}
AppName=LiveLingo
AppVersion={#AppVersion}
AppVerName=LiveLingo {#AppVersion}
AppPublisher=LiveLingo
AppPublisherURL=https://github.com/JimboMachine/LiveLingo
DefaultDirName={localappdata}\Programs\LiveLingo
DefaultGroupName=LiveLingo
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=LiveLingo-Setup
SetupIconFile=livelingo.ico
UninstallDisplayIcon={app}\LiveLingo.exe
UninstallDisplayName=LiveLingo
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
RestartApplications=no
ShowLanguageDialog=no
VersionInfoVersion={#AppVersion}

[Languages]
#if FileExists(AddBackslash(SourcePath) + "ChineseTraditional.isl")
Name: "zhtw"; MessagesFile: "ChineseTraditional.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Japanese.isl")
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 更新時先清掉舊版的程式檔，免得新舊檔案混在一起
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\LiveLingo\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\LiveLingo"; Filename: "{app}\LiveLingo.exe"
Name: "{autodesktop}\LiveLingo"; Filename: "{app}\LiveLingo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LiveLingo.exe"; Description: "{cm:LaunchProgram,LiveLingo}"; Flags: nowait postinstall skipifsilent
; 自動更新時（安靜模式）裝完直接把 LiveLingo 再打開
Filename: "{app}\LiveLingo.exe"; Flags: nowait; Check: IsUpdate

[UninstallDelete]
; 卸載時連下載的模型、設定和紀錄一起刪乾淨
Type: filesandordirs; Name: "{localappdata}\LiveLingo"

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

procedure StopLiveLingo;
var
  i, code: Integer;
begin
  // LiveLingo 開著的時候檔案換不掉：先等它自己關（自動更新時它會馬上關），等不到就強制關閉
  for i := 1 to 40 do
  begin
    if not CheckForMutexes('LiveLingoRunning') then
      Break;
    Sleep(500);
  end;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM LiveLingo.exe', '', SW_HIDE, ewWaitUntilTerminated, code);
  Sleep(500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopLiveLingo;
  Result := '';
end;

function InitializeUninstall: Boolean;
begin
  StopLiveLingo;
  Result := True;
end;
