"""2.19.0 的學習功能：每日複習提醒、單字發音、用自己的對話出題、跟讀打分、收藏整句、程度設定、學習統計、
匯出 Anki、沉浸字幕、問「為什麼這樣說」、韓文漢字詞對照、對話回放練習。"""

import json
import random
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "livelingo"


def _book(tmp_path):
    from livelingo.learn import Wordbook

    return Wordbook(tmp_path / "words.json")


def test_levels_stats_sentences_and_persistence(tmp_path):
    wb = _book(tmp_path)
    text = "I really appreciate the unbelievable hospitality of everyone here"
    middle, easy, hard = wb.fresh(text, "en"), wb.fresh(text, "en", level="beginner"), wb.fresh(text, "en", level="advanced")
    assert middle and len(easy) <= 1 and all(len(w) <= 7 for w in easy) and all(len(w) >= 8 for w in hard)
    wb.add("appreciate", "en", "感謝")
    assert wb.seen_again("We appreciate it, appreciate!", "en") == 1
    assert wb.words[0]["seen"] == 1
    wb.review("appreciate", "en", True)
    wb.add_sentence("Nice to meet you", "en", "很高興認識你", "禮貌的初次見面用語")
    wb.add_sentence("Nice to meet you", "en", "", "")             # 同一句不重複
    assert len(wb.sentences) == 1 and wb.sentences[0]["note"]
    st = wb.stats()
    assert st["new"] == 1 and st["reviewed"] == 1 and st["remembered"] == 1 and st["sentences"] == 1 and len(st["daily"]) == 7
    assert st["top"][0]["w"] == "appreciate"
    again = _book(tmp_path)                                         # 存檔後重開還在
    assert again.sentences and again.stats()["reviewed"] == 1 and again.state()["sentences"] == 1
    sid = again.sentences[0]["id"]
    assert again.remove_sentence(sid) and not again.remove_sentence(sid)
    assert again.clear() == 1 and again.sentences == [] and again.stats()["reviewed"] == 0
    assert _book(tmp_path).add("학교", "ko", "學校", hanja="學校")["hanja"] == "學校"


def test_quiz_builds_fill_and_meaning_questions():
    from livelingo import study

    words = [{"w": w, "lang": "en", "meaning": m, "example": e, "example_tr": ""} for w, m, e in
             (("apple", "蘋果", "I ate an apple today"), ("river", "河", ""), ("cloud", "雲", "A cloud passed"), ("stone", "石頭", ""),
              ("bread", "麵包", "")) ]
    assert study.quiz(words[:3]) == []                              # 不到 4 個字出不了題
    items = study.quiz(words, 5, random.Random(1))
    assert len(items) == 5
    for it in items:
        assert it["answer"] in it["choices"] and len(set(it["choices"])) == len(it["choices"]) >= 2
        if it["kind"] == "fill":
            assert "＿＿" in it["prompt"] and it["answer"] == it["w"]
        else:
            assert it["prompt"] == it["w"]
    assert any(it["kind"] == "fill" for it in items) and any(it["kind"] == "meaning" for it in items)


def test_shadow_score_and_anki_text(tmp_path):
    from livelingo import study

    assert study.shadow_score("Nice to meet you", "nice to meet you!", "en") == {"score": 100, "missed": []}
    part = study.shadow_score("Nice to meet you", "nice meet", "en")
    assert 0 < part["score"] < 100 and part["missed"] == ["to", "you"] or "to" in part["missed"][0]
    ko = study.shadow_score("안녕하세요 만나서 반갑습니다", "안녕하세요 만나서 반갑", "ko")
    assert ko["score"] >= 80 and ko["missed"] == ["습니다"]
    assert study.shadow_score("", "x", "en")["score"] == 0 and study.shadow_score("hello", "", "en")["score"] == 0
    text = study.anki_text([{"w": "apple", "lang": "en", "meaning": "蘋果\t水果", "example": "An apple", "example_tr": "一顆蘋果"}],
                           [{"text": "Nice to meet you", "lang": "en", "tr": "很高興認識你", "note": "客氣"}])
    lines = text.splitlines()
    assert lines[0] == "#separator:tab" and lines[4].split("\t")[:3] == ["apple", "蘋果 水果", "An apple → 一顆蘋果"]
    assert lines[5].split("\t")[:2] == ["Nice to meet you", "很高興認識你 (客氣)"]
    first = study.save_anki(tmp_path, [{"w": "a", "lang": "en"}], [])
    second = study.save_anki(tmp_path, [{"w": "a", "lang": "en"}], [])
    assert first != second and first.read_text(encoding="utf-8").startswith("#separator")


