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
5. `i18n.js`：名稱和說明，五種語言
6. `overlay.py`、`tray.py`、`window.py`：真正讀這項設定的地方
7. `test_basics.py`：補一個檢查

自動把關：測試會擋：設定沒有預設值、介面用了不存在的設定名稱、說明文字少一種語言。改了之後有沒有真的生效，要自己開起來看。

### 新增或改一句文字

1. `i18n.js`：五個語言區塊都要有
2. `tray.py`、`chatpaint.py`、`region.py`、`transcript.py`：同一句話如果也出現在系統匣、浮動留言、框選畫面、字幕檔，那裡各有自己的文字表
3. `app.js`：用到這個 key 的地方，版面會不會被較長的語言擠壞

自動把關：測試會擋：五種語言的 key 不一致、簡體裡混進繁體字、視窗外的文字表少一種語言。

### 新增一種語言

1. `translate.py`：語言清單（LANGS）、翻譯的提示和範例
2. `config.py`：介面語言（UI_LANGS）、第一次開啟時照 Windows 語言挑
3. `i18n.js`：整個語言區塊
4. `tray.py`、`chatpaint.py`、`region.py`、`transcript.py`：各自的文字表和字型
5. `speech.py`、`service.py`：語音辨識認不認得、自動偵測會不會切過去
6. `ocr.py`、`assets.py`：螢幕文字的辨識模型
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

1. `service.py`：送出的事件和欄位
2. `app.js`：接事件的地方、對話畫面
3. `overlay.py`：浮動留言
4. `transcript.py`：字幕檔
5. `tray.py`：選單要不要跟著更新

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
4. `overlay.py`：位置、大小、淡出
5. `app.js`、`i18n.js`、`style.css`：設定畫面的控制項和預覽
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

1. `requirements.txt`：套件和版本
2. `livelingo.spec`：執行時才載入的要點名，會讀自己資料夾的要整包帶走
3. `build.yml`：檢查那一段的手寫清單
4. `selftest.py`：打包後真的載得到；跟電腦環境有關的列為只記錄
5. `service.py`：載不到的時候程式照常能用

自動把關：測試會擋：打包設定沒點名執行時才載入的套件。自動打包裡「檢查」那一段的套件清單是手寫的，要自己補。

### 改版本號或發佈

1. `__init__.py`：版本號
2. `build.yml`：讀版本、推到 main 就發佈
3. `installer.iss`：安裝檔的版本和檔名
4. `updater.py`：比較版本、下載、靜默安裝
5. `changes.py`：data/changes.json 要有新版本那一段，五種語言、句數一樣；更新提示、關於頁、GitHub 發行說明都讀它
6. `updater.py`、`service.py`：回上一版：update.json 記「從哪一版來的」，留著的安裝檔要核對雜湊才用；新版加了舊版不認得的東西（像搬過位置的大檔案），退回之前要先處理（STORE_BEFORE）
7. `installer.iss`：[Files] 要有 ignoreversion，裝舊版才換得掉新版的檔案

自動把關：測試會擋：更新程式下載的檔名和發佈的檔名不一樣。自動打包會真的安裝、更新、卸載一次。

### 新增一個介面檔案

1. `index.html`：載入順序
2. `server.py`：有沒有送得出去
3. `selftest.py`：檔案清單
4. `livelingo.spec`：整個 ui 資料夾會一起打包，不用改

自動把關：測試會擋：畫面載入的檔案不存在、自我檢查沒看、載入順序錯。

### 換模型或下載來源

1. `assets.py`：檔案清單、大小、網址
2. `gpu.py`：哪種顯示卡用哪個模型
3. `service.py`：準備流程、缺什麼、退回標準模式
4. `engine.py`、`ocr.py`、`speech.py`：各自去哪裡找檔案
5. `app.js`、`i18n.js`：第一次開啟的下載畫面

自動把關：測試會看模式的選法和引擎檔案的挑法。真的下載只在自動打包的自我檢查裡跑。

### 改收音或語音辨識

1. `audio.py`：裝置、兩種收音方式
2. `speech.py`：斷句、辨識、選語言
3. `meters.py`：音量
4. `service.py`：找麥克風、自動偵測語言
5. `app.js`：音量條、錯誤訊息
6. `test_basics.py`：假裝置的檢查

