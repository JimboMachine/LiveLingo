"""
分辨說話的人：每一句話算出一組「聲紋」（256 個數字），聲紋像的就當成同一個人，照出現的順序編成 1、2、3…。
全部在本機執行，不會傳出去。聲紋平常只留在記憶體裡，程式關掉就沒有了；只有使用者打開「記住取過名字的人」，
取過名字的那幾個人才會存在這台電腦上（livelingo/voicebook.py），下次開就認得。

模型是 WeSpeaker 的 ResNet34（voxceleb_resnet34_LM.onnx，約 27 MB），用 onnxruntime 跑；
它要的輸入是 80 維的 fbank（Kaldi 的算法），這裡用 numpy 自己算，不用多裝套件。

實測（30 段朗讀錄音照程式自己的斷句切成 55 句、7 個人）：同一個人的兩句相似度 95% 在 0.55 以上，
不同人的 99% 在 0.41 以下；門檻用 0.50／0.45 時，打亂順序跑 8 次都剛好分出 7 個人，438 句裡標錯 1 句
（0.45／0.30 會把兩個聲音比較像的人併進別人）。這是乾淨的朗讀聲；Discord 語音會差一些，要在真的環境再調。
一段聲音只有 1 秒時還分得出來，但開始會出錯，所以太短的句子不拿來「認識新的人」。
兩個人同時說話、或背景有很大的遊戲音效時，聲紋會混在一起，這是做不到的部分。
"""

import threading

import numpy as np

from .audio import SR

SAME = 0.50           # 相似度到這裡：確定是同一個人
MAYBE = 0.45          # 介於 MAYBE 和 SAME：大概是他（標給他，但不拿這一句去更新他的聲紋）；比 MAYBE 低就是別人
MIN_SECONDS = 0.7     # 比這個還短的句子不分辨（不標是誰）
NEW_SECONDS = 1.5     # 至少這麼長、而且誰都不像，才算「新的人」
MAX_SPEAKERS = 8      # 最多分幾個人（Discord 語音加上雜音，分太多只會亂）
LONGEST = 12.0        # 一句最多拿前面幾秒去算（斷句本來就不會超過這麼長）

_N_FFT, _FRAME, _SHIFT, _MELS = 512, 400, 160, 80