def test_hanja_explain_and_remind_helpers():
    from livelingo import study

    assert study.hanja_of("학교") == "學校" and study.hanja_of("사람") == "" and study.hanja_of("없는말") == ""
    assert study.hanja_of("선생님") == "先生님"
    assert study.hanja_ok("학교", "學校") and not study.hanja_ok("학교", "學") and not study.hanja_ok("학교", "학교") and not study.hanja_ok("학교", "ab")
    assert "학교" in study.hanja_prompt("학교")
    assert study.parse_explain("1. 語尾 -요 是客氣說法\n- 常見\n\n* 第三行\n第四行") == "語尾 -요 是客氣說法\n常見\n第三行"
    assert "{n}" not in study.remind_text("zh-TW", 3) and "3" in study.remind_text("xx", 3)
    assert "학교" in study.explain_prompt("학교 가요", "去學校", "zh-TW") and "Traditional Chinese" in study.explain_prompt("a", "b", "zh-TW")


def test_practice_sessions_pick_their_foreign_lines():
    from livelingo import study

    rows = [{"at": 100 + i, "k": f"a{i}", "who": "them", "original": f"안녕 {i}", "translated": f"你好 {i}", "lang": "ko"} for i in range(5)]
    rows += [{"at": 5000 + i, "k": f"b{i}", "who": "them", "original": f"hi {i}", "translated": "", "lang": "zh-TW"} for i in range(6)]
    rows += [{"at": 9000 + i, "k": f"c{i}", "who": "them", "original": f"yo {i}", "translated": "哟", "lang": "en"} for i in range(2)]
    out = study.practice_sessions(rows, 600, "zh")
    assert len(out) == 1 and out[0]["count"] == 5 and out[0]["lines"][0]["original"] == "안녕 0"      # 中文的、太短的不算
    assert study.practice_sessions([], 600, "zh") == []


def test_tray_notify_without_tray_is_quiet():
    from livelingo.tray import Tray

    tray = Tray.__new__(Tray)
    tray.icon = None
    tray.running = False
    assert tray.notify("hi") is False


