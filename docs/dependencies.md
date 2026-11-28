# 相依圖：改了哪裡，就回去檢查哪裡

這份文件是從 `dependencies.json` 做出來的（`python docs/make_dependencies.py`），不要直接改。
改完任何東西之前先查這裡：找到你改的那一項，把列出來的地方都看過一遍，需要的話一起修。

相依有兩種。**改壞會報錯**的是程式直接載入的；**改壞不會報錯**的只靠名字對上（設定名稱、文字的 key、API 路徑、檔名），
最容易漏掉，`tests/test_basics.py` 的 `test_dependencies_hold` 會自動檢查其中能檢查的。

## 我改了什麼

### 新增或改一項設定

1. `config.py`：預設值（DEFAULTS），要讓介面能改就加進 EDITABLE
2. `service.py`：update_config 裡的檢查，還有改了之後要馬上做的事
3. `service.py`：匯出、匯入、恢復預設：這一項算不算設定（SETTINGS_SKIP）、恢復預設時要不要留著（RESET_KEEP）；介面改不到的項目要在 _apply_settings 另外檢查
4. `app.js`：設定畫面的開關、存好的回饋、畫面有沒有跟著更新
5. `i18n.js`：名稱和說明，每種介面語言
6. `overlay.py`、`tray.py`、`window.py`：真正讀這項設定的地方
7. `test_basics.py`：補一個檢查

自動把關：測試會擋：設定沒有預設值、介面用了不存在的設定名稱、說明文字少一種語言。改了之後有沒有真的生效，要自己開起來看。

### 新增或改一句文字

1. `i18n.js`：五個語言區塊都要有
2. `tray.py`、`chatpaint.py`、`region.py`、`transcript.py`：同一句話如果也出現在系統匣、浮動留言、框選畫面、字幕檔，那裡各有自己的文字表
3. `app.js`：用到這個 key 的地方，版面會不會被較長的語言擠壞

自動把關：測試會擋：每種介面語言的 key 不一致、簡體裡混進繁體字、視窗外的文字表少一種語言。

### 新增一種語言

1. `translate.py`：語言清單（LANGS）、翻譯的提示和範例
2. `config.py`：介面語言（UI_LANGS）、第一次開啟時照 Windows 語言挑
3. `i18n.js`：整個語言區塊
4. `tray.py`、`chatpaint.py`、`region.py`、`transcript.py`：各自的文字表和字型
5. `speech.py`、`service.py`：語音辨識認不認得、自動偵測會不會切過去
6. `ocr.py`、`assets.py`：螢幕文字的辨識模型（ocr.MODELS：這種文字用哪一個模型）
7. `installer.iss`、`build.yml`：安裝程式的語言檔
8. `test_basics.py`：每種語言都有的檢查

自動把關：測試會擋：介面語言和翻譯語言不一致、任何一張文字表少這個語言。字型、辨識模型、安裝程式的語言要自己對。

### 新增或改一條 API

1. `server.py`：路徑和參數
2. `service.py`：對應的方法
3. `app.js`：呼叫的地方、失敗時顯示什麼
4. `selftest.py`：自我檢查有沒有走到

自動把關：測試會擋：介面呼叫了伺服器沒有的路徑。

### 改事件或留言的格式

1. `service.py`：送出的事件和欄位（留言的 why：失敗原因的代碼；lost：譯文裡找不到的數字）
2. `translate.py`：why() 多一種失敗原因時，介面要有對應的 err_代碼（每種介面語言）
3. `app.js`、`i18n.js`：接事件的地方、對話畫面；失敗的那一則的說明和「重試」（/api/again）
4. `overlay.py`：浮動留言
5. `stream.py`：直播字幕（line_of 決定哪些出現）
6. `transcript.py`：字幕檔
7. `tray.py`：選單要不要跟著更新

自動把關：測試會擋：背景送出介面沒接的事件、提醒少了文字。欄位改名不會被擋，四個收留言的地方要自己對。

### 改三種來源（讀螢幕、聽對方、聽我）

1. `service.py`：開關、忙碌中、錯誤
2. `app.js`：來源卡片
3. `tray.py`：系統匣選單
4. `config.py`：下次開啟要不要自動打開
5. `i18n.js`：名稱和錯誤訊息

自動把關：只有文字有測試在看。開關狀態有三個地方在顯示，要一起對。

### 改浮動留言的長相

1. `config.py`：浮動留言的設定（overlay_…）
2. `service.py`、`server.py`：檢查允許的值、改了之後馬上重畫、預覽圖
3. `chatpaint.py`：字型、顏色、描邊、換行
4. `overlay.py`：位置、大小、淡出；停留時間（設定的秒數是最少的，長句照 read_seconds 多留）
5. `app.js`、`i18n.js`、`style.css`：設定畫面的控制項和預覽；停留時間的說明（ov_seconds_hint）要跟實際做法一致
6. `selftest.py`、`build.yml`：截圖

自動把關：測試會把留言畫出來比大小。真的蓋在畫面上的樣子要看自動打包附的截圖。

### 改顯示方式、位置或螢幕（浮在畫面上、底部字幕條、只在視窗裡）

1. `config.py`：overlay、overlay_form、overlay_monitor、overlay_rect、overlay_layouts；哪些算外觀（OVERLAY_LOOK）
2. `overlay.py`：現在該在哪（_home）、字幕條固定的排法（BAR_LOOK）、拉回畫面、一鍵版面和自己存的版面
3. `oswin.py`：接著哪些螢幕
4. `service.py`、`server.py`：允許的值、/api/overlay 和 /api/screens、預覽圖也要照字幕條的排法
5. `tray.py`：系統匣的開關改的是 overlay
6. `app.js`、`i18n.js`、`style.css`：三張卡片、縮小的螢幕圖（拖的時候不能重畫整頁）、版面清單
7. `test_basics.py`：顯示方式的檢查

自動把關：測試會擋：位置算錯、螢幕拔掉沒拉回來、字幕條改到使用者自己的設定、存的版面套不回來。

### 換標誌或品牌色

1. `brand.js`：介面裡的標誌，還有三個圖示檔
2. `style.css`、`index.html`：標誌的樣式、開場畫面
3. `tray.py`：系統匣圖示
4. `livelingo.spec`、`installer.iss`：程式圖示、安裝程式圖示
5. `window.py`：標題列顏色

自動把關：測試會擋：圖示檔不見、打包設定沒指到。每個地方的標誌長得一不一樣，要自己看。

### 改主題或顏色

1. `style.css`：深色、淺色、每種強調色
2. `app.js`：主題選單、送給標題列的顏色
3. `window.py`、`oswin.py`：標題列
4. `config.py`、`service.py`：允許的主題和強調色
5. `selftest.py`：深色和淺色的截圖

自動把關：測試會擋：強調色少了樣式。標題列的顏色要看 Windows 上的截圖。

### 新增一個 Python 套件

1. `requirements.txt`：requirements.txt 寫確切的版本；constraints.txt 要補上這個套件和它帶進來的每一個（自動打包紅的時候，訊息會列出少了哪些）
2. `livelingo.spec`：執行時才載入的要點名，會讀自己資料夾的要整包帶走
3. `build.yml`：檢查那一段的手寫清單
4. `selftest.py`：打包後真的載得到；跟電腦環境有關的列為只記錄
5. `service.py`：載不到的時候程式照常能用

自動把關：測試會擋：打包設定沒點名執行時才載入的套件、套件沒鎖在確切的版本、兩份清單版本不一樣。自動打包會擋：實際裝進來的套件（連帶進來的）有一個沒鎖或版本不同。自動打包裡「檢查」那一段的套件清單是手寫的，要自己補。

### 改版本號或發佈

1. `__init__.py`：版本號
2. `build.yml`：讀版本、推到 main 就發佈
3. `installer.iss`：安裝檔的版本和檔名
4. `updater.py`：比較版本、下載、靜默安裝
5. `service.py`、`config.py`、`app.js`、`README.md`、`guide.py`：要不要檢查更新是設定（update_check）；檢查有兩個時間點（開啟時、程式一直開著時每隔幾小時），設定頁、關於頁、README、說明頁的說法要和這兩點一致；會連網路的地方變多或變少時，那幾句要一起改
6. `changes.py`：data/changes.json 要有新版本那一段，13 種介面語言、句數一樣；那一段要寫完這一版真正放進去的所有改動，不能只寫自己做的那一件（和別的分支合併後要重讀一次，這一版的說明有沒有漏、有沒有放錯版本）；更新提示、關於頁、GitHub 發行說明、更新後第一次開啟的「這次改了什麼」都讀它
7. `service.py`、`app.js`、`i18n.js`、`updater.py`：更新後第一次開啟要列出「上次看過之後所有的改動」（seen_version、Service._whats_new、/api/seen、介面的 #wn-notes），按「知道了」才關；新安裝、設定精靈還沒走完的不顯示；更新提示拿不到說明時要告訴使用者更新後會看到，不能什麼都不說（updater.fetch_notes 會重試）。test_whats_new_is_told_after_every_update_or_reinstall 在擋
8. `updater.py`、`service.py`、`build.yml`：用過的安裝檔不留著：新版一開就刪（updater.tidy、_installers_cleanup），剛跑完的那一個等安裝程式結束再試；比現在新的（還沒裝的更新）不能刪。Windows 安裝測試會真的看它有沒有被刪掉
9. `updater.py`、`service.py`、`app.js`、`i18n.js`：回上一版：update.json 記「從哪一版來的」，退回時重新下載那一版、核對雜湊才裝（介面和儲存空間頁的說明要跟著這個說法）；新版加了舊版不認得的東西（像搬過位置的大檔案），退回之前要先處理（STORE_BEFORE）
10. `installer.iss`：[Files] 要有 ignoreversion，裝舊版才換得掉新版的檔案
11. `installer.iss`、`__main__.py`、`build.yml`、`engine.py`：換檔前先把舊的 _internal、LiveLingo.exe 改名成 .old 留著（改名成功才表示檔案都放開了；等不到就用 KillFileHolders 關掉載入了程式資料夾或引擎資料夾裡檔案的程式，再等不到就什麼都不動），裝好才刪，沒裝好就換回去、自動更新時再打開舊版；不要再用 [InstallDelete] 先刪。exit_now 不能先放掉「我還開著」的記號（LiveLingoRunning），收尾出錯也要關引擎並結束；開引擎（和以後開任何外部程式）要包在 engine.own_dlls_only() 裡，否則它會載入並佔住 _internal 的 msvcp140.dll。Windows 安裝測試會在檔案被佔住時更新，看程式還打不打得開
12. `service.py`、`updater.py`、`app.js`、`i18n.js`：程式一直開著（縮在系統匣）時每隔幾小時再查一次新版本（UPDATE_RECHECK、Service.update_recheck）：只在主畫面上提醒（update_offer 事件和狀態欄位、#up-banner），按了「現在更新」才走和開啟時一樣的下載安裝（_install_update）；使用者關掉 update_check、還沒準備好、說過「這次先不要」或退回來的那一版都不提醒；「看改了什麼」看到的和開啟時的詢問是同一張（updateAskHTML）。test_update_reminder_while_the_app_stays_open 在擋

自動把關：測試會擋：更新程式下載的檔名和發佈的檔名不一樣。自動打包會真的安裝、更新、卸載一次。

### 新增一個介面檔案

1. `index.html`：載入順序
2. `server.py`：有沒有送得出去
3. `selftest.py`：檔案清單
4. `livelingo.spec`：整個 ui 資料夾會一起打包，不用改