自動把關：測試用假的音訊裝置。真的麥克風和喇叭只能在你的電腦上確認。

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
2. `i18n.js`：每個代碼一句話（h_代碼），五種語言
3. `app.js`：健康檢查頁、狀態變了自動重查
4. `audio.py`、`hotkeys.py`、`watcher.py`、`engine.py`、`gpu.py`、`config.py`、`history.py`：各自的狀態是從哪裡來的
5. `test_basics.py`：各種狀況的檢查

自動把關：測試會擋：健康檢查的代碼少了哪一種語言的說明。新的功能如果有「開不起來」「被擋住」這種狀態，要記得在健康檢查加一個燈或併進現有的燈。

### 改對話歷史

1. `history.py`：記哪些欄位、檔案格式、保留幾天
2. `service.py`：哪一刻算翻好了要記、訊息被拿掉時一起拿掉
3. `config.py`：history_keep、history_days
4. `transcript.py`：匯出的文字長得要一樣
5. `app.js`：歷史頁的清單、搜尋、匯出、清除前的確認
6. `i18n.js`：歷史頁的文字
7. `test_basics.py`：歷史的檢查

自動把關：測試會擋：關掉保存還在記、過期的沒刪、清除之後檔案還在、五種語言的標題。留言多一個欄位時，歷史、字幕記錄、匯出的文字三邊要一起看。

### 改快速鍵或快速輸入框

1. `hotkeys.py`：動作名稱（ACTIONS）、能用的鍵、短提示的五種語言
2. `config.py`：預設的組合
3. `service.py`：檢查送來的組合、每個動作做什麼、設定頁等新組合時先停用
4. `app.js`：動作清單、按鍵怎麼變成組合的寫法、狀態怎麼顯示
5. `i18n.js`：每個動作的名稱和說明
6. `overlay.py`、`chatpaint.py`：短提示怎麼顯示、停留多久
7. `quickbox.py`、`__main__.py`：輸入框的參數和交回去的檔案
8. `selftest.py`、`test_basics.py`：登記、按下、輸入框的檢查

自動把關：測試會擋：組合鍵的寫法、兩個動作撞鍵、五種語言的提示、這兩個檔案裡出現替人打字或讀按鍵的函式。真的按下去有沒有反應、輸入框有沒有跑到遊戲上面，只能在 Windows 上試。

### 改聊天區（可以好幾個、可以跟著程式視窗）

1. `config.py`：regions 的欄位、舊版 region 的搬移、最多幾個
2. `service.py`：介面送來的清單怎麼檢查、框選之後存哪一個、跟著視窗時實際讀哪裡
3. `watcher.py`：哪些欄位變了算換了一個區域（要從頭讀）、每個區域的統計
4. `apps.py`：視窗位置（放大比例不同的螢幕要拿真正的像素）
5. `region.py`：框選畫面交回來的位置
6. `app.js`：區域卡片、主畫面那一列、第一次設定的那一步
7. `test_basics.py`：區域的檢查

自動把關：測試會擋：舊設定檔沒搬過來、介面改得到位置大小、統計數字對不起來。區域多一個欄位時下面每一處都要認得它。

### 改翻譯的做法

1. `translate.py`：提示、整理輸出、補救
2. `terms.py`：遊戲用語
3. `config.py`：詞彙表、學過的句子
4. `service.py`：翻成哪種語言、重試
5. `romanize.py`：韓文唸法
6. `test_basics.py`：固定句子的檢查

自動把關：測試有固定的句子在把關，例如陳述句不能變成問句。

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
4. `app.js`：儲存空間頁每一項的名字和說明（sto_i_、sto_d_）要五種語言
5. `installer.iss`、`__main__.py`、`build.yml`：卸載：搬出去的也要刪，只刪有記號檔的資料夾
6. `test_basics.py`：test_storage_sizes_move_and_remove_unused

自動把關：測試會擋：服務裡還有地方直接用 self.data 找模型或引擎。真的卸載會不會清乾淨，由 Windows 上的安裝檢查在看。

## 每個部分牽動誰

