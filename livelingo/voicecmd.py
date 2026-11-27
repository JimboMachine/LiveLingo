"""語音指令（2.18.0）：對麥克風說「暫停翻譯」「重翻上一句」就能控制，不用按鍵。

只看麥克風（我說的話），而且整句話就是指令才算（「我們先暫停翻譯這個話題」不算），
所以平常聊天不會被誤會成指令。說的是指令的話，這句不會被翻譯、也不會送出去。
每個介面語言都有自己的說法，英文的說法任何介面都可以用。"""

import re
import unicodedata

COMMANDS = ("pause", "resume", "again", "clear", "overlay", "mark", "copy")

PHRASES = {
    "zh-TW": {"pause": ["暫停翻譯", "停止翻譯", "先不要翻譯"], "resume": ["繼續翻譯", "開始翻譯", "恢復翻譯"],
              "again": ["重翻上一句", "重新翻譯", "再翻一次"], "clear": ["清除字幕", "清空字幕"],
              "overlay": ["開關字幕", "切換字幕"], "mark": ["標記精華", "標記這句"], "copy": ["複製翻譯", "複製上一句"]},
    "zh-CN": {"pause": ["暂停翻译", "停止翻译", "先不要翻译"], "resume": ["继续翻译", "开始翻译", "恢复翻译"],
              "again": ["重翻上一句", "重新翻译", "再翻一次"], "clear": ["清除字幕", "清空字幕"],
              "overlay": ["开关字幕", "切换字幕"], "mark": ["标记精华", "标记这句"], "copy": ["复制翻译", "复制上一句"]},
    "en": {"pause": ["pause translation", "stop translating", "pause translating"], "resume": ["resume translation", "start translating", "continue translating"],
           "again": ["translate again", "retranslate", "redo translation"], "clear": ["clear subtitles", "clear captions"],
           "overlay": ["toggle subtitles", "toggle overlay"], "mark": ["mark highlight", "mark this"], "copy": ["copy translation"]},
    "ko": {"pause": ["번역 멈춰", "번역 정지", "번역 일시정지"], "resume": ["번역 다시 시작", "번역 계속", "번역 시작"],
           "again": ["다시 번역", "다시 번역해"], "clear": ["자막 지워", "자막 지우기"],
           "overlay": ["자막 켜고 끄기", "자막 전환"], "mark": ["하이라이트 표시", "여기 표시"], "copy": ["번역 복사"]},
    "ja": {"pause": ["翻訳停止", "翻訳を止めて", "翻訳一時停止"], "resume": ["翻訳再開", "翻訳を再開", "翻訳開始"],
           "again": ["もう一度翻訳", "翻訳し直して"], "clear": ["字幕を消して", "字幕クリア"],
           "overlay": ["字幕切り替え"], "mark": ["ハイライトを付けて", "ここマーク"], "copy": ["翻訳をコピー"]},
    "vi": {"pause": ["tạm dừng dịch", "dừng dịch"], "resume": ["tiếp tục dịch", "bắt đầu dịch"], "again": ["dịch lại"],
           "clear": ["xóa phụ đề"], "overlay": ["bật tắt phụ đề"], "mark": ["đánh dấu điểm nhấn"], "copy": ["sao chép bản dịch"]},
    "th": {"pause": ["หยุดแปล", "หยุดแปลชั่วคราว"], "resume": ["แปลต่อ", "เริ่มแปล"], "again": ["แปลใหม่", "แปลอีกครั้ง"],
           "clear": ["ล้างซับ", "ล้างคำบรรยาย"], "overlay": ["เปิดปิดซับ"], "mark": ["ทำเครื่องหมายไฮไลต์"], "copy": ["คัดลอกคำแปล"]},
    "id": {"pause": ["jeda terjemahan", "berhenti menerjemahkan"], "resume": ["lanjutkan terjemahan", "mulai menerjemahkan"],
           "again": ["terjemahkan lagi"], "clear": ["hapus subtitle"], "overlay": ["nyalakan matikan subtitle"],
           "mark": ["tandai sorotan"], "copy": ["salin terjemahan"]},
    "es": {"pause": ["pausar traducción", "detener traducción"], "resume": ["reanudar traducción", "continuar traducción"],
           "again": ["traducir otra vez", "traducir de nuevo"], "clear": ["borrar subtítulos"], "overlay": ["mostrar ocultar subtítulos"],
           "mark": ["marcar momento", "marcar esto"], "copy": ["copiar traducción"]},
    "pt": {"pause": ["pausar tradução", "parar tradução"], "resume": ["retomar tradução", "continuar tradução"],
           "again": ["traduzir de novo", "traduzir novamente"], "clear": ["limpar legendas"], "overlay": ["mostrar ocultar legendas"],
           "mark": ["marcar momento", "marcar isso"], "copy": ["copiar tradução"]},
    "ru": {"pause": ["пауза перевода", "остановить перевод"], "resume": ["продолжить перевод", "начать перевод"],
           "again": ["перевести снова", "перевести заново"], "clear": ["очистить субтитры"], "overlay": ["переключить субтитры"],
           "mark": ["отметить момент", "отметить это"], "copy": ["копировать перевод"]},
    "de": {"pause": ["übersetzung pausieren", "übersetzung stoppen"], "resume": ["übersetzung fortsetzen", "übersetzung starten"],
           "again": ["nochmal übersetzen", "neu übersetzen"], "clear": ["untertitel löschen"], "overlay": ["untertitel umschalten"],
           "mark": ["highlight markieren", "das markieren"], "copy": ["übersetzung kopieren"]},
    "fr": {"pause": ["pause traduction", "arrête la traduction"], "resume": ["reprendre la traduction", "reprends la traduction"],
           "again": ["retraduire", "traduire encore"], "clear": ["effacer les sous-titres"], "overlay": ["afficher masquer les sous-titres"],
           "mark": ["marquer ce moment", "marquer ça"], "copy": ["copier la traduction"]},
}