自動把關：測試會擋：畫面載入的檔案不存在、自我檢查沒看、載入順序錯。

### 換模型、換引擎版本或下載來源

1. `assets.py`：版本代碼（Hugging Face 的 commit、ENGINE_TAG），還有 CHECKS、ENGINE_FILES 裡每個檔案的位元組數和 sha256，要一起換
2. `engine.py`：換引擎版本時：command() 用的每個參數新版還在不在（--swa-full、--cache-ram、--chat-template gemma…），還有新版認不認得環境變數 LLAMA_API_KEY（不認得的話引擎就沒有金鑰保護）
3. `selftest.py`：engine_online 會抓鎖定的引擎並跑 --version；鎖定的網址有一個不在就會紅
4. `README.md`：下載大小變了要改 README 的「約幾 GB」
5. `gpu.py`：哪種顯示卡用哪個模型
6. `service.py`：準備流程、缺什麼、退回標準模式
7. `engine.py`、`ocr.py`、`speech.py`：各自去哪裡找檔案
8. `app.js`、`i18n.js`：第一次開啟的下載畫面

自動把關：測試會擋：有檔案沒鎖在確切的版本、少了大小或 sha256、下載完沒核對。自動打包的自我檢查會確認每個鎖定的網址還在、大小一樣，並真的下載引擎跑一次。新版引擎認不認得我們用的參數，測試看不出來。

### 改收音或語音辨識

1. `audio.py`：裝置、兩種收音方式；記住「這支麥克風要用第二種方式」（Ways，存在 mic_ways.json：只有裝置名稱、日期、程式版本，30 天或換版本就作廢）。新加的收音方式要想清楚：記住的做法失敗了要能自己忘掉（forget）
2. `speech.py`：斷句、辨識、選語言
3. `meters.py`：音量
4. `service.py`：找麥克風、自動偵測語言；斷線後自動重新連接（_voice_error、RECONNECT_WAITS）：靠收音那邊「打開成功」和「出錯」兩個通知，新的收音方式兩個都要叫
5. `app.js`、`i18n.js`：音量條、錯誤訊息；卡片上的「正在重新連接」（來源的 lost 欄位）和健康檢查的黃燈
6. `test_basics.py`：假裝置的檢查

自動把關：測試用假的音訊裝置，會擋：第二種方式收到聲音才記、記住之後直接用第二種、過期或換版本就忘掉、記住的做法行不通時自己忘掉再試第一種。真的麥克風和喇叭只能在你的電腦上確認。

### 改視窗行為

1. `window.py`：大小、置頂、關閉
2. `oswin.py`：Windows 功能
3. `config.py`：記住的視窗設定
4. `tray.py`：關掉視窗後還在不在
5. `app.js`：切換畫面模式
6. `selftest.py`：視窗截圖

自動把關：只能靠 Windows 上的自我檢查和截圖。

### 改健康檢查，或是新增一個會出狀況的地方

1. `service.py`：health()：每一項怎麼判斷綠、黃、紅、灰
2. `i18n.js`：每個代碼一句話（h_代碼），每種介面語言
3. `app.js`：健康檢查頁、狀態變了自動重查
4. `audio.py`、`hotkeys.py`、`watcher.py`、`engine.py`、`gpu.py`、`config.py`、`history.py`：各自的狀態是從哪裡來的
5. `watcher.py`、`service.py`：讀螢幕抓到全黑的畫面（on_black）：黑幾秒才算、提醒只說一次、停止讀螢幕時要清掉
6. `test_basics.py`：各種狀況的檢查

自動把關：測試會擋：健康檢查的代碼少了哪一種語言的說明。新的功能如果有「開不起來」「被擋住」這種狀態，要記得在健康檢查加一個燈或併進現有的燈。

### 改翻過的句子（翻譯記憶）或翻譯分享檔

1. `memory.py`：資料表欄位（memory、packs、works、work_lines）、誰比較優先（find 的分數）、分享檔格式（KIND、VERSION、works）：改格式要能讀舊版的檔
2. `watcher.py`：雙語字幕（pair_lang、_pairs）：配到的那一行不能再當成要翻的一則；on_message 有配到時多一個 shown
3. `subtitles.py`：字幕檔怎麼讀、怎麼配對（MIN_OVERLAP、雙語檔的分法）；字幕的句子 fixed = SUBTITLE，排在教過的後面、機器翻的前面
4. `works.py`：標題怎麼整理（clean_title、GENERIC）：改了之後舊的作品 key 會對不上，等於全部變成新的一部
5. `translate.py`：什麼時候查、什麼時候記：半句（unfinished）、數字對不上、留著原文的都不記
6. `service.py`：remember 只從 _translate 給（回譯不記）；teach、lesson_remove 要同步；設定 remember_translations、use_shared 改了要清暫存
7. `config.py`：remember_translations、use_shared、remember_works、use_shown_translation
8. `server.py`：/api/memory 的動作：export（可帶 work、title）、import、remove_pack、forget、own_only、forget_work、subtitles_preview、subtitles_import；請求最大 8 MB（兩個字幕檔）
9. `app.js`、`i18n.js`：翻譯品質頁的「翻過的句子」區塊和作品清單（worksBlock）、訊息旁的「翻過的／朋友的」（kept_）、「這部翻過了」提示（n_work_known）、匯入字幕檔的預覽（subtitlePreview、sub_*、n_sub_*）、匯入時選錯檔案（詞彙檔、分享檔）的提示
10. `storage.py`：memory.db 算在「設定和紀錄」裡
11. `guide.py`：說明頁提到分享檔的那一段
12. `test_basics.py`：test_translation_memory_and_share_files、test_works_recognise_what_is_watched_and_share_one、test_subtitle_files_become_translations

自動把關：測試會擋：同一句沒有直接用記住的、比較差的模型翻的被拿來用、詞彙表改了還用舊的、匯入的蓋過教過的、匯出含了我自己打的、刪一包沒刪乾淨、同一部裡幾乎一樣的句子沒用上、別部的拿錯、第 1 版的檔讀不進來。真的在 Windows 上用起來快不快、瀏覽器的視窗標題對不對，要自己看。

### 改對話歷史

1. `history.py`：記哪些欄位、檔案格式、保留幾天
2. `service.py`：哪一刻算翻好了要記、訊息被拿掉時一起拿掉
3. `config.py`：history_keep、history_days
4. `transcript.py`：匯出的文字長得要一樣
5. `app.js`：歷史頁的清單、搜尋、匯出、清除前的確認
6. `i18n.js`：歷史頁的文字
7. `test_basics.py`：歷史的檢查

自動把關：測試會擋：關掉保存還在記、過期的沒刪、清除之後檔案還在、每種介面語言的標題。留言多一個欄位時，歷史、字幕記錄、匯出的文字三邊要一起看。

### 改快速鍵或快速輸入框

1. `hotkeys.py`：動作名稱（ACTIONS）、能用的鍵、短提示的每種介面語言
2. `config.py`：預設的組合（DEFAULTS["hotkeys"]：一律 Ctrl+Shift+字母，不用 Ctrl+Alt，因為德文、法文鍵盤上那就是 AltGr）。換預設要把 hotkeys_v（HOTKEYS_V）加一、舊預設放進 OLD_HOTKEYS，migrate_hotkeys 才會只換「還是舊預設」的人，自己設過的不動；快速輸入框用 Q（Ctrl+Shift+T 是瀏覽器的「重開分頁」）
3. `service.py`：檢查送來的組合、每個動作做什麼、設定頁等新組合時先停用；設定檔匯出帶 hotkeys_v（不算設定，在 SETTINGS_SKIP），舊版匯出的檔案匯入時一樣只換還是舊預設的
4. `app.js`：動作清單、按鍵怎麼變成組合的寫法、狀態怎麼顯示
5. `i18n.js`：每個動作的名稱和說明
6. `overlay.py`、`chatpaint.py`：短提示怎麼顯示、停留多久
7. `quickbox.py`、`__main__.py`：輸入框的參數和交回去的檔案
8. `selftest.py`、`test_basics.py`：登記、按下、輸入框的檢查

自動把關：測試會擋：組合鍵的寫法、兩個動作撞鍵、預設不是 Ctrl+Shift+字母、從舊版升上來時自己設過的被換掉、每種介面語言的提示、這兩個檔案裡出現替人打字或讀按鍵的函式。真的按下去有沒有反應、輸入框有沒有跑到遊戲上面、跟瀏覽器或遊戲自己的快速鍵撞不撞，只能在 Windows 上試。

### 改聊天區（可以好幾個、可以跟著程式視窗）

1. `config.py`：regions 的欄位、舊版 region 的搬移、最多幾個
2. `service.py`：介面送來的清單怎麼檢查、框選之後存哪一個、跟著視窗時實際讀哪裡
3. `watcher.py`：哪些欄位變了算換了一個區域（要從頭讀）、每個區域的統計
4. `apps.py`：視窗位置（放大比例不同的螢幕要拿真正的像素）
5. `region.py`：框選畫面交回來的位置
6. `app.js`：區域卡片、主畫面那一列、第一次設定的那一步
7. `test_basics.py`：區域的檢查

自動把關：測試會擋：舊設定檔沒搬過來、介面改得到位置大小、統計數字對不起來。區域多一個欄位時下面每一處都要認得它。

### 改回話建議

1. `core/assist.py`：提示（reply_prompt）和解析（parse_replies：一行一句、||| 分開）要一起改
2. `translate.py`：ask_free：跟翻譯同一條排隊
3. `service.py`、`server.py`：replies、reply_box、reply_copy；/api/replies；replies 事件
4. `quickbox.py`、`__main__.py`：回話小框（choose）、命令列參數；13 種語言的 REPLY_TEXTS
5. `hotkeys.py`、`config.py`、`guide.py`：快速鍵 reply（ACTIONS、預設 Ctrl+Shift+R、NOTES 的 reply_nothing / reply_copied、說明頁的 {reply}）
6. `app.js`、`i18n.js`、`style.css`：對方那句下面的「怎麼回？」和建議
7. `selftest.py`：reply_box
8. `test_basics.py`：test_seven_features_part_two

自動把關：測試用假的模型回答檢查解析和流程；建議好不好要用真的模型看。

### 改語言學習（生字、單字本、複習）

1. `learn.py`：candidates、Wordbook（wordbook.json 的格式、STEPS）
2. `service.py`、`server.py`：_learn（翻譯在排隊就不查）、_gloss（用 translate）、word_action、words_list；/api/words；words、wordbook 事件
3. `translate.py`：_gloss 用的是一般翻譯，會套詞彙表和術語包
4. `config.py`：learn_mode
5. `app.js`、`i18n.js`、`style.css`：字幕下的生字、選取字浮出的按鈕、學習頁（複習卡、單字本）、導覽的「學習」
6. `test_basics.py`：test_seven_features_part_three

自動把關：測試會擋：挑字（韓文去助詞、日文漢字和片假名、英文常見字不標）、收過或說會了的不再標、複習的間隔、檔案壞了照樣開。意思準不準要用真的模型看。

### 改對話摘要

1. `core/assist.py`：summary_prompt 和 parse_summary（一行一點，- 開頭）要一起改
2. `recap.py`：sessions、worth、GAP、MIN_LINES、recaps.json
3. `service.py`、`server.py`：recap_check（遊戲偵測那條執行緒每 30 秒看一次）、recap_now、_summarize、recap_remove；/api/recaps；recaps 事件、recap_ready 通知
4. `history.py`：有存歷史就從歷史拿句子，沒存就用畫面上的
5. `config.py`：recap_auto
6. `app.js`、`i18n.js`、`style.css`：歷史頁的摘要區塊，跟著歷史的搜尋一起找
7. `test_basics.py`：test_seven_features_part_three