### 打包與發佈（把程式變成安裝檔、送到使用者電腦）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 套件清單 | `requirements.txt` | `livelingo.spec`：執行時才載入的套件要點名（改壞不會報錯）<br>`build.yml`：要裝的套件（檢查那一段是手寫清單）（改壞不會報錯） |
| 打包設定 | `packaging/livelingo.spec` | `installer.iss`：打包出來的資料夾（改壞不會報錯）<br>`build.yml`：打包（改壞不會報錯） |
| 自動打包與發佈 | `.github/workflows/build.yml`<br>`packaging/ci_install_test.py` | `updater.py`：發佈出去的檔名（改壞不會報錯） |
| 安裝程式 | `packaging/installer.iss` | `updater.py`：靜默安裝的參數（改壞不會報錯）<br>`build.yml`：做安裝檔（改壞不會報錯） |
| 版本號 | `livelingo/__init__.py` | `changes.py`：現在是哪一版（改壞會報錯）<br>`__main__.py`：版本號（改壞會報錯）<br>`service.py`：版本號（改壞會報錯）<br>`updater.py`：比較版本（改壞會報錯）<br>`build.yml`：版本號、發佈標籤（改壞不會報錯） |
| 自動更新 | `livelingo/updater.py` | `service.py`：檢查更新（改壞會報錯） |
| 自我檢查 | `livelingo/selftest.py` | `__main__.py`：自我檢查模式（改壞會報錯）<br>`build.yml`：自我檢查的結果、截圖檔名（改壞不會報錯） |
| 測試 | `tests/test_basics.py`<br>`tests/devserver.py` | `build.yml`：跑測試（改壞不會報錯） |

### 介面（視窗裡面看到的畫面）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 畫面骨架 | `livelingo/ui/index.html` | `app.js`：區塊的 id、按鈕的 data-act（改壞不會報錯）<br>`server.py`：介面檔案（改壞不會報錯）<br>`livelingo.spec`：ui 和 data 資料夾（改壞不會報錯） |
| 畫面行為 | `livelingo/ui/app.js` | `window.py`：標題列顏色、畫面模式（/api/chrome、/api/view）（改壞不會報錯） |
| 五種語言的文字 | `livelingo/ui/i18n.js` | `app.js`：每一句文字的 key（改壞不會報錯） |
| 樣式與主題 | `livelingo/ui/style.css` | `app.js`：class 名稱、主題顏色（改壞不會報錯）<br>`index.html`：class 名稱（改壞不會報錯） |
| 標誌與圖示檔 | `livelingo/ui/brand.js`<br>`packaging/livelingo.ico`<br>`packaging/livelingo.png`<br>`livelingo/data/tray.png` | `app.js`：標誌（window.BRAND）（改壞不會報錯）<br>`index.html`：標誌的位置、載入順序（改壞不會報錯）<br>`style.css`：標誌的樣式（.mark）（改壞不會報錯）<br>`tray.py`：圖示 tray.png（改壞不會報錯）<br>`livelingo.spec`：程式圖示（改壞不會報錯）<br>`installer.iss`：安裝程式圖示（改壞不會報錯） |

