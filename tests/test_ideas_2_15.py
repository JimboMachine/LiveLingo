"""2.15.0 的十個點子：斷句修補、讀錯字修正、沒把握的句子再翻一次比對和灰掉／收起來、縮寫提醒、教過的句子留版本、
粵語、每週錯誤報告、語助詞不出字幕、網路用語註解。"""

import json
import time


def test_join_split_sentences():
    from livelingo import textfix

    assert textfix.should_join("내일 저녁에", "같이 게임할래?", 0.6, "ko")
    assert textfix.should_join("I think we should", "go mid now", 0.4, "en")
    assert not textfix.should_join("내일 저녁에 보자.", "그래", 0.3, "ko")            # 前半句說完了
    assert not textfix.should_join("내일 저녁에", "같이 게임할래?", 3.0, "ko")         # 隔太久：新的一句
    assert not textfix.should_join("내일 저녁에", "같이", -1, "ko")
    long = "我們今天晚上要不要一起去吃那家新開的火鍋店"
    assert not textfix.should_join(long, "好不好", 0.5, "zh")                        # 前半句很長：不是沒說完
    assert textfix.join("我們等一下", "去吃飯吧", "zh") == "我們等一下去吃飯吧"
    assert textfix.join("I think we should,", "go mid", "en") == "I think we should go mid"


def test_ocr_fixes_only_what_is_surely_wrong():
    from livelingo.textfix import fix_ocr

    assert fix_ocr("1O:3O에 시작") == "10:30에 시작"
    assert fix_ocr("2O분 남음") == "20분 남음"
    assert fix_ocr("0ㅋ 가자") == "ㅇㅋ 가자" and fix_ocr("ㅇ0") == "ㅇㅇ" and fix_ocr("7ㄱ") == "ㄱㄱ"
    for keep in ("hello1", "lol", "Faker_99", "hide0nbush", "IO", "LOL 1:0", "ㅋㅋㅋ", "오늘 3시"):
        assert fix_ocr(keep) == keep, keep


def test_fillers_are_dropped():
    from livelingo.textfix import only_filler

    for text in ("음...", "어어", "嗯嗯", "啊？", "um, uh", "Hmm.", "ㅋㅋㅋㅋ", "하하하", "(웃음)", "えーっと", "[Laughter]", "哈哈哈"):
        assert only_filler(text), text
    for text in ("응", "うん", "네", "好", "ok", "음 괜찮아", "um I think so", "哈哈好啊", "어디야"):
        assert not only_filler(text), text      # 「是」的意思、或有一個真的字：要翻


def test_abbreviation_reminder():
    from livelingo import textfix

    assert textfix.abbreviations("ㅈㅂ 하자 ㅋㅋ ㅎㅎ JGL AFK GLHF gg") == ["ㅈㅂ", "JGL"]
    watch = textfix.abbr_watch()
    for _ in range(textfix.ABBR_NEED - 1):
        assert watch.add("ㅈㅂ ㄱ") == ""
    assert watch.add("ㅈㅂ 하자") == "ㅈㅂ" and watch.add("ㅈㅂ") == ""
    other = textfix.abbr_watch()
    for _ in range(textfix.ABBR_NEED + 1):
        assert other.add("ㄱㄱ", skip=["ㄱㄱ"]) == ""          # 用語包裡已經有的：不問


def test_lessons_keep_old_versions_and_undo(tmp_path):
    from livelingo.config import Lessons

    lessons = Lessons(tmp_path)
    assert lessons.add("가자", "走吧", "ko", "zh-TW", before="去吧")
    assert lessons.list()[0]["old"] == ["去吧"]
    lessons.add("가자", "我們走", "ko", "zh-TW", before="走吧")
    lessons.add("가자", "出發", "ko", "zh-TW")
    assert lessons.list()[0]["dst"] == "出發" and lessons.list()[0]["old"] == ["去吧", "走吧", "我們走"]
    assert Lessons(tmp_path).list()[0]["old"] == ["去吧", "走吧", "我們走"]      # 有存進檔案
    assert lessons.undo("가자", "zh-TW") == "我們走" and lessons.undo("가자", "zh-TW") == "走吧"
    assert lessons.undo("가자", "zh-TW") == "去吧" and lessons.undo("가자", "zh-TW") == ""
    assert "old" not in lessons.list()[0]                                         # 沒有舊的了：清單裡不帶
    for i in range(Lessons.KEEP_OLD + 3):
        lessons.add("또", f"v{i}", "ko", "zh-TW")
    assert len(next(i for i in lessons.list() if i["src"] == "또")["old"]) == Lessons.KEEP_OLD
    lessons.add("새", "新", "ko", "zh-TW")
    assert "old" not in lessons.list()[0]                                         # 第一次教、沒給舊譯文