# 前後多說的客氣話、叫名字，比對前先拿掉
_EXTRA = re.compile(r"^(?:livelingo|嘿|hey|ok|okay|請|请|please|麻煩|麻烦|幫我|帮我)+|(?:livelingo|please|吧|一下|啦|了|하세요|해줘|해 줘|주세요|줘|してください|ください|して|お願い)+$",
                    re.IGNORECASE)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith(("P", "S", "Z")) and not ch.isspace())
    prev = None
    while prev != text:
        prev, text = text, _EXTRA.sub("", text)
    return text


_TABLE: dict[str, dict[str, str]] = {}


def _table(ui_lang: str) -> dict[str, str]:
    if ui_lang not in _TABLE:
        out: dict[str, str] = {}
        for lang in ("en", ui_lang):
            for cmd, words in (PHRASES.get(lang) or {}).items():
                for w in words:
                    out[_norm(w)] = cmd
        _TABLE[ui_lang] = out
    return _TABLE[ui_lang]


def _ui_langs(lang: str) -> list[str]:
    """語言代碼 → 有說法的介面語言（zh 兩種中文都算）。"""
    lang = str(lang or "")
    if lang in PHRASES:
        return [lang]
    if lang.split("-")[0] == "zh":
        return ["zh-TW", "zh-CN"]
    return [lang.split("-")[0]] if lang.split("-")[0] in PHRASES else []


def match(text: str, ui_lang: str, *more: str) -> str:
    """整句話是指令就回傳指令名稱（pause、again…），不是就回傳空字串。
    看介面語言、英文，以及 more 給的語言（我說的語言）的說法。"""
    said = _norm(text)
    if not said or len(said) > 40:
        return ""
    for lang in (ui_lang, *more):
        for code in _ui_langs(lang) or ["en"]:
            hit = _table(code).get(said, "")
            if hit:
                return hit
    return ""


def examples(ui_lang: str) -> dict[str, str]:
    """{指令: 第一個說法}，設定頁顯示用。"""
    table = PHRASES.get(ui_lang) or PHRASES["en"]
    return {cmd: table[cmd][0] for cmd in COMMANDS if table.get(cmd)}
