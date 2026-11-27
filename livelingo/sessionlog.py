"""這一場的紀錄（2.18.0）：
- 通話紀錄匯出：說話者、時間、原文、翻譯排成 Markdown 或 Word（.docx）檔。
- 存成字幕檔：這一場的翻譯照時間排成 .srt，自己錄的影片可以直接掛上去。
- 精華標記的時間點也一起寫進去，方便剪片。
「這一場」= 畫面上現在的對話（按「清除」就從頭算），時間從第一則開始算。"""

import io
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .chatpaint import NAMES, speaker_name

HEAD = {
    "zh-TW": ("通話紀錄", "時間", "誰", "原文", "翻譯", "精華", "影片時間"),
    "zh-CN": ("通话记录", "时间", "谁", "原文", "翻译", "精华", "视频时间"),
    "en": ("Call log", "Time", "Who", "Original", "Translation", "Highlights", "Video time"),
    "ko": ("통화 기록", "시간", "누구", "원문", "번역", "하이라이트", "영상 시간"),
    "ja": ("通話記録", "時刻", "誰", "原文", "翻訳", "ハイライト", "動画の時間"),
    "vi": ("Nhật ký cuộc gọi", "Giờ", "Ai", "Nguyên văn", "Bản dịch", "Điểm nhấn", "Thời điểm video"),
    "th": ("บันทึกการสนทนา", "เวลา", "ใคร", "ต้นฉบับ", "คำแปล", "ไฮไลต์", "เวลาในวิดีโอ"),
    "id": ("Log panggilan", "Waktu", "Siapa", "Asli", "Terjemahan", "Sorotan", "Waktu video"),
    "es": ("Registro de la llamada", "Hora", "Quién", "Original", "Traducción", "Momentos destacados", "Tiempo del vídeo"),
    "pt": ("Registro da chamada", "Hora", "Quem", "Original", "Tradução", "Destaques", "Tempo do vídeo"),
    "ru": ("Журнал разговора", "Время", "Кто", "Оригинал", "Перевод", "Лучшие моменты", "Время в видео"),
    "de": ("Gesprächsprotokoll", "Zeit", "Wer", "Original", "Übersetzung", "Highlights", "Videozeit"),
    "fr": ("Journal de l'appel", "Heure", "Qui", "Original", "Traduction", "Moments forts", "Temps vidéo"),
}
KINDS = ("md", "docx", "srt")
CUE_MIN, CUE_MAX = 1.5, 7.0      # 一句字幕最少、最多顯示幾秒


def rows(messages: list[dict], voice_only: bool = False) -> list[dict]:
    """要寫進紀錄的訊息：翻好了的、沒有出錯的。voice_only：只要語音（通話）。"""
    out = []
    for m in messages:
        if m.get("pending") or m.get("error") or not str(m.get("original") or "").strip():
            continue
        if voice_only and m.get("via") not in ("heard", "spoken"):
            continue
        out.append(m)
    return sorted(out, key=lambda m: float(m.get("at") or 0))


def started_at(messages: list[dict]) -> float:
    """這一場從什麼時候開始：第一則開始說（或讀到）的時間。"""
    starts = [_start(m) for m in messages if m.get("at")]
    return min(starts) if starts else time.time()


def _start(m: dict) -> float:
    """這一句開始說的時間：顯示時間往前推辨識和說話花的時間。"""
    return float(m.get("at") or 0) - float(m.get("hear") or 0) - float(m.get("length") or 0)


def _who(m: dict, ui_lang: str) -> str:
    names = NAMES.get(ui_lang) or NAMES["en"]
    if m.get("who") == "me":
        return names["me"]
    if m.get("speaker"):
        return speaker_name(int(m["speaker"]), ui_lang, str(m.get("voice") or ""))
    return names["screen" if m.get("via") == "screen" else "heard"]


def clock(seconds: float) -> str:
    """影片裡的時間 1:02:03 / 12:34。"""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60:02d}:{rest % 60:02d}"


def _translation(m: dict) -> str:
    t = str(m.get("translated") or "").strip()
    return "" if t == str(m.get("original") or "").strip() else t


