"""2.18.0 的十個點子：背景降噪、左右耳分流、語氣小圖示、語音指令、自動選語音設定、通話紀錄匯出、
聽到自我介紹就建議記住聲音、對方講太快的提醒、精華標記、這一場存成字幕檔。"""

import io
import json
import time
import zipfile

import numpy as np


def test_denoise_lowers_steady_noise_and_keeps_length():
    from livelingo.core.denoise import Denoiser

    rng = np.random.default_rng(0)
    sr = 16000
    noise = (0.05 * rng.standard_normal(sr * 2)).astype(np.float32)
    t = np.arange(sr) / sr
    voice = (0.3 * np.sin(2 * np.pi * 300 * t) * np.sin(2 * np.pi * 2 * t) ** 2).astype(np.float32)
    mixed = noise[:sr] + voice
    d = Denoiser()
    d.learn(noise[sr:])
    out = d.clean(mixed)
    assert out.shape == mixed.shape and out.dtype == np.float32
    residual_before = float(np.mean((mixed - voice) ** 2))
    residual_after = float(np.mean((out - voice) ** 2))
    assert residual_after < residual_before * 0.6                 # 噪音少了一大半
    assert float(np.mean(out ** 2)) > float(np.mean(voice ** 2)) * 0.4      # 人聲還在
    assert len(Denoiser().clean(mixed[:100])) == 100              # 太短照原樣
    assert Denoiser().clean(mixed).shape == mixed.shape           # 沒聽過安靜也能做（用最安靜的幾段）
    d2 = Denoiser()
    d2.learn(np.zeros(sr, np.float32))
    assert d2.noise is None                                       # 完全沒聲音不算噪音


def test_pipeline_cleans_only_when_on_and_passes_loudness():
    from livelingo.core.speech import SpeechPipeline

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe.denoise = None
    pipe._denoiser = None
    pipe._loud_avg = 0.0
    sound = np.ones(4000, np.float32) * 0.1
    assert pipe._clean(sound) is sound                            # 沒開不動
    pipe.denoise = lambda: True
    assert pipe._clean(sound).shape == sound.shape and pipe._denoiser is not None
    assert pipe._loudness(sound) == 1.0
    assert pipe._loudness(sound * 4) > 2.5                        # 突然大聲很多


def test_tone_guesses():
    from livelingo import tone

    cases = {"너 진짜 잘한다ㅋㅋㅋ": "laugh", "すごいwww": "laugh", "哈哈哈": "laugh", "lol nice": "laugh",
             "just kidding": "joke", "開玩笑的啦": "joke", "씨발 뭐야": "angry", "ㅠㅠ 졌다": "sad",
             "hello": "", "go go go!!!": "", "visit www.site.com": "", "aww": ""}
    for text, want in cases.items():
        assert tone.guess(text) == want, text
    assert tone.guess("stop it!", loud=2.5) == "angry" and tone.guess("stop it", loud=1.0) == ""
    assert set(tone.WORDS) >= {"zh-TW", "en", "ko", "ja"} and tone.word("laugh", "xx") == "laughing"


def test_voice_commands_only_whole_sentences():
    from livelingo import voicecmd

    assert voicecmd.match("暫停翻譯。", "zh-TW") == "pause"
    assert voicecmd.match("請重翻上一句", "zh-TW") == "again"
    assert voicecmd.match("Pause translation.", "ko") == "pause"              # 英文任何介面都可以
    assert voicecmd.match("번역 멈춰 줘", "ko") == "pause"
    assert voicecmd.match("翻訳を止めてください", "ja") == "pause"
    assert voicecmd.match("Hey LiveLingo, mark this!", "en") == "mark"
    assert voicecmd.match("我們先暫停翻譯這個話題", "zh-TW") == ""          # 句子裡提到不算
    assert voicecmd.match("highlight", "en") == "" and voicecmd.match("", "en") == ""
    from livelingo.config import UI_LANGS

    assert set(voicecmd.PHRASES) == set(UI_LANGS)
    for lang in UI_LANGS:
        assert set(voicecmd.examples(lang)) == set(voicecmd.COMMANDS), lang


def test_self_introductions():
    from livelingo.introduce import name_in

    yes = {("Hi, I'm Mina.", "en"): "Mina", ("저는 민아예요", "ko"): "민아", ("我是 Mina 啦", "zh"): "Mina", ("我叫小明", "zh-TW"): "小明",
           ("私はミナです", "ja"): "ミナ", ("me llamo Carlos", "es"): "Carlos", ("меня зовут Анна", "ru"): "Анна"}
    for (text, lang), name in yes.items():
        assert name_in(text, lang) == name, text
    for text, lang in (("I'm sorry", "en"), ("저는 학생이에요", "ko"), ("我是說你很強", "zh"), ("this is great", "en"), ("I am Going", "en")):
        assert name_in(text, lang) == "", text