自動把關：測試用假的模型回答檢查切段、解析和流程；摘要好不好要用真的模型看。

### 改翻選取的字、框選翻譯（翻譯結果小卡）

1. `snap.py`：paragraphs：上下緊貼、左邊對齊才接成一段；一個框最多幾段
2. `service.py`：pick_text、snap_text、_snap_read、_card_show、_card_done（翻好、略過、失敗三條路都要叫）；submit 的 asked
3. `quickbox.py`、`__main__.py`、`region.py`：小卡（card）、13 種語言的 CARD_TEXTS；--card 參數；框選的 --mode snap 和 SNAP_HINTS
4. `hotkeys.py`、`config.py`、`guide.py`：快速鍵 pick（Ctrl+Shift+X）、snap（Ctrl+Shift+F）；NOTES 的 pick_empty、snap_nothing；pick_copy 預設關；說明裡的 {pick} {snap}
5. `app.js`、`i18n.js`：快速鍵清單、hk_pick、hk_snap、pick_copy 的文字（13 種語言）

自動把關：測試用假的辨識和假的框選結果檢查接段、翻譯、小卡內容、翻不出來和沒讀到字的提示；真的按快速鍵、框選畫面有沒有跑到遊戲上面、替人按 Ctrl+C 有沒有用，只能在 Windows 上試。

### 改常用句庫

1. `phrases.py`：phrases.json 的格式（items、tr、fixed）、MAX_PHRASES、起手的句子（STARTERS）
2. `service.py`、`server.py`：phrase_action、_phrases_fill（換對方語言時補翻）、phrase_box、_phrase_box_write；/api/phrases；phrases 事件
3. `quickbox.py`、`__main__.py`：小框（phrases）、13 種語言的 PHRASE_TEXTS；--phrase-box / --phrase-out
4. `hotkeys.py`、`config.py`、`guide.py`：快速鍵 phrase（Ctrl+Shift+P）、NOTES 的 phrase_empty、phrase_copied；說明裡的 {phrase}
5. `app.js`、`i18n.js`：設定頁的「常用句庫」段落、ph_ 開頭的文字（13 種語言）

自動把關：測試會擋：加、刪、改譯文、重複和滿了的處理、檔案壞了照樣開、換對方語言後補翻、改過的譯文不被蓋掉、小框的檔案交接。小框長什麼樣、數字鍵有沒有反應，只能在 Windows 上試。

### 改喊我名字提醒

1. `mention.py`：clean_words、find
2. `service.py`：_mention_of、_alert；alert_words 在 update_config 整理
3. `overlay.py`、`chatpaint.py`：浮動留言標紅（MENTION_COLOR）；快取的 key 要含 mention
4. `config.py`：alert_words、alert_sound
5. `app.js`、`style.css`、`i18n.js`：訊息上的標籤（mention-tag）、設定頁的段落、al_ 開頭的文字

自動把關：測試會擋：名字整理（逗號、重複、大小寫）、英文名字要整個字才算、中文包含就算、只看對方的話、關掉就不標、響一次不連響。畫面上標紅看起來對不對、真的響不響，要自己看。

### 改念出我的譯文

1. `core/speak.py`：PowerShell 腳本（SCRIPT）、語音的語言對照（VOICE_LANG）、試聽句（SAMPLES，每種語言）、WAV 讀取和播放
2. `config.py`：speak_out、speak_device、speak_name
3. `service.py`：翻好我的話之後念（_speak_mine）；念出來的又被收到時丟掉（_is_own_voice）；speak 事件、沒有語音的提醒
4. `app.js`、`i18n.js`：「聲音裝置」頁的段落、VB-CABLE 的說明、試聽
5. `selftest.py`：speak_voice

自動把關：測試用假的語音和播放；真的 Windows 語音只有自動打包的自我檢查（speak_voice）會試。

### 改直播字幕（OBS）

1. `stream.py`：網頁本身（PAGE）、哪些留言出現（line_of）、連接埠被佔用時往後找、通行碼和 Host 檢查
2. `config.py`：stream_…；stream_key 不匯出（SETTINGS_SKIP），網址才不會跟著設定檔流出去
3. `service.py`：每個送留言的地方都要叫到 stream（_post、_drop、翻到一半、清空）；開關改了要 stream.apply()
4. `app.js`、`i18n.js`、`style.css`：「顯示在畫面上」頁的直播段落、網址、複製按鈕、OBS 連著幾個
5. `server.py`：/api/stream（複製網址）
6. `test_basics.py`：test_seven_features_part_one

自動把關：測試會連上直播字幕頁看字幕有沒有出來、通行碼不對會不會被擋。OBS 裡真正的樣子要自己開 OBS 看。

### 改翻譯的做法

1. `translate.py`：提示、整理輸出、補救；數字核對（numbers_lost）認得的寫法：新增語言時要補那個語言的數字寫法和數字用語；超譯檢查（trim_extra、meta_reply、too_long）：新增語言時要補那個語言「跟人說話」的說法（_META）和長度比例（_RATE）
2. `terms.py`：遊戲用語；照正在玩的遊戲切換（GAME_PACKS、GAMES）
3. `config.py`：詞彙表（有 game 的是遊戲專用詞）、學過的句子
4. `memory.py`：記住的翻法照語氣分開存（memory.TONES、Translator.memory_tone：保留語氣是 +keep）；機器翻的要過 find 的 ok（over_translated），超譯的不拿出來用
5. `service.py`：翻成哪種語言、重試；留言的 lost 欄位（譯文裡找不到的數字）
6. `app.js`、`style.css`、`i18n.js`：留言下面的提醒（numbers_lost）
7. `index.html`、`app.js`：輸入框和「改原文」的字數上限要跟 translate.MAX_TEXT 一樣（測試會擋）；超過 500 個字的會切段分開翻
8. `romanize.py`：韓文唸法
9. `test_basics.py`：固定句子的檢查

自動把關：測試有固定的句子在把關，例如陳述句不能變成問句、原文的數字翻完要還在。

### 改顏色、字的大小，或加新的畫面、新的按鈕

1. `style.css`：顏色都寫在最上面的變數裡，深色、淺色、五個強調色各一組；改一個就要每一組都算過
2. `app.js`：ACCENTS 色票要和樣式表同一個顏色；會動的東西一律用 button；新的整塊重畫靠焦點看守找回同一顆按鈕，按鈕要有 data-act 和分得出彼此的 data-*
3. `app.js`：新的蓋板、確認框：Esc 要能關，關了焦點回到打開它的那顆按鈕
4. `region.py`、`quickbox.py`：另外開的小視窗（框選、快速輸入框）也要能只用鍵盤做完
5. `test_basics.py`：test_text_colours_are_readable…、test_keyboard_only_paths

自動把關：測試會擋：字的顏色對比不到 4.5 比 1、字小於 12px、滑鼠點得到但不是真的按鈕、色票和樣式表的顏色不一樣。按了之後焦點有沒有掉，要自己用鍵盤走一次。

### 改模型、引擎放的位置，或加新的大檔案

1. `service.py`：找模型、引擎一律用 self.store，不是 self.data；設定和紀錄才用 self.data
2. `storage.py`：新的大檔案要列進 items（佔多少、能不能刪）；會搬的只有 PARTS 裡的資料夾
3. `assets.py`：新模型、新引擎的檔名和資料夾
4. `app.js`：儲存空間頁每一項的名字和說明（sto_i_、sto_d_）要每種介面語言
5. `installer.iss`、`__main__.py`、`build.yml`：卸載：搬出去的也要刪，只刪有記號檔的資料夾
6. `installer.iss`、`build.yml`：卸載時選「留下設定」只刪 installer.iss 裡列的資料夾（models、engine、update、webview）：資料夾裡新增大的、可以重新產生的東西要加進那份清單和安裝檢查
7. `README.md`、`guide.py`：卸載會留什麼、刪什麼的說明
8. `test_basics.py`：test_storage_sizes_move_and_remove_unused

自動把關：測試會擋：服務裡還有地方直接用 self.data 找模型或引擎。真的卸載會不會清乾淨，由 Windows 上的安裝檢查在看。

### 加新的設定、新的紀錄內容，或任何會存下使用者打的字、說的話的東西

1. `service.py`：新的設定值如果是使用者自己打的字（名字、句子），在 _report_sections 裡拿掉，只留數量
2. `service.py`：self.log 寫進紀錄檔的句子不能帶對話內容；裝置和程式的名稱可以
3. `report.py`：新的段落要有每種介面語言的標題；路徑和帳號名稱一律過 scrub
4. `app.js`、`i18n.js`：畫面上說「裡面有什麼、沒有什麼」的那幾句要跟實際內容一樣（rep_hint、rep_part_…、報告開頭三行）
5. `server.py`：/api/report 只有三件事：做出來、存檔、複製。不能加會送到外面的
6. `test_basics.py`：test_problem_report_is_shown_first_and_never_sent

自動把關：測試會擋：報告裡出現對話、詞彙表、取的名字、個人資料夾；report.py 裡出現連網路的東西；還沒給使用者看過就能存檔。新的設定值會不會洩漏內容，要自己看一次。

### 加新的輸入來源（新的 via），或改翻譯的流程

1. `usage.py`：新的來源要加進 VIAS，不然那種句子不會被算到
2. `service.py`：一句只算一次（_count 用訊息編號擋重翻）；翻譯失敗走 failed
3. `app.js`、`i18n.js`：來源的分項那一句（use_split）要加新的來源，每種介面語言
4. `report.py`：問題報告的使用數字那一段會自動帶新的來源
5. `test_basics.py`：test_usage_numbers_are_local_counts_only

自動把關：測試會擋：翻譯流程裡沒有記數、usage.py 出現連網路的東西、檔案裡出現數字以外的東西。新的來源有沒有算進去，要自己看一次。

### 新增或改設定頁的頁面、段落（設定頁最上面的搜尋框要找得到）

1. `app.js`：每一頁的內容在 pageHTML(tab)，搜尋索引（settingsIndex，換介面語言就重建）是照它畫出來的頁面建的：新的設定頁要同時進 TABS 和 pageHTML；段落要有標題（h2，或 .setting-row 裡的名稱）；不該被搜到的頁放進 SEARCH_SKIP
2. `app.js`：pageHTML 只能組 HTML，不能打 API、不能改狀態（搜尋會替每一頁各畫一次）；靠非同步資料才出現的項目（裝置、螢幕）用 pageForIndex 的替身，其他的只搜得到段落標題
3. `i18n.js`：搜尋框和結果的文字（sq_ 開頭）；別名和同義詞（kw_ 開頭，空格隔開，每種介面語言寫自己的說法，例如「快速鍵」「快捷键」「hotkeys」「단축키」），段落用 data-kw 指到它；段落名字使用者猜不到時才加。內容很長的段落（例如更新說明）用 data-titleonly，只用標題和一句說明來比對
4. `index.html`、`style.css`：搜尋列（#settings-bar）、結果（#sq-results）、跳過去之後框起來的 .found；700px 以下和每一種語言的長度不能擠、不能橫向捲動
5. `guide.py`：說明頁和 README 如果提到「設定頁最上面的搜尋」，要和實際的做法一致
6. `test_basics.py`：test_settings_pages_have_a_search_box_that_finds_every_section

