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
OWN_STAY = 1.5      # 設過語言的人：主語言比副語言優先這麼多（短短一兩個字不會被聽成副語言；整句說副語言還是換得過去）


def not_sure(avg_logprob: float, no_speech_prob: float) -> bool:
    """這一段辨識出來了，但模型自己沒什麼把握（背景很吵、說得很快很含糊）：介面會標「可能聽錯」。"""
    return avg_logprob < -0.75 or no_speech_prob > 0.5


def level_of(block: np.ndarray) -> float:
    """把一小段聲音的音量換成 0~1：-60 dB 以下當作沒聲音，-10 dB 以上當作滿格。"""
    rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
    db = 20 * np.log10(max(rms, 1e-6))
    return float(min(1.0, max(0.0, (db + 60) / 50)))


class Segmenter:
    """把連續的聲音切成一句一句。用 Silero VAD 判斷哪裡有人說話，所以遊戲音效、音樂不太會被當成說話。"""

    def __init__(self, end_silence: float = 0.6, max_len: float = 12.0, min_len: float = 0.4, step: float = 0.3,
                 threshold: float = 0.5):
        from faster_whisper.vad import get_speech_timestamps

        self._vad = get_speech_timestamps
        self.tune(end_silence, threshold)
        self.max_len = int(max_len * SR)
        self.min_len = int(min_len * SR)
        self.step = int(step * SR)
        self.buf = np.zeros(0, dtype=np.float32)
        self._since = 0
        self._gain = 1.0
        self.speaking = False

    def so_far(self) -> np.ndarray | None:
        """還在說的那一句，到目前為止的聲音（已經照音量放大過）。沒有人在說話時是 None。"""
        if not self.speaking or not len(self.buf):
            return None
        return self.buf.copy() if self._gain == 1.0 else np.clip(self.buf * self._gain, -1.0, 1.0)

    def tune(self, end_silence: float, threshold: float) -> None:
        """改「停頓多久算說完」和「多小聲算有人說話」。收音中也可以改，下一次判斷就用新的。"""
        from faster_whisper.vad import VadOptions

        self._opts = VadOptions(threshold=threshold, min_speech_duration_ms=250,
                                min_silence_duration_ms=int(end_silence * 1000), speech_pad_ms=200)

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
        self._gain = 1.0 if peak >= 0.1 else min(MAX_GAIN, 0.1 / peak)
        loud = self.buf if peak >= 0.1 else np.clip(self.buf * self._gain, -1.0, 1.0)
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


class LivePacer:
    """
    對方還在說的時候，什麼時候先辨識一次（即時字幕）。太常做會佔住辨識器，等他真的說完時整句反而更晚出來，所以：
    至少說了 first 秒才做第一次（太短的半句翻出來常常是錯的）；之後每多聽到 grow 秒才再做；兩次之間至少休息「上一次花的時間 × rest」，
    辨識得慢的電腦（只用處理器）就自動做得少，快的（顯示卡）就做得密。
    """

    def __init__(self, first: float = 1.5, grow: float = 0.6, rest: float = 1.0):
        self.first, self.grow, self.rest = first, grow, rest
        self.reset()

    def reset(self) -> None:
        self._length, self._ended, self._spent = 0.0, 0.0, 0.0

    def due(self, length: float, now: float, busy: bool = False) -> bool:
        """length：這一句到目前為止幾秒。busy：辨識器正在忙別的（另一條收音）。"""
        if busy or length < self.first or length - self._length < self.grow:
            return False
        return now - self._ended >= self._spent * self.rest

    def done(self, length: float, now: float, spent: float) -> None:
        self._length, self._ended, self._spent = length, now, spent


