"""背景降噪（2.18.0）：辨識之前先把一直都在的聲音（遊戲音樂、風扇、鍵盤聲）壓下去，吵的場景比較聽得準。

做法是「頻譜減法」：沒人說話的時候記住每個頻率平常有多大聲（噪音的樣子），
說完一句要辨識之前，每個頻率減掉噪音那一份，剩下的才是人聲。只用 numpy，不另外下載模型。
還沒聽過沒人說話的聲音時，就拿這一句裡最安靜的那幾小段當作噪音。"""

import numpy as np

FRAME = 512          # 每一小段 512 點（16 kHz 下 32 毫秒）
HOP = FRAME // 2
STRENGTH = 1.5       # 噪音估計放大一點再減，比較乾淨
FLOOR = 0.08         # 每個頻率最多壓到原本的 8%，不壓成 0（全壓掉人聲會變得像機器人）
LEARN = 0.1          # 噪音的樣子每次更新多少（慢慢跟著換場景）


def _frames(audio: np.ndarray) -> np.ndarray:
    n = 1 + max(0, -(-(len(audio) - FRAME) // HOP))      # 蓋得到每一個點（最後一段不夠長補 0）
    pad = np.zeros(max(FRAME, (n - 1) * HOP + FRAME), dtype=np.float32)
    pad[:len(audio)] = audio
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    return pad[idx]


_WINDOW = np.sqrt(np.hanning(FRAME + 1)[:-1]).astype(np.float32)      # 分析、合成各乘一次，加起來剛好是 hann


def spectrum(audio: np.ndarray) -> np.ndarray:
    """每一小段的頻譜大小（幾段 × 257）。"""
    return np.abs(np.fft.rfft(_frames(audio) * _WINDOW, axis=1))


class Denoiser:
    def __init__(self):
        self.noise: np.ndarray | None = None      # 每個頻率的噪音大小（257 個數）

    def learn(self, audio: np.ndarray) -> None:
        """餵沒人說話時的聲音，記住噪音的樣子。完全沒聲音的不算（關掉的麥克風）。"""
        if len(audio) < FRAME or float(np.abs(audio).max()) < 1e-4:
            return
        now = np.median(spectrum(audio), axis=0)
        self.noise = now if self.noise is None else (1 - LEARN) * self.noise + LEARN * now

    def clean(self, audio: np.ndarray) -> np.ndarray:
        """回傳降噪後的聲音（長度一樣）。太短的照原樣。"""
        audio = np.asarray(audio, dtype=np.float32)
        if len(audio) < FRAME * 2:
            return audio
        frames = _frames(audio) * _WINDOW
        spec = np.fft.rfft(frames, axis=1)
        mag = np.abs(spec)
        noise = self.noise
        if noise is None:      # 還沒聽過安靜的時候：這句裡最安靜的 15% 小段當作噪音
            energy = mag.sum(axis=1)
            quiet = mag[energy <= np.percentile(energy, 15)]
            noise = np.median(quiet, axis=0) if len(quiet) else np.zeros(mag.shape[1])
        gain = np.maximum(1.0 - STRENGTH * noise[None, :] / np.maximum(mag, 1e-9), FLOOR)
        out_frames = np.fft.irfft(spec * gain, n=FRAME, axis=1) * _WINDOW
        out = np.zeros((len(out_frames) - 1) * HOP + FRAME, dtype=np.float32)
        for i, f in enumerate(out_frames):
            out[i * HOP:i * HOP + FRAME] += f
        return np.clip(out[:len(audio)], -1.0, 1.0).astype(np.float32)
