"""
全域快速鍵：打遊戲的時候不用切回 LiveLingo，按一組鍵就能開關字幕、開關麥克風、複製上一句翻譯、叫出快速輸入框。

用的是 Windows 的 RegisterHotKey：只是請 Windows「這組鍵被按下時通知我」，不會去攔截或讀取其他按鍵。
同一組鍵一次只能給一個程式用，被別的程式先登記走的會標成 taken，讓使用者換一組。
"""

import sys
import threading

IS_WIN = sys.platform == "win32"

ACTIONS = ("overlay", "mic", "copy", "quick", "reply", "pick", "snap", "phrase", "inplace")      # 開關字幕、開關麥克風、複製上一句翻譯、快速輸入框、回話建議、翻剪貼簿的字、框選翻譯、常用句庫
MODS = {"Ctrl": 0x2, "Alt": 0x1, "Shift": 0x4, "Win": 0x8}      # 也是顯示的順序
_NOREPEAT = 0x4000                                  # 按住不放不要一直重複觸發
KEYS = {**{chr(c): c for c in range(ord("A"), ord("Z") + 1)}, **{str(d): ord(str(d)) for d in range(10)},
        **{f"F{n}": 0x6F + n for n in range(1, 13)}}

# 按了快速鍵之後顯示在浮動留言上的短提示（人在遊戲裡，看不到主視窗）
NOTES = {
    "zh-TW": {"inplace_on": "原地覆蓋翻譯：開", "inplace_off": "原地覆蓋翻譯：關", "inplace_no_region": "還沒有框選的區域。先到「螢幕讀取」框一塊要翻的畫面", "pick_empty": "沒有讀到文字。先選取文字並按 Ctrl+C，再按這組鍵", "snap_nothing": "框裡沒有讀到可以翻的字", "phrase_empty": "常用句庫還是空的，到設定裡加幾句", "phrase_copied": "已複製，貼到聊天就好", "reply_nothing": "對方還沒說話，沒有可以回的", "reply_copied": "已複製回話，貼到聊天就好", "mic_on": "麥克風：開", "mic_off": "麥克風：關", "copied": "已複製上一句翻譯", "nothing": "還沒有可以複製的翻譯",
              "not_ready": "翻譯引擎還沒準備好",
              "skipped": "聊天太快，有 {n} 則來不及翻，已略過",
              "engine_restarting": "翻譯引擎停了，正在自動重開…",
              "engine_back": "翻譯引擎回來了，剛剛沒翻的句子現在補上",
              "engine_waking": "翻譯引擎剛剛在休息，正在叫醒（約 10 秒）…"},
    "zh-CN": {"inplace_on": "原地覆盖翻译：开", "inplace_off": "原地覆盖翻译：关", "inplace_no_region": "还没有框选的区域。先到「屏幕读取」框一块要翻的画面", "pick_empty": "没有读到文字。先选中文字并按 Ctrl+C，再按这组键", "snap_nothing": "框里没有读到可以翻的字", "phrase_empty": "常用句库还是空的，到设置里加几句", "phrase_copied": "已复制，粘贴到聊天就好", "reply_nothing": "对方还没说话，没有可以回的", "reply_copied": "已复制回话，粘贴到聊天就好", "mic_on": "麦克风：开", "mic_off": "麦克风：关", "copied": "已复制上一句翻译", "nothing": "还没有可以复制的翻译",
              "not_ready": "翻译引擎还没准备好",
              "skipped": "聊天太快，有 {n} 条来不及翻，已略过",
              "engine_restarting": "翻译引擎停了，正在自动重启…",
              "engine_back": "翻译引擎恢复了，刚才没翻的句子现在补上",
              "engine_waking": "翻译引擎刚才在休息，正在唤醒（约 10 秒）…"},
    "en": {"inplace_on": "In-place translation: on", "inplace_off": "In-place translation: off", "inplace_no_region": "No screen area yet. Select the part of the screen to translate in Screen reading first", "pick_empty": "No text found. Select text and press Ctrl+C first, then press this hotkey", "snap_nothing": "No text to translate in that box", "phrase_empty": "The phrasebook is empty. Add some in Settings", "phrase_copied": "Copied. Paste it into the chat", "reply_nothing": "Nothing to reply to yet", "reply_copied": "Reply copied. Paste it into the chat", "mic_on": "Microphone on", "mic_off": "Microphone off", "copied": "Last translation copied",
           "nothing": "Nothing to copy yet", "not_ready": "The translation engine is not ready yet",
           "skipped": "Chat is moving too fast: {n} lines were skipped",
           "engine_restarting": "The translation engine stopped. Restarting it…",
           "engine_back": "The translation engine is back. Waiting sentences are being translated",
           "engine_waking": "The translation engine was resting. Waking it up (about 10 s)…"},
    "ko": {"inplace_on": "제자리 덮어쓰기 번역: 켜짐", "inplace_off": "제자리 덮어쓰기 번역: 꺼짐", "inplace_no_region": "아직 선택한 영역이 없습니다. 먼저 '화면 읽기'에서 번역할 화면 영역을 지정하세요", "pick_empty": "텍스트가 없습니다. 먼저 텍스트를 선택하고 Ctrl+C를 누른 다음 이 단축키를 누르세요", "snap_nothing": "상자 안에서 번역할 글자를 찾지 못했습니다", "phrase_empty": "자주 쓰는 문구가 비어 있습니다. 설정에서 추가하세요", "phrase_copied": "복사했습니다. 채팅에 붙여 넣으세요", "reply_nothing": "아직 답할 말이 없습니다", "reply_copied": "답장을 복사했습니다. 채팅에 붙여 넣으세요", "mic_on": "마이크 켜짐", "mic_off": "마이크 꺼짐", "copied": "마지막 번역을 복사했습니다", "nothing": "아직 복사할 번역이 없습니다",
           "not_ready": "번역 엔진이 아직 준비되지 않았습니다",
           "skipped": "채팅이 너무 빨라서 {n}줄을 건너뛰었습니다",
           "engine_restarting": "번역 엔진이 멈춰서 다시 시작하는 중입니다…",
           "engine_back": "번역 엔진이 다시 켜졌습니다. 밀린 문장을 이어서 번역합니다",
           "engine_waking": "번역 엔진이 쉬고 있어서 깨우는 중입니다(약 10초)…"},
    "ja": {"inplace_on": "その場に重ねて翻訳：オン", "inplace_off": "その場に重ねて翻訳：オフ", "inplace_no_region": "まだ範囲がありません。先に「画面の読み取り」で翻訳する範囲を選んでください", "pick_empty": "テキストがありません。先に文字を選んで Ctrl+C を押してから、このショートカットを押してください", "snap_nothing": "枠の中に翻訳できる文字がありませんでした", "phrase_empty": "定型文がまだ空です。設定で追加してください", "phrase_copied": "コピーしました。チャットに貼り付けてください", "reply_nothing": "まだ返信する相手の発言がありません", "reply_copied": "返信をコピーしました。チャットに貼り付けてください", "mic_on": "マイク：オン", "mic_off": "マイク：オフ", "copied": "直前の翻訳をコピーしました", "nothing": "コピーできる翻訳がまだありません",
           "not_ready": "翻訳エンジンの準備がまだできていません",
           "skipped": "チャットの流れが速いため、{n} 件を飛ばしました",
           "engine_restarting": "翻訳エンジンが止まったため、再起動しています…",
           "engine_back": "翻訳エンジンが復帰しました。待っていた文をこれから翻訳します",
           "engine_waking": "翻訳エンジンが休止中だったため、起動しています（約 10 秒）…"},
    "vi": {"inplace_on": "Dịch đè tại chỗ: bật", "inplace_off": "Dịch đè tại chỗ: tắt", "inplace_no_region": "Chưa có vùng màn hình nào. Hãy chọn vùng cần dịch trong mục Đọc màn hình trước", "pick_empty": "Không có văn bản. Hãy chọn văn bản và bấm Ctrl+C trước, rồi bấm phím tắt này", "snap_nothing": "Không đọc được chữ nào để dịch trong khung", "phrase_empty": "Kho câu hay dùng còn trống. Hãy thêm trong Cài đặt", "phrase_copied": "Đã sao chép. Dán vào chat là xong", "reply_nothing": "Chưa có gì để trả lời", "reply_copied": "Đã sao chép câu trả lời. Dán vào chat là xong", "mic_on": "Micro bật", "mic_off": "Micro tắt", "copied": "Đã sao chép bản dịch gần nhất", "nothing": "Chưa có gì để sao chép", "not_ready": "Bộ máy dịch chưa sẵn sàng", "skipped": "Chat chạy quá nhanh: đã bỏ qua {n} dòng", "engine_restarting": "Bộ máy dịch đã dừng. Đang khởi động lại…", "engine_back": "Bộ máy dịch đã trở lại. Đang dịch các câu đang chờ", "engine_waking": "Bộ máy dịch đang nghỉ. Đang đánh thức (khoảng 10 giây)…"},
    "th": {"inplace_on": "แปลทับตำแหน่งเดิม: เปิด", "inplace_off": "แปลทับตำแหน่งเดิม: ปิด", "inplace_no_region": "ยังไม่มีพื้นที่หน้าจอ ให้เลือกส่วนที่จะแปลในหน้าการอ่านหน้าจอก่อน", "pick_empty": "ไม่พบข้อความ เลือกข้อความและกด Ctrl+C ก่อน แล้วค่อยกดปุ่มลัดนี้", "snap_nothing": "ไม่พบข้อความที่แปลได้ในกรอบ", "phrase_empty": "คลังประโยคที่ใช้บ่อยยังว่าง เพิ่มได้ในการตั้งค่า", "phrase_copied": "คัดลอกแล้ว วางในแชตได้เลย", "reply_nothing": "ยังไม่มีอะไรให้ตอบ", "reply_copied": "คัดลอกคำตอบแล้ว วางในแชตได้เลย", "mic_on": "ไมโครโฟนเปิด", "mic_off": "ไมโครโฟนปิด", "copied": "คัดลอกคำแปลล่าสุดแล้ว", "nothing": "ยังไม่มีอะไรให้คัดลอก", "not_ready": "เครื่องมือแปลยังไม่พร้อม", "skipped": "แชทเร็วเกินไป: ข้ามไป {n} บรรทัด", "engine_restarting": "เครื่องมือแปลหยุดทำงาน กำลังเริ่มใหม่…", "engine_back": "เครื่องมือแปลกลับมาแล้ว กำลังแปลประโยคที่รออยู่", "engine_waking": "เครื่องมือแปลพักอยู่ กำลังปลุก (ประมาณ 10 วินาที)…"},
    "id": {"inplace_on": "Terjemahan di tempat: aktif", "inplace_off": "Terjemahan di tempat: mati", "inplace_no_region": "Belum ada area layar. Pilih bagian layar yang akan diterjemahkan di Pembacaan layar dulu", "pick_empty": "Tidak ada teks. Pilih teks dan tekan Ctrl+C dulu, lalu tekan tombol pintas ini", "snap_nothing": "Tidak ada teks untuk diterjemahkan di kotak itu", "phrase_empty": "Daftar kalimat favorit masih kosong. Tambahkan di Pengaturan", "phrase_copied": "Disalin. Tempel ke obrolan", "reply_nothing": "Belum ada yang bisa dibalas", "reply_copied": "Balasan disalin. Tempel ke obrolan", "mic_on": "Mikrofon menyala", "mic_off": "Mikrofon mati", "copied": "Terjemahan terakhir disalin", "nothing": "Belum ada yang bisa disalin", "not_ready": "Mesin terjemahan belum siap", "skipped": "Chat terlalu cepat: {n} baris dilewati", "engine_restarting": "Mesin terjemahan berhenti. Sedang dimulai ulang…", "engine_back": "Mesin terjemahan sudah kembali. Kalimat yang menunggu sedang diterjemahkan", "engine_waking": "Mesin terjemahan sedang istirahat. Membangunkannya (sekitar 10 dtk)…"},
    "es": {"inplace_on": "Traducción sobre el original: activada", "inplace_off": "Traducción sobre el original: desactivada", "inplace_no_region": "Aún no hay un área de pantalla. Primero selecciona la zona a traducir en Lectura de pantalla", "pick_empty": "No hay texto. Selecciona texto y pulsa Ctrl+C primero, luego pulsa este atajo", "snap_nothing": "No hay texto que traducir en el recuadro", "phrase_empty": "La lista de frases está vacía. Añade algunas en Ajustes", "phrase_copied": "Copiado. Pégalo en el chat", "reply_nothing": "Todavía no hay nada que responder", "reply_copied": "Respuesta copiada. Pégala en el chat", "mic_on": "Micrófono activado", "mic_off": "Micrófono desactivado", "copied": "Última traducción copiada", "nothing": "Todavía no hay nada que copiar", "not_ready": "El motor de traducción aún no está listo", "skipped": "El chat va demasiado rápido: se saltaron {n} líneas", "engine_restarting": "El motor de traducción se detuvo. Reiniciándolo…", "engine_back": "El motor de traducción ha vuelto. Traduciendo las frases pendientes", "engine_waking": "El motor de traducción estaba descansando. Despertándolo (unos 10 s)…"},
    "pt": {"inplace_on": "Tradução sobre o original: ativada", "inplace_off": "Tradução sobre o original: desativada", "inplace_no_region": "Ainda não há uma área da tela. Primeiro selecione a parte a traduzir em Leitura de tela", "pick_empty": "Não há texto. Selecione o texto e aperte Ctrl+C primeiro, depois aperte este atalho", "snap_nothing": "Não há texto para traduzir no quadro", "phrase_empty": "A lista de frases está vazia. Adicione algumas em Configurações", "phrase_copied": "Copiado. Cole no chat", "reply_nothing": "Ainda não há nada para responder", "reply_copied": "Resposta copiada. Cole no chat", "mic_on": "Microfone ligado", "mic_off": "Microfone desligado", "copied": "Última tradução copiada", "nothing": "Ainda não há nada para copiar", "not_ready": "O motor de tradução ainda não está pronto", "skipped": "O chat está rápido demais: {n} linhas foram puladas", "engine_restarting": "O motor de tradução parou. Reiniciando…", "engine_back": "O motor de tradução voltou. As frases em espera estão sendo traduzidas", "engine_waking": "O motor de tradução estava descansando. Acordando (uns 10 s)…"},
    "ru": {"inplace_on": "Перевод поверх текста: включён", "inplace_off": "Перевод поверх текста: выключен", "inplace_no_region": "Область экрана ещё не выбрана. Сначала выделите нужную часть в разделе «Чтение экрана»", "pick_empty": "Текста нет. Сначала выделите текст и нажмите Ctrl+C, затем это сочетание клавиш", "snap_nothing": "В рамке нет текста для перевода", "phrase_empty": "Список фраз пуст. Добавьте фразы в настройках", "phrase_copied": "Скопировано. Вставьте в чат", "reply_nothing": "Пока не на что отвечать", "reply_copied": "Ответ скопирован. Вставьте его в чат", "mic_on": "Микрофон включён", "mic_off": "Микрофон выключен", "copied": "Последний перевод скопирован", "nothing": "Пока нечего копировать", "not_ready": "Движок перевода ещё не готов", "skipped": "Чат идёт слишком быстро: пропущено строк: {n}", "engine_restarting": "Движок перевода остановился. Перезапуск…", "engine_back": "Движок перевода вернулся. Переводятся ожидающие фразы", "engine_waking": "Движок перевода отдыхал. Пробуждаем (около 10 с)…"},
    "de": {"inplace_on": "Übersetzung an Ort und Stelle: an", "inplace_off": "Übersetzung an Ort und Stelle: aus", "inplace_no_region": "Noch kein Bildschirmbereich. Wähle zuerst unter Bildschirmlesen den zu übersetzenden Bereich aus", "pick_empty": "Kein Text gefunden. Erst Text markieren und Strg+C drücken, dann dieses Tastenkürzel", "snap_nothing": "Im Rahmen wurde kein Text zum Übersetzen gefunden", "phrase_empty": "Die Satzsammlung ist leer. Füge in den Einstellungen welche hinzu", "phrase_copied": "Kopiert. Einfach in den Chat einfügen", "reply_nothing": "Noch nichts zum Antworten", "reply_copied": "Antwort kopiert. Einfach in den Chat einfügen", "mic_on": "Mikrofon an", "mic_off": "Mikrofon aus", "copied": "Letzte Übersetzung kopiert", "nothing": "Noch nichts zum Kopieren", "not_ready": "Die Übersetzungs-Engine ist noch nicht bereit", "skipped": "Der Chat ist zu schnell: {n} Zeilen wurden übersprungen", "engine_restarting": "Die Übersetzungs-Engine wurde beendet. Sie wird neu gestartet…", "engine_back": "Die Übersetzungs-Engine ist zurück. Wartende Sätze werden übersetzt", "engine_waking": "Die Übersetzungs-Engine hat geruht. Sie wird geweckt (etwa 10 s)…"},
    "fr": {"inplace_on": "Traduction sur place : activée", "inplace_off": "Traduction sur place : désactivée", "inplace_no_region": "Aucune zone d'écran pour l'instant. Sélectionnez d'abord la zone à traduire dans Lecture d'écran", "pick_empty": "Aucun texte. Sélectionnez du texte et appuyez d'abord sur Ctrl+C, puis sur ce raccourci", "snap_nothing": "Aucun texte à traduire dans le cadre", "phrase_empty": "Le recueil de phrases est vide. Ajoutez-en dans les réglages", "phrase_copied": "Copié. Collez-le dans le chat", "reply_nothing": "Rien à quoi répondre pour l'instant", "reply_copied": "Réponse copiée. Collez-la dans le chat", "mic_on": "Micro activé", "mic_off": "Micro désactivé", "copied": "Dernière traduction copiée", "nothing": "Rien à copier pour l'instant", "not_ready": "Le moteur de traduction n'est pas encore prêt", "skipped": "Le chat va trop vite : {n} lignes ont été ignorées", "engine_restarting": "Le moteur de traduction s'est arrêté. Redémarrage…", "engine_back": "Le moteur de traduction est de retour. Les phrases en attente sont traduites", "engine_waking": "Le moteur de traduction était au repos. Réveil en cours (environ 10 s)…"},
}