def _mel_bank() -> np.ndarray:
    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)  # noqa: E731
    low, high = mel(20.0), mel(SR / 2)
    centers = low + (high - low) * np.arange(_MELS + 2) / (_MELS + 1)
    freqs = mel(np.arange(_N_FFT // 2 + 1) * SR / _N_FFT)
    bank = np.zeros((_MELS, _N_FFT // 2 + 1), dtype=np.float32)
    for m in range(_MELS):
        left, center, right = centers[m], centers[m + 1], centers[m + 2]
        bank[m] = np.maximum(0.0, np.minimum((freqs - left) / (center - left), (right - freqs) / (right - center)))
    return bank


_BANK = _mel_bank()
_WINDOW = (0.54 - 0.46 * np.cos(2 * np.pi * np.arange(_FRAME) / (_FRAME - 1))).astype(np.float32)


def fbank(audio: np.ndarray) -> np.ndarray:
    """16 kHz 單聲道的聲音 → (幾格, 80) 的特徵。每格 25 毫秒、每 10 毫秒一格，最後減掉整段的平均（去掉麥克風和音量的差別）。"""
    x = np.asarray(audio, dtype=np.float32) * 32768.0
    if len(x) < _FRAME:
        return np.zeros((0, _MELS), dtype=np.float32)
    n = 1 + (len(x) - _FRAME) // _SHIFT
    frames = x[np.arange(_FRAME)[None, :] + _SHIFT * np.arange(n)[:, None]]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * (1 - 0.97), frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)
    power = np.abs(np.fft.rfft(frames * _WINDOW, _N_FFT, axis=1)) ** 2
    feats = np.log(np.maximum(power @ _BANK.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


class Voiceprint:
    """聲紋模型。embed(聲音) 回傳長度是 1 的向量；聲音太短回傳 None。"""

    def __init__(self, model_path, threads: int = 2):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))      # 少用幾條線：一句只要零點幾秒，不要跟辨識和翻譯搶
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        if len(audio) < MIN_SECONDS * SR:
            return None
        feats = fbank(audio[:int(LONGEST * SR)])
        if len(feats) < 40:
            return None
        out = self._session.run(None, {self._input: feats[None]})[0][0].astype(np.float32)
        norm = float(np.linalg.norm(out))
        return out / norm if norm > 0 else None


class Speakers:
    """把一句一句的聲紋分成幾個人。who(聲紋, 這句幾秒) 回傳這個人的編號（從 1 開始），認不出來回傳 None。"""

    def __init__(self, same: float = SAME, maybe: float = MAYBE, limit: int = MAX_SPEAKERS):
        self.same, self.maybe, self.limit = same, maybe, limit
        self._sums: list[np.ndarray] = []      # 每個人：他說過的句子的聲紋加起來（長的句子算比較重）
        self.counts: list[int] = []            # 每個人說了幾句
        self._known = 0                        # 前面幾個是以前記住的人（seed 放進來的），不算在人數上限裡
        self._lock = threading.Lock()

    def who(self, voice: np.ndarray | None, seconds: float) -> int | None:
        if voice is None or seconds < MIN_SECONDS:
            return None
        with self._lock:
            best, score = -1, -1.0
            for i, total in enumerate(self._sums):
                s = float(voice @ (total / (np.linalg.norm(total) + 1e-9)))
                if s > score:
                    best, score = i, s
            if best >= 0 and score >= self.same:
                if seconds >= 1.0:      # 太短的句子聲紋不準，不拿來改他的聲紋
                    self._sums[best] = self._sums[best] + voice * min(float(seconds), 6.0)
                self.counts[best] += 1
                return best + 1
            if seconds >= NEW_SECONDS and score < self.maybe and len(self._sums) - self._known < self.limit:
                self._sums.append(voice * min(float(seconds), 6.0))
                self.counts.append(1)
                return len(self._sums)
            if best >= 0 and score >= self.maybe:
                self.counts[best] += 1
                return best + 1
            return None

    def guess(self, voice: np.ndarray | None, seconds: float) -> int | None:
        """只看不記：這份聲紋像已經分出來的哪一個人（編號），誰都不夠像回傳 None。
        不會多一個人、不改任何人的聲紋、不算一句。辨識之前先問「大概是誰」用的（才知道要用誰的語言去聽）。"""
        if voice is None or seconds < MIN_SECONDS:
            return None
        with self._lock:
            best, score = -1, -1.0
            for i, total in enumerate(self._sums):
                s = float(voice @ (total / (np.linalg.norm(total) + 1e-9)))
                if s > score:
                    best, score = i, s
            return best + 1 if best >= 0 and score >= self.maybe else None

    def people(self) -> list[dict]:
        with self._lock:
            return [{"id": i + 1, "count": n} for i, n in enumerate(self.counts)]

    def seed(self, voice: np.ndarray, weight: float = 6.0) -> int:
        """放進一個以前記住的人（還沒有人說話之前叫），回傳他的編號。weight：這份聲紋算「說過幾秒」那麼重。"""
        with self._lock:
            self._sums.append(np.asarray(voice, dtype=np.float32) * float(weight))
            self.counts.append(0)
            self._known += 1
            return len(self._sums)

    def voice_of(self, n: int) -> tuple[np.ndarray, float] | None:
        """第 n 個人目前的聲紋（長度是 1）和它有多重（大約是拿來算的句子一共幾秒）。沒有這個人回傳 None。"""
        with self._lock:
            if not 1 <= n <= len(self._sums):
                return None
            norm = float(np.linalg.norm(self._sums[n - 1]))
            return (self._sums[n - 1] / norm, norm) if norm > 0 else None

    def forget(self) -> None:
        """全部重來（換了一群人、或分錯了）。"""
        with self._lock:
            self._sums.clear()
            self.counts.clear()
            self._known = 0


class Voices:
    """模型加上分人，合成一個「這段聲音是誰」。"""

    def __init__(self, model_path, threads: int = 2):
        self.model = Voiceprint(model_path, threads)
        self.speakers = Speakers()

    def who(self, audio: np.ndarray, voice: np.ndarray | None = None) -> int | None:
        """voice：這段聲音已經算好的聲紋（guess 給的），有給就不用再算一次。"""
        return self.speakers.who(self.model.embed(audio) if voice is None else voice, len(audio) / SR)

    def guess(self, audio: np.ndarray) -> tuple[np.ndarray | None, int | None]:
        """辨識之前先看一下大概是誰：回傳（聲紋, 編號或 None）。不會記下任何東西；聲紋交回給 who 就不用算兩次。"""
        voice = self.model.embed(audio)
        return voice, self.speakers.guess(voice, len(audio) / SR)