自動把關：測試會擋：新的頁面沒接進 pageHTML、pageHTML 裡有會做事的呼叫、搜尋用的文字少一種語言、各種語言的範例（快速鍵、備份、麥克風）找不到對的段落。搜尋框照畫出來的設定頁自己建索引，所以新段落不用另外登記；別名說得好不好、跳過去之後捲到哪裡、框起來的樣子，要自己開起來搜一次。

### 新增或改「出錯了」的提示（要一句白話，電腦回報的原文收進「詳細」）

1. `app.js`：WHY_RULES（原文裡的關鍵字 → 原因代碼，由上往下先比到的先用）和 why()、whyCode()：新的原因加一條規則，認不出來就用「不明原因」，不能出現 RuntimeError 這類字眼；規則的寫法要 JavaScript 和 Python 的 re 都看得懂（測試用後者跑）
2. `app.js`：原文一律走 rawFold（摺起來的「詳細」）或提醒上的「詳細」按鈕（toast 的 raw 參數）；不要把 e.message 放進句子。同一個原因的兩則提醒（收音和聽對方）要併成一則
3. `i18n.js`：why_原因（一句白話：發生了什麼＋可以怎麼做）、err_unknown、raw_detail、audio_failed_both；每種介面語言
4. `service.py`、`translate.py`：背景送給介面的 error 欄位維持原文（介面才認得出原因）；翻譯失敗的代碼（why()）多一種時看「改事件或留言的格式」
5. `style.css`：原文的樣子（.raw-fold、pre.raw）：等寬字、會換行、不超出框
6. `test_basics.py`：test_error_messages_are_plain_and_the_raw_text_stays_in_detail

自動把關：測試會擋：原因的比對規則認錯（拿真實的錯誤訊息逐一跑）、少一種語言的白話、介面又把原文直接塞進句子裡、「詳細」鍵盤到不到。白話說得準不準、同一個原因的兩則提醒有沒有併成一則，要自己在 Windows 上讓它出錯看看。

### 改頁面的名字、按鈕的名字、快速鍵、鍵盤操作，或加使用者會用到的新功能

1. `guide.py`：data/help.json 每種介面語言一起改；引號裡的頁面和按鈕名字要和介面上的一模一樣
2. `i18n.js`：介面上的名字改了（nav_、src_ 開頭的），回去改說明裡引用它的句子
3. `app.js`、`i18n.js`、`guide.py`：把功能搬到另一頁（例如「翻譯品質」拆出「詞彙與記憶」）：在介面的提示和說明（i18n 的 _hint、_desc，data/help.json）、README 搜舊的頁面名字，都改成新的名字；左邊導覽的順序變了，Ctrl+數字 對到的頁面也跟著變
4. `app.js`：視窗裡的鍵盤操作改了（Ctrl+數字、F1、Esc），說明的「快速鍵和鍵盤」那一段要跟著改
5. `guide.py`：安裝方式或系統需求變了：給朋友那一頁的安裝步驟（friend.install）要改
6. `test_basics.py`：test_help_page_and_the_page_for_a_friend

自動把關：測試會擋：每種介面語言的說明結構不一樣、提到的按鍵不一樣、說明裡寫的視窗內按鍵程式裡沒有、給朋友的那一頁載入外部的東西。說明裡寫的頁面名字和做法還對不對，要自己讀一次。

### 改「即時」：譯文邊翻邊出、對方還在說就先出字

1. `translate.py`：_ask 有給 on_part 或 stop 才跟引擎要 stream（自己開連線，Stop 才關得掉）；給畫面的半句要先過繁簡轉換和整理；重翻（問句、數字）那幾次不出半句
2. `translate.py`：半句（unfinished）：提示裡多一段「還沒說完，不要自己補完」、不補句號、不重翻、另外記，不能被當成整句的譯文
3. `speech.py`：LivePacer 決定多久先辨識一次（辨識慢就做得少）；說完要先叫 on_partial(來源, "")，沒結果要叫 on_partial(來源, None)；只給「聽對方說話」
4. `jobs.py`：半句排在所有東西後面，每個來源只留最新的一份；說完就丟掉
5. `service.py`：_translate 送 partial 事件，最快 PARTIAL_EVERY 秒一次；整句的 message 一定最後到，蓋掉半句；新的半句開頭跟畫面上一樣就先不換
6. `service.py`：整句用同一個編號接手（submit 的 live）；回音、關掉收音、清除對話、辨識出錯都要把半則收回（_end_live）
7. `service.py`：翻譯和辨識都用處理器時不能同時跑（_live_wait）；整句一來，正在翻的半句要馬上停（Stop），不然整句的辨識會慢好幾秒
8. `config.py`、`app.js`、`i18n.js`：開關 live_captions，每種介面語言
9. `overlay.py`：on_partial 只給對方說的話；螢幕讀到的要翻完才知道要不要顯示
10. `app.js`、`style.css`：還在翻的那一則用 #part-編號 直接換字，不整塊重畫；游標閃動在「減少動態」時要停；還在說的原文後面有「…」
11. `history.py`、`transcript.py`、`usage.py`：半句不進歷史、不進字幕檔、不算次數
12. `speech.py`：半句只補到 QUICK_WINDOW 秒（快），整句一定照 Recognizer.window（15 秒）：整句補短實測會不準（重複、漏字）
13. `service.py`、`translate.py`、`speech.py`：兩邊都用處理器時，翻譯（Translator.translate）和辨識（Recognizer.transcribe）拿同一把鎖輪流跑（Service._cpu_turn）；任何一邊用顯示卡就不擋。新增會用到處理器的重活也要拿這把鎖
14. `service.py`、`translate.py`：引擎開好、換語言或語氣時在背景暖機（_warm_engine → Translator.warm，只吐 1 個字）；提示開頭的寫法（build_prompt）改了，暖機就跟著對
15. `test_basics.py`：test_translation_streams_out_while_it_is_made、test_live_captions_while_the_other_person_is_still_talking、test_faster_translation_warm_up_turns_and_quick_window

自動把關：測試會擋：還沒翻完的字沒有送到畫面、整句不是最後才到、自己打的字先出半句、半句進了歷史或被算次數、說完之後半句沒被整句接手或沒收回、停不下來。真的快了多少、整句有沒有因此變慢，要用真的模型和錄音量。

### 改「分辨說話者」：門檻、模型、標籤、名字、每個人的語言

1. `voices.py`：SAME／MAYBE／NEW_SECONDS 改了要重量：幾個人、標錯幾句、有沒有同一個人被分成兩個
2. `assets.py`、`storage.py`：聲紋模型鎖版本、核對 sha256；打開功能才下載；儲存空間頁列得出來、沒開的時候可以刪
3. `speech.py`：只有「聽對方說話」的管線有 voices；分不出來不能讓字幕也沒有
4. `service.py`：訊息多三個欄位 speaker、speaker_run、voice；取名字、全部重來（_speaker_run 加一）之後畫面和浮動字幕要跟著變
5. `service.py`、`report.py`、`history.py`：聲紋和取的名字平常只在記憶體：不寫進設定檔、匯出的設定、歷史、問題報告（報告只有記了幾個人）
6. `voicebook.py`、`service.py`、`config.py`、`installer.iss`：記住取過名字的人（remember_speakers，預設關）：只有這個開著才寫 voices.json；關掉開關、清空名字、卸載（連「留下設定」也是）都要刪。全部重新分辨不會忘掉記住的人。換聲紋模型要改 voicebook.MODEL
7. `overlay.py`、`chatpaint.py`、`transcript.py`：名字和顏色：浮動字幕、字幕檔都要用同一套（speaker_name、speaker_color）；改名字、全部重來的時候字幕檔裡已經記下的句子也要跟著（name_speaker、forget_speakers）
8. `app.js`、`style.css`、`i18n.js`：顏色的點要和 chatpaint.SPEAKER_COLORS 同一組、同樣的順序；「說話者 n」每種介面語言在三個地方（i18n、chatpaint.SPEAKER）要一致
9. `guide.py`：說明裡提到的頁面和開關名字
10. `speech.py`、`voices.py`、`service.py`：每個人自己的語言（主語言、副語言）：辨識之前先看大概是誰（guess 只看不記），設過語言的人用他的語言聽（speaker_langs、OWN_STAY），聲紋只算一次交給 who；沒人設語言時不能多算。設過語言的人說的話不拿來換「對方的語言」（自動偵測）
11. `voicebook.py`、`service.py`、`server.py`、`app.js`、`style.css`、`i18n.js`：語言跟名字存在同一筆（lang、lang2），跟聲紋一樣不進設定檔、匯出的設定、問題報告；POST /api/speakers 的 action language；名單上每個人兩個選單（清單跟翻譯語言同一份 PAIR_LANGS），正在選的時候不能重畫；多一種翻譯語言時這裡自動跟著多
12. `test_basics.py`：test_speakers_are_told_apart、test_named_voices_are_remembered、test_each_speaker_has_their_own_languages

自動把關：測試會擋：分人的規則變了（太短的不認新的人、像的歸同一個人）、聲紋被存檔或放進問題報告、重新分辨之後舊的標籤還在、介面沒有接 speakers、模型沒有鎖版本。門檻準不準，要用真的聲音量（乾淨的朗讀和 Discord 語音差很多）。

### 改「字幕和視窗讓分享螢幕看得到」或任何會蓋在螢幕上的東西

1. `oswin.py`：exclude_from_capture(hide)：hide=False 是 WDA_NONE；Windows 10 2004 以前沒有這個功能
2. `overlay.py`、`window.py`：字幕視窗和主視窗開的時候都照 share_visible；新增任何蓋在螢幕上的視窗，都要讓 Service.capture_mask 知道它在哪
3. `watcher.py`、`service.py`：讀螢幕（包括「試讀一次」）先用 capture_mask 塗黑自己的字幕，不然譯文會被讀進來再翻一次
4. `config.py`、`app.js`、`i18n.js`：share_visible 的開關、名稱和說明五種語言

自動把關：測試會擋：預設拍得到、設定改了開著的視窗馬上換、拍得到的時候讀螢幕會把字幕蓋住的地方塗黑、沒有字幕就不塗。Discord、OBS 真的拍不拍得到，要在 Windows 上實際分享一次才知道。

### 改自動偵測對方的語言：連續幾句才換、候選語言、換了之後哪些要跟著換

1. `service.py`：判斷的規則（SWITCH_RUN、SWITCH_RUN_GAP、SWITCH_COOLDOWN、EVIDENCE_MIN）：只算連續的句子，太短的不算也不打斷；設過語言的人說的（speaker_langs）不算也不打斷；紀錄只寫語言代碼和句數，不寫說了什麼
2. `service.py`、`speech.py`、`watcher.py`、`ocr.py`：換了之後只能就地更新：收音的語言清單（pipe.langs，聽對方和麥克風各一份）、讀螢幕的語言和文字辨識模型（watcher.source_langs、watcher.ocr）。不能停掉讀螢幕或重開收音（重開之後畫面上最後幾行會再翻一次）；只有使用者自己換語言才重開（_languages_changed）
3. `config.py`、`service.py`：候選語言 auto_langs：空的 = 全部；目前的語言和我的語言一定算在裡面（_auto_families）；update_config 去掉不認得的、全選回來就存成空的；也影響讀螢幕（_screen_langs）
4. `app.js`、`style.css`、`i18n.js`：設定頁的候選語言按鈕（只有開著自動偵測才出現、至少留一種）；auto_langs_ 開頭的文字和換語言的通知（n_their_switched），每種介面語言
5. `test_basics.py`：test_auto_detect_their_language、test_auto_detect_follows_without_restarting_capture

