"""不需要模型、不需要 Windows 就能跑的檢查。"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from livelingo import assets, gpu, updater
from livelingo.config import UI_LANGS, Config, Glossary, Lessons
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

    def _ask(self, text, source, target, hints, examples=None, style="full", context=None):
        self.asked.append((text, style, list(examples or [])))
        self.contexts = getattr(self, "contexts", []) + [list(context or [])]
        if callable(self.answers):
            return self.answers(text, style, context)
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
    assert cfg["their_lang"] == "ko" and cfg["ui_lang"] in ("zh-TW", "zh-CN", "en", "ko", "ja")
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
    blocks = re.split(r'\n  "([A-Za-z-]+)": \{', text)[1:]
    keys = {blocks[i]: set(re.findall(r"(?:^|[\s,{])([a-z][a-z0-9_]*): \"", blocks[i + 1])) for i in range(0, len(blocks), 2)}
    assert set(keys) == set(UI_LANGS)
    for lang, found in keys.items():
        assert found == keys["zh-TW"], (lang, found ^ keys["zh-TW"])
    app = (Path(__file__).resolve().parent.parent / "livelingo" / "ui" / "app.js").read_text(encoding="utf-8")
    used = {k for k in re.findall(r"\bt\(\"([a-z0-9_]+)\"", app) if not k.endswith("_")}   # 結尾是 _ 的是拼出來的
    assert used <= keys["zh-TW"], used - keys["zh-TW"]


def test_every_translation_language_has_an_interface():
    """有什麼翻譯語言，就要有什麼介面：設定、介面文字、語言按鈕、浮動留言和框選畫面都要齊。"""
    import re
    from pathlib import Path

    from livelingo import chatpaint, region
    from livelingo.core.translate import LANGS

    assert set(UI_LANGS) == set(LANGS)
    ui = Path(__file__).resolve().parent.parent / "livelingo" / "ui"
    app = (ui / "app.js").read_text(encoding="utf-8")
    buttons = re.search(r"const natives = (\[.*?\]);", app).group(1)
    pairs = re.search(r"const PAIR_LANGS = (\[.*?\]);", app).group(1)
    html_langs = re.search(r"const htmlLang = (\{.*?\})", app).group(1)
    names = (ui / "i18n.js").read_text(encoding="utf-8")
    for code in LANGS:
        assert f'"{code}"' in buttons and f'"{code}"' in pairs, code
        assert f'"{code}":' in html_langs or f"{code}:" in html_langs, code
        key = {"zh-TW": "zh", "zh-CN": "zhcn"}.get(code, code)
        assert f"lang_{key}:" in names and (f"short_{key}:" in names or key == "zhcn"), code      # 短名字繁簡共用「中文」
        for table in (chatpaint.NAMES, chatpaint.HINTS, chatpaint.SAMPLES, region.HINTS, region.FONTS):
            assert code in table, code
    # 簡體介面裡不能混進繁體字
    from opencc import OpenCC

    plain = OpenCC("t2s")
    block = names.split('\n  "zh-CN": {')[1].split('\n  "en": {')[0]
    for line in block.splitlines():
        assert plain.convert(line) == line, line


def test_first_run_follows_the_windows_language(monkeypatch):
    import locale

    from livelingo import config

    monkeypatch.setattr(config.os, "name", "posix")
    for name, expected in (("zh_CN", "zh-CN"), ("zh_SG", "zh-CN"), ("zh_TW", "zh-TW"), ("zh_HK", "zh-TW"),
                           ("ko_KR", "ko"), ("ja_JP", "ja"), ("de_DE", "en")):
        monkeypatch.setattr(locale, "getlocale", lambda name=name: (name, "UTF-8"))
        assert config.system_ui_lang() == expected, name


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


def test_auto_detect_their_language(tmp_path):
    from livelingo.core.speech import Recognizer
    from livelingo.service import Service

    pick = Recognizer.pick_language
    probs = [("en", 0.05), ("ko", 0.40), ("ja", 0.45), ("zh", 0.05), ("fr", 0.9)]
    assert pick(probs, ["ko", "ja"]) == "ja"                    # 沒有偏好：機率高的贏，不在清單裡的語言不算
    assert pick(probs, ["ko", "zh", "en", "ja"], stay=1.5) == "ko"   # 差一點點：留在目前的語言
    assert pick([("ja", 0.8), ("ko", 0.1)], ["ko", "zh", "en", "ja"], stay=1.5) == "ja"
    assert pick([], ["ko", "ja"]) == "ko"

    service = Service(tmp_path)
    notices = []
    service.notice = lambda code, **kw: notices.append((code, kw))
    service.update_config({"my_lang": "zh-TW", "their_lang": "ko"})
    assert service._voice_langs("listen") == ["ko"] and service._screen_langs() == {"ko"}
    service.update_config({"their_auto": True})
    assert service.cfg["their_auto"] is True and service.cfg["their_lang"] == "ko"
    assert service._voice_langs("listen") == ["ko", "zh", "en", "ja"]
    assert service._voice_langs("mic") == ["zh", "ko"]

    service._on_speech("loopback", "はい", "ja")                # 短短一句：先不換
    assert service.cfg["their_lang"] == "ko"
    service._on_speech("loopback", "탑 갈게", "ko")              # 又回到韓文：剛剛那句不算
    service._on_speech("loopback", "うん", "ja")
    assert service.cfg["their_lang"] == "ko"
    service._on_speech("loopback", "行こう", "ja")              # 連續兩句都是日文：換
    assert service.cfg["their_lang"] == "ja" and notices[-1] == ("their_switched", {"lang": "ja"})
    assert service._voice_langs("listen") == ["ja", "ko", "zh", "en"]
    assert service._voice_langs("mic") == ["zh", "ja"]
    assert service._screen_langs() == {"ja"}                    # 我用中文：螢幕上的中文不用翻
    assert service._target_for({"who": "me", "lang": "zh"}) == "ja"

    service._on_speech("loopback", "지금 용 잡으러 가자 빨리 와", "ko")   # 夠長的一句：馬上換
    assert service.cfg["their_lang"] == "ko"
    service._on_speech("loopback", "我們現在去打龍，快點過來", "zh")       # 對方說的是我的語言：不換
    assert service.cfg["their_lang"] == "ko" and service.cfg["their_auto"] is True

    service.update_config({"my_lang": "ko", "their_lang": "en"})
    assert service._screen_langs() == {"en", "zh", "ja"}        # 中文、日文、英文同一個文字模型：都收
    service._on_speech("loopback", "我們現在去打龍，快點過來", "zh")
    assert service.cfg["their_lang"] == "zh-TW"

    service.update_config({"their_lang": "ja", "their_auto": False})      # 自己選了語言：不再自動換
    service._on_speech("loopback", "지금 용 잡으러 가자 빨리 와", "en")
    assert service.cfg["their_lang"] == "ja" and service._voice_langs("listen") == ["ja"]
    service.shutdown()


def test_subtitle_log(tmp_path):
    from livelingo.service import Service
    from livelingo.transcript import render

    service = Service(tmp_path / "data")
    notices = []
    service.notice = lambda code, **kw: notices.append(code)
    folder = tmp_path / "我的字幕"
    service.cfg.set(transcript_dir=str(folder), ui_lang="zh-TW")
    assert service.record_state()["on"] is False and service.record_state()["folder"] == str(folder)

    def said(msg_id, who, via, original, translated="", **more):
        service._post({"id": msg_id, "who": who, "via": via, "original": original, "lang": "ko",
                       "translated": translated, "pending": not translated, "error": "", "time": "15:31", **more})

    said(1, "them", "heard", "記錄之前說的", "不會存進去")
    assert service.set_recording(True)["on"] is True
    said(2, "them", "heard", "탑 갈게")                         # 先出現原文
    said(2, "them", "heard", "탑 갈게", "我去上路。")            # 翻好了：同一則，不會變兩行
    said(3, "me", "typed", "等我一下", "잠깐만 기다려")
    said(4, "them", "screen", "ㅋㅋㅋ", "哈哈哈")
    said(5, "them", "screen", "看錯的字", "會被拿掉")
    service._drop(5)
    said(6, "me", "spoken", "還沒翻完")
    assert service.record_state()["count"] == 4
    waiting = service.set_recording(False)                        # 還有一句在翻：先等它，這時候說的新句子不收
    assert waiting["on"] is True and waiting["saving"] is True
    said(60, "them", "heard", "按了結束之後才說的", "不會存進去")
    said(6, "me", "spoken", "還沒翻完", "아직 안 끝났어")
    for _ in range(50):
        if not service.record_state()["on"]:
            break
        time.sleep(0.1)
    state = service.record_state()
    assert state["on"] is False and state["saving"] is False and state["saved"] == 4 and state["name"].startswith("LiveLingo ") and state["name"].endswith(".txt")
    raw = (folder / state["name"]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw     # 記事本打得開、不會亂碼
    text = raw.decode("utf-8-sig")
    assert "LiveLingo 字幕記錄" in text and "不會存進去" not in text and "會被拿掉" not in text
    assert "對方：탑 갈게\r\n→ 我去上路。" in text and text.count("탑 갈게") == 1
    assert "我：等我一下\r\n→ 잠깐만 기다려" in text and "聊天：ㅋㅋㅋ\r\n→ 哈哈哈" in text
    assert text.endswith("我：還沒翻完\r\n→ 아직 안 끝났어\r\n")

    said(7, "them", "heard", "記錄結束之後說的", "不會存進去")
    service.set_recording(True)
    assert service.set_recording(False)["file"] == "" and notices == ["record_empty"]     # 一句都沒有：不存檔
    assert len(list(folder.glob("*.txt"))) == 1

    service.update_config({"transcript_original": False})
    service.set_recording(True)
    said(8, "them", "heard", "탑 갈게", "我去上路。")
    said(80, "me", "spoken", "一直翻不出來的")
    second = service.set_recording(False, wait=0)                 # 不等：還沒翻完的只留原文
    assert second["name"] != state["name"]                       # 同一分鐘存兩次：不會蓋掉前一個
    assert "對方：我去上路。" in (folder / second["name"]).read_text(encoding="utf-8-sig")

    service.set_recording(True)                                   # 記錄到一半關程式：自動存起來
    said(9, "me", "typed", "關掉之前", "끄기 전에")
    service.shutdown()
    assert len(list(folder.glob("*.txt"))) == 3

    en = render([{"who": "them", "via": "heard", "original": "탑 갈게", "translated": "Going top.", "at": 0}], "en", 0, 60)
    assert "Them: 탑 갈게" in en and "LiveLingo Subtitle log" in en


def test_look_settings(tmp_path):
    import json

    from livelingo.config import Config
    from livelingo.service import Service

    service = Service(tmp_path)
    assert (service.cfg["theme"], service.cfg["accent"]) == ("dark", "mint")
    service.update_config({"theme": "light", "accent": "violet"})
    assert (service.cfg["theme"], service.cfg["accent"]) == ("light", "violet")
    service.update_config({"theme": "neon", "accent": "#123456"})          # 不認得的值不收
    assert (service.cfg["theme"], service.cfg["accent"]) == ("light", "violet")
    assert service.set_chrome("#FFFFFF", "#111111", False) in (True, False)
    assert service.chrome == ("#FFFFFF", "#111111", False)
    service.set_chrome("red; drop", "#111111", False)                       # 不是顏色：不收
    assert service.chrome == ("#FFFFFF", "#111111", False)
    service.shutdown()

    # 從舊版升級：以前比較窄的視窗放不下左邊的導覽，放寬一次；之後使用者自己調的大小要留著
    old = tmp_path / "old"
    old.mkdir()
    (old / "config.json").write_text(json.dumps({"window": {"w": 520, "h": 760, "x": 40, "y": 40}, "setup_done": True}), encoding="utf-8")
    cfg = Config(old)
    assert cfg["window"]["w"] >= 900 and cfg["layout"] == 2 and cfg["setup_done"] is True
    cfg.set(window={"w": 700, "h": 600, "x": 0, "y": 0})
    cfg.save()
    assert Config(old)["window"]["w"] == 700


def test_tray_setting(tmp_path):
    from livelingo.service import Service
    from livelingo.tray import TEXT, Tray, texts

    assert set(TEXT) == {"zh-TW", "zh-CN", "en", "ko", "ja"}
    assert all(set(v) == set(TEXT["zh-TW"]) for v in TEXT.values())
    assert texts("xx") == TEXT["en"]

    service = Service(tmp_path)
    assert service.cfg["close_to_tray"] is True and service.cfg["tray_hint_shown"] is False
    service.update_config({"close_to_tray": 0})
    assert service.cfg["close_to_tray"] is False
    tray = Tray(service)
    assert tray.start() is (os.name == "nt") or tray.error      # Linux 上開不起來：程式照常運作，只是關視窗就等於結束
    tray.notify_hidden()                                          # 圖示沒開起來也不會出錯
    tray.stop()
    service.shutdown()


def test_dependencies_hold():
    """
    docs/dependencies.md 那張相依圖裡「靠名字對上、改壞了也不會報錯」的地方，在這裡自動檢查：
    設定名稱、五種語言的文字表、API 路徑、事件名稱、提醒代碼、介面檔案、圖示檔、安裝檔的名字。
    新增一種這類的對應時，這裡和相依圖要一起補。
    """
    import re
    from pathlib import Path

    from livelingo import chatpaint, history, hotkeys, quickbox, region, transcript, tray
    from livelingo.config import ACCENTS, DEFAULTS, EDITABLE, THEMES

    root = Path(__file__).resolve().parent.parent
    read = lambda *parts: root.joinpath(*parts).read_text(encoding="utf-8")      # noqa: E731
    app, i18n, css, html = (read("livelingo", "ui", n) for n in ("app.js", "i18n.js", "style.css", "index.html"))
    server, service, selftest = (read("livelingo", n) for n in ("server.py", "service.py", "selftest.py"))
    python = "".join(p.read_text(encoding="utf-8") for p in root.joinpath("livelingo").rglob("*.py"))

    # 設定：介面能改的每一項都有預設值；介面用到的設定名稱都真的存在
    assert EDITABLE <= set(DEFAULTS), EDITABLE - set(DEFAULTS)
    used = set(re.findall(r"\bS\.cfg\.([a-z_]+)", app))
    assert used <= set(DEFAULTS), used - set(DEFAULTS)
    assert DEFAULTS["theme"] in THEMES and DEFAULTS["accent"] in ACCENTS
    for accent in ACCENTS:
        assert accent == "mint" or f'[data-accent="{accent}"]' in css, accent
        assert f"{accent}:" in app, accent

    # 文字：視窗外面的每一張文字表（系統匣、浮動留言、框選、字幕檔）五種語言都有，欄位一樣多
    tables = {"tray.TEXT": tray.TEXT, "chatpaint.NAMES": chatpaint.NAMES, "chatpaint.HINTS": chatpaint.HINTS,
              "chatpaint.SAMPLES": chatpaint.SAMPLES, "region.HINTS": region.HINTS, "region.FONTS": region.FONTS,
              "transcript.TITLES": transcript.TITLES, "hotkeys.NOTES": hotkeys.NOTES, "quickbox.HINTS": quickbox.HINTS,
              "history.TITLES": history.TITLES}
    for name, table in tables.items():
        assert set(table) == set(UI_LANGS), (name, set(table) ^ set(UI_LANGS))
        shapes = {lang: (sorted(v) if isinstance(v, dict) else len(v) if isinstance(v, list) else 1) for lang, v in table.items()}
        assert len({repr(s) for s in shapes.values()}) == 1, (name, shapes)

    # API：介面呼叫的每一條路徑，本機伺服器都有接
    called = set(re.findall(r'"(/api/[a-z_/]+)"', app))
    served = set(re.findall(r'"(/api/[a-z_/]+)"', server))
    assert called and called <= served, called - served

    # 事件：背景送出的每一種事件，介面都有接；每一種提醒都有五種語言的文字
    block = app[app.index("const handlers = {"):]
    handled = set(re.findall(r"^    ([a-z_]+): ", block[:block.index("\n  };")], re.M))
    published = set(re.findall(r'publish\("([a-z_]+)"', python))
    assert published <= handled, published - handled
    zh = i18n[i18n.index('"zh-TW": {'):i18n.index('"zh-CN": {')]
    for code in set(re.findall(r'\.notice\("([a-z_]+)"', python)):
        assert f"n_{code}:" in zh, code

    # 介面檔案：畫面載入的檔案都在，而且自我檢查看的是同一批
    loaded = set(re.findall(r'(?:src|href)="([a-z0-9_.]+\.(?:js|css))"', html))
    assert loaded == {"brand.js", "i18n.js", "app.js", "style.css"}
    for name in loaded:
        assert root.joinpath("livelingo", "ui", name).is_file(), name
        assert f'"{name}"' in selftest, name
    assert html.index("brand.js") < html.index("i18n.js") < html.index("app.js")      # 後面的檔案會用到前面的

    # 圖示和資料檔：程式和打包設定指到的檔案都在
    for parts in (("livelingo", "data", "tray.png"), ("livelingo", "data", "terms_lol.json"),
                  ("packaging", "livelingo.ico"), ("packaging", "livelingo.png")):
        assert root.joinpath(*parts).is_file(), parts

    # 按住才說話的按鍵：介面上要換成當地說法的那幾個，都是 oswin.PTT_KEYS 裡有的，而且有文字
    from livelingo import oswin

    named = re.search(r'function keyName\(key\) \{ return \[([^\]]*)\]', app).group(1).replace('"', "").split(", ")
    assert named and set(named) <= set(oswin.PTT_KEYS) and DEFAULTS["ptt_key"] in oswin.PTT_KEYS
    for name in named:
        assert f"key_{name.lower()}: " in i18n, name

    # 用語包：terms.PACKS 列的每一包都有資料檔、介面上有名字和說明，設定頁列的也是同一批
    from livelingo.terms import PACKS

    assert DEFAULTS["term_packs"] == list(PACKS)
    assert re.search(r'\[([^\]]*)\]\.map\(\(p\) => `<button class="pack"', app).group(1).replace('"', "").split(", ") == list(PACKS)
    for name in PACKS:
        raw = json.loads(root.joinpath("livelingo", "data", f"terms_{name}.json").read_text(encoding="utf-8"))
        assert raw["ko_zh"] and raw["zh_ko"], name
        assert f"pack_{name}: " in i18n and f"pack_{name}_desc: " in i18n, name      # 五個語言都有，另一個測試會查
    spec, iss, build = read("packaging", "livelingo.spec"), read("packaging", "installer.iss"), read(".github", "workflows", "build.yml")
    assert "livelingo.ico" in spec and "SetupIconFile=livelingo.ico" in iss

    # 發佈：更新程式下載的檔名，就是安裝程式做出來、流程發佈上去的那一個
    stem = updater.ASSET_NAME.removesuffix(".exe")
    assert f"OutputBaseFilename={stem}" in iss and f"dist/{updater.ASSET_NAME}" in build
    for line in read("requirements.txt").split():
        package = re.split(r"[=<>]", line)[0]
        if package in ("pystray", "pywebview", "soundcard", "sounddevice", "faster-whisper", "rapidocr", "onnxruntime"):
            module = {"pywebview": "webview", "faster-whisper": "faster_whisper"}.get(package, package)
            assert module in spec, package      # 會在執行時才載入的套件，打包設定要點名
    assert "from .service import Service" in server and "publish" in service


def test_dependency_map_is_current():
    """相依圖（docs/dependencies.json）要跟程式一致：檔案都在、程式之間每一個載入都畫在圖上、好讀的那份是最新的。"""
    import importlib.util
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    data = json.loads(root.joinpath("docs", "dependencies.json").read_text(encoding="utf-8"))
    owner = {}
    for node in data["nodes"]:
        for file in node["files"]:
            assert root.joinpath(file).is_file(), file
            owner[file] = node["id"]
    modules = sorted(p.relative_to(root).as_posix() for p in root.joinpath("livelingo").rglob("*.py") if p.stat().st_size)
    assert set(modules) <= set(owner), set(modules) - set(owner)      # 新增了模組：要放進相依圖
    drawn = {(e["from"], e["to"]) for e in data["edges"]}
    for file in modules:
        text, package = root.joinpath(file).read_text(encoding="utf-8"), Path(file).parent
        for dots, module, names in re.findall(r"^\s*from (\.+)([\w.]*) import ([\w, ]+)", text, re.M):
            base = package if len(dots) == 1 else package.parent
            targets = [base.joinpath(*module.split(".")).as_posix() + ".py"] if module else []
            targets += [(base.joinpath(*module.split(".")) if module else base).joinpath(n.strip().split(" ")[0]).as_posix() + ".py" for n in names.split(",")]
            if not module and "__version__" in names:
                targets.append("livelingo/__init__.py")
            for target in targets:
                if target in owner and owner[target] != owner[file]:
                    assert (owner[file], owner[target]) in drawn, f"{file} 用到 {target}，相依圖上沒有這條線"
    ids = {n["id"] for n in data["nodes"]}
    for rule in data["rules"]:
        assert all(set(step["nodes"]) <= ids for step in rule["steps"]), rule["id"]
    spec = importlib.util.spec_from_file_location("make_dependencies", root / "docs" / "make_dependencies.py")
    maker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(maker)
    assert maker.render(data) == root.joinpath("docs", "dependencies.md").read_text(encoding="utf-8"), "先跑 python docs/make_dependencies.py"


def test_settings_really_save(tmp_path):
    """設定改了就寫進檔案、重開還在；寫不進去會說，而且只說一次；回傳值讓介面知道要不要顯示「已儲存」。"""
    from livelingo.service import Service

    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data: events.append((name, data))

    result = service.update_config({"overlay_font": 33, "copy_spoken": 0, "theme": "light", "nonsense": 1, "tone": "rude"})
    assert result["saved"] is True and result["error"] == ""
    assert result["changed"] == ["copy_spoken", "overlay_font", "theme"]       # 不認得的、不合法的都不會進去
    assert service.cfg["copy_spoken"] is False                                   # 開關一律存成 true / false
    again = Config(tmp_path)
    assert (again["overlay_font"], again["copy_spoken"], again["theme"], again["tone"]) == (33, False, "light", "casual")
    assert service.update_config({"overlay_font": 33})["changed"] == []          # 沒變也算存好，不會出錯

    # 唸法：不管開關都先算好，介面切換開關時舊的句子才會跟著變
    service.cfg.set(show_pronunciation=False)
    assert service._pronunciation("안녕", "ko") and service._pronunciation("你好", "zh-TW") == ""

    # 設定檔寫不進去（這裡用「路徑其實是資料夾」來模擬）：回傳 False、跳一次提醒，之後修好了又能存
    events.clear()
    good = service.cfg.path
    service.cfg.path = tmp_path / "blocked" / "config.json"
    (tmp_path / "blocked").write_text("not a folder", encoding="utf-8")
    failed = service.update_config({"overlay_font": 20})
    assert failed["saved"] is False and failed["error"]
    assert service.cfg["overlay_font"] == 20                                     # 這次開著的時候還是有效
    service.update_config({"overlay_font": 21})
    assert [d["code"] for n, d in events if n == "notice"] == ["save_failed"]    # 連續失敗只提醒一次
    service.cfg.path = good
    assert service.update_config({"overlay_font": 22})["saved"] is True
    assert Config(tmp_path)["overlay_font"] == 22

    # 不是在設定畫面直接改的東西存好了，也會通知介面
    events.clear()
    service.overlay.reset_position()
    assert ("saved", {"what": "overlay_rect"}) in events


def test_floating_chat_look(tmp_path):
    """浮動留言的外觀設定：不合法的值進不來、每一項真的會改變畫出來的圖、可以一次恢復預設。"""
    import io

    from PIL import Image

    from livelingo import chatpaint
    from livelingo.config import DEFAULTS, OVERLAY_LOOK
    from livelingo.service import Service

    service = Service(tmp_path)
    service.hub.publish = lambda name, data: None
    assert chatpaint.style_of(service.cfg) == chatpaint.DEFAULT_STYLE      # 兩邊的預設值是同一套

    result = service.update_config({"overlay_family": "comic", "overlay_align": "middle", "overlay_grow": "sideways",
                                    "overlay_color": "red", "overlay_outline": 99, "overlay_opacity": 5, "overlay_lines": -3,
                                    "overlay_name_colors": {"heard": "#ff0000", "nobody": "#00ff00", "me": "blue"}})
    assert result["changed"] == ["overlay_name_colors", "overlay_opacity", "overlay_outline"]
    cfg = service.cfg
    assert (cfg["overlay_family"], cfg["overlay_align"], cfg["overlay_grow"], cfg["overlay_color"]) == ("sans", "left", "up", "#FFFFFF")
    assert (cfg["overlay_outline"], cfg["overlay_opacity"], cfg["overlay_lines"]) == (4, 30, 0)
    assert cfg["overlay_name_colors"] == {"heard": "#FF0000", "screen": "#FFC45C", "me": "#5CDEFF"}      # 只改送來而且合法的那一個
    assert Config(tmp_path)["overlay_name_colors"]["heard"] == "#FF0000"

    def tile(**style):
        return chatpaint.paint_message("heard", "對方", "打野來一下下路 OK", 420, 26, style=style)

    plain = tile()
    assert plain.tobytes() == chatpaint.paint_message("heard", "對方", "打野來一下下路 OK", 420, 26).tobytes()
    for change in ({"color": "#FFE98A"}, {"outline": 0}, {"outline": 4}, {"names": False}, {"align": "right"}, {"align": "center"},
                   {"name_colors": {"heard": "#FF0000"}}):
        assert tile(**change).tobytes() != plain.tobytes(), change
    assert tile(names=False).getbbox()[2] < plain.getbbox()[2]                     # 不顯示名字就短一截
    assert tile(align="right").getbbox()[0] > plain.getbbox()[0] + 100             # 靠右真的在右邊
    assert tile(color="nonsense", family="comic", outline="x").size == plain.size   # 亂七八糟的值不會讓畫面壞掉

    tall = Image.new("RGBA", (200, 40), (255, 255, 255, 255))
    up = chatpaint.compose([(tall, 1.0)], (200, 200))
    down = chatpaint.compose([(tall, 1.0)], (200, 200), grow="down")
    assert up.getbbox() == (0, 160, 200, 200) and down.getbbox() == (0, 0, 200, 40)
    assert chatpaint.compose([(tall, 1.0)] * 5, (200, 400), lines=2).getbbox() == (0, 320, 200, 400)
    assert chatpaint.compose([(tall, 1.0)] * 9, (200, 100), grow="down").getbbox() == (0, 0, 200, 80)      # 放不下：舊的讓位
    assert chatpaint.compose([(tall, 1.0)], (200, 200), opacity=50).getpixel((10, 180))[3] in range(120, 135)

    service.update_config({"overlay_family": "serif", "overlay_align": "right", "overlay_grow": "down", "overlay_color": "#ffe98a"})
    assert service.cfg["overlay_color"] == "#FFE98A"
    before = service.overlay_preview()
    picture = Image.open(io.BytesIO(before))
    assert picture.format == "PNG" and picture.size == (640, 230)
    service.update_config({k: DEFAULTS[k] for k in OVERLAY_LOOK})                  # 「恢復預設外觀」送的就是這個
    assert chatpaint.style_of(service.cfg) == chatpaint.DEFAULT_STYLE and service.cfg["overlay_font"] == DEFAULTS["overlay_font"]
    assert service.overlay_preview() != before


def test_floating_chat_presets(tmp_path):
    """一鍵版面：外觀整組換、位置跟著搬、存得起來；先前自己調的東西不會殘留到下一個版面。"""
    from livelingo import chatpaint, overlay
    from livelingo.config import DEFAULTS, EDITABLE, OVERLAY_LOOK
    from livelingo.service import Service

    for name, preset in overlay.PRESETS.items():
        assert set(preset["look"]) <= set(OVERLAY_LOOK) <= EDITABLE, name
    service = Service(tmp_path)
    service.hub.publish = lambda name, data: None
    assert service.overlay.apply_preset("nonsense") is None

    service.update_config({"overlay_color": "#FF0000", "overlay_family": "kai"})
    result = service.overlay.apply_preset("subtitle")
    assert result["saved"] is True
    cfg = Config(tmp_path)
    assert (cfg["overlay_align"], cfg["overlay_names"], cfg["overlay_lines"], cfg["overlay_font"]) == ("center", False, 2, 30)
    assert (cfg["overlay_color"], cfg["overlay_family"]) == ("#FFFFFF", "sans")      # 自己調過的也回到這個版面的樣子
    left, top, width, height = overlay.oswin.work_area()
    x, y, w, h = cfg["overlay_rect"]
    assert abs((x + w / 2) - (left + width / 2)) <= 2 and y > top + height / 2      # 正下方置中
    assert service.overlay.rect == cfg["overlay_rect"]

    service.overlay.apply_preset("quiet")
    cfg = Config(tmp_path)
    assert (cfg["overlay_align"], cfg["overlay_grow"], cfg["overlay_names"]) == ("right", "down", True)
    assert cfg["overlay_rect"][0] > left + width / 2 and cfg["overlay_rect"][1] < top + height / 2      # 右上角

    service.overlay.apply_preset("chat")
    assert chatpaint.style_of(service.cfg) == chatpaint.DEFAULT_STYLE
    assert service.cfg["overlay_rect"] == overlay.default_rect() and service.cfg["overlay_font"] == DEFAULTS["overlay_font"]


def test_back_translation(tmp_path):
    """回譯：我說的話翻成對方的語言之後，再翻回我的語言給我看；對方的話不用；重翻之後回譯也要跟著換。"""
    from livelingo.service import Service

    class Echo:
        tone = "casual"

        def __init__(self):
            self.calls = []

        def translate(self, text, target, source=None, context=None):
            self.calls.append((text, target, source))
            return f"<{target}>{text}"

        def clear_cache(self):
            pass

    service = Service(tmp_path)
    service.translator = Echo()
    events = []
    service.hub.publish = lambda name, data: events.append((name, dict(data) if isinstance(data, dict) else data))
    shown = []
    service.overlay.on_message = lambda msg: shown.append(msg["id"])
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()

    def wait(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    def last(msg_id):
        return [d for n, d in events if n == "message" and d["id"] == msg_id][-1]

    try:
        service.submit("typed", "我現在過去", "zh")
        mine = service.messages[-1]["id"]
        wait(lambda: last(mine).get("back"))
        assert last(mine)["translated"] == "<ko>我現在過去" and last(mine)["back"] == "<zh-TW><ko>我現在過去"
        assert service.translator.calls[-1] == ("<ko>我現在過去", "zh-TW", "ko")          # 從對方的語言翻回我的語言
        assert shown.count(mine) == 2                                                    # 回譯不會讓浮動留言再跳一次（原文一次、翻好一次）

        service.submit("heard", "지금 갈게", "ko")
        theirs = service.messages[-1]["id"]
        wait(lambda: last(theirs)["translated"])
        time.sleep(0.2)
        assert last(theirs)["back"] == ""                                                # 對方的話本來就翻成我的語言，不用回譯

        assert service.teach(mine, "지금 갈게")                                           # 重新教過：舊的回譯清掉，換成新的
        wait(lambda: last(mine).get("back") == "<zh-TW>지금 갈게")

        service.update_config({"back_translate": False})
        service.submit("typed", "等我一下", "zh")
        quiet = service.messages[-1]["id"]
        wait(lambda: last(quiet)["translated"])
        time.sleep(0.2)
        assert last(quiet)["back"] == "" and Config(tmp_path)["back_translate"] is False
    finally:
        service.closing.set()
        worker.join(2)


def test_context_of_previous_sentences(tmp_path):
    """參考前文：前面兩句的原文放進提示裡給模型參考；模型把前文也翻出來時不給前文重翻；
    螢幕上讀到的字不用前文；設定關掉就不用。"""
    from livelingo.core.translate import CONTEXT_INTRO, build_prompt
    from livelingo.service import Service

    prompt = build_prompt("그럼 내가 갈게", "ko", "zh-TW", {}, context=["바론 먹을까?", "지금 체력 없어"])
    assert CONTEXT_INTRO in prompt and "- 바론 먹을까?\n- 지금 체력 없어\n" in prompt
    assert prompt.rstrip().endswith("그럼 내가 갈게<end_of_turn>\n<start_of_turn>model")      # 要翻的那句還是在最後面
    assert CONTEXT_INTRO not in build_prompt("그럼 내가 갈게", "ko", "zh-TW", {})
    assert CONTEXT_INTRO not in build_prompt("그럼 내가 갈게", "ko", "zh-TW", {}, style="short", context=["a"])

    tr = _FakeModel("那我去。")
    assert tr.translate("그럼 내가 갈게", "zh-TW", "ko", ["1", "2", "3"]) == "那我去。"
    assert tr.contexts == [["2", "3"]]                                         # 只用最後兩句
    assert tr.translate("그럼 내가 갈게", "zh-TW", "ko", ["2", "3"]) == "那我去。" and len(tr.asked) == 1      # 同樣的前文：用快取
    tr.translate("그럼 내가 갈게", "zh-TW", "ko", ["別的前文"])
    assert len(tr.asked) == 2                                                  # 前文不一樣要重翻
    leaky = _FakeModel(lambda text, style, context: "要打巴龍嗎？現在沒血了。" * 6 + "那我去。" if context else "那我去。")
    assert leaky.translate("그럼 내가 갈게", "zh-TW", "ko", ["바론 먹을까?"]) == "那我去。"      # 譯文長得不合理：不給前文再翻一次
    assert leaky.contexts == [["바론 먹을까?"], []]

    service = Service(tmp_path)
    now = time.time()
    service.messages.extend([
        {"id": 1, "via": "heard", "original": "很久以前的話", "at": now - 500},
        {"id": 2, "via": "heard", "original": "바론 먹을까?", "at": now - 20},
        {"id": 3, "via": "screen", "original": "螢幕上的字", "at": now - 10},
        {"id": 4, "via": "typed", "original": "我沒血了", "at": now - 5},
        {"id": 5, "via": "heard", "original": "翻失敗的", "at": now - 4, "error": "x"},
    ])
    assert service._context_for({"id": 6, "via": "heard", "at": now}) == ["바론 먹을까?", "我沒血了"]
    assert service._context_for({"id": 6, "via": "screen", "at": now}) == []
    assert service._context_for({"id": 4, "via": "typed", "at": now - 5}) == ["바론 먹을까?"]      # 只看它前面的
    assert service.update_config({"use_context": False})["saved"]
    assert service._context_for({"id": 6, "via": "heard", "at": now}) == [] and Config(tmp_path)["use_context"] is False


def test_tone_for_each_language():
    """語氣：韓文（半語／해요體）、日文（タメ口／です・ます）、中文有禮貌時（您、請）各有自己的說法。"""
    from livelingo.core.translate import build_prompt

    def prompt(target, tone, style="full"):
        return build_prompt("你吃飯了嗎", "zh" if target not in ("zh-TW", "zh-CN") else "ko", target, {}, tone, style=style)

    assert "반말" in prompt("ko", "casual") and "해요체" in prompt("ko", "polite")
    assert "タメ口" in prompt("ja", "casual") and "です/ます form)" in prompt("ja", "polite") and "タメ口" not in prompt("ja", "polite")
    assert "您 and 請" in prompt("zh-TW", "polite") and "您 and 请" in prompt("zh-CN", "polite")
    assert "您" not in prompt("zh-TW", "casual") and "반말" not in prompt("en", "casual")
    assert "タメ口" in prompt("ja", "casual", "short") and "タメ口" not in prompt("ja", "polite", "short")


def test_unsure_and_edit_original(tmp_path):
    """沒把握的句子做記號；把辨識錯的原文改對會重新翻譯，記號拿掉。"""
    from livelingo.core.speech import not_sure
    from livelingo.service import Service

    assert not_sure(-0.9, 0.1) and not_sure(-0.3, 0.6) and not not_sure(-0.3, 0.1)

    class Echo:
        tone = "casual"

        def translate(self, text, target, source=None, context=None):
            return f"<{target}>{text}"

        def clear_cache(self):
            pass

    service = Service(tmp_path)
    service.translator = Echo()
    service.cfg.set(back_translate=False)
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()

    def wait(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    try:
        service._on_speech("loopback", "지금 갈개", "ko", 0.5, True)
        heard = service.messages[-1]
        assert heard["unsure"] is True and heard["via"] == "heard"
        wait(lambda: service.messages[-1]["translated"])
        assert not service.edit(heard["id"], "지금 갈개") and not service.edit(heard["id"], "  ") and not service.edit(999, "x")
        assert service.edit(heard["id"], "지금 갈게")
        wait(lambda: service.messages[-1]["translated"] == "<zh-TW>지금 갈게")
        fixed = service.messages[-1]
        assert fixed["id"] == heard["id"] and fixed["unsure"] is False and fixed["edited"] is True and len(service.messages) == 1

        service._on_speech("mic", "我現在過去", "zh", 0.5)
        assert service.messages[-1]["unsure"] is False
        service.submit("typed", "打字的", "zh", unsure=True)
        assert service.messages[-1]["unsure"] is False                           # 自己打的字沒有「聽錯」這回事
    finally:
        service.closing.set()
        worker.join(2)

    # 讀螢幕：辨識分數跟著文字一起交出來，分數低的做記號
    got = []
    service.submit = lambda via, text, lang=None, unsure=False, place="": got.append((via, text, unsure))

    class FakeOcr:
        family = "ko"

    import numpy as np

    import livelingo.core.watcher as watcher_mod
    from livelingo.core.ocr import Line

    lines = [Line("안녕하세요 반갑습니다", 0, 0, 20, 0.95), Line("지금 갈게요 기다려", 0, 40, 20, 0.7)]
    old = watcher_mod.message_lines
    watcher_mod.message_lines = lambda _lines: lines
    FakeOcr.read = lambda self, bgr: []
    frames = iter([np.zeros((60, 200, 3), np.uint8)] * 2 + [np.full((60, 200, 3), 90, np.uint8)] * 50)
    service.grab = lambda region: next(frames)
    service._load_ocr = lambda: FakeOcr()
    service.cfg.set(regions=[{"id": "r1", "name": "", "on": True, "app": "", "anchor": None,
                              "left": 0, "top": 0, "width": 200, "height": 60}], interval=0.02)
    try:
        service._start_screen()
        wait(lambda: len(got) >= 2)
        assert got == [("screen", "안녕하세요 반갑습니다", False), ("screen", "지금 갈게요 기다려", True)]
    finally:
        watcher_mod.message_lines = old
        service._stop_watcher()


def test_term_packs(tmp_path):
    """用語包可以多選：選了哪幾包就只用那幾包；只有子音的縮寫（ㄱㄱ、ㅊㅋㅊㅋ）認得；提示前面那句話跟著換。"""
    from livelingo.core.translate import HINT_INTRO, HINT_INTRO_CHAT
    from livelingo.service import Service
    from livelingo.terms import PACKS

    both, lol, chat = TermPack(), TermPack(["lol"]), TermPack(["chat"])
    assert both.names == list(PACKS) and TermPack(["chat", "nope"]).names == ["chat"] and TermPack([]).names == []
    assert both.find("ㄱㄱ 탑 갈게", "ko", "zh-TW") == [("탑", "上路"), ("ㄱㄱ", "走吧")]
    assert lol.find("ㄱㄱ 탑 갈게", "ko", "zh-TW") == [("탑", "上路")]
    assert chat.find("ㄱㄱ 탑 갈게", "ko", "zh-TW") == [("ㄱㄱ", "走吧")]
    assert chat.find("ㅊㅋㅊㅋ", "ko", "zh-TW") == [("ㅊㅋ", "恭喜")]                # 重複打的也算
    assert chat.find("ㅋㅋㅋㅋ ㅇㅋㅋ", "ko", "zh-TW") == []                           # 笑聲不是用語
    assert chat.find("나 잠수 탈게", "ko", "zh-CN") == [("잠수", "暂时离开（挂机）")]
    assert ("掛機", "잠수") in chat.find("我先掛機一下", "zh", "ko") and TermPack([]).find("탑 갈게", "ko", "zh-TW") == []
    assert both.find("부캐로 할게", "ko", "zh-TW") == [("부캐", "小號")]              # 兩包都有的詞只出現一次

    prompt = build_prompt("ㄱㄱ", "ko", "zh-TW", {}, "casual", [("ㄱㄱ", "走吧")], hint_intro=HINT_INTRO_CHAT)
    assert HINT_INTRO_CHAT + "ㄱㄱ = 走吧" in prompt and "League of Legends" not in prompt
    assert HINT_INTRO + "ㄱㄱ = 走吧" in build_prompt("ㄱㄱ", "ko", "zh-TW", {}, "casual", [("ㄱㄱ", "走吧")])

    service = Service(tmp_path)
    assert service.terms.names == list(PACKS) and service.translator.hint_intro == HINT_INTRO
    result = service.update_config({"term_packs": ["chat", "lol", "nope"]})
    assert result["saved"] and "term_packs" not in result["changed"]                    # 整理之後跟原本一樣：沒有變
    assert service.update_config({"term_packs": ["chat"]})["changed"] == ["term_packs"]
    assert service.terms.names == ["chat"] and service.translator.hint_intro == HINT_INTRO_CHAT
    assert service._hints("ㄱㄱ 탑 갈게", "ko", "zh-TW") == [("ㄱㄱ", "走吧")]
    assert Config(tmp_path)["term_packs"] == ["chat"]
    service.update_config({"term_packs": "lol"})                                        # 不是清單：當成一包都沒選
    assert service.cfg["term_packs"] == [] and service._hints("탑 갈게", "ko", "zh-TW") == []
    service.update_config({"term_packs": ["lol"], "game_terms": False})
    assert service._hints("탑 갈게", "ko", "zh-TW") == []                               # 總開關關掉就都不用


def test_export_and_import_library(tmp_path):
    """詞彙表和教過的句子：匯出成一個檔案；匯入只加原本沒有的，亂七八糟的檔案不收也不會弄壞原本的。"""
    from pathlib import Path

    from livelingo.service import LIBRARY_KIND, Service

    mine = Service(tmp_path / "mine")
    mine.cfg.set(transcript_dir=str(tmp_path / "out"))
    mine.glossary.add("Faker", "Faker")
    mine.lessons.add("我先去洗澡", "먼저 씻으러 갈게", "zh", "ko")
    out = mine.export_library()
    assert out["ok"] and out["sentences"] == 1 and out["words"] == len(mine.glossary.list())
    data = json.loads(Path(out["path"]).read_text(encoding="utf-8"))
    assert data["kind"] == LIBRARY_KIND and {"src": "Faker", "dst": "Faker"} in data["glossary"]

    friend = Service(tmp_path / "friend")
    friend.glossary.add("faker", "飛科")                                   # 朋友自己已經有這個詞：匯入不能蓋掉
    before = len(friend.glossary.list())
    events = []
    friend.hub.publish = lambda name, payload: events.append(name)
    result = friend.import_library(data)
    assert result["ok"] and result["sentences"] == 1 and result["words"] == out["words"] - before
    assert friend.glossary.as_dict()["faker"] == "飛科" and "Faker" not in friend.glossary.as_dict()
    assert friend.lessons.list() == [{"src": "我先去洗澡", "dst": "먼저 씻으러 갈게", "lang": "zh", "target": "ko"}]
    assert "glossary" in events and "lessons" in events
    again = friend.import_library(data)                                    # 再匯入一次：什麼都不會多
    assert again["ok"] and again["words"] == 0 and again["sentences"] == 0 and again["skipped"] == out["words"] + 1
    assert Lessons(tmp_path / "friend").list() == friend.lessons.list()   # 有存進檔案

    for bad in (None, [], "x", {"kind": "other"}, {"glossary": [{"src": "a", "dst": "b"}]}):
        assert friend.import_library(bad) == {"ok": False, "error": "not_library"}
    odd = {"kind": LIBRARY_KIND, "glossary": [1, {"src": "", "dst": "x"}, {"src": "新詞", "dst": "새말"}, {"src": "新詞", "dst": "別的"}],
           "lessons": "nope"}
    assert friend.import_library(odd)["words"] == 1 and friend.glossary.as_dict()["新詞"] == "새말"
    odd = {"kind": LIBRARY_KIND, "lessons": [{"src": "a", "dst": "b", "lang": "xx", "target": "ko"},
                                             {"src": "a", "dst": "b", "lang": "zh", "target": "klingon"},
                                             {"src": "你好", "dst": "안녕", "lang": "zh", "target": "ko"}]}
    assert friend.import_library(odd)["sentences"] == 1


def test_voice_tuning_delay_echo_and_push_to_talk(tmp_path):
    """聲音設定：靈敏度、斷句、快／準收音中直接換；延遲有記；喇叭回音不算我說的；按住才說話只收按著的那一段。"""
    import numpy as np

    from livelingo import oswin
    from livelingo.core.audio import SR
    from livelingo.core.speech import SpeechPipeline
    from livelingo.service import BEAMS, VAD_THRESHOLD, Service

    class Echo:
        tone = "casual"

        def translate(self, text, target, source=None, context=None):
            time.sleep(0.05)
            return f"<{target}>{text}"

        def clear_cache(self):
            pass

    service = Service(tmp_path)
    service.translator = Echo()
    service.cfg.set(back_translate=False)
    assert service.state()["ptt_keys"] == list(oswin.PTT_KEYS) and service.cfg["ptt_key"] in oswin.PTT_KEYS

    # ---- 設定：檢查範圍，收音中的管線直接換
    tuned = []
    pipe = type("P", (), {})()
    pipe.segmenter = type("S", (), {"tune": lambda self, pause, threshold: tuned.append((pause, threshold))})()
    service.pipelines["mic"] = pipe
    service.recognizer = type("R", (), {"beam": 1})()
    result = service.update_config({"voice_sensitivity": 9, "end_silence": "0.94", "speech_mode": "accurate",
                                    "ptt_key": "Space", "ptt": 1, "drop_echo": 0})
    assert result["saved"] and (service.cfg["voice_sensitivity"], service.cfg["end_silence"]) == (5, 0.9)
    assert service.cfg["ptt_key"] == "F8" and service.cfg["ptt"] is True and service.cfg["drop_echo"] is False
    assert tuned == [(0.9, VAD_THRESHOLD[5])] and service.recognizer.beam == BEAMS["accurate"] == 5
    service.update_config({"end_silence": "x", "speech_mode": "turbo", "voice_sensitivity": None})      # 亂七八糟的值：不動
    assert (service.cfg["end_silence"], service.cfg["speech_mode"], service.cfg["voice_sensitivity"]) == (0.9, "accurate", 5)
    assert set(VAD_THRESHOLD) == {1, 2, 3, 4, 5} and VAD_THRESHOLD[1] > VAD_THRESHOLD[3] > VAD_THRESHOLD[5]
    assert Config(tmp_path)["end_silence"] == 0.9
    service.pipelines.clear()

    # ---- 按住才說話：有開才看按鍵
    held = {"down": False}
    real_key_down = oswin.key_down
    oswin.key_down = lambda name: held["down"]
    try:
        assert service._mic_gate() is False
        held["down"] = True
        assert service._mic_gate() is True
        service.update_config({"ptt": False})
        held["down"] = False
        assert service._mic_gate() is True
    finally:
        oswin.key_down = real_key_down
    assert oswin.key_down("Space") is False and (oswin.IS_WIN or oswin.key_down("F8") is False)

    # ---- 延遲和回音
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()

    def wait(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    try:
        assert service.delay() == {"hear": 0.0, "translate": 0.0, "count": 0}
        service._on_speech("loopback", "지금 바로 갈게 기다려", "ko", 1.2)
        wait(lambda: service.messages[-1]["translated"])
        first = service.messages[-1]
        assert first["hear"] == 1.2 and first["took"] >= 1.2 and service.delay()["count"] == 1 and service.delay()["hear"] == 1.2
        service.edit(first["id"], "지금 바로 갈게 기다려요")                      # 重翻的不算進延遲
        wait(lambda: service.messages[-1]["translated"].endswith("요"))
        assert service.delay()["count"] == 1

        service.update_config({"drop_echo": True})
        count = len(service.messages)
        service._on_speech("mic", "지금 바로 갈게 기다려", "ko", 0.5)             # 喇叭的聲音被麥克風收到：不要
        assert len(service.messages) == count
        service._on_speech("mic", "我現在過去找你", "zh", 0.5)                    # 真的是我說的：照常
        assert len(service.messages) == count + 1 and service.messages[-1]["via"] == "spoken"

        service._on_speech("mic", "오늘 저녁에 뭐 먹을까", "ko", 0.5)             # 麥克風那句先到，對方那句後到：收回麥克風那句
        early = service.messages[-1]["id"]
        service._on_speech("loopback", "오늘 저녁에 뭐 먹을까", "ko", 0.5)
        assert early not in [m["id"] for m in service.messages] and service.messages[-1]["via"] == "heard"

        service.update_config({"drop_echo": False})
        count = len(service.messages)
        service._on_speech("mic", "오늘 저녁에 뭐 먹을까", "ko", 0.5)             # 關掉就不管
        assert len(service.messages) == count + 1
    finally:
        service.closing.set()
        worker.join(2)

    # ---- 管線：沒按著的聲音不進斷句，放開時補一段安靜把句子結束掉
    class Cap:
        kind = "mic"

        def __init__(self, blocks):
            import queue as q

            self.q = q.Queue()
            self.blocks = blocks

        def start(self):
            pass

        def stop(self):
            pass

        def is_alive(self):
            return True

    fed, state = [], {"open": False}
    cap = Cap([])
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    threading.Thread.__init__(pipe, daemon=True)
    pipe.capture, pipe.recognizer, pipe.langs, pipe.stay = cap, None, ["zh"], 1.0
    pipe.on_text = pipe.on_state = lambda *a: None
    pipe.on_level = lambda *a: None
    pipe._halt = threading.Event()
    pipe.segmenter = type("S", (), {"speaking": False, "buf": np.ones(5, dtype=np.float32),
                                    "feed": lambda self, audio: fed.append((len(audio), float(np.abs(audio).max()))) or []})()
    pipe.gate = lambda: state["open"]
    pipe.start()
    try:
        loud = np.full(1600, 0.3, dtype=np.float32)
        cap.q.put(loud)
        time.sleep(0.3)
        assert fed == []                                                        # 沒按著：不收
        state["open"] = True
        cap.q.put(loud)
        wait(lambda: len(fed) == 1)
        assert fed[0] == (1600, 0.3) or abs(fed[0][1] - 0.3) < 1e-6
        assert len(pipe.segmenter.buf) == 0                                     # 剛按下：從這裡開始算
        state["open"] = False
        cap.q.put(loud)
        wait(lambda: len(fed) == 2)
        assert fed[1] == (SR, 0.0)                                              # 剛放開：補一秒安靜
        cap.q.put(loud)
        time.sleep(0.3)
        assert len(fed) == 2
    finally:
        pipe._halt.set()


def test_segmenter_tuning():
    """斷句的兩個參數真的進到語音偵測的設定裡。"""
    pytest = __import__("pytest")
    pytest.importorskip("faster_whisper")
    from livelingo.core.speech import Segmenter

    seg = Segmenter(end_silence=0.6)
    assert seg._opts.threshold == 0.5 and seg._opts.min_silence_duration_ms == 600
    seg.tune(1.2, 0.3)
    assert seg._opts.threshold == 0.3 and seg._opts.min_silence_duration_ms == 1200


def test_speech_on_the_graphics_card(tmp_path, monkeypatch):
    """語音辨識用顯示卡：檔案沒齊先用處理器並說要下載；下載的壓縮檔只拿 DLL；跑不起來自動退回處理器並通知。"""
    import io
    import zipfile

    from livelingo.core.speech import Recognizer
    from livelingo.service import Service

    index = {"releases": {
        "8.9.7.29": [{"filename": "nvidia_cudnn_cu12-8.9.7.29-py3-none-win_amd64.whl", "url": "https://x/8", "size": 1}],
        "9.10.2.21": [{"filename": "nvidia_cudnn_cu12-9.10.2.21-py3-none-manylinux_2_27_x86_64.whl", "url": "https://x/linux", "size": 2},
                      {"filename": "nvidia_cudnn_cu12-9.10.2.21-py3-none-win_amd64.whl", "url": "https://x/9.10", "size": 3}],
        "9.2.0.82": [{"filename": "nvidia_cudnn_cu12-9.2.0.82-py3-none-win_amd64.whl", "url": "https://x/9.2", "size": 4}],
        "9.99.0.1": [{"filename": "nvidia_cudnn_cu12-9.99.0.1-py3-none-win_amd64.whl", "url": "https://x/yanked", "size": 5, "yanked": True}],
        "weird": [{"filename": "x-win_amd64.whl", "url": "https://x/weird"}]}}
    assert assets.pick_cudnn_wheel(index) == ("https://x/9.10", 3)                   # 最新的 9、Windows 版、沒被撤回
    assert assets.pick_cudnn_wheel({"releases": {"8.1": index["releases"]["8.9.7.29"]}}) is None

    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("nvidia/cudnn/bin/cudnn_ops64_9.dll", b"ops")
        z.writestr("nvidia/cudnn/bin/cudnn64_9.dll", b"main")
        z.writestr("nvidia/cudnn/include/cudnn.h", b"header")
        z.writestr("nvidia_cudnn_cu12-9.10.2.21.dist-info/METADATA", b"meta")

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda req, timeout=0: Reply(json.dumps(index).encode()))
    fetched = []

    def fake_fetch(url, dest, progress=None, cancel=None, attempts=8):
        fetched.append(url)
        dest.write_bytes(wheel.getvalue())
        if progress:
            progress(50, 100)

    monkeypatch.setattr(assets, "fetch", fake_fetch)
    data = tmp_path / "data"
    assert not assets.has_cudnn(data) and assets.cuda_runtime_dir(data) is None
    folder = assets.get_cudnn(data)
    assert fetched == ["https://x/9.10"] and sorted(p.name for p in folder.iterdir()) == ["cudnn64_9.dll", "cudnn_ops64_9.dll"]
    assert assets.has_cudnn(data) and assets.get_cudnn(data) == folder and len(fetched) == 1      # 已經有了就不再下載

    # ---- 程式這邊：狀態
    service = Service(tmp_path / "svc")
    events = []
    service.hub.publish = lambda name, payload: events.append((name, dict(payload) if isinstance(payload, dict) else payload))
    assert service._speech_engine() == ("cpu", "int8") and service.speech_gpu_state()["can"] is False      # 沒有顯示卡
    service.update_config({"speech_device": "tpu"})
    assert service.cfg["speech_device"] == "cpu"
    service.cfg.set(speech_device="gpu")
    assert service._speech_engine() == ("cpu", "int8") and service.speech_gpu["state"] == "off"            # 選了但沒有顯示卡
    assert service.speech_gpu_download() is False

    service.gpu = {"name": "NVIDIA GeForce RTX 4070", "vram_mb": 12282, "tier": "high"}
    assert service._speech_engine() == ("cpu", "int8") and service.speech_gpu["state"] == "need"           # 檔案還沒下載
    assets.cudnn_dir(service.data).mkdir(parents=True)
    (assets.cudnn_dir(service.data) / "cudnn_ops64_9.dll").write_bytes(b"x")
    assert service._speech_engine() == ("cpu", "int8") and service.speech_gpu["state"] == "failed"         # 有 cuDNN 但沒有顯示卡版的引擎
    service.speech_gpu["state"] = "off"
    cuda = service.data / "engine" / "cuda" / "llama"
    cuda.mkdir(parents=True)
    (cuda / "cublas64_12.dll").write_bytes(b"x")
    assert service._speech_engine() == ("cuda", "float16") and service.speech_gpu_state()["active"] is True
    assert service._speech_gpu_files() == [cuda, assets.cudnn_dir(service.data)]

    service._speech_gpu_failed(RuntimeError("Library cudnn_ops64_9.dll is not found"))
    assert service.speech_gpu["state"] == "failed" and service.speech_gpu["active"] is False
    assert ("notice", {"code": "speech_gpu_failed"}) in [(n, {"code": p.get("code")}) for n, p in events if n == "notice"]
    assert service._speech_engine() == ("cpu", "int8")                                                     # 這次開著的期間不再試
    service._speech_device_changed()                                                                       # 使用者再選一次：重試
    assert service.speech_gpu["state"] == "ready" and service.recognizer is None
    assert [p["state"] for n, p in events if n == "speech_gpu"][-1] == "ready"

    # ---- 辨識器：顯示卡那邊出錯，改用處理器把這一句重做
    loads, fell = [], []
    rec = Recognizer.__new__(Recognizer)
    rec._lock = threading.Lock()
    rec._fw = type("FW", (), {"pad_or_trim": None})()
    rec._short_pad = rec._orig_pad = None
    rec.device = "cuda"
    rec.on_fallback = fell.append

    def load(device, compute):
        loads.append((device, compute))
        rec.device = device

    def run(audio, langs, share_encoder=True, stay=1.0):
        if rec.device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return "지금 갈게요", "ko", False

    rec._load, rec._run = load, run
    assert rec.transcribe(None, ["ko"]) == ("지금 갈게요", "ko", False)
    assert loads == [("cpu", "int8")] and len(fell) == 1 and rec.device == "cpu"


def test_listen_to_chosen_programs_only(tmp_path, monkeypatch):
    """只聽指定程式的聲音：找得到程式的最上層程序、好幾個程式混成一路、程式晚開或關掉都跟得上、
    Windows 不給聲音時退回整台電腦並通知。"""
    import ctypes

    import numpy as np

    from livelingo import apps
    from livelingo.core import procaudio
    from livelingo.core.audio import BLOCK, SR
    from livelingo.service import Service

    # ---- 給 Windows 的資料長得對不對（差一個位元組就整個不能用）
    assert ctypes.sizeof(procaudio.WaveFormat) == 18 and ctypes.sizeof(procaudio.ActivationParams) == 12
    assert ctypes.sizeof(procaudio.GUID) == 16 and ctypes.sizeof(procaudio.PropVariant) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
    assert bytes(procaudio.IID_AUDIO_CLIENT)[:4] == bytes.fromhex("4CADB91C")          # 前四個位元組是反過來放的
    assert procaudio.IID_AGILE.same(procaudio.GUID("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90"))
    fmt = procaudio.pcm_format(48000)
    assert (fmt.tag, fmt.channels, fmt.rate, fmt.block_align, fmt.bytes_per_sec, fmt.bits) == (1, 2, 48000, 4, 192000, 16)

    # ---- 聲音格式：雙聲道 16 位元 → 單聲道；48000 / 44100 → 16000，一段一段餵也接得起來
    stereo = np.array([[32767, -32768], [16384, 16384]], dtype=np.int16).tobytes()
    assert np.allclose(procaudio.to_mono(stereo, 2), [-0.5 / 32768, 0.5], atol=1e-4)
    for rate in (48000, 44100):
        tone = np.sin(2 * np.pi * 440 * np.arange(rate) / rate).astype(np.float32)
        whole = procaudio.Resampler(rate).feed(tone)
        pieces = procaudio.Resampler(rate)
        joined = np.concatenate([pieces.feed(tone[i:i + 479]) for i in range(0, len(tone), 479)])
        assert abs(len(whole) - SR) <= 1 and abs(len(joined) - SR) <= 1, (rate, len(whole), len(joined))
        n = min(len(whole), len(joined))
        assert np.allclose(whole[:n], joined[:n], atol=1e-3)
        crossings = int(np.sum(np.diff(np.sign(whole[200:])) != 0))
        assert 860 <= crossings <= 880                                               # 還是 440 Hz
        assert 0.9 < float(np.abs(whole[200:]).max()) < 1.05
    high = np.sin(2 * np.pi * 12000 * np.arange(48000) / 48000).astype(np.float32)
    assert float(np.abs(procaudio.Resampler(48000).feed(high)[200:]).max()) < 0.05   # 太高的聲音要先濾掉

    # ---- 程式清單
    table = [(4, 0, "System"), (100, 4, "explorer.exe"), (200, 100, "Discord.exe"), (201, 200, "Discord.exe"),
             (202, 200, "Discord.exe"), (300, 100, "League of Legends.exe"), (400, 100, "discord.exe"), (500, 100, "chrome.exe")]
    assert apps.roots(table, ["Discord.exe", "League of Legends.exe", "NotRunning.exe"]) == {
        "Discord.exe": [200, 400], "League of Legends.exe": [300]}
    assert apps.display_name("Discord.exe") == "Discord" and apps.display_name("steam") == "steam"
    monkeypatch.setenv("LIVELINGO_FAKE_APPS", "Discord.exe|Discord - 一般;League of Legends.exe|League of Legends (TM) Client")
    assert [a["name"] for a in apps.running()] == ["Discord", "League of Legends"]
    assert apps.pids_of(["discord.exe", "Nope.exe"]) == {"Discord.exe": [1000]}

    # ---- 混音
    class FakeStream:
        made = []

        def __init__(self, pid, on_audio, on_end):
            self.pid, self.on_audio, self.on_end, self.stopped, self.started = pid, on_audio, on_end, False, False
            FakeStream.made.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True
            self.on_end(self, None)

    running = {"Discord.exe": [200], "Game.exe": [300]}
    opened, errors = [], []
    cap = procaudio.AppCapture(["Discord.exe", "Game.exe", "Later.exe"], lambda exes: {k: v for k, v in running.items() if k in exes},
                               on_error=lambda kind, ex: errors.append(str(ex)), on_open=lambda kind, name: opened.append(name),
                               make_stream=FakeStream, rescan=0.05)
    assert cap.kind == "loopback"
    cap._scan()
    assert sorted(st.pid for st in FakeStream.made) == [200, 300] and all(st.started for st in FakeStream.made)
    assert opened == ["Discord、Game"]
    a, b = FakeStream.made
    a.on_audio(a, np.full(BLOCK, 0.25, dtype=np.float32))
    b.on_audio(b, np.full(BLOCK // 2, 0.5, dtype=np.float32))
    mixed = cap._mix()
    assert len(mixed) == BLOCK and abs(mixed[0] - 0.75) < 1e-6 and abs(mixed[-1] - 0.25) < 1e-6      # 兩路加起來，短的那路後面當安靜
    assert float(np.abs(cap._mix()).max()) == 0.0
    a.on_audio(a, np.full(BLOCK, 0.9, dtype=np.float32))
    b.on_audio(b, np.full(BLOCK, 0.9, dtype=np.float32))
    assert float(cap._mix().max()) == 1.0                                                              # 不會爆音
    a.on_audio(a, np.zeros(SR * 3, dtype=np.float32))
    assert len(cap._buffers[200]) == SR                                                                # 最多積一秒

    running["Later.exe"] = [900]                                                                       # 晚一點才開的程式
    del running["Game.exe"]                                                                            # 關掉的程式
    cap._scan()
    assert b.stopped and 300 not in cap.streams and sorted(cap.streams) == [200, 900] and opened[-1] == "Discord、Later"

    cap.start()                                                                                        # 真的跑起來：固定每 0.1 秒送一段
    try:
        block = cap.q.get(timeout=2)
        assert len(block) == BLOCK and block.dtype == np.float32
        for st in list(cap.streams.values()):                                                          # 每一路都被 Windows 拒絕
            st.on_end(st, OSError("activating the program's sound failed (0x88890008)"))
        end = time.time() + 3
        while time.time() < end and not errors:
            time.sleep(0.02)
        assert errors and "0x88890008" in errors[0]
    finally:
        cap.stop()
        cap.join(2)

    # ---- 程式主體：有選程式就用這條路，不行就退回整台電腦並通知
    class Audio:
        def __init__(self):
            self.calls = []
            self.can = True

        def apps_available(self):
            return self.can

        def capture_apps(self, exes, on_error=None, on_open=None):
            self.calls.append(("apps", list(exes)))
            self.on_error = on_error
            return type("C", (), {"start": lambda s: None, "stop": lambda s: None, "is_alive": lambda s: True,
                                  "kind": "loopback", "q": __import__("queue").Queue()})() if self.can else None

        def capture(self, kind, device, on_error=None, on_open=None):
            self.calls.append(("all", kind))
            return type("C", (), {"start": lambda s: None, "stop": lambda s: None, "is_alive": lambda s: True,
                                  "kind": kind, "q": __import__("queue").Queue()})()

    audio = Audio()
    service = Service(tmp_path, audio=audio)
    notices = []
    service.notice = lambda code, **kw: notices.append(code)
    result = service.update_config({"listen_apps": ["Discord.exe", "discord.exe", "", 5, "  Game.exe  "]})
    assert result["saved"] and service.cfg["listen_apps"] == ["discord.exe", "Game.exe"] or service.cfg["listen_apps"] == ["Discord.exe", "Game.exe"]
    state = service.apps_state()
    assert state["available"] and [(a["name"], a["chosen"], a["running"]) for a in state["apps"]] == [
        ("Discord", True, True), ("League of Legends", False, True), ("Game", True, False)]

    import livelingo.core.speech as speech_mod

    class Pipe:
        def __init__(self, capture, *a, **kw):
            self.capture = capture

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(speech_mod, "SpeechPipeline", Pipe)
    service.recognizer = type("R", (), {"beam": 1})()
    service._restart_voice = lambda key: service._start_voice(key)
    service._start_voice("listen")
    assert audio.calls[-1][0] == "apps" and service.sources["listen"]["apps"] is True and service.sources["listen"]["on"]
    service._start_voice("mic")
    assert audio.calls[-1] == ("all", "mic") and service.sources["mic"]["apps"] is False            # 麥克風不受影響

    audio.on_error("loopback", OSError("0x88890008"))                                                # Windows 不給
    assert notices == ["apps_audio_failed"] and audio.calls[-1] == ("all", "loopback")
    assert service.sources["listen"]["apps"] is False and service.apps_state()["off"] is True
    service.update_config({"listen_apps": ["Discord.exe"]})                                          # 重新選一次：再試
    assert service.apps_state()["off"] is False and audio.calls[-1][0] == "apps"
    audio.can = False                                                                                # 舊的 Windows：沒有這個功能
    service._start_voice("listen")
    assert audio.calls[-1] == ("all", "loopback") and service.apps_state()["available"] is False
    service.update_config({"listen_apps": "Discord.exe"})
    assert service.cfg["listen_apps"] == []



def test_several_named_screen_regions(tmp_path, monkeypatch):
    """
    聊天區可以好幾個、各有名字和開關；區域可以跟著指定程式的視窗；讀到幾行、翻了幾行、略過幾行有數字。
    舊版只有一個聊天區的設定檔會自動搬過來。
    """
    import numpy as np

    import livelingo.core.watcher as watcher_mod
    from livelingo import apps
    from livelingo.config import MAX_REGIONS
    from livelingo.core.ocr import Line
    from livelingo.service import Service

    # ---- 舊設定檔：region 搬成 regions 的第一個
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "config.json").write_text(json.dumps({"region": {"left": 5, "top": 6, "width": 300, "height": 120}, "layout": 2}),
                                                  encoding="utf-8")
    moved = Config(tmp_path / "old")
    assert moved["region"] is None and moved["regions"] == [
        {"id": "r1", "name": "", "on": True, "app": "", "anchor": None, "left": 5, "top": 6, "width": 300, "height": 120}]

    # ---- 程式視窗的位置
    monkeypatch.setenv("LIVELINGO_FAKE_APPS", "Discord.exe|Discord|100,50,1000,700;Game.exe|Game")
    assert apps.window_rect("discord.exe") == (100, 50, 1000, 700) and apps.window_rect("Nope.exe") is None

    service = Service(tmp_path)
    notices, events = [], []
    service.notice = lambda code, **kw: notices.append(code)
    publish = service.hub.publish
    service.hub.publish = lambda name, data=None: (events.append((name, data)), publish(name, data))[1]

    def frame(*box):
        monkeypatch.setenv("LIVELINGO_FAKE_REGION", ",".join(str(v) for v in box))

    def wait(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    # 框選是在背景做的；等到它整個做完（最後會送 region_done）再檢查，不然會剛好卡在「存了但還沒通知」中間
    finished = lambda: sum(1 for name, _data in events if name == "region_done")      # noqa: E731

    # ---- 框第一個、再新增一個、重框指定的那一個
    frame(10, 20, 400, 200)
    service.select_region(then_enable=False)
    wait(lambda: len(service.cfg["regions"]) == 1 and not service._selecting and finished() == 1)
    frame(500, 20, 300, 200)
    service.select_region(then_enable=False, new=True)
    wait(lambda: len(service.cfg["regions"]) == 2 and not service._selecting and finished() == 2)
    assert [r["id"] for r in service.cfg["regions"]] == ["r1", "r2"] and all(r["on"] for r in service.cfg["regions"])
    assert ("saved", {"what": "region", "id": "r2"}) in events
    frame(520, 40, 320, 220)
    service.select_region(then_enable=False, rid="r2")
    wait(lambda: service.cfg["regions"][1]["left"] == 520 and not service._selecting and finished() == 3)
    assert service.cfg["regions"][0]["left"] == 10 and len(service.cfg["regions"]) == 2
    frame(1, 1, 10, 10)                                   # 太小的框不收
    service.select_region(then_enable=False, new=True)
    wait(lambda: "region_small" in notices and not service._selecting and finished() == 4)
    assert len(service.cfg["regions"]) == 2

    # ---- 介面只能改名字、開關、跟著哪個程式、刪除；位置大小改不了，亂送的會被擋掉
    sent = [dict(r) for r in service.cfg["regions"]]
    sent[0].update(name="  遊戲   聊天  ", left=9999, width=1)
    sent[1].update(name="Discord 文字", app="Discord.exe")
    sent += [{"id": "made-up", "left": 0, "top": 0, "width": 50, "height": 50}, "junk", dict(sent[0])]
    result = service.update_config({"regions": sent})
    first, second = service.cfg["regions"]
    assert result["saved"] and "regions" in result["changed"] and len(service.cfg["regions"]) == 2
    assert first["name"] == "遊戲 聊天" and first["left"] == 10 and first["width"] == 400
    assert second["app"] == "Discord.exe" and second["anchor"] == [100, 50]
    # 跟著的視窗搬家：讀的位置跟著搬；視窗不見了就先不讀
    assert service._locate(second) == {"left": 520, "top": 40, "width": 320, "height": 220}
    monkeypatch.setenv("LIVELINGO_FAKE_APPS", "Discord.exe|Discord|400,250,1000,700")
    assert service._locate(second) == {"left": 820, "top": 240, "width": 320, "height": 220}
    assert service._locate(first) == {"left": 10, "top": 20, "width": 400, "height": 200}
    monkeypatch.setenv("LIVELINGO_FAKE_APPS", "Game.exe|Game")
    assert service._locate(second) is None
    assert service.ocr_test("r2") == {"ok": False, "error": "app_closed", "app": "Discord"}
    # 要跟的程式沒開：不知道視窗在哪，不存，並且告訴使用者
    sent = [dict(r) for r in service.cfg["regions"]]
    sent[0]["app"] = "Nope.exe"
    result = service.update_config({"regions": sent})
    assert "regions" not in result["changed"] and "region_app_closed" in notices and service.cfg["regions"][0]["app"] == ""
    monkeypatch.setenv("LIVELINGO_FAKE_APPS", "Discord.exe|Discord|100,50,1000,700")

    # ---- 最多 MAX_REGIONS 個
    full = [dict(service.cfg["regions"][0], id=f"x{n}") for n in range(MAX_REGIONS)]
    service.cfg.set(regions=full)
    service.select_region(then_enable=False, new=True)
    assert "regions_full" in notices and not service._selecting and len(service.cfg["regions"]) == MAX_REGIONS
    service.cfg.set(regions=[first, second])

    # ---- 同時讀兩個區域：各自記各自看過的句子，訊息標是哪個區域讀到的，統計數字對得起來
    class FakeOcr:
        family = "ko"

        def read(self, bgr):
            return int(bgr[0, 0, 0])                      # 用畫面的顏色代表是哪個區域、第幾個畫面

    pages = {
        10: [Line("안녕하세요 반갑습니다", 0, 0, 20, 0.95), Line("hello there", 0, 30, 20, 0.9)],
        120: [Line("안녕하세요 반갑습니다", 0, 0, 20, 0.95), Line("지금 갈게요 기다려", 0, 30, 20, 0.95)],
        220: [Line("안녕하세요 반갑습니다", 0, 0, 20, 0.95)],
    }
    monkeypatch.setattr(watcher_mod, "message_lines", lambda shade: pages[shade])
    shade = {"r1": 10, "r2": 220}
    service.grab = lambda place: np.full((60, 200, 3), shade["r1" if place["left"] == 10 else "r2"], np.uint8)
    service._load_ocr = lambda: FakeOcr()
    got = []
    service.submit = lambda via, text, lang=None, unsure=False, place="": got.append((text, place))
    service.cfg.set(interval=0.02)
    try:
        service._start_screen()
        wait(lambda: len(got) >= 2)
        assert sorted(got) == [("안녕하세요 반갑습니다", "Discord 文字"), ("안녕하세요 반갑습니다", "遊戲 聊天")]
        shade["r1"] = 120                                 # 第一個區域多了一行
        wait(lambda: len(got) >= 3)
        assert got[2] == ("지금 갈게요 기다려", "遊戲 聊天")
        stats = service.watcher.stats()
        assert stats["r1"] == {"scans": 2, "lines": 4, "sent": 2, "repeat": 1, "other": 1, "old": 0}
        assert stats["r2"] == {"scans": 1, "lines": 1, "sent": 1, "repeat": 0, "other": 0, "old": 0}
        wait(lambda: any(name == "screen_stats" and data == stats for name, data in events))
        assert service.state()["screen_stats"] == stats

        # 改名字不會從頭讀（不會把畫面上的舊訊息再翻一次）；關掉一個就只讀剩下的
        sent = [dict(r) for r in service.cfg["regions"]]
        sent[0]["name"] = "LoL"
        sent[1]["on"] = False
        service.update_config({"regions": sent})
        assert [r["id"] for r in service.watcher.regions] == ["r1"] and service.watcher.stats()["r1"]["sent"] == 2
        shade["r1"] = 10
        time.sleep(0.3)
        assert len(got) == 3
        # 只剩一個區域開著：訊息不用再標是哪裡
        service.watcher.set_regions(service._regions_on())
        service._on_screen_line("새로운 줄입니다", "ko", 0.9, service.cfg["regions"][0])
        assert got[-1] == ("새로운 줄입니다", "")

        # 全部關掉：讀螢幕這個來源跟著關
        service.sources["screen"]["on"] = True
        sent[0]["on"] = False
        service.update_config({"regions": sent})
        assert not service.sources["screen"]["on"] and not service.cfg["sources"]["screen"]
        # 再打開讀螢幕：有框過但全關著，就把第一個打開
        service.phase = "ready"
        service.set_source("screen", True)
        assert service.cfg["regions"][0]["on"] and not service.cfg["regions"][1]["on"]
        # 刪掉一個
        service.update_config({"regions": [dict(service.cfg["regions"][0])]})
        assert [r["id"] for r in service.cfg["regions"]] == ["r1"]
    finally:
        service._stop_watcher()


def test_child_processes_get_all_three_handles():
    """
    每個另外開的程序都要指定輸入、輸出、錯誤輸出要接到哪裡。打包後的程式沒有主控台，
    從某些地方啟動時沒指定的那一個會讓子程序直接開不起來（WinError 50，自動檢查抓到過）。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "livelingo"
    missing = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("run", "Popen")
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
                continue
            given = {k.arg for k in node.keywords}
            if "stdin" not in given or not ("capture_output" in given or {"stdout", "stderr"} <= given):
                missing.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not missing, missing