### 視窗外看得到的（視窗本身、系統匣、蓋在遊戲上的留言、存出去的檔案）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 視窗 | `livelingo/window.py` | `__main__.py`：開視窗（改壞會報錯） |
| 系統匣 | `livelingo/tray.py` | `window.py`：系統匣有沒有開起來（改壞不會報錯）<br>`service.py`：系統匣（改壞會報錯） |
| 浮動留言 | `livelingo/overlay.py` | `hotkeys.py`：短提示顯示在浮動留言上（note）（改壞不會報錯）<br>`service.py`：浮動留言（改壞會報錯） |
| 留言怎麼畫 | `livelingo/chatpaint.py` | `overlay.py`：留言怎麼畫（改壞會報錯）<br>`transcript.py`：說話的人的名字（NAMES）（改壞會報錯）<br>`history.py`：說話的人的名字（NAMES）（改壞會報錯）<br>`service.py`：外觀有哪些選項、設定畫面的預覽圖（改壞會報錯） |
| 框選聊天區 | `livelingo/region.py` | `quickbox.py`：顏色和字型（改壞會報錯）<br>`__main__.py`：框選模式（改壞會報錯）<br>`service.py`：框選結果（另開一個程式）（改壞不會報錯） |
| 全域快速鍵 | `livelingo/hotkeys.py` | `service.py`：登記、狀態（ok／taken／off）、按下時做什麼（改壞會報錯）<br>`app.js`：能用哪些鍵、動作名稱要一樣（改壞不會報錯） |
| 快速輸入框 | `livelingo/quickbox.py` | `service.py`：打的字（另開一個程式）（改壞不會報錯）<br>`__main__.py`：快速輸入模式（--quick-input）（改壞會報錯） |
| 字幕記錄 | `livelingo/transcript.py` | `history.py`：冒號的寫法（COLON）（改壞會報錯）<br>`service.py`：字幕記錄（改壞會報錯） |
| 對話歷史 | `livelingo/history.py` | `service.py`：記下翻好的句子、搜尋、匯出、清除（改壞會報錯）<br>`app.js`：歷史的欄位（at、who、via、original、translated、place）（改壞不會報錯） |
| 儲存空間：大檔案搬移、刪除 | `livelingo/storage.py` | `service.py`：大檔案放在哪（self.store）、搬移、刪掉沒在用的（改壞會報錯）<br>`__main__.py`：卸載時把搬出去的大檔案也刪掉（--remove-store）（改壞會報錯）<br>`installer.iss`：卸載前叫程式自己清（[UninstallRun]）；記號檔的名字（改壞不會報錯）<br>`build.yml`：真的卸載一次，看搬出去的有沒有刪、旁邊的檔案有沒有被動到（改壞不會報錯）<br>`app.js`：儲存空間頁的欄位（items、moving、lost）（改壞不會報錯） |
| 每一版改了什麼（五種語言） | `livelingo/changes.py`<br>`livelingo/data/changes.json` | `service.py`：關於頁和更新提示要顯示的改動（改壞會報錯）<br>`updater.py`：從網路拿來的說明先過一次 clean（改壞會報錯）<br>`build.yml`：發行時把 changes.json 一起放上去，發行說明也從它產生（改壞不會報錯）<br>`app.js`：改動的格式（版本 → 語言 → 句子）（改壞不會報錯） |

### 中樞（所有東西都經過這裡）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 進入點 | `livelingo/__main__.py`<br>`packaging/entry.py` | `livelingo.spec`：打包的進入點（改壞不會報錯） |
| 本機 API | `livelingo/server.py` | `app.js`：API 路徑（/api/…）（改壞不會報錯）<br>`window.py`：介面的網址（改壞不會報錯）<br>`__main__.py`：開本機伺服器（改壞會報錯） |
| 主體 | `livelingo/service.py` | `app.js`：事件名稱、狀態和留言的欄位（改壞不會報錯）<br>`i18n.js`：提醒代碼（n_…）（改壞不會報錯）<br>`tray.py`：來源和字幕記錄的狀態與開關（改壞不會報錯）<br>`overlay.py`：留言的欄位（改壞不會報錯）<br>`quickbox.py`：交回去的檔案格式（{"text"}）（改壞不會報錯）<br>`window.py`：視窗出現時開系統匣圖示（改壞不會報錯）<br>`transcript.py`：留言的欄位（改壞不會報錯）<br>`history.py`：留言的欄位（改壞不會報錯）<br>`__main__.py`：啟動主體（改壞會報錯）<br>`server.py`：每條 API 對應的方法（改壞會報錯） |
| 設定與詞彙表 | `livelingo/config.py` | `app.js`：設定名稱（S.cfg.…）（改壞不會報錯）<br>`i18n.js`：五種介面語言（UI_LANGS）（改壞不會報錯）<br>`window.py`：視窗大小、置頂、關閉時收到系統匣（改壞不會報錯）<br>`tray.py`：五種語言的選單文字、設定名稱（改壞不會報錯）<br>`overlay.py`：顯示方式、位置、哪個螢幕、外觀、自己存的版面（overlay_…）（改壞不會報錯）<br>`chatpaint.py`：五種語言的名字、提示、範例，還有字型（改壞不會報錯）<br>`region.py`：五種語言的提示和字型（改壞不會報錯）<br>`hotkeys.py`：預設的組合（hotkeys）、五種語言的短提示（改壞不會報錯）<br>`quickbox.py`：五種語言的提示（改壞不會報錯）<br>`transcript.py`：五種語言的標題、存檔資料夾（改壞不會報錯）<br>`history.py`：要不要保存、保留幾天、五種語言的標題（改壞不會報錯）<br>`changes.py`：五種介面語言都要寫（改壞不會報錯）<br>`storage.py`：store_dir、store_leftover（改壞不會報錯）<br>`server.py`：外觀的預設值（改壞會報錯）<br>`service.py`：所有設定、詞彙表、學過的句子（改壞會報錯）<br>`installer.iss`：安裝程式的語言（改壞不會報錯） |

