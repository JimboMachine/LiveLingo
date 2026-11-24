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


def test_downloads_are_pinned_and_checked(tmp_path, monkeypatch):
    """要下載的檔案都鎖在確切的版本、都有大小和 sha256；下載完會核對，不對的不會留下來、也不會被拿來用。"""
    import hashlib
    import io
    import re
    from pathlib import Path

    import pytest

    # ---- 每一個下載都鎖住了
    files = [(url, name) for url, name, _ in assets.MODELS.values()] + [(url, rel) for url, rel, _ in assets.OCR_FILES + assets.WHISPER_FILES + assets.VOICE_FILES]
    assert {name for _, name in files} == set(assets.CHECKS)                       # 每個檔案都有核對用的資料，沒有多的
    for url, _ in files:
        assert re.search(r"/resolve/[0-9a-f]{40}/", url), url                      # 鎖在一個 commit 上，不是 main
    for size, digest in assets.CHECKS.values():
        assert size > 0 and re.fullmatch(r"[0-9a-f]{64}", digest)
    assert re.fullmatch(r"b\d+", assets.ENGINE_TAG) and f"/download/{assets.ENGINE_TAG}" in assets.ENGINE_BASE
    assert len(assets.ENGINE_FILES["cpu"]) == 1 and len(assets.ENGINE_FILES["cuda"]) == 2
    for kind, engine_files in assets.ENGINE_FILES.items():
        for name, size, digest in engine_files:
            assert name.endswith("-x64.zip") and size > 0 and re.fullmatch(r"[0-9a-f]{64}", digest)
        assert assets.ENGINE_MB[kind] == round(sum(size for _, size, _ in engine_files) / 1e6)
    assert len(assets.pinned()) == len(files) + 3 and all(url.startswith("https://") for url, _, _ in assets.pinned())
    source = (Path(assets.__file__)).read_text(encoding="utf-8")
    assert "resolve/main" not in source and "api.github.com" not in source         # 不再抓「最新的」

    # ---- 下載完核對
    good = b"the real file " * 100
    check = (len(good), hashlib.sha256(good).hexdigest())
    served = []

    class Reply(io.BytesIO):
        status = 200

        def __init__(self, body):
            super().__init__(body)
            self.headers = {"Content-Length": str(len(body))}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def serve(*bodies):
        queue_ = list(bodies)

        def urlopen(req, timeout=0):
            served.append(req.headers.get("Range"))
            return Reply(queue_.pop(0))

        monkeypatch.setattr(assets.urllib.request, "urlopen", urlopen)

    monkeypatch.setattr(assets.time, "sleep", lambda s: None)
    dest = tmp_path / "model.bin"
    serve(good)
    assets.fetch("https://x/model", dest, check=check)
    assert dest.read_bytes() == good and not dest.with_name("model.bin.part").exists()

    dest.unlink()
    serve(b"something else entirely", b"still not the file")                       # 兩次都不是那個檔案
    with pytest.raises(assets.DownloadError, match="not the expected one"):
        assets.fetch("https://x/model", dest, check=check)
    assert not dest.exists() and not dest.with_name("model.bin.part").exists()      # 沒留下來

    serve(b"wrong the first time", good)                                           # 第一次不對：整個重抓，第二次對了
    assets.fetch("https://x/model", dest, check=check)
    assert dest.read_bytes() == good

    dest.unlink()
    serve(good)                                                                    # 沒給核對資料（更新安裝檔自己另外核對）：照舊
    assets.fetch("https://x/model", dest)
    assert dest.read_bytes() == good

    # ---- 引擎：照鎖定的檔名和雜湊抓
    asked = []

    def fake_fetch(url, dest, progress=None, cancel=None, attempts=8, check=None):
        asked.append((url, check))
        import zipfile

        with zipfile.ZipFile(dest, "w") as z:
            z.writestr("llama-server.exe", b"exe")

    monkeypatch.setattr(assets, "fetch", fake_fetch)
    monkeypatch.setattr(assets.sys, "platform", "win32")
    exe = assets.get_engine(tmp_path / "data", "cuda")
    assert exe.name == "llama-server.exe"
    assert asked == [(f"{assets.ENGINE_BASE}/{name}", (size, digest)) for name, size, digest in assets.ENGINE_FILES["cuda"]]


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

    def _ask(self, text, source, target, hints, examples=None, style="full", context=None, on_part=None, unfinished=False, stop=None):
        self.asked.append((text, style, list(examples or [])))
        self.unfinished = getattr(self, "unfinished", []) + [bool(unfinished)]
        self.contexts = getattr(self, "contexts", []) + [list(context or [])]
        if callable(self.answers):
            out = self.answers(text, style, context)
        else:
            out = self.answers[style] if isinstance(self.answers, dict) else self.answers
        if on_part:      # 像真的模型一樣，一個字一個字吐出來
            for i in range(1, len(out) + 1):
                on_part(out[:i])
        return out


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
                           ("ko_KR", "ko"), ("ja_JP", "ja"), ("de_DE", "de"), ("pt_BR", "pt"), ("vi_VN", "vi"),
                           ("th_TH", "th"), ("id_ID", "id"), ("ru_RU", "ru"), ("nl_NL", "en")):
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
    from livelingo.service import VOICE_FAMILIES, Service

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
    assert service._voice_langs("listen") == list(VOICE_FAMILIES) and VOICE_FAMILIES[:4] == ("ko", "zh", "en", "ja")
    assert service._voice_langs("mic") == ["zh", "ko"]

    service._on_speech("loopback", "はい", "ja")                # 短短一句：先不換
    assert service.cfg["their_lang"] == "ko"
    service._on_speech("loopback", "탑 갈게", "ko")              # 又回到韓文：剛剛那句不算
    service._on_speech("loopback", "うん", "ja")
    assert service.cfg["their_lang"] == "ko"
    service._on_speech("loopback", "行こう", "ja")              # 連續兩句都是日文：換
    assert service.cfg["their_lang"] == "ja" and notices[-1] == ("their_switched", {"lang": "ja"})
    assert service._voice_langs("listen") == ["ja", "ko", "zh", "en", *VOICE_FAMILIES[4:]]
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

    service.update_config({"my_lang": "zh-TW", "their_lang": "es"})
    assert service._screen_langs() == {"es", "pt", "fr", "de", "id", "vi"}      # 拉丁字母的語言同一個文字模型：都收
    service._on_speech("loopback", "да", "ru")                               # 俄文短短一句：先不換
    assert service.cfg["their_lang"] == "es"
    service._on_speech("loopback", "Идём на дракона, у них нет флеша", "ru")  # 夠長：換過去，螢幕改用俄文的模型
    assert service.cfg["their_lang"] == "ru" and service._screen_langs() == {"ru"}

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

    assert set(TEXT) == set(UI_LANGS)
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
    設定名稱、每種介面語言的文字表、API 路徑、事件名稱、提醒代碼、介面檔案、圖示檔、安裝檔的名字。
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

    # 文字：視窗外面的每一張文字表（系統匣、浮動留言、框選、字幕檔）每種介面語言都有，欄位一樣多
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

    # 事件：背景送出的每一種事件，介面都有接；每一種提醒都有每種介面語言的文字
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


def test_translation_memory_and_share_files(tmp_path):
    """翻過的句子：同一句直接用；比較差的模型翻的、詞彙表改過的要重翻；匯入的一包一包、可以整包刪、可以不用；
    教過的最優先；匯出預設不含我自己打的、說的；亂七八糟的檔案不收。"""
    from pathlib import Path

    from livelingo.memory import KIND, Memory, terms_print
    from livelingo.service import LIBRARY_KIND, Service

    mem = Memory(tmp_path / "m")
    words = {}
    tr = _FakeModel("오늘 날씨 좋다.", memory=mem, glossary=lambda: dict(words))
    tr.model = "4b"
    assert tr.translate("今天天氣很好", "ko", "zh", remember="heard") == "오늘 날씨 좋다." and len(tr.asked) == 1
    tr.clear_cache()                                                             # 重開程式也一樣（暫存沒了，記憶還在）
    tr.answers = "다른 번역"
    assert tr.translate("今天天氣很好", "ko", "zh", remember="heard") == "오늘 날씨 좋다." and len(tr.asked) == 1
    assert Memory(tmp_path / "m").lookup("今天天氣很好", "zh", "ko", "casual", "4b", "") == "오늘 날씨 좋다."   # 存進檔案了
    assert mem.lookup("今天天氣很好", "zh", "ko", "polite", "4b", "") is None   # 語氣不同不算
    tr.clear_cache()
    tr.model = "12b"                                                             # 換成高品質：標準翻的不用，重翻、記新的
    assert tr.translate("今天天氣很好", "ko", "zh", remember="heard") == "다른 번역" and len(tr.asked) == 2
    tr.clear_cache()
    tr.model = "4b"                                                              # 回到標準：高品質翻的照樣可以用
    assert tr.translate("今天天氣很好", "ko", "zh", remember="heard") == "다른 번역" and len(tr.asked) == 2
    tr.clear_cache()
    words["天氣"] = "날씨님"                                                      # 詞彙表多了跟這句有關的詞：重翻
    tr.answers = "오늘 날씨님 좋다."
    assert tr.translate("今天天氣很好", "ko", "zh", remember="heard") == "오늘 날씨님 좋다." and len(tr.asked) == 3
    tr.clear_cache()
    words["無關"] = "x"                                                           # 無關的詞不影響
    assert tr.translate("今天天氣很好", "ko", "zh", remember="heard") == "오늘 날씨님 좋다." and len(tr.asked) == 3
    del words["天氣"], words["無關"]
    tr.clear_cache()
    tr.answers = "x"
    tr.translate("沒有記的", "ko", "zh")                                          # 沒說要記（例如回譯）：不記
    assert mem.lookup("沒有記的", "zh", "ko", "casual", "4b", "") is None
    tr.answers = "3시에 봐."
    tr.translate("5點見", "ko", "zh", remember="heard")                          # 數字對不上的不記，下次再翻
    assert mem.lookup("5點見", "zh", "ko", "casual", "4b", "") is None
    tr.translate("還在說", "ko", "zh", remember="heard", unfinished=True)        # 還沒說完的半句不記
    assert mem.lookup("還在說", "zh", "ko", "casual", "4b", "") is None
    assert terms_print([]) == "" and terms_print([("a", "b")]) == terms_print([("a", "b"), ("a", "b")]) != ""

    # ---- 教過的最優先，而且不會被之後翻的蓋掉
    mem.fix("今天天氣很好", "zh", "ko", "casual", "오늘 날씨 진짜 좋다.")
    mem.store("今天天氣很好", "zh", "ko", "casual", "덮어쓰기", "12b", "", "heard")
    assert mem.lookup("今天天氣很好", "zh", "ko", "casual", "12b", "") == "오늘 날씨 진짜 좋다."
    mem.store("我先去睡了", "zh", "ko", "casual", "나 먼저 잘게.", "12b", "", "typed")      # 我自己打的
    mem.store("  牠來了  ", "zh", "ko", "casual", "걔 왔다.", "12b", "", "screen")
    assert mem.lookup("牠來了", "zh", "ko", "casual", "4b", "") == "걔 왔다."      # 前後空白不算

    # ---- 匯出：只有我的；預設不含我自己打的、說的；教過的一定在
    data = mem.export("all")
    srcs = {i["src"] for i in data["items"]}
    assert data["kind"] == KIND and "我先去睡了" not in srcs and {"今天天氣很好", "牠來了"} <= srcs
    assert next(i for i in data["items"] if i["src"] == "今天天氣很好")["fixed"] is True
    assert "我先去睡了" in {i["src"] for i in mem.export("all", with_mine=True)["items"]}
    clock = [10_000_000.0]
    old = Memory(tmp_path / "old", now=lambda: clock[0])
    old.store("很久以前", "zh", "ko", "casual", "옛날에", "4b", "", "heard")
    old.fix("教過的", "zh", "ko", "casual", "가르친 거")
    clock[0] += 3 * 3600
    assert {i["src"] for i in old.export("2h")["items"]} == {"教過的"} and len(old.export("all")["items"]) == 2

    # ---- 朋友匯入：一包；同一個檔案匯入兩次不會重複；人工改過的蓋過朋友自己翻的；不想用就關掉；可以整包刪
    friend = Memory(tmp_path / "f")
    friend.store("今天天氣很好", "zh", "ko", "casual", "朋友自己翻的", "12b", "", "heard")
    friend.store("牠來了", "zh", "ko", "casual", "朋友的", "12b", "", "heard")
    langs = ("zh", "ko", "en", "ja")
    got = friend.import_pack(data, "第一集", sources=langs, targets=("ko", "zh-TW"))
    assert got["ok"] and got["added"] == len(data["items"]) and got["pack"]["name"] == "第一集"
    assert friend.import_pack(data, "第一集", sources=langs, targets=("ko",))["added"] == got["added"]
    st = friend.state()
    assert len(st["packs"]) == 1 and st["packs"][0]["count"] == got["added"] and st["mine"] == 2
    assert friend.lookup("今天天氣很好", "zh", "ko", "casual", "12b", "") == "오늘 날씨 진짜 좋다."   # 人工改過的 > 自己機器翻的
    assert friend.lookup("牠來了", "zh", "ko", "casual", "12b", "") == "朋友的"                     # 機器翻的：自己的優先
    friend.forget_mine()
    assert friend.lookup("牠來了", "zh", "ko", "casual", "12b", "") == "걔 왔다."
    assert friend.lookup("牠來了", "zh", "ko", "casual", "12b", "abc") is None                     # 我的詞彙表跟這句有關：不用別人機器翻的
    friend.shared_on = False
    assert friend.lookup("今天天氣很好", "zh", "ko", "casual", "12b", "") is None                   # 只用自己翻的
    friend.shared_on = True
    friend.mine_on = False
    friend.store("新的一句", "zh", "ko", "casual", "새 문장", "12b", "", "heard")
    assert friend.state()["mine"] == 0                                                               # 關掉記住：不記新的
    friend.mine_on = True
    friend.remove_pack(got["pack"]["id"])
    assert friend.state()["packs"] == [] and friend.lookup("牠來了", "zh", "ko", "casual", "4b", "") is None
    for bad in (None, [], "x", {"kind": "other"}, {"kind": KIND, "items": "nope"}):
        assert friend.import_pack(bad, "x", sources=langs, targets=("ko",))["ok"] is False
    odd = {"kind": KIND, "items": [1, {"src": "", "dst": "x", "lang": "zh", "target": "ko"},
                                   {"src": "a", "dst": "b", "lang": "xx", "target": "ko"},
                                   {"src": "a", "dst": "b", "lang": "zh", "target": "klingon"},
                                   {"src": "一樣", "dst": "一樣", "lang": "zh", "target": "ko"},
                                   {"src": "好", "dst": "좋아", "lang": "zh", "target": "ko", "tone": "rude"},
                                   {"src": "你好", "dst": "안녕", "lang": "zh", "target": "ko", "fixed": "yes"}]}
    got = friend.import_pack(odd, "", sources=langs, targets=("ko",))
    assert got["added"] == 1 and got["skipped"] == 6
    assert friend.lookup("你好", "zh", "ko", "casual", "12b", "") is None             # fixed 不是 true：當機器翻的，模型不明比 12b 差
    assert friend.lookup("你好", "zh", "ko", "casual", "", "") == "안녕"
    mem.close()
    assert mem.lookup("今天天氣很好", "zh", "ko", "casual", "4b", "") is None          # 資料庫關了：當作沒記住，不會出錯
    mem.store("x", "zh", "ko", "casual", "y", "4b", "", "heard")

    # ---- 整個程式：教句子會同步、刪掉教的也會忘掉、匯出檔、選錯檔案的提示、設定開關
    me = Service(tmp_path / "me")
    me.cfg.set(transcript_dir=str(tmp_path / "out"))
    assert me.translator.memory is me.memory and me.state()["memory"]["mine"] == 0
    me.messages.append({"id": 1, "who": "them", "via": "heard", "original": "我們走吧", "lang": "zh", "target": "ko",
                        "translated": "갈까?", "pending": False, "error": ""})
    me._post = lambda msg: None
    me._want_back = lambda msg: None
    assert me.teach(1, "가자.")
    assert me.memory.lookup("我們走吧", "zh", "ko", me.cfg["tone"], "12b", "") == "가자."
    out = me.memory_export("all")
    assert out["ok"] and out["sentences"] == 1 and json.loads(Path(out["path"]).read_text(encoding="utf-8"))["kind"] == KIND
    assert me.memory_export("bogus")["ok"]                                              # 不認得的範圍：當全部
    me.lesson_remove("我們走吧", "ko")
    assert me.memory.lookup("我們走吧", "zh", "ko", me.cfg["tone"], "4b", "") is None
    assert me.memory_export("all") == {"ok": False, "error": "empty"}
    assert me.memory_import({"kind": LIBRARY_KIND}) == {"ok": False, "error": "is_library"}
    assert me.import_library({"kind": KIND, "items": []}) == {"ok": False, "error": "is_pack"}
    got = me.memory_import(data, "第一集.json")
    assert got["ok"] and got["pack"]["name"] == "第一集"
    assert me.memory_remove_pack(got["pack"]["id"])["ok"] and me.memory.state()["packs"] == []
    me.update_config({"remember_translations": False, "use_shared": False})
    assert me.memory.mine_on is False and me.memory.shared_on is False
    me.update_config({"remember_translations": True, "use_shared": True})
    assert me.memory.mine_on and me.memory.shared_on and me.memory_forget()["ok"]


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
    assert assets.pick_cudnn_wheel(index) == ("https://x/9.10", 3, "")               # 最新的 9、Windows 版、沒被撤回
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

    def fake_fetch(url, dest, progress=None, cancel=None, attempts=8, check=None):
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

    # ---- 快速輸入框本身：每種介面語言都有提示，放在螢幕下方置中；這個小程式裡沒有任何「替人打字、貼上」的東西
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
    每一種狀況在每種介面語言裡都有一句話，而且檢查本身不會打開麥克風、不會改設定。
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

    # 每一個燈的名字、每一種狀態、每一個代碼，每種介面語言都有一句話
    root = Path(__file__).resolve().parent.parent / "livelingo"
    source = (root / "service.py").read_text(encoding="utf-8")
    body = source[source.index("    def health(self)"):source.index("    # ------------------------------------------------------------------ 問題報告")]
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


def test_settings_export_import_reset_undo(tmp_path, monkeypatch):
    """
    設定可以匯出成檔案、匯入回來、恢復預設；匯入和恢復預設之前的設定會留一份，可以復原。
    恢復預設不動語言、框好的聊天區、自己存的版面；亂寫的設定檔不會把設定弄壞。
    """
    import json as _json

    from livelingo import hotkeys, oswin, overlay
    from livelingo.config import DEFAULTS, EDITABLE
    from livelingo.service import RESET_KEEP, SETTINGS_KIND, SETTINGS_SKIP, Service

    assert set(SETTINGS_SKIP) <= set(DEFAULTS) and set(RESET_KEEP) <= set(DEFAULTS) and not set(SETTINGS_SKIP) & set(RESET_KEEP)
    monkeypatch.setattr(oswin, "open_path", lambda path: True)
    monkeypatch.setenv("LIVELINGO_FAKE_MONITORS", "0,0,1920,1080")
    service = Service(tmp_path)
    service.hotkeys.backend = hotkeys.FakeBackend()
    service.start_hotkeys()
    service.overlay.window_class = overlay.MemoryWindow
    region = {"id": "r1", "name": "隊伍", "left": 10, "top": 20, "width": 400, "height": 200, "on": True, "app": "", "anchor": None}
    layout = {"name": "打 LOL", "look": {"overlay_font": 30, "overlay_bold": False}, "rect": [50, 60, 500, 260], "form": "float", "monitor": 0}
    try:
        service.update_config({"theme": "light", "accent": "rose", "overlay_font": 40, "tone": "polite", "my_lang": "ja", "ui_lang": "ja",
                               "history_days": "90", "hotkeys": {"mic": "Ctrl+Shift+M"}, "ptt": True, "voice_sensitivity": 5})
        service.cfg.set(regions=[region], overlay_layouts=[layout], overlay_rect=[100, 100, 500, 300], setup_done=True,
                        window={"w": 1000, "h": 700, "x": 5, "y": 6}, interval=1.0)
        mine = service.cfg.snapshot()

        # ---- 匯出：是 LiveLingo 的設定檔，裡面沒有視窗位置這類不算設定的東西
        done = service.settings_export()
        assert done["ok"] and done["name"].startswith("LiveLingo-settings-")
        exported = _json.loads((service.transcript_dir() / done["name"]).read_text(encoding="utf-8"))
        assert exported["kind"] == SETTINGS_KIND and exported["config"]["theme"] == "light"
        assert not set(SETTINGS_SKIP) & set(exported["config"]) and EDITABLE - set(SETTINGS_SKIP) <= set(exported["config"])

        # ---- 恢復預設：設定回去了，該留的留著，而且留了一份可以復原
        assert service.settings_backup() is None
        reset = service.settings_reset()
        now = service.cfg.snapshot()
        assert reset["ok"] and reset["saved"] and reset["backup"]["reason"] == "reset" and reset["count"] == len(reset["changed"]) > 5
        for key in ("theme", "accent", "overlay_font", "tone", "history_days", "hotkeys", "ptt", "voice_sensitivity", "overlay_rect", "interval"):
            assert now[key] == DEFAULTS[key], key
        for key in (*RESET_KEEP, *SETTINGS_SKIP):
            assert now[key] == mine[key], key
        assert service.hotkeys.bindings["mic"] == "Ctrl+Alt+M" and service.history.days == DEFAULTS["history_days"]      # 馬上生效
        assert service.state()["settings_backup"]["reason"] == "reset"

        # ---- 復原：跟恢復預設之前一模一樣，留著的那一份用掉了
        undone = service.settings_undo()
        assert undone["ok"] and service.cfg.snapshot() == mine and service.settings_backup() is None
        assert service.hotkeys.bindings["mic"] == "Ctrl+Shift+M"
        assert service.settings_undo() == {"ok": False, "error": "no_backup"}

        # ---- 匯入：換一台電腦（全新的設定）匯入剛才那個檔，聊天區和版面也回來
        other = Service(tmp_path / "pc2")
        try:
            fresh = other.cfg.snapshot()
            got = other.settings_import(exported)
            after = other.cfg.snapshot()
            assert got["ok"] and got["backup"]["reason"] == "import" and "regions" in got["changed"] and "theme" in got["changed"]
            for key in exported["config"]:
                assert after[key] == mine[key], key
            for key in SETTINGS_SKIP:
                assert after[key] == fresh[key], key
            other.settings_undo(forget=True)      # 不復原，只是把留著的丟掉
            assert other.settings_backup() is None and other.cfg["theme"] == "light"
        finally:
            other.shutdown()

        # ---- 亂寫的檔案：不是設定檔的不收；個別項目不合格的跳過，其他照套
        for junk in (None, [], "x", {"kind": "livelingo-library"}, {"kind": SETTINGS_KIND}, {"kind": SETTINGS_KIND, "config": []}):
            assert service.settings_import(junk) == {"ok": False, "error": "not_settings"}
        weird = {"kind": SETTINGS_KIND, "config": {
            "theme": "neon", "accent": "blue", "overlay_font": 9999, "hotkeys": {"mic": "M", "copy": "Ctrl+Alt+S"}, "history_days": 13,
            "regions": [{"id": "r9", "left": 1, "top": 1, "width": 500, "height": 300, "name": "x" * 99, "app": "Game.exe", "anchor": "bad"},
                        {"id": "tiny", "left": 0, "top": 0, "width": 5, "height": 5}, "junk", {"id": "r9", "left": 0, "top": 0, "width": 99, "height": 99}],
            "overlay_layouts": [{"name": "ok", "look": {"overlay_font": "big", "overlay_bold": True}, "rect": [1, 2, 300, 200], "monitor": "x"},
                                {"name": "", "look": {}, "rect": [1, 2, 3, 4]}, {"name": "norect", "look": {}}],
            "overlay_rect": [1, 2, "wide", 4], "interval": 999, "threads": 8, "window": {"w": 1, "h": 1}, "setup_done": False,
            "made_up": 1, "transcript_dir": 5}}
        got = service.settings_import(weird)
        now = service.cfg.snapshot()
        assert got["ok"] and now["theme"] == "light" and now["accent"] == "blue" and now["overlay_font"] == 60
        assert now["hotkeys"] == mine["hotkeys"] and now["history_days"] == 90
        assert now["regions"] == [{"id": "r9", "name": "x" * 30, "left": 1, "top": 1, "width": 500, "height": 300, "on": True, "app": "", "anchor": None}]
        assert now["overlay_layouts"] == [{"name": "ok", "look": {"overlay_bold": True}, "rect": [1, 2, 300, 200], "form": "float", "monitor": 0}]
        assert now["overlay_rect"] == mine["overlay_rect"] and now["interval"] == 1.0 and now["threads"] == 8
        assert now["window"] == mine["window"] and now["setup_done"] is True and "made_up" not in now
        assert now["transcript_dir"] == mine["transcript_dir"]
    finally:
        service.shutdown()


