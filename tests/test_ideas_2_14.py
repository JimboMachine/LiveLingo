"""2.14.0 的十個點子：不翻的字和名字建議、數字補回、語音熱詞、開遊戲就開始翻、劇情對話框、念對方的話給我聽、
情境卡、隱私暫停、翻譯自我檢查。"""

import json
import time

import numpy as np


def test_keep_words_and_name_suggestions():
    from livelingo import names

    assert names.clean_words("gg, Faker，ㅋㅋ、gg\n  ") == ["gg", "Faker", "ㅋㅋ"]
    assert names.clean_words(["a", "A", " b  c "]) == ["a", "b c"] and names.clean_words(None) == []
    assert len(names.clean_words([f"w{i}" for i in range(200)])) == names.MAX_WORDS
    # 像遊戲 ID 的：有數字、底線、大小寫混著；一般英文字、版本號不算
    assert names.looks_like_id("Faker_99") and names.looks_like_id("xXSniperXx") and names.looks_like_id("hide0nbush")
    assert not names.looks_like_id("Hello") and not names.looks_like_id("ps5") and not names.looks_like_id("v1.2")
    # 整句英文：只算像 ID 的（不然每個大寫開頭的字都會被問）
    assert names.candidates("Hello there Faker_99 nice play") == ["Faker_99"]
    # 韓文句子裡夾著的英文字：多半是名字，常見字（gg、lol）和全大寫的縮寫除外
    assert names.candidates("Faker 오늘 잘한다 gg MVP") == ["Faker"]
    watch = names.NameWatch()
    assert watch.add("Faker 오늘 잘한다") == "" and watch.add("Faker 진짜") == ""
    assert watch.add("faker 미쳤다") == "faker"                      # 第三次：問一次（不分大小寫）
    assert "faker" in watch.asked and watch.add("Faker 또") == ""      # 問過就不再問
    other = names.NameWatch()
    for _ in range(3):
        assert other.add("Chovy 잘해", skip=["chovy"]) == ""     # 已經在不翻的字、或說過不是名字的：不問
    # 語音辨識的熱詞：只放這個語言會講到的，1 個字的不放
    assert names.hotwords("ko", ["Faker", "페이커", "王", "大魔王"]) == "Faker 페이커"
    assert names.hotwords("zh", ["Faker", "페이커", "大魔王"]) == "Faker 大魔王"
    long = names.hotwords("en", [f"Name{i:03d}" for i in range(100)])
    assert len(long) <= names.MAX_HOT_CHARS and len(long.split()) <= names.MAX_HOT


def test_numbers_are_put_back_and_keep_words_stay():
    from livelingo.core.translate import repair_numbers, tidy

    assert repair_numbers("3點見", "30시에 봐.", "ko", "zh") == "3시에 봐."
    assert repair_numbers("2시 30분에 시작해", "20點3分開始", "zh-TW", "ko") == "2點30分開始"
    assert repair_numbers("3點見", "3시에 봐.", "ko", "zh") == "3시에 봐."                # 對的不動
    assert repair_numbers("3點和5點", "3시에 봐.", "ko", "zh") == "3시에 봐."              # 個數不一樣：不動，留給提醒
    assert repair_numbers("3點見", "세 시에 봐.", "ko", "zh") == "세 시에 봐."              # 寫成字也算有
    # ㅋㅋ 平常會換成看得懂的笑；設成不翻的字就照原樣留著
    assert "ㅋ" not in tidy("ㅋㅋ 웃기다", "zh-TW")
    assert "ㅋㅋ" in tidy("ㅋㅋ 웃기다", "zh-TW", keep=["ㅋㅋ"])


def test_translator_fresh_skips_cache_and_memory(tmp_path):
    from livelingo.core.translate import Translator

    tr = Translator("http://engine")
    asked = []
    tr._ask = lambda body, *a, **kw: (asked.append(body), "안녕")[1]
    first = tr.translate("你好", "ko", "zh")
    again = tr.translate("你好", "ko", "zh")
    assert first == again and len(asked) == 1                              # 平常：同一句用快取
    tr.translate("你好", "ko", "zh", fresh=True)
    assert len(asked) == 2                                                 # 自我檢查：每次真的重翻