def test_service_learning_flow(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    try:
        service.cfg.set(learn_mode=True, my_lang="zh-TW", their_lang="ko")
        # 程度設定：不合法的值不收
        service.update_config({"learn_level": "expert"})
        assert service.cfg["learn_level"] == "middle"
        service.update_config({"learn_level": "advanced", "immerse": True})
        assert service.cfg["learn_level"] == "advanced" and service.cfg["immerse"] is True
        # 單字：韓文漢字詞（表裡有的）
        r = service.word_action("add", "학교", "ko", "學校的意思")
        assert r["ok"] and service.wordbook.listing()[0]["hanja"] == "學校"
        assert service.word_action("say", "학교", "ko")["ok"] in (True, False)
        # 收藏整句，說明用問過的第一行
        service._post({"id": 11, "who": "them", "via": "heard", "original": "만나서 반가워요", "translated": "很高興認識你", "pending": False,
                       "error": "", "lang": "ko", "at": time.time()})
        service._explained[11] = "-요 是客氣的語尾\n第二行"
        saved = service.sentence_action("save", 11)
        assert saved["ok"] and saved["sentences"][0]["note"] == "-요 是客氣的語尾" and saved["stats"]["sentences"] == 1
        assert service.sentence_action("remove", sid=saved["sentences"][0]["id"])["sentences"] == []
        assert service.sentence_action("save", 999)["ok"] is False and service.sentence_action("zzz")["ok"] is False
        # 出題：字不夠
        assert service.study_quiz()["error"] == "few"
        for w, m in (("가방", "包包"), ("사과", "蘋果"), ("나무", "樹")):
            service.wordbook.add(w, "ko", m)
        quiz = service.study_quiz(3)
        assert quiz["ok"] and len(quiz["items"]) == 3
        # Anki
        service.cfg.set(transcript_dir=str(tmp_path / "out"))
        import livelingo.oswin as oswin

        oswin.open_path = lambda p: True
        exported = service.anki_export()
        assert exported["ok"] and exported["count"] == 4 and (tmp_path / "out" / exported["name"]).exists()
        # 問為什麼這樣說
        assert service.explain(11)["error"] == "not_ready"
        service.phase = "ready"
        service._wake_engine = lambda: None
        service.translator.ask_free = lambda prompt, n: "1. 語尾 -요 客氣\n2. 常用問候"
        service._explained.clear()
        assert service.explain(11)["ok"]
        end = time.time() + 3
        while not any(e[0] == "explain" and e[1]["text"] for e in events) and time.time() < end:
            time.sleep(0.02)
        assert ("explain", {"id": 11, "text": "語尾 -요 客氣\n常用問候"}) in events
        assert service.explain(12)["error"] == "nothing"
        # 練習：沒存歷史
        service.history.keep = False
        assert service.practice_list()["error"] == "history_off"
        assert service.shadow("", "x", "ko")["ok"] is False and service.shadow("!!!", "x", "ko")["ok"] is False
    finally:
        service.shutdown()


def test_hanja_asks_model_only_for_unknown_words(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    service.hub.publish = lambda *a, **k: None
    try:
        service.cfg.set(my_lang="zh-TW")
        service.phase = "ready"
        answers = iter(["學習", "한자", "-"])
        service.translator.ask_free = lambda prompt, n: next(answers)
        assert service._hanja("학습", "ko") == "學習"             # 表裡沒有，問模型，字數對、是漢字才收
        assert service._hanja("학습", "ko") == ""                  # 模型亂回的不收
        assert service._hanja("가방", "ko") == ""
        assert service._hanja("학교", "ko") == "學校" and service._hanja("apple", "en") == ""
        service.cfg.set(hanja=False)
        assert service._hanja("학교", "ko") == ""
    finally:
        service.shutdown()


def test_daily_review_reminder_once_a_day(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    events, shown = [], []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    service.tray.notify = lambda text, title="LiveLingo": shown.append(text) or True
    try:
        assert service._review_remind_once() is False                # 沒有該複習的字
        service.wordbook.add("apple", "en", "蘋果")
        assert service._review_remind_once() is True and len(shown) == 1
        assert ("notice", {"code": "review_due", "count": 1}) in events
        assert service._review_remind_once() is False                # 今天提醒過了
        service.cfg.set(review_reminded="")
        service.cfg.set(review_remind=False)
        assert service._review_remind_once() is False
    finally:
        service.shutdown()


def test_wiring_for_2_19_0():
    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS

    i18n = (ROOT / "ui" / "i18n.js").read_text(encoding="utf-8")
    for key in ("lv_title", "im_show", "hj_desc", "rm_title", "lst_top", "qz_done", "pr_load", "sh_missed", "se_hint", "ex_why", "ak_saved",
                "wd_say", "n_review_due"):
        assert len(re.findall(rf"(?<![\w]){key}: \"", i18n)) == len(UI_LANGS), key
    for key in ("learn_level", "review_remind", "immerse", "hanja"):
        assert key in DEFAULTS and key in EDITABLE, key
    assert "review_reminded" in DEFAULTS and "review_reminded" not in EDITABLE
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    for call in ("learn_data(", "practice_list(", "study_quiz(", "explain(", "shadow(", "anki_export(", "practice_say(", "sentence_action("):
        assert f"service.{call}" in server, call
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    for act in ("reveal", "explain", "sentence-save", "sentence-remove", "anki-export", "quiz-start", "quiz-pick", "quiz-next", "practice-load",
                "practice-open", "practice-back", "practice-show", "practice-next", "practice-say", "shadow-rec", "word-say"):
        assert f'data-act="{act}"' in app and f'"{act}":' in app, act
    assert "explain: (d) =>" in app
    changes = json.loads((ROOT / "data" / "changes.json").read_text(encoding="utf-8"))
    assert changes