### 核心（翻譯、辨識文字、收音、辨識語音）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 看螢幕 | `livelingo/core/watcher.py` | `service.py`：看螢幕（文字、語言、辨識分數）（改壞會報錯） |
| 文字辨識 | `livelingo/core/ocr.py` | `service.py`：文字辨識（改壞會報錯）<br>`watcher.py`：辨識結果（改壞會報錯） |
| 收音 | `livelingo/core/audio.py` | `service.py`：收音、裝置清單（改壞會報錯）<br>`procaudio.py`：取樣率、每段長度（對外的樣子要跟 Capture 一樣）（改壞會報錯）<br>`speech.py`：取樣率（改壞會報錯） |
| 語音辨識 | `livelingo/core/speech.py` | `service.py`：語音辨識（文字、語言、有沒有把握）、自動偵測語言（改壞會報錯）<br>`meters.py`：音量算法（改壞會報錯） |
| 只收指定程式的聲音 | `livelingo/core/procaudio.py` | `__main__.py`：試收模式（--probe-app-audio）（改壞會報錯）<br>`service.py`：只聽指定程式（可多選），不行就退回整台電腦（改壞會報錯） |
| 音量 | `livelingo/meters.py` | `service.py`：音量條（改壞會報錯） |
| 翻譯 | `livelingo/core/translate.py` | `service.py`：翻譯（可以帶前面兩句當前文）、語言清單（LANGS）（改壞會報錯）<br>`config.py`：介面語言要等於翻譯語言（改壞不會報錯）<br>`watcher.py`：判斷語言（改壞會報錯）<br>`terms.py`：繁簡轉換（改壞會報錯） |
| 翻譯引擎 | `livelingo/core/engine.py` | `service.py`：翻譯引擎（改壞會報錯） |
| 用語包（可多選） | `livelingo/terms.py`<br>`livelingo/data/terms_lol.json`<br>`livelingo/data/terms_chat.json` | `service.py`：遊戲用語（改壞會報錯） |
| 韓文唸法 | `livelingo/core/romanize.py` | `service.py`：韓文唸法（改壞會報錯） |

### 底層（路徑、Windows 功能、模型下載、顯示卡）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 路徑 | `livelingo/paths.py` | `tray.py`：圖示檔的位置（改壞會報錯）<br>`changes.py`：changes.json 放在哪（改壞會報錯）<br>`__main__.py`：資料夾（改壞會報錯）<br>`server.py`：ui 資料夾（改壞會報錯）<br>`service.py`：重新啟動自己的指令（改壞會報錯） |
| Windows 功能 | `livelingo/oswin.py` | `window.py`：標題列顏色、WebView2（改壞會報錯）<br>`overlay.py`：透明視窗、接著哪些螢幕和各自的大小（改壞會報錯）<br>`__main__.py`：只開一個、WebView2（改壞會報錯）<br>`service.py`：剪貼簿、開資料夾、按住才說話的按鍵（PTT_KEYS）（改壞會報錯） |
| 開著哪些程式 | `livelingo/apps.py` | `service.py`：開著的程式清單、程式的最上層程序、視窗在螢幕上的位置（改壞會報錯）<br>`app.js`：程式清單的欄位（exe、name、title）（改壞不會報錯） |
| 模型下載 | `livelingo/assets.py` | `storage.py`：模型和引擎的檔名、資料夾（改壞會報錯）<br>`service.py`：缺哪些模型、下載（含語音辨識用顯示卡的 cuDNN）（改壞會報錯）<br>`gpu.py`：模型名稱（改壞不會報錯）<br>`engine.py`：翻譯引擎的檔案（改壞不會報錯）<br>`ocr.py`：辨識模型的檔案（改壞不會報錯）<br>`speech.py`：語音模型的檔案；用顯示卡時要 CUDA 和 cuDNN 的 DLL（改壞不會報錯）<br>`updater.py`：下載（改壞會報錯） |
| 顯示卡判斷 | `livelingo/gpu.py` | `service.py`：選模式（改壞會報錯） |