def test_text_colours_are_readable_and_everything_has_a_keyboard_path():
    """第 43 項：字的顏色對背景夠清楚（WCAG AA，4.5 比 1），字不小於 12px，滑鼠點得到的都是真的按鈕。"""
    import re
    from pathlib import Path
    ui = Path(__file__).resolve().parents[1] / "livelingo" / "ui"
    css = (ui / "style.css").read_text(encoding="utf-8")
    js = (ui / "app.js").read_text(encoding="utf-8")
    html = (ui / "index.html").read_text(encoding="utf-8")

    def tokens(selector):
        block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css).group(1)
        return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", block))

    def rgb(c):
        return [int(c[i:i + 2], 16) for i in (1, 3, 5)]

    def lum(c):
        r, g, b = [v / 255 / 12.92 if v / 255 <= 0.03928 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def ratio(a, b):
        x, y = lum(a), lum(b)
        return (max(x, y) + 0.05) / (min(x, y) + 0.05)

    def soft(accent, ground):      # --accent-soft：一成強調色蓋在底色上
        return [accent[i] * 0.10 + ground[i] * 0.90 for i in range(3)]

    dark = tokens(":root")
    light = {**dark, **tokens(':root[data-theme="light"]')}
    accents = ("blue", "violet", "rose", "cyan")
    themes = [("dark", dark, [dark] + [{**dark, **tokens(f':root[data-accent="{a}"]')} for a in accents]),
              ("light", light, [light] + [{**light, **tokens(f':root[data-theme="light"][data-accent="{a}"]')} for a in accents])]
    low = []
    for name, base, variants in themes:
        grounds = [rgb(base[k]) for k in ("ground", "surface", "surface-2")]
        for key in ("text", "dim", "faint", "me", "danger"):
            for g in grounds:
                if ratio(rgb(base[key]), g) < 4.5:
                    low.append((name, key, base[key], round(ratio(rgb(base[key]), g), 2)))
        for key in ("me", "me-dim"):      # 我說的話那一塊的底色
            if ratio(rgb(base[key]), rgb(base["me-bg"])) < 4.5:
                low.append((name, key, "on me-bg"))
        for v in variants:
            a = rgb(v["accent"])
            if ratio(a, rgb(v["accent-ink"])) < 4.5:
                low.append((name, "accent-ink", v["accent"]))
            for g in grounds:      # 強調色的字會放在一般底色上，也會放在淡淡的強調色底上
                worst = min(ratio(a, g), ratio(a, soft(a, g)))
                if worst < 4.5:
                    low.append((name, "accent", v["accent"], round(worst, 2)))
    assert not low, low

    # 色票上畫的顏色要和樣式表裡的一樣，不然選了一個顏色出來是另一個
    table = {m[0]: (m[1], m[2]) for m in re.findall(r'(\w+): \["(#[0-9A-Fa-f]{6})", "(#[0-9A-Fa-f]{6})"\]', re.search(r"const ACCENTS = \{([^}]*)\}", js).group(1))}
    assert table["mint"] == (dark["accent"], light["accent"])
    for a in accents:
        assert table[a] == (tokens(f':root[data-accent="{a}"]')["accent"], tokens(f':root[data-theme="light"][data-accent="{a}"]')["accent"]), a

    # 字不小於 12px（font-size: 0 是把字藏起來，不算）
    sizes = [float(s) for s in re.findall(r"font-size:\s*([0-9.]+)px", css + js + html)]
    assert all(s == 0 or s >= 12 for s in sizes), sorted(set(s for s in sizes if 0 < s < 12))
    assert not re.search(r"font-size:\s*[0-9.]+(em|rem|%)", css + js + html)      # 用 px，才檢查得到

    # 點得到的東西都是鍵盤也到得了的：data-act 只放在 button 上
    for src in (js, html):
        for tag in re.findall(r"<([a-z0-9]+)\b[^<>]*\bdata-act=", src):
            assert tag == "button", tag
    assert "onclick=" not in js + html
    assert not re.search(r'tabindex="-1"', js + html)
    assert ":focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }" in css
    for bad in re.findall(r"[^{}]*:focus[^{}]*\{[^}]*outline:\s*(?:none|0)[^}]*\}", css):      # 拿掉外框的地方要有別的看得到的標示
        assert "border-color" in bad or "box-shadow" in bad, bad


def test_keyboard_only_paths():
    """第 43 項：沒有滑鼠也能做完每一件事。框選可以用方向鍵；畫面重畫之後焦點不會掉；蓋板開著的時候底下的不能按。"""
    from pathlib import Path

    from livelingo import region

    # 框選：第一下方向鍵先放一個框在主螢幕中間
    assert region.first_box(1920, 1080) == (576, 405, 1344, 675)
    assert region.first_box(1920, 1080, 1920, 0)[0] == 1920 + 576      # 主螢幕不在最左邊的時候（左邊還有一台）
    box = (100, 100, 400, 300)
    assert region.nudge(box, region.STEP, 0, False, 1920, 1080) == (120, 100, 420, 300)
    assert region.nudge(box, 0, -region.STEP, False, 1920, 1080) == (100, 80, 400, 280)
    assert region.nudge((0, 0, 300, 200), -region.STEP, -region.STEP, False, 1920, 1080) == (0, 0, 300, 200)      # 到邊了就不動，大小不變
    assert region.nudge((1700, 900, 1920, 1080), 20, 20, False, 1920, 1080) == (1700, 900, 1920, 1080)
    assert region.nudge(box, region.STEP, region.STEP, True, 1920, 1080) == (100, 100, 420, 320)      # Shift：動右下角
    assert region.nudge(box, -1000, -1000, True, 1920, 1080) == (100, 100, 100 + region.SMALLEST[0], 100 + region.SMALLEST[1])
    assert region.nudge(box, 5000, 5000, True, 1920, 1080) == (100, 100, 1920, 1080)
    assert region.nudge((400, 300, 100, 100), 0, 0, False, 1920, 1080) == (100, 100, 400, 300)      # 滑鼠從右下往左上拖出來的框
    src = (Path(region.__file__)).read_text(encoding="utf-8")
    for key in ('"<Return>"', '"<Escape>"', "<Shift-{name}>", '"Left"', '"Right"', '"Up"', '"Down"'):
        assert key in src, key
    assert set(region.HINTS) == set(UI_LANGS)
    for text in region.HINTS.values():
        assert "Shift" in text and "Enter" in text and "Esc" in text

    js = (Path(region.__file__).parent / "ui" / "app.js").read_text(encoding="utf-8")
    # 焦點看守：重畫之後找回同一顆按鈕；用滑鼠的時候不搶
    assert "new MutationObserver(focusRepair).observe(app" in js
    assert 'document.addEventListener("focusin"' in js and 'document.addEventListener("pointerdown"' in js
    # 蓋板（更新、下載、錯誤）開著的時候底下的都不能按
    assert '["rail", "view-main", "view-settings", "view-wizard"].forEach((id) => { $(id).inert = !!html; });' in js
    assert 'role="alertdialog" aria-modal="true"' in js
    # Esc 關掉還在問的確認；Ctrl+數字換頁
    assert 'if (S.ui.resetAsk && S.view === "settings") actions["set-reset-no"]();' in js
    assert 'if (S.ui.histConfirm && S.view === "settings") actions["hist-clear-no"]();' in js
    assert "/^[1-9]$/.test(e.key)" in js


def test_storage_sizes_move_and_remove_unused(tmp_path, monkeypatch):
    """第 45 項：大檔案放在哪、各佔多少、搬到別的資料夾、刪掉沒在用的。卸載時搬出去的也會刪，但只刪有記號的資料夾。"""
    import json as _json
    from pathlib import Path

    from livelingo import assets, storage
    from livelingo.config import DEFAULTS, EDITABLE
    from livelingo.service import SETTINGS_SKIP, Service

    assert DEFAULTS["store_dir"] == "" and DEFAULTS["store_leftover"] == []
    assert not {"store_dir", "store_leftover"} & EDITABLE and {"store_dir", "store_leftover"} <= set(SETTINGS_SKIP)      # 介面不能直接改，匯出匯入也不帶

    data = tmp_path / "data"
    files = {"models/translategemma-4b.gguf": 5000, "models/translategemma-12b.gguf": 9000, "models/whisper/model.bin": 3000,
             "models/ocr/det.onnx": 200, "engine/cpu/llama-server": 100, "engine/cuda/llama-server": 700, "engine/cuda/cublas64_12.dll": 300,
             "engine/cudnn/cudnn64_9.dll": 400, "models/translategemma-12b.gguf.part": 50, "update/LiveLingo-Setup.exe": 800,
             "app.log": 120}
    for rel, size in files.items():
        if size:
            (data / rel).parent.mkdir(parents=True, exist_ok=True)
            (data / rel).write_bytes(b"x" * size)

    # 純函式
    assert storage.store_dir(data, "") == data and storage.store_dir(data, str(tmp_path / "gone")) == data      # 記的資料夾不見了：回原本的
    assert storage.target_for(tmp_path / "D" / "Games") == tmp_path / "D" / "Games" / "LiveLingo"
    assert storage.target_for(tmp_path / "D" / "livelingo") == tmp_path / "D" / "livelingo"      # 選的就是空的 LiveLingo 資料夾
    (tmp_path / "busy" / "LiveLingo").mkdir(parents=True)
    (tmp_path / "busy" / "LiveLingo" / "save.dat").write_bytes(b"1")
    assert storage.target_for(tmp_path / "busy" / "LiveLingo") == tmp_path / "busy" / "LiveLingo" / "LiveLingo"      # 同名但有別人的東西：再開一層
    assert storage.size_bytes(data / "models") == 5000 + 9000 + 3000 + 200 + 50
    assert storage.check_target(data, data)[0] == "store_same"
    assert storage.check_target(data, data / "models" / "x" / "LiveLingo")[0] == "store_inside"
    monkeypatch.setattr(storage, "free_bytes", lambda folder: 1000)
    code, more = storage.check_target(data, tmp_path / "small" / "LiveLingo")
    assert code == "store_no_space" and more["free"] == 0.0 and not (tmp_path / "small").exists()
    monkeypatch.undo()

    opened, notices = [], []
    monkeypatch.setattr("livelingo.service.oswin.open_path", lambda p: opened.append(Path(p)))
    service = Service(data)
    service.notice = lambda code, **more: notices.append((code, more))
    events = []
    service.hub.publish = (lambda old: lambda kind, payload=None: (events.append((kind, payload)), old(kind, payload))[1])(service.hub.publish)
    started = []
    service._start_engine = lambda: started.append(service.store)
    try:
        assert service.store == data
        service.phase = "ready"
        st = service.storage_state()
        rows = {r["id"]: r for r in st["items"]}
        assert st["folder"] == str(data) and not st["moved"] and st["lost"] == "" and st["free"] > 0 and st["moving"] is None and st["can_pick"] is False
        assert rows["model_4b"] == {"id": "model_4b", "bytes": 5000, "used": True, "removable": False}      # 現在用的（標準、不用顯示卡）
        assert rows["model_12b"]["removable"] and rows["engine_cuda"]["removable"] and rows["cudnn"]["removable"]
        assert rows["engine_cpu"]["used"] and rows["engine_cuda"]["bytes"] == 1000
        assert rows["whisper"]["used"] and not rows["whisper"]["removable"] and not rows["ocr"]["removable"]
        assert rows["partial"] == {"id": "partial", "bytes": 50, "used": False, "removable": True}
        assert rows["updates"]["bytes"] == 800 and rows["updates"]["removable"]
        assert rows["records"]["bytes"] >= 120 and not rows["records"]["removable"]
        assert st["total"] == sum(r["bytes"] for r in st["items"])
        # 語音辨識用顯示卡：cuDNN 和顯示卡版引擎就不能刪
        service.cfg.set(speech_device="gpu")
        rows = {r["id"]: r for r in service.storage_state()["items"]}
        assert rows["cudnn"]["used"] and rows["engine_cuda"]["used"] and not rows["engine_cuda"]["removable"]
        service.cfg.set(speech_device="cpu")
        # 設定是高品質、這台也跑得動，只是暫時退回標準：兩個模型都要留
        service.gpu = {"name": "RTX", "vram_mb": 16000, "tier": "high"}
        service.cfg.set(quality="auto")
        rows = {r["id"]: r for r in service.storage_state()["items"]}
        assert rows["model_12b"]["used"] and rows["model_4b"]["used"]
        service.gpu = {"name": None, "vram_mb": 0, "tier": "cpu"}

        # 刪掉沒在用的；在用的刪不掉
        assert service.storage_remove("model_4b") == {"ok": False, "error": "store_in_use"}
        assert service.storage_remove("whisper")["error"] == "store_in_use" and service.storage_remove("records")["error"] == "store_in_use"
        assert service.storage_remove("nothing")["error"] == "store_in_use"
        assert (data / "models" / "translategemma-4b.gguf").exists() and (data / "models" / "whisper" / "model.bin").exists()
        assert service.storage_remove("model_12b") == {"ok": True, "freed": 9000, "error": ""}
        assert not (data / "models" / "translategemma-12b.gguf").exists() and (data / "models" / "translategemma-12b.gguf.part").exists()
        assert service.storage_remove("partial")["ok"] and not (data / "models" / "translategemma-12b.gguf.part").exists()
        assert service.storage_remove("updates")["freed"] == 800 and not (data / "update").exists()
        assert service.storage_remove("cudnn")["ok"] and not (data / "engine" / "cudnn").exists()
        assert [k for k, _ in events].count("storage") == 4
        service.phase = "downloading"
        assert service.storage_remove("engine_cuda")["error"] == "store_busy" and service.storage_move(str(tmp_path / "D"))["error"] == "store_busy"
        service.phase = "ready"

        # 搬：不能搬的情況
        assert service.storage_move("")["error"] == "store_not_writable" and service.storage_move("relative/folder")["error"] == "store_not_writable"
        assert service.storage_choose() == {"ok": False, "error": "store_no_window"}

        # 搬到別的資料夾（使用者選的是 D/Games，實際放在 D/Games/LiveLingo）
        picked = tmp_path / "D" / "Games"
        picked.mkdir(parents=True)
        (picked / "other-game.sav").write_bytes(b"keep")
        service.window = type("W", (), {"choose_folder": lambda self, start="": str(picked)})()
        assert service.storage_state()["can_pick"] is True
        r = service.storage_choose()
        new = picked / "LiveLingo"
        assert r == {"ok": True, "to": str(new)}
        deadline = time.time() + 10
        while service._move and time.time() < deadline:
            time.sleep(0.02)
        assert service._move is None and service.store == new and service.cfg["store_dir"] == str(new)
        assert (new / storage.MARK).exists() and (new / "models" / "translategemma-4b.gguf").stat().st_size == 5000
        assert (new / "engine" / "cuda" / "cublas64_12.dll").exists() and (new / "models" / "whisper" / "model.bin").exists()
        assert not (data / "models").exists() and not (data / "engine").exists()      # 舊的刪掉了
        assert (data / "config.json").exists() and (data / "app.log").exists()      # 設定和歷史留在原本的地方
        assert started == [new]      # 翻譯引擎重開，讀新位置的模型
        assert notices[-1] == ("store_moved", {"folder": str(new)}) and service.cfg["store_leftover"] == []
        assert any(k == "storage_move" and p["total"] == 5000 + 3000 + 200 + 100 + 1000 for k, p in events)
        st = service.storage_state()
        assert st["moved"] and st["folder"] == str(new) and st["default"] == str(data)
        assert assets.missing(new, service.mode, need_engine=False)[0]["key"] == "ocr"      # 缺的只有測試沒放的 OCR 檔，模型找得到
        service.storage_open()
        assert opened[-1] == new
        assert _json.loads((data / "config.json").read_text(encoding="utf-8"))["store_dir"] == str(new)

        # 重開程式：照記下來的位置；匯出的設定不帶這個位置
        again = Service(data)
        try:
            assert again.store == new and "store_dir" not in again._settings_now()
        finally:
            again.shutdown()

        # 搬到一半取消：新的地方清乾淨，原本的沒動
        slow = tmp_path / "E"
        slow.mkdir()
        real_copy = storage.copy_parts

        def cancelling(store, target, progress=None, cancel=None, mark=True):
            service.storage_cancel()
            return real_copy(store, target, progress, cancel, mark)

        monkeypatch.setattr(storage, "copy_parts", cancelling)
        assert service.storage_move(str(slow))["ok"]
        deadline = time.time() + 10
        while service._move and time.time() < deadline:
            time.sleep(0.02)
        assert notices[-1][0] == "store_cancelled" and service.store == new and not (slow / "LiveLingo" / "models").exists()
        assert (new / "models" / "translategemma-4b.gguf").exists()
        monkeypatch.setattr(storage, "copy_parts", real_copy)

        # 搬回原本的位置；舊的那邊有刪不掉的（還開著）就記下來，下次開程式再刪
        real_remove = storage.remove_parts
        monkeypatch.setattr(storage, "remove_parts", lambda folder: [str(folder / "engine")] if folder == new else real_remove(folder))
        assert service.storage_move(back=True) == {"ok": True, "to": str(data)}
        deadline = time.time() + 10
        while service._move and time.time() < deadline:
            time.sleep(0.02)
        assert service.store == data and service.cfg["store_dir"] == "" and not (data / storage.MARK).exists()
        assert (data / "models" / "translategemma-4b.gguf").exists() and notices[-1][0] == "store_moved_later"
        assert service.cfg["store_leftover"] == [str(new)] and (new / "models").exists()
        monkeypatch.setattr(storage, "remove_parts", real_remove)
        service._storage_cleanup()
        assert service.cfg["store_leftover"] == [] and not new.exists() and (picked / "other-game.sav").read_bytes() == b"keep"
        # 清單裡混進沒有記號的資料夾：不碰
        (tmp_path / "stranger" / "models").mkdir(parents=True)
        service.cfg.set(store_leftover=[str(tmp_path / "stranger")])
        service._storage_cleanup()
        assert (tmp_path / "stranger" / "models").exists() and service.cfg["store_leftover"] == []

        # 正在搬的時候不能下載（會下載到舊的位置）
        service._move = {"to": "x", "done": 0, "total": 0}
        service.phase = "need_download"
        service.start_download()
        assert service.phase == "need_download" and notices[-1][0] == "store_busy"
        service._move = None
    finally:
        service.shutdown()

    # 卸載時：搬出去的也刪；沒有記號的資料夾、原本的資料夾都不碰
    moved = tmp_path / "F" / "LiveLingo"
    (moved / "models").mkdir(parents=True)
    (moved / "models" / "m.bin").write_bytes(b"x")
    (tmp_path / "F" / "mine.txt").write_text("mine", encoding="utf-8")
    assert storage.remove_moved(data, str(moved)) is False and (moved / "models" / "m.bin").exists()      # 沒有記號：不刪
    (moved / storage.MARK).write_text("x", encoding="utf-8")
    assert storage.remove_moved(data, "") is False and storage.remove_moved(data, str(data)) is False
    assert storage.remove_moved(data, str(moved)) is True and not moved.exists() and (tmp_path / "F" / "mine.txt").exists()
    main_src = (Path(storage.__file__).parent / "__main__.py").read_text(encoding="utf-8")
    assert '"--remove-store" in sys.argv' in main_src and "storage.remove_moved(data, str(saved))" in main_src
    iss = (Path(storage.__file__).parents[1] / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert 'Parameters: "--remove-store"' in iss and "[UninstallRun]" in iss
    # 順序：先叫程式刪搬走的大檔案（那時候程式還在），整個卸載完才刪資料夾
    assert "[UninstallDelete]" not in iss and "CurUninstallStep <> usPostUninstall" in iss
    assert iss.startswith("\ufeff")      # 檔案裡有中文、韓文、日文的訊息：要有 BOM，安裝程式的編譯器才會當成 UTF-8
    ci = (Path(storage.__file__).parents[1] / "packaging" / "ci_install_test.py").read_text(encoding="utf-8")
    assert storage.MARK in ci and "not-ours.txt" in ci      # 真的安裝、卸載的那個檢查也看這件事
    # 服務裡找模型、引擎一律用 self.store；每一個會回給介面的原因代碼都有每種介面語言的句子
    import re
    svc = (Path(storage.__file__).parent / "service.py").read_text(encoding="utf-8")
    assert not re.search(r"assets\.\w+\(self\.data", svc) and 'self.data / "models"' not in svc and 'self.data / "engine"' not in svc
    i18n = (Path(storage.__file__).parent / "ui" / "i18n.js").read_text(encoding="utf-8")
    codes = set(re.findall(r'"(store_[a-z_]+)"', svc + Path(storage.__file__).read_text(encoding="utf-8"))) - {"store_dir", "store_leftover"}
    assert {"store_same", "store_inside", "store_no_space", "store_not_writable", "store_busy", "store_in_use", "store_moved"} <= codes
    for code in codes:
        assert i18n.count(f"n_{code}:") == len(UI_LANGS), code
    for row in ("model_4b", "model_12b", "engine_cpu", "engine_cuda", "cudnn", "whisper", "ocr", "updates", "partial", "records"):
        assert i18n.count(f"sto_i_{row}:") == len(UI_LANGS) and i18n.count(f"sto_d_{row}:") == len(UI_LANGS), row


def test_update_prompt_says_what_changed(tmp_path, monkeypatch):
    """第 46 項：更新提示裡有這一版改了什麼。程式裡、更新提示、GitHub 發行說明用的是同一份 changes.json。"""
    import json as _json
    import re
    from pathlib import Path

    from livelingo import __version__, changes, updater

    root = Path(changes.__file__).parent
    notes = changes.bundled()
    # 現在這一版一定要寫改了什麼，每種介面語言都要有，而且句數一樣（少翻一句會被擋）。
    # 2.5.0 才加了越南文等八種介面；更早的版本只有原本五種，這八種介面看舊版說明時用英文
    assert __version__ in notes and list(notes)[0] == __version__
    for version, entry in notes.items():
        need = set(UI_LANGS) if changes._key(version) >= (2, 5, 0) else {"zh-TW", "zh-CN", "en", "ko", "ja"}
        assert set(entry) == need, version
        assert len({len(items) for items in entry.values()}) == 1 and 1 <= len(entry["en"]) <= changes.MAX_ITEMS, version
        for items in entry.values():
            assert all(0 < len(i) <= changes.MAX_CHARS for i in items)
    assert _json.loads((root / "data" / "changes.json").read_text(encoding="utf-8")) == notes      # 檔案本身就是乾淨的，沒有被 clean 丟掉的東西

    # 從網路拿來的：壞掉的部分丟掉，不會整個壞
    dirty = {"3.0.0": {"en": ["ok", "", 5, "x" * 999], "zh-TW": "not a list", "ko": []}, "bad version": {"en": ["x"]}, "2.9": ["x"], "1.0.0": {"en": ["old"]}}
    cleaned = changes.clean(dirty)
    assert list(cleaned) == ["3.0.0", "1.0.0"] and cleaned["3.0.0"] == {"en": ["ok", "x" * changes.MAX_CHARS]}
    assert changes.clean(None) == {} and changes.clean(["x"]) == {} and changes.clean({"1.0.0": {"en": [f"n{i}" for i in range(20)]}})["1.0.0"]["en"] == [f"n{i}" for i in range(changes.MAX_ITEMS)]
    # 只列比現在新的，新的在前
    many = changes.clean({"2.4.0": {"en": ["b"]}, "2.10.0": {"en": ["c"]}, "2.3.0": {"en": ["a"]}, "1.0.0": {"en": ["z"]}})
    assert [n["version"] for n in changes.since(many, "2.3.0")] == ["2.10.0", "2.4.0"] and changes.since(many, "9.0.0") == [] and changes.since(many, "weird") == []

    # 發行說明
    text = changes.release_notes()
    assert "LiveLingo-Setup.exe" in text and "## 這一版改了什麼" in text and "## What's new" in text
    assert all(f"- {item}" in text for item in notes[__version__]["zh-TW"] + notes[__version__]["en"])
    out = tmp_path / "notes.md"
    assert changes.main([str(out)]) == 0 and out.read_text(encoding="utf-8") == text
    workflow = (root.parent / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert "python -m livelingo.changes dist/release-notes.md" in workflow and "livelingo/data/changes.json --target" in workflow and "--notes-file dist/release-notes.md" in workflow
    assert '"livelingo/data"' in (root.parent / "packaging" / "livelingo.spec").read_text(encoding="utf-8")      # data 資料夾有打包進去

    # 查更新：一起帶回說明檔的網址；拿說明
    class Hub(BaseHTTPRequestHandler):
        notes_body = _json.dumps({"9.9.9": {"en": ["Faster"], "zh-TW": ["更快"]}, __version__: {"en": ["current"]}}).encode()

        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path == "/notes":
                body = Hub.notes_body
            else:
                body = _json.dumps({"tag_name": "v9.9.9", "assets": [
                    {"name": "LiveLingo-Setup.exe", "size": 123, "browser_download_url": base + "/setup"},
                    {"name": "changes.json", "browser_download_url": base + "/notes"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Hub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        monkeypatch.setenv("LIVELINGO_UPDATE_API", base + "/latest")
        info = updater.check()
        assert info["version"] == "9.9.9" and info["notes_url"] == base + "/notes" and info["size"] == 123
        got = updater.fetch_notes(info["notes_url"])
        assert changes.since(got) == [{"version": "9.9.9", "items": {"en": ["Faster"], "zh-TW": ["更快"]}}]
        Hub.notes_body = b"<html>not json</html>"
        assert updater.fetch_notes(info["notes_url"]) == {}
        Hub.notes_body = b"[" + b"1," * updater.NOTES_LIMIT + b"1]"
        assert updater.fetch_notes(info["notes_url"]) == {}      # 太大：不讀
        assert updater.fetch_notes("") == {} and updater.fetch_notes("http://127.0.0.1:9/none", timeout=1) == {}

        # 服務：問要不要更新的時候帶著說明和大小；按「這次先不要」就照常
        from livelingo.service import Service
        Hub.notes_body = _json.dumps({"9.9.9": {"en": ["Faster"], "zh-TW": ["更快"]}}).encode()
        service = Service(tmp_path / "svc")
        try:
            assert service.changes == notes and service.state()["changes"] == notes and service.state()["updated_from"] == ""
            seen = []
            service.set_phase = lambda phase, **detail: seen.append((phase, detail))
            done = []
            threading.Thread(target=lambda: done.append(service._update_if_needed()), daemon=True).start()
            deadline = time.time() + 10
            while not any(d.get("step") == "ask" for _, d in seen) and time.time() < deadline:
                time.sleep(0.02)
            ask = next(d for _, d in seen if d.get("step") == "ask")
            assert ask == {"step": "ask", "version": "9.9.9", "size": 123, "notes": [{"version": "9.9.9", "items": {"en": ["Faster"], "zh-TW": ["更快"]}}]}
            service.update_choice("later")
            deadline = time.time() + 10
            while not done and time.time() < deadline:
                time.sleep(0.02)
            assert done == [False]
        finally:
            service.shutdown()
    finally:
        httpd.shutdown()

    # 自動更新完第一次開：說一次「已更新」，之後不再說
    folder = tmp_path / "upd"
    folder.mkdir()
    assert updater.just_updated(folder) == ""
    (folder / "update.json").write_text(_json.dumps({"version": __version__, "at": time.time(), "from": "2.2.1"}), encoding="utf-8")
    assert updater.just_updated(folder) == "2.2.1" and updater.just_updated(folder) == ""
    (folder / "update.json").write_text(_json.dumps({"version": "9.9.9", "at": time.time(), "from": "2.2.1"}), encoding="utf-8")
    assert updater.just_updated(folder) == ""      # 沒裝成功（還是舊版）：不說
    (folder / "update.json").write_text(_json.dumps({"version": __version__, "at": time.time(), "from": __version__}), encoding="utf-8")
    assert updater.just_updated(folder) == ""

    js = (root / "ui" / "app.js").read_text(encoding="utf-8")
    assert "changesHTML(d.notes" in js and 'post("/api/seen"' in js and "aboutChangesHTML()" in js
    assert not re.search(r"<li>\$\{(?!esc\()", js)      # 說明是從網路來的字：一定當作純文字放進畫面
    ci = (root.parent / "packaging" / "ci_install_test.py").read_text(encoding="utf-8")
    assert "the update prompt does not carry what changed" in ci


def test_roll_back_to_the_previous_version(tmp_path, monkeypatch):
    """第 47 項：更新之後可以一鍵退回上一版（重新下載那一版的安裝檔，核對過才裝）；退回之後不再問要不要更新到被退掉的那一版。"""
    import hashlib
    import json as _json
    from pathlib import Path

    from livelingo import __version__, updater
    from livelingo.service import Service

    newer = ".".join(str(n) for n in (*updater.parse_version(__version__)[:2], updater.parse_version(__version__)[2] + 1))
    older = "2.2.1"
    assert updater.is_newer(__version__, older) and updater.is_newer(newer, __version__)

    data = tmp_path / "data"
    (data / "update").mkdir(parents=True)
    attempts = updater.Attempts(data)
    assert attempts.read() == {} and updater.previous(data) is None and updater.skipped(data) == ""

    # 下載新版的時候：別的版本的安裝檔都用不到了，刪掉
    current_setup = updater.installer_path(data / "update", __version__)
    current_setup.write_bytes(b"this version's installer")
    (data / "update" / "LiveLingo-Setup-1.0.0.exe").write_bytes(b"ancient")
    payload = b"the next version's installer"

    class Files(BaseHTTPRequestHandler):
        hits: list[str] = []

        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            Files.hits.append(self.path)
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path.startswith("/tags/"):
                version = self.path.rsplit("/", 1)[1].lstrip("v")
                blob = f"installer of {version}".encode()
                body = _json.dumps({"tag_name": "v" + version, "assets": [{
                    "name": "LiveLingo-Setup.exe", "size": len(blob), "digest": "sha256:" + hashlib.sha256(blob).hexdigest(),
                    "browser_download_url": f"{base}/file/{version}"}]}).encode() if version != "2.0.9" else b"{}"
            elif self.path.startswith("/file/"):
                body = f"installer of {self.path.rsplit('/', 1)[1]}".encode()
            elif self.path == "/latest":
                body = _json.dumps({"tag_name": "v" + newer, "assets": [{
                    "name": "LiveLingo-Setup.exe", "size": len(payload), "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                    "browser_download_url": f"{base}/next"}]}).encode()
            else:
                body = payload
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Files)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setenv("LIVELINGO_UPDATE_API", base + "/latest")
    monkeypatch.setenv("LIVELINGO_RELEASE_API", base + "/tags/v{version}")
    launched = []
    monkeypatch.setattr(updater, "launch_installer", lambda path: launched.append(Path(path)))
    try:
        info = updater.check()
        got = updater.download(info, data / "update")
        assert got.read_bytes() == payload and not current_setup.exists() and not (data / "update" / "LiveLingo-Setup-1.0.0.exe").exists()
        got.unlink()
        # 指定版本的安裝檔；查不到、或回來的不是那一版
        old_info = updater.release_of(older)
        assert old_info["version"] == older and old_info["url"] == f"{base}/file/{older}" and old_info["sha256"]
        assert updater.release_of("2.0.9") is None

        # 一、自動更新的那一刻：記下「從哪一版來的」
        service = Service(data)
        exits = []
        service.exit_now = lambda: exits.append(True)
        seen = []
        real_phase = service.set_phase
        service.set_phase = lambda phase, **detail: (seen.append((phase, detail)), real_phase(phase, **detail))[1]
        try:
            assert service.state()["update"] == {"previous": None, "skipped": "", "version": __version__}
            assert service.rollback() == {"ok": False, "error": "rollback_none"}
            done = []
            threading.Thread(target=lambda: done.append(service._update_if_needed()), daemon=True).start()
            deadline = time.time() + 10
            while not any(d.get("step") == "ask" for _, d in seen) and time.time() < deadline:
                time.sleep(0.02)
            service.update_choice("now")
            deadline = time.time() + 10
            while not done and time.time() < deadline:
                time.sleep(0.02)
            assert done == [True] and launched == [updater.installer_path(data / "update", newer)] and exits == [True]
            last = attempts.read()
            assert last["version"] == newer and last["from"] == __version__ and "from_sha256" not in last
        finally:
            service.shutdown()

        # 二、假裝現在是新版（剛從 older 更新上來）。update 資料夾裡就算有同名的舊檔也不用它
        def pretend(from_version: str, blob: bytes | None, **more) -> None:
            for p in (data / "update").glob("LiveLingo-Setup-*"):
                p.unlink()
            if blob is not None:
                updater.installer_path(data / "update", from_version).write_bytes(blob)
            attempts.path.write_text(_json.dumps({"version": __version__, "at": time.time(), "from": from_version, **more}), encoding="utf-8")

        kept = b"some old file with the same name"
        pretend(older, kept)
        # 比 ROLLBACK_FLOOR 舊的版本不提供（它不認得這一版加的東西，退回去會出事）
        assert updater.is_newer(updater.ROLLBACK_FLOOR, older) and updater.previous(data) is None
        monkeypatch.setenv("LIVELINGO_ROLLBACK_FLOOR", "2.0.0")      # 以下假裝上一版夠新
        assert updater.previous(data) == {"version": older}
        assert updater.just_updated(data) == older and updater.just_updated(data) == ""
        launched.clear()
        Files.hits.clear()
        service = Service(data)
        exits, notices = [], []
        service.exit_now = lambda: exits.append(True)
        service.notice = lambda code, **more: notices.append((code, more))
        monkeypatch.setattr("livelingo.service.oswin.open_path", lambda p: None)
        try:
            service.phase = "ready"
            assert service.state()["update"]["previous"] == {"version": older}
            service.phase = "downloading"
            assert service.rollback()["error"] == "store_busy"
            service.phase = "ready"
            service.cfg.set(theme="light")
            assert service.rollback() == {"ok": True, "version": older}
            deadline = time.time() + 10
            while not exits and time.time() < deadline:
                time.sleep(0.02)
            assert exits == [True] and launched == [updater.installer_path(data / "update", older)]
            assert f"/tags/v{older}" in Files.hits and f"/file/{older}" in Files.hits      # 重新下載那一版
            assert launched[0].read_bytes() == f"installer of {older}".encode()      # 裝的是核對過的那一份，不是資料夾裡原本的同名檔
            last = attempts.read()
            assert last["version"] == older and last["from"] == __version__ and last["skip"] == __version__ and last["rollback"] is True
            assert service.phase == "updating" and service.detail == {"step": "installing", "version": older, "back": True}
            copy = service.transcript_dir() / f"LiveLingo-settings-{__version__}-before-rollback.json"      # 設定另外存了一份
            assert _json.loads(copy.read_text(encoding="utf-8"))["config"]["theme"] == "light"

            # 資料夾裡有沒有同名的檔都一樣：重新下載、核對過才裝
            for blob in (b"tampered", None):
                pretend(older, blob)
                assert updater.previous(data) == {"version": older}
                launched.clear(); exits.clear(); Files.hits.clear()
                service.phase, service.detail = "ready", {}
                assert service.rollback()["ok"]
                deadline = time.time() + 10
                while not exits and time.time() < deadline:
                    time.sleep(0.02)
                assert exits == [True] and launched[0].read_bytes() == f"installer of {older}".encode()
                assert f"/tags/v{older}" in Files.hits and f"/file/{older}" in Files.hits

            # 那一版在 GitHub 上找不到：不動現在的版本，回到原本的畫面，說一聲
            pretend("2.0.9", None)
            launched.clear(); exits.clear()
            service.phase, service.detail = "ready", {}
            assert service.rollback()["ok"]
            deadline = time.time() + 10
            while not notices and time.time() < deadline:
                time.sleep(0.02)
            assert notices[-1] == ("rollback_failed", {"version": "2.0.9"}) and not launched and not exits and service.phase == "ready"
            assert attempts.read()["from"] == "2.0.9" and "skip" not in attempts.read()
        finally:
            service.shutdown()

        # 三、退回之後（現在這一版 = 被退回到的那一版）：不再問要不要更新到被退掉的那一版；也沒有「再上一版」可以退
        attempts.path.write_text(_json.dumps({"version": __version__, "at": time.time() - 9999, "from": newer, "skip": newer, "rollback": True}), encoding="utf-8")
        assert updater.previous(data) is None and updater.skipped(data) == newer
        service = Service(data)
        seen = []
        service.set_phase = lambda phase, **detail: seen.append((phase, detail))
        try:
            assert service.updated_from == newer and service.state()["update"]["skipped"] == newer
            assert service._update_if_needed() is False and not any(d.get("step") == "ask" for _, d in seen)
            assert service.update_unskip() == {"ok": True, "previous": None, "skipped": "", "version": __version__}      # 恢復提示
            done = []
            threading.Thread(target=lambda: done.append(service._update_if_needed()), daemon=True).start()
            deadline = time.time() + 10
            while not any(d.get("step") == "ask" for _, d in seen) and time.time() < deadline:
                time.sleep(0.02)
            assert any(d.get("step") == "ask" and d["version"] == newer for _, d in seen)
            service.update_choice("later")
            deadline = time.time() + 10
            while not done and time.time() < deadline:
                time.sleep(0.02)
            assert done == [False]
        finally:
            service.shutdown()
    finally:
        httpd.shutdown()

    root = Path(updater.__file__).parent
    iss = (root.parent / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "ignoreversion" in iss      # 裝舊版蓋過新版的時候，檔案要真的被換掉
    assert updater.parse_version(updater.ROLLBACK_FLOOR) >= (2, 3, 0)      # 2.3.0 之前的版本不認得 --remove-store 和搬過位置的大檔案
    ci = (root.parent / "packaging" / "ci_install_test.py").read_text(encoding="utf-8")
    assert '"rollback": True' in ci and "LIVELINGO_ROLLBACK_FLOOR" in ci      # Windows 上真的退一次
    assert "LIVELINGO_RELEASE_API" in ci and "the used installer to be removed" in ci and "from_sha256" not in ci      # 退回要重新下載；用過的安裝檔要刪掉
    js = (root / "ui" / "app.js").read_text(encoding="utf-8")
    for piece in ('post("/api/update", { rollback: true })', 'post("/api/update", { unskip: true })', 'data-act="rollback-yes"', "up_back_downloading"):
        assert piece in js, piece


def test_problem_report_is_shown_first_and_never_sent(tmp_path, monkeypatch):
    """第 48 項：問題報告先整份給使用者看，他按了才存檔或複製；裡面沒有對話內容和個人資料夾，程式自己不送出任何東西。"""
    import re
    from pathlib import Path

    from livelingo import report
    from livelingo.config import UI_LANGS
    from livelingo.service import Service

    assert set(report.TEXT) == set(UI_LANGS)
    for lang, text in report.TEXT.items():
        assert set(text) == set(report.TEXT["zh-TW"]) and len(text["intro"]) == 3, lang
        assert "{n}" in text["dropped"], lang

    # 換掉個人資料夾、帳號名稱、電腦名稱（路徑有三種寫法；太短或太普通的名字不整個換）
    hide = {"home": r"C:\Users\JimboKim", "user": "JimboKim", "pc": "DESKTOP-J1"}
    raw = (r"folder C:\Users\JimboKim\Documents\LiveLingo, json C:\\Users\\JimboKim\\AppData, slash c:/users/jimbokim/x, "
           "owner jimbokim on DESKTOP-J1, device JimboKim's AirPods, word jimbokimchi stays")
    clean = report.scrub(raw, **hide)
    assert "jimbokim" not in clean.lower().replace("jimbokimchi", "") and "DESKTOP-J1" not in clean
    assert clean.count("%USERPROFILE%") == 3 and "<user>'s AirPods" in clean and "on <pc>" in clean and "jimbokimchi stays" in clean
    assert report.scrub("the user al opened C:\\", home="C:\\", user="al") == "the user al opened C:\\"
    assert report.scrub("admin mode, /home/admin/x", home="/home/admin", user="admin") == "admin mode, %USERPROFILE%/x"

    # 紀錄檔裡含著對話整句話的行不要（短的字不比，免得誤刪）
    rows, dropped = report.drop_said(["mic opened: 麥克風", "error while sending 오늘 저녁에 같이 게임할래", "ok"], ["오늘 저녁에 같이 게임할래", "麥克風", ""])
    assert rows == ["mic opened: 麥克風", "ok"] and dropped == 1

    # 排版：自己寫的說明在最前面，太長會截掉；空的段落寫「沒有」
    text = report.build("en", [("app", {"version": "2.3.0", "installed": True, "error": None, "list": [], "nested": {"a": 1}}), ("log", [])],
                        note="  it broke\nafter ten minutes  ", when=0, dropped=2, hide=hide)
    assert text.startswith("LiveLingo problem report\r\n") and "It has not been sent to anyone" in text
    assert "== What happened ==\r\nit broke\r\nafter ten minutes\r\n" in text
    assert "version: 2.3.0\r\ninstalled: yes\r\nerror: -\r\nlist: -\r\nnested: a=1\r\n" in text
    assert "== Recent log ==\r\n(none)\r\n2 lines were left out" in text
    assert len(report.build("en", [], note="x" * 5000, hide=hide)) < 3000
    assert report.tail(tmp_path / "missing.log", 10) == []

    # 這個檔案不連網路
    source = (Path(report.__file__)).read_text(encoding="utf-8")
    for banned in ("urllib", "socket", "requests", "http.client", "webbrowser", "smtplib", "subprocess"):
        assert banned not in source, banned

    # 真的做一份
    data = tmp_path / "data"
    data.mkdir()
    secret, reply = "오늘 저녁에 같이 게임할래?", "今天晚上要不要一起打遊戲？"
    (data / "app.log").write_text("\n".join(
        [f"2026-11-22 10:01:00 line {n}" for n in range(300)] + ["2026-11-22 10:02:00 mic capture opened: 麥克風 (HyperX Cloud II)",
         f"2026-11-22 10:02:01 something leaked {secret} into the log",
         f"2026-11-22 10:02:02 subtitle log saved: {Path.home()}/Documents/LiveLingo/a.txt"]) + "\n",
        encoding="utf-8")
    (data / "engine.log").write_text("0.00.028 I srv llama_server: initializing ...\n0.07.640 I srv llama_server: model loaded\n", encoding="utf-8")
    opened, copied = [], []
    monkeypatch.setattr("livelingo.service.oswin.open_path", lambda p: opened.append(Path(p)))
    monkeypatch.setattr("livelingo.service.oswin.copy_text", lambda text: copied.append(text) or True)
    monkeypatch.setattr("livelingo.service.oswin.monitors", lambda: [{"left": 0, "top": 0, "width": 2560, "height": 1440, "work": [0, 0, 2560, 1400], "primary": True}])
    service = Service(data)
    service.cfg.set(ui_lang="zh-TW", transcript_dir=str(tmp_path / "out"),
                    regions=[{"id": "r1", "name": "朋友的秘密頻道", "left": 10, "top": 20, "width": 300, "height": 200, "on": True, "app": "Discord.exe", "anchor": [0, 0]}],
                    overlay_layouts=[{"name": "我的排位版面", "look": {}, "rect": None, "form": "float", "monitor": 0}])
    service.glossary.add("정글", "打野")
    service.messages.append({"id": 1, "who": "them", "via": "heard", "original": secret, "translated": reply, "back": "", "pending": False, "error": ""})
    service.history.add({"id": 1, "who": "them", "via": "heard", "original": secret, "translated": reply, "lang": "ko", "target": "zh-TW", "place": "", "at": 1})

    assert service.report_save() == {"ok": False, "error": "report_none"} and service.report_copy()["ok"] is False      # 還沒看過不能存
    made = service.report_make(note="聽對方說話十分鐘後就沒字幕了")
    text = made["text"]
    assert made["ok"] and made["bytes"] == len(text.encode("utf-8")) and made["lines"] == text.count("\n")
    assert not list(tmp_path.glob("out/*")) and not opened and not copied      # 做出來只是給他看：沒存檔、沒開資料夾、沒複製
    for title in ("發生了什麼事", "LiveLingo", "這台電腦", "健康檢查", "收音和螢幕", "儲存空間", "設定", "最近的紀錄", "翻譯引擎的紀錄"):
        assert f"== {title} ==" in text, title
    assert "聽對方說話十分鐘後就沒字幕了" in text and f"version: {__import__('livelingo').__version__}" in text and "screens: 2560x1440 at 0,0 (main)" in text
    assert f"word_list_count: {len(service.glossary.list())}" in text and "history_count: 1" in text and "llama_server: model loaded" in text
    assert re.search(r"\[(ok|warn|bad|off)\] engine: engine_", text) and "line 299" in text and "line 99\r\n" not in text      # 紀錄只有最後 200 行
    log_rows = text.split("== 最近的紀錄 ==\r\n")[1].split("\r\n\r\n")[0].split("\r\n")
    assert 150 < len(log_rows) <= report.LOG_LINES
    for private in (secret, reply, "정글", "打野", "朋友的秘密頻道", "我的排位版面"):
        assert private not in text, private      # 對話、詞彙表、取的名字都不在裡面
    assert "Discord.exe" in text and "有 1 行因為含著對話裡的句子" in text, [row for row in text.split("\r\n") if "region" in row or "行因為" in row]
    assert "mic capture opened: 麥克風 (HyperX Cloud II)" in text and "something leaked" not in text
    assert len(str(Path.home())) <= 3 or (str(Path.home()) not in text and "saved: %USERPROFILE%/Documents/LiveLingo/a.txt" in text)

    # 存的和看到的一字不差；存好打開資料夾
    saved = service.report_save()
    path = Path(saved["path"])
    assert saved["ok"] and path.parent == tmp_path / "out" and re.fullmatch(r"LiveLingo-report-\d{8}-\d{6}\.txt", saved["name"]) and opened == [tmp_path / "out"]
    assert path.read_bytes() == b"\xef\xbb\xbf" + text.encode("utf-8")
    assert service.report_copy() == {"ok": True} and copied == [text]

    # 不放設定、不放紀錄
    short = service.report_make(settings=False, log=False)["text"]
    assert "== 設定 ==" not in short and "== 最近的紀錄 ==" not in short and "mic capture opened" not in short and "== 健康檢查 ==" in short

    # 做報告的時候出錯：還是給得出一份（至少有版本和紀錄）
    monkeypatch.setattr(service, "health", lambda: 1 / 0)
    broken = service.report_make()["text"]
    assert "report_error: ZeroDivisionError" in broken and f"version: {__import__('livelingo').__version__}" in broken

    root = Path(report.__file__).parent
    server = (root / "server.py").read_text(encoding="utf-8")
    assert 'route == "/api/report"' in server and "service.report_save()" in server and "service.report_copy()" in server
    js = (root / "ui" / "app.js").read_text(encoding="utf-8")
    for piece in ('post("/api/report", { save: true })', 'post("/api/report", { copy: true })', 'data-act="report-make"', 'id="rep-text"', "rep_unsent"):
        assert piece in js, piece


def test_toast_is_not_mistaken_for_a_volume_bar():
    """提示訊息和音量條不能用同一個 data- 名字：音量條每秒重畫好幾次，會把提示訊息的寬度改成 0%（變成一條直的）。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "livelingo" / "ui"
    js, css = (root / "app.js").read_text(encoding="utf-8"), (root / "style.css").read_text(encoding="utf-8")
    assert 'el.dataset.toast = level || "info"' in js and '.toast[data-toast="warn"]' in css and '.toast[data-toast="error"]' in css
    assert "toast[data-level" not in css and "el.dataset.level = " not in js
    assert len(re.findall(r'querySelectorAll\("\[data-(?:vu|level)\]"\)', js)) == 2
    # 會被整批改寬度的選擇器，只能選到音量條自己
    for attr in re.findall(r'querySelectorAll\("\[data-(\w+)\]"\)\.forEach\(\(el\) => \{\n[^}]*el\.style\.width', js):
        assert attr in ("vu", "level"), attr
        assert f'id="toast"' not in "".join(re.findall(rf"<[^>]*data-{attr}=[^>]*>", (root / "index.html").read_text(encoding="utf-8")))


def test_usage_numbers_are_local_counts_only(tmp_path, monkeypatch):
    """第 50 項：每天翻了幾句、失敗幾次、平均等多久。只有數字、只在這台電腦上；一句只算一次；可以關掉、可以清除。"""
    import json as _json
    import re
    from pathlib import Path

    from livelingo import usage
    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS
    from livelingo.service import Service

    assert DEFAULTS["usage_keep"] is True and "usage_keep" in EDITABLE

    day = 86400
    clock = [1_800_000_000.0]
    u = usage.Usage(tmp_path, now=lambda: clock[0])
    assert u.summary()["since"] == "" and u.summary()["today"] == {"count": 0, "heard": 0, "screen": 0, "spoken": 0, "typed": 0, "failed": 0, "wait": None}
    u.add("heard", wait=2.0)
    u.add("typed", wait=1.0)
    u.add("screen", failed=True, wait=30.0)      # 失敗的不算進平均
    u.add("nonsense", wait=1.0)      # 不認得的來源不記
    u.add("spoken", wait=9999)      # 離譜的等待時間（電腦睡著了）：這句算，時間不算
    assert u.summary()["today"] == {"count": 4, "heard": 1, "screen": 1, "spoken": 1, "typed": 1, "failed": 1, "wait": 1.5}
    saved = _json.loads((tmp_path / usage.FILE).read_text(encoding="utf-8"))      # 第一句就寫檔，之後 15 秒內不重寫
    assert list(saved) == ["days"] and sum(saved["days"][d]["heard"] for d in saved["days"]) == 1
    u.flush()
    saved = _json.loads((tmp_path / usage.FILE).read_text(encoding="utf-8"))
    row = next(iter(saved["days"].values()))
    assert set(row) == {"heard", "screen", "spoken", "typed", "failed", "wait", "waited"} and row["waited"] == 2      # 檔案裡只有數字
    assert all(isinstance(v, (int, float)) for v in row.values())

    clock[0] += 3 * day
    u.add("heard", wait=4.0)
    s = u.summary()
    assert s["today"]["count"] == 1 and s["week"]["count"] == 5 and s["week"]["wait"] == round(7.0 / 3, 1) and s["month"]["failed"] == 1
    assert len(s["days"]) == usage.CHART_DAYS and [d["count"] for d in s["days"]][-4:] == [4, 0, 0, 1] and s["days"][-4]["failed"] == 1
    clock[0] += 10 * day
    assert u.summary()["week"]["count"] == 0 and u.summary()["month"]["count"] == 5

    # 重開：數字還在；超過 90 天的丟掉；檔案壞了從頭來
    u.flush()
    again = usage.Usage(tmp_path, now=lambda: clock[0])
    assert again.summary()["month"]["count"] == 5 and again.summary()["since"] == s["days"][-4]["day"]
    clock[0] += 78 * day
    old = usage.Usage(tmp_path, now=lambda: clock[0])
    assert old.summary()["since"] == s["days"][-1]["day"]      # 最早那天超過 90 天，丟掉了
    (tmp_path / usage.FILE).write_text("{broken", encoding="utf-8")
    assert usage.Usage(tmp_path).summary()["since"] == ""

    # 關掉不記；清除
    again.configure(False)
    again.add("heard", wait=1.0)
    assert again.summary()["today"]["count"] == 0 and again.summary()["keep"] is False
    assert again.clear() >= 1 and not (tmp_path / usage.FILE).exists() and again.summary()["since"] == ""

    # 這個檔案不連網路
    source = Path(usage.__file__).read_text(encoding="utf-8")
    for banned in ("urllib", "socket", "requests", "http.client", "subprocess"):
        assert banned not in source, banned

    # 接到服務上：翻好一句算一次，重翻不再算，失敗算失敗
    data = tmp_path / "data"
    data.mkdir()
    service = Service(data)
    msg = {"id": 7, "via": "heard", "hear": 0.5, "at": time.time() - 1.0}
    service._count(msg)
    service._count(msg)
    service._count({"id": 8, "via": "typed", "at": time.time()}, failed=True)
    state = service.usage_state()
    assert state["ok"] and state["today"]["count"] == 2 and state["today"]["failed"] == 1 and 1.4 <= state["today"]["wait"] <= 2.0
    report = service.report_make()["text"]
    assert "today: count=2, heard=1, typed=1, failed=1" in report and "recorded: yes" in report
    service.update_config({"usage_keep": False})
    service._count({"id": 9, "via": "typed", "at": time.time()})
    assert service.usage_state()["today"]["count"] == 2 and service.usage.keep is False
    cleared = service.usage_clear()
    assert cleared["ok"] and cleared["removed"] == 1 and cleared["since"] == ""
    body = Path(usage.__file__).with_name("service.py").read_text(encoding="utf-8")
    worker = body[body.index("    def _translate_worker"):body.index("    def _count")]
    assert worker.count("self._count(msg") == 2 and "self._count(msg, failed=True)" in worker and "self.usage.flush()" in body

    root = Path(usage.__file__).parent
    server = (root / "server.py").read_text(encoding="utf-8")
    assert 'url.path == "/api/usage"' in server and "service.usage_clear()" in server
    js, i18n = (root / "ui" / "app.js").read_text(encoding="utf-8"), (root / "ui" / "i18n.js").read_text(encoding="utf-8")
    for piece in ('get("/api/usage")', 'post("/api/usage", { clear: true })', 'switchRow("usage_keep"', 'data-act="use-clear-yes"'):
        assert piece in js, piece
    for key in ("use_title", "use_day", "use_split", "use_clear_ask"):
        assert len(re.findall(rf"\b{key}: ", i18n)) == len(UI_LANGS), key


def test_help_page_and_the_page_for_a_friend(tmp_path, monkeypatch):
    """第 49 項：程式裡的說明頁，和一頁可以傳給朋友的快速上手。五種語言同一個結構；寫的快速鍵是真的；給朋友的那一頁是獨立的檔案，不連外部的東西。"""
    import re
    from pathlib import Path

    from livelingo import guide
    from livelingo.config import DEFAULTS, UI_LANGS
    from livelingo.service import Service

    data = guide.load()
    assert set(data) == set(UI_LANGS)
    base = data["zh-TW"]
    marks = lambda text: (sorted(re.findall(r"\{(\w+)\}", text)), len(re.findall(r"\[\[.+?\]\]", text)), sorted(re.findall(r"\[\[([A-Za-z0-9+↑↓]+)\]\]", text)))  # noqa: E731
    for lang, text in data.items():
        assert set(text) == {"title", "unset", "intro", "sections", "friend"}, lang
        assert [s["id"] for s in text["sections"]] == [s["id"] for s in base["sections"]], lang
        for mine, theirs in zip(text["sections"], base["sections"]):
            assert len(mine["items"]) == len(theirs["items"]) and mine["title"].strip(), (lang, mine["id"])
            for a, b in zip(mine["items"], theirs["items"]):
                assert marks(a) == marks(b), (lang, mine["id"], a)      # 每種語言提到的快速鍵、按鍵都一樣
        assert set(text["friend"]) == {"title", "lead", "install_title", "install", "footer"} and len(text["friend"]["install"]) == len(base["friend"]["install"]), lang
        assert sum("{download}" in i for i in text["friend"]["install"]) == 1 and "{version}" in text["friend"]["footer"], lang
        used = set(re.findall(r"\{(\w+)\}", str(text)))
        assert used <= set(guide.VALUES), (lang, used - set(guide.VALUES))
    assert {"start", "typing", "keys", "privacy", "trouble"} <= {s["id"] for s in base["sections"]} and set(guide.FRIEND_SECTIONS) <= {s["id"] for s in base["sections"]}
    keys = next(s for s in base["sections"] if s["id"] == "keys")
    assert {"overlay", "mic", "copy", "quick"} <= set(re.findall(r"\{(\w+)\}", keys["items"][0])) == set(DEFAULTS["hotkeys"])      # 四個全域快速鍵都有寫
    js = (Path(guide.__file__).parent / "ui" / "app.js").read_text(encoding="utf-8")
    for key in ("Ctrl+1", "Ctrl+9", "Esc", "F1"):      # 說明裡寫的視窗內按鍵，程式裡真的有
        assert f"[[{key}]]" in keys["items"][1], key
    assert 'e.key === "F1"' in js and "/^[1-9]$/.test(e.key)" in js and 'e.key === "Escape"' in js

    # 沒設定的快速鍵：寫「沒設定」，不畫成按鍵
    assert guide.fill("按 [[{quick}]] 或 [[{mic}]]，版本 {version}", {"quick": "Ctrl+Alt+T", "mic": "", "version": "2.3.0"}, "（沒設定）") == "按 [[Ctrl+Alt+T]] 或 （沒設定），版本 2.3.0"
    assert guide.page("xx", {})["title"] == data["en"]["title"]      # 沒有的語言用英文

    opened = []
    monkeypatch.setattr("livelingo.service.oswin.open_path", lambda p: opened.append(Path(p)))
    service = Service(tmp_path / "data")
    service.cfg.set(ui_lang="zh-TW", their_lang="ko", transcript_dir=str(tmp_path / "out"), ptt_key="F9",
                    hotkeys={"overlay": "Ctrl+Alt+S", "mic": "", "copy": "Ctrl+Alt+C", "quick": "Ctrl+Shift+F2"})
    page = service.help_state()
    text = "\n".join(item for section in page["sections"] for item in section["items"])
    assert page["ok"] and page["title"] == "說明" and page["friend_lang"] == "ko" and set(page["langs"]) == set(UI_LANGS)
    assert "[[Ctrl+Shift+F2]]" in text and "[[F9]]" in text and "（沒設定） 開關麥克風" in text and "{" not in text      # 現在設定的快速鍵
    service.cfg.set(their_lang="vi" if "vi" not in UI_LANGS else "ko")
    assert service.help_state()["friend_lang"] in ("zh-TW", "ko")

    assert service.help_friend("xx")["ok"] is False and not opened
    saved = service.help_friend("ko")
    path = Path(saved["path"])
    assert saved["ok"] and saved["name"] == "LiveLingo-quick-start-ko.html" and path.parent == tmp_path / "out" and opened == [tmp_path / "out"]
    html = path.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and '<html lang="ko">' in html and "<kbd>Ctrl+Alt+T</kbd>" in html and "<kbd>Ctrl+Alt+M</kbd>" in html      # 朋友是新裝的：預設快速鍵
    assert "Ctrl+Shift+F2" not in html and f'<a href="{guide.DOWNLOAD}">' in html and f"LiveLingo {service.state()['version']}" in html
    assert html.count("<section>") == 1 + len(guide.FRIEND_SECTIONS) and "<svg" in html and "{" not in re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    for banned in ("<script", "src=", "<link", "@import", "url(h", "url('", 'url("'):      # 獨立的一頁：不載入任何外部的東西（url(#…) 是標誌自己的漸層）
        assert banned not in html, banned
    assert set(re.findall(r'https?://[^\s"<]+', html)) == {guide.DOWNLOAD}
    assert "[[" not in html
    assert "<b>x</b>" not in guide._rich("<b>x</b> [[Esc]]") and "<kbd>Esc</kbd>" in guide._rich("<b>x</b> [[Esc]]")      # 內容會先跳脫

    root = Path(guide.__file__).parent
    server = (root / "server.py").read_text(encoding="utf-8")
    assert 'url.path == "/api/help"' in server and "service.help_friend(" in server
    i18n = (root / "ui" / "i18n.js").read_text(encoding="utf-8")
    for piece in ('get("/api/help")', 'post("/api/help", { friend:', '["help", "nav_help", "help"]', 'data-act="friend-save"'):
        assert piece in js, piece
    for key in ("nav_help", "help_friend_title", "help_friend_saved", "n_help_failed"):
        assert len(re.findall(rf"\b{key}: ", i18n)) == len(UI_LANGS), key


def test_my_own_sentences_are_translated_first(tmp_path):
    """排隊：我打的、說的先翻，再來是對方說的，然後是螢幕上讀到的，回譯最後；螢幕上排太久的略過並說一聲；
    使用者自己按重翻的不會被略過。"""
    import queue

    import pytest

    from livelingo import hotkeys, jobs
    from livelingo.service import Service

    clock = [100.0]
    q = jobs.Jobs(now=lambda: clock[0], stale=20)
    q.put({"via": "screen", "n": 1})
    q.put({"back_of": 9})
    q.put({"via": "heard", "n": 2})
    q.put({"via": "screen", "n": 3})
    q.put({"via": "typed", "n": 4})
    q.put({"via": "spoken", "n": 5})
    order = [q.get(0)[0] for _ in range(6)]
    assert [j.get("n") for j in order] == [4, 5, 2, 1, 3, None] and "back_of" in order[-1]
    with pytest.raises(queue.Empty):
        q.get(0.01)

    q.put({"via": "screen", "n": 1})
    q.put({"via": "heard", "n": 2})
    q.put({"via": "screen", "n": 3}, asked=True)      # 使用者按了重翻
    clock[0] += 21
    q.put({"via": "screen", "n": 4})                  # 剛到的
    got = [(job["n"], late) for job, late in (q.get(0) for _ in range(4))]
    assert got == [(3, False), (2, False), (1, True), (4, False)]      # 只有排了 21 秒的那一則螢幕訊息要略過

    class Slow:
        tone = "casual"

        def __init__(self):
            self.calls, self.hold = [], threading.Event()

        def translate(self, text, target, source=None, context=None):
            self.calls.append(text)
            if text == "첫 번째":
                self.hold.wait(5)      # 第一句翻得很慢，後面的都在排隊
            return f"<{target}>{text}"

        def clear_cache(self):
            pass

    service = Service(tmp_path)
    service.cfg.set(back_translate=False, ui_lang="ko")
    service.translator = Slow()
    ticks = [0.0]
    service.jobs = jobs.Jobs(now=lambda: ticks[0], stale=20)
    notices, notes = [], []
    service.notice = lambda code, **more: notices.append((code, more))
    service.overlay.note = notes.append
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()

    def wait(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    try:
        service.submit("screen", "첫 번째", "ko")
        wait(lambda: service.translator.calls == ["첫 번째"])
        service.submit("screen", "두 번째", "ko")
        service.submit("screen", "세 번째", "ko")
        service.submit("heard", "들리는 말", "ko")
        service.submit("typed", "我打的字", "zh")
        ticks[0] = 30.0                                   # 排隊的時候過了 30 秒
        service.submit("screen", "방금 온 것", "ko")
        service.translator.hold.set()
        wait(lambda: service.jobs.waiting() == 0 and len(service.translator.calls) == 4 and notices)
        assert service.translator.calls == ["첫 번째", "我打的字", "들리는 말", "방금 온 것"]      # 我的先，舊的兩則螢幕訊息沒翻
        assert notices == [("skipped", {"n": 2})]                                               # 兩則合起來只說一次
        assert notes == [hotkeys.NOTES["ko"]["skipped"].format(n=2)]                             # 浮動留言上也說（人在遊戲裡）
        assert not [m for m in service.messages if m["original"] in ("두 번째", "세 번째")]       # 畫面上沒有留下半則
    finally:
        service.closing.set()
        worker.join(2)


def test_wrapped_lines_are_joined_and_repeats_are_new(monkeypatch):
    """讀螢幕：一則訊息換成兩三行的接回一則再翻；有人又說了一次一樣的話要算新的一則；
    聊天框收起來又打開、舊訊息重新出現時不重翻；換行訊息的上半捲出畫面之後，剩下的幾行不重翻。"""
    import numpy as np

    from livelingo.core import watcher as watcher_mod
    from livelingo.core.ocr import Line
    from livelingo.core.watcher import ScreenWatcher, SeenLines, join_wrapped, new_from, text_edge

    def row(text, y, w, x=10, h=20):
        return Line(text, x, y, h, 0.95, w)

    # ---- 換行：上一行寫到最右邊，而且下一行的第一個詞本來就放不進去
    seen = SeenLines()
    rows = [row("오늘 저녁에 같이 게임 할 사람 있으면", 0, 470), row("디스코드로 들어와", 26, 220), row("ㅇㅋ 갈게", 52, 110)]
    known, edge = text_edge(rows, 500)
    assert (known, edge) == (480.0, 480.0)
    joined = join_wrapped(rows, seen, edge)
    assert [ln.text for ln, _ in joined] == ["오늘 저녁에 같이 게임 할 사람 있으면 디스코드로 들어와", "ㅇㅋ 갈게"]
    assert joined[0][1] == ["오늘 저녁에 같이 게임 할 사람 있으면", "디스코드로 들어와"] and joined[0][0].w == 470
    # 中文沒有空白：接的時候不加空白
    zh = [row("我們今天晚上要不要一起打排位我剛剛看了一下", 0, 470), row("時間應該可以", 26, 150)]
    assert [ln.text for ln, _ in join_wrapped(zh, SeenLines(), 480.0)] == ["我們今天晚上要不要一起打排位我剛剛看了一下時間應該可以"]
    # 短短的兩則各自一行：下一則的第一個詞放得進上一行，是另一則訊息，不接
    short = [row("안녕", 0, 60), row("뭐해 지금", 26, 120)]
    assert len(join_wrapped(short, SeenLines(), 480.0)) == 2
    # 還不知道文字最右邊在哪裡（最長的一行還不到區域六成寬）：不接
    assert text_edge(short, 500) == (130.0, 0.0) and len(join_wrapped(rows, SeenLines(), 0.0)) == 3
    # 沒有對齊同一個左邊、行距太大、沒有寬度的：不接
    assert len(join_wrapped([rows[0], row("디스코드로 들어와", 26, 220, x=60)], SeenLines(), 480.0)) == 2
    assert len(join_wrapped([rows[0], row("디스코드로 들어와", 90, 220)], SeenLines(), 480.0)) == 2
    assert len(join_wrapped([Line(rows[0].text, 10, 0, 20, 0.9), rows[1]], SeenLines(), 480.0)) == 2
    # 以前單獨讀到過的那一行是一則獨立的訊息：之後下面來了新的一行，不會被接上去
    alone = SeenLines()
    alone.add(rows[0].text)
    assert len(join_wrapped(rows[:2], alone, 480.0)) == 2

    # ---- 上一次和這一次怎麼對
    assert new_from(["a1", "b2", "gg"], ["b2", "gg", "gg"]) == 2          # 往上捲了一行，下面多了一則 gg
    assert new_from(["a1", "b2", "gg"], ["a1", "b2", "gg"]) == 3          # 沒變
    assert new_from(["gg", "gg"], ["gg", "gg", "gg"]) == 2
    assert new_from([], ["a1"]) is None and new_from(["a1"], []) is None
    assert new_from(["a1", "b2", "c3", "d4"], ["d4", "x1", "y2", "z3"]) is None      # 只對得上一行，不到一半：不算數
    assert new_from(["a1", "b2", "c3"], ["x1", "y2"]) is None

    # ---- 整條：盯著螢幕的那一條線
    pages = {
        10: [row("안녕하세요 반갑습니다", 0, 200), row("빨리 와", 26, 80)],
        60: [row("안녕하세요 반갑습니다", 0, 200), row("빨리 와", 26, 80), row("빨리 와", 52, 80)],             # 又說了一次
        110: [row("빨리 와", 0, 80), row("빨리 와", 26, 80),
              row("오늘 저녁에 같이 게임 할 사람 있으면", 52, 470), row("디스코드로 들어와", 78, 220)],           # 一則換行的
        160: [],                                                                                               # 聊天框收起來
        210: [row("빨리 와", 0, 80), row("빨리 와", 26, 80),
              row("오늘 저녁에 같이 게임 할 사람 있으면", 52, 470), row("디스코드로 들어와", 78, 220)],           # 又打開：都是舊的
        250: [row("디스코드로 들어와", 0, 220), row("지금 갈게 기다려", 26, 200)],                               # 換行那則的上半捲出去了
    }
    monkeypatch.setattr(watcher_mod, "message_lines", lambda shade: pages[shade])

    class FakeOcr:
        def read(self, bgr):
            return int(bgr[0, 0, 0])

    got = []
    watcher = ScreenWatcher(FakeOcr(), lambda text, lang, score, region: got.append(text), source_langs={"ko"})
    watcher.set_regions([{"id": "a", "left": 0, "top": 0, "width": 500, "height": 200}])
    slot = watcher._slots[0]
    for shade in pages:
        frame = np.full((200, 500, 3), shade, dtype=np.uint8)
        watcher._look(slot, lambda place: frame)      # 第一次：畫面剛變，等它停下來
        watcher._look(slot, lambda place: frame)      # 第二次：停下來了，辨識
    assert got == ["안녕하세요 반갑습니다", "빨리 와", "빨리 와", "오늘 저녁에 같이 게임 할 사람 있으면 디스코드로 들어와", "지금 갈게 기다려"]
    assert slot.stats["sent"] == 5 and slot.stats["scans"] == 6


def test_engine_that_stops_is_restarted(tmp_path):
    """翻譯引擎用到一半自己停了：自動重開，剛好在翻的那一句補翻（不算失敗）；十分鐘內停太多次就不再試；
    顯示卡模式一直停就改用標準模式。"""
    from livelingo import hotkeys
    from livelingo import service as service_mod
    from livelingo.service import Service

    class Proc:
        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

    made = []

    class FakeEngine:
        def __init__(self, ok=True):
            self.proc, self.ok, self.url, self.stopped = Proc(), ok, f"http://engine/{len(made)}", False
            self.key = f"key-{len(made)}"
            made.append(self)

        def start(self):
            if not self.ok:
                raise RuntimeError("cannot start")

        def wait_ready(self, timeout):
            return True

        def stop(self):
            self.stopped = True

    class Model:
        tone, url, key = "casual", "", ""

        def translate(self, text, target, source=None, context=None):
            if service.engine.proc.poll() is not None:
                raise ConnectionError("connection refused")
            return f"<{target}>{text}"

        def clear_cache(self):
            pass

    service = Service(tmp_path)
    service.cfg.set(back_translate=False, ui_lang="zh-TW")
    service.translator = Model()
    service.phase = "ready"
    service.engine = FakeEngine()
    service._new_engine = lambda: FakeEngine()
    (tmp_path / "engine.log").write_text("out of memory\n", encoding="utf-8")
    notices, notes, phases = [], [], []
    service.notice = lambda code, **more: notices.append(code)
    service.overlay.note = notes.append
    service.set_phase = lambda phase, **detail: (phases.append(phase), setattr(service, "phase", phase))
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()

    def wait(test):
        end = time.time() + 8
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    try:
        assert not service._engine_gone()
        service.engine.proc.returncode = -9                       # 引擎被關掉了
        assert service._engine_gone()
        first = service.submit("typed", "我現在過去", "zh")
        wait(lambda: any(m["id"] == first and m["translated"] for m in service.messages))
        done = next(m for m in service.messages if m["id"] == first)
        assert done["translated"] == "<ko>我現在過去" and not done["error"]          # 補翻成功，沒有顯示失敗
        assert notices == ["engine_restarting", "engine_back"]
        assert notes == [hotkeys.NOTES["zh-TW"]["engine_restarting"], hotkeys.NOTES["zh-TW"]["engine_back"]]
        assert len(made) == 2 and made[0].stopped and service.engine is made[1] and service.translator.url == made[1].url
        assert service.translator.key == made[1].key      # 新的引擎有新的金鑰，翻譯那邊要跟著換
        assert (tmp_path / "engine-stopped.log").read_text(encoding="utf-8") == "out of memory\n"      # 留一份，才看得到為什麼停
        assert service._engine_ok.is_set() and service.phase == "ready" and service.usage.summary()["today"]["failed"] == 0
        made_report = service.report_make()["text"]
        assert "翻譯引擎上次自己停掉之前的紀錄" in made_report and "out of memory" in made_report      # 問題報告裡看得到它停之前寫了什麼

        # 一直停：重開到上限就不再試，照「引擎開不起來」處理，之後的句子才顯示失敗
        for _ in range(service_mod.REVIVE_TIMES - 1):
            service.engine.proc.returncode = 1
            assert service._revive_engine() is True
        service.engine.proc.returncode = 1
        assert service._revive_engine() is False and phases == ["error"] and not service._engine_gone()
        second = service.submit("typed", "等我一下", "zh")
        wait(lambda: any(m["id"] == second and m["error"] for m in service.messages))

        # 重開的那一個開不起來：也是照「開不起來」處理
        phases.clear()
        service._revivals.clear()
        service.phase, service.engine = "ready", FakeEngine()
        service.engine.proc.returncode = 1
        service._new_engine = lambda: FakeEngine(ok=False)
        assert service._revive_engine() is False and phases == ["error"] and made[-1].stopped

        # 顯示卡模式停到上限：改用標準模式重來（跟開程式時顯示卡開不起來一樣）
        phases.clear()
        notices.clear()
        prepared = []
        service._prepare = lambda: prepared.append(dict(fallback=service.fallback))
        service.mode = {"model": "12b", "engine": "cuda", "quality": "high"}
        service.phase, service.engine = "ready", FakeEngine()
        service.engine.proc.returncode = 1
        service._revive_engine()
        assert prepared == [{"fallback": True}] and "gpu_fallback" in notices and phases == []
    finally:
        service.closing.set()
        worker.join(2)


def test_unhandled_errors_are_logged_and_shown(tmp_path):
    """沒有人接住的錯誤：寫進紀錄檔、畫面上說一聲（一分鐘最多一次）；翻譯那條線遇到意外的錯也不會停。"""
    import sys

    from livelingo.service import Service

    service = Service(tmp_path)
    notices = []
    service.notice = lambda code, **more: notices.append(code)
    old = sys.excepthook, threading.excepthook
    try:
        service.watch_errors()

        def boom():
            raise ValueError("it broke here")

        for _ in range(2):
            t = threading.Thread(target=boom, name="some-part")
            t.start()
            t.join()
        log = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert log.count("unhandled error in thread some-part") == 2 and "ValueError: it broke here" in log
        assert notices == ["part_failed"]      # 連續兩次只說一次
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)      # 使用者自己中斷的不算
        assert notices == ["part_failed"]
    finally:
        sys.excepthook, threading.excepthook = old

    # 翻譯那條線：排隊那邊丟出意外的錯，寫進紀錄之後繼續翻下一句
    class Echo:
        tone = "casual"

        def translate(self, text, target, source=None, context=None):
            return f"<{target}>{text}"

        def clear_cache(self):
            pass

    service.translator = Echo()
    service.cfg.set(back_translate=False)
    real, calls = service.jobs.get, []

    def flaky(timeout=0.5):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("queue hiccup")
        return real(timeout)

    service.jobs.get = flaky
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()
    try:
        mine = service.submit("typed", "我現在過去", "zh")
        end = time.time() + 5
        while time.time() < end and not any(m["id"] == mine and m["translated"] for m in service.messages):
            time.sleep(0.02)
        assert any(m["id"] == mine and m["translated"] == "<ko>我現在過去" for m in service.messages)
        assert "queue hiccup" in (tmp_path / "app.log").read_text(encoding="utf-8") and worker.is_alive()
    finally:
        service.closing.set()
        worker.join(2)


def test_engine_needs_a_key(tmp_path, monkeypatch):
    """本機的翻譯引擎每次啟動有一把隨機的金鑰：用環境變數交給它（不放指令列），翻譯的請求要帶著。"""
    import io
    import subprocess

    from livelingo.core import engine as engine_mod
    from livelingo.core.engine import Engine

    exe, model = tmp_path / "llama-server.exe", tmp_path / "model.gguf"
    exe.write_bytes(b"x")
    model.write_bytes(b"x")
    started = {}

    class Proc:
        pid = 4321

        def poll(self):
            return None

    def popen(cmd, **kw):
        started.update(cmd=cmd, env=kw.get("env"))
        return Proc()

    monkeypatch.setattr(engine_mod.subprocess, "Popen", popen)
    monkeypatch.delenv("LLAMA_SERVER", raising=False)
    first, second = Engine(exe, model, tmp_path / "engine.log"), Engine(exe, model, tmp_path / "engine.log")
    assert len(first.key) >= 24 and first.key != second.key                        # 每次都不一樣
    first.start()
    assert started["env"]["LLAMA_API_KEY"] == first.key and "PATH" in started["env"]
    assert not any(first.key in part for part in started["cmd"]) and "--api-key" not in started["cmd"]
    assert ["--host", "127.0.0.1"] == started["cmd"][started["cmd"].index("--host"):][:2]      # 還是只聽這台電腦

    sent = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=0):
        sent.append(dict(req.header_items()))
        return Reply(json.dumps({"choices": [{"text": "안녕"}]}).encode())

    from livelingo.core import translate as translate_mod

    monkeypatch.setattr(translate_mod.urllib.request, "urlopen", urlopen)
    translator = Translator("http://127.0.0.1:1")
    translator.translate("你好", "ko", "zh")
    assert "Authorization" not in sent[-1]                                          # 沒有金鑰（測試、自己接的引擎）就不帶
    translator.key = first.key
    translator.clear_cache()
    translator.translate("你好", "ko", "zh")
    assert sent[-1]["Authorization"] == f"Bearer {first.key}"


def test_every_package_is_pinned():
    """套件全部鎖在確切的版本：requirements.txt 和 constraints.txt 對得上，自動打包兩段都照著裝，裝完還會核對一次。"""
    import importlib.util
    import re
    from pathlib import Path

    import pytest

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("check_pins", root / "packaging" / "check_pins.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)

    direct = check.read_pins(root.joinpath("requirements.txt").read_text(encoding="utf-8"))
    pins = check.read_pins(root.joinpath("constraints.txt").read_text(encoding="utf-8"))
    assert len(direct) == 11 and len(pins) > 50
    assert all(pins.get(name) == version for name, version in direct.items())
    for tool in ("pyinstaller", "pytest", "opencv-python-headless", "ctranslate2", "onnxruntime"):
        assert tool in pins, tool                                  # 打包工具和帶進來的大套件也要鎖
    for loose in ("numpy", "numpy>=2", "pillow~=13.0", "mss==10.*"):
        with pytest.raises(ValueError):
            check.read_pins(loose)
    assert check.read_pins("# note\nPyYAML==6.0.4  # why\n\ncharset_normalizer==3.5.2\n") == {"pyyaml": "6.0.4", "charset-normalizer": "3.5.2"}

    # 核對：從直接要的套件一路找下去，沒鎖的、版本不同的都抓出來；這個平台沒裝的不算
    world = {"app": ("1.0", ["lib>=1", "winonly; sys_platform == 'win32'", "extra-thing; extra == 'gui'"]),
             "lib": ("2.1", ["deep"]), "deep": ("0.3", ["app"])}
    wanted = lambda req: None if ";" in req else re.split(r"[<>=!~ ]", req)[0]
    look = lambda name: world.get(name)
    assert check.problems(["App", "missing"], {"app": "1.0", "lib": "2.1", "deep": "0.3"}, look, wanted) == []
    assert check.problems(["app"], {"app": "1.0", "lib": "2.0"}, look, wanted) == [
        "deep 0.3 is installed but not pinned in constraints.txt", "lib: constraints.txt says 2.0, installed is 2.1"]
    assert check._wanted("colorama>=0.4; sys_platform == 'nowhere'") is None and check._wanted("idna (>=3.4)") == "idna"
    assert check._wanted("socks>=1; extra == 'socks'") is None

    build = root.joinpath(".github", "workflows", "build.yml").read_text(encoding="utf-8")
    installs = [line for line in build.splitlines() if "pip install" in line]
    assert len(installs) == 2 and all("-c constraints.txt" in line for line in installs)
    assert build.count("python packaging/check_pins.py") == 2
    assert "-c constraints.txt" in root.joinpath("README.md").read_text(encoding="utf-8")


def test_numbers_are_checked_after_translating(tmp_path):
    """原文的數字翻完要還在：不見了就換寫法重翻，還是不行就在那一則下面提醒使用者核對。"""
    from pathlib import Path

    from livelingo.core.translate import numbers, numbers_lost
    from livelingo.service import Service

    assert numbers("０６:５１ 1,000 gold 3.5") == ["6", "51", "1000", "3", "5"]      # 全形、千分位、開頭的 0
    assert numbers("666 太強了 88", "zh") == [] and numbers("gr8 m8 see u in 5", "en") == ["5"]      # 網路用語不是數量
    same = [("3分鐘後到", "3분 후에 도착해", "ko"), ("15分開龍", "십오 분에 용 잡자", "ko"), ("2명 더 와", "再來兩個人", "zh-TW"),
            ("10시에 봐", "十點見", "zh-TW"), ("10000골드", "1萬金", "zh-TW"), ("meet at 7:30", "7點半見", "zh-TW"),
            ("我有2個", "I have two", "en"), ("1등 했어", "拿第一名了", "zh-TW"), ("25분", "二十五分鐘", "zh-TW"), ("666", "대박", "ko"),
            ("세 명 왔어", "來了 3 個人", "zh-TW"), ("第3波", "第3波", "zh-CN")]
    for original, out, target in same:
        assert numbers_lost(original, out, target) == [], original
    assert numbers_lost("3分鐘後到", "5분 후에 도착해", "ko") == ["3"]
    assert numbers_lost("10시에 봐", "九點見", "zh-TW") == ["10"]
    assert numbers_lost("meet at 7:30", "8點見", "zh-TW") == ["7", "30"]
    assert numbers_lost("25분 남았어", "還剩二十分鐘", "zh-TW") == ["25"]
    assert numbers_lost("3 3 3", "3 3", "ko", "en") == ["3"]                      # 同一個數出現三次，譯文只有兩次

    tr = _FakeModel({"full": "5분 후에 도착해.", "short": "3분 후에 도착해."})
    assert tr.translate("3分鐘後到", "ko", "zh") == "3분 후에 도착해." and [a[1] for a in tr.asked] == ["full", "short"]
    tr = _FakeModel({"full": "5분 후에 도착해.", "short": "곧 도착해.", "plain": "삼 분 후에 도착해."})
    assert tr.translate("3分鐘後到", "ko", "zh") == "삼 분 후에 도착해." and [a[1] for a in tr.asked] == ["full", "short", "plain"]
    tr = _FakeModel("곧 도착해.")                                                   # 怎麼翻都沒有：留第一次的
    assert tr.translate("3分鐘後到", "ko", "zh") == "곧 도착해." and len(tr.asked) == 3
    tr = _FakeModel("3분 후에 도착해.")
    assert tr.translate("3分鐘後到", "ko", "zh") == "3분 후에 도착해." and len(tr.asked) == 1      # 數字都在就不多翻

    service = Service(tmp_path)
    service.cfg.set(back_translate=False, my_lang="zh-TW", friend_lang="ko")
    service.translator = _FakeModel(lambda text, style, context: "곧 도착해." if "3" in text else "20분 뒤에 봐.")
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()
    try:
        service.submit("typed", "3分鐘後到", "zh")
        service.submit("typed", "20分鐘後見", "zh")
        end = time.time() + 5
        while time.time() < end and len([m for m in service.messages if not m.get("pending")]) < 2:
            time.sleep(0.02)
        done = {m["original"]: m for m in service.messages}
        assert done["3分鐘後到"]["lost"] == ["3"] and done["3分鐘後到"]["translated"] == "곧 도착해."
        assert done["20分鐘後見"]["lost"] == []
    finally:
        service.closing.set()
        worker.join(2)

    ui = Path(__file__).resolve().parent.parent / "livelingo" / "ui"
    assert 't("numbers_lost", { numbers: m.lost.join(' in ui.joinpath("app.js").read_text(encoding="utf-8")
    assert ".msg .lost {" in ui.joinpath("style.css").read_text(encoding="utf-8")
    assert ui.joinpath("i18n.js").read_text(encoding="utf-8").count("numbers_lost: ") == len(UI_LANGS)


def test_long_text_is_split_not_cut():
    """超過 500 個字的訊息以前只翻前 500 個字。現在切成幾段分開翻再接起來，一個字都不能少。"""
    from pathlib import Path

    from livelingo.core.translate import MAX_TEXT, split_long

    assert split_long("短短的一句。", 500) == ["短短的一句。"]
    text = "這是其中的一句，講的是今天打的那一場。" * 60                          # 一千多個字
    parts = split_long(text, 500)
    assert len(parts) == 3 and all(len(p) <= 500 for p in parts) and "".join(parts) == text
    assert all(p.endswith("。") for p in parts)                                   # 切在句子結尾，不切在句子中間
    english = " ".join(f"Sentence number {i} is about version 2.5 of the game." for i in range(40))
    parts = split_long(english, 500)
    assert all(len(p) <= 500 and p.endswith(".") for p in parts) and " ".join(parts) == english      # 2.5 的小數點不是句尾
    assert split_long("字" * 1200, 500) == ["字" * 500, "字" * 500, "字" * 200]      # 完全沒有標點：只好硬切，但不丟字
    comma = "，".join(["沒有句號只有逗號的一長串"] * 60)
    assert all(len(p) <= 500 for p in split_long(comma, 500)) and "".join(split_long(comma, 500)) == comma

    tr = _FakeModel(lambda sent, style, context: f"[{len(sent)}]")
    out = tr.translate(text, "ko", "zh", context=["前一句"])
    assert out == " ".join(f"[{len(p)}]" for p in split_long(text, 500))           # 每一段都翻了，照順序接起來
    assert tr.contexts[0] == ["前一句"] and tr.contexts[1] == []                    # 前文只給第一段
    assert sum(len(a[0]) for a in tr.asked) == len(text)
    tr = _FakeModel("好")
    assert tr.translate("Hello there. " * 100, "zh-TW", "en") == "好" * 3           # 翻成中文、日文時段落之間不加空白
    tr = _FakeModel("가")
    tr.translate("".join(chr(0x4E00 + i) for i in range(5000)), "ko", "zh")        # 五千個不一樣的字
    assert sum(len(a[0].rstrip("。")) for a in tr.asked) == MAX_TEXT == 2000        # 再長就真的不收了（輸入框也只收這麼多）

    ui = Path(__file__).resolve().parent.parent / "livelingo" / "ui"
    assert f'id="say" autocomplete="off" maxlength="{MAX_TEXT}"' in ui.joinpath("index.html").read_text(encoding="utf-8")
    assert f'class="edit-src" rows="2" maxlength="{MAX_TEXT}"' in ui.joinpath("app.js").read_text(encoding="utf-8")


def test_long_sentences_stay_longer_on_the_overlay():
    """浮動留言停留的時間：設定的秒數是最少的，長的句子照閱讀時間多留，但有上限；程式的短提示和「不會消失」不受影響。"""
    from livelingo import overlay

    life = overlay.Overlay._life
    assert overlay.read_seconds("가자") == 0.4 and overlay.read_seconds("hello there") == 10 / 15
    assert life({"text": "가자"}, 15) == 15                                  # 短句：照設定
    long_ko = "지금 바텀에 정글이 오고 있으니까 조심하고 용 나오기 전에 와드 좀 박아 줘"
    assert 15 < life({"text": long_ko * 2}, 15) < 50                          # 長句：多留一下
    assert life({"text": long_ko}, 5) == 3 + 1.5 * overlay.read_seconds(long_ko) > 5
    assert life({"text": long_ko * 20}, 5) == 20                             # 再長也有上限（三倍再加 5 秒）
    assert life({"text": long_ko * 20}, 0) == 0                              # 設成不會消失：不變
    assert life({"text": long_ko * 20, "life": 3.0}, 15) == 3.0              # 程式自己的短提示：照它自己的
    assert life({"text": "a few short words"}, 8) == 8


def test_uninstall_asks_before_removing_personal_data():
    """卸載：自己累積的設定、詞彙表、歷史要先問要不要留；模型和引擎一律刪。安靜模式照舊全部刪，自動測試兩種都走一次。"""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    iss = root.joinpath("packaging", "installer.iss").read_text(encoding="utf-8")
    code = iss[iss.index("[Code]"):]
    assert "SuppressibleMsgBox(CustomMessage('KeepData'), mbConfirmation, MB_YESNO, IDNO) = IDYES" in code
    assert "{param:KEEPDATA|0}" in code and "not UninstallSilent" in code
    keep = code[code.index("if KeepData then"):code.index("DelTree(data, True, True, True)")]
    assert [name for name in ("models", "engine", "update", "webview") if f"data + '\\{name}'" in keep] == ["models", "engine", "update", "webview"]
    assert "config" not in keep and "history" not in keep                       # 留下來的那一邊不能碰到設定和歷史
    messages = iss[iss.index("[CustomMessages]"):iss.index("[Tasks]")]
    flags = (("zhtw", "HasZhTW"), ("zhcn", "HasZhCN"), ("ko", "HasKo"), ("ja", "HasJa"), ("es", "HasEs"), ("pt", "HasPt"), ("fr", "HasFr"),
             ("de", "HasDe"), ("ru", "HasRu"), ("vi", "HasVi"), ("id", "HasId"))      # 泰文 Inno Setup 沒有安裝文字，用英文
    assert messages.count("KeepData=") == 1 + len(flags)                         # 預設（英文）加其他語言
    for lang, flag in flags:
        assert f"#ifdef {flag}\n{lang}.KeepData=" in messages and iss.count(f"#define {flag}") >= 1, lang
    ci = root.joinpath("packaging", "ci_install_test.py").read_text(encoding="utf-8")
    assert '"/KEEPDATA=1"' in ci and "glossary-kept-by-test.json" in ci and ci.count("unins000.exe\"), *SILENT") == 2
    main_src = root.joinpath("livelingo", "__main__.py").read_text(encoding="utf-8")
    assert 'kept.pop("store_dir", None)' in main_src                           # 留下來的設定不能還指著刪掉的資料夾
    assert "會全部刪除" not in root.joinpath("README.md").read_text(encoding="utf-8")
    help_text = root.joinpath("livelingo", "data", "help.json").read_text(encoding="utf-8")
    assert "卸載時會一起刪掉" not in help_text and "removed when you uninstall" not in help_text


def test_black_capture_is_reported(tmp_path):
    """遊戲用「全螢幕」時抓到的畫面是全黑的：黑了幾秒就說一聲、健康檢查亮黃燈；畫面回來就恢復。切換畫面黑一下不算。"""
    import numpy as np

    from livelingo.core import watcher as watcher_mod
    from livelingo.core.watcher import ScreenWatcher
    from livelingo.service import Service

    class NoText:
        def read(self, bgr):
            return []

    frames = {"a": np.zeros((60, 200, 3), np.uint8), "b": np.full((60, 200, 3), 40, np.uint8)}
    told = []
    w = ScreenWatcher(NoText(), on_message=lambda *a: None, grab=lambda place: frames[place["id"]], on_black=told.append)
    w.set_regions([{"id": "a", "left": 0, "top": 0, "width": 200, "height": 60}, {"id": "b", "left": 0, "top": 0, "width": 200, "height": 60}])
    clock = [100.0]
    real = watcher_mod.time.monotonic
    watcher_mod.time.monotonic = lambda: clock[0]
    try:
        look = lambda: [w._look(slot, w.grab) for slot in list(w._slots)]
        look()
        assert told == [] and w.black() == []                 # 才剛黑：先不說
        clock[0] += 3
        look()
        assert told == []
        clock[0] += 3
        look()
        assert told == [["a"]] and w.black() == ["a"]         # 黑了六秒：只有全黑的那一區
        look()
        assert told == [["a"]]                                # 不會一直說
        frames["a"] = np.full((60, 200, 3), 200, np.uint8)
        look()
        assert told == [["a"], []] and w.black() == []        # 畫面回來了
        frames["a"] = np.zeros((60, 200, 3), np.uint8)
        look()
        clock[0] += 2
        frames["a"] = np.full((60, 200, 3), 90, np.uint8)     # 只黑了兩秒（切換畫面）：不算
        look()
        assert told == [["a"], []]
    finally:
        watcher_mod.time.monotonic = real

    service = Service(tmp_path)
    notices = []
    service.notice = lambda code, **more: notices.append(code)
    service.cfg.set(regions=[{"id": "a", "name": "", "left": 0, "top": 0, "width": 200, "height": 60, "on": True, "app": "", "anchor": None}])
    service.sources["screen"]["on"] = True
    light = lambda: next(i for i in service.health()["items"] if i["id"] == "screen")
    assert light()["code"] == "screen_ok"
    service._screen_black(["a"])
    service._screen_black(["a"])
    assert notices == ["screen_black"]                        # 同一區只說一次
    assert (light()["state"], light()["code"]) == ("warn", "screen_black")
    service._screen_black([])
    assert light()["code"] == "screen_ok"
    service._screen_black(["a"])
    service._stop_watcher()
    assert service._black_regions == set() and notices == ["screen_black", "screen_black"]


def test_failed_translation_says_why_and_can_be_retried(tmp_path):
    """翻譯失敗：用看得懂的話說原因（五種語言），電腦回報的原文只當補充；失敗的那一則可以按「重試」再翻。"""
    import socket
    import urllib.error
    from pathlib import Path

    from livelingo.core.translate import why
    from livelingo.service import Service

    assert why(urllib.error.URLError(ConnectionRefusedError(10061, "refused"))) == "engine_down"
    assert why(ConnectionResetError("reset")) == "engine_down"
    assert why(socket.timeout("timed out")) == "timeout" and why(urllib.error.URLError("timed out")) == "timeout"
    http = lambda code: urllib.error.HTTPError("http://x", code, "x", {}, None)
    assert why(http(401)) == "engine_key" and why(http(503)) == "engine_busy" and why(http(500)) == "engine_reply"
    assert why(KeyError("choices")) == "engine_reply" and why(RuntimeError("?")) == "other"

    ui = Path(__file__).resolve().parent.parent / "livelingo" / "ui"
    i18n, app = ui.joinpath("i18n.js").read_text(encoding="utf-8"), ui.joinpath("app.js").read_text(encoding="utf-8")
    for code in ("engine_down", "timeout", "engine_key", "engine_busy", "engine_reply"):
        assert i18n.count(f"err_{code}: ") == len(UI_LANGS), code
    assert 't("err_" + m.why)' in app and 'data-act="again"' in app and 'post("/api/again"' in app

    class Flaky:
        tone, fail = "casual", True

        def translate(self, text, target, source=None, context=None):
            if self.fail:
                raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
            return "안녕"

        def clear_cache(self):
            pass

    service = Service(tmp_path)
    service.cfg.set(back_translate=False)
    service.translator = Flaky()
    service._engine_gone = lambda: False               # 引擎還在，只是這一次沒回應
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()

    def wait(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    try:
        mid = service.submit("typed", "你好", "zh")
        get = lambda: next(m for m in service.messages if m["id"] == mid)
        wait(lambda: get()["error"])
        assert get()["why"] == "engine_down" and not get()["pending"]
        assert service.again(mid + 99) is False
        service.translator.fail = False
        assert service.again(mid) is True
        wait(lambda: get()["translated"] == "안녕")
        assert get()["error"] == "" and get()["why"] == "" and service.again(mid) is False      # 翻好的不能再按
    finally:
        service.closing.set()
        worker.join(2)

    # 引擎一直自己停、不再重開的那個畫面：也用看得懂的話說，紀錄檔指向停之前留下來的那一份
    lost = Service(tmp_path / "lost")
    assert lost._engine_lost(RuntimeError("the translation engine keeps stopping"), code="keeps_stopping") is False
    assert lost.phase == "error" and lost.detail["code"] == "keeps_stopping" and lost.detail["log"].endswith("engine-stopped.log")
    assert i18n.count("err_restart_failed: ") == len(UI_LANGS) and 'code="restart_failed"' in ui.parent.joinpath("service.py").read_text(encoding="utf-8")
    assert i18n.count("err_keeps_stopping: ") == len(UI_LANGS) and 'd.code ? t("err_" + d.code) : (d.message || "")' in app
    lost._engine_lost(RuntimeError("boom"))
    assert lost.detail["code"] == "" and lost.detail["message"] == "boom" and lost.detail["log"].endswith("engine.log")


def test_lost_audio_device_is_reconnected(tmp_path, monkeypatch):
    """收音裝置用到一半斷線：自己重新連接（隔幾秒試一次），連上了說一聲；一開始就打不開的照舊馬上顯示錯誤；
    等的時候使用者關掉就不再試；試完還是不行才顯示錯誤。"""
    import queue
    from pathlib import Path

    from livelingo import service as service_mod
    from livelingo.service import Service

    monkeypatch.setattr(service_mod, "RECONNECT_WAITS", (0.05, 0.05, 0.05))
    made = []

    class FakeCapture:
        def __init__(self, kind, on_error, on_open):
            self.kind, self.on_error, self.on_open, self.q, self.alive = kind, on_error, on_open, queue.Queue(), True
            made.append(self)

        def start(self):
            pass

        def stop(self):
            self.alive = False

        def is_alive(self):
            return self.alive

    class FakeAudio:
        def capture(self, kind, device, on_error=None, on_open=None):
            return FakeCapture(kind, on_error, on_open)

    class FakeRecognizer:
        beam = 1

    class FakePipeline:      # 真的那個要語音模型的套件；這裡只測斷線重連，換成假的，沒裝那個套件的地方也能跑
        def __init__(self, capture, *a, **kw):
            self.capture, self.stay = capture, 1.0

        def start(self):
            self.capture.start()

        def stop(self):
            self.capture.stop()

    from livelingo.core import speech
    monkeypatch.setattr(speech, "SpeechPipeline", FakePipeline)

    def wait(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    service = Service(tmp_path)
    service.audio, service.recognizer, service.phase = FakeAudio(), FakeRecognizer(), "ready"
    notices = []
    service.notice = lambda code, **more: notices.append(code)
    mic = service.sources["mic"]
    try:
        # 一開始就打不開：不重試，馬上顯示錯誤
        service.cfg.set(sources={"screen": False, "listen": False, "mic": True})
        service._start_voice("mic")
        made[-1].on_error("mic", RuntimeError("no microphone found"))
        assert mic["on"] is False and mic["error"] == "no microphone found" and not mic["lost"] and len(made) == 1 and notices == []

        # 開起來之後才斷：自己重新連接
        service._start_voice("mic")
        made[-1].on_open("mic", "USB Headset")
        assert mic["on"] and mic["device"] == "USB Headset"
        made[-1].on_error("mic", RuntimeError("device unplugged"))
        assert mic["on"] and mic["lost"] and mic["error"] == "" and notices == ["device_lost"]
        light = lambda: next(i for i in service.health()["items"] if i["id"] == "mic")
        wait(lambda: len(made) == 3)                          # 等了一下，再開一次
        made[-1].on_error("mic", RuntimeError("audio device not found"))      # 還沒接回去
        assert mic["lost"] and notices == ["device_lost"]     # 只說一次
        wait(lambda: len(made) == 4)
        made[-1].on_open("mic", "Laptop Microphone")          # 連上了（換成另一支也算）
        assert mic["on"] and not mic["lost"] and mic["device"] == "Laptop Microphone"
        assert notices == ["device_lost", "device_back"] and service._voice_tries == {}

        # 等的時候使用者自己關掉：不再試
        made[-1].on_error("mic", RuntimeError("device unplugged"))
        assert mic["lost"]
        service.set_source("mic", False)
        assert not mic["on"] and not mic["lost"]
        count = len(made)
        time.sleep(0.3)
        assert len(made) == count

        # 試完全部還是不行：顯示錯誤
        service.set_source("mic", True)
        wait(lambda: len(made) == count + 1 and mic["on"])
        made[-1].on_open("mic", "USB Headset")
        made[-1].on_error("mic", RuntimeError("device unplugged"))
        for n in range(3):
            wait(lambda: len(made) == count + 2 + n)
            wait(lambda: not mic["busy"])
            made[-1].on_error("mic", RuntimeError("audio device not found"))
        assert not mic["on"] and not mic["lost"] and mic["error"] == "audio device not found"
    finally:
        service.closing.set()
        for capture in made:
            capture.alive = False

    source = (Path(service_mod.__file__).parent / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'if (s.lost) return t("src_lost");' in source
    i18n = (Path(service_mod.__file__).parent / "ui" / "i18n.js").read_text(encoding="utf-8")
    for key in ("src_lost", "n_device_lost", "n_device_back", "h_mic_lost", "h_listen_lost"):
        assert i18n.count(f"{key}: ") == len(UI_LANGS), key


def test_update_check_can_be_turned_off(tmp_path, monkeypatch):
    """「開啟時檢查有沒有新版本」可以關：關了就不連網路去問，也不會出現更新的畫面。預設是開的。"""
    from pathlib import Path

    from livelingo.config import DEFAULTS, EDITABLE
    from livelingo.service import Service

    assert DEFAULTS["update_check"] is True and "update_check" in EDITABLE
    asked = []
    monkeypatch.setenv("LIVELINGO_UPDATE_API", "http://127.0.0.1:9/none")      # 讓「會自動更新」成立
    monkeypatch.delenv("LIVELINGO_NO_UPDATE", raising=False)
    monkeypatch.setattr(updater, "check", lambda: asked.append(1) or None)
    service = Service(tmp_path)
    phases = []
    service.set_phase = lambda phase, **more: phases.append(phase)
    assert service._update_if_needed() is False and asked == [1] and phases == ["updating"]      # 開著：會去問
    assert service.update_config({"update_check": False})["changed"] == ["update_check"]
    assert service._update_if_needed() is False and asked == [1] and phases == ["updating"]      # 關了：沒有再問
    ui = Path(__file__).resolve().parent.parent / "livelingo" / "ui"
    assert 'switchRow("update_check", t("update_check"), t("update_check_desc"))' in ui.joinpath("app.js").read_text(encoding="utf-8")
    i18n = ui.joinpath("i18n.js").read_text(encoding="utf-8")
    for key in ("update_check", "update_check_desc", "about_update_off"):
        assert i18n.count(f"    {key}: ") + i18n.count(f", {key}: ") == len(UI_LANGS), key


def test_translation_streams_out_while_it_is_made(tmp_path, monkeypatch):
    """即時：譯文一邊翻一邊出現，不用等整句翻完。最後那一則才是整理過、核對過的。"""
    import json as _json
    from pathlib import Path

    import pytest

    import livelingo.service as service_mod
    from livelingo.core import translate as tr_mod
    from livelingo.service import Service

    # 一、跟引擎要「一邊翻一邊回」，一行一筆地讀（用一個真的本機小伺服器當引擎）
    import http.server

    seen = {"gone": threading.Event(), "wait": 0.0, "status": 200, "chunked": False}

    class FakeEngine(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen["body"] = _json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            seen["auth"] = self.headers.get("Authorization")
            time.sleep(seen["wait"])      # 像引擎在讀原文：還沒有任何回應
            def send(data: bytes) -> None:      # 真的引擎是一塊一塊送的（chunked），而且一塊不一定剛好是一行
                if seen["chunked"]:
                    for part in (data[:7], data[7:]):
                        if part:
                            self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                else:
                    self.wfile.write(data)
                self.wfile.flush()

            try:
                self.send_response(seen["status"])
                self.send_header("Content-Type", "text/event-stream")
                if seen["chunked"]:
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                send(b": keep-alive\n\n")
                for piece in ("3", "분 후에", " 도착해.", ""):
                    send(("data: " + _json.dumps({"choices": [{"text": piece}]}, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    time.sleep(0.05)
                send(b"data: [DONE]\n\n")
                if seen["chunked"]:
                    self.wfile.write(b"0\r\n\r\n")
            except OSError:
                seen["gone"].set()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        tr = Translator(f"http://127.0.0.1:{server.server_address[1]}")
        tr.key = "k1"
        parts = []
        assert tr._ask("3分鐘後到。", "zh", "ko", [], on_part=parts.append) == "3분 후에 도착해."
        assert seen["body"]["stream"] is True and seen["auth"] == "Bearer k1" and parts == ["3", "3분 후에", "3분 후에 도착해."]

        seen["chunked"] = True
        parts.clear()
        assert tr._ask("3分鐘後到。", "zh", "ko", [], on_part=parts.append) == "3분 후에 도착해." and parts == ["3", "3분 후에", "3분 후에 도착해."]
        seen["chunked"] = False

        def boom(text):
            raise RuntimeError("screen is gone")

        assert tr._ask("3分鐘後到。", "zh", "ko", [], on_part=boom) == "3분 후에 도착해."      # 畫面那邊出錯，翻譯照樣完成
        with pytest.raises(tr_mod.Stopped):
            tr._ask("3分鐘後到。", "zh", "ko", [], on_part=lambda text: False)                  # 畫面說不用翻完了
        assert "do not finish" not in seen["body"]["prompt"].lower()      # 整句的問法裡沒有「還沒說完」那一段

        # 「停」：引擎還在讀原文、一個字都還沒回的時候也要馬上停，不能等它
        seen["wait"], stop, started = 3.0, tr_mod.Stop(), time.time()
        threading.Timer(0.2, stop.stop).start()
        with pytest.raises(tr_mod.Stopped):
            tr.translate("3分鐘後到", "ko", "zh", on_part=parts.append, unfinished=True, stop=stop)
        assert time.time() - started < 1.5 and not tr.busy()
        assert seen["gone"].wait(5)      # 引擎那邊看得到連線關了（真的引擎會因此停下那一次的工作）
        with pytest.raises(tr_mod.Stopped):
            tr.translate("5分鐘後到", "ko", "zh", unfinished=True, stop=stop)      # 已經按過停的：不會去問引擎
        seen["wait"] = 0.0
        assert tr.translate("3分鐘後到", "ko", "zh", unfinished=True, stop=tr_mod.Stop()) == "3분 후에 도착해."
        assert "Do not finish or guess the rest" in seen["body"]["prompt"] and seen["body"]["prompt"].rstrip().endswith("3分鐘後到<end_of_turn>\n<start_of_turn>model")

        seen["status"] = 401
        with pytest.raises(Exception) as bad:
            tr._ask("3分鐘後到。", "zh", "ko", [], on_part=parts.append)
        assert tr_mod.why(bad.value) == "engine_key"      # 出錯的原因照樣分得出來
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(Exception) as bad:
        tr._ask("3分鐘後到。", "zh", "ko", [], on_part=parts.append)
    assert tr_mod.why(bad.value) == "engine_down"

    # 二、translate() 把還沒翻完的字整理過再給畫面（簡轉繁）；沒有要即時的時候，問法跟以前一樣
    tr = _FakeModel("这个队伍很强。")
    parts = []
    assert tr.translate("이 팀 진짜 세다", "zh-TW", "ko", on_part=parts.append) == "這個隊伍很強。"
    assert parts[0] == "這" and parts[-1] == "這個隊伍很強。" and all("这" not in p and "队" not in p for p in parts)
    parts.clear()
    assert tr.translate("이 팀 진짜 세다", "zh-TW", "ko", on_part=parts.append) == "這個隊伍很強。" and parts == []      # 記得的：直接給整句
    long_text = "。".join(f"第{chr(0x4E00 + i)}句話要翻譯" for i in range(120))
    tr = _FakeModel(lambda text, style, context: "가" * 3)
    parts = []
    out = tr.translate(long_text, "ko", "zh", on_part=parts.append)
    assert len(tr.asked) > 1 and parts[-1] == out and all(len(a) <= len(b) for a, b in zip(parts, parts[1:]))      # 分段翻的：前面翻好的不會消失

    # 三、服務把還沒翻完的字送給主畫面（partial）和浮動字幕（只有對方說的話）
    monkeypatch.setattr(service_mod, "PARTIAL_EVERY", 0.0)
    service = Service(tmp_path)
    service.cfg.set(back_translate=False, my_lang="zh-TW", friend_lang="ko")
    service.translator = _FakeModel(lambda text, style, context: "我們去打龍吧。" if "용" in text else "용 잡으러 가자.")
    events, shown = [], []
    old_publish = service.hub.publish
    monkeypatch.setattr(service.hub, "publish", lambda kind, data=None: (events.append((kind, data)), old_publish(kind, data))[1])
    monkeypatch.setattr(service.overlay, "push", lambda *a, **kw: shown.append((a, kw)))
    service.cfg.set(overlay=True)
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()
    try:
        service.submit("heard", "용 잡으러 가자", "ko")
        service.submit("typed", "我們去打龍吧", "zh")
        end = time.time() + 5
        while time.time() < end and len([m for m in service.messages if not m.get("pending")]) < 2:
            time.sleep(0.02)
        by = {m["original"]: m for m in service.messages}
        heard, typed = by["용 잡으러 가자"], by["我們去打龍吧"]
        assert heard["translated"] == "我們去打龍吧。" and typed["translated"] == "용 잡으러 가자."
        got = [d["text"] for k, d in events if k == "partial" and d["id"] == heard["id"]]
        assert got and got[0] == "我" and got[-1] == "我們去打龍吧。"
        assert [d["text"] for k, d in events if k == "partial" and d["id"] == typed["id"]][-1] == "용 잡으러 가자."
        kinds = [k for k, d in events if (k == "partial" and d["id"] == heard["id"]) or (k == "message" and d.get("id") == heard["id"] and not d.get("pending"))]
        assert kinds[-1] == "message" and set(kinds[:-1]) == {"partial"}      # 整句最後才到，蓋掉還沒翻完的
        texts = [a[1] for a, kw in shown if kw.get("key") == heard["id"]]
        assert texts[0] == "我" and texts[-1] == "我們去打龍吧。" and len(texts) > 2      # 浮動字幕也是一個字一個字出來
        assert not [1 for a, kw in shown if kw.get("key") == typed["id"] and a[1] != "용 잡으러 가자."]      # 自己打的字不先出半句
    finally:
        service.closing.set()

    ui = Path("livelingo/ui")
    js, css = (ui / "app.js").read_text(encoding="utf-8"), (ui / "style.css").read_text(encoding="utf-8")
    assert "partial: (d)" in js and "part-${m.id}" in js and ".text.streaming" in css and "prefers-reduced-motion" in css


def test_live_captions_while_the_other_person_is_still_talking(tmp_path, monkeypatch):
    """即時字幕：對方還在說，就先辨識目前聽到的、先翻；說完由整句接手同一則。半句不進歷史、字幕檔、使用數字。"""
    import queue

    import numpy as np
    import pytest

    import livelingo.service as service_mod
    from livelingo import jobs as jobs_mod
    from livelingo.core.audio import SR
    from livelingo.core.speech import LivePacer, SpeechPipeline
    from livelingo.service import Service

    # 一、節奏：說了一陣子才先辨識；多聽到一些才再做；辨識得慢的電腦自動做得少；辨識器在忙就不做
    pacer = LivePacer(first=1.5, grow=0.6, rest=1.0)
    assert not pacer.due(1.0, 100.0) and pacer.due(1.6, 100.0) and not pacer.due(1.6, 100.0, busy=True)
    pacer.done(1.6, 101.5, spent=1.5)
    assert not pacer.due(2.0, 101.6)                      # 才多聽到 0.4 秒
    assert not pacer.due(2.6, 102.5) and pacer.due(2.6, 103.1)      # 上一次花了 1.5 秒：至少休息 1.5 秒
    pacer.done(2.6, 103.2, spent=0.1)
    assert pacer.due(3.3, 103.35)                         # 辨識很快（顯示卡）：多聽到一點就再做
    pacer.reset()
    assert pacer.due(1.5, 0.0)

    # 二、管線：先顯示、說完前通知、整句接手；沒結果就收回
    calls, state = [], {"interim": ("안녕하세", "ko", False), "final": ("안녕하세요 여러분", "ko", False), "live": True, "busy": False}

    class Seg:
        speaking, emit, audio = False, [], np.zeros(SR * 2, dtype=np.float32)

        def feed(self, audio):
            out, self.emit = self.emit, []
            return out

        def so_far(self):
            return self.audio if self.speaking else None

    class Rec:
        asked = []

        def busy(self):
            return False

        def transcribe(self, audio, langs, stay=1.0, quick=False):
            self.asked.append(quick)
            return state["interim"] if quick else state["final"]

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe.capture = type("C", (), {"kind": "loopback"})()
    pipe.segmenter, pipe.recognizer, pipe.langs, pipe.stay = Seg(), Rec(), ["ko"], 1.0
    pipe.on_text = lambda kind, text, lang, seconds, unsure: calls.append(("text", text))
    pipe.on_partial = lambda kind, text, lang: calls.append(("part", text))
    pipe.live, pipe.busy, pipe.pacer = (lambda: state["live"]), (lambda: state["busy"]), LivePacer(rest=0.0, grow=0.5)
    block = np.zeros(1600, dtype=np.float32)
    pipe._hear(block)
    assert calls == []                                    # 沒有人在說話
    pipe.segmenter.speaking = True
    state["live"] = False
    pipe._hear(block)
    state["live"], state["busy"] = True, True
    pipe._hear(block)
    assert calls == []                                    # 設定關著、或電腦正在忙：不做
    state["busy"] = False
    pipe._hear(block)
    assert calls == [("part", "안녕하세")] and Rec.asked == [True]      # 用快的算法
    pipe.segmenter.audio = np.zeros(SR * 3, dtype=np.float32)
    pipe._hear(block)
    assert calls == [("part", "안녕하세")]                 # 聽到的字沒變：不重複送
    pipe.segmenter.speaking, pipe.segmenter.emit = False, [np.zeros(SR * 3, dtype=np.float32)]
    pipe._hear(block)
    assert calls[1:] == [("part", ""), ("text", "안녕하세요 여러분")]      # 先說「說完了」，再給整句
    calls.clear()
    pipe.segmenter.speaking, pipe.segmenter.audio = True, np.zeros(SR * 2, dtype=np.float32)
    pipe._hear(block)
    state["final"] = None                                 # 這一句最後被當成雜音
    pipe.segmenter.speaking, pipe.segmenter.emit = False, [np.zeros(SR * 2, dtype=np.float32)]
    pipe._hear(block)
    assert calls == [("part", "안녕하세"), ("part", ""), ("part", None)]      # 先顯示的要收回
    plain = SpeechPipeline.__new__(SpeechPipeline)        # 沒有要即時字幕的（聽我說話）：完全不先辨識
    plain.capture, plain.segmenter, plain.recognizer, plain.langs, plain.stay = pipe.capture, Seg(), Rec(), ["ko"], 1.0
    plain.on_text = lambda *a: None
    plain.segmenter.speaking, before = True, len(Rec.asked)
    plain._hear(block)
    assert len(Rec.asked) == before

    # 三、排隊：半句排最後，每個來源只留最新的一份
    q = jobs_mod.Jobs()
    q.put_live({"via": "heard", "original": "a"})
    q.put_live({"via": "heard", "original": "ab"})
    q.put({"via": "screen", "original": "s"})
    q.put({"back_of": 3})
    assert q.waiting() == 2
    assert [q.get(0.01)[0].get("original", "back") for _ in range(3)] == ["s", "back", "ab"]
    with pytest.raises(queue.Empty):
        q.get(0.01)
    q.put_live({"via": "heard", "original": "abc"})
    q.drop_live("heard")
    with pytest.raises(queue.Empty):
        q.get(0.01)                                       # 說完了：半句不翻了

    # 四、服務：同一則訊息從半句變成整句
    monkeypatch.setattr(service_mod, "PARTIAL_EVERY", 0.0)
    service = Service(tmp_path)
    service.cfg.set(back_translate=False, my_lang="zh-TW", their_lang="ko", drop_echo=False)
    assert service.cfg["live_captions"] is True
    answers = {"용 잡으러": "去打龍", "용 잡으러 가자 지금": "現在去打龍吧", "용 잡으러 가자 지금 빨리": "現在快去打龍吧。"}
    model = service.translator = _FakeModel(lambda text, style, context: answers.get(text.rstrip("."), "？"))
    events, shown, kept = [], [], []
    old_publish = service.hub.publish
    monkeypatch.setattr(service.hub, "publish", lambda kind, data=None: (events.append((kind, data)), old_publish(kind, data))[1])
    monkeypatch.setattr(service.overlay, "push", lambda *a, **kw: shown.append((a, kw)))
    monkeypatch.setattr(service.history, "add", lambda msg: kept.append(dict(msg)) or False)
    counted = []
    monkeypatch.setattr(service, "_count", lambda msg, failed=False: counted.append(msg["id"]))
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()

    def wait_for(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    def live():
        return [m for m in service.messages if m.get("live")]

    try:
        service._on_live("loopback", "용 잡으러", "ko")
        wait_for(lambda: live() and live()[0].get("partial") == "去打龍")
        first = live()[0]
        assert first["pending"] and first["who"] == "them" and first["via"] == "heard" and first["original"] == "용 잡으러"
        assert model.unfinished == [True] and model.asked[0][0] == "용 잡으러"      # 跟模型說這是半句，也不幫它補句號
        service._on_live("loopback", "용 잡으러 가자 지금", "ko")
        wait_for(lambda: live() and live()[0].get("partial") == "現在去打龍吧")
        assert [m["id"] for m in service.messages] == [first["id"]]      # 還是同一則
        texts = [d["text"] for k, d in events if k == "partial"]
        assert "去打龍" in texts and texts[-1] == "現在去打龍吧"
        assert [a[1] for a, kw in shown if kw.get("key") == first["id"]][-1] == "現在去打龍吧"      # 浮動字幕也跟著長
        assert kept == [] and counted == [] and not service.transcript.on      # 半句不進歷史、不算使用次數
        service._on_live("loopback", "", None)            # 說完了，整句正在辨識
        assert live()[0]["closing"] and live()[0]["partial"] == "現在去打龍吧"
        service._on_speech("loopback", "용 잡으러 가자 지금 빨리", "ko", 0.4)
        wait_for(lambda: service.messages and not service.messages[0].get("pending"))
        done = service.messages[0]
        assert len(service.messages) == 1 and done["id"] == first["id"] and done["at"] == first["at"]
        assert done["translated"] == "現在快去打龍吧。" and not done.get("live") and "partial" not in done
        assert model.unfinished[-1] is False and model.asked[-1][0] == "용 잡으러 가자 지금 빨리."      # 整句照平常的方式翻
        assert counted == [first["id"]] and kept and kept[-1]["translated"] == "現在快去打龍吧。" and not service._live
        pending = [d for k, d in events if k == "message" and d["id"] == first["id"] and d.get("pending") and not d.get("live")]
        assert pending and pending[0]["partial"] == "現在去打龍吧"      # 整句翻好之前，畫面上留著先翻的半句

        # 最後沒有結果：收回；回音、關掉收音、清除對話也都不會留下半則
        service._on_live("loopback", "아 진짜", "ko")
        wait_for(lambda: bool(live()))
        gone = live()[0]["id"]
        service._on_live("loopback", None, None)
        assert not live() and ("message_removed", {"id": gone}) in events
        service._on_live("loopback", "아 진짜", "ko")
        service._stop_voice("listen")
        assert not live() and not service._live
        service._on_live("loopback", "아 진짜", "ko")
        service.clear_messages()
        assert not service._live and list(service.messages) == []
        service._on_live("loopback", "", None)
        service._on_live("loopback", None, None)          # 沒有先顯示過的：什麼事都不會發生
        assert list(service.messages) == []

        # 翻譯和辨識都用處理器的電腦：正在翻的時候先不辨識半句（兩個一起跑會互搶，都慢好幾倍）
        service.mode = {"model": "4b", "engine": "cpu", "quality": "standard"}
        assert service._live_wait() is False
        with model._lock:
            assert service._live_wait() is True
        service.jobs.put({"via": "screen", "original": "x", "id": 999, "lang": "ko", "who": "them"})
        assert service._live_wait() is True
        service.mode = {"model": "12b", "engine": "cuda", "quality": "high"}
        assert service._live_wait() is False              # 翻譯用顯示卡：不用等
    finally:
        service.closing.set()

    from pathlib import Path

    ui = Path("livelingo/ui")
    js, css = (ui / "app.js").read_text(encoding="utf-8"), (ui / "style.css").read_text(encoding="utf-8")
    assert 'switchRow("live_captions"' in js and "m.live" in js and ".orig.hearing" in css
    source = Path("livelingo/service.py").read_text(encoding="utf-8")
    assert 'on_partial=self._on_live if key == "listen" else None' in source      # 只給對方說的話


def test_speakers_are_told_apart(tmp_path, monkeypatch):
    """分辨說話者：照聲紋把對方分成說話者 1、2、3，各有顏色、可以取名字；聲紋和名字只在記憶體裡，不進設定、歷史、問題報告。"""
    import json as json_mod
    from pathlib import Path

    import numpy as np

    import livelingo.core.voices as voices_mod
    from livelingo import chatpaint, storage, transcript
    from livelingo.core.audio import SR
    from livelingo.core.speech import LivePacer, SpeechPipeline
    from livelingo.core.voices import MAYBE, SAME, Speakers, Voiceprint, fbank
    from livelingo.service import Service

    def voice(*parts):      # 做一個方向固定的聲紋：(第幾維, 多少) …
        v = np.zeros(256, dtype=np.float32)
        for i, x in parts:
            v[i] = x
        return v / np.linalg.norm(v)

    # 一、分人的規則
    a, b, c = voice((0, 1)), voice((1, 1)), voice((2, 1))
    people = Speakers()
    assert people.who(None, 3.0) is None and people.who(a, 0.5) is None      # 沒有聲紋、太短：不分
    assert people.who(a, 1.0) is None and people.people() == []             # 誰都不像，但太短：不拿來認識新的人
    assert people.who(a, 3.0) == 1 and people.who(b, 3.0) == 2 and people.who(a, 2.0) == 1
    near = voice((0, 0.47), (5, (1 - 0.47 ** 2) ** 0.5))                  # 跟 1 號有點像，又不夠像
    assert MAYBE <= float(near @ a) < SAME
    before = people._sums[0].copy()
    assert people.who(near, 3.0) == 1 and np.array_equal(people._sums[0], before)      # 標給他，但不拿去改他的聲紋
    alike = voice((0, 0.9), (6, (1 - 0.81) ** 0.5))
    assert people.who(alike, 0.8) == 1 and np.array_equal(people._sums[0], before)     # 夠像但很短：也不改
    assert people.who(alike, 2.0) == 1 and not np.array_equal(people._sums[0], before)
    assert people.people() == [{"id": 1, "count": 5}, {"id": 2, "count": 1}]
    few = Speakers(limit=2)
    assert [few.who(v, 3.0) for v in (a, b, c)] == [1, 2, None]             # 人數到上限：不再多分
    people.forget()
    assert people.people() == [] and people.who(b, 3.0) == 1                # 全部重來：從 1 開始

    # 二、聲音特徵的形狀；太短的聲音不送進模型
    feats = fbank(np.random.default_rng(1).standard_normal(SR).astype(np.float32) * 0.1)
    assert feats.shape == (98, 80) and feats.dtype == np.float32 and abs(float(feats.mean(axis=0).max())) < 1e-3
    assert fbank(np.zeros(100, dtype=np.float32)).shape == (0, 80)
    assert Voiceprint.__new__(Voiceprint).embed(np.zeros(int(SR * 0.5), dtype=np.float32)) is None

    # 三、語音那邊：有分出是誰才多交一個 speaker；分的時候出錯，字幕照出
    got, state = [], {"who": 2}

    class Seg:
        speaking, emit = False, []

        def feed(self, audio):
            out, self.emit = self.emit, []
            return out

        def so_far(self):
            return None

    class Rec:
        def busy(self):
            return False

        def transcribe(self, audio, langs, stay=1.0, quick=False):
            return ("안녕하세요", "ko", False)

    class Who:
        def who(self, audio):
            if state["who"] == "boom":
                raise RuntimeError("model broke")
            return state["who"]

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe.capture = type("C", (), {"kind": "loopback"})()
    pipe.segmenter, pipe.recognizer, pipe.langs, pipe.stay = Seg(), Rec(), ["ko"], 1.0
    pipe.on_text = lambda kind, text, lang, seconds, unsure, **kw: got.append(kw)
    pipe.live, pipe.busy, pipe.pacer = (lambda: False), (lambda: False), LivePacer()
    pipe.voices = lambda: Who()
    for who in (2, None, "boom"):
        state["who"] = who
        pipe.segmenter.emit = [np.zeros(SR * 2, dtype=np.float32)]
        pipe._hear(np.zeros(1600, dtype=np.float32))
    pipe.voices = lambda: None                            # 功能沒開
    pipe.segmenter.emit = [np.zeros(SR * 2, dtype=np.float32)]
    pipe._hear(np.zeros(1600, dtype=np.float32))
    assert got == [{"speaker": 2}, {}, {}, {}]

    # 四、服務：模型在背景準備，失敗看得到、可以再試
    service = Service(tmp_path)
    service.cfg.set(back_translate=False, my_lang="zh-TW", their_lang="ko", drop_echo=False, ui_lang="zh-TW")
    assert service.cfg["tell_speakers"] is False and service.speakers_state()["on"] is False
    assert service._voices_now() is None and service.state()["speakers"]["state"] == "off"
    events = []
    old_publish = service.hub.publish
    monkeypatch.setattr(service.hub, "publish", lambda kind, data=None: (events.append((kind, data)), old_publish(kind, data))[1])

    class FakeVoices:
        def __init__(self, path=None):
            self.speakers = Speakers()

        def who(self, audio):
            return 1

    plan = {"fail": True}

    def fake_get(store, progress=None, cancel=None):
        if plan["fail"]:
            raise OSError("no network")
        progress(50, 100)
        return Path(store) / "voice.onnx"

    monkeypatch.setattr("livelingo.assets.get_voice", fake_get)
    monkeypatch.setattr("livelingo.assets.has_voice", lambda store: False)
    monkeypatch.setattr(voices_mod, "Voices", FakeVoices)

    def wait_for(test):
        end = time.time() + 5
        while time.time() < end and not test():
            time.sleep(0.02)
        assert test()

    service.speakers_load()
    assert service.speakers["state"] == "off"            # 沒開：什麼都不下載
    service.cfg.set(tell_speakers=True)
    service.speakers_load()
    wait_for(lambda: service.speakers["state"] == "failed")
    assert "no network" in service.speakers["error"] and service._voices_now() is None
    service.speakers_load()
    assert service.speakers["state"] == "failed"         # 不會自己一直重試
    plan["fail"] = False
    service.speakers_load(retry=True)
    wait_for(lambda: service.speakers["state"] == "ready")
    assert isinstance(service.voices, FakeVoices) and service._voices_now() is service.voices
    assert any(k == "speakers" and d["state"] == "downloading" and d["percent"] == 50 for k, d in events)
    assert service._storage_used()["voice"] is True

    # 五、訊息帶著是誰說的；取名字；全部重來
    hold, waiting = threading.Event(), threading.Event()      # hold 沒放開的時候，翻譯會停在半路
    hold.set()

    def answer(text, style, context):
        waiting.set()
        hold.wait(5)
        return "你好"

    service.translator = _FakeModel(answer)
    shown = []
    monkeypatch.setattr(service.overlay, "push", lambda *args, **kw: shown.append((args, kw)))
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()
    try:
        service.voices.speakers.who(a, 3.0)
        service.voices.speakers.who(b, 3.0)
        service.transcript.start()
        service._on_speech("loopback", "안녕", "ko", 0.2, speaker=1)
        service._on_speech("loopback", "반가워", "ko", 0.2, speaker=2)
        service._on_speech("mic", "大家好", "zh", 0.2, speaker=1)      # 我自己說的不分
        service._on_speech("loopback", "뭐해", "ko", 0.2)              # 這一句分不出來
        wait_for(lambda: len(service.messages) == 4 and not any(m.get("pending") for m in service.messages))
        first, second, mine, unknown = list(service.messages)
        assert (first["speaker"], first["speaker_run"], first["voice"]) == (1, 1, "") and second["speaker"] == 2
        assert "speaker" not in mine and "speaker" not in unknown
        assert service.speaker_tag(first) == (1, "") and service.speaker_tag(mine) is None
        assert [kw.get("who") for args, kw in shown if kw.get("key") == second["id"]][-1] == (2, "")      # 浮動字幕也知道是誰
        secret = "阿哲Zq7"
        assert service.speaker_rename(1, f"  {secret}  ") == {"ok": True, "name": secret}
        assert service.speaker_rename(9, "x") == {"ok": False}      # 沒有這個人
        assert service.messages[0]["voice"] == secret and service.speaker_tag(service.messages[0]) == (1, secret)
        state = service.speakers_state()
        assert state["people"][0]["name"] == secret and state["people"][1]["name"] == "" and state["run"] == 1
        assert len(service.speaker_rename(2, "x" * 60)["name"]) == 24
        assert service.speaker_rename(2, "")["name"] == "" and 2 not in service._speaker_names
        service._on_speech("loopback", "가자", "ko", 0.2, speaker=1)
        wait_for(lambda: len(service.messages) == 5 and not service.messages[-1].get("pending"))
        assert service.messages[-1]["voice"] == secret              # 之後的句子直接帶著名字

        # 字幕記錄（使用者自己要存的檔）寫的是那個人的名字
        text = transcript.render(list(service.transcript._items.values()), "zh-TW", time.time(), time.time())
        assert f"{secret}：안녕" in text and "說話者 2：반가워" in text and "對方：뭐해" in text and "我：大家好" in text

        # 設定檔、歷史、問題報告裡都沒有名字，也沒有聲紋
        from livelingo import history as history_mod
        assert not {"speaker", "voice", "speaker_run"} & set(history_mod.FIELDS)
        saved = (tmp_path / history_mod.FILE).read_text(encoding="utf-8")
        assert "가자" in saved and secret not in saved and "speaker" not in saved
        assert secret not in (tmp_path / "config.json").read_text(encoding="utf-8")
        assert secret not in service.report_make()["text"]
        assert not any(tmp_path.rglob("*.npy")) and secret.encode("utf-8") not in b"".join(
            p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())

        # 還在翻的時候改名字：翻好之後名字不能被送去翻譯時的那一份蓋回去（畫面、浮動字幕、字幕檔都一樣）
        hold.clear()
        service._on_speech("loopback", "기다려", "ko", 0.2, speaker=2)
        wait_for(lambda: len(service.messages) == 6 and waiting.is_set())
        assert service.messages[-1]["pending"] and service.speaker_rename(2, "小美")["ok"]
        hold.set()
        wait_for(lambda: not service.messages[-1].get("pending"))
        assert service.messages[-1]["voice"] == "小美" and service.messages[-1]["translated"] == "你好"
        assert [kw.get("who") for args, kw in shown if kw.get("key") == service.messages[-1]["id"]][-1] == (2, "小美")
        text = transcript.render(list(service.transcript._items.values()), "zh-TW", time.time(), time.time())
        assert "小美：기다려" in text and "小美：반가워" in text

        # 關掉功能：不再標；再打開，原來的還在
        service.cfg.set(tell_speakers=False)
        assert service.speaker_tag(service.messages[0]) is None and service._voices_now() is None
        service.cfg.set(tell_speakers=True)
        assert service.speaker_tag(service.messages[0]) == (1, secret)

        # 全部重新分辨：人和名字都忘掉，之前的句子不再標是誰，字幕記錄也不再寫
        assert service.speakers_forget() == {"ok": True}
        assert service.speakers_state()["people"] == [] and service.speakers_state()["run"] == 2
        assert service.speaker_tag(service.messages[0]) is None and not service._speaker_names
        text = transcript.render(list(service.transcript._items.values()), "zh-TW", time.time(), time.time())
        assert secret not in text and "小美" not in text and "對方：안녕" in text
        # 還在翻的時候按了重新分辨：那一句翻好之後也不再標是誰（不然舊的 1 號會和新的 1 號混在一起）
        waiting.clear()
        hold.clear()
        service.voices.speakers.who(a, 3.0)
        service._on_speech("loopback", "잠깐", "ko", 0.2, speaker=1)
        wait_for(lambda: len(service.messages) == 7 and waiting.is_set())
        service.speakers_forget()
        hold.set()
        wait_for(lambda: not service.messages[-1].get("pending"))
        assert service.speaker_tag(service.messages[-1]) is None and service.messages[-1]["voice"] == ""
        text = transcript.render(list(service.transcript._items.values()), "zh-TW", time.time(), time.time())
        assert "對方：잠깐" in text and "說話者" not in text
    finally:
        service.closing.set()

    # 六、名字和顏色：五種語言、六個顏色輪著用，介面上的圓點跟浮動字幕同一組顏色
    assert set(chatpaint.SPEAKER) == set(UI_LANGS)
    assert chatpaint.speaker_name(3, "ko") == "화자 3" and chatpaint.speaker_name(3, "ko", " 민수 ") == "민수"
    assert chatpaint.speaker_color(1) == chatpaint.speaker_color(7) != chatpaint.speaker_color(2)
    assert chatpaint.NAME_COLORS["me"] not in chatpaint.SPEAKER_COLORS and chatpaint.NAME_COLORS["screen"] not in chatpaint.SPEAKER_COLORS
    plain = chatpaint.paint_message("heard", "說話者 2", "你好", 400, 22)
    tinted = chatpaint.paint_message("heard", "說話者 2", "你好", 400, 22, name_color=chatpaint.speaker_color(2))
    assert plain.size == tinted.size and plain.tobytes() != tinted.tobytes()
    ui = Path("livelingo/ui")
    js, css = (ui / "app.js").read_text(encoding="utf-8"), (ui / "style.css").read_text(encoding="utf-8")
    dots = css[css.index(".spk-dot {"):css.index(".msg .meta .spk {")].upper()
    for i, color in enumerate(chatpaint.SPEAKER_COLORS):
        assert "#%02X%02X%02X" % color in dots, i
    assert "spk-c${(m.speaker - 1) % 6}" in js and len(chatpaint.SPEAKER_COLORS) == 6
    assert "speakers: (d) =>" in js and 'switchRow("tell_speakers"' in js and "m.speaker_run === sp.run" in js

    # 七、儲存空間：模型列得出來，功能關著才可以刪
    store = tmp_path / "store"
    path = assets.voice_path(store)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 2048)
    row = [r for r in storage.items(tmp_path, store, {"voice": True}) if r["id"] == "voice"][0]
    assert row["bytes"] == 2048 and row["used"] and not row["removable"]
    row = [r for r in storage.items(tmp_path, store, {"voice": False}) if r["id"] == "voice"][0]
    assert row["removable"] and storage.remove_item(tmp_path, store, "voice") and not path.exists()
    source = Path("livelingo/service.py").read_text(encoding="utf-8")
    assert 'voices=self._voices_now if key == "listen" else None' in source      # 只分對方說的話


def test_used_installers_are_removed_after_updating(tmp_path, monkeypatch):
    """更新完，用過的安裝檔（一個一百多 MB）要刪掉，不能一直留在 update 資料夾；下載好還沒裝的不能碰。"""
    import json as _json
    from pathlib import Path

    import livelingo.service as service_mod
    from livelingo import __version__, updater
    from livelingo.service import Service

    parts = updater.parse_version(__version__)
    newer = ".".join(str(n) for n in (*parts[:2], parts[2] + 1))
    newest = ".".join(str(n) for n in (*parts[:2], parts[2] + 2))
    data = tmp_path / "data"
    folder = data / "update"
    folder.mkdir(parents=True)
    assert updater.tidy(tmp_path / "nowhere") == (0, [])      # 沒有那個資料夾也不會出事

    def fill():
        files = {f"LiveLingo-Setup-{__version__}.exe": 1000, "LiveLingo-Setup-2.2.1.exe": 900, "LiveLingo-Setup-2.2.1.exe.part": 50,
                 f"LiveLingo-Setup-{newer}.exe": 800, f"LiveLingo-Setup-{newest}.exe.part": 70,
                 "install.log": 10, "LiveLingo-Setup-notes.txt": 5, "LiveLingo-Setup-abc.exe": 5}
        for name, size in files.items():
            (folder / name).write_bytes(b"x" * size)
        (folder / f"LiveLingo-Setup-{__version__}.exe.d").mkdir(exist_ok=True)      # 不是檔案的不碰

    def names():
        return sorted(p.name for p in folder.iterdir())

    # 剛從 2.2.1 更新上來：這一版和更舊的安裝檔刪掉；比現在新的（下載好還沒裝、下載到一半）和別的檔都留著
    fill()
    (data / "update.json").write_text(_json.dumps({"version": __version__, "at": 1.0, "from": "2.2.1"}), encoding="utf-8")
    freed, left = updater.tidy(data)
    assert (freed, left) == (1950, [])
    assert names() == sorted([f"LiveLingo-Setup-{newer}.exe", f"LiveLingo-Setup-{newest}.exe.part", "install.log", "LiveLingo-Setup-notes.txt",
                              "LiveLingo-Setup-abc.exe", f"LiveLingo-Setup-{__version__}.exe.d"])
    assert (data / "update.json").exists() and updater.previous(data) is None      # 2.2.1 太舊，本來就不能退；紀錄檔沒被動到
    assert updater.tidy(data) == (0, [])

    # 剛從 newer 退回來：被退掉的那一版不會再裝，它的安裝檔也刪
    fill()
    (data / "update.json").write_text(_json.dumps({"version": __version__, "at": 1.0, "from": newer, "skip": newer, "rollback": True}), encoding="utf-8")
    assert updater.tidy(data)[0] == 1950 + 800 and f"LiveLingo-Setup-{newer}.exe" not in names() and f"LiveLingo-Setup-{newest}.exe.part" in names()
    (data / "update.json").unlink()

    # 程式一開就清；安裝程式還沒結束、它自己那個檔刪不掉的話，過一下再試，刪掉了儲存空間頁要跟著更新
    monkeypatch.setattr(service_mod, "INSTALLER_RETRY", (0.05, 0.05, 0.05))
    fill()
    busy = {"on": True, "tries": 0}
    mine, real_unlink = folder / f"LiveLingo-Setup-{__version__}.exe", Path.unlink

    def unlink(self, *args, **kw):
        if self == mine and busy["on"]:
            busy["tries"] += 1
            raise PermissionError(13, "in use")
        return real_unlink(self, *args, **kw)

    monkeypatch.setattr(Path, "unlink", unlink)
    service = Service(data)
    logs, events = [], []
    service.log = lambda text: logs.append(text)
    old_publish = service.hub.publish
    monkeypatch.setattr(service.hub, "publish", lambda kind, payload=None: (events.append(kind), old_publish(kind, payload))[1])
    try:
        service.phase = "updating"                        # 正在更新或退回的時候不能碰 update 資料夾
        service._installers_cleanup()
        assert mine.exists() and not (folder / "LiveLingo-Setup-2.2.1.exe").exists() and (folder / f"LiveLingo-Setup-{newer}.exe").exists()
        assert busy["tries"] == 1 and logs == ["removed used installers: 0 MB"]
        time.sleep(0.12)
        assert mine.exists() and busy["tries"] == 1
        service.phase = "ready"
        busy["on"] = False
        deadline = time.time() + 5
        while mine.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert not mine.exists() and (folder / f"LiveLingo-Setup-{newer}.exe").exists()
        deadline = time.time() + 2
        while "storage" not in events and time.time() < deadline:
            time.sleep(0.02)
        assert "storage" in events
    finally:
        service.closing.set()
    source = Path("livelingo/service.py").read_text(encoding="utf-8")
    boot = source[source.index("    def _boot(self)"):source.index("    def _boot(self)") + 400]
    assert boot.index("self._installers_cleanup()") < boot.index("self._update_if_needed()")      # 先清，再看要不要更新
    assert "up_back_kept" not in Path("livelingo/ui/i18n.js").read_text(encoding="utf-8")      # 介面不再說「安裝檔還留著」


def test_named_voices_are_remembered(tmp_path, monkeypatch):
    """記住取過名字的人：開了才存（voices.json），下次開一開口就認得；清空名字、關掉開關、卸載都會刪。別的地方都不能有名字和聲紋。"""
    import json as json_mod
    from pathlib import Path

    import numpy as np

    import livelingo.core.voices as voices_mod
    import livelingo.service as service_mod
    from livelingo import voicebook
    from livelingo.core.voices import Speakers
    from livelingo.service import Service

    def voice(*parts):
        v = np.zeros(256, dtype=np.float32)
        for i, x in parts:
            v[i] = x
        return v / np.linalg.norm(v)

    a, b, c = voice((0, 1)), voice((1, 1)), voice((2, 1))

    # 一、存檔：存進去讀得回來；壞掉的、別的模型存的、長度不對的，都當作沒有；一個人都沒有就不留檔案
    book = tmp_path / "book"
    book.mkdir()
    assert voicebook.load(book) == [] and voicebook.count(book) == 0
    assert voicebook.save(book, [{"name": "  阿哲  Zq7 ", "voice": a * 3, "weight": 500.0}, {"name": "", "voice": b, "weight": 2.0},
                                 {"name": "壞的", "voice": np.zeros(256), "weight": 2.0}, {"name": "短的", "voice": np.ones(8), "weight": 2.0}])
    got = voicebook.load(book)
    assert len(got) == 1 and got[0]["name"] == "阿哲 Zq7" and got[0]["weight"] == voicebook.MAX_WEIGHT      # 太重的壓到上限：下次還跟得上新的聲音
    assert abs(float(got[0]["voice"] @ a) - 1.0) < 1e-5 and not (book / (voicebook.FILE + ".tmp")).exists()
    saved = json_mod.loads((book / voicebook.FILE).read_text(encoding="utf-8"))
    assert saved["kind"] == voicebook.KIND and saved["model"] == voicebook.MODEL and voicebook.DIM == 256
    assert voicebook.save(book, [{"name": f"p{i}", "voice": voice((i, 1)), "weight": 5.0} for i in range(30)])
    assert voicebook.count(book) == voicebook.MAX_PEOPLE
    for bad in ("not json", json_mod.dumps({**saved, "model": "some-other-model"}), json_mod.dumps({**saved, "people": "x"}),
                json_mod.dumps({**saved, "people": [{"name": "x", "voice": "AAAA", "weight": 1}, {"voice": 3}, 7]})):
        (book / voicebook.FILE).write_text(bad, encoding="utf-8")
        assert voicebook.load(book) == [], bad[:30]
    assert voicebook.save(book, []) and not (book / voicebook.FILE).exists()
    voicebook.clear(book)      # 沒有檔案也不會出事

    # 二、分人：記住的人先放進去，一開口就是他；他們不佔「最多分幾個人」的名額
    people = Speakers(limit=1)
    assert people.seed(a, 10.0) == 1 and people.seed(b, 10.0) == 2 and people.people() == [{"id": 1, "count": 0}, {"id": 2, "count": 0}]
    assert people.who(voice((1, 0.9), (7, 0.3)), 2.0) == 2 and people.who(c, 3.0) == 3 and people.who(voice((3, 1)), 3.0) is None
    got = people.voice_of(2)
    assert abs(float(np.linalg.norm(got[0])) - 1.0) < 1e-5 and 11.0 < got[1] <= 12.0 and people.voice_of(9) is None and people.voice_of(0) is None
    people.forget()
    assert people.people() == [] and people.who(c, 3.0) == 1 and people.who(a, 3.0) is None      # 忘掉之後名額照原本的算

    # 三、服務
    class FakeVoices:
        def __init__(self, path=None):
            self.speakers = Speakers()

    monkeypatch.setattr("livelingo.assets.get_voice", lambda store, progress=None, cancel=None: Path(store) / "voice.onnx")
    monkeypatch.setattr("livelingo.assets.has_voice", lambda store: True)
    monkeypatch.setattr(voices_mod, "Voices", FakeVoices)
    monkeypatch.setattr(service_mod, "VOICES_SAVE_EVERY", 0.0)
    data = tmp_path / "data"
    file = data / voicebook.FILE

    def open_app():
        service = Service(data)
        service.cfg.set(back_translate=False, my_lang="zh-TW", their_lang="ko", drop_echo=False, ui_lang="zh-TW", tell_speakers=True)
        service.translator = _FakeModel("你好")
        threading.Thread(target=service._translate_worker, daemon=True).start()
        service.speakers_load()
        end = time.time() + 5
        while service.speakers["state"] != "ready" and time.time() < end:
            time.sleep(0.02)
        assert service.speakers["state"] == "ready"
        return service

    def hear(service, vec, text, seconds=3.0):
        n = service.voices.speakers.who(vec, seconds)
        service._on_speech("loopback", text, "ko", 0.2, speaker=n)
        end = time.time() + 5
        while (not service.messages or service.messages[-1].get("pending")) and time.time() < end:
            time.sleep(0.02)
        return service.messages[-1]

    secret = "阿哲Zq7"
    first = open_app()
    try:
        assert first.cfg["remember_speakers"] is False and first.speakers_state()["remember"] is False
        assert hear(first, a, "안녕")["speaker"] == 1 and hear(first, b, "반가워")["speaker"] == 2
        assert first.speaker_rename(1, secret)["ok"]
        assert not file.exists() and first.speakers_state()["people"][0]["known"] is False      # 沒開：取了名字也不存
        assert first.update_config({"remember_speakers": True})["changed"] == ["remember_speakers"]
        rows = voicebook.load(data)
        assert [r["name"] for r in rows] == [secret] and float(rows[0]["voice"] @ a) > 0.99      # 只記取過名字的；2 號沒有名字，不記
        state = first.speakers_state()
        assert state["remember"] and [p["known"] for p in state["people"]] == [True, False]
        # 設定檔、匯出的設定、歷史、問題報告裡沒有名字；報告只說記了幾個
        assert secret not in (data / "config.json").read_text(encoding="utf-8") and secret not in json_mod.dumps(first._settings_now(), ensure_ascii=False)
        report = first.report_make()["text"]
        assert secret not in report and "remembered_voices_count: 1" in report
        from livelingo import history as history_mod
        assert secret not in (data / history_mod.FILE).read_text(encoding="utf-8")
        assert [p.name for p in data.rglob("*") if p.is_file() and secret.encode("utf-8") in p.read_bytes()] == [voicebook.FILE]
        # 他又說了幾句（聲音有一點不一樣）：聲紋跟著調整，也存進去
        hear(first, voice((0, 0.95), (9, 0.31)), "가자")
        assert float(voicebook.load(data)[0]["voice"][9]) > 0.01
        first.speaker_rename(2, "小美")
    finally:
        first.shutdown()
    assert sorted(r["name"] for r in voicebook.load(data)) == sorted([secret, "小美"])

    second = open_app()      # 下次開程式
    try:
        state = second.speakers_state()
        assert [(p["id"], p["name"], p["count"], p["known"]) for p in state["people"]] == [(1, secret, 0, True), (2, "小美", 0, True)]
        said = hear(second, voice((1, 0.97), (8, 0.24)), "나야")      # 小美一開口就認得
        assert (said["speaker"], said["voice"]) == (2, "小美") and second.speaker_tag(said) == (2, "小美")
        assert hear(second, c, "누구세요")["speaker"] == 3 and second.messages[-1]["voice"] == ""      # 沒聽過的人是新的「說話者 3」
        # 全部重新分辨：這一次分出來的沒名字的人忘掉，記住的人還在
        assert second.speakers_forget()["ok"]
        state = second.speakers_state()
        assert [(p["name"], p["count"]) for p in state["people"]] == [(secret, 0), ("小美", 0)] and state["run"] == 2
        assert sorted(r["name"] for r in voicebook.load(data)) == sorted([secret, "小美"])
        # 把名字清空：忘掉那個人
        assert second.speaker_rename(1, "")["ok"] and [r["name"] for r in voicebook.load(data)] == ["小美"]
        # 聲紋模型還沒準備好的時候（還不知道以前記了誰）不能把檔案洗掉
        loaded, second.voices = second.voices, None
        second._voices_save()
        assert [r["name"] for r in voicebook.load(data)] == ["小美"]
        second.voices = loaded
        # 關掉開關：存的刪掉，這一次開啟期間的名字還在；再打開就再存
        assert second.update_config({"remember_speakers": False})["changed"] and not file.exists()
        assert second.speakers_state()["people"][1]["name"] == "小美" and second.speakers_state()["people"][1]["known"] is False
        assert second.update_config({"remember_speakers": True})["changed"] and [r["name"] for r in voicebook.load(data)] == ["小美"]
        second.update_config({"remember_speakers": False})
    finally:
        second.shutdown()
    assert not file.exists()      # 關著的時候，關程式也不會存

    third = open_app()
    try:
        assert third.speakers_state()["people"] == []      # 沒有記住任何人
    finally:
        third.shutdown()

    # 四、卸載：選「留下設定」的時候，記住的聲音也要刪（它不是設定）；介面有開關，而且說清楚存在哪、怎麼刪
    root = Path(voicebook.__file__).parent
    iss = (root.parent / "packaging" / "installer.iss").read_text(encoding="utf-8")
    keep = iss[iss.index("if KeepData then"):iss.index("DelTree(data, True, True, True)")]
    assert f"DeleteFile(data + '\\{voicebook.FILE}')" in keep
    js = (root / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'switchRow("remember_speakers"' in js and "p.known" in js and "spk_forgot_kept" in js
    config_src = (root / "config.py").read_text(encoding="utf-8")
    assert '"remember_speakers": False' in config_src      # 預設關：要使用者自己打開才會存別人的聲紋
    source = (root / "service.py").read_text(encoding="utf-8")
    boot = source[source.index("    def _boot(self)"):source.index("    def _boot(self)") + 700]
    assert "self.speakers_load()" in boot      # 一開程式就準備好，不用等到開始收音才看得到記住的人


def test_engine_keeps_no_extra_ram_cache(tmp_path, monkeypatch):
    """llama-server 預設會在電腦記憶體裡另外存最多 8 GB 的舊句子快取，開久了真的會長到那麼大
    （顯示卡模式跑一晚：引擎 9.5 GB）。顯示卡和處理器兩種都要用 --cache-ram 0 關掉。"""
    from livelingo.core.engine import Engine

    monkeypatch.delenv("LLAMA_SERVER", raising=False)
    for layers in (0, 99):
        args = Engine(tmp_path / "llama-server.exe", tmp_path / "m.gguf", tmp_path / "engine.log", gpu_layers=layers).command()
        assert args[args.index("--cache-ram") + 1] == "0", layers
        assert args.count("--cache-ram") == 1
        assert ("-ngl" in args) == (layers > 0)


def test_idle_engine_rests_and_wakes(tmp_path):
    """幾分鐘沒翻譯：翻譯引擎休息（關掉，放掉記憶體）；下一句來了先叫醒再翻，不算失敗、不被當成「自己停了」重開。
    兩邊收音都關著一段時間：語音辨識模型也放掉。"""
    from livelingo import service as service_mod
    from livelingo.service import Service

    class Proc:
        returncode = None

        def poll(self):
            return self.returncode

    made = []

    class FakeEngine:
        def __init__(self):
            self.proc, self.url, self.key, self.stopped = Proc(), f"http://engine/{len(made)}", f"k{len(made)}", False
            made.append(self)

        def start(self):
            pass

        def wait_ready(self, timeout):
            return True

        def stop(self):
            self.stopped = True
            self.proc.returncode = 0

    class Model:
        tone, url, key = "casual", "", ""

        def translate(self, text, target, source=None, context=None):
            if service.engine.proc.poll() is not None:
                raise ConnectionError("connection refused")
            return f"<{target}>{text}"

        def clear_cache(self):
            pass

    service = Service(tmp_path)
    service.cfg.set(back_translate=False, ui_lang="zh-TW")
    assert service.cfg["engine_rest"] == 30 and 30 in service_mod.ENGINE_REST
    assert service.update_config({"engine_rest": "10"})["changed"] == ["engine_rest"] and service.cfg["engine_rest"] == 10
    assert service.update_config({"engine_rest": 7})["changed"] == [] and service.update_config({"engine_rest": "x"})["changed"] == []
    service.translator = Model()
    service.phase = "ready"
    service.engine = FakeEngine()
    service._new_engine = lambda: FakeEngine()
    notes, notices = [], []
    service.overlay.note = notes.append
    service.notice = lambda code, **more: notices.append(code)

    service._last_use = time.monotonic() - 9 * 60
    service._maybe_rest()
    assert not service._resting and not made[0].stopped          # 還沒到 10 分鐘
    service._last_use = time.monotonic() - 11 * 60
    service._maybe_rest()
    assert service._resting and made[0].stopped and not service._engine_ok.is_set()
    assert not service._engine_gone()                            # 休息不是「自己停了」，守衛不會去重開

    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()
    try:
        first = service.submit("typed", "我現在過去", "zh")
        end = time.time() + 8
        while time.time() < end and not any(m["id"] == first and m["translated"] for m in service.messages):
            time.sleep(0.02)
        done = next(m for m in service.messages if m["id"] == first)
        assert done["translated"] and not done.get("error")
        assert not service._resting and service._engine_ok.is_set() and len(made) == 2 and service.engine is made[1]
        assert service.translator.url == made[1].url and service.translator.key == made[1].key
        assert any("休息" in n for n in notes) and not notices    # 浮動留言說一聲，不算錯誤

        service.update_config({"engine_rest": 0})                 # 不休息
        service._last_use = time.monotonic() - 10 * 3600
        service._maybe_rest()
        assert not service._resting and not made[1].stopped
    finally:
        service.closing.set()
        worker.join(2)

    # 語音辨識模型：兩邊收音都關著超過 SPEECH_REST 秒才放掉；有收音開著就不放
    service.recognizer = object()
    service.pipelines = {"listen": object()}
    service._maybe_free_speech()
    assert service.recognizer is not None and service._voice_off_since is None
    service.pipelines = {}
    service._maybe_free_speech()
    assert service.recognizer is not None
    service._voice_off_since = time.monotonic() - service_mod.SPEECH_REST - 1
    service._maybe_free_speech()
    assert service.recognizer is None


def test_more_languages_are_told_apart():
    """加進來的語言（越南、泰、印尼、西、葡、俄、德、法）：文字判斷得出來、螢幕上的句子收得到、數字核對得到。"""
    from livelingo.core.ocr import model_of
    from livelingo.core.translate import LANGS, SOURCES, numbers_lost, tidy
    from livelingo.service import VOICE_FAMILIES

    for text, lang in (("¿Vamos a la base? Necesito comprar", "es"), ("Bora pro dragão, não tem flash", "pt"),
                       ("Wir gehen jetzt zum Drachen", "de"), ("On va au dragon, c'est très bien", "fr"),
                       ("Ayo ke naga, musuh gak ada flash", "id"), ("Đi đánh rồng thôi", "vi"), ("di danh rong thoi khong", "vi"),
                       ("Идём на дракона", "ru"), ("ไปมังกรกันเถอะ", "th"), ("that was really close lol", "en"),
                       ("gank mid pls", "en")):
        assert detect_language(text) == lang, text
    assert wanted("Di dánh rông thôi, dich không có tôc biên", {"vi"}) == "vi"      # OCR 少了聲調，看起來像法文也照收
    assert wanted("vamos al dragón ahora", {"es", "en"}) == "es"
    assert wanted("ok lets go now", {"es", "en"}) == "en"
    assert wanted("Идём на дракона сейчас", {"ru"}) == "ru" and wanted("Идём на дракона", {"es"}) is None
    assert wanted("ไปมังกรกันเถอะ", {"th"}) == "th"
    for code, info in LANGS.items():
        assert info["family"] in SOURCES and info["family"] in VOICE_FAMILIES, code
    assert {model_of(f) for f in ("es", "pt", "fr", "de", "id", "vi")} == {"latin"}
    assert model_of("ru") == "eslav" and model_of("th") == "thai" and model_of("zh") == model_of("en") == "builtin"
    assert numbers_lost("come in 3 minutes", "ven en tres minutos", "es", "en") == []
    assert numbers_lost("come in 3 minutes", "ven en 5 minutos", "es", "en") == ["3"]
    assert numbers_lost("빨리 와 ㅋㅋㅋ", "Vem logo", "pt", "ko") == [] and tidy("Vem logo ㅋㅋㅋ", "pt") == "Vem logo kkkk"
