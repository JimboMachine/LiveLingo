"""2.16.0 的十個點子：遊戲專屬版面、劇情書、框選翻譯相簿、聊天頻道分色、玩家備註、打架時讀慢、獨佔全螢幕提醒、
常見遊戲範本、自動找聊天框、副螢幕字幕牆。"""

import json
import threading
import time


class Echo:
    tone = "casual"

    def translate(self, text, target, source=None, context=None):
        return f"<{target}>{text}"

    def clear_cache(self):
        pass


def wait(test, seconds=5):
    end = time.time() + seconds
    while time.time() < end and not test():
        time.sleep(0.02)
    assert test()


def test_templates_and_find_chat_box():
    from livelingo import gametools
    from livelingo.core.ocr import Line

    have = gametools.templates()
    assert len(have) >= 10 and all(all(0 <= v <= 1 for v in t["box"]) for t in have.values())
    exe = next(iter(have))
    region = gametools.template_region(exe, [100, 50, 1920, 1080], "t1", "Game")
    assert region["app"] == exe and region["on"] and region["width"] >= 60 and region["height"] >= 30
    assert region["anchor"] == [region["left"] - 100, region["top"] - 50]
    assert gametools.template_region("nope.exe", [0, 0, 800, 600], "t2", "x") is None
    assert gametools.template_region(exe, [0, 0, 0, 0], "t3", "x") is None

    # 左下角一疊對齊的聊天；右上角零散的介面字
    chat = [Line(f"player{i}: 안녕하세요 반가워요", 20, 700 + i * 24, 18, 0.9, 260) for i in range(6)]
    noise = [Line("HP 100", 1500, 30, 30, 0.9, 120), Line("MENU", 1700, 400, 30, 0.9, 90)]
    box = gametools.find_chat_box(chat + noise, 1920, 1080)
    assert box and box[0] <= 20 and box[1] <= 700 and box[0] + box[2] >= 280 and box[1] + box[3] >= 700 + 5 * 24 + 18
    assert gametools.find_chat_box(noise, 1920, 1080) is None


def test_channel_colors_and_player_notes():
    import numpy as np

    from livelingo import gametools
    from livelingo.core.watcher import line_color

    assert gametools.channel_of("#FFFFFF") == "" and gametools.channel_of("#808080") == "" and gametools.channel_of("bad") == ""
    assert gametools.channel_of("#40E040") == "#81C995" and gametools.channel_of("#FF3030") == "#F28B82"
    assert gametools.channel_of("#3080FF") == "#8AB4F8"
    img = np.zeros((20, 100, 3), np.uint8)
    img[6:14, 10:90] = (60, 220, 60)       # BGR：綠字
    color = line_color(img, 0, 0, 100, 20)
    assert gametools.channel_of(color) == "#81C995"
    assert line_color(img, 0, 0, 2, 2) == ""

    notes = gametools.clean_players({" Nayeon7 ": "上次的隊友", "x" * 40: "y" * 80, "empty": "", "nayeon7": "重複"})
    assert notes == {"Nayeon7": "上次的隊友", "x" * 30: "y" * 60}
    assert gametools.clean_players("nope") == {}
    assert gametools.players_in("nayeon7 가자", notes) == [["Nayeon7", "上次的隊友"]]
    assert gametools.players_in("Nayeon77 가자", notes) == []      # 只比整個 ID


def test_storybook(tmp_path):
    from livelingo.storybook import StoryBook

    clock = iter(range(1000, 2000))
    book = StoryBook(tmp_path, now=lambda: float(next(clock)))
    assert book.add("Genshin.exe", "안녕", "你好") and not book.add("genshin.exe", "안녕", "你好")      # 同一段又讀到：不重記
    assert book.add("genshin.exe", "가자", "走吧") and book.add("other.exe", "one", "一")
    games = book.games()
    assert games[0]["game"] == "other.exe" and {g["game"]: g["count"] for g in games}["genshin.exe"] == 2
    page = book.read("genshin.exe")
    assert page["total"] == 2 and [i["dst"] for i in page["items"]] == ["你好", "走吧"]
    assert book.read("genshin.exe", offset=1)["items"][0]["src"] == "가자"
    path = book.export("genshin.exe", "Genshin", tmp_path / "out")
    text = path.read_text(encoding="utf-8-sig")
    assert "Genshin" in text and "안녕" in text and "走吧" in text
    assert book.export("none.exe", "x", tmp_path / "out") is None
    assert book.clear("genshin.exe") == 2 and book.read("genshin.exe")["total"] == 0 and book.read("other.exe")["total"] == 1


def test_album(tmp_path):
    import numpy as np

    from livelingo import album as album_mod

    clock = [1000.0]
    shots = album_mod.Album(tmp_path, now=lambda: clock[0]) if "now" in album_mod.Album.__init__.__code__.co_varnames else album_mod.Album(tmp_path)
    big = np.full((100, 3000, 3), 80, np.uint8)
    aid = shots.add(big, [("안녕", "你好"), ("  ", "")], "game.exe")
    assert aid and shots.list()[0]["items"] == [{"src": "안녕", "dst": "你好"}]
    png = shots.image(aid)
    assert png and png[:4] == b"\x89PNG"
    assert shots.image("../x") is None and shots.add(big, [], "") == ""
    assert shots.remove(aid) and shots.list() == [] and shots.image(aid) is None


