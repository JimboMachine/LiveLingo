"""
語音辨識：把一直進來的聲音切成一句一句（偵測有人在說話、說完了），再用 Whisper 轉成文字。
全部在本機執行。
"""

import queue
import re
import threading
import time
from pathlib import Path

import numpy as np

from .audio import SR

# Whisper 在沒人說話、只有音樂或雜音時常會「幻聽」出這類句子，直接丟掉
_HALLUCINATIONS = re.compile(
    r"시청해\s*주셔서|구독과\s*좋아요|MBC\s*뉴스|한글자막|자막\s*제공|다음\s*영상에서|"
    r"字幕|謝謝觀看|感謝觀看|謝謝收看|感謝收看|請不吝|點贊|订阅|訂閱|明鏡|"
    r"thanks for watching|thank you for watching|subscribe", re.IGNORECASE)


QUIET = 0.0008      # 整段聲音的最大值比這個還小，就當成完全沒聲音（關掉的麥克風送來的是一串 0）
MAX_GAIN = 30.0     # 太小聲的聲音最多放大這麼多倍


def level_of(block: np.ndarray) -> float:
    """把一小段聲音的音量換成 0~1：-60 dB 以下當作沒聲音，-10 dB 以上當作滿格。"""
    rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
    db = 20 * np.log10(max(rms, 1e-6))
    return float(min(1.0, max(0.0, (db + 60) / 50)))


