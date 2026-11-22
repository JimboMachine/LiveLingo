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
; 中文的安裝文字從 Inno Setup 6.5 開始內建；比較舊的版本才用建置時另外下載的檔案
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseTraditional.isl")
Name: "zhtw"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
#elif FileExists(AddBackslash(SourcePath) + "ChineseTraditional.isl")
Name: "zhtw"; MessagesFile: "ChineseTraditional.isl"
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "zhcn"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#elif FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
Name: "zhcn"; MessagesFile: "ChineseSimplified.isl"
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

[UninstallRun]
; 大檔案（模型、翻譯引擎）搬到別的磁碟的話，那邊的也一起刪。只刪有 LiveLingo 記號檔的資料夾，不會動到別的東西
Filename: "{app}\LiveLingo.exe"; Parameters: "--remove-store"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveMovedFiles"

[UninstallDelete]
; 卸載時連下載的模型、設定和紀錄一起刪乾淨
Type: filesandordirs; Name: "{localappdata}\LiveLingo"

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

function LiveLingoRunning: Boolean;
begin
  Result := CheckForMutexes('LiveLingoRunning');
end;

procedure StopLiveLingo(WithChildren: Boolean);
var
  i, code: Integer;
  args: String;
begin
  // LiveLingo 開著的時候檔案換不掉：先等它自己關（自動更新時它會馬上關），等不到才強制關閉。
  for i := 1 to 60 do
  begin
    if not LiveLingoRunning then
      Break;
    Sleep(500);
  end;
  if LiveLingoRunning then
  begin
    // 自動更新時這個安裝程式是 LiveLingo 開出來的，所以不能連「它開出來的程式」一起關（會把自己也關掉）
    args := '/F /IM LiveLingo.exe';
    if WithChildren then
      args := '/F /T /IM LiveLingo.exe';
    Exec(ExpandConstant('{sys}\taskkill.exe'), args, '', SW_HIDE, ewWaitUntilTerminated, code);
  end;
  Sleep(1000);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopLiveLingo(False);
  Result := '';
end;

function InitializeUninstall: Boolean;
begin
  StopLiveLingo(True);
  Result := True;
end;
