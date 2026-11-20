"""不需要模型、不需要 Windows 就能跑的檢查。"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from livelingo import assets, gpu, updater
from livelingo.config import Config, Glossary, Lessons
from livelingo.core.romanize import romanize
from livelingo.core.translate import (Translator, _leftover, apply_glossary, build_prompt, detect_language, salvage,
                                      sentence_kind, similarity, tidy, with_end_mark)
from livelingo.core.watcher import wanted
from livelingo.terms import TermPack


def test_version_compare():
    assert updater.is_newer("v2.0.1", "2.0.0")
    assert updater.is_newer("v2.1", "2.0.9")
    assert updater.is_newer("v10.0.0", "9.9.9")
    assert not updater.is_newer("v2.0.0", "2.0.0")
    assert not updater.is_newer("v1.9.9", "2.0.0")
    assert not updater.is_newer("", "2.0.0")


def test_gpu_modes(monkeypatch):
    monkeypatch.setenv("LIVELINGO_FAKE_GPU", "NVIDIA GeForce RTX 4070, 12282")
    g = gpu.detect()
    assert g["tier"] == "high" and g["name"] == "NVIDIA GeForce RTX 4070"
    assert gpu.choose_mode("auto", g) == {"model": "12b", "engine": "cuda", "quality": "high"}
    assert gpu.choose_mode("standard", g) == {"model": "4b", "engine": "cuda", "quality": "standard"}
    monkeypatch.setenv("LIVELINGO_FAKE_GPU", "NVIDIA GeForce RTX 4070 Laptop GPU, 8188")
    g = gpu.detect()
    assert g["tier"] == "fast"
    assert gpu.choose_mode("high", g)["model"] == "4b"
    monkeypatch.setenv("LIVELINGO_FAKE_GPU", "")
    g = gpu.detect()
    assert g["tier"] == "cpu" and gpu.choose_mode("auto", g)["engine"] == "cpu"
    monkeypatch.setenv("LIVELINGO_FAKE_GPU", "NVIDIA GeForce GTX 1650, 4096\nNVIDIA GeForce RTX 5090, 32607")
    assert gpu.detect()["name"] == "NVIDIA GeForce RTX 5090"


def test_engine_asset_picking():
    def asset(name):
        return {"name": name, "browser_download_url": "https://example.invalid/" + name}

    releases = [
        {"assets": [asset("llama-b9000-bin-ubuntu-x64.zip")]},
        {"assets": [asset("llama-b8999-bin-win-cpu-x64.zip"), asset("llama-b8999-bin-win-cuda-12.4-x64.zip"),
                    asset("cudart-llama-bin-win-cuda-12.4-x64.zip"), asset("llama-b8999-bin-win-cpu-arm64.zip")]},
    ]
    assert [n for n, _ in assets.pick_engine_assets(releases, "cpu")] == ["llama-b8999-bin-win-cpu-x64.zip"]
    assert [n for n, _ in assets.pick_engine_assets(releases, "cuda")] == [
        "llama-b8999-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"]
    assert assets.pick_engine_assets([{"assets": []}], "cuda") is None


def test_terms_and_prompt():
    pack = TermPack()
    assert ("정글", "打野") in pack.find("아 진짜 정글 차이 개심하네", "ko", "zh-TW")
    assert ("탑", "上路") in pack.find("ㅇㅋ 탑 갈게 나 서폿 못함", "ko", "zh-TW")
    assert pack.find("사용하는 내용이 피곤", "ko", "zh-TW") == []          # 「용」「피」藏在別的詞裡不算
    assert ("打野", "정글") in pack.find("笑死 對面打野一直抓我", "zh", "ko")
    assert pack.find("hello top", "en", "zh-TW") == []
    prompt = build_prompt("탑 갈게", "ko", "zh-TW", {}, "casual", [("탑", "上路")])
    assert "탑 = 上路" in prompt and prompt.endswith("<start_of_turn>model\n")
    assert "League of Legends" not in build_prompt("밥 먹자", "ko", "zh-TW", {}, "casual", [])


def test_glossary_direction():
    g = {"쵸비": "Chovy", "Chovy": "쵸비", "롤드컵": "世界大賽"}
    assert apply_glossary("나 쵸비 좋아해", g, "zh-TW")[0] == "나 Chovy 좋아해"
    assert apply_glossary("我喜歡 Chovy", g, "ko")[0] == "我喜歡 쵸비"
    assert apply_glossary("롤드컵 볼래?", g, "en")[0] == "롤드컵 볼래?"       # 中文的譯法不會用在翻成英文


def test_leftover_and_detection():
    assert _leftover("大絕 없어，閃現也 없어。", "ko", "zh-TW") == 4
    assert _leftover("沒有大絕，也沒有閃現。", "ko", "zh-TW") == 0
    assert _leftover("나 打野 할게", "zh", "ko") == 2
    assert detect_language("안녕 jimbo") == "ko"
    assert detect_language("今日は疲れた") == "ja"
    assert detect_language("我先去吃飯") == "zh"
    assert detect_language("gg wp") == "en"
    assert detect_language("123 !!") is None


def test_statement_stays_a_statement():
    assert sentence_kind("我先去洗澡", "zh") == "statement"
    assert sentence_kind("我們打龍吧", "zh") == "statement"
    assert sentence_kind("你吃飯了嗎", "zh") == "question"
    assert sentence_kind("你在哪？", "zh") == "question"
    assert sentence_kind("你吃饭了吗", "zh") == "question" and sentence_kind("你在干什么", "zh") is None      # 簡體字
    assert sentence_kind("你在幹什麼", "zh") is None                         # 有疑問詞但沒有問號：不猜
    assert sentence_kind("밥 먹었어", "ko") == "statement" and sentence_kind("밥 먹었어?", "ko") == "question"
    assert sentence_kind("뭐해", "ko") is None and sentence_kind("같이 할까", "ko") is None      # 疑問詞、疑問語尾：不猜
    assert sentence_kind("I am home", "en") is None and sentence_kind("I am home.", "en") == "statement"
    assert with_end_mark("밥 먹었어", "ko", "statement") == "밥 먹었어."
    assert with_end_mark("트롤이네 ㅋㅋ", "ko", "statement") == "트롤이네 ㅋㅋ"
    assert with_end_mark("我先去洗澡", "zh", "statement") == "我先去洗澡。"
    assert with_end_mark("你吃飯了嗎", "zh", "question") == "你吃飯了嗎？"
    assert with_end_mark("我先去洗澡！", "zh", "statement") == "我先去洗澡！"    # 已經有標點就不動
    assert with_end_mark("뭐해", "ko", None) == "뭐해"
    full = build_prompt("我先去洗澡。", "zh", "ko", {}, "casual", [])
    assert "A statement stays a statement" in full
    assert "Translate faithfully" not in build_prompt("我先去洗澡。", "zh", "ko", {}, "casual", [], style="plain")
    assert "방금" in build_prompt("剛剛那波我的錯。", "zh", "ko", {}, "casual", [])      # 常被翻錯的時間詞給提示
    assert "방금" not in build_prompt("我先去洗澡。", "zh", "ko", {}, "casual", [])
    taught = build_prompt("我們打巴龍吧。", "zh", "ko", {}, "casual", [], examples=[("我們打龍吧", "우리 용 잡자.")])
    assert "我們打龍吧 => 우리 용 잡자." in taught


def test_cleaning_the_model_output():
    assert salvage("我觉得 그들이 바텀에 갱을 올 것 같아.", "zh", "ko") == "그들이 바텀에 갱을 올 것 같아."
    assert salvage("我觉得他们会来抓下路\n그들이 바텀에 올 것 같아.", "zh", "ko") == "그들이 바텀에 올 것 같아."
    assert not salvage("我觉得他们会来抓下路", "zh", "ko")
    assert salvage("我們的打野（정글러）太爛了 哈哈", "ko", "zh-TW") == "我們的打野太爛了 哈哈"      # 括號裡的原文註解拿掉
    assert not salvage("我們的 정글러 太爛了", "ko", "zh-TW")
    assert tidy("我們打野在送頭ㅋㅋㅋ", "zh-TW") == "我們打野在送頭 哈哈"
    assert tidy("抱歉ㅠㅠ", "zh-TW") == "抱歉 嗚嗚"
    assert tidy("ㅋㅋㅋ 좋아", "ko") == "ㅋㅋㅋ 좋아"                            # 翻成韓文時不動


class _FakeModel(Translator):
    """不連模型：照著準備好的答案回話，並記下每次是怎麼問的。"""

    def __init__(self, answers, **kw):
        super().__init__("http://127.0.0.1:9", **kw)
        self.answers, self.asked = answers, []

    def _ask(self, text, source, target, hints, examples=None, style="full"):
        self.asked.append((text, style, list(examples or [])))
        return self.answers[style] if isinstance(self.answers, dict) else self.answers


def test_question_mark_gets_a_second_try():
    tr = _FakeModel({"full": "먼저 씻으러 갈까?", "short": "먼저 씻으러 갈게."})
    assert tr.translate("我先去洗澡", "ko", "zh") == "먼저 씻으러 갈게."
    assert [a[1] for a in tr.asked] == ["full", "short"] and tr.asked[0][0] == "我先去洗澡。"
    tr = _FakeModel({"full": "밥 먹었어?", "short": "밥 먹었어?"})          # 兩次都是問句：留著第一次的，不亂改
    assert tr.translate("你吃飯了", "ko", "zh") == "밥 먹었어?"
    tr = _FakeModel("밥 먹었어?")
    assert tr.translate("你吃飯了嗎", "ko", "zh") == "밥 먹었어?" and len(tr.asked) == 1      # 本來就是問句：不重翻
    tr = _FakeModel({"full": "你吃過飯了嗎？", "short": "我吃過飯了。"})      # 朋友的韓文直述句也一樣
    assert tr.translate("밥 먹었어", "zh-TW", "ko") == "我吃過飯了。" and tr.asked[0][0] == "밥 먹었어."


def test_built_in_examples():
    tr = _FakeModel("우리 용 잡자.")
    tr.translate("我們打龍吧", "ko", "zh")
    assert tr.asked[-1][2] == [("我們走吧", "우리 가자.")]                       # 「…吧」的提議：示範用「…자」，不要變成問句
    tr.translate("他剛剛回家了", "ko", "zh")
    assert tr.asked[-1][2] == [("我剛剛吃飽了", "나 방금 밥 다 먹었어.")]
    tr.translate("我今天很累", "ko", "zh")
    assert tr.asked[-1][2] == []
    polite = _FakeModel("우리 용 잡아요.", tone="polite")
    polite.translate("我們打龍吧", "ko", "zh")
    assert polite.asked[-1][2] == [("我們走吧", "우리 가요.")]                    # 禮貌語氣用禮貌的示範
    back = _FakeModel("我們走吧")
    back.translate("우리 가자", "zh-TW", "ko")
    assert back.asked[-1][2] == []


def test_lessons(tmp_path):
    lessons = Lessons(tmp_path)
    assert lessons.list() == []
    assert lessons.add("我們打龍吧", "우리 용 잡자.", "zh", "ko")
    assert lessons.add("나 먼저 잘게", "我先去睡了。", "ko", "zh-TW")
    assert not lessons.add("", "x", "zh", "ko") and not lessons.add("一樣", "一樣", "zh", "ko")
    assert lessons.add("我們打龍吧", "용 잡자.", "zh", "ko")                    # 同一句再教一次：換成新的
    again = Lessons(tmp_path)
    assert [(i["src"], i["dst"]) for i in again.list()] == [("我們打龍吧", "용 잡자."), ("나 먼저 잘게", "我先去睡了。")]

    tr = _FakeModel("우리 바론 잡자.", lessons=again.list)
    assert tr.translate("我們打龍吧", "ko", "zh") == "용 잡자." and tr.asked == []          # 一模一樣：直接用，不問模型
    assert tr.translate("我們打龍吧！", "ko", "zh") == "용 잡자."
    assert tr.translate("我們打巴龍吧", "ko", "zh") == "우리 바론 잡자."
    assert tr.asked[-1][2] == [("我們打龍吧", "용 잡자."), ("我們走吧", "우리 가자.")]   # 很像的句子當範例，教過的排前面
    tr.translate("今天天氣很好", "ko", "zh")
    assert tr.asked[-1][2] == []                                             # 不像的句子：不給範例，免得帶歪
    assert tr.taught("나 먼저 잘게", "ko", "ko") == (None, [])                  # 方向不同的不算
    assert similarity("我們打龍吧", "我們打巴龍吧") >= 0.5 > similarity("我們打龍吧", "今天天氣很好")
    again.remove("我們打龍吧", "ko")
    assert tr.taught("我們打龍吧", "zh", "ko") == (None, [("我們走吧", "우리 가자.")])
    (tmp_path / "lessons.json").write_text("{ broken", encoding="utf-8")
    assert Lessons(tmp_path).list() == []


def test_screen_line_filter():
    assert wanted("이따 밤에 봐 jimbo", {"ko"}) == "ko"
    assert wanted("今日は疲れたね", {"ja"}) == "ja"
    assert wanted("今天好累", {"zh"}) == "zh"
    assert wanted("今天好累", {"ko"}) is None
    assert wanted("that was really close lol", {"ko", "en"}) == "en"
    assert wanted("AX jimbo", {"ko", "en"}) is None


def test_romanize():
    assert romanize("같이") == "gachi"
    assert romanize("한국말") == "hangungmal"
    assert romanize("괜찮아") == "gwaenchana"


def test_config_and_glossary(tmp_path):
    cfg = Config(tmp_path)
    assert cfg["their_lang"] == "ko" and cfg["ui_lang"] in ("zh-TW", "en", "ko", "ja")
    cfg.set(their_lang="ja", sources={"screen": True, "listen": False, "mic": False})
    again = Config(tmp_path)
    assert again["their_lang"] == "ja" and again["sources"]["screen"] is True
    (tmp_path / "config.json").write_text("{ this is not json", encoding="utf-8")
    assert Config(tmp_path)["their_lang"] == "ko"                          # 設定檔壞了就用預設值
    g = Glossary(tmp_path)
    assert g.as_dict()["쵸비"] == "Chovy"                                   # 第一次有內建的例子
    assert g.add("아영", "阿英") and not g.add("", "x")
    g.remove("쵸비")
    g2 = Glossary(tmp_path)
    assert "아영" in g2.as_dict() and "쵸비" not in g2.as_dict()


def test_ui_languages_are_complete():
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "livelingo" / "ui" / "i18n.js").read_text(encoding="utf-8")
    blocks = re.split(r'\n  "(zh-TW|en|ko|ja)": \{', text)[1:]
    keys = {blocks[i]: set(re.findall(r"(?:^|[\s,{])([a-z][a-z0-9_]*): \"", blocks[i + 1])) for i in range(0, len(blocks), 2)}
    assert set(keys) == {"zh-TW", "en", "ko", "ja"}
    for lang, found in keys.items():
        assert found == keys["zh-TW"], (lang, found ^ keys["zh-TW"])
    app = (Path(__file__).resolve().parent.parent / "livelingo" / "ui" / "app.js").read_text(encoding="utf-8")
    used = {k for k in re.findall(r"\bt\(\"([a-z0-9_]+)\"", app) if not k.endswith("_")}   # 結尾是 _ 的是拼出來的
    assert used <= keys["zh-TW"], used - keys["zh-TW"]


class _Release(BaseHTTPRequestHandler):
    payload = b""
    installer = b"MZ fake installer " * 1000

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        body = self.installer if self.path.endswith(".exe") else self.payload
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_update_check_and_download(tmp_path, monkeypatch):
    import hashlib

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Release)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    digest = hashlib.sha256(_Release.installer).hexdigest()

    def publish(tag, sha=digest, size=len(_Release.installer)):
        _Release.payload = json.dumps({"tag_name": tag, "assets": [
            {"name": "LiveLingo-Setup.exe", "browser_download_url": base + "/LiveLingo-Setup.exe",
             "size": size, "digest": "sha256:" + sha}]}).encode()

    monkeypatch.setenv("LIVELINGO_UPDATE_API", base + "/latest")
    try:
        publish("v0.0.1")
        assert updater.check() is None                                     # 比現在舊：不更新
        publish("v99.0.0")
        info = updater.check()
        assert info["version"] == "99.0.0" and info["sha256"] == digest
        path = updater.download(info, tmp_path / "update")
        assert path.read_bytes() == _Release.installer
        path.unlink()
        publish("v99.0.0", sha="0" * 64)
        bad = updater.check()
        try:
            updater.download(bad, tmp_path / "update")
            raise AssertionError("a tampered installer must be rejected")
        except assets.DownloadError:
            pass
        assert not list((tmp_path / "update").glob("*.exe"))
        attempts = updater.Attempts(tmp_path)
        assert not attempts.recently_tried("99.0.0")
        attempts.record("99.0.0")
        assert attempts.recently_tried("99.0.0") and not attempts.recently_tried("99.0.1")
    finally:
        httpd.shutdown()
        os.environ.pop("LIVELINGO_UPDATE_API", None)


def test_floating_chat_painting():
    from livelingo import chatpaint

    short = chatpaint.paint_message("heard", "對方", "好", 400, 24)
    long = chatpaint.paint_message("heard", "對方", "打野來一下下路，對面沒有閃現，我大絕好了等我先開" * 6, 400, 24)
    mixed = chatpaint.paint_message("me", "我", "좋아, 지금 갈게 let's go!", 400, 24, original="好，我現在過去", backdrop=40)
    assert short.size[0] == long.size[0] == mixed.size[0] == 400
    assert long.size[1] > short.size[1] * 2          # 太長會換行
    assert mixed.size[1] > short.size[1]             # 多一行小字的原文
    assert short.getchannel("A").getextrema()[1] == 255 and short.getpixel((399, 0))[3] == 0   # 有字，其餘透明

    # 最新的在最下面；放不下的舊留言不會畫出來
    frame = chatpaint.compose([(short, 1.0)] * 30, (400, 200))
    assert frame.size == (400, 200) and frame.getchannel("A").getextrema()[1] == 255
    faded = chatpaint.compose([(short, 0.3)], (400, 200))
    assert 0 < faded.getchannel("A").getextrema()[1] < 120
    raw = chatpaint.to_bgra_premultiplied(frame)
    assert len(raw) == 400 * 200 * 4
    edit = chatpaint.edit_frame(frame, "zh-TW", 24)
    assert edit.getpixel((200, 100))[3] > 0          # 調整位置時整塊看得到


def test_floating_chat_lifecycle(tmp_path):
    from livelingo.overlay import MemoryWindow, Overlay
    from livelingo.service import Service

    service = Service(tmp_path)
    overlay = Overlay(service, window_class=MemoryWindow)
    service.overlay = overlay
    service.update_config({"overlay": False})
    assert not overlay.running
    service.update_config({"overlay": True, "overlay_seconds": 10, "overlay_font": 999})
    assert overlay.running and service.cfg["overlay_font"] == 60
    overlay._stop.set()                              # 停掉背景的重畫，下面自己一格一格畫才不會互相干擾
    overlay._wake.set()
    overlay._thread.join(2)
    try:
        overlay.on_message({"id": 1, "who": "them", "via": "heard", "original": "안녕", "translated": "你好"})
        overlay.on_message({"id": 2, "who": "me", "via": "typed", "original": "好", "translated": "좋아", "pending": True})
        assert [i["key"] for i in overlay.items] == [1]      # 還沒翻好的不顯示
        start = overlay.items[0]["at"]
        image, _ = overlay.frame(start + 1)
        assert image is not None and image.getchannel("A").getextrema()[1] == 255
        assert overlay.frame(start + 1.5)[0] is None         # 沒有變化就不重畫
        fading, wait = overlay.frame(start + 9.6)
        assert fading is not None and wait < 0.1 and 0 < fading.getchannel("A").getextrema()[1] < 255
        gone, _ = overlay.frame(start + 11)
        assert gone.getchannel("A").getextrema()[1] == 0 and not overlay.items

        overlay.set_edit(True)                               # 調整位置：看得到範圍和範例
        assert overlay.window.editing and overlay.frame()[0].getpixel((5, 5))[3] > 0
        overlay._moved([50, 60, 500, 300], True)
        overlay.set_edit(False)
        assert service.cfg["overlay_rect"] == [50, 60, 500, 300]

        service.update_config({"overlay_mine": False})
        overlay.on_message({"id": 3, "who": "me", "via": "typed", "original": "好", "translated": "좋아"})
        assert not overlay.items
    finally:
        service.update_config({"overlay": False})
    assert not overlay.running
    service.shutdown()


def test_microphone_second_way(monkeypatch):
    """第一種收音方式打不開麥克風時，自動改用第二種；兩種都不行才回報錯誤，而且錯誤一定有內容。"""
    import sys
    import time
    import types

    import numpy as np

    from livelingo.core import audio

    def broken(self):
        assert 1 == 2          # 沒有訊息的錯誤，就像收音元件遇到不認得的麥克風格式

    monkeypatch.setattr(audio.Capture, "_run_soundcard", broken)

    class Stream:
        def __init__(self, callback=None, device=None, **kw):
            self.callback, self.device = callback, device

        def __enter__(self):
            self.callback(np.full((audio.BLOCK, 1), 0.25, dtype=np.float32), audio.BLOCK, None, None)
            return self

        def __exit__(self, *a):
            return False

    devices = [{"name": "Speakers (Realtek(R) Audio)", "max_input_channels": 0},
               {"name": "Microphone (HD Webcam C270)", "max_input_channels": 1},
               {"name": "麥克風 (HyperX Cloud II)", "max_input_channels": 1}]
    apis = [{"name": "Windows WASAPI", "devices": [0, 1, 2], "default_input_device": 1},
            {"name": "MME", "devices": [0, 1, 2], "default_input_device": 1}]
    fake = types.SimpleNamespace(InputStream=Stream, query_devices=lambda: devices, query_hostapis=lambda: apis)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    assert audio.portaudio_input(fake, "麥克風 (HyperX Cloud II) 加上被截掉的名稱") == (2, "麥克風 (HyperX Cloud II)")
    assert audio.portaudio_input(fake, None) == (1, "Microphone (HD Webcam C270)")

    opened, errors, logged = [], [], []
    monkeypatch.setattr(audio, "log", logged.append)
    cap = audio.Capture("mic", on_error=lambda kind, ex: errors.append(str(ex)), on_open=lambda kind, name: opened.append(name))
    cap.start()
    block = cap.q.get(timeout=3)
    cap.stop()
    cap.join(3)
    assert cap.backend == "portaudio" and opened == ["Microphone (HD Webcam C270)"] and not errors
    assert float(block[0]) == 0.25 and "AssertionError" in logged[0] and "assert 1 == 2" in logged[0]

    def also_broken(self):
        raise OSError("device is busy")

    monkeypatch.setattr(audio.Capture, "_run_portaudio", also_broken)
    cap = audio.Capture("mic", on_error=lambda kind, ex: errors.append(str(ex)))
    cap.start()
    cap.join(3)
    assert len(errors) == 1 and "AssertionError" in errors[0] and "device is busy" in errors[0]
    errors.clear()
    cap = audio.Capture("loopback", on_error=lambda kind, ex: errors.append(str(ex)))      # 電腦的聲音沒有第二種方式
    cap.start()
    cap.join(3)
    assert len(errors) == 1 and errors[0].startswith("AssertionError")
    assert audio.describe(ValueError("bad")) == "ValueError: bad"
    time.sleep(0)


def test_quiet_microphone_and_levels():
    import numpy as np

    from livelingo.core.audio import BLOCK, SR, ArrayCapture
    from livelingo.core.speech import QUIET, SpeechPipeline, level_of

    assert level_of(np.zeros(1600, dtype=np.float32)) == 0.0
    assert level_of(np.full(1600, 0.5, dtype=np.float32)) == 1.0
    assert 0.3 < level_of(np.full(1600, 0.01, dtype=np.float32)) < 0.5
    assert QUIET < 0.003

    seen = []
    cap = ArrayCapture("mic", np.concatenate([np.full(BLOCK * 3, 0.2, dtype=np.float32), np.zeros(SR, dtype=np.float32)]))
    pipe = SpeechPipeline.__new__(SpeechPipeline)      # 不載入語音模型，只看音量回報
    import threading
    threading.Thread.__init__(pipe, daemon=True)
    pipe.capture, pipe.recognizer, pipe.langs = cap, None, ["zh"]
    pipe.on_text = pipe.on_state = lambda *a: None
    pipe.on_level = lambda kind, level, silent_for: seen.append((kind, level, silent_for))
    pipe._halt = threading.Event()
    pipe.segmenter = type("S", (), {"speaking": False, "feed": lambda self, audio: []})()
    pipe.start()
    import time
    deadline = time.time() + 5
    while time.time() < deadline and not (len(seen) >= 2 and seen[-1][1] == 0.0 and seen[-1][2] > 0.2):
        time.sleep(0.05)
    pipe.stop()
    assert seen[0][0] == "mic" and max(level for _, level, _ in seen) > 0.8      # 有聲音的那一段
    assert seen[-1][1] == 0.0 and seen[-1][2] > 0.2                             # 之後一直沒聲音，秒數會累加


def test_find_my_microphone(tmp_path):
    """「自動找麥克風」：說話時最大聲的那一支會被選起來；全部沒聲音就回報找不到。"""
    import numpy as np

    from livelingo.core.audio import SR, ArrayCapture
    from livelingo.service import Service

    class Audio:
        loud = "mic-headset"

        def devices(self):
            return {"speakers": [], "microphones": [{"id": "mic-webcam", "name": "Webcam", "default": True},
                                                     {"id": "mic-headset", "name": "Headset", "default": False}]}

        def capture(self, kind, device, on_error=None, on_open=None):
            level = 0.2 if device == self.loud else 0.0
            return ArrayCapture(kind, np.full(SR * 3, level, dtype=np.float32), realtime=False)

    service = Service(tmp_path, audio=Audio())
    events = []
    service.hub.publish = lambda name, data: events.append((name, data))
    service.set_source = lambda key, on: events.append(("set_source", (key, on)))
    service._find_mic(seconds=0.4)
    found = [d for name, d in events if name == "find_mic"]
    assert found[0]["state"] == "listening"
    assert found[-1] == {"state": "found", "name": "Headset", "changed": True, "previous": {"id": None, "name": ""}}
    assert service.cfg["mic_device"] == "mic-headset" and ("set_source", ("mic", True)) in events
    events.clear()
    Audio.loud = "nothing"
    service._find_mic(seconds=0.4)
    last = [d for name, d in events if name == "find_mic"][-1]
    assert last["state"] == "none" and last["count"] == 2 and last["blocked"] is False
    events.clear()
    Audio.loud = "mic-webcam"
    service._find_cancel.set()                                   # 按了取消：不換麥克風
    service._find_mic(seconds=0.4)
    assert [d for name, d in events if name == "find_mic"][-1] == {"state": "cancelled"}
    assert service.cfg["mic_device"] == "mic-headset"
    service.shutdown()


def test_same_language_and_simplified_chinese(tmp_path):
    from livelingo.core.translate import LANGS, to_script
    from livelingo.service import Service

    assert LANGS["zh-CN"]["family"] == "zh"
    assert to_script("这个软件很好用", "zh-TW") == "這個軟體很好用"
    assert to_script("這個軟體很好用", "zh-CN") == "这个软件很好用"
    assert to_script("안녕", "ko") == "안녕"

    tr = _FakeModel("我去上路。")
    assert tr.translate("这个软件很好用", "zh-TW", "zh") == "這個軟體很好用" and tr.asked == []      # 簡體變繁體不用問模型
    assert tr.translate("我們打龍吧", "zh-CN", "zh") == "我们打龙吧"
    assert tr.translate("탑 갈게", "zh-CN", "ko") == "我去上路。"
    assert "Simplified Chinese (zh-CN)" in build_prompt("탑 갈게", "ko", "zh-CN", {}, "casual", [])
    assert tidy("抱歉ㅠㅠ", "zh-CN") == "抱歉 呜呜"

    pack = TermPack()
    assert ("점멸", "闪现") in pack.find("점멸 없어", "ko", "zh-CN")                 # 建議的譯法跟著變簡體
    assert ("闪现", "점멸") in pack.find("对面没有闪现", "zh", "ko")                 # 簡體的原文也認得遊戲用語
    assert ("閃現", "점멸") in pack.find("對面沒有閃現", "zh", "ko")

    service = Service(tmp_path)
    service.update_config({"my_lang": "zh-TW", "their_lang": "zh-TW"})                 # 兩邊一樣：不再自動對調
    assert (service.cfg["my_lang"], service.cfg["their_lang"]) == ("zh-TW", "zh-TW")
    assert service._voice_langs("mic") == ["zh"]
    service.update_config({"their_lang": "zh-CN"})
    assert (service.cfg["my_lang"], service.cfg["their_lang"]) == ("zh-TW", "zh-CN")
    assert service._target_for({"who": "me", "lang": "zh"}) == "zh-CN"                  # 我說的給對方看：變簡體
    assert service._target_for({"who": "them", "lang": "zh"}) == "zh-TW"                # 對方說的給我看：變繁體
    service.update_config({"their_lang": "ko"})
    assert service._target_for({"who": "me", "lang": "zh"}) == "ko" and service._target_for({"who": "me", "lang": "ko"}) == "zh-TW"
    service.update_config({"their_lang": "klingon"})
    assert service.cfg["their_lang"] == "ko"
    service.shutdown()


def test_update_asks_first(tmp_path, monkeypatch):
    """有新版本時先問：選「這次先不要」不下載、照常使用；選「現在更新」才裝；下載到一半可以取消。"""
    from livelingo import updater as up
    from livelingo.service import Service

    info = {"version": "9.9.9", "url": "http://example.invalid/x.exe", "size": 0, "sha256": ""}
    calls = []
    monkeypatch.setattr(up, "enabled", lambda: True)
    monkeypatch.setattr(up, "check", lambda timeout=8: info)
    monkeypatch.setattr(up, "launch_installer", lambda path: calls.append(("install", path.name)))

    def download(info, folder, progress=None, cancel=None):
        calls.append(("download", bool(cancel and cancel())))
        return folder / "LiveLingo-Setup-9.9.9.exe"

    monkeypatch.setattr(up, "download", download)

    def boot(choice):
        service = Service(tmp_path)
        phases = []
        service.set_phase = lambda phase, **detail: phases.append((phase, detail.get("step")))
        service.exit_now = lambda: calls.append(("exit",))
        service.update_choice(choice)
        result = service._update_if_needed()
        service.shutdown()
        return result, phases

    done, phases = boot("later")
    assert done is False and calls == [] and phases[-1] == ("updating", "ask")
    assert not (tmp_path / "update.json").exists()                     # 沒有記成「試過了」，下次開啟還會再問

    def cancelled_download(info, folder, progress=None, cancel=None):
        service_stop.set()
        raise up.DownloadError("cancelled")

    done, phases = boot("now")
    assert done is True and [c[0] for c in calls] == ["download", "install", "exit"]
    assert ("updating", "installing") in phases
    calls.clear()
    (tmp_path / "update.json").unlink()

    service = Service(tmp_path)                                         # 下載到一半按取消：不安裝、不算失敗
    service_stop = service._update_stop
    notices = []
    service.notice = lambda code, **kw: notices.append(code)
    service.set_phase = lambda phase, **detail: None
    monkeypatch.setattr(up, "download", cancelled_download)
    service.update_choice("now")
    assert service._update_if_needed() is False and calls == [] and notices == []
    service.shutdown()