class Recognizer:
    def __init__(self, model_dir: Path, threads: int = 4, device: str = "cpu", compute_type: str = "int8",
                 window: int = 15, on_fallback=None):
        import faster_whisper.transcribe as fw

        self.model_dir, self.threads = model_dir, threads
        # on_fallback(錯誤)：本來要用顯示卡，結果不行（少了檔案、記憶體不夠…），已經改用處理器
        self.on_fallback = on_fallback or (lambda ex: None)
        try:
            self._load(device, compute_type)
        except Exception as ex:  # noqa: BLE001
            if device == "cpu":
                raise
            self._load("cpu", "int8")
            self.on_fallback(ex)
        self.window = window
        self.beam = 1          # 1 = 快；5 = 比較準、慢大約一倍（設定裡的「比較準」）
        self._quick = False    # 這一次是「還在說就先辨識」：一律用快的
        self._lock = threading.Lock()
        # Whisper 預設每次都把聲音補到 30 秒再算。我們一句最長 12 秒，補到 15 秒就夠，
        # 實測準確度一樣、速度快一倍以上（4 核心 CPU：3.6 秒 → 1.6 秒）。
        self._fw = fw
        self._orig_pad = fw.pad_or_trim
        frames = window * 100

        def short_pad(array, length: int = 3000, *, axis: int = -1):
            return self._orig_pad(array, frames, axis=axis)

        self._short_pad = short_pad

    def _load(self, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(str(self.model_dir), device=device, compute_type=compute_type,
                                  cpu_threads=self.threads, local_files_only=True)
        self.device = device

    @staticmethod
    def pick_language(probs, langs: list[str], stay: float = 1.0) -> str:
        """
        從語音模型給的機率裡，在 langs 這幾個語言中選一個。langs[0] 是「目前以為的語言」：
        stay 大於 1 的時候它比較不容易被換掉，短短一兩個字的句子才不會讓語言跳來跳去。
        """
        allowed = [(l, p * (stay if l == langs[0] else 1.0)) for l, p in probs if l in langs]
        return max(allowed, key=lambda x: x[1])[0] if allowed else langs[0]

    def busy(self) -> bool:
        """現在是不是正在辨識（兩條收音共用一個辨識器）。"""
        return self._lock.locked()

    def _decode(self, audio: np.ndarray, lang: str | None):
        return self.model.transcribe(
            audio, language=lang, beam_size=1 if self._quick else self.beam, temperature=0.0, condition_on_previous_text=False,
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
            parts, unsure = [], False
            for s in segments:
                if s.no_speech_prob > 0.8 or s.avg_logprob < -1.2 or s.compression_ratio > 2.4:
                    continue
                if not_sure(s.avg_logprob, s.no_speech_prob):
                    unsure = True
                parts.append(s.text.strip())
        finally:
            if enc is not None:
                del m.encode
        return " ".join(p for p in parts if p).strip(), lang, unsure

    def transcribe(self, audio: np.ndarray, langs: list[str], stay: float = 1.0, quick: bool = False) -> tuple[str, str, bool] | None:
        """audio：16 kHz 單聲道。langs：可能的語言（zh / ko / en / ja），多於一個時會自動判斷是哪個。
        quick：話還沒說完、先看看目前聽到什麼（即時字幕）：用最快的算法，結果之後會被整句的取代。
        回傳（文字, 語言, 是不是沒把握）。"""
        langs = list(langs)      # 辨識到一半時清單可能被換掉（自動偵測換語言），先留一份
        with self._lock:
            self._quick = bool(quick)
            self._fw.pad_or_trim = self._short_pad
            try:
                try:
                    text, lang, unsure = self._run(audio, langs, stay=stay)
                except Exception as ex:  # noqa: BLE001
                    if getattr(self, "device", "cpu") != "cpu":
                        # 顯示卡跑不起來（常見：少了 cuDNN、顯示記憶體被遊戲佔滿）：改用處理器，這一句重來
                        self._load("cpu", "int8")
                        self.on_fallback(ex)
                        text, lang, unsure = self._run(audio, langs, stay=stay)
                    else:      # 加速的算法萬一不相容，退回標準算法
                        self._fw.pad_or_trim = self._orig_pad
                        text, lang, unsure = self._run(audio, langs, share_encoder=False, stay=stay)
            finally:
                self._fw.pad_or_trim = self._orig_pad
                self._quick = False
        if not text or _HALLUCINATIONS.search(text):
            return None
        if len(re.sub(r"[\W_]+", "", text)) < 2:
            return None
        return text, lang, unsure


class SpeechPipeline(threading.Thread):
    """
    一條收音來源一條管線：收音 → 切句 → 辨識 → 交給 on_text(來源, 文字, 語言, 辨識花的秒數, 是不是沒把握)。
    辨識器（Recognizer）是共用的，兩條管線不會同時搶 CPU。

    即時字幕：有給 on_partial 而且 live() 是 True 的時候，話還沒說完就先辨識目前聽到的，交給
    on_partial(來源, 文字, 語言)。說完了、要開始辨識整句的時候先叫 on_partial(來源, "", None)（半句不用再翻了）；
    整句照樣走 on_text；那一句最後沒有結果（雜音、太短）就叫 on_partial(來源, None, None) 收回。
    busy()：回傳 True 的時候先不要做（電腦正在忙別的重活）。
    分辨說話者：有給 voices 的話，每一句說完順便算是誰說的，多交一個 speaker=編號 給 on_text。
    每個人自己的語言：有給 speaker_langs 的話（回傳 {編號: [主語言, 副語言]}），辨識之前先看這句大概是誰說的，
    是設過語言的人就只在他的語言裡聽（主語言優先），不用整條收音共用的 langs。
    """

    gate = None      # gate()：現在要不要收音（「按住才說話」沒按著的時候是 False）。None = 一直收
    on_partial = None      # 沒給就不做即時字幕
    voices = None          # voices()：回傳分辨說話者用的東西（有 who(聲音)），沒開那個功能就回傳 None
    speaker_langs = None   # speaker_langs()：{說話者編號: [主語言, 副語言（可以沒有）]}，只有使用者設過語言的人
    _live_text = None
    _live_langs = None     # 還在說的這一句：先看過是誰了，用他的語言（None = 還沒看過，() = 看過了，不是設過語言的人）

    def __init__(self, capture, recognizer: Recognizer, langs: list[str], on_text, on_state=None,
                 end_silence: float = 0.6, on_level=None, threshold: float = 0.5, gate=None, on_partial=None, live=None, busy=None, voices=None,
                 speaker_langs=None):
        super().__init__(daemon=True)
        self.voices = voices
        self.speaker_langs = speaker_langs
        self.on_partial = on_partial
        self.live = live or (lambda: True)
        self.busy = busy or (lambda: False)
        self.pacer = LivePacer()
        self._live_text = None      # 這一句目前已經先顯示出來的字（None = 還沒顯示過）
        self.capture = capture
        self.recognizer = recognizer
        self.langs = langs
        self.stay = 1.0          # 大於 1：比較相信 langs[0]（自動偵測對方語言時用）
        self.on_text = on_text
        self.on_state = on_state or (lambda kind, speaking: None)
        # on_level(來源, 現在的音量 0~1, 已經連續幾秒完全沒聲音)：給介面畫音量條、提醒「麥克風沒聲音」
        self.on_level = on_level or (lambda kind, level, silent_for: None)
        self.segmenter = Segmenter(end_silence=end_silence, threshold=threshold)
        self.gate = gate
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()
        self.capture.stop()

    def run(self):
        self.capture.start()
        was_speaking = False
        gate_open = self.gate is None      # 有「按住才說話」的閘門時，一開始當作沒按著
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
            if self.gate is not None:
                open_now = bool(self.gate())
                if not open_now and not gate_open:
                    continue                                      # 沒按著：這段聲音不要
                if not open_now:
                    audio = np.zeros(SR, dtype=np.float32)        # 剛放開：補一秒安靜，讓剛剛那句馬上結束送去辨識
                elif not gate_open:
                    self.segmenter.buf = np.zeros(0, dtype=np.float32)      # 剛按下：從這裡開始算
                gate_open = open_now
            self._hear(audio)
            if self.segmenter.speaking != was_speaking:
                was_speaking = self.segmenter.speaking
                self.on_state(self.capture.kind, was_speaking)

    def _who(self, utter: np.ndarray, voice=None) -> int | None:
        try:
            voices = self.voices() if self.voices else None
            if not voices:
                return None
            return voices.who(utter) if voice is None else voices.who(utter, voice)
        except Exception:  # noqa: BLE001  分不出來不能讓字幕也沒有
            return None

    def _langs_of(self, sound: np.ndarray):
        """
        這段聲音大概是誰說的；他有設自己的語言就回傳（他的語言, 聲紋），不然回傳（None, 聲紋）。
        沒有人設過語言的時候什麼都不算（聲紋是 None），跟以前一樣快。
        """
        try:
            table = self.speaker_langs() if self.speaker_langs else None
            voices = self.voices() if (table and self.voices) else None
            if not voices or not hasattr(voices, "guess"):
                return None, None
            voice, likely = voices.guess(sound)
            return ([l for l in table.get(likely) or [] if l] or None) if likely else None, voice
        except Exception:  # noqa: BLE001  看不出來就照平常的語言聽
            return None, None

    def _hear(self, audio: np.ndarray) -> None:
        """把新進來的聲音交給切句的；說完的句子送去辨識，還在說的那一句看情況先辨識一次。"""
        kind, given = self.capture.kind, False
        for utter in self.segmenter.feed(audio):
            if self._live_text is not None:
                self.on_partial(kind, "", None)      # 說完了：半句不用再翻，把電腦讓給整句
            t = time.time()
            own, voice = self._langs_of(utter)      # 設過語言的人：只在他的主語言、副語言裡聽
            self._live_langs = None
            try:
                res = self.recognizer.transcribe(utter, own or self.langs, OWN_STAY if own else self.stay)
            except Exception as ex:  # noqa: BLE001
                self.on_text(kind, str(ex), "error", 0.0, False)
                continue
            if res:
                given = True
                who = self._who(utter, voice)      # 這一句是誰說的（有開「分辨說話者」才會有）
                self.on_text(kind, res[0], res[1], time.time() - t, bool(res[2]) if len(res) > 2 else False,
                             **({"speaker": who} if who else {}))
                self._live_text = None      # 先顯示的那一則已經被整句接手
                self.pacer.reset()
        if not self.on_partial:
            return
        if not self.segmenter.speaking:
            if self._live_text is not None and not given:
                self.on_partial(kind, None, None)      # 先顯示過了，但最後沒有結果：收回
            self._live_text = None
            self._live_langs = None
            self.pacer.reset()
            return
        so_far = self.segmenter.so_far() if self.live() else None
        if so_far is None or not self.pacer.due(len(so_far) / SR, time.time(), self.recognizer.busy() or self.busy()):
            return
        t = time.time()
        if self._live_langs is None:      # 這一句第一次先辨識：看一次是誰就好，後面幾次沿用
            self._live_langs = tuple(self._langs_of(so_far)[0] or ())
        own = list(self._live_langs)
        try:
            res = self.recognizer.transcribe(so_far, own or self.langs, OWN_STAY if own else self.stay, quick=True)
        except Exception:  # noqa: BLE001  先看一下而已，失敗就等整句
            res = None
        self.pacer.done(len(so_far) / SR, time.time(), time.time() - t)
        if res and res[0] != self._live_text:
            self._live_text = res[0]
            self.on_partial(kind, res[0], res[1])