def test_pace_watch_and_phrases():
    from livelingo import pace
    from livelingo.config import UI_LANGS

    now = [1000.0]
    w = pace.PaceWatch(clock=lambda: now[0])
    assert w.add("a", "en", True, 2) == "" and w.add("b", "en", True, 2) == ""
    assert w.add("c", "en", True, 2) == "unclear"
    for _ in range(4):
        assert w.add("d", "en", True, 2) == ""                    # 剛提醒過：冷卻中
    now[0] += pace.COOLDOWN + 1
    assert w.add("e", "en", True, 2) == "unclear"                 # 冷卻完：還是聽不清楚就再提醒一次
    w = pace.PaceWatch(clock=lambda: now[0])
    fast = "x" * 60
    assert [w.add(fast, "en", False, 2.0) for _ in range(3)][-1] == "fast"
    assert pace.ask_in("zh") == pace.ASK["zh"] and pace.ask_in("ko") and pace.ask_in("xx") == pace.ASK["en"]
    for lang in UI_LANGS:
        assert pace.ask_in(lang), lang


def _msgs(start):
    return [
        {"id": 1, "who": "them", "via": "heard", "original": "안녕하세요", "translated": "你好", "pending": False, "error": "",
         "at": start + 2.0, "hear": 0.5, "length": 1.0, "speaker": 1, "voice": "Mina"},
        {"id": 2, "who": "me", "via": "spoken", "original": "你好 | 嗨", "translated": "안녕 | 하이", "pending": False, "error": "", "at": start + 5.0},
        {"id": 3, "who": "them", "via": "screen", "original": "gg", "translated": "打得好", "pending": False, "error": "", "at": start + 9.0},
        {"id": 4, "who": "them", "via": "heard", "original": "x", "translated": "", "pending": True, "error": "", "at": start + 10.0},
    ]


def test_session_files():
    from livelingo import sessionlog

    start = time.time() - 60
    msgs = _msgs(start)
    voice = sessionlog.rows(msgs, voice_only=True)
    assert [m["id"] for m in voice] == [1, 2]
    assert sessionlog.started_at(msgs) == start + 0.5
    marks = [{"id": 1, "at": start + 30, "offset": 75.0, "original": "gg", "translated": "打得好"}]
    md = sessionlog.markdown(voice, marks, "zh-TW", start)
    assert "| 時間 | 誰 | 原文 | 翻譯 |" in md and "Mina" in md and "你好 \\| 嗨" in md and "01:15" in md and "精華" in md
    doc = sessionlog.docx(voice, marks, "en", start)
    with zipfile.ZipFile(io.BytesIO(doc)) as z:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(z.namelist())
        body = z.read("word/document.xml").decode("utf-8")
    assert "안녕하세요" in body and "Highlights" in body and "&lt;" not in "".join(m["original"] for m in voice)
    srt = sessionlog.srt(sessionlog.rows(msgs), start)
    cues = srt.strip().split("\n\n")
    assert len(cues) == 3 and cues[0].startswith("1\n00:00:00,500 --> ") and "你好" in cues[0] and "打得好" in cues[2]
    assert sessionlog.clock(3725) == "1:02:05" and sessionlog.clock(75) == "01:15"


