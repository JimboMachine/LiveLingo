"""
影片、音訊檔 → 雙語字幕（2.13.0）：選一個 mp4 / mkv / mp3 / wav …，LiveLingo 把裡面的人聲一句一句聽出來（Whisper）、
翻成我的語言，存成一個 .srt（放在影片旁邊，播放器會自動載入），原文在上、譯文在下。
全部在這台電腦上做，不用上傳影片。翻好的句子也會記進「作品」（works.py）：之後用 LiveLingo 看同一部、
聽到同一句，直接用這邊的翻法。

聲音是用 faster-whisper 自帶的 PyAV 解開的，所以不用另外裝 ffmpeg。
這裡只放「不碰整個程式」的部分：檔案判斷、切句、寫 .srt、背景工作（Job）。辨識、翻譯、進度通知都由呼叫的人傳進來。
"""

import re
import threading
import time
from pathlib import Path

SR = 16000
EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv", ".wmv", ".ts", ".mpg", ".mpeg",
        ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"}
MAX_SECONDS = 3 * 3600      # 解開之後整段聲音都放在記憶體裡（每小時約 230 MB），所以一次最長三小時
MAX_SEGMENT = 12.0          # 一句最長幾秒（辨識模型一次最多聽這麼長最準，跟即時收音一樣）
MIN_GAP = 0.3               # 兩句之間至少留這麼多（字幕不黏在一起）
MODES = ("both", "tr", "src")      # 原文＋譯文、只有譯文、只有原文


def is_media(path) -> bool:
    return Path(str(path)).suffix.lower() in EXTS


def decode(path: str):
    """把影片或音訊檔裡的聲音解成 16 kHz 單聲道（float32，-1 ~ 1）。沒有聲音軌的會丟出錯誤。
    不用 faster_whisper.audio.decode_audio：它呼叫 av.open 時帶了新版 PyAV 已經不收的參數（我們鎖的 av 19 會直接出錯）。"""
    import av
    import numpy as np

    chunks = []
    with av.open(str(path), mode="r") as container:
        if not container.streams.audio:
            raise ValueError("no audio track")
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):      # 剩在重新取樣器裡的最後一小段
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def cut(audio) -> list[tuple[float, float]]:
    """有人說話的地方，一句一句切出來：[(開始秒, 結束秒)]。太長的在停頓處切開（最長 MAX_SEGMENT 秒）。"""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(threshold=0.5, min_speech_duration_ms=250, min_silence_duration_ms=500, speech_pad_ms=150,
                      max_speech_duration_s=MAX_SEGMENT)
    out = []
    for t in get_speech_timestamps(audio, opts):
        start, end = t["start"] / SR, t["end"] / SR
        if end - start >= 0.3:
            out.append((round(start, 3), round(end, 3)))
    return out


def stamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt_text(cues: list[dict], mode: str = "both") -> str:
    """cues：[{"start", "end", "text", "tr"}]。both：原文在上、譯文在下；tr：只有譯文；src：只有原文。
    沒有譯文的句子（原文本來就是我的語言）只放原文。時間一定往後走、不重疊。"""
    out, n, last_end = [], 0, 0.0
    for c in cues:
        text, tr = str(c.get("text") or "").strip(), str(c.get("tr") or "").strip()
        lines = [tr or text] if mode == "tr" else [text] if mode == "src" else [text] + ([tr] if tr and tr != text else [])
        lines = [x for x in lines if x]
        if not lines:
            continue
        start = max(float(c["start"]), last_end)
        end = max(float(c["end"]), start + 0.5)
        n += 1
        out.append(f"{n}\n{stamp(start)} --> {stamp(end)}\n" + "\n".join(lines) + "\n")
        last_end = end
    return "\n".join(out)


def output_path(media: str, label: str) -> Path:
    """.srt 放在影片旁邊：<影片名>.<label>.srt；已經有了就加 (2)、(3)，不蓋掉之前的。"""
    media = Path(media)
    stem = re.sub(r"[\\/:*?\"<>|]+", "_", media.stem)[:120] or "subtitles"
    base = media.with_name(f"{stem}.{label}.srt")
    path, n = base, 1
    while path.exists():
        n += 1
        path = media.with_name(f"{stem}.{label} ({n}).srt")
    return path


def write_srt(path: Path, text: str, fallback: Path | None = None) -> Path:
    """存起來。影片旁邊不能寫（唯讀的光碟、網路磁碟）就改存 fallback 資料夾。BOM 讓 Windows 的播放器認得是 UTF-8。"""
    try:
        path.write_text(text, encoding="utf-8-sig")
        return path
    except OSError:
        if fallback is None:
            raise
        fallback.mkdir(parents=True, exist_ok=True)
        alt = fallback / path.name
        alt.write_text(text, encoding="utf-8-sig")
        return alt


