# 相依圖：改了哪裡，就回去檢查哪裡

這份文件是從 `dependencies.json` 做出來的（`python docs/make_dependencies.py`），不要直接改。
改完任何東西之前先查這裡：找到你改的那一項，把列出來的地方都看過一遍，需要的話一起修。

相依有兩種。**改壞會報錯**的是程式直接載入的；**改壞不會報錯**的只靠名字對上（設定名稱、文字的 key、API 路徑、檔名），
最容易漏掉，`tests/test_basics.py` 的 `test_dependencies_hold` 會自動檢查其中能檢查的。

## 我改了什麼

### 新增或改一項設定

1. `config.py`：預設值（DEFAULTS），要讓介面能改就加進 EDITABLE
2. `service.py`：update_config 裡的檢查，還有改了之後要馬上做的事
3. `app.js`：設定畫面的開關、存好的回饋、畫面有沒有跟著更新
4. `i18n.js`：名稱和說明，五種語言
5. `overlay.py`、`tray.py`、`window.py`：真正讀這項設定的地方
6. `test_basics.py`：補一個檢查

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

1. `config.py`：浮動留言的設定
2. `service.py`：改了之後馬上重畫
3. `chatpaint.py`：字型、顏色、描邊、換行
4. `overlay.py`：位置、大小、淡出
5. `app.js`、`i18n.js`：設定畫面和預覽
6. `selftest.py`、`build.yml`：截圖

自動把關：測試會把留言畫出來比大小。真的蓋在畫面上的樣子要看自動打包附的截圖。

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

### 改翻譯的做法

1. `translate.py`：提示、整理輸出、補救
2. `terms.py`：遊戲用語
3. `config.py`：詞彙表、學過的句子
4. `service.py`：翻成哪種語言、重試
5. `romanize.py`：韓文唸法
6. `test_basics.py`：固定句子的檢查

自動把關：測試有固定的句子在把關，例如陳述句不能變成問句。

## 每個部分牽動誰

### 打包與發佈（把程式變成安裝檔、送到使用者電腦）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 套件清單 | `requirements.txt` | `livelingo.spec`：執行時才載入的套件要點名（改壞不會報錯）<br>`build.yml`：要裝的套件（檢查那一段是手寫清單）（改壞不會報錯） |
| 打包設定 | `packaging/livelingo.spec` | `installer.iss`：打包出來的資料夾（改壞不會報錯）<br>`build.yml`：打包（改壞不會報錯） |
| 自動打包與發佈 | `.github/workflows/build.yml`<br>`packaging/ci_install_test.py` | `updater.py`：發佈出去的檔名（改壞不會報錯） |
| 安裝程式 | `packaging/installer.iss` | `updater.py`：靜默安裝的參數（改壞不會報錯）<br>`build.yml`：做安裝檔（改壞不會報錯） |
| 版本號 | `livelingo/__init__.py` | `__main__.py`：版本號（改壞會報錯）<br>`service.py`：版本號（改壞會報錯）<br>`updater.py`：比較版本（改壞會報錯）<br>`build.yml`：版本號、發佈標籤（改壞不會報錯） |
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
| 浮動留言 | `livelingo/overlay.py` | `service.py`：浮動留言（改壞會報錯） |
| 留言怎麼畫 | `livelingo/chatpaint.py` | `overlay.py`：留言怎麼畫（改壞會報錯）<br>`transcript.py`：說話的人的名字（NAMES）（改壞會報錯） |
| 框選聊天區 | `livelingo/region.py` | `__main__.py`：框選模式（改壞會報錯）<br>`service.py`：框選結果（另開一個程式）（改壞不會報錯） |
| 字幕記錄 | `livelingo/transcript.py` | `service.py`：字幕記錄（改壞會報錯） |