def test_watcher_reads_slower_when_busy():
    from livelingo.core import watcher as w

    sw = w.ScreenWatcher.__new__(w.ScreenWatcher)
    sw.slow_busy, sw._busy_since, sw._busy_seen = True, None, 0.0
    assert not sw.busy()
    now = time.monotonic()
    sw._busy_since, sw._busy_seen = now - w.BUSY_AFTER - 1, now
    assert sw.busy()
    sw.slow_busy = False
    assert not sw.busy()
    sw.slow_busy, sw._busy_seen = True, now - 5
    assert not sw.busy()      # 最近沒在動了


def test_game_profiles(tmp_path, monkeypatch):
    from livelingo import apps
    from livelingo.service import Service

    service = Service(tmp_path)
    windows = [{"exe": "League of Legends.exe", "title": "League", "minimized": False, "rect": [0, 0, 1920, 1080]}]
    monkeypatch.setattr(apps, "windows", lambda: windows)
    notices = []
    service.notice = lambda code, **kw: notices.append((code, kw))
    try:
        service.cfg.set(regions=[{"id": "r1", "name": "", "on": True, "app": "", "anchor": None, "left": 0, "top": 0, "width": 200, "height": 60},
                                 {"id": "r2", "name": "", "on": False, "app": "", "anchor": None, "left": 0, "top": 100, "width": 200, "height": 60}])
        service.update_config({"their_lang": "ko"})
        saved = service.profile_action("save")
        assert saved["ok"] and saved["exe"] == "league of legends.exe"
        profile = service.cfg["game_profiles"]["league of legends.exe"]
        assert profile["their_lang"] == "ko" and profile["regions"] == ["r1"] and "look" in profile["layout"]
        # 換到別的設定，再切回遊戲：自動套回來
        service.update_config({"their_lang": "ja"})
        service.cfg.set(regions=[dict(r, on=r["id"] == "r2") for r in service.cfg["regions"]])
        service._profile_game = ""
        assert service.check_profile(windows) == "league of legends.exe"
        assert service.cfg["their_lang"] == "ko" and [r["on"] for r in service.cfg["regions"]] == [True, False]
        assert notices[-1][0] == "profile_applied"
        assert service.check_profile(windows) == ""                                # 同一個遊戲：不再套
        service.update_config({"game_profile_auto": False})
        service._profile_game = ""
        assert service.check_profile(windows) == ""
        assert service.profile_action("remove")["ok"] and service.cfg["game_profiles"] == {}
        assert service.profile_action("nope")["error"] == "unknown"
        monkeypatch.setattr(apps, "windows", lambda: [])
        service.game = ""
        assert service.profile_action("save") == {"ok": False, "error": "no_game"}
        assert "game_profiles" not in service.update_config({"game_profiles": {"x": {}}}).get("changed", [])
    finally:
        service.shutdown()


def test_fullscreen_warning_once(tmp_path, monkeypatch):
    from livelingo import oswin
    from livelingo.service import Service

    service = Service(tmp_path)
    monkeypatch.setattr(oswin, "exclusive_fullscreen", lambda: True)
    notices = []
    service.notice = lambda code, **kw: notices.append(code)
    game = [{"exe": "League of Legends.exe", "minimized": False}]
    try:
        service.cfg.set(overlay=True)
        assert service.check_fullscreen(game) and not service.check_fullscreen(game)      # 這次開遊戲只說一次
        assert notices == ["fullscreen_exclusive"]
        assert not service.check_fullscreen([])                                       # 遊戲關了：下次再說
        assert service.check_fullscreen(game)
        service._fullscreen_told = False
        service.update_config({"fullscreen_warn": False})
        assert not service.check_fullscreen(game)
    finally:
        service.shutdown()