def test_corrections_report(tmp_path):
    from livelingo.corrections import TOP, Corrections

    clock = {"now": 1_800_000_000.0}
    log = Corrections(tmp_path, now=lambda: clock["now"])
    assert not log.add("word", "a", "a") and not log.add("other", "a", "b") and not log.add("word", "a", "")
    for _ in range(3):
        log.add("heard", "패이커", "페이커", "ko")
    log.add("word", "ㅈㅂ", "解散")
    log.add("sentence", "가자", "走吧", "ko", "zh-TW")
    log.add("sentence", "가자", "出發", "ko", "zh-TW")
    report = log.report()
    assert report["total"] == 6 and report["kinds"] == {"heard": 3, "seen": 0, "sentence": 2, "word": 1}
    assert report["top"][0] == {"kind": "heard", "wrong": "패이커", "right": "페이커", "last": clock["now"], "n": 3}
    assert report["top"][1]["kind"] == "sentence" and report["top"][1]["right"] == "出發" and report["top"][1]["n"] == 2
    clock["now"] += 8 * 86400
    assert log.report()["total"] == 0                                             # 一週前的不算
    for i in range(TOP + 5):
        log.add("word", f"w{i}", "x")
    assert len(log.report()["top"]) == TOP
    log.clear()
    assert log.report()["total"] == 0 and not (tmp_path / "corrections.jsonl").exists()


class _Tr:
    """假的翻譯：second_opinion 回傳 self.second。"""

    def __init__(self, second):
        self.second = second

    def second_opinion(self, text, target, source=None):
        return self.second


def test_unsure_sentences_checked_twice_and_hidden(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    shown, removed = [], []
    service.overlay.on_message = lambda msg: shown.append(msg["id"])
    service.overlay.on_removed = lambda mid: removed.append(mid)
    try:
        msg = {"id": 1, "who": "them", "via": "heard", "original": "내일 보자", "lang": "ko", "unsure": True}
        service.translator.second_opinion = _Tr("明天見吧").second_opinion
        assert service._still_unsure(msg, "zh-TW", "明天見") is False              # 兩次意思差不多：不標
        service.translator.second_opinion = _Tr("我愛吃蘋果").second_opinion
        assert service._still_unsure(msg, "zh-TW", "明天見") is True
        service.translator.second_opinion = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("engine down"))
        assert service._still_unsure(msg, "zh-TW", "明天見") is True               # 翻不出來：照舊標著

        base = {"who": "them", "via": "heard", "lang": "ko", "target": "zh-TW", "at": 1_800_000_000.0, "copied": False,
                "back": "", "pron": "", "pending": False, "error": "", "original": "x", "translated": "y"}
        service._post(dict(base, id=10, unsure=True))
        assert shown == [10]
        service.update_config({"unsure_view": "hide"})
        service._post(dict(base, id=11, unsure=True))
        service._post(dict(base, id=12, unsure=False))
        assert shown == [10, 12] and removed == [11]                              # 收起來：浮動字幕不出
        assert service.update_config({"unsure_view": "nope"})["changed"] == [] and service.cfg["unsure_view"] == "hide"
    finally:
        service.shutdown()