### 中樞（所有東西都經過這裡）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 進入點 | `livelingo/__main__.py`<br>`packaging/entry.py` | `livelingo.spec`：打包的進入點（改壞不會報錯） |
| 本機 API | `livelingo/server.py` | `app.js`：API 路徑（/api/…）（改壞不會報錯）<br>`window.py`：介面的網址（改壞不會報錯）<br>`__main__.py`：開本機伺服器（改壞會報錯） |
| 主體 | `livelingo/service.py` | `app.js`：事件名稱、狀態和留言的欄位（改壞不會報錯）<br>`i18n.js`：提醒代碼（n_…）（改壞不會報錯）<br>`tray.py`：來源和字幕記錄的狀態與開關（改壞不會報錯）<br>`overlay.py`：留言的欄位（改壞不會報錯）<br>`transcript.py`：留言的欄位（改壞不會報錯）<br>`__main__.py`：啟動主體（改壞會報錯）<br>`server.py`：每條 API 對應的方法（改壞會報錯） |
| 設定與詞彙表 | `livelingo/config.py` | `app.js`：設定名稱（S.cfg.…）（改壞不會報錯）<br>`i18n.js`：五種介面語言（UI_LANGS）（改壞不會報錯）<br>`window.py`：視窗大小、置頂、關閉時收到系統匣（改壞不會報錯）<br>`tray.py`：五種語言的選單文字、設定名稱（改壞不會報錯）<br>`overlay.py`：浮動留言的設定（overlay_…）（改壞不會報錯）<br>`chatpaint.py`：五種語言的名字、提示、範例，還有字型（改壞不會報錯）<br>`region.py`：五種語言的提示和字型（改壞不會報錯）<br>`transcript.py`：五種語言的標題、存檔資料夾（改壞不會報錯）<br>`service.py`：所有設定、詞彙表、學過的句子（改壞會報錯）<br>`installer.iss`：安裝程式的語言（改壞不會報錯） |

### 核心（翻譯、辨識文字、收音、辨識語音）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 看螢幕 | `livelingo/core/watcher.py` | `service.py`：看螢幕（改壞會報錯） |
| 文字辨識 | `livelingo/core/ocr.py` | `service.py`：文字辨識（改壞會報錯）<br>`watcher.py`：辨識結果（改壞會報錯） |
| 收音 | `livelingo/core/audio.py` | `service.py`：收音、裝置清單（改壞會報錯）<br>`speech.py`：取樣率（改壞會報錯） |
| 語音辨識 | `livelingo/core/speech.py` | `service.py`：語音辨識、自動偵測語言（改壞會報錯）<br>`meters.py`：音量算法（改壞會報錯） |
| 音量 | `livelingo/meters.py` | `service.py`：音量條（改壞會報錯） |
| 翻譯 | `livelingo/core/translate.py` | `service.py`：翻譯、語言清單（LANGS）（改壞會報錯）<br>`config.py`：介面語言要等於翻譯語言（改壞不會報錯）<br>`watcher.py`：判斷語言（改壞會報錯）<br>`terms.py`：繁簡轉換（改壞會報錯） |
| 翻譯引擎 | `livelingo/core/engine.py` | `service.py`：翻譯引擎（改壞會報錯） |
| 遊戲用語 | `livelingo/terms.py`<br>`livelingo/data/terms_lol.json` | `service.py`：遊戲用語（改壞會報錯） |
| 韓文唸法 | `livelingo/core/romanize.py` | `service.py`：韓文唸法（改壞會報錯） |

### 底層（路徑、Windows 功能、模型下載、顯示卡）

| 部分 | 檔案 | 改了它，要回去檢查 |
| --- | --- | --- |
| 路徑 | `livelingo/paths.py` | `tray.py`：圖示檔的位置（改壞會報錯）<br>`__main__.py`：資料夾（改壞會報錯）<br>`server.py`：ui 資料夾（改壞會報錯）<br>`service.py`：重新啟動自己的指令（改壞會報錯） |
| Windows 功能 | `livelingo/oswin.py` | `window.py`：標題列顏色、WebView2（改壞會報錯）<br>`overlay.py`：透明視窗、螢幕大小（改壞會報錯）<br>`__main__.py`：只開一個、WebView2（改壞會報錯）<br>`service.py`：剪貼簿、開資料夾（改壞會報錯） |
| 模型下載 | `livelingo/assets.py` | `service.py`：缺哪些模型、下載（改壞會報錯）<br>`gpu.py`：模型名稱（改壞不會報錯）<br>`engine.py`：翻譯引擎的檔案（改壞不會報錯）<br>`ocr.py`：辨識模型的檔案（改壞不會報錯）<br>`speech.py`：語音模型的檔案（改壞不會報錯）<br>`updater.py`：下載（改壞會報錯） |
| 顯示卡判斷 | `livelingo/gpu.py` | `service.py`：選模式（改壞會報錯） |