def test_service_voice_commands_pause_and_marks(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    sent = []
    service.submit = lambda via, text, lang=None, **kw: (sent.append((via, text)), len(sent))[1]
    try:
        service.cfg.set(drop_echo=False, join_split=False)
        service._on_speech("mic", "暫停翻譯", "zh")
        assert sent == [("spoken", "暫停翻譯")]                   # 預設關：照平常翻
        service.update_config({"voice_commands": True})
        service._on_speech("mic", "暫停翻譯。", "zh")
        end = time.time() + 2
        while not service.voice_paused and time.time() < end:
            time.sleep(0.02)
        assert service.voice_paused and len(sent) == 1 and ("voice_paused", True) in events
        service._on_speech("loopback", "안녕하세요", "ko")
        service._on_screen_line("gg", "en")
        assert len(sent) == 1                                     # 暫停中：對方的話和畫面的字都不翻
        assert service.set_voice_paused(False)["paused"] is False
        service._on_speech("loopback", "안녕하세요", "ko", length=1.2)
        assert sent[-1] == ("heard", "안녕하세요") and service._msg_len[2] == 1.2

        service._post({"id": 7, "who": "them", "via": "screen", "original": "gg", "translated": "打得好", "pending": False, "error": "",
                       "lang": "en", "at": time.time() - 5})
        mark = service.mark_now()
        assert mark["translated"] == "打得好" and mark["offset"] >= 4 and events[-2][0] == "marks"
        assert service.marks_action("remove", mark["id"])["marks"] == []
        service.marks_action("add")
        assert len(service.marks) == 1 and service.marks_action("nope")["ok"] is False
        service.cfg.set(transcript_dir=str(tmp_path / "out"))
        saved = service.session_export("srt")
        assert saved["ok"] and saved["name"].endswith(".srt") and "打得好" in (tmp_path / "out" / saved["name"]).read_text(encoding="utf-8-sig")
        assert service.session_export("md")["error"] == "empty"     # 通話紀錄只算語音，畫面上那一句不算
        assert service.session_export("exe")["ok"] is False
        service.clear_messages()
        assert service.marks == [] and ("marks", []) in events
    finally:
        service.shutdown()


def test_service_tone_intro_and_slow_tip(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    try:
        mid = service.submit("heard", "ㅋㅋㅋ 진짜?", "ko")
        assert next(m for m in service.messages if m["id"] == mid).get("tone") == "laugh"
        pushed = []
        service.overlay.push = lambda kind, text, original, **kw: pushed.append(text)
        service.cfg.set(ui_lang="zh-TW")
        service.overlay.on_message({"id": 50, "who": "them", "via": "heard", "translated": "真的？", "original": "ㅋㅋ 진짜?", "tone": "laugh"})
        assert pushed == ["(笑) 真的？"]                         # 浮動字幕用短字
        service.update_config({"tone_icons": False})
        mid = service.submit("heard", "ㅋㅋㅋ", "ko")
        assert "tone" not in next(m for m in service.messages if m["id"] == mid)
        mid = service.submit("typed", "哈哈哈", "zh")
        assert "tone" not in next(m for m in service.messages if m["id"] == mid)       # 我自己說的不加

        service.cfg.set(tell_speakers=True)
        service._listen_extras("Hi, I'm Mina", "en", False, 2, 1.0)
        assert ("voice_name", {"speaker": 2, "name": "Mina", "remember": bool(service.cfg["remember_speakers"])}) in events
        n = len(events)
        service._listen_extras("I'm Mina again", "en", False, 2, 1.0)
        assert not any(e[0] == "voice_name" for e in events[n:])   # 同一個人只問一次
        for _ in range(3):
            service._listen_extras("뭐라고", "ko", True, None, 1.0)
        tip = next(d for name, d in events if name == "slow_tip")
        assert tip["why"] == "unclear" and tip["phrase"] == "조금만 천천히 말해 줄 수 있어요?"
    finally:
        service.shutdown()


def test_one_ear_playback_and_speed_pick(tmp_path):
    from livelingo.core import speak
    from livelingo.service import pick_speech

    mono = np.ones(10, np.float32)
    assert speak.to_ear(mono, "off") is mono
    left = speak.to_ear(mono, "left")
    assert left.shape == (10, 2) and left[:, 0].all() and not left[:, 1].any()
    assert speak.to_ear(mono, "right")[:, 1].all()

    class Voice:
        def synth(self, text, lang, path):
            import wave

            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x10" * 160)
            return {"ok": True, "voice": "x"}

    played, done = [], []
    s = speak.Speaker(tmp_path, voice=Voice(), player=lambda data, rate, device: played.append(data.shape), on_done=done.append, ear=lambda: "right")
    assert s.say("hi", "en")
    end = time.time() + 3
    while not done and time.time() < end:
        time.sleep(0.02)
    assert played == [(160, 2)]

    assert pick_speech(0.05) == ("accurate", True) and pick_speech(0.3) == ("fast", True) and pick_speech(0.9) == ("fast", False)


def test_wiring_for_2_18_0():
    import re
    from pathlib import Path

    from livelingo import hotkeys
    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS

    root = Path(__file__).resolve().parent.parent / "livelingo"
    i18n = (root / "ui" / "i18n.js").read_text(encoding="utf-8")
    for key in ("tf_laugh", "ear_title", "dn_title", "sp_result", "vc_title", "vn_ask", "sl_fast", "ss_srt", "mk_title", "vp_text", "hk_mark",
                "n_speech_picked_slow"):
        assert len(re.findall(rf"(?<![\w]){key}: \"", i18n)) == len(UI_LANGS), key
    for key in ("denoise", "ear_split", "tone_icons", "voice_commands", "speech_autopick", "intro_suggest", "slow_tip"):
        assert key in DEFAULTS and key in EDITABLE, key
    assert DEFAULTS["denoise"] is False and DEFAULTS["voice_commands"] is False and "speech_picked" not in EDITABLE
    assert "mark" in hotkeys.ACTIONS and DEFAULTS["hotkeys"]["mark"] == "Ctrl+Shift+F3"
    for lang in UI_LANGS:
        for key in ("vc_pause", "mark_done", "slow_tip", "intro_heard"):
            assert key in hotkeys.NOTES[lang], (lang, key)
    server = (root / "server.py").read_text(encoding="utf-8")
    for route, call in (("/api/marks", "marks_action("), ("/api/session", "session_export("), ("/api/voicepause", "set_voice_paused("),
                        ("/api/speedpick", "speech_repick(")):
        assert f'route == "{route}"' in server and f"service.{call}" in server, route
    app = (root / "ui" / "app.js").read_text(encoding="utf-8")
    for act in ("mark-add", "mark-remove", "session-save", "speed-pick", "voice-name-keep", "slow-copy", "voice-resume"):
        assert f'data-act="{act}"' in app and f'"{act}":' in app, act
    for event in ("marks", "slow_tip", "voice_name", "voice_paused"):
        assert f"{event}: (d) =>" in app, event
    assert json.loads((root / "data" / "changes.json").read_text(encoding="utf-8"))