def test_quality_check_scores_and_compares(tmp_path):
    from livelingo import qualitycheck as qc

    cases = qc.load_cases()
    assert len(cases) >= 20 and len({c["id"] for c in cases}) == len(cases)
    assert qc.judge({"src": "3點開會", "lang": "zh", "target": "en", "numbers": True}, "Meeting at 3.") == []
    assert qc.judge({"src": "3點開會", "lang": "zh", "target": "en", "numbers": True}, "Meeting at 5.") == ["numbers"]
    assert qc.judge({"src": "어디야?", "lang": "ko", "target": "zh-TW", "kind": "question"}, "你在哪裡。") == ["not_question"]
    assert qc.judge({"src": "빨리 와", "lang": "ko", "target": "zh-TW", "kind": "statement"}, "快來嗎？") == ["became_question"]
    assert qc.judge({"src": "我是台北人", "lang": "zh", "target": "ko", "has": ["타이베이"], "not": ["서울"]}, "저는 서울 사람이에요.") == ["has", "not"]
    assert qc.judge({"src": "빨리 와", "target": "zh-TW"}, "") == ["empty"]

    changes = []
    check = qc.QualityCheck(tmp_path, on_change=changes.append)
    small = [{"id": "a", "src": "3點開會", "lang": "zh", "target": "en", "numbers": True},
             {"id": "b", "src": "빨리 와", "lang": "ko", "target": "zh-TW", "kind": "statement"}]
    answers = {"3點開會": "Meeting at 3.", "빨리 와": "快來。"}

    def run():
        assert check.start(lambda case: answers[case["src"]], "2.14.0", "4b", cases=small)
        end = time.time() + 5
        while check.running and time.time() < end:
            time.sleep(0.01)

    run()
    first = check.state()
    assert first["last"]["passed"] == 2 and first["last"]["total"] == 2 and first["worse"] == [] and changes
    answers["3點開會"] = "Meeting at 5."
    run()
    second = check.state()
    assert second["last"]["passed"] == 1 and second["worse"] == ["a"] and second["previous"]["passed"] == 2
    for _ in range(qc.MAX_RUNS + 2):
        run()
    assert len(json.loads((tmp_path / "quality.json").read_text(encoding="utf-8"))["runs"]) == qc.MAX_RUNS


def test_private_pause_keeps_nothing(tmp_path, monkeypatch):
    from livelingo import oswin
    from livelingo.service import Service

    monkeypatch.setattr(oswin, "copy_text", lambda text: True)
    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    try:
        base = {"who": "them", "via": "screen", "lang": "ko", "target": "zh-TW", "at": 1_800_000_000.0, "copied": False,
                "back": "", "pron": "", "pending": False, "error": ""}
        service._post({**base, "id": 1, "original": "안녕", "translated": "你好"})
        assert service.history.count == 1
        assert service.set_private(True) == {"ok": True, "on": True} and ("private", {"on": True}) in events
        assert service.state()["private"] is True
        service._post({**base, "id": 2, "original": "비밀", "translated": "秘密"})
        assert service.history.count == 1                                  # 暫停中：不記
        for _ in range(4):
            service._watch_names({**base, "original": "Faker 오늘 잘한다"})
        assert not [e for e in events if e[0] == "name_suggest"]          # 暫停中：也不數名字
        service.set_private(False)
        service._post({**base, "id": 3, "original": "다시", "translated": "再一次"})
        assert service.history.count == 2
        assert service.cfg["hotkeys"]["private"] == "Ctrl+Shift+H" and Service(tmp_path / "again").private is False
    finally:
        service.shutdown()