def test_screen_lines_carry_color_story_and_players(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    service.translator = Echo()
    service.cfg.set(back_translate=False, regions=[{"id": "r1", "name": "", "on": True, "app": "", "anchor": None, "story": True,
                                                    "left": 0, "top": 0, "width": 200, "height": 60}])
    service.update_config({"player_notes": {"Nayeon7": "上次的隊友", "": "x"}})
    assert service.cfg["player_notes"] == {"Nayeon7": "上次的隊友"}
    worker = threading.Thread(target=service._translate_worker, daemon=True)
    worker.start()
    try:
        service.game = "genshinimpact.exe"
        service._on_screen_line("Nayeon7 같이 가요", "ko", 0.95, service.cfg["regions"][0], color="#40E040")
        wait(lambda: service.messages and not service.messages[-1].get("pending"))
        msg = service.messages[-1]
        assert msg["tint"] == "#81C995" and msg["story"] and msg["players"] == [["Nayeon7", "上次的隊友"]]
        wait(lambda: service.storybook.read("genshinimpact.exe")["total"] == 1)
        assert service.story_state()["games"][0]["game"] == "genshinimpact.exe"
        # 關掉分色：不帶顏色；不是劇情區：不記劇情
        service.update_config({"channel_colors": False})
        assert service._screen_extra("#40E040", {"id": "r2"}) == {}
        service.update_config({"channel_colors": True})
        assert service._screen_extra("#FFFFFF", None) == {} and service._screen_extra("#40E040", {"story": True}) == {"tint": "#81C995", "story": True}
        assert service.story_action("clear", "genshinimpact.exe") == {"ok": True, "removed": 1}
        assert service.story_action("nope")["ok"] is False
    finally:
        service.closing.set()
        worker.join(2)
        service.shutdown()


def test_template_find_chat_and_wall(tmp_path, monkeypatch):
    import numpy as np

    from livelingo import apps, gametools, oswin
    from livelingo.core.ocr import Line
    from livelingo.service import Service

    service = Service(tmp_path)
    exe = next(iter(gametools.templates()))
    monkeypatch.setattr(apps, "windows", lambda: [{"exe": exe, "minimized": False, "rect": [0, 0, 1600, 900]}])
    try:
        r = service.template_apply()
        assert r["ok"] and service.cfg["regions"][-1]["app"] == exe
        monkeypatch.setattr(apps, "windows", lambda: [])
        r = service.template_apply()
        assert r["error"] == "no_game" and r["games"]

        service.phase = "ready"
        monkeypatch.setattr(oswin, "monitors", lambda: [{"left": 0, "top": 0, "width": 1920, "height": 1080}])
        service.grab = lambda place: np.zeros((place["height"], place["width"], 3), np.uint8)
        chat = [Line(f"user{i}: 안녕하세요", 30, 600 + i * 24, 18, 0.9, 200) for i in range(5)]

        class Ocr:
            def read(self, bgr):
                return chat

        service._load_ocr = lambda: Ocr()
        before = len(service.cfg["regions"])
        found = service.find_chat()
        assert found["ok"] and len(service.cfg["regions"]) == before + 1 and service.cfg["regions"][-1]["on"] is False
        chat[:] = []
        assert service.find_chat()["error"] == "not_found"
        service.phase = "loading"
        assert service.find_chat()["error"] == "not_ready"

        assert service.wall_action("open") == {"ok": False, "error": "one_screen"}
        assert service.wall_action("nope")["error"] == "unknown"
        assert service.wall_action("close") == {"ok": True} and service.stream.wall_on is False
        assert "&wall=1" in (service.stream.url(wall=True) or "&wall=1")
        assert "wall_monitor" not in service.update_config({"wall_monitor": "2"}).get("changed", [])
        assert "wall_monitor" in service.update_config({"wall_monitor": 2}).get("changed", [])
    finally:
        service.shutdown()


def test_wiring_for_2_16_0():
    import re
    from pathlib import Path

    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS
    from livelingo.service import RESET_KEEP

    root = Path(__file__).resolve().parent.parent / "livelingo"
    i18n = (root / "ui" / "i18n.js").read_text(encoding="utf-8")
    for key in ("gt_title", "cc_title", "cs_title", "fw_title", "gp_title", "gp_failed", "ab_title", "sb_title", "wall_title", "wall_failed",
                "pn_title", "n_profile_applied", "n_fullscreen_exclusive"):
        assert len(re.findall(rf"(?<![\w]){key}: \"", i18n)) == len(UI_LANGS), key
    for key in ("game_profile_auto", "story_book", "snap_album", "channel_colors", "player_notes", "combat_slow", "fullscreen_warn", "wall_monitor"):
        assert key in DEFAULTS and key in EDITABLE, key
    assert "game_profiles" in DEFAULTS and "game_profiles" not in EDITABLE
    assert "game_profiles" in RESET_KEEP and "player_notes" in RESET_KEEP
    server = (root / "server.py").read_text(encoding="utf-8")
    for route, call in (("/api/profile", "profile_action("), ("/api/template", "template_apply("), ("/api/find_chat", "find_chat("),
                        ("/api/wall", "wall_action("), ("/api/story", "story_action("), ("/api/album", "album_action(")):
        assert f'route == "{route}"' in server and f"service.{call}" in server, route
    assert '"/api/album_image"' in server and '"/api/story"' in server
    app = (root / "ui" / "app.js").read_text(encoding="utf-8")
    for act in ("find-chat", "template-apply", "profile-save", "profile-remove", "album-remove", "story-export", "story-clear",
                "wall", "wall-monitor", "player-add", "player-remove"):
        assert f'data-act="{act}"' in app and f'"{act}":' in app, act
    assert "album: (d) =>" in app and "S.album = st.album" in app
    assert json.loads((root / "data" / "game_templates.json").read_text(encoding="utf-8"))["games"]