def parse(combo: str) -> tuple[int, int] | None:
    """"Ctrl+Alt+S" → (修飾鍵, 按鍵碼)。一定要有 Ctrl、Alt、Shift 或 Win 其中一個，不然會搶走平常打字和遊戲用的鍵。"""
    parts = [p.strip() for p in str(combo or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    names = {m.lower(): m for m in MODS}
    mods = 0
    for part in parts[:-1]:
        name = names.get(part.lower())
        if name is None or mods & MODS[name]:
            return None
        mods |= MODS[name]
    key = parts[-1].upper()
    return (mods, KEYS[key]) if key in KEYS else None


def normalize(combo: str) -> str:
    """整理成固定的寫法（"alt + ctrl+s" → "Ctrl+Alt+S"）；不能用的回傳空字串。"""
    parsed = parse(combo)
    if not parsed:
        return ""
    mods, vk = parsed
    key = next(name for name, code in KEYS.items() if code == vk)
    return "+".join([name for name, bit in MODS.items() if mods & bit] + [key])


class Hotkeys:
    """
    set(bindings) 之後照著登記；按下時呼叫 on_fire(動作名稱)。states 是每個動作現在的狀態：
    ok（可以用）/ taken（被別的程式佔用）/ off（沒設定）。register / unregister 測試時可以換成假的。
    """

    def __init__(self, on_fire, on_state=None, backend=None):
        self.on_fire = on_fire
        self.on_state = on_state or (lambda states: None)
        self.backend = backend if backend is not None else (_WinBackend() if IS_WIN else _NoBackend())
        self.bindings: dict[str, str] = {}
        self.states: dict[str, str] = {name: "off" for name in ACTIONS}
        self._lock = threading.Lock()
        self._paused = False

    @property
    def available(self) -> bool:
        return self.backend.available

    def start(self) -> None:
        self.backend.start(self._fired)

    def stop(self) -> None:
        self.backend.stop()

    def set(self, bindings: dict) -> dict:
        """換成這一組設定。回傳每個動作的狀態。"""
        wanted = {name: normalize(bindings.get(name, "")) for name in ACTIONS}
        with self._lock:
            self.bindings = wanted
            self._paused = False
            states = self._apply()
        self.on_state(states)
        return states

    def _apply(self) -> dict:
        wanted = self.bindings
        results = self.backend.apply({n: parse(c) for n, c in wanted.items() if c}) if self.available else {}
        self.states = {name: ("off" if not wanted[name] or not self.available else ("ok" if results.get(name) else "taken"))
                       for name in ACTIONS}
        return dict(self.states)

    def pause(self, on: bool) -> None:
        """設定頁正在等使用者按新的組合：先把登記的都放掉，不然按到現在那一組會直接觸發動作。on=False 再登記回來。"""
        with self._lock:
            if on == self._paused:
                return
            self._paused = on
            if on:
                if self.available:
                    self.backend.apply({})
                return
            states = self._apply()
        self.on_state(states)

    @property
    def paused(self) -> bool:
        return self._paused

    def _fired(self, name: str) -> None:
        if name in ACTIONS:
            self.on_fire(name)


class _NoBackend:
    """不是 Windows：沒有全域快速鍵。設定照樣存，狀態一律是 off。"""

    available = False

    def start(self, on_fire) -> None:
        pass

    def stop(self) -> None:
        pass

    def apply(self, wanted: dict) -> dict:
        return {}


class FakeBackend:
    """測試和開發用：假裝登記成功；taken 裡的組合（例如 "Ctrl+Alt+C"）假裝被別的程式佔用。press(組合) 模擬按下。"""

    available = True

    def __init__(self, taken=()):
        self.taken = {parse(c) for c in taken}
        self.registered: dict = {}
        self._on_fire = lambda name: None

    def start(self, on_fire) -> None:
        self._on_fire = on_fire

    def stop(self) -> None:
        self.registered = {}

    def apply(self, wanted: dict) -> dict:
        results = {name: combo not in self.taken for name, combo in wanted.items()}
        self.registered = {combo: name for name, combo in wanted.items() if results[name]}
        return results

    def press(self, combo: str) -> bool:
        name = self.registered.get(parse(combo))
        if name:
            self._on_fire(name)
        return bool(name)


class _WinBackend:
    """一條專門的執行緒：登記快速鍵、等 Windows 通知。登記和取消都要在同一條執行緒上做。"""

    available = True
    _WM_HOTKEY, _WM_APPLY, _WM_QUIT = 0x0312, 0x8001, 0x0012

    def __init__(self):
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._done = threading.Event()
        self._wanted: dict = {}
        self._results: dict = {}
        self._ids: dict[int, str] = {}
        self._on_fire = lambda name: None

    def start(self, on_fire) -> None:
        if self._thread:
            return
        self._on_fire = on_fire
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkeys")
        self._thread.start()
        self._ready.wait(3)

    def stop(self) -> None:
        if self._thread and self._thread_id:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self._WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None

    def apply(self, wanted: dict) -> dict:
        if not self._thread_id:
            return {}
        import ctypes

        self._wanted = dict(wanted)
        self._done.clear()
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, self._WM_APPLY, 0, 0)
        self._done.wait(3)
        return dict(self._results)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        msg = wintypes.MSG()
        u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)      # 先碰一下，這條執行緒才會有訊息佇列
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self._ready.set()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self._WM_HOTKEY:
                name = self._ids.get(int(msg.wParam))
                if name:
                    try:
                        self._on_fire(name)
                    except Exception:  # noqa: BLE001  （動作出錯不能讓這條執行緒停掉）
                        pass
            elif msg.message == self._WM_APPLY:
                for number in list(self._ids):
                    u32.UnregisterHotKey(None, number)
                self._ids, results = {}, {}
                for number, (name, (mods, vk)) in enumerate(self._wanted.items(), start=1):
                    ok = bool(u32.RegisterHotKey(None, number, mods | _NOREPEAT, vk))
                    results[name] = ok
                    if ok:
                        self._ids[number] = name
                self._results = results
                self._done.set()
        for number in list(self._ids):
            u32.UnregisterHotKey(None, number)
        self._ids = {}
        self._thread_id = 0
