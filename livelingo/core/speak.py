"""
把我的話的譯文念出來（2.9.0）：用 Windows 內建的語音（不用另外下載模型，也不連網），
念到使用者選的播放裝置。選「CABLE Input」（VB-CABLE 虛擬音訊線）的話，Discord 把麥克風設成「CABLE Output」，
對方就會聽到外文。

- 合成：一個一直開著的 PowerShell（不用每句都等它開），先用 Windows 10/11 的新語音（Windows.Media.SpeechSynthesis，
  語言比較多），不行再用舊的 SAPI 語音（System.Speech）。要念的字用 base64 傳過去，不會有編碼問題。
- 播放：拿回 WAV 檔，用 soundcard 播到選的裝置（跟「對方的聲音從哪個裝置出來」用同一份清單）。
- 那個語言沒有裝語音：回報 no_voice，介面請使用者到 Windows 設定 → 時間與語言 → 語音 加一個（Windows 自己下載）。
"""

import base64
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

IS_WIN = sys.platform == "win32"
MAX_CHARS = 400          # 一次最多念幾個字（太長的念到一半對方早就換話題了）
QUEUE = 4                # 排隊最多幾句，再多就把最舊的丟掉

# 每種介面語言的代碼 → Windows 語音的語言（Language 屬性的開頭）
VOICE_LANG = {"zh-TW": ("zh-TW", "zh-HK", "zh-CN"), "zh-CN": ("zh-CN", "zh-TW", "zh-HK"), "en": ("en-US", "en-GB", "en"),
              "ko": ("ko",), "ja": ("ja",), "vi": ("vi",), "th": ("th",), "id": ("id",), "es": ("es",), "pt": ("pt-BR", "pt"),
              "ru": ("ru",), "de": ("de",), "fr": ("fr",)}

# 「試聽」念的句子（用對方的語言）
SAMPLES = {"zh-TW": "你好，這是 LiveLingo 的語音。", "zh-CN": "你好，这是 LiveLingo 的语音。", "en": "Hi, this is the LiveLingo voice.",
           "ko": "안녕, 이건 LiveLingo 목소리야.", "ja": "こんにちは、LiveLingo の音声です。", "vi": "Xin chào, đây là giọng đọc của LiveLingo.",
           "th": "สวัสดี นี่คือเสียงของ LiveLingo", "id": "Halo, ini suara LiveLingo.", "es": "Hola, esta es la voz de LiveLingo.",
           "pt": "Oi, esta é a voz do LiveLingo.", "ru": "Привет, это голос LiveLingo.", "de": "Hallo, das ist die Stimme von LiveLingo.",
           "fr": "Salut, voici la voix de LiveLingo."}

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$modern = $false
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
  $null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $null = $t.Wait(-1); $t.Result }
  $modern = $true
} catch { }
try { Add-Type -AssemblyName System.Speech } catch { }