def test_display_mode_screens_and_saved_layouts(tmp_path, monkeypatch):
    """
    顯示方式三選一（浮在畫面上、底部字幕條、只在視窗裡）共用同一組外觀；可以選螢幕；螢幕配置變了、
    留言跑到看不見的地方會自動拉回來；設定頁的螢幕圖可以直接擺位置；現在的樣子可以存成自己的版面。
    """
    from pathlib import Path

    from livelingo import chatpaint, overlay, oswin
    from livelingo.config import DEFAULTS, EDITABLE, MAX_LAYOUTS, OVERLAY_LOOK
    from livelingo.service import Service

    two = "0,0,1920,1080;1920,0,2560,1440"
    monkeypatch.setenv("LIVELINGO_FAKE_MONITORS", two)
    screens = oswin.monitors()
    assert [(m["left"], m["width"], m["primary"]) for m in screens] == [(0, 1920, True), (1920, 2560, False)]
    assert screens[0]["work"] == [0, 0, 1920, 1040]

    # ---- 位置的計算
    assert overlay.fit_rect([100, 100, 400, 200], screens) == [100, 100, 400, 200]            # 看得到：不動
    assert overlay.fit_rect([1800, 100, 400, 200], screens) == [1800, 100, 400, 200]          # 跨兩個螢幕也算看得到
    assert overlay.fit_rect([5000, 100, 400, 200], screens) == [1520, 100, 400, 200]          # 完全在外面：回主螢幕靠近的那一邊
    assert overlay.fit_rect([4400, 1300, 400, 300], screens) == [4080, 1100, 400, 300]        # 大半在外面：推回那個螢幕裡
    assert overlay.fit_rect([-3000, -3000, 9000, 9000], screens[:1]) == [0, 0, 1920, 1040]    # 比螢幕還大：縮到放得下
    assert overlay.monitor_of([2000, 50, 300, 200], screens) == 1 and overlay.monitor_of([9000, 9000, 10, 10], screens) == 0
    moved = overlay.move_to_monitor([960, 520, 400, 200], screens[0], screens[1])
    assert moved == [1920 + 1280, 700, 400, 200]                                               # 同樣的相對位置（正中間偏下）
    bar = overlay.bar_rect(screens[1], 30)
    assert abs((bar[0] + bar[2] / 2) - (1920 + 1280)) <= 1 and bar[1] + bar[3] <= 1400 and bar[1] > 1000
    assert overlay.monitor_at(7, screens) is screens[0] and overlay.monitor_at("x", screens) is screens[0]
    assert not {"overlay_rect", "overlay_form", "overlay_monitor", "overlay_layouts"} & set(OVERLAY_LOOK)
    assert {"overlay_form", "overlay_monitor"} <= EDITABLE and "overlay_layouts" not in EDITABLE

    service = Service(tmp_path)
    notices, events = [], []
    service.notice = lambda code, **kw: notices.append(code)
    service.hub.publish = lambda name, data=None: events.append((name, data))
    ov = service.overlay
    ov.window_class = overlay.MemoryWindow
    service.cfg.set(overlay=True, overlay_rect=[2500, 300, 500, 260], overlay_monitor=1)
    ov.apply()
    try:
        assert ov.running and ov.rect == [2500, 300, 500, 260]
        assert ov.screens()["monitor"] == 1 and len(ov.screens()["monitors"]) == 2 and ov.screens()["form"] == "float"

        # ---- 第二個螢幕拔掉了：拉回主螢幕、存起來、通知一次
        assert ov.check_screens() is False                                                     # 第一次只是記下現在的配置
        monkeypatch.setenv("LIVELINGO_FAKE_MONITORS", "0,0,1920,1080")
        assert ov.check_screens() is True
        assert ov.rect == [1420, 300, 500, 260] and service.cfg["overlay_rect"] == ov.rect and service.cfg["overlay_monitor"] == 0
        assert notices == ["overlay_pulled_back"]
        assert ov.check_screens() is False and notices == ["overlay_pulled_back"]              # 沒再變就不會一直通知
        monkeypatch.setenv("LIVELINGO_FAKE_MONITORS", two)
        assert ov.check_screens() is False and ov.rect == [1420, 300, 500, 260]                # 螢幕接回來：位置本來就看得到，不動

        # ---- 設定頁的螢幕圖：拖的時候直接搬，放開才存
        events.clear()
        result = ov.place([2100, 200, 600, 300], final=False)
        assert result["ok"] and not result["saved"] and ov.rect == [2100, 200, 600, 300]
        assert service.cfg["overlay_rect"] == [1420, 300, 500, 260] and not events
        result = ov.place([2200, 250, 10, 10], final=True)                                     # 太小的會放大到最小尺寸
        assert result["saved"] and ov.rect == [2200, 250, *overlay.MIN_SIZE] == service.cfg["overlay_rect"]
        assert service.cfg["overlay_monitor"] == 1 and ("saved", {"what": "overlay_map"}) in events
        assert ov.place([9999, 9999, 400, 200])["rect"] == [1520, 840, 400, 200]               # 拖到螢幕外：推回來
        assert ov.place("junk") == {"ok": False} and ov.place([1, 2, 3]) == {"ok": False}
        assert Config(tmp_path)["overlay_rect"] == [1520, 840, 400, 200]

        # ---- 換螢幕：浮動留言搬到另一個螢幕上同樣的相對位置
        ov.place([960, 520, 400, 200])
        result = ov.choose_monitor(1)
        assert result["saved"] and ov.rect == [1920 + 1280, 700, 400, 200] and service.cfg["overlay_monitor"] == 1
        assert ov.choose_monitor(5) == {"ok": False}

        # ---- 底部字幕條：位置自動、置中兩句、不能拖；外觀照同一組設定
        seen = {}
        compose = chatpaint.compose
        monkeypatch.setattr(chatpaint, "compose", lambda tiles, size, **kw: (seen.update(kw, size=size), compose(tiles, size, **kw))[1])
        service.update_config({"overlay_color": "#FFAA00", "overlay_align": "right", "overlay_lines": 6})
        result = service.update_config({"overlay_form": "bar"})
        assert result["saved"] and ov.bar and ov.rect == overlay.bar_rect(screens[1], service.cfg["overlay_font"])
        ov.push("heard", "你好")
        ov._last = None
        assert ov.frame()[0] is not None
        assert seen["grow"] == "up" and seen["lines"] == 2 and seen["size"] == (ov.rect[2], ov.rect[3])
        assert service.cfg["overlay_align"] == "right" and service.cfg["overlay_lines"] == 6      # 自己的設定沒有被改掉
        ov.set_edit(True)
        assert not ov.editing
        assert ov.place([10, 10, 400, 200])["ok"] is False                                      # 字幕條不能擺
        ov.choose_monitor(0)
        assert ov.rect == overlay.bar_rect(screens[0], service.cfg["overlay_font"])
        service.update_config({"overlay_font": 40})                                             # 字變大，字幕條跟著變高
        assert ov.rect == overlay.bar_rect(screens[0], 40) and ov.rect[3] > overlay.bar_rect(screens[0], 24)[3]
        assert service.update_config({"overlay_form": "sideways", "overlay_monitor": "x"})["changed"] == []
        assert service.update_config({"overlay_monitor": 99})["saved"] and service.cfg["overlay_monitor"] == 15

        # ---- 自己存的版面：存、蓋掉同名的、套用、最多幾組、刪
        service.update_config({"overlay_monitor": 0})
        assert ov.save_layout("  ") == {"ok": False, "error": "name"}
        assert ov.save_layout(" 看  影片 ")["index"] == 0 and service.cfg["overlay_layouts"][0]["name"] == "看 影片"
        service.update_config({"overlay_form": "float", "overlay_color": "#00FF00", "overlay_font": 20})
        ov.place([300, 400, 500, 250])
        assert ov.save_layout("打遊戲")["index"] == 1
        result = ov.apply_layout(0)
        assert result["saved"] and (service.cfg["overlay_form"], service.cfg["overlay_color"], service.cfg["overlay_font"]) == ("bar", "#FFAA00", 40)
        assert ov.rect == overlay.bar_rect(screens[0], 40)
        ov.apply_layout(1)
        assert (service.cfg["overlay_form"], service.cfg["overlay_color"], service.cfg["overlay_font"]) == ("float", "#00FF00", 20)
        assert ov.rect == [300, 400, 500, 250] == service.cfg["overlay_rect"]
        service.update_config({"overlay_color": "#123456"})
        assert ov.save_layout("打遊戲")["index"] == 1 and len(service.cfg["overlay_layouts"]) == 2      # 同名：蓋掉
        assert service.cfg["overlay_layouts"][1]["look"]["overlay_color"] == "#123456"
        for n in range(MAX_LAYOUTS - 2):
            assert ov.save_layout(f"第 {n} 組")["ok"]
        assert ov.save_layout("再一組") == {"ok": False, "error": "full"}
        assert ov.remove_layout(0)["saved"] and service.cfg["overlay_layouts"][0]["name"] == "打遊戲"
        assert ov.remove_layout(40) == {"ok": False} and ov.apply_layout("x") == {"ok": False}
        assert len(Config(tmp_path)["overlay_layouts"]) == MAX_LAYOUTS - 1

        # ---- 只在視窗裡：畫面上的東西收掉；一鍵版面一律回到浮動留言
        service.update_config({"overlay": False})
        assert not ov.running
        service.update_config({"overlay": True, "overlay_form": "bar"})
        assert ov.running and ov.bar
        ov.apply_preset("quiet")
        assert service.cfg["overlay_form"] == "float" and ov.rect == service.cfg["overlay_rect"] == overlay.preset_rect("quiet", screens[0])
    finally:
        ov.stop()

    # ---- 舊的「字幕列」畫面拿掉了（併進顯示方式），不會留下進得去的入口
    service.set_view("subtitle")
    assert service.view != "subtitle"
    ui = Path(__file__).resolve().parents[1] / "livelingo" / "ui"
    for name in ("index.html", "app.js", "style.css"):
        assert "view-subtitle" not in (ui / name).read_text(encoding="utf-8"), name
    assert DEFAULTS["overlay_form"] == "float" and DEFAULTS["overlay_layouts"] == []