class Segmenter:
    """把連續的聲音切成一句一句。用 Silero VAD 判斷哪裡有人說話，所以遊戲音效、音樂不太會被當成說話。"""

    def __init__(self, end_silence: float = 0.6, max_len: float = 12.0, min_len: float = 0.4, step: float = 0.3):
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        self._vad = get_speech_timestamps
        self._opts = VadOptions(threshold=0.5, min_speech_duration_ms=250,
                                min_silence_duration_ms=int(end_silence * 1000), speech_pad_ms=200)
        self.max_len = int(max_len * SR)
        self.min_len = int(min_len * SR)
        self.step = int(step * SR)
        self.buf = np.zeros(0, dtype=np.float32)
        self._since = 0
        self.speaking = False

    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        """餵進一小段聲音，回傳「已經說完」的句子（通常是空的或一句）。"""
        self.buf = np.concatenate([self.buf, block])
        self._since += len(block)
        if self._since < self.step:
            return []
        self._since = 0
        peak = float(np.abs(self.buf).max())
        if peak < QUIET:      # 完全沒聲音，不用跑偵測
            self.buf = self.buf[-SR // 2:]
            self.speaking = False
            return []
        # 麥克風音量開得很小的時候，先放大再判斷，不然小聲說話會整句被當成沒聲音
        loud = self.buf if peak >= 0.1 else np.clip(self.buf * min(MAX_GAIN, 0.1 / peak), -1.0, 1.0)
        ts = self._vad(loud, self._opts)
        if not ts:
            self.buf = self.buf[-SR // 2:]
            self.speaking = False
            return []
        out = [loud[t["start"]:t["end"]] for t in ts[:-1]]
        last = ts[-1]
        if len(self.buf) - last["end"] > SR // 10:     # 最後一句後面已經安靜下來 = 說完了
            out.append(loud[last["start"]:last["end"]])
            self.buf = self.buf[-SR // 4:]
            self.speaking = False
        elif len(self.buf) - last["start"] >= self.max_len:   # 一直講不停，先切一段出來翻
            out.append(loud[last["start"]:])
            self.buf = np.zeros(0, dtype=np.float32)
            self.speaking = False
        else:
            self.buf = self.buf[max(0, last["start"] - SR // 5):]
            self.speaking = True
        return [u for u in out if len(u) >= self.min_len]


class Recognizer:
    def __init__(self, model_dir: Path, threads: int = 4, device: str = "cpu", compute_type: str = "int8",
                 window: int = 15):
        import faster_whisper.transcribe as fw
        from faster_whisper import WhisperModel

        self.model = WhisperModel(str(model_dir), device=device, compute_type=compute_type, cpu_threads=threads,
                                  local_files_only=True)
        self.window = window
        self._lock = threading.Lock()
        # Whisper 預設每次都把聲音補到 30 秒再算。我們一句最長 12 秒，補到 15 秒就夠，
        # 實測準確度一樣、速度快一倍以上（4 核心 CPU：3.6 秒 → 1.6 秒）。
        self._fw = fw
        self._orig_pad = fw.pad_or_trim
        frames = window * 100

        def short_pad(array, length: int = 3000, *, axis: int = -1):
            return self._orig_pad(array, frames, axis=axis)

        self._short_pad = short_pad

    @staticmethod
    def pick_language(probs, langs: list[str], stay: float = 1.0) -> str:
        """
        從語音模型給的機率裡，在 langs 這幾個語言中選一個。langs[0] 是「目前以為的語言」：
        stay 大於 1 的時候它比較不容易被換掉，短短一兩個字的句子才不會讓語言跳來跳去。
        """
        allowed = [(l, p * (stay if l == langs[0] else 1.0)) for l, p in probs if l in langs]
        return max(allowed, key=lambda x: x[1])[0] if allowed else langs[0]

    def _decode(self, audio: np.ndarray, lang: str | None):
        return self.model.transcribe(
            audio, language=lang, beam_size=1, temperature=0.0, condition_on_previous_text=False,
            without_timestamps=True, chunk_length=self.window,
            initial_prompt="以下是繁體中文的句子。" if lang == "zh" else None)

    def _run(self, audio: np.ndarray, langs: list[str], share_encoder: bool = True, stay: float = 1.0):
        m = self.model
        lang, enc = langs[0], None
        if len(langs) > 1:
            # 先判斷這句是哪個語言（只在指定的幾個語言裡選）
            if share_encoder:
                # 判斷語言和辨識文字要用到同一份「聲音編碼」，算一次就好，省掉一半時間
                feat = m.feature_extractor(audio, chunk_length=self.window)
                seg = self._short_pad(feat[:, :min(m.feature_extractor.nb_max_frames, feat.shape[-1] - 1)])
                enc = m.encode(seg)
                probs = [(tok[2:-2], p) for tok, p in m.model.detect_language(enc)[0]]
            else:
                _, _, probs = m.detect_language(audio)
            lang = self.pick_language(probs, langs, stay)
        if enc is not None:
            real_encode, pending = m.encode, [enc]
            m.encode = lambda features: pending.pop() if pending else real_encode(features)
        try:
            segments, _ = self._decode(audio, lang)
            parts = []
            for s in segments:
                if s.no_speech_prob > 0.8 or s.avg_logprob < -1.2 or s.compression_ratio > 2.4:
                    continue
                parts.append(s.text.strip())
        finally:
            if enc is not None:
                del m.encode
        return " ".join(p for p in parts if p).strip(), lang

    def transcribe(self, audio: np.ndarray, langs: list[str], stay: float = 1.0) -> tuple[str, str] | None:
        """audio：16 kHz 單聲道。langs：可能的語言（zh / ko / en / ja），多於一個時會自動判斷是哪個。"""
        langs = list(langs)      # 辨識到一半時清單可能被換掉（自動偵測換語言），先留一份
        with self._lock:
            self._fw.pad_or_trim = self._short_pad
            try:
                try:
                    text, lang = self._run(audio, langs, stay=stay)
                except Exception:  # noqa: BLE001  加速的算法萬一不相容，退回標準算法
                    self._fw.pad_or_trim = self._orig_pad
                    text, lang = self._run(audio, langs, share_encoder=False, stay=stay)
            finally:
                self._fw.pad_or_trim = self._orig_pad
        if not text or _HALLUCINATIONS.search(text):
            return None
        if len(re.sub(r"[\W_]+", "", text)) < 2:
            return None
        return text, lang


class SpeechPipeline(threading.Thread):
    """
    一條收音來源一條管線：收音 → 切句 → 辨識 → 交給 on_text(來源, 文字, 語言, 辨識花的秒數)。
    辨識器（Recognizer）是共用的，兩條管線不會同時搶 CPU。
    """

    def __init__(self, capture, recognizer: Recognizer, langs: list[str], on_text, on_state=None,
                 end_silence: float = 0.6, on_level=None):
        super().__init__(daemon=True)
        self.capture = capture
        self.recognizer = recognizer
        self.langs = langs
        self.stay = 1.0          # 大於 1：比較相信 langs[0]（自動偵測對方語言時用）
        self.on_text = on_text
        self.on_state = on_state or (lambda kind, speaking: None)
        # on_level(來源, 現在的音量 0~1, 已經連續幾秒完全沒聲音)：給介面畫音量條、提醒「麥克風沒聲音」
        self.on_level = on_level or (lambda kind, level, silent_for: None)
        self.segmenter = Segmenter(end_silence=end_silence)
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()
        self.capture.stop()

    def run(self):
        self.capture.start()
        was_speaking = False
        last_sound = time.time()
        while not self._halt.is_set():
            try:
                block = self.capture.q.get(timeout=0.5)
            except queue.Empty:
                if not self.capture.is_alive():
                    return
                continue
            # 辨識的時候聲音會先積在佇列裡，一次拿完比較不會落後
            blocks = [block]
            try:
                while True:
                    blocks.append(self.capture.q.get_nowait())
            except queue.Empty:
                pass
            audio = np.concatenate(blocks)
            now = time.time()
            if len(audio) and float(np.abs(audio).max()) >= QUIET:
                last_sound = now
            self.on_level(self.capture.kind, level_of(audio[-SR // 5:]), now - last_sound)
            for utter in self.segmenter.feed(audio):
                t = time.time()
                try:
                    res = self.recognizer.transcribe(utter, self.langs, self.stay)
                except Exception as ex:  # noqa: BLE001
                    self.on_text(self.capture.kind, str(ex), "error", 0.0)
                    continue
                if res:
                    self.on_text(self.capture.kind, res[0], res[1], time.time() - t)
            if self.segmenter.speaking != was_speaking:
                was_speaking = self.segmenter.speaking
                self.on_state(self.capture.kind, was_speaking)