自動把關：測試會擋：一句話就換、換語言時停掉讀螢幕或重開收音、候選語言整理錯、介面少了候選語言的按鈕或少一種語言的文字。真的聲音和畫面要在你的電腦上聽看看。

### 改原地覆蓋翻譯（譯文蓋在畫面上原文的位置）

1. `inplace.py`：colors、paint；MAX_PENDING、MAX_CACHE；視窗一律 hidden=True（擷取拍不到）：讀螢幕才不會把自己的譯文讀回來，所以它「不能」加進 capture_mask
2. `watcher.py`、`service.py`：on_frame（辨識完、視窗不見兩個地方都要叫）；_on_frame、_inplace_apply、_inplace_translate；來源關掉 reset、區域清單改了 keep_only、換語言和語氣 clear_cache
3. `snap.py`：blocks（方框和字的大小）；paragraphs 照舊
4. `hotkeys.py`、`config.py`、`guide.py`：快速鍵 inplace（Ctrl+Shift+O）、NOTES 的 inplace_on、inplace_off、inplace_no_region；inplace 預設關；說明裡的 {inplace}
5. `app.js`、`i18n.js`：螢幕讀取頁的開關段落（inplace-section）、hk_inplace、inplace_ 開頭的文字（13 種語言）

自動把關：測試會擋：一段一段的方框、取底色和字色（字和底太像改黑或白）、畫的位置和放不下時往下長、同一段只翻一次、別的翻譯排隊時先等、區域移動／消失／關掉／換語言時視窗跟著動或收掉、讀螢幕接手後不再進浮動留言。真的蓋得準不準、擷取真的拍不到、遊戲視窗模式下看起來如何，只能在 Windows 上試。

### 改影片字幕（影片、音訊檔做雙語 .srt）

1. `filesubs.py`：decode 不能用 faster_whisper.audio.decode_audio：它呼叫 av.open 時帶的參數，我們鎖的 av 19 不收（見 constraints.txt）；cut 的 MAX_SEGMENT；MAX_SECONDS（整段聲音在記憶體裡）
2. `service.py`、`server.py`：filesubs_start/cancel/open/pick、_ensure_recognizer（收音和影片字幕共用）；_maybe_free_speech 工作中不放掉模型；/api/filesubs；filesubs 事件
3. `window.py`、`oswin.py`：choose_file（pywebview 的開檔視窗）、reveal（檔案總管裡指出來）
4. `memory.py`、`subtitles.py`：translate 的 remember=file、work：翻好的記進這一部；寫出來的 .srt 匯入字幕檔那邊讀得回來
5. `app.js`、`i18n.js`、`style.css`：「影片字幕」頁（filesubsPage、fs-live）、導覽項目放在「語言與一般」後面（Ctrl+數字不變）、fs_ 開頭的文字（13 種語言）

自動把關：測試會擋：解聲音（有 PyAV 就真的解一個 wav）、字幕檔的格式和時間、三種內容、不蓋掉舊檔、唯讀資料夾改存別處、重複句只留一個、聊天排隊時先等、取消會存已做好的、各種錯誤代碼、翻好的記進作品。真的影片和語音模型，在雲端用過 tiny 模型跑過一次；實際速度和 Windows 的選檔視窗要在你的電腦上試。

### 不翻的字、名字建議、數字修正、隱私暫停、遊戲自動開、劇情框、念出來、情境卡、翻譯健檢（2.14.0）

1. `names.py`、`translate.py`：clean_words、candidates、NameWatch.add、hotwords；repair_numbers、tidy(keep)、translate(fresh)
2. `service.py`、`server.py`、`config.py`：keep_words/name_skip/name_suggest/game_start/speak_heard* 在 DEFAULTS 和 EDITABLE；RESET_KEEP；/api/names /api/private /api/scenario /api/quality；private 不寫 history、transcript、memory
3. `watcher.py`、`speech.py`、`core/speak.py`：STORY_WAIT 和 story_text；hotwords 只在不是空的時候帶；reader.recent() 算進 _is_own_voice
4. `qualitycheck.py`：quality_cases.json 的檢查種類；quality.json 只留 10 次
5. `hotkeys.py`、`app.js`、`i18n.js`、`style.css`、`index.html`：private 熱鍵 Ctrl+Shift+H 和 NOTES 13 種語言；i18n 新字 13 種語言都有

自動把關：測試會擋：數字修正、tidy 留下不翻的字、名字候選和第三次才問、熱詞、健檢的打分、自動開關只關自己開的、隱私暫停不寫紀錄、念出來的聲音不被當成對方、劇情框等字停、情境卡、各個 /api。真的遊戲畫面、語音熱詞效果、Windows 念出來的聲音要在你的電腦上試。

### 斷句修補、讀錯字修正、沒把握的句子、縮寫提醒、教過的句子留版本、粵語、每週錯誤報告、語助詞、網路用語註解（2.15.0）

1. `textfix.py`、`names.py`：JOIN_GAP、JOIN_SHORT、_FILLERS（不要放進「是」的意思：응、うん）、ABBR_NEED
2. `service.py`、`server.py`、`config.py`：join_split/ocr_fix/drop_fillers/unsure_recheck/unsure_view/abbr_suggest/abbr_skip/cantonese/slang_notes 在 DEFAULTS 和 EDITABLE；abbr_skip 在 RESET_KEEP；_join_split 用 _recent_voice 的 end；_hidden 在 _post
3. `translate.py`：second_opinion 不用暫存也不記
4. `speech.py`：cantonese()：zh 的時候 transcribe 用 yue，偵測語言時 yue 的機率算進 zh
5. `corrections.py`、`config.py`：Lessons.add(before=) 留 old（最多 KEEP_OLD）；list() 只有改過的才帶 old；undo
6. `app.js`、`i18n.js`、`style.css`、`index.html`：#abbr-row、unsure-dim/unsure-hidden、.notes、lesson-undo、wr- 開頭的報告（rep_ 是問題回報頁的，不要撞名）

自動把關：測試會擋：接句子的條件、讀錯字只改數字和韓文子音、語助詞判斷、縮寫第 5 次才問、Lessons 的 old 和 undo、改正紀錄的報告、再翻一次比對、hide 時浮動字幕不出、粵語把 zh 換成 yue、各個 /api。真的語音斷句和粵語辨識要在你的電腦上試。

### 遊戲專屬版面、劇情書、框選相簿、頻道分色、玩家備註、打架時讀慢、全螢幕提醒、遊戲範本、自動找聊天框、副螢幕字幕牆（2.16.0）

1. `gametools.py`：game_templates.json 的 box 是比例（0~1）；channel_of 白灰回空字串
2. `watcher.py`、`service.py`：colors 關著時 on_message 不帶 color；_screen_extra 沒有東西就不帶 tint/story（舊的 submit 呼叫方式不變）
3. `service.py`、`server.py`、`config.py`：game_profiles 不在 EDITABLE（只走 /api/profile）；game_profiles、player_notes 在 RESET_KEEP；隱私暫停時劇情書和相簿都不存
4. `stream.py`、`window.py`：wall_on 時直播字幕伺服器不能關；字幕牆網址帶 wall=1
5. `app.js`、`i18n.js`、`style.css`：album 事件、#album-section、#story-section、#wall-section、#players-section、#profile-section、.msg.tinted；al_ 是提醒頁的，相簿用 ab_；err_unknown 結尾的 key 會被數到，不要用

自動把關：測試會擋：範本算出的區域、找聊天框、顏色歸類、玩家備註的整理和比對、劇情書和相簿的存取上限、版面存和套用、全螢幕只提醒一次、字幕牆網址、各個 /api。範本位置、真的遊戲字色、獨佔全螢幕偵測、字幕牆視窗要在你的電腦上試。

### 手把快捷鍵、條漫長圖模式、拖圖片進來翻、剪貼簿圖片翻譯、說話送進遊戲聊天、滑鼠靠近變淡、釘選句子、快捷鍵一覽、攝影機翻實物（2.17.0）

1. `hotkeys.py`、`config.py`、`guide.py`：ACTIONS 加了 clipimg、keys：DEFAULTS 的 hotkeys、help.json 的 keys 段（13 種語言都要有 {clipimg} {keys}）、guide.VALUES、介面的 HOTKEYS；hotkeys.py 不能出現 clipboard 這個字
2. `watcher.py`、`service.py`：grouper 由 service 給（core 不 import snap）；comic 區域在 on_frame 之後才分段
3. `overlay.py`、`chatpaint.py`：compose(pinned=) 釘選的最多佔一半高；HOVER_POLL 只在開著變淡時才縮短重畫間隔
4. `service.py`、`oswin.py`：chat_send 預設 False；只送 via spoken、who me；前景是自己就不送
5. `app.js`、`i18n.js`、`style.css`：gamepad、keys_card 事件；拖放（dragover/drop）；#image-section、#gamepad-section、#chat-send-section；pin 按鈕

自動把關：測試會擋：手把組合的整理和觸發、快捷鍵一覽的文字、圖片解碼和太大的擋下、讀圖走 _read_image、條漫一個對話框一則且不重翻、釘選最多 3 句和浮動字幕的位置、滑鼠在上面時變淡、送進聊天只送麥克風且預設關、各個 /api。真的手把、剪貼簿、攝影機、送進遊戲聊天要在你的電腦上試。

### 背景降噪、左右耳分流、語氣小圖示、語音指令、自動選語音設定、通話紀錄匯出、自報名字記聲音、講太快提醒、精華標記、存成字幕檔（2.18.0）

1. `hotkeys.py`、`config.py`、`guide.py`：ACTIONS 加了 mark：DEFAULTS hotkeys（Ctrl+Shift+F3）、help.json keys 段 13 種語言都要有 {mark}、guide.VALUES、介面 HOTKEYS、舊測試數快速鍵的地方
2. `speech.py`、`core/denoise.py`：denoise 預設關；降噪只給 Whisper，聲紋（分辨說話者）用原本的聲音
3. `service.py`、`voicecmd.py`：只看麥克風；voice_commands 預設關；暫停時對方的話和畫面的字都不翻，打字照常
4. `app.js`、`i18n.js`、`style.css`：marks、slow_tip、voice_name、voice_paused 事件；#voice-help-section、#session-section、#vpause-row、#voice-name-row、#slow-row

自動把關：測試會擋：降噪真的把噪音壓低又不改長度、語氣的判斷、語音指令只認整句、自我介紹的名字、講太快的提醒和冷卻、Word／Markdown／.srt 的內容、精華標記、暫停翻譯、單邊耳朵、自動選設定。真的吵雜遊戲聲、真的耳機分流要在你的電腦上試。

### 每日複習提醒、單字發音、生字程度、沉浸字幕、問為什麼這樣說、韓文漢字詞、用自己的對話出題、跟讀打分、收藏整句、Anki 匯出、對話回放練習、學習統計（2.19.0）

1. `learn.py`、`service.py`、`study.py`：wordbook.state 的 sentences 是數字，learn_data 回的 sentences 是清單（順序不能讓前者蓋掉後者）；改單字本欄位要同步 study.quiz、anki_text、介面單字列
2. `app.js`、`i18n.js`、`style.css`：事件 explain；#stats-section、#quiz-section、#practice-section、#sent-section；按鈕 data-act 都有處理函式
3. `config.py`、`tray.py`：learn_level、review_remind、immerse、hanja 在 DEFAULTS 和 EDITABLE，review_reminded 不能在介面改