class Job(threading.Thread):
    """
    一個檔案的工作：解聲音 → 切句 → 一句一句辨識、翻譯 → 存 .srt。
    recognize(音訊, 語言清單) → (文字, 語言, 沒把握) 或 None；translate(原文, 語言, 前面幾句的原文) → 譯文；
    on_state(狀態) 進度有變就呼叫；wait() 回傳 True 表示現在有別的翻譯在排隊（聊天、語音），這一句先等一下。
    結果在 self.result：{"ok", "path", "cues", "partial", "error"}。
    """

    def __init__(self, media: str, langs: list[str], target_family: str, label: str, mode: str, recognize, translate,
                 on_state=None, wait=None, fallback: Path | None = None, decode_fn=None, cut_fn=None, log=None, on_done=None):
        super().__init__(daemon=True, name="filesubs")
        self.media, self.langs, self.target_family, self.label = str(media), list(langs), target_family, label
        self.mode = mode if mode in MODES else "both"
        self.recognize, self.translate = recognize, translate
        self.on_state = on_state or (lambda state: None)
        self.wait = wait or (lambda: False)
        self.fallback = fallback
        self.decode_fn, self.cut_fn = decode_fn or decode, cut_fn or cut
        self.log = log or (lambda text: None)
        self.on_done = on_done or (lambda result: None)      # 做完（成功、失敗、取消）呼叫一次
        self.cancel = threading.Event()
        self.state = {"stage": "decoding", "done": 0, "total": 0, "cues": 0, "eta": None, "last": "", "name": Path(media).name}
        self.result: dict = {}
        self.cues: list[dict] = []

    def _tell(self, **changes) -> None:
        self.state.update(changes)
        try:
            self.on_state(dict(self.state))
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        try:
            self._work()
        except Exception as ex:  # noqa: BLE001
            self.log(f"subtitle job failed: {ex!r}")
            self.result = {"ok": False, "error": "failed", "detail": str(ex)[:300], "cues": len(self.cues)}
        self._tell(stage="done" if self.result.get("ok") else "failed")
        try:
            self.on_done(dict(self.result))
        except Exception as ex:  # noqa: BLE001
            self.log(f"subtitle job callback failed: {ex!r}")

    def _work(self) -> None:
        self._tell(stage="decoding")
        try:
            audio = self.decode_fn(self.media)
        except Exception as ex:  # noqa: BLE001
            self.result = {"ok": False, "error": "unreadable", "detail": str(ex)[:300]}
            return
        if len(audio) / SR > MAX_SECONDS:
            self.result = {"ok": False, "error": "too_long", "seconds": int(len(audio) / SR)}
            return
        if not len(audio):
            self.result = {"ok": False, "error": "no_audio"}
            return
        self._tell(stage="cutting", seconds=int(len(audio) / SR))
        spans = self.cut_fn(audio)
        if not spans:
            self.result = {"ok": False, "error": "no_speech"}
            return
        total, started, previous = len(spans), time.monotonic(), []
        self._tell(stage="working", total=total, done=0)
        for n, (start, end) in enumerate(spans, 1):
            if self.cancel.is_set():
                break
            while self.wait() and not self.cancel.is_set():
                time.sleep(0.4)      # 聊天、語音的翻譯先；檔案的不趕時間
            if self.cancel.is_set():
                break
            chunk = audio[int(start * SR):int(end * SR)]
            heard = self.recognize(chunk, self.langs)
            if heard:
                text, lang, unsure = heard
                if not (self.cues and self.cues[-1]["text"] == text and start - self.cues[-1]["end"] < 3):      # 模型卡住會重複同一句
                    tr = ""
                    if lang != self.target_family:
                        tr = (self.translate(text, lang, previous[-2:]) or "").strip()
                    self.cues.append({"start": start, "end": end, "text": text, "tr": tr, "lang": lang, "unsure": bool(unsure)})
                    previous.append(text)
                    self.state["last"] = tr or text
            elapsed = time.monotonic() - started
            self._tell(done=n, cues=len(self.cues), eta=int(elapsed / n * (total - n)) if n >= 3 else None)
        partial = self.cancel.is_set()
        if not self.cues:
            self.result = {"ok": False, "error": "no_speech" if not partial else "cancelled"}
            return
        path = write_srt(output_path(self.media, self.label), srt_text(self.cues, self.mode), self.fallback)
        langs = sorted({c["lang"] for c in self.cues})
        self.result = {"ok": True, "path": str(path), "cues": len(self.cues), "partial": partial, "langs": langs,
                       "unsure": sum(1 for c in self.cues if c["unsure"])}