def test_join_fillers_and_notes_in_the_service(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    sent, jobs = [], []
    service.jobs.put = lambda msg, asked=False: jobs.append(msg)
    real_submit = service.submit
    service.submit = lambda *a, **k: (sent.append(a), real_submit(*a, **k))[1]
    try:
        service._on_speech("loopback", "음...", "ko", seconds=0.5)
        assert sent == []                                                          # 只有語助詞：不翻
        service._on_speech("loopback", "내일 저녁에", "ko", seconds=1.0)
        assert len(sent) == 1
        first = service.messages[-1]
        service._post(dict(first, pending=False, translated="明天晚上"))           # 第一句翻好了
        service._on_speech("loopback", "같이 게임할래?", "ko", seconds=0.3, length=0.9)      # 緊接著：接回去
        assert len(sent) == 1 and len(service.messages) == 1
        assert service.messages[-1]["original"] == "내일 저녁에 같이 게임할래?" and service.messages[-1]["joined"]
        assert jobs[-1]["original"] == "내일 저녁에 같이 게임할래?"
        service.update_config({"join_split": False})
        service._post(dict(service.messages[-1], pending=False, translated="明天晚上要一起玩嗎"))
        service._on_speech("loopback", "그리고", "ko", seconds=0.5)
        assert len(sent) == 2                                                     # 關掉：各翻各的
        # 網路用語註解：用語包認得的（ㄱㄱ → 走吧）放在譯文下面
        notes = service._slang_notes({"who": "them", "original": "ㄱㄱ 빨리", "lang": "ko", "translated": "快點"}, "zh-TW")
        assert ["ㄱㄱ", "走吧"] in notes
        assert service._slang_notes({"who": "me", "original": "ㄱㄱ", "lang": "ko"}, "zh-TW") == []
        service.update_config({"slang_notes": False})
        assert service._slang_notes({"who": "them", "original": "ㄱㄱ", "lang": "ko"}, "zh-TW") == []
    finally:
        service.shutdown()


def test_abbr_answers_and_corrections_from_the_service(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    try:
        msg = {"who": "them", "via": "screen", "original": "ㅈㅂ 하자"}
        for _ in range(5):
            service._watch_abbr(msg)
        assert [d for n, d in events if n == "abbr_suggest"] == [{"word": "ㅈㅂ", "lang": ""}]
        assert service.abbr_answer("ㅈㅂ", "解散")["ok"] and service.glossary.as_dict()["ㅈㅂ"] == "解散"
        assert service.abbr_answer("ㅁㅊ", "") == {"ok": True} and service.cfg["abbr_skip"] == ["ㅁㅊ"]
        assert service.corrections_report()["kinds"]["word"] == 1                 # 加進詞彙表也記一筆
        service.set_private(True)
        service.glossary_add("秘密", "비밀")
        assert service.corrections_report()["kinds"]["word"] == 1                 # 隱私暫停：不記
        service.set_private(False)
        assert service.corrections_clear() == {"ok": True} and service.corrections_report()["total"] == 0
        assert service.state()["corrections"]["total"] == 0
    finally:
        service.shutdown()


def test_cantonese_uses_yue():
    import numpy as np

    from livelingo.core.speech import Recognizer

    rec = Recognizer.__new__(Recognizer)
    calls = []

    class Model:
        def transcribe(self, audio, language=None, **kw):
            calls.append(language)
            return iter([]), None

    rec.model, rec.beam, rec._quick, rec._now_window = Model(), 1, False, 15
    rec.hotwords = lambda lang: ""
    rec.cantonese = lambda: False
    rec._decode(np.zeros(10, np.float32), "zh")
    rec.cantonese = lambda: True
    rec._decode(np.zeros(10, np.float32), "zh")
    rec._decode(np.zeros(10, np.float32), "ko")
    assert calls == ["zh", "yue", "ko"]


def test_wiring_for_2_15_0():
    import re
    from pathlib import Path

    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS

    root = Path(__file__).resolve().parent.parent / "livelingo"
    i18n = (root / "ui" / "i18n.js").read_text(encoding="utf-8")
    keys = ("uv_hidden_line", "abbr_title", "abbr_ask", "lv_undo", "uv_title", "ur_title", "uv_hide", "sn_title", "wr_title", "wr_kind_seen",
            "vf_title", "js_title", "fil_title", "yue_title", "ocrfix_title", "abbr_saved")
    for key in keys:
        assert len(re.findall(rf"(?<![\w]){key}: \"", i18n)) == len(UI_LANGS), key
    for key in ("join_split", "ocr_fix", "drop_fillers", "unsure_recheck", "unsure_view", "abbr_suggest", "abbr_skip", "cantonese", "slang_notes"):
        assert key in DEFAULTS and key in EDITABLE, key
    server = (root / "server.py").read_text(encoding="utf-8")
    for route, call in (("/api/lessons/undo", "lesson_undo("), ("/api/abbr", "abbr_answer("), ("/api/corrections", "corrections_report(")):
        assert f'route == "{route}"' in server and f"service.{call}" in server, route
    app = (root / "ui" / "app.js").read_text(encoding="utf-8")
    for act in ("abbr-save", "abbr-no", "lesson-undo", "wr-clear", "unsure-show"):
        assert f'data-act="{act}"' in app and f'"{act}":' in app, act
    assert 'id="abbr-row"' in (root / "ui" / "index.html").read_text(encoding="utf-8")
    assert json.loads((root / "data" / "changes.json").read_text(encoding="utf-8"))


def test_join_waits_only_for_a_short_pause(tmp_path):
    """後半句和前半句中間停太久（這句的長度扣掉之後還超過 JOIN_GAP 秒）：當成新的一句。"""
    from livelingo.service import Service

    service = Service(tmp_path)
    service.jobs.put = lambda msg, asked=False: None
    try:
        service._on_speech("loopback", "내일 저녁에", "ko", seconds=0.2, length=1.0)
        service._post(dict(service.messages[-1], pending=False, translated="明天晚上"))
        service._recent_voice[-1]["end"] -= 4.0                                   # 上一句是 4 秒前說完的
        service._on_speech("loopback", "같이 게임할래?", "ko", seconds=0.2, length=1.0)
        assert len(service.messages) == 2
    finally:
        service.shutdown()