自動把關：測試會擋：程度挑字、統計和收藏句子存檔後還在、出題（要 4 個字、填空和選意思）、跟讀分數、Anki 檔格式、漢字詞表和模型答案的檢查、練習只挑對方的外語、每日提醒一天一次、explain 事件。真的錄音跟讀、真的工具列通知、Windows 語音念單字要在你的電腦上試。

### 新功能亮點標記、迷你模式、字體縮放、色盲友善配色、依電腦推薦設定、歷史時間軸、多份設定檔、工具列圖示顯示語言、減少動畫（2.20.0）

1. `config.py`、`service.py`、`window.py`：ui_scale、mini、colorblind、reduce_motion、tray_lang 在 DEFAULTS 和 EDITABLE；tips_seen 不能在介面改；設定檔 SKIP 要包含這些「我的東西」
2. `app.js`、`i18n.js`、`style.css`：applyTheme 套 data-cb、data-motion、zoom、#app[data-mini]；#recommend-section、#profile-section、#timeline-section；導覽的 .new-tag；profiles 事件
3. `tray.py`、`trayicon.py`：圖示畫不出字（找不到字型）時照原圖，不能丟錯

自動把關：測試會擋：推薦設定的規則、設定檔存／換／刪和不碰「我的東西」、時間軸分段、圖示語言小字、新標記只標一次、不合法的字體縮放不收。真的縮小視窗、真的工具列圖示、色盲配色的觀感要在你的電腦上看。

## 每個部分牽動誰

### 打包與發佈（把程式變成安裝檔、送到使用者電腦）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 套件清單與鎖定的版本 | `requirements.txt`<br>`constraints.txt` | `livelingo.spec`：執行時才載入的套件要點名（改壞不會報錯）<br>`build.yml`：要裝的套件（檢查那一段是手寫清單）；兩段都用 -c constraints.txt，裝完跑 check_pins.py 核對（改壞不會報錯） |
| 給使用者看的說明：功能、安裝、下載大小、更新 | `README.md` | 沒有別的地方用到它 |
| 打包設定 | `packaging/livelingo.spec` | `installer.iss`：打包出來的資料夾（改壞不會報錯）<br>`build.yml`：打包（改壞不會報錯） |
| 自動打包與發佈 | `.github/workflows/build.yml`<br>`packaging/ci_install_test.py`<br>`packaging/check_pins.py` | `updater.py`：發佈出去的檔名（改壞不會報錯） |
| 安裝程式 | `packaging/installer.iss` | `updater.py`：靜默安裝的參數（改壞不會報錯）<br>`build.yml`：做安裝檔（改壞不會報錯） |
| 版本號 | `livelingo/__init__.py` | `changes.py`：現在是哪一版（改壞會報錯）<br>`__main__.py`：版本號（改壞會報錯）<br>`service.py`：版本號（改壞會報錯）<br>`updater.py`：比較版本（改壞會報錯）<br>`build.yml`：版本號、發佈標籤（改壞不會報錯） |
| 自動更新 | `livelingo/updater.py` | `guide.py`：下載網址要和更新用的是同一個地方（改壞不會報錯）<br>`service.py`：檢查更新（改壞會報錯） |
| 自我檢查 | `livelingo/selftest.py` | `__main__.py`：自我檢查模式（改壞會報錯）<br>`build.yml`：自我檢查的結果、截圖檔名（改壞不會報錯） |
| 測試 | `tests/test_basics.py`<br>`tests/devserver.py` | `build.yml`：跑測試（改壞不會報錯） |

### 介面（視窗裡面看到的畫面）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 畫面骨架 | `livelingo/ui/index.html` | `app.js`：區塊的 id、按鈕的 data-act（改壞不會報錯）<br>`server.py`：介面檔案（改壞不會報錯）<br>`livelingo.spec`：ui 和 data 資料夾（改壞不會報錯） |
| 畫面行為 | `livelingo/ui/app.js` | `window.py`：標題列顏色、畫面模式（/api/chrome、/api/view）（改壞不會報錯） |
| 每種介面語言的文字 | `livelingo/ui/i18n.js` | `app.js`：每一句文字的 key（改壞不會報錯）<br>`cheatsheet.py`：讀 i18n.js 裡 hk_ 開頭的動作名字（改了 key 的名字，小卡就拿不到）（改壞不會報錯） |
| 樣式與主題 | `livelingo/ui/style.css` | `app.js`：class 名稱、主題顏色（改壞不會報錯）<br>`index.html`：class 名稱（改壞不會報錯） |
| 標誌與圖示檔 | `livelingo/ui/brand.js`<br>`packaging/livelingo.ico`<br>`packaging/livelingo.png`<br>`livelingo/data/tray.png` | `app.js`：標誌（window.BRAND）（改壞不會報錯）<br>`index.html`：標誌的位置、載入順序（改壞不會報錯）<br>`style.css`：標誌的樣式（.mark）（改壞不會報錯）<br>`tray.py`：圖示 tray.png（改壞不會報錯）<br>`guide.py`：給朋友的那一頁用同一個標誌（從 brand.js 讀 markSmall）（改壞不會報錯）<br>`livelingo.spec`：程式圖示（改壞不會報錯）<br>`installer.iss`：安裝程式圖示（改壞不會報錯） |

