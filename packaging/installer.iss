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
#define HasZhTW
#elif FileExists(AddBackslash(SourcePath) + "ChineseTraditional.isl")
Name: "zhtw"; MessagesFile: "ChineseTraditional.isl"
#define HasZhTW
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "zhcn"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#define HasZhCN
#elif FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
Name: "zhcn"; MessagesFile: "ChineseSimplified.isl"
#define HasZhCN
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"
#define HasKo
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Japanese.isl")
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
#define HasJa
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Spanish.isl")
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
#define HasEs
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\BrazilianPortuguese.isl")
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
#define HasPt
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\French.isl")
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
#define HasFr
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\German.isl")
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
#define HasDe
#endif
#if FileExists(AddBackslash(CompilerPath) + "Languages\Russian.isl")
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
#define HasRu
#endif
; 越南文、印尼文的安裝文字是 Inno Setup 的非官方翻譯，建置時另外下載；泰文沒有，泰文的 Windows 會看到英文
#if FileExists(AddBackslash(SourcePath) + "Vietnamese.isl")
Name: "vi"; MessagesFile: "Vietnamese.isl"
#define HasVi
#endif
#if FileExists(AddBackslash(SourcePath) + "Indonesian.isl")
Name: "id"; MessagesFile: "Indonesian.isl"
#define HasId
#endif

[CustomMessages]
; 卸載時問的那一句。沒有前綴的是預設（英文），其他語言有裝才加
KeepData=Keep your settings, glossary, taught sentences and conversation history on this PC?%n%nYes: only the program and the downloaded models are removed, and your settings are back when you install LiveLingo again.%nNo: everything is removed.
#ifdef HasZhTW
zhtw.KeepData=要把你的設定、詞彙表、教過的句子和對話歷史留在這台電腦上嗎？%n%n「是」：只刪除程式和下載的模型，之後再安裝 LiveLingo，設定都還在。%n「否」：全部刪除。
#endif
#ifdef HasZhCN
zhcn.KeepData=要把你的设置、词汇表、教过的句子和对话历史留在这台电脑上吗？%n%n“是”：只删除程序和下载的模型，以后再安装 LiveLingo，设置都还在。%n“否”：全部删除。
#endif
#ifdef HasKo
ko.KeepData=설정, 용어집, 가르친 문장, 대화 기록을 이 컴퓨터에 남겨 둘까요?%n%n「예」: 프로그램과 내려받은 모델만 삭제하며, 나중에 LiveLingo를 다시 설치하면 설정이 그대로 있습니다.%n「아니요」: 모두 삭제합니다.
#endif
#ifdef HasJa
ja.KeepData=設定、用語集、教えた文、会話の履歴をこのパソコンに残しますか？%n%n「はい」：プログラムとダウンロードしたモデルだけを削除します。あとで LiveLingo を入れ直すと設定はそのまま使えます。%n「いいえ」：すべて削除します。
#endif
#ifdef HasEs
es.KeepData=¿Conservar tus ajustes, glosario, frases enseñadas e historial de conversación en este PC?%n%nSí: solo se quitan el programa y los modelos descargados, y tus ajustes vuelven si instalas LiveLingo de nuevo.%nNo: se quita todo.
#endif
#ifdef HasPt
pt.KeepData=Manter suas configurações, glossário, frases ensinadas e histórico de conversa neste PC?%n%nSim: só o programa e os modelos baixados são removidos, e suas configurações voltam se você instalar o LiveLingo de novo.%nNão: tudo é removido.
#endif
#ifdef HasFr
fr.KeepData=Garder vos réglages, votre glossaire, vos phrases apprises et l'historique des conversations sur ce PC ?%n%nOui : seuls le programme et les modèles téléchargés sont supprimés, et vos réglages reviennent si vous réinstallez LiveLingo.%nNon : tout est supprimé.
#endif
#ifdef HasDe
de.KeepData=Einstellungen, Glossar, beigebrachte Sätze und Gesprächsverlauf auf diesem PC behalten?%n%nJa: Nur das Programm und die heruntergeladenen Modelle werden entfernt, und deine Einstellungen sind wieder da, wenn du LiveLingo neu installierst.%nNein: Alles wird entfernt.
#endif
#ifdef HasRu
ru.KeepData=Оставить на этом компьютере ваши настройки, глоссарий, выученные фразы и историю разговоров?%n%nДа: удаляются только программа и скачанные модели, а при повторной установке LiveLingo настройки вернутся.%nНет: удаляется всё.
#endif
#ifdef HasVi
vi.KeepData=Giữ lại cài đặt, danh sách từ, các câu đã dạy và lịch sử hội thoại trên máy này?%n%nCó: chỉ xóa chương trình và các mô hình đã tải, cài lại LiveLingo thì cài đặt vẫn còn.%nKhông: xóa tất cả.
#endif
#ifdef HasId
id.KeepData=Simpan pengaturan, glosarium, kalimat yang diajarkan, dan riwayat percakapan di PC ini?%n%nYa: hanya program dan model yang diunduh yang dihapus, dan pengaturanmu kembali kalau LiveLingo diinstal lagi.%nTidak: semuanya dihapus.
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