function Voices {
  $out = @()
  if ($modern) {
    foreach ($v in [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices) { $out += @{ name = $v.DisplayName; lang = $v.Language; kind = 'modern' } }
  }
  try {
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    foreach ($v in $s.GetInstalledVoices()) { if ($v.Enabled) { $out += @{ name = $v.VoiceInfo.Name; lang = $v.VoiceInfo.Culture.Name; kind = 'sapi' } } }
    $s.Dispose()
  } catch { }
  return ,$out
}

function Pick($langs, $kind) {
  foreach ($l in $langs) {
    if ($kind -eq 'modern' -and $modern) {
      foreach ($v in [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices) { if ($v.Language -like "$l*") { return $v } }
    }
    if ($kind -eq 'sapi') {
      $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
      foreach ($v in $s.GetInstalledVoices()) { if ($v.Enabled -and $v.VoiceInfo.Culture.Name -like "$l*") { $n = $v.VoiceInfo.Name; $s.Dispose(); return $n } }
      $s.Dispose()
    }
  }
  return $null
}

function Say($req) {
  $langs = @($req.langs)
  $v = Pick $langs 'modern'
  if ($v -ne $null) {
    $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    $synth.Voice = $v
    $stream = Await ($synth.SynthesizeTextToStreamAsync($req.text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
    $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
    $size = [uint32]$stream.Size
    $null = Await ($reader.LoadAsync($size)) ([uint32])
    $bytes = New-Object byte[] $size
    $reader.ReadBytes($bytes)
    [IO.File]::WriteAllBytes($req.path, $bytes)
    $synth.Dispose()
    return @{ ok = $true; voice = $v.DisplayName }
  }
  $n = Pick $langs 'sapi'
  if ($n -ne $null) {
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.SelectVoice($n)
    $s.SetOutputToWaveFile($req.path)
    $s.Speak($req.text)
    $s.SetOutputToNull()
    $s.Dispose()
    return @{ ok = $true; voice = $n }
  }
  return @{ ok = $false; error = 'no_voice' }
}

while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  try {
    $req = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line.Trim())) | ConvertFrom-Json
    if ($req.op -eq 'voices') { $res = @{ ok = $true; voices = (Voices) } } else { $res = Say $req }
  } catch { $res = @{ ok = $false; error = $_.Exception.Message } }
  [Console]::Out.WriteLine(($res | ConvertTo-Json -Compress -Depth 4))
  [Console]::Out.Flush()
}
"""


class SpeakError(Exception):
    pass


class WindowsVoice:
    """跟一直開著的 PowerShell 說話：一行請求（base64 的 JSON），一行回答（JSON）。"""

    def __init__(self, folder: Path, log=lambda text: None):
        self.folder = Path(folder)
        self.log = log
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _start(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        script = self.folder / "speak.ps1"
        script.write_text(SCRIPT, encoding="utf-8-sig")      # 有 BOM，舊版 PowerShell 才會當成 UTF-8 讀
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
        return self._proc

    def ask(self, request: dict, timeout: float = 30.0) -> dict:
        with self._lock:
            proc = self._start()
            line = base64.b64encode(json.dumps(request, ensure_ascii=False).encode("utf-8")) + b"\n"
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except OSError as ex:
                self.stop()
                raise SpeakError(f"voice helper stopped: {ex!r}") from ex
            answer: list[bytes] = []
            reader = threading.Thread(target=lambda: answer.append(proc.stdout.readline()), daemon=True)
            reader.start()
            reader.join(timeout)
            if not answer or not answer[0]:
                self.stop()
                raise SpeakError("voice helper did not answer")
            return json.loads(answer[0].decode("utf-8", errors="replace"))

    def voices(self) -> list[dict]:
        return list(self.ask({"op": "voices"}).get("voices") or [])

    def synth(self, text: str, lang: str, path: Path) -> dict:
        return self.ask({"op": "say", "text": text, "langs": list(VOICE_LANG.get(lang, (lang,))), "path": str(path)})

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(2)
            except Exception:  # noqa: BLE001
                proc.kill()


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """WAV 檔 → (float32 的聲音, 取樣率)。多聲道的混成單聲道。"""
    with wave.open(str(path), "rb") as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise SpeakError(f"unsupported wav sample width {width}")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def play(data: np.ndarray, rate: int, device=None) -> None:
    """播到指定的播放裝置（soundcard 的喇叭 id；None = Windows 預設）。"""
    from .audio import _soundcard

    sc = _soundcard()

    speaker = None
    if device:
        try:
            speaker = sc.get_speaker(device)
        except Exception:  # noqa: BLE001  選的裝置不在了（拔掉了）：用預設的
            speaker = None
    speaker = speaker or sc.default_speaker()
    speaker.play(data.reshape(-1, 1), samplerate=rate)


class Speaker:
    """
    排隊念：say(text, lang) 馬上回來，背景一句一句念。念的時候和念完 1 秒內 speaking() 是 True，
    主程式用它和 recent() 認出「麥克風／聽對方收到的是我自己念出來的聲音」，不要再翻一次。
    voice / synth / play 可以換成假的（測試用）。
    """

    def __init__(self, folder: Path, device=lambda: None, on_done=None, log=lambda text: None, voice=None, player=None):
        self.folder = Path(folder)
        self.device = device
        self.on_done = on_done or (lambda result: None)
        self.log = log
        self.voice = voice if voice is not None else (WindowsVoice(folder, log) if IS_WIN else None)
        self.player = player or play
        self._jobs: queue.Queue = queue.Queue()
        self._until = 0.0
        self._recent: list[tuple[float, str]] = []
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return self.voice is not None

    def say(self, text: str, lang: str) -> bool:
        text = " ".join(str(text or "").split())[:MAX_CHARS]
        if not text or self.voice is None:
            return False
        while self._jobs.qsize() >= QUEUE:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        self._jobs.put((text, lang))
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, daemon=True, name="speak")
                self._thread.start()
        return True

    def speaking(self) -> bool:
        return time.monotonic() < self._until

    def recent(self, seconds: float = 20.0) -> list[str]:
        now = time.monotonic()
        with self._lock:
            self._recent = [(at, t) for at, t in self._recent if now - at < seconds]
            return [t for _, t in self._recent]

    def _work(self) -> None:
        while True:
            try:
                text, lang = self._jobs.get(timeout=5)
            except queue.Empty:
                return
            result = {"ok": False, "text": text, "lang": lang, "voice": "", "error": ""}
            fd, name = tempfile.mkstemp(prefix="livelingo-say-", suffix=".wav", dir=str(self.folder))
            os.close(fd)
            path = Path(name)
            try:
                got = self.voice.synth(text, lang, path)
                if not got.get("ok"):
                    result["error"] = str(got.get("error") or "failed")
                else:
                    data, rate = read_wav(path)
                    with self._lock:
                        self._recent.append((time.monotonic(), text))
                    self._until = time.monotonic() + len(data) / max(1, rate) + 1.0
                    self.player(data, rate, self.device())
                    self._until = time.monotonic() + 1.0
                    result.update(ok=True, voice=str(got.get("voice") or ""))
            except Exception as ex:  # noqa: BLE001
                self.log(f"speaking failed: {ex!r}")
                result["error"] = str(ex) or type(ex).__name__
            finally:
                path.unlink(missing_ok=True)
            try:
                self.on_done(result)
            except Exception:  # noqa: BLE001
                pass

    def voices(self) -> list[dict]:
        if self.voice is None:
            return []
        try:
            return self.voice.voices()
        except Exception as ex:  # noqa: BLE001
            self.log(f"listing voices failed: {ex!r}")
            return []

    def stop(self) -> None:
        stop = getattr(self.voice, "stop", None)
        if stop:
            stop()