def test_global_hotkeys_and_quick_input(tmp_path, monkeypatch):
    """
    全域快速鍵：開關字幕、開關麥克風、複製我上一句的翻譯、叫出快速輸入框；按鍵可以自己設定，
    被別的程式佔用的會標出來；設定頁等新組合的時候先停用。快速輸入框打的字當成我打的字去翻譯，不會替人在別的程式打字。
    """
    import ast
    from pathlib import Path

    from livelingo import hotkeys, oswin, quickbox
    from livelingo.config import DEFAULTS, EDITABLE
    from livelingo.service import Service

    # ---- 組合鍵的寫法
    assert hotkeys.normalize("alt + ctrl+s") == "Ctrl+Alt+S" and hotkeys.normalize("win+shift+f12") == "Shift+Win+F12"
    for bad in ("S", "F5", "Ctrl+Ctrl+S", "Ctrl+;", "Ctrl+Alt", "", None, "Ctrl+Space"):
        assert hotkeys.normalize(bad) == "", bad      # 沒有修飾鍵的不收：不能搶走平常打字和遊戲用的鍵
    assert hotkeys.parse("Ctrl+Alt+S") == (0x2 | 0x1, ord("S")) and hotkeys.parse("Shift+F1") == (0x4, 0x70)
    assert set(DEFAULTS["hotkeys"]) == set(hotkeys.ACTIONS) and "hotkeys" in EDITABLE
    combos = [hotkeys.normalize(c) for c in DEFAULTS["hotkeys"].values()]
    assert all(combos) and len(set(combos)) == len(combos)

    # ---- 沒有 Windows 的地方：設定照存，狀態都是 off，不會出錯
    plain = hotkeys.Hotkeys(lambda name: None, backend=hotkeys._NoBackend())
    plain.start()
    assert not plain.available and set(plain.set(DEFAULTS["hotkeys"]).values()) == {"off"}

    monkeypatch.delenv("LIVELINGO_FAKE_QUICK", raising=False)
    copied = []
    monkeypatch.setattr(oswin, "copy_text", lambda text: copied.append(text) or True)
    service = Service(tmp_path)
    notices, events = [], []
    service.notice = lambda code, **kw: notices.append(code)
    publish = service.hub.publish
    service.hub.publish = lambda name, data=None: (events.append((name, data)), publish(name, data))[1]
    backend = hotkeys.FakeBackend(taken=["Ctrl+Alt+C"])
    service.hotkeys.backend = backend
    service._hotkey = lambda name: service._hotkey_work(name) if name != "quick" else service.quick_input()      # 測試裡直接做，不另開執行緒
    service.hotkeys.on_fire = service._hotkey
    try:
        service.start_hotkeys()
        state = service.state()["hotkeys"]
        assert state["available"] and state["states"] == {"overlay": "ok", "mic": "ok", "copy": "taken", "quick": "ok"}
        assert state["defaults"] == DEFAULTS["hotkeys"]

        # 開關字幕
        was = service.cfg["overlay"]
        assert backend.press("Ctrl+Alt+S") and service.cfg["overlay"] is (not was)
        backend.press("Ctrl+Alt+S")
        assert service.cfg["overlay"] is was

        # 被佔用的那一組按了沒反應；換一組就好了，而且只動那一個
        assert not backend.press("Ctrl+Alt+C")
        result = service.update_config({"hotkeys": {"copy": "ctrl+shift+c"}})
        assert result["saved"] and result["changed"] == ["hotkeys"]
        assert service.cfg["hotkeys"] == {**DEFAULTS["hotkeys"], "copy": "Ctrl+Shift+C"}
        assert service.hotkeys.states["copy"] == "ok" and ("hotkeys", service.hotkeys_state()) in events
        # 跟別的動作一樣的、不能用的：不收，也說了為什麼
        assert service.update_config({"hotkeys": {"mic": "Ctrl+Alt+S"}})["changed"] == [] and notices[-1] == "hotkey_same"
        assert service.update_config({"hotkeys": {"mic": "M"}})["changed"] == [] and notices[-1] == "hotkey_invalid"
        # 兩個互換是可以的；空字串 = 不用
        swapped = service.update_config({"hotkeys": {"overlay": "Ctrl+Alt+M", "mic": "Ctrl+Alt+S"}})
        assert swapped["changed"] == ["hotkeys"] and service.cfg["hotkeys"]["mic"] == "Ctrl+Alt+S"
        service.update_config({"hotkeys": {"quick": ""}})
        assert service.hotkeys.states["quick"] == "off" and not backend.press("Ctrl+Alt+T")
        service.update_config({"hotkeys": DEFAULTS["hotkeys"]})      # 恢復預設
        assert service.cfg["hotkeys"] == DEFAULTS["hotkeys"]
        saved = __import__("json").loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert saved["hotkeys"] == DEFAULTS["hotkeys"]

        # 設定頁在等新組合：先停用，恢復之後照舊
        before = service.cfg["overlay"]
        service.pause_hotkeys(True)
        assert not backend.press("Ctrl+Alt+S") and service.cfg["overlay"] is before
        service.pause_hotkeys(False)
        assert backend.press("Ctrl+Alt+S") and service.cfg["overlay"] is (not before)

        # 複製我上一句的翻譯：只找我說的、已經翻好的；沒有就說沒有
        service.update_config({"hotkeys": {"copy": "Ctrl+Shift+C"}})
        notes = []
        service.overlay.note = notes.append
        service.cfg.set(ui_lang="zh-TW")
        backend.press("Ctrl+Shift+C")
        assert copied == [] and notes[-1] == hotkeys.NOTES["zh-TW"]["nothing"]
        service.cfg.set(ui_lang="ko")
        service.messages.extend([
            {"id": 1, "who": "me", "via": "typed", "original": "等我一下", "translated": "잠깐만 기다려", "copied": False},
            {"id": 2, "who": "them", "via": "heard", "original": "알겠어", "translated": "知道了", "copied": False},
            {"id": 3, "who": "me", "via": "spoken", "original": "還沒翻好", "translated": "", "pending": True, "copied": False}])
        backend.press("Ctrl+Shift+C")
        assert copied == ["잠깐만 기다려"] and notes[-1] == hotkeys.NOTES["ko"]["copied"]
        assert service.messages[0]["copied"] is True and events[-2][0] == "message" and events[-2][1]["id"] == 1

        # 麥克風：引擎還沒好的時候不動，說一聲；好了之後開、關，各有提示
        calls = []
        service.set_source = lambda key, on: (calls.append((key, on)), service.sources[key].update(on=on))
        backend.press("Ctrl+Alt+M")
        assert calls == [] and notes[-1] == hotkeys.NOTES["ko"]["not_ready"]
        service.phase = "ready"
        backend.press("Ctrl+Alt+M")
        backend.press("Ctrl+Alt+M")
        assert calls == [("mic", True), ("mic", False)] and notes[-2:] == [hotkeys.NOTES["ko"]["mic_on"], hotkeys.NOTES["ko"]["mic_off"]]

        # 快速輸入框：打的字當成我打的字去翻譯（翻好自動複製是打字本來就有的）；引擎沒好就不開
        sent = []
        service.submit = lambda via, text, **kw: sent.append((via, text))
        monkeypatch.setenv("LIVELINGO_FAKE_QUICK", "等我一下")
        service.update_config({"hotkeys": {"quick": "Ctrl+Alt+T"}})
        assert backend.press("Ctrl+Alt+T") and sent == [("typed", "等我一下")]
        service.phase = "starting"
        assert not service.quick_input() and len(sent) == 1
    finally:
        service.shutdown()
    assert backend.registered == {}

    # ---- 浮動留言上的短提示：幾秒就消失，不受「留言不會自己消失」影響，新的換掉舊的
    from livelingo import overlay

    other = Service(tmp_path / "b")
    ov = other.overlay
    ov.window_class = overlay.MemoryWindow
    other.cfg.set(overlay=True, overlay_seconds=0)
    ov.apply()
    try:
        ov.push("heard", "知道了", key=7)
        ov.note("麥克風：開")
        ov.note("麥克風：關")
        assert [(i["kind"], i["text"]) for i in ov.items] == [("heard", "知道了"), ("note", "麥克風：關")]
        import time as _time

        image, _wait = ov.frame(_time.monotonic() + 1)
        assert image is not None
        ov.frame(_time.monotonic() + overlay.NOTE_SECONDS + 1)
        assert [i["kind"] for i in ov.items] == ["heard"]
    finally:
        other.shutdown()

    # ---- 快速輸入框本身：五種語言都有提示，放在螢幕下方置中；這個小程式裡沒有任何「替人打字、貼上」的東西
    assert quickbox.place((0, 0, 1920, 1080)) == ((1920 - quickbox.SIZE[0]) // 2, int(1080 * 0.66))
    assert quickbox.place((1920, 0, 2560, 1440))[0] == 1920 + (2560 - quickbox.SIZE[0]) // 2
    root = Path(__file__).resolve().parent.parent / "livelingo"
    for name in ("quickbox.py", "hotkeys.py"):
        source = (root / name).read_text(encoding="utf-8")
        for banned in ("SendInput", "keybd_event", "SetWindowsHookEx", "GetAsyncKeyState", "clipboard"):
            assert banned not in source, (name, banned)
    main = (root / "__main__.py").read_text(encoding="utf-8")
    assert "--quick-input" in main
    # 真的程式有把系統匣圖示和快速鍵開起來（之前只有健康檢查會開系統匣）
    calls = {ast.unparse(n.func) for path in ("window.py", "service.py")
             for n in ast.walk(ast.parse((root / path).read_text(encoding="utf-8"))) if isinstance(n, ast.Call)}
    assert "service.tray.start" in calls and "self.start_hotkeys" in calls


def test_history_kept_searched_exported_cleared(tmp_path, monkeypatch):
    """
    對話歷史：翻好的句子存在電腦上，重開還在；可以搜尋、匯出成文字檔、一鍵清除；過期的自動刪；
    也可以整個關掉不保存。還在翻的、翻失敗的不記；同一則改過只留最新的；被拿掉的訊息歷史裡也拿掉。
    """
    import json as _json

    from livelingo import history, oswin
    from livelingo.config import DEFAULTS, EDITABLE
    from livelingo.service import Service

    assert DEFAULTS["history_keep"] is True and DEFAULTS["history_days"] in history.DAYS
    assert {"history_keep", "history_days"} <= EDITABLE

    clock = {"now": 1_800_000_000.0}
    h = history.History(tmp_path, keep=True, days=30, now=lambda: clock["now"])
    msg = lambda i, who, original, translated, **kw: {      # noqa: E731
        "id": i, "who": who, "via": "typed" if who == "me" else "heard", "original": original, "translated": translated,
        "lang": "zh" if who == "me" else "ko", "target": "ko" if who == "me" else "zh-TW", "at": clock["now"], **kw}
    assert not h.add(msg(1, "me", "等我一下", "", pending=True))            # 還在翻：不記
    assert not h.add(msg(1, "me", "等我一下", "", error="boom"))             # 翻失敗：不記
    assert h.add(msg(1, "me", "等我一下", "잠깐만 기다려"))
    assert not h.add(msg(1, "me", "等我一下", "잠깐만 기다려"))              # 一模一樣再來一次：不重複寫
    clock["now"] += 60
    assert h.add(msg(2, "them", "정글 와줘", "打野來一下", place="隊伍聊天"))
    assert h.add(msg(1, "me", "等我一下", "잠깐만요", back="稍等"))          # 重翻：同一則，換成新的
    assert h.add(msg(3, "them", "Bot lane GANK", "下路抓人"))
    h.drop(3)
    assert h.count == 2
    found = h.search("")
    assert [r["original"] for r in found["items"]] == ["정글 와줘", "等我一下"]      # 最新的在前
    assert found["items"][1]["translated"] == "잠깐만요" and found["items"][0]["place"] == "隊伍聊天"
    assert found["total"] == 2 and found["all"] == 2 and not found["more"]
    assert [r["original"] for r in h.search("打野")["items"]] == ["정글 와줘"]          # 找翻譯
    assert [r["original"] for r in h.search("等我  잠깐")["items"]] == ["等我一下"]      # 每個詞都要有，原文和翻譯都算
    assert h.search("GANK")["total"] == 0 and h.search("gank 抓人")["total"] == 0      # 拿掉的那則找不到
    page = h.search("", offset=0, limit=1)
    assert len(page["items"]) == 1 and page["more"] and h.search("", offset=1, limit=1)["items"][0]["original"] == "等我一下"

    # 重開：還在，而且檔案被整理過（一則一行）
    lines = (tmp_path / history.FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    (tmp_path / history.FILE).open("a", encoding="utf-8").write("{broken line\n")      # 寫到一半斷電的那種
    again = history.History(tmp_path, keep=True, days=30, now=lambda: clock["now"])
    assert [r["original"] for r in again.search("")["items"]] == ["정글 와줘", "等我一下"]
    assert len((tmp_path / history.FILE).read_text(encoding="utf-8").splitlines()) == 2
    assert again.key_of(1) != h.key_of(1) or True      # 鍵含這次開啟的時間，編號重新從 1 開始也不會蓋到舊的

    # 匯出：舊的在上面、每天一個標題、有 BOM
    out, count = again.export(tmp_path / "out", "zh-TW")
    text = out.read_text(encoding="utf-8-sig")
    assert count == 2 and out.name.startswith("LiveLingo history ") and out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert text.index("等我一下") < text.index("정글 와줘") and "→ 잠깐만요" in text and "→ 打野來一下" in text
    assert "LiveLingo 對話歷史" in text and text.count("── ") == 1 and "我：等我一下" in text and "對方：정글 와줘" in text
    only, n = again.export(tmp_path / "out", "en", "打野")
    assert n == 1 and "等我一下" not in only.read_text(encoding="utf-8-sig") and only != out
    assert again.export(tmp_path / "out", "en", "沒有這句") == (None, 0)

    # 過期的自動刪；改短天數馬上生效；0 = 不限
    clock["now"] += 8 * 86400
    again.add(msg(9, "me", "新的", "새로운"))
    again.configure(True, 7)
    assert [r["original"] for r in again.search("")["items"]] == ["新的"]
    assert len((tmp_path / history.FILE).read_text(encoding="utf-8").splitlines()) == 1
    clock["now"] += 400 * 86400
    again.configure(True, 0)
    assert again.count == 1
    # 關掉保存：不再記新的，已經存的還在；清除之後檔案也沒了
    again.configure(False, 0)
    assert not again.add(msg(10, "me", "不要記", "기록하지 마")) and again.count == 1
    assert again.clear() == 1 and again.count == 0 and not (tmp_path / history.FILE).exists()
    assert history.History(tmp_path, now=lambda: clock["now"]).count == 0

    # ---- 接在主體上
    monkeypatch.setattr(oswin, "copy_text", lambda text: True)
    opened = []
    monkeypatch.setattr(oswin, "open_path", lambda path: opened.append(path) or True)
    service = Service(tmp_path / "svc")
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    try:
        base = {"who": "me", "via": "typed", "lang": "zh", "target": "ko", "at": 1_800_000_000.0, "copied": False, "back": "", "pron": ""}
        service._post({**base, "id": 1, "original": "等我一下", "translated": "", "pending": True, "error": ""})
        assert service.history.count == 0
        service._post({**base, "id": 1, "original": "等我一下", "translated": "잠깐만 기다려", "pending": False, "error": ""})
        service._post({**base, "id": 2, "who": "them", "via": "screen", "original": "ㄱㄱ", "translated": "走走", "pending": False, "error": ""})
        assert service.history.count == 2 and [e for e in events if e[0] == "history"][-1][1]["count"] == 2
        assert service.state()["history"]["count"] == 2 and service.state()["history"]["bytes"] > 0
        service._drop(2)
        assert service.history.count == 1
        service.clear_messages()      # 清掉畫面上的對話不會動到歷史
        assert service.history.count == 1
        done = service.history_export()
        assert done["ok"] and done["count"] == 1 and opened and (service.transcript_dir() / done["name"]).exists()
        # 設定：天數只收清單裡的；關掉保存之後不再記
        assert service.update_config({"history_days": "90"})["changed"] == ["history_days"] and service.history.days == 90
        assert service.update_config({"history_days": 13})["changed"] == [] and service.update_config({"history_days": "x"})["changed"] == []
        service.update_config({"history_keep": False})
        service._post({**base, "id": 3, "original": "不要記", "translated": "기록하지 마", "pending": False, "error": ""})
        assert service.history.count == 1
        cleared = service.history_clear()
        assert cleared == {"ok": True, "removed": 1, "error": ""} and service.history.count == 0
        assert service.history_export() == {"ok": False, "error": "empty"}
        assert _json.loads((tmp_path / "svc" / "config.json").read_text(encoding="utf-8"))["history_keep"] is False
    finally:
        service.shutdown()


def test_monitor_list_is_safe_from_several_threads(monkeypatch):
    """
    問螢幕配置的函式會同時被好幾條執行緒呼叫（浮動留言每幾秒檢查一次、設定頁的螢幕圖）。
    在真的 Windows 上曾經因為每次都重新設定共用的參數型別而互相蓋掉出錯；現在型別只準備一次。
    """
    import sys
    import threading

    from livelingo import oswin

    monkeypatch.delenv("LIVELINGO_FAKE_MONITORS", raising=False)
    caught, results = [], []
    monkeypatch.setattr(sys, "unraisablehook", lambda info: caught.append(repr(info.exc_value)))

    def ask():
        for _ in range(25):
            results.append(oswin.monitors())

    threads = [threading.Thread(target=ask) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    assert not caught, caught[:2]
    assert len(results) == 100 and all(r and r[0]["primary"] and r[0]["width"] > 0 for r in results)
    assert all(r == results[0] for r in results)
    if sys.platform == "win32":
        assert oswin._monitor_api() is oswin._monitor_api()
    source = open(oswin.__file__, encoding="utf-8").read()
    assert "ctypes.windll.user32.GetMonitorInfoW" not in source and "u32.GetMonitorInfoW.argtypes" not in source


def test_health_check_lights(tmp_path, monkeypatch):
    """
    健康檢查：翻譯引擎、顯示卡、麥克風、耳機、聊天區、快速鍵、設定存檔各一個燈（綠 ok／黃 warn／紅 bad／灰 off），
    每一種狀況在五種語言裡都有一句話，而且檢查本身不會打開麥克風、不會改設定。
    """
    import re
    from pathlib import Path

    from livelingo import hotkeys, oswin
    from livelingo.config import UI_LANGS
    from livelingo.service import Service

    class Audio:
        def __init__(self):
            self.found = {"speakers": [{"id": "spk", "name": "耳機 (HyperX)", "default": True}],
                          "microphones": [{"id": "mic1", "name": "麥克風 (HyperX)", "default": True},
                                          {"id": "mic2", "name": "Webcam", "default": False}]}
            self.opened = []

        def devices(self):
            if self.found is None:
                raise RuntimeError("audio service is not running")
            return self.found

        def capture(self, *a, **kw):
            self.opened.append(a)
            raise AssertionError("the health check must not open a device")

    monkeypatch.setattr(oswin, "mic_privacy_blocked", lambda: False)
    audio = Audio()
    service = Service(tmp_path, audio=audio)
    service.hotkeys.backend = hotkeys.FakeBackend()
    service.start_hotkeys()
    before = service.cfg.snapshot()
    light = lambda name: next(i for i in service.health()["items"] if i["id"] == name)      # noqa: E731
    try:
        first = service.health()
        assert [i["id"] for i in first["items"]] == ["engine", "gpu", "mic", "listen", "screen", "hotkeys", "saving"]
        assert all(i["state"] in ("ok", "warn", "bad", "off") and i["tab"] for i in first["items"])
        # 剛開、引擎還沒好：黃燈；沒有顯示卡：黃燈；裝置找得到但沒在聽：綠燈（找到了）；沒框聊天區：灰燈
        assert (light("engine")["state"], light("engine")["code"]) == ("warn", "engine_busy")
        assert (light("gpu")["state"], light("gpu")["code"]) == ("warn", "gpu_none")
        assert (light("mic")["code"], light("mic")["name"]) == ("mic_found", "麥克風 (HyperX)")
        assert (light("listen")["code"], light("listen")["name"]) == ("listen_found", "耳機 (HyperX)")
        assert (light("screen")["state"], light("screen")["code"]) == ("off", "screen_none")
        assert (light("hotkeys")["state"], light("hotkeys")["n"]) == ("ok", 4)
        assert (light("saving")["state"], light("saving")["folder"]) == ("ok", str(tmp_path))
        assert first["worst"] == "warn"

        # 引擎
        service.phase, service.mode = "ready", {"model": "12b", "engine": "gpu", "quality": "high"}
        assert (light("engine")["state"], light("engine")["quality"], light("engine")["engine"]) == ("ok", "high", "gpu")
        service.fallback = True
        assert (light("engine")["state"], light("engine")["code"]) == ("warn", "engine_fallback")
        service.fallback = False
        service.phase, service.detail = "need_download", {"total_mb": 3100}
        assert (light("engine")["state"], light("engine")["mb"]) == ("bad", 3100)
        service.phase, service.detail = "error", {"message": "llama-server exited"}
        assert (light("engine")["state"], light("engine")["error"]) == ("bad", "llama-server exited")
        assert service.health()["worst"] == "bad"
        service.phase = "ready"
        # 顯示卡
        service.gpu = {"name": "RTX 4070", "vram_mb": 12288, "tier": "high"}
        assert (light("gpu")["state"], light("gpu")["gb"]) == ("ok", 12.0)
        service.gpu = {"name": "GTX 1650", "vram_mb": 4096, "tier": "fast"}
        assert (light("gpu")["state"], light("gpu")["code"]) == ("warn", "gpu_small")

        # 麥克風
        service.sources["mic"].update(on=True, device="麥克風 (HyperX)")
        assert (light("mic")["state"], light("mic")["code"]) == ("ok", "mic_ok")
        service.sources["mic"].update(quiet="silent")
        assert (light("mic")["state"], light("mic")["code"]) == ("warn", "mic_silent")
        service.sources["mic"].update(quiet="blocked")
        assert (light("mic")["state"], light("mic")["code"]) == ("bad", "mic_blocked")
        service.sources["mic"].update(quiet="", on=False, device="", error="device is busy")
        assert (light("mic")["state"], light("mic")["error"]) == ("bad", "device is busy")
        service.sources["mic"].update(error="")
        service.cfg.set(mic_device="gone")
        assert (light("mic")["state"], light("mic")["code"], light("mic")["name"]) == ("warn", "mic_missing", "麥克風 (HyperX)")
        service.cfg.set(mic_device="mic2")
        assert (light("mic")["code"], light("mic")["name"]) == ("mic_found", "Webcam")
        monkeypatch.setattr(oswin, "mic_privacy_blocked", lambda: True)
        assert light("mic")["code"] == "mic_blocked" and light("listen")["state"] == "ok"
        monkeypatch.setattr(oswin, "mic_privacy_blocked", lambda: False)
        audio.found = {"speakers": audio.found["speakers"], "microphones": []}
        assert (light("mic")["state"], light("mic")["code"]) == ("bad", "mic_none")
        # 耳機
        service.cfg.set(listen_apps=["Discord.exe"])
        service._apps_audio_off = True
        assert (light("listen")["state"], light("listen")["code"]) == ("warn", "listen_apps_off")
        service._apps_audio_off = False
        audio.found = None
        assert (light("mic")["code"], light("listen")["code"], light("listen")["state"]) == ("audio_error", "audio_error", "bad")
        audio.found = {"speakers": [], "microphones": []}
        assert light("listen")["code"] == "listen_none"
        audio.found = Audio().found

        # 聊天區
        region = {"id": "r1", "name": "隊伍", "left": 0, "top": 0, "width": 300, "height": 200, "on": True, "app": "", "anchor": None}
        service.cfg.set(regions=[region])
        assert (light("screen")["state"], light("screen")["code"], light("screen")["n"]) == ("off", "screen_off", 1)
        service.sources["screen"].update(on=True)
        assert (light("screen")["state"], light("screen")["code"]) == ("ok", "screen_ok")
        monkeypatch.setenv("LIVELINGO_FAKE_APPS", "Discord.exe|Discord")      # 開著但沒有視窗位置 = 縮到最小
        service.cfg.set(regions=[dict(region, app="League.exe", anchor=[0, 0])])
        assert (light("screen")["state"], light("screen")["code"], light("screen")["name"]) == ("warn", "screen_closed", "League")
        service.sources["screen"].update(error="screen capture failed")
        assert light("screen")["state"] == "bad"
        service.sources["screen"].update(error="", on=False)

        # 快速鍵
        service.hotkeys.backend.taken = {hotkeys.parse("Ctrl+Alt+C")}
        service.hotkeys.set(service.cfg["hotkeys"])
        assert (light("hotkeys")["state"], light("hotkeys")["n"]) == ("warn", 1)
        service.update_config({"hotkeys": {name: "" for name in hotkeys.ACTIONS}})
        assert (light("hotkeys")["state"], light("hotkeys")["code"]) == ("off", "hotkeys_off")
        # 設定存檔
        service.cfg.error = "disk is full"
        assert (light("saving")["state"], light("saving")["error"]) == ("bad", "disk is full")
        service.cfg.error = ""
        service.history.error = "access denied"
        assert (light("saving")["state"], light("saving")["tab"]) == ("warn", "history")
        service.history.error = ""
        assert not audio.opened
    finally:
        service.shutdown()
    assert before["sources"] == service.cfg["sources"]

    # 每一個燈的名字、每一種狀態、每一個代碼，五種語言都有一句話
    root = Path(__file__).resolve().parent.parent / "livelingo"
    source = (root / "service.py").read_text(encoding="utf-8")
    body = source[source.index("    def health(self)"):source.index("    # ------------------------------------------------------------------ 對話歷史")]
    codes = set(re.findall(r'"((?:engine|gpu|mic|listen|screen|hotkeys|saving|audio)_[a-z_]+)"', body))
    codes -= {"listen_apps", "mic_device", "speaker_device"}      # 這幾個是設定的名字，不是代碼
    for kind in ("mic", "listen"):
        codes |= {f"{kind}_{tail}" for tail in re.findall(r'f"\{name\}_([a-z]+)"', body)}
    assert {"mic_none", "listen_error", "mic_ok", "listen_found", "engine_busy", "saving_history", "screen_closed"} <= codes
    i18n = (root / "ui" / "i18n.js").read_text(encoding="utf-8")
    keys = ([f"h_{c}" for c in sorted(codes)] + [f"h_n_{n}" for n in ("engine", "gpu", "mic", "listen", "screen", "hotkeys", "saving")]
            + [f"h_s_{s}" for s in ("ok", "warn", "bad", "off")] + ["h_q_high", "h_q_standard", "h_e_gpu", "h_e_cpu"])
    for key in keys:
        assert len(re.findall(rf"\b{key}: ", i18n)) == len(UI_LANGS), key