; 舊版的程式檔不在這裡刪：PrepareToInstall 先把它整個改名成 _internal.old 留著，裝好才刪，裝失敗就換回來（卸載時在 CurUninstallStepChanged 刪剩下的備份）。
; 以前用 [InstallDelete] 先刪：LiveLingo 還沒完全結束時刪不乾淨，接著換檔失敗、安裝中止，留下一個打不開的程式（2.4.2 那次）。

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

; 資料夾（%LOCALAPPDATA%\LiveLingo）在下面的 CurUninstallStepChanged 刪：使用者可以選擇把設定、詞彙表、歷史留下來

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

function LiveLingoRunning: Boolean;
begin
  Result := CheckForMutexes('LiveLingoRunning');
end;

procedure KillLiveLingo(WithChildren: Boolean);
var
  code: Integer;
  args: String;
begin
  // 自動更新時這個安裝程式是 LiveLingo 開出來的，所以不能連「它開出來的程式」一起關（會把自己也關掉）
  args := '/F /IM LiveLingo.exe';
  if WithChildren then
    args := '/F /T /IM LiveLingo.exe';
  Exec(ExpandConstant('{sys}\taskkill.exe'), args, '', SW_HIDE, ewWaitUntilTerminated, code);
end;

procedure StopLiveLingo(WithChildren: Boolean);
var
  i: Integer;
begin
  // LiveLingo 開著的時候檔案換不掉：先等它自己關（自動更新時它會馬上關），等不到才強制關閉。
  for i := 1 to 60 do
  begin
    if not LiveLingoRunning then
      Break;
    Sleep(500);
  end;
  if LiveLingoRunning then
    KillLiveLingo(WithChildren);
  Sleep(1000);
end;

var
  BackedUp: Boolean;       // 舊版的程式檔已經改名留著了
  Installed: Boolean;      // 新版的檔案全部裝好了
  AppDir: String;          // 到 PrepareToInstall 才記下來：安裝取消得更早時 {app} 還不能用

function AppFile(Name: String): String;
begin
  Result := AddBackslash(AppDir) + Name;
end;

procedure Note(Text: String);
begin
  Log('LiveLingo: ' + Text);
end;

// 舊版的程式檔整個改名留著，新版裝進空的資料夾。改名成功表示裡面的檔案都沒人在用
// （有檔案還開著時 Windows 不讓資料夾改名），所以不會再出現「刪一半、換一半」。
function BackUpOldFiles: String;
var
  i: Integer;
begin
  Result := '';
  // 上一次留下的備份：程式檔還在就是用不到了；程式檔不見了（上次沒換回來）就先把備份放回去
  if DirExists(AppFile('_internal.old')) then
  begin
    if DirExists(AppFile('_internal')) then
      DelTree(AppFile('_internal.old'), True, True, True)
    else if RenameFile(AppFile('_internal.old'), AppFile('_internal')) then
      Note('put back the program files left from an earlier update');
  end;
  if FileExists(AppFile('LiveLingo.exe.old')) then
  begin
    if FileExists(AppFile('LiveLingo.exe')) then
      DeleteFile(AppFile('LiveLingo.exe.old'))
    else
      RenameFile(AppFile('LiveLingo.exe.old'), AppFile('LiveLingo.exe'));
  end;
  if not DirExists(AppFile('_internal')) then
    Exit;       // 第一次安裝
  // LiveLingo 放掉「我還開著」的記號之後，可能還要幾秒才真的結束；等它，等太久就強制關閉，再等一下
  for i := 1 to 120 do
  begin
    if RenameFile(AppFile('_internal'), AppFile('_internal.old')) then
    begin
      BackedUp := True;
      Break;
    end;
    if (i = 40) or (i = 80) then
    begin
      Note('program files still in use; closing LiveLingo');
      KillLiveLingo(False);
    end;
    Sleep(500);
  end;
  if not BackedUp then
  begin
    // 什麼都還沒動：舊版原封不動，下次再更新
    Note('program files are still in use after 60 seconds; nothing was changed');
    Result := 'LiveLingo is still running, so it cannot be updated now. Nothing was changed.';
    Exit;
  end;
  Note('old program files moved to _internal.old');
  // 程式本身：執行中的 exe 可以改名但不能覆蓋，所以也改名留著
  if FileExists(AppFile('LiveLingo.exe')) then
  begin
    DeleteFile(AppFile('LiveLingo.exe.old'));
    if not RenameFile(AppFile('LiveLingo.exe'), AppFile('LiveLingo.exe.old')) then
      Note('could not move LiveLingo.exe aside; it will be replaced in place');
  end;