def markdown(items: list[dict], marks: list[dict], ui_lang: str, start: float) -> str:
    head = HEAD.get(ui_lang) or HEAD["en"]
    day = time.strftime("%Y-%m-%d %H:%M", time.localtime(start))
    lines = [f"# LiveLingo {head[0]}", "", day, "", f"| {head[1]} | {head[2]} | {head[3]} | {head[4]} |", "|---|---|---|---|"]

    def cell(text: str) -> str:
        return " ".join(str(text).split()).replace("|", "\\|")

    for m in items:
        stamp = time.strftime("%H:%M:%S", time.localtime(float(m["at"])))
        lines.append(f"| {stamp} | {cell(_who(m, ui_lang))} | {cell(m['original'])} | {cell(_translation(m))} |")
    if marks:
        lines += ["", f"## {head[5]}", "", f"| {head[6]} | {head[1]} | {head[3]} | {head[4]} |", "|---|---|---|---|"]
        for k in marks:
            lines.append(f"| {clock(k['offset'])} | {time.strftime('%H:%M:%S', time.localtime(k['at']))} | "
                         f"{cell(k.get('original', ''))} | {cell(k.get('translated', ''))} |")
    return "\n".join(lines) + "\n"


def _para(text: str, bold: bool = False, size: int = 0) -> str:
    props = ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "")
    run_props = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:p><w:r>{run_props}<w:t xml:space="preserve">{escape(str(text))}</w:t></w:r></w:p>'


def _table(header: tuple, body: list[tuple]) -> str:
    def row(cells, bold=False):
        return "<w:tr>" + "".join(f"<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>{_para(c, bold)}</w:tc>" for c in cells) + "</w:tr>"
    borders = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                      for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    return (f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{borders}</w:tblBorders></w:tblPr>'
            + row(header, True) + "".join(row(r) for r in body) + "</w:tbl>")


def docx(items: list[dict], marks: list[dict], ui_lang: str, start: float) -> bytes:
    """一個最簡單的 Word 檔（不用另外裝套件）：標題、日期、一張表；有精華標記的話再一張表。"""
    head = HEAD.get(ui_lang) or HEAD["en"]
    body = [_para(f"LiveLingo {head[0]}", True, 32), _para(time.strftime("%Y-%m-%d %H:%M", time.localtime(start)))]
    body.append(_table(head[1:5], [(time.strftime("%H:%M:%S", time.localtime(float(m["at"]))), _who(m, ui_lang),
                                    m["original"], _translation(m)) for m in items]))
    if marks:
        body += [_para(""), _para(head[5], True, 28)]
        body.append(_table((head[6], head[1], head[3], head[4]), [(clock(k["offset"]), time.strftime("%H:%M:%S", time.localtime(k["at"])),
                                                                  k.get("original", ""), k.get("translated", "")) for k in marks]))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(body) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
                "</w:body></w:document>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", document)
    return out.getvalue()


def _stamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt(items: list[dict], start: float, with_original: bool = False) -> str:
    """有時間軸的字幕檔。時間從這一場開始算（錄影從同一個時候開始的話，直接掛上去就對得上）。
    每句從開始說的時候出現，到下一句開始（最多 7 秒）。"""
    cues = []
    shown = [m for m in items if _translation(m) or m.get("who") == "me" or m.get("translated")]
    for i, m in enumerate(shown):
        begin = max(0.0, _start(m) - start)
        nxt = _start(shown[i + 1]) - start if i + 1 < len(shown) else begin + CUE_MAX
        end = min(begin + CUE_MAX, max(begin + CUE_MIN, nxt - 0.05))
        text = _translation(m) or str(m.get("original") or "")
        if with_original and _translation(m):
            text = f"{m['original'].strip()}\n{text}"
        cues.append(f"{len(cues) + 1}\n{_stamp(begin)} --> {_stamp(end)}\n{text.strip()}\n")
    return "\n".join(cues)


def unique(folder: Path, stem: str, ext: str) -> Path:
    path, n = folder / f"{stem}.{ext}", 2
    while path.exists():
        path, n = folder / f"{stem} ({n}).{ext}", n + 1
    return path


def save(folder: Path, kind: str, items: list[dict], marks: list[dict], ui_lang: str, start: float) -> Path:
    """存檔，回傳檔案路徑。kind：md / docx / srt。"""
    folder.mkdir(parents=True, exist_ok=True)
    stem = "LiveLingo " + time.strftime("%Y-%m-%d %H-%M", time.localtime(start))
    path = unique(folder, stem, kind)
    if kind == "docx":
        path.write_bytes(docx(items, marks, ui_lang, start))
    elif kind == "srt":
        path.write_text(srt(items, start), encoding="utf-8-sig")
    else:
        path.write_text(markdown(items, marks, ui_lang, start), encoding="utf-8")
    return path