### 視窗外看得到的（視窗本身、系統匣、蓋在遊戲上的留言、存出去的檔案）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 視窗 | `livelingo/window.py` | `__main__.py`：開視窗（改壞會報錯）<br>`service.py`：mini 改了就 window.mini(on)（沒有視窗控制或沒有這個方法就跳過）；_boot 開著 mini 時縮小（改壞不會報錯）<br>`service.py`：open_wall/close_wall：字幕牆的無邊框視窗（改壞不會報錯） |
| 系統匣 | `livelingo/tray.py` | `window.py`：系統匣有沒有開起來（改壞不會報錯）<br>`service.py`：系統匣（改壞會報錯）<br>`service.py`：_review_remind_once：tray.notify 跳每日複習提醒（圖示沒開起來回傳 False，不能丟錯）（改壞會報錯） |
| 浮動留言 | `livelingo/overlay.py` | `hotkeys.py`：短提示顯示在浮動留言上（note）（改壞不會報錯）<br>`service.py`：浮動留言（改壞會報錯）<br>`inplace.py`：蓋在畫面上的視窗用跟浮動留言一樣的（LayeredWindow，擷取拍不到）（改壞不會報錯）<br>`service.py`：current_entry/apply_entry：遊戲專屬版面存的浮動字幕位置（改壞不會報錯） |
| 直播字幕（給 OBS 的網頁） | `livelingo/stream.py` | `service.py`：直播字幕：每則留言、翻到一半的譯文、刪掉、清空（on_message、on_partial、on_removed、clear）（改壞會報錯）<br>`service.py`：wall_on、url(wall=True)：副螢幕字幕牆用直播字幕頁（改壞不會報錯） |
| 留言怎麼畫 | `livelingo/chatpaint.py` | `overlay.py`：留言怎麼畫；說話者的名字和顏色（speaker_name、speaker_color）（改壞會報錯）<br>`transcript.py`：說話的人的名字（NAMES、speaker_name）（改壞會報錯）<br>`history.py`：說話的人的名字（NAMES）（改壞會報錯）<br>`service.py`：外觀有哪些選項、設定畫面的預覽圖（改壞會報錯）<br>`inplace.py`：換行排字（_wrap）、字型、han_kind（改壞會報錯）<br>`sessionlog.py`：NAMES、speaker_name：誰說的（改壞會報錯） |
| 框選聊天區 | `livelingo/region.py` | `quickbox.py`：顏色和字型（改壞會報錯）<br>`__main__.py`：框選模式（改壞會報錯）<br>`__main__.py`：框選翻譯用同一個框選程式（--mode snap）（改壞不會報錯）<br>`service.py`：框選結果（另開一個程式）（改壞不會報錯） |
| 全域快速鍵 | `livelingo/hotkeys.py` | `service.py`：登記、狀態（ok／taken／off）、按下時做什麼；程式自己的短提示（略過幾則、引擎重開）也用 NOTES，介面那邊要有同名的 n_ 文字（改壞會報錯）<br>`app.js`：能用哪些鍵、動作名稱要一樣（改壞不會報錯）<br>`guide.py`：四個全域快速鍵的名稱（overlay、mic、copy、quick）（改壞不會報錯）<br>`gamepad.py`：動作名字跟 hotkeys.ACTIONS 一樣（service 用 HOTKEY_ACTIONS 過濾）（改壞不會報錯） |
| 快速輸入框 | `livelingo/quickbox.py` | `service.py`：打的字（另開一個程式）（改壞不會報錯）<br>`__main__.py`：快速輸入模式（--quick-input）（改壞會報錯） |
| 字幕記錄 | `livelingo/transcript.py` | `history.py`：冒號的寫法（COLON）（改壞會報錯）<br>`service.py`：字幕記錄（改壞會報錯） |
| 記住的聲音（voices.json） | `livelingo/voicebook.py` | `service.py`：有開「記住取過名字的人」：取過名字的人存檔、下次開先放回來（改壞會報錯） |
| 對話歷史 | `livelingo/history.py` | `report.py`：不放對話內容：拿最近的句子去比對紀錄檔，含著整句的那一行不要（改壞不會報錯）<br>`service.py`：記下翻好的句子、搜尋、匯出、清除（改壞會報錯）<br>`app.js`：歷史的欄位（at、who、via、original、translated、place）（改壞不會報錯） |
| 語言學習：挑生字、單字本、複習 | `livelingo/learn.py` | `service.py`：學習模式挑生字（Wordbook.fresh）、單字本的收、刪、複習（改壞會報錯）<br>`study.py`：quiz 讀單字本 listing() 的欄位（w、lang、meaning、example、example_tr）；anki_text 讀 words 和 sentences 的欄位（改壞不會報錯）<br>`service.py`：learn_level 給 fresh(level=)、seen_again、add(hanja=)、sentences、stats；wordbook 事件的 state 多了 sentences（改壞會報錯） |
| 對話摘要：切出一段段對話、存摘要、搜尋 | `livelingo/recap.py` | `service.py`：切出對話（sessions、worth、GAP）、存摘要、搜尋（改壞會報錯）<br>`study.py`：practice_sessions 用 recap.sessions 分段（改壞會報錯）<br>`timeline.py`：用 recap.sessions 分段（改壞會報錯） |
| 框選翻譯：把辨識出的一列列字接成一段段 | `livelingo/snap.py` | `service.py`：框選翻譯把辨識出的字接成段（paragraphs）（改壞會報錯）<br>`inplace.py`：一段一段的方框（blocks）（改壞會報錯） |
| 喊我名字提醒：名字和關鍵字的整理、比對 | `livelingo/mention.py` | `service.py`：名字、關鍵字的整理（clean_words）和比對（find）；訊息的 mention 欄位（改壞會報錯） |
| 不翻的字和名字建議：整理、像不像遊戲 ID、出現三次就問；語音辨識的熱詞 | `livelingo/names.py` | `service.py`：keep_words 和 name_skip 的整理（clean_words）、NameWatch 名字建議、hotwords 給語音辨識（改壞會報錯）<br>`textfix.py`：NameWatch(find=abbreviations) 數縮寫；_COMMON 排除常見英文字（改壞會報錯） |
| 翻譯健檢：拿固定的真實句子重翻、打分、跟上一次比 | `livelingo/qualitycheck.py`<br>`livelingo/data/quality_cases.json` | `service.py`：QualityCheck：/api/quality 開始、停止；用 translator.translate(fresh=True)（改壞會報錯） |
| 翻譯前的小修補：切成兩半的句子接回去、讀錯字修正、只有語助詞不翻、縮寫提醒 | `livelingo/textfix.py` | `service.py`：fix_ocr（讀螢幕）、only_filler、should_join/join（語音）、abbr_watch（縮寫提醒）（改壞會報錯） |
| 改正紀錄（聽錯、看錯、整句、詞彙）和每週錯誤報告 | `livelingo/corrections.py` | `service.py`：Corrections：edit、teach、glossary_add 記一筆；/api/corrections 的報告（改壞會報錯） |
| 遊戲小工具：常見遊戲範本、自動找聊天框、頻道顏色歸類、玩家備註 | `livelingo/gametools.py`<br>`livelingo/data/game_templates.json` | `service.py`：template_region（範本）、find_chat_box（自動找聊天框）、channel_of（頻道色）、clean_players/players_in（玩家備註）（改壞會報錯） |
| 劇情書：劇情對話框讀到的句子照遊戲分本記下來、匯出 | `livelingo/storybook.py` | `service.py`：StoryBook：劇情對話框翻好的句子 add；/api/story 讀、匯出、清掉（改壞會報錯） |
| 框選翻譯相簿：截圖和翻好的句子一起存 | `livelingo/album.py` | `service.py`：Album：框選翻譯翻好後 add；/api/album、/api/album_image（改壞會報錯） |
| 圖片來源：拖進來的圖片、剪貼簿的圖、攝影機拍一張，變成給讀字用的影像 | `livelingo/imagetext.py` | `service.py`：from_data_url（/api/image）、clipboard_image（clipimg 快速鍵）、camera_frame（/api/camera），讀字走 _read_image（改壞會報錯） |
| 手把快捷鍵：讀 XInput 按鈕，Back＋鍵觸發跟鍵盤快速鍵一樣的動作 | `livelingo/gamepad.py` | `service.py`：Gamepad(_pad_action)、clean(gamepad_keys)、BUTTONS/DEFAULTS 給介面（改壞會報錯） |
| 快捷鍵一覽小卡：動作名字從 i18n.js 的 hk_ 拿 | `livelingo/cheatsheet.py` | `service.py`：show_keys：lines(hotkeys, 手把, 介面語言)（改壞會報錯） |
| 語氣小圖示：從原文記號和聲音大小猜在笑、開玩笑、生氣、難過 | `livelingo/tone.py` | `service.py`：submit：對方的話 tone_icons 開著就 guess(text, loud) → msg.tone（改壞會報錯）<br>`overlay.py`：word(tone, 介面語言)：浮動字幕在譯文前面加 (笑) 這種短字（改壞會報錯） |
| 語音指令：整句話就是指令才算，13 種介面語言的說法 | `livelingo/voicecmd.py` | `service.py`：_on_speech：麥克風整句 match(text, ui_lang) → _voice_command；state 的 voice_cmds 用 examples（改壞會報錯） |
| 聽到自報名字：找出「我是 Mina」裡的名字 | `livelingo/introduce.py` | `service.py`：_listen_extras：name_in → voice_name 事件（要開分辨說話者），記住走 speaker_rename（改壞會報錯） |
| 對方講太快：最近幾句沒把握或太快就提醒，附請對方慢一點的句子 | `livelingo/pace.py` | `service.py`：_listen_extras：PaceWatch.add → slow_tip 事件和浮動字幕提示 ask_in(語言)（改壞會報錯） |
| 依這台電腦（顯示卡、記憶體、核心數）推薦翻譯品質和語音設定 | `livelingo/recommend.py` | `service.py`：recommend_state / recommend_apply（/api/recommend）：state(gpu, 設定)；套用前 _keep_backup（改壞會報錯） |
| 多份設定檔：存名字、換過去、刪掉（profiles.json） | `livelingo/profiles.py` | `service.py`：settings_profile（/api/profiles）：存 _settings_now、換 _apply_settings；SKIP 的項目不進設定檔（改壞會報錯） |
| 工具列圖示角落的語言小字（韓→中） | `livelingo/trayicon.py` | `tray.py`：label / render：圖示角落小字和提示文字；_signature 要包含 their_lang、my_lang、tray_lang（改壞會報錯） |
| 新功能亮點：哪一版哪些設定頁要標「新」 | `livelingo/highlights.py` | `service.py`：state 的 tips = new_tabs(tips_seen, 版本)；tip_seen（/api/tips）；全新安裝 tips_seen = all_tabs（改壞會報錯） |
| 歷史時間軸：把歷史切成一段一段 | `livelingo/timeline.py` | `service.py`：timeline_data（/api/timeline）：build(歷史句子, RECAP_GAP)（改壞會報錯） |
| 學習工具：用自己的對話出題、跟讀打分、Anki 匯出、為什麼這樣說、韓文漢字詞表、對話回放練習、每日提醒文字 | `livelingo/study.py` | `service.py`：study_quiz、shadow、explain、anki_export、practice_list/say、sentence_action、_hanja（/api/study、/api/practice）；_review_remind 用 remind_text（改壞會報錯） |
| 這一場的紀錄：通話紀錄 Markdown／Word、.srt 字幕檔、精華時間點 | `livelingo/sessionlog.py` | `service.py`：session_export（/api/session）、mark_now（/api/marks、mark 快速鍵），存在 transcript_dir（改壞會報錯） |
| 常用句庫：存句子和各語言的譯文（phrases.json） | `livelingo/phrases.py` | `service.py`：常用句庫（Phrasebook：add、remove、set_translation、missing、listing、state）（改壞會報錯） |
| 原地覆蓋翻譯：把譯文畫在畫面上原文的位置 | `livelingo/inplace.py` | `service.py`：原地覆蓋翻譯（InPlace：set_on、update、reset、keep_only、clear_cache、close）（改壞會報錯）<br>`watcher.py`：每辨識一次畫面就交過去（on_frame）；接手的區域不再送去浮動留言（改壞不會報錯） |
| 影片、音訊檔做雙語字幕：解聲音、切句、寫 .srt | `livelingo/filesubs.py` | `service.py`：影片字幕的工作（Job、decode、EXTS、MODES、MAX_SECONDS、is_media）（改壞會報錯） |
| 翻過的句子（不用重翻）、翻譯分享檔 | `livelingo/memory.py` | `service.py`：記住、匯出、匯入、刪一包、清除；教過的句子同步成人工改過的（改壞會報錯）<br>`translate.py`：翻之前查、翻好之後記；模型和詞彙表的指紋（terms_print）（改壞會報錯）<br>`app.js`：分享檔的匯出範圍（RANGES 的 2h、today、all）、狀態欄位（mine、fixed、packs、works）（改壞不會報錯）<br>`filesubs.py`：翻好的句子記進作品（translate 的 remember、work）（改壞不會報錯） |
| 讀 .srt / .vtt 字幕檔，配成原文和翻譯 | `livelingo/subtitles.py` | `service.py`：字幕檔的預覽和匯入（_subtitle_pairs）；語言用 detect_language 判斷（改壞會報錯）<br>`filesubs.py`：寫出來的 .srt 要讀得回來（原文在上、譯文在下）（改壞不會報錯） |
| 作品：從視窗標題認出在看哪一部影片、哪一個網頁 | `livelingo/works.py` | `service.py`：每一句是在哪一部看到的（_work_now）：挑視窗、整理標題、算 key（改壞會報錯）<br>`memory.py`：匯入第 2 版分享檔時用標題重算作品的 key（改壞會報錯） |
| 儲存空間：大檔案搬移、刪除 | `livelingo/storage.py` | `service.py`：大檔案放在哪（self.store）、搬移、刪掉沒在用的（改壞會報錯）<br>`__main__.py`：卸載時把搬出去的大檔案也刪掉（--remove-store）（改壞會報錯）<br>`installer.iss`：卸載前叫程式自己清（[UninstallRun]）；記號檔的名字；留下設定時要刪的資料夾名字（models、engine）（改壞不會報錯）<br>`build.yml`：真的卸載一次，看搬出去的有沒有刪、旁邊的檔案有沒有被動到（改壞不會報錯）<br>`app.js`：儲存空間頁的欄位（items、moving、lost）（改壞不會報錯）<br>`report.py`：儲存空間那一段列的項目（改壞不會報錯） |
| 每一版改了什麼（每種介面語言） | `livelingo/changes.py`<br>`livelingo/data/changes.json` | `service.py`：關於頁和更新提示要顯示的改動（改壞會報錯）<br>`updater.py`：從網路拿來的說明先過一次 clean（改壞會報錯）<br>`build.yml`：發行時把 changes.json 一起放上去，發行說明也從它產生（改壞不會報錯）<br>`app.js`：改動的格式（版本 → 語言 → 句子）（改壞不會報錯）<br>`highlights.py`：用 changes._key 比版本大小（改壞會報錯） |
| 問題報告：先給使用者看，不自己送出 | `livelingo/report.py` | `service.py`：把狀態、設定、紀錄排成一份文字；存檔、複製用的是剛才給使用者看過的那一份（改壞會報錯）<br>`app.js`：預覽的欄位（text、lines、bytes）和存好之後的檔名（改壞不會報錯） |
| 翻譯的排隊：我自己的句子先翻，螢幕上排太久的略過 | `livelingo/jobs.py` | `service.py`：翻譯的排隊：誰先翻、還在說的半句排最後而且只留最新的（put_live）、螢幕上排太久的略過（略過時用 hotkeys.NOTES 的 skipped 和介面的 n_skipped 說一聲）（改壞會報錯） |
| 使用數字：每天幾句、失敗幾次、平均等多久（只有數字） | `livelingo/usage.py` | `service.py`：翻好一句（或翻失敗）就記一次；關程式時把還沒寫的寫進檔案（改壞會報錯）<br>`app.js`：健康檢查頁的三個數字和 14 天小圖（today、week、month、days）（改壞不會報錯）<br>`report.py`：問題報告裡放使用數字（只有次數）（改壞不會報錯）<br>`storage.py`：usage.json 算在「設定和紀錄」那一項的大小裡（改壞不會報錯） |
| 說明頁，和一頁給朋友的快速上手（每種介面語言） | `livelingo/guide.py`<br>`livelingo/data/help.json` | `service.py`：說明頁的內容（填進現在的快速鍵）；把給朋友的一頁存成檔案（改壞會報錯）<br>`app.js`：說明頁的欄位（sections、items）和 [[鍵]] 的寫法；F1 開說明（改壞不會報錯） |