def test_name_suggestion_answers_and_keep_words_reach_the_translator(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    try:
        msg = {"who": "them", "via": "heard", "original": "Nayeon7 오늘 잘한다"}
        service._watch_names(dict(msg, who="me"))                         # 我自己說的：不算
        for _ in range(3):
            service._watch_names(msg)
        assert [d for n, d in events if n == "name_suggest"] == [{"name": "Nayeon7"}]
        for _ in range(3):
            service._watch_names(dict(msg, original="Faker 오늘 잘한다"))      # 詞彙表裡已經有的（內建的選手名）：不問
        assert len([n for n, _ in events if n == "name_suggest"]) == 1
        assert service.name_answer("Nayeon7", True)["ok"] and service.cfg["keep_words"] == ["Nayeon7"]
        assert service._words_now()["Nayeon7"] == "Nayeon7"                   # 翻譯時當成「Nayeon7 = Nayeon7」
        assert service.name_answer("Chovy", False)["ok"] and service.cfg["name_skip"] == ["Chovy"]
        assert service.name_answer("  ", True) == {"ok": False}
        assert service.update_config({"keep_words": "gg, ㅋㅋ"})["changed"] == ["keep_words"]
        assert service.cfg["keep_words"] == ["gg", "ㅋㅋ"] and service.translator.keep() == ["gg", "ㅋㅋ"]
    finally:
        service.shutdown()


def test_game_start_only_stops_what_it_started(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    notes, notices, calls = [], [], []
    service.overlay.note = notes.append
    service.notice = lambda code, **kw: notices.append(code)
    service.set_source = lambda key, on: (calls.append((key, on)), service.sources[key].update(on=on))
    try:
        service.phase = "ready"
        service.cfg.set(regions=[{"id": "r1", "left": 0, "top": 0, "width": 100, "height": 50, "app": "game.exe"}])
        game = [{"exe": "Game.exe", "title": "Game"}]
        assert service.check_auto_start(game) == []                       # 沒打開這個設定：不動
        service.update_config({"game_start": True})
        assert service.check_auto_start(game) == ["screen", "listen"] and notes
        assert service.check_auto_start(game) == []                        # 一直開著：不再動
        service.set_source("mic", True)                                    # 使用者自己開的麥克風
        assert service.check_auto_start([]) == ["screen", "listen"] and "auto_stopped" in notices
        assert service.sources["mic"]["on"] is True                        # 自己開的不關
        service.set_source("listen", True)                                 # 使用者先開了聽：遊戲開了只補開畫面
        assert service.check_auto_start(game) == ["screen"]
        assert service.check_auto_start([]) == ["screen"] and service.sources["listen"]["on"] is True
        service.phase = "starting"
        assert service.check_auto_start(game) == []
    finally:
        service.shutdown()


def test_scenarios_change_display_and_sources(tmp_path):
    from livelingo.service import SCENARIOS, Service

    service = Service(tmp_path)
    calls = []
    service.set_source = lambda key, on: (calls.append((key, on)), service.sources[key].update(on=on))
    try:
        assert set(Service.scenarios_state()) == set(SCENARIOS) == {"game", "video", "call", "stream"}
        assert service.state()["scenarios"] == Service.scenarios_state()
        lang = service.cfg["my_lang"]
        done = service.apply_scenario("game")
        assert done == {"ok": True, "name": "game", "need_region": True}   # 還沒框區域：提醒先框
        assert service.cfg["overlay_form"] == "float" and ("listen", True) in calls and ("screen", True) in calls
        calls.clear()
        assert service.apply_scenario("video")["need_region"] is False
        assert service.cfg["overlay_form"] == "bar" and ("screen", False) in calls and service.cfg["my_lang"] == lang
        assert service.apply_scenario("call")["ok"] and service.cfg["drop_echo"] is True and service.sources["mic"]["on"]
        assert service.apply_scenario("nope") == {"ok": False, "error": "unknown"}
    finally:
        service.shutdown()


def test_read_their_words_to_me(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    said = []
    service.reader.say = lambda text, lang: said.append((text, lang)) or True
    try:
        me = service.cfg["my_lang"]
        msg = {"who": "them", "via": "heard"}
        service._read_heard(msg, me, "你好")
        assert said == []                                                  # 沒打開：不念
        service.update_config({"speak_heard": True})
        service._read_heard(msg, me, "你好")
        service._read_heard(dict(msg, who="me"), me, "我說的")             # 我說的：不念
        service._read_heard(dict(msg, via="screen"), me, "畫面上的")       # 畫面上的：要另外打開
        service._read_heard(msg, "xx", "不是翻成我的語言")
        assert said == [("你好", me)]
        service.update_config({"speak_heard_screen": True})
        service._read_heard(dict(msg, via="screen"), me, "畫面上的")
        assert said[-1] == ("畫面上的", me)
        # 念出來的聲音被麥克風收到：認得是自己念的，不再翻一次
        service.reader._recent.append((time.monotonic(), "對方說他快到了"))
        assert service._is_own_voice("對方說他快到了")
    finally:
        service.shutdown()


def test_story_box_waits_until_the_text_stops(monkeypatch):
    import livelingo.core.watcher as watcher_mod
    from livelingo.core.watcher import ScreenWatcher

    reads = iter(["你", "你好", "你好啊", "你好啊"])
    seen = []

    class Ocr:
        family = "zh"

        def read(self, bgr):
            return [next(reads)]

    monkeypatch.setattr(watcher_mod, "message_lines", lambda lines: [])
    frames = [np.full((50, 100, 3), v, np.uint8) for v in (10, 60, 110, 160)]
    w = ScreenWatcher(Ocr(), on_message=lambda *a: None, grab=lambda place: frames[0])
    w.on_frame = lambda region, place, bgr, raw: seen.append(raw) or True
    w.set_regions([{"id": "r1", "left": 0, "top": 0, "width": 100, "height": 50, "story": True}])
    slot = w._slots[0]
    w._look(slot, lambda place: frames[0])
    w._look(slot, lambda place: frames[0])          # 停下來了：讀到「你」，先不翻
    assert seen == [] and slot.story_text == "你"
    w._look(slot, lambda place: frames[0])          # 畫面沒變但還在等：再讀一次「你好」，還在變
    assert seen == [] and slot.story_text == "你好"
    w._look(slot, lambda place: frames[0])          # 「你好啊」
    w._look(slot, lambda place: frames[0])          # 又是「你好啊」：打完了，翻
    assert seen == [["你好啊"]] and slot.story_text is None


def test_wiring_for_2_14_0():
    """介面、伺服器、說明頁都接上了：每個新字 13 種語言都有，每個新按鈕都有人處理。"""
    import re
    from pathlib import Path

    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS
    from livelingo.hotkeys import NOTES

    root = Path(__file__).resolve().parent.parent / "livelingo"
    i18n = (root / "ui" / "i18n.js").read_text(encoding="utf-8")
    added = set(re.findall(r"\b((?:hk_private|gs|story|heard|keep|name|qc|scen|priv)_?\w*): \"", i18n))
    assert len(added) >= 40
    for key in added | {"n_auto_stopped"}:
        assert len(re.findall(rf"\b{key}: \"", i18n)) == len(UI_LANGS), key
    for lang in UI_LANGS:
        assert {"private_on", "private_off", "auto_started"} <= set(NOTES[lang]), lang
    for key in ("keep_words", "name_suggest", "name_skip", "game_start", "speak_heard", "speak_heard_screen",
                "speak_heard_device", "speak_heard_name"):
        assert key in DEFAULTS and key in EDITABLE, key
    server = (root / "server.py").read_text(encoding="utf-8")
    for route, call in (("/api/names", "name_answer("), ("/api/private", "set_private("), ("/api/scenario", "apply_scenario("),
                        ("/api/quality", "quality_action(")):
        assert f'route == "{route}"' in server and f"service.{call}" in server, route
    app = (root / "ui" / "app.js").read_text(encoding="utf-8")
    for act in ("name-keep", "name-no", "private", "scenario", "quality-check", "region-story"):
        assert f'data-act="{act}"' in app and f'"{act}"' in app.replace(f'data-act="{act}"', ""), act
    html = (root / "ui" / "index.html").read_text(encoding="utf-8")
    assert all(f'id="{i}"' in html for i in ("btn-private", "scen-row", "priv-row", "name-row"))
    help_data = json.loads((root / "data" / "help.json").read_text(encoding="utf-8"))
    assert all("{private}" in next(s for s in v["sections"] if s["id"] == "keys")["items"][0] for v in help_data.values())