end;

// 安裝沒有完成（換檔失敗、被取消）：把舊版放回去，至少還能用舊版
procedure PutBackOldFiles;
begin
  if not BackedUp then
    Exit;
  DelTree(AppFile('_internal'), True, True, True);
  if RenameFile(AppFile('_internal.old'), AppFile('_internal')) then
    Note('install did not finish; the old program files were put back')
  else
    Note('install did not finish and the old program files could not be put back');
  if FileExists(AppFile('LiveLingo.exe.old')) then
  begin
    DeleteFile(AppFile('LiveLingo.exe'));
    RenameFile(AppFile('LiveLingo.exe.old'), AppFile('LiveLingo.exe'));
  end;
  BackedUp := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  AppDir := ExpandConstant('{app}');
  StopLiveLingo(False);
  Result := BackUpOldFiles;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    Installed := True;
    // 新版裝好了：舊的備份刪掉（還有檔案刪不掉就留著，下次安裝或卸載再刪）
    if BackedUp then
    begin
      DelTree(AppFile('_internal.old'), True, True, True);
      DeleteFile(AppFile('LiveLingo.exe.old'));
    end;
  end;
end;

procedure DeinitializeSetup;
var
  code: Integer;
begin
  if Installed or (AppDir = '') then
    Exit;
  PutBackOldFiles;
  // 自動更新失敗：把原本的 LiveLingo 再打開，不要讓使用者面對一個關掉的程式
  if IsUpdate and FileExists(AppFile('LiveLingo.exe')) and DirExists(AppFile('_internal')) and not LiveLingoRunning then
    ExecAsOriginalUser(AppFile('LiveLingo.exe'), '', '', SW_SHOWNORMAL, ewNoWait, code);
end;

var
  KeepData: Boolean;

function InitializeUninstall: Boolean;
begin
  // 設定、詞彙表、教過的句子、對話歷史是使用者自己累積的，刪掉就沒了，所以先問。模型可以重新下載，一律刪。
  // 安靜模式（自動測試）不問，照舊全部刪；加 /KEEPDATA=1 才留。
  KeepData := ExpandConstant('{param:KEEPDATA|0}') = '1';
  if (not KeepData) and (not UninstallSilent) then
    KeepData := SuppressibleMsgBox(CustomMessage('KeepData'), mbConfirmation, MB_YESNO, IDNO) = IDYES;
  StopLiveLingo(True);
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  data: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  // 上一次更新沒刪成功的舊版備份（見 BackUpOldFiles）
  DelTree(ExpandConstant('{app}\_internal.old'), True, True, True);
  DeleteFile(ExpandConstant('{app}\LiveLingo.exe.old'));
  RemoveDir(ExpandConstant('{app}'));
  data := ExpandConstant('{localappdata}\LiveLingo');
  if KeepData then
  begin
    // 只刪大的、可以重新下載或重新產生的：模型、翻譯引擎、下載過的更新檔、內嵌瀏覽器的暫存
    DelTree(data + '\models', True, True, True);
    DelTree(data + '\engine', True, True, True);
    DelTree(data + '\update', True, True, True);
    DelTree(data + '\webview', True, True, True);
    // 記住的朋友的聲音不是「設定」：留設定的時候也刪掉
    DeleteFile(data + '\voices.json');
  end
  else
    DelTree(data, True, True, True);
end;