### 中樞（所有東西都經過這裡）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 進入點 | `livelingo/__main__.py`<br>`packaging/entry.py` | `livelingo.spec`：打包的進入點（改壞不會報錯） |
| 本機 API | `livelingo/server.py` | `app.js`：API 路徑（/api/…）（改壞不會報錯）<br>`window.py`：介面的網址（改壞不會報錯）<br>`__main__.py`：開本機伺服器（改壞會報錯） |
| 主體 | `livelingo/service.py` | `app.js`：事件名稱（含還沒翻完的譯文 partial）、狀態和留言的欄位（改壞不會報錯）<br>`i18n.js`：提醒代碼（n_…）（改壞不會報錯）<br>`tray.py`：來源和字幕記錄的狀態與開關（改壞不會報錯）<br>`overlay.py`：留言的欄位；還沒翻完的譯文（on_partial，只有對方說的話）（改壞不會報錯）<br>`quickbox.py`：交回去的檔案格式（{"text"}）（改壞不會報錯）<br>`window.py`：視窗出現時開系統匣圖示（改壞不會報錯）<br>`transcript.py`：留言的欄位（改壞不會報錯）<br>`history.py`：留言的欄位（改壞不會報錯）<br>`__main__.py`：啟動主體（改壞會報錯）<br>`server.py`：每條 API 對應的方法（改壞會報錯）<br>`overlay.py`：訊息的 mention 欄位：浮動留言把提到我的那一則名字標紅（MENTION_COLOR）（改壞不會報錯）<br>`quickbox.py`：翻譯結果小卡：card.json（{items: [{original, translated}], done}）；常用句庫小框：phrases-in.json → phrases-out.json，--card / --phrase-box / --phrase-out（改壞不會報錯）<br>`app.js`：常用句庫（/api/phrases、phrases 事件、S.phrases）；提到我的那一則（mention 欄位）（改壞不會報錯）<br>`quickbox.py`：回話小框：replies-in.json（建議）→ replies-out.json（選的那句），--reply-box / --reply-out / --their（改壞不會報錯）<br>`stream.py`：留言的欄位（who、via、original、translated、pending、error）、說話者名字（speaker_tag）（改壞不會報錯）<br>`watcher.py`：colors 開著時 on_message 多帶 color=；slow_busy 開著時畫面一直大動就讀慢（改壞不會報錯） |
| 設定與詞彙表 | `livelingo/config.py` | `app.js`：設定名稱（S.cfg.…）（改壞不會報錯）<br>`i18n.js`：介面語言清單（UI_LANGS，十三種）（改壞不會報錯）<br>`window.py`：視窗大小、置頂、關閉時收到系統匣（改壞不會報錯）<br>`tray.py`：每種介面語言的選單文字、設定名稱（改壞不會報錯）<br>`overlay.py`：顯示方式、位置、哪個螢幕、外觀、自己存的版面（overlay_…）（改壞不會報錯）<br>`chatpaint.py`：每種介面語言的名字、提示、範例，還有字型（改壞不會報錯）<br>`region.py`：每種介面語言的提示和字型（改壞不會報錯）<br>`hotkeys.py`：預設的組合（hotkeys）、每種介面語言的短提示（改壞不會報錯）<br>`quickbox.py`：每種介面語言的提示（改壞不會報錯）<br>`transcript.py`：每種介面語言的標題、存檔資料夾（改壞不會報錯）<br>`history.py`：要不要保存、保留幾天、每種介面語言的標題（改壞不會報錯）<br>`changes.py`：每種介面語言都要寫（改壞不會報錯）<br>`storage.py`：store_dir、store_leftover（改壞不會報錯）<br>`report.py`：報告的標題和說明每種介面語言都要寫（改壞不會報錯）<br>`usage.py`：usage_keep：要不要記（改壞不會報錯）<br>`guide.py`：每種介面語言都要寫；給朋友的那一頁用預設快速鍵（改壞不會報錯）<br>`server.py`：外觀的預設值（改壞會報錯）<br>`service.py`：所有設定、詞彙表、學過的句子（改壞會報錯）<br>`stream.py`：stream、stream_original、stream_mine、stream_port、stream_key；字的大小和停留秒數跟 overlay_font、overlay_seconds（改壞不會報錯）<br>`installer.iss`：安裝程式的語言（改壞不會報錯）<br>`overlay.py`：reduce_motion：_alpha 不淡入淡出、frame 的等待時間不算淡出（改壞不會報錯） |

### 核心（翻譯、辨識文字、收音、辨識語音）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 請翻譯模型做的小事：回話建議 | `livelingo/core/assist.py` | `service.py`：回話建議的提示和解析（reply_prompt、parse_replies）（改壞會報錯） |
| 念出我的譯文（Windows 內建語音） | `livelingo/core/speak.py` | `service.py`：念出我的譯文（Speaker.say）；念出來的聲音又被收到時不要再翻（recent、_is_own_voice）（改壞會報錯）<br>`service.py`：reader 的 ear=cfg ear_split：to_ear 做成只有一邊的雙聲道（改壞不會報錯） |
| 背景降噪：頻譜減法，沒人說話時記住噪音，辨識前減掉 | `livelingo/core/denoise.py` | `speech.py`：SpeechPipeline.denoise() 開著：沒人說話時 learn、辨識前 clean（整句和半句）（改壞會報錯） |
| 看螢幕 | `livelingo/core/watcher.py` | `inplace.py`：normalize（同一段字的比對用）（改壞會報錯）<br>`service.py`：看螢幕（文字、語言、辨識分數）（改壞會報錯） |
| 文字辨識 | `livelingo/core/ocr.py` | `snap.py`：辨識出的每一列（Line：text、x、y、h、w、score）（改壞不會報錯）<br>`service.py`：文字辨識；對方的語言用哪一個辨識模型（model_of），讀螢幕自動偵測時同一個模型的語言都會一起讀（改壞會報錯）<br>`watcher.py`：辨識結果（改壞會報錯） |
| 收音 | `livelingo/core/audio.py` | `core/speak.py`：播到選的播放裝置（_soundcard，跟「對方的聲音從哪個裝置出來」同一份清單）（改壞會報錯）<br>`service.py`：收音、裝置清單（改壞會報錯）<br>`procaudio.py`：取樣率、每段長度（對外的樣子要跟 Capture 一樣）（改壞會報錯）<br>`speech.py`：取樣率（改壞會報錯）<br>`voices.py`：取樣率（改壞會報錯） |
| 語音辨識 | `livelingo/core/speech.py` | `filesubs.py`：一句一句辨識（Service 把 Recognizer.transcribe 傳進來）（改壞不會報錯）<br>`service.py`：語音辨識（文字、語言、有沒有把握）、自動偵測語言；還在說就先辨識（on_partial：文字／空字串＝說完了／None＝收回）（改壞會報錯）<br>`meters.py`：音量算法（改壞會報錯）<br>`service.py`：pipe.denoise = cfg denoise；on_text 收 loud=（語氣用，舊的假函式沒有這個參數就不給）（改壞不會報錯）<br>`names.py`：hotwords 字串交給 faster-whisper 的 hotwords（空的就不帶）（改壞不會報錯） |
| 分辨說話者（聲紋） | `livelingo/core/voices.py` | `service.py`：每一句是第幾個說話者；分出來的人和名字平常只在記憶體裡（改壞會報錯）<br>`voicebook.py`：聲紋的長度和模型（MODEL、DIM）要跟聲紋模型一致；換模型舊檔就作廢（改壞不會報錯）<br>`speech.py`：每一句說完問一次是誰（who），交給 on_text 的 speaker（改壞不會報錯） |
| 只收指定程式的聲音 | `livelingo/core/procaudio.py` | `__main__.py`：試收模式（--probe-app-audio）（改壞會報錯）<br>`service.py`：只聽指定程式（可多選），不行就退回整台電腦（改壞會報錯） |
| 音量 | `livelingo/meters.py` | `service.py`：音量條（改壞會報錯） |
| 翻譯 | `livelingo/core/translate.py` | `core/assist.py`：語言名稱（LANGS）、韓日文的說法（REGISTER）、繁簡轉換；用 Translator.ask_free 送給模型（改壞會報錯）<br>`service.py`：翻譯（可以帶前面兩句當前文；on_part 讓譯文邊翻邊出）、語言清單（LANGS）、翻完核對數字（numbers_lost）、一則最長幾個字（MAX_TEXT）（改壞會報錯）<br>`config.py`：介面語言要等於翻譯語言（改壞不會報錯）<br>`watcher.py`：判斷語言；拉丁字母的語言（LATIN）分不清時歸給唯一在讀的那一種（改壞會報錯）<br>`terms.py`：繁簡轉換（改壞會報錯）<br>`qualitycheck.py`：_leftover、meta_reply、numbers_lost：健檢用翻譯那邊同一套判斷（改壞會報錯）<br>`names.py`：不翻的字當成 word = word 的詞彙；tidy 的 keep 不把 ㅋㅋ 換掉（改壞不會報錯） |
| 翻譯引擎 | `livelingo/core/engine.py` | `service.py`：翻譯引擎；用到一半停了會自動重開（engine-stopped.log 留一份給問題報告）；每次啟動把新的網址和金鑰交給翻譯（改壞會報錯）<br>`translate.py`：引擎每次啟動的金鑰（環境變數 LLAMA_API_KEY；請求要帶 Authorization: Bearer）（改壞不會報錯） |
| 用語包（可多選） | `livelingo/terms.py`<br>`livelingo/data/terms_lol.json`<br>`livelingo/data/terms_chat.json` | `service.py`：現在在玩哪個遊戲（find_game）、照遊戲挑用語包（packs_for）、遊戲名字（game_name）（改壞會報錯）<br>`service.py`：遊戲用語（改壞會報錯） |
| 韓文唸法 | `livelingo/core/romanize.py` | `service.py`：韓文唸法（改壞會報錯） |

### 底層（路徑、Windows 功能、模型下載、顯示卡）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 路徑 | `livelingo/paths.py` | `tray.py`：圖示檔的位置（改壞會報錯）<br>`changes.py`：changes.json 放在哪（改壞會報錯）<br>`guide.py`：help.json 和品牌標誌放在哪（改壞會報錯）<br>`__main__.py`：資料夾（改壞會報錯）<br>`server.py`：ui 資料夾（改壞會報錯）<br>`service.py`：重新啟動自己的指令（改壞會報錯） |
| Windows 功能 | `livelingo/oswin.py` | `window.py`：標題列顏色、WebView2（改壞會報錯）<br>`overlay.py`：透明視窗、接著哪些螢幕和各自的大小（改壞會報錯）<br>`__main__.py`：只開一個、WebView2（改壞會報錯）<br>`service.py`：剪貼簿、開資料夾、按住才說話的按鍵（PTT_KEYS）（改壞會報錯）<br>`overlay.py`：cursor_pos：滑鼠靠近變淡（改壞不會報錯）<br>`service.py`：send_chat、foreground_is_self：說話送進遊戲聊天（預設關）（改壞不會報錯）<br>`service.py`：exclusive_fullscreen：獨佔全螢幕提醒（改壞不會報錯） |
| 開著哪些程式 | `livelingo/apps.py` | `works.py`：apps.windows() 的欄位（exe、title、rect、minimized）（改壞不會報錯）<br>`service.py`：開著的程式清單、程式的最上層程序、視窗在螢幕上的位置（改壞會報錯）<br>`app.js`：程式清單的欄位（exe、name、title）（改壞不會報錯） |
| 模型下載 | `livelingo/assets.py` | `storage.py`：模型和引擎的檔名、資料夾（改壞會報錯）<br>`service.py`：缺哪些模型、下載（含語音辨識用顯示卡的 cuDNN、分辨說話者的聲紋模型）（改壞會報錯）<br>`voices.py`：聲紋模型的檔案（打開功能時才下載）（改壞不會報錯）<br>`gpu.py`：模型名稱（改壞不會報錯）<br>`engine.py`：翻譯引擎的檔案（改壞不會報錯）<br>`ocr.py`：辨識模型的檔案（改壞不會報錯）<br>`speech.py`：語音模型的檔案；用顯示卡時要 CUDA 和 cuDNN 的 DLL（改壞不會報錯）<br>`updater.py`：下載（改壞會報錯）<br>`README.md`：第一次下載大約幾 GB（要和實際的檔案大小對得上）（改壞不會報錯） |
| 顯示卡判斷 | `livelingo/gpu.py` | `service.py`：選模式（改壞會報錯） |
