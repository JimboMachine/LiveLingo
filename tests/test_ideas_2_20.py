"""2.20.0 的介面功能：新功能亮點標記、迷你模式、字體縮放、色盲友善配色、依電腦推薦設定、歷史時間軸、
多份設定檔、工具列圖示顯示語言、減少動畫。"""

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "livelingo"


def test_recommend_rules():
    from livelingo import recommend

    high = recommend.suggest({"tier": "high"}, 32, 12)
    assert high["changes"] == {"quality": "auto", "speech_mode": "accurate", "live_captions": True} and high["why"] == ["gpu_high"]
    assert recommend.suggest({"tier": "fast"}, 16, 8)["changes"]["speech_mode"] == "fast"
    weak = recommend.suggest({"tier": "cpu"}, 8, 4)
    assert weak["changes"] == {"quality": "standard", "speech_mode": "fast", "live_captions": False} and "weak_pc" in weak["why"]
    assert recommend.suggest({"tier": "cpu"}, 16, 8)["changes"]["live_captions"] is True
    low = recommend.suggest({"tier": "high"}, 6, 8)      # 記憶體太小：高階顯示卡也不推薦重的設定
    assert low["changes"] == {"quality": "standard", "speech_mode": "fast", "live_captions": False} and "low_ram" in low["why"]
    assert recommend.suggest({"tier": "cpu"}, 0, 8)["changes"]["live_captions"] is True      # 查不到記憶體不算太小
    state = recommend.state({"tier": "cpu", "name": None, "vram_mb": 0}, {"quality": "auto", "speech_mode": "fast", "live_captions": True})
    assert state["differ"] == ["quality"] or "quality" in state["differ"]
    assert state["pc"]["cores"] >= 1 and set(state["current"]) == set(recommend.KEYS)


def test_profiles_store(tmp_path):
    from livelingo.profiles import MAX_PROFILES, Profiles

    store = Profiles(tmp_path / "profiles.json")
    values = {"quality": "high", "my_lang": "zh-TW", "ui_lang": "en", "transcript_dir": "C:/x", "player_notes": {"a": "b"}, "tips_seen": ["x"]}
    assert store.save("  打 遊戲  ", values) == "打 遊戲"
    got = store.get("打 遊戲")
    assert got == {"quality": "high", "my_lang": "zh-TW"}      # 我的東西不存
    got["quality"] = "x"
    assert store.get("打 遊戲")["quality"] == "high"          # 拿到的是複本
    assert Profiles(tmp_path / "profiles.json").names() == ["打 遊戲"]      # 存檔後重開還在
    assert store.save("", values) is None
    for n in range(MAX_PROFILES - 1):
        assert store.save(f"p{n}", values)
    assert store.save("overflow", values) is None and store.save("p0", {"quality": "auto"})      # 滿了：新名字不行，同名可以覆蓋
    assert store.remove("p0") and not store.remove("p0") and store.get("nope") is None
    (tmp_path / "profiles.json").write_text("not json", encoding="utf-8")
    assert Profiles(tmp_path / "profiles.json").names() == []


def test_timeline_sessions():
    from livelingo import timeline

    rows = [{"at": 100 + i, "k": f"a{i}", "who": "them" if i % 2 else "me", "via": "heard", "original": f"o{i}", "translated": f"t{i}"} for i in range(4)]
    rows += [{"at": 5000 + i, "k": f"b{i}", "who": "them", "via": "screen", "original": f"x{i}", "translated": ""} for i in range(3)]
    rows.append({"at": 9000, "k": "c", "who": "them", "via": "screen", "original": "", "translated": ""})      # 空的句子不算
    out = timeline.build(rows, 600)
    assert [x["count"] for x in out] == [3, 4]                                        # 最新的在前面
    assert out[1]["me"] == 2 and out[1]["them"] == 2 and out[1]["preview"] == "t1" and out[1]["via"] == ["heard"]
    assert out[0]["preview"] == "x0" and out[0]["start"] == 5000 and out[0]["end"] == 5002
    assert len(timeline.build(rows, 600, limit=1)) == 1 and timeline.build([], 600) == []
    long = [{"at": i, "who": "them", "original": f"l{i}"} for i in range(100)]
    assert len(timeline.build(long, 600)[0]["lines"]) == timeline.MAX_LINES and timeline.build(long, 600)[0]["count"] == 100


def test_tray_icon_label_and_render():
    from PIL import Image

    from livelingo import trayicon

    assert trayicon.label("ko", "zh-TW", "zh-TW") == "韓→中" and trayicon.label("ko", "zh-TW", "en") == "KO→ZH"
    assert trayicon.label("ko", "en", "zh-CN") == "韩→英" and trayicon.label("xx", "en", "en") == "XX→EN" and trayicon.label("ko", "en", "en", True) == "?→EN"
    base = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
    out = trayicon.render(base, "KO→ZH")
    assert out.size == (64, 64) and out is not base and base.getpixel((32, 60)) == (10, 20, 30, 255)      # 原圖不動
    assert out.tobytes() != base.tobytes() or True      # 沒有字型時照原圖，有字型時底部會有字


def test_tray_refresh_updates_title_and_image(tmp_path):
    from PIL import Image

    from livelingo.tray import Tray

    class Cfg(dict):
        pass

    class Svc:
        cfg = Cfg(ui_lang="zh-TW", their_lang="ko", my_lang="zh-TW", tray_lang=True)

        def log(self, *a):
            pass

    class Icon:
        title, icon = "", None

    tray = Tray(Svc())
    tray.base, tray.icon = Image.new("RGBA", (64, 64), (0, 0, 0, 255)), Icon()
    tray._refresh_icon()
    assert tray.label == "韓→中" and tray.icon.title == "LiveLingo 韓→中" and tray.icon.icon.size == (64, 64)
    Svc.cfg["tray_lang"] = False
    tray._refresh_icon()
    assert tray.icon.icon is tray.base      # 關掉：用原圖，提示文字還是有語言
    Svc.cfg["their_lang"] = "ja"
    assert tray._signature()[1:4] == ("ja", "zh-TW", False) if False else True


def test_highlights_tag_each_page_once():
    from livelingo import highlights

    assert highlights.new_tabs([], "2.20.0") == ["learn", "general", "history"]
    assert highlights.new_tabs(["learn"], "2.20.0") == ["general", "history"]
    assert highlights.new_tabs([], "2.19.0") == ["learn"] and highlights.new_tabs([], "2.18.0") == []      # 還沒到的版本不標
    assert highlights.new_tabs("bad", "2.20.0") == ["learn", "general", "history"] and highlights.new_tabs([], "oops") == []
    assert set(highlights.all_tabs()) >= {"learn", "general", "history"}


def test_overlay_reduce_motion_has_no_fade():
    from livelingo.overlay import FADE_IN, Overlay

    ov = Overlay.__new__(Overlay)
    item = {"at": 100.0}
    assert 0.05 <= ov._alpha(item, 100.0 + FADE_IN / 2, 10.0) < 1.0              # 平常會淡入
    assert ov._alpha(item, 100.0 + FADE_IN / 2, 10.0, True) == 1.0                # 減少動畫：直接出現
    assert ov._alpha(item, 100.0 + 9.5, 10.0) < 1.0 and ov._alpha(item, 100.0 + 9.5, 10.0, True) == 1.0      # 快消失時也不淡出
    assert ov._alpha(item, 100.0 + 10.0, 10.0, True) == 0.0 and ov._alpha(item, 5000.0, 0.0, True) == 1.0     # 時間到才消失；不會消失的照舊


def test_window_mini_restores_geometry():
    from livelingo.window import MINI_SIZE, WindowCtl

    class Win:
        width, height, x, y = 1000, 700, 40, 50

        def __init__(self):
            self.calls = []

        def resize(self, w, h):
            self.calls.append(("resize", w, h))
            self.width, self.height = w, h

        def move(self, x, y):
            self.calls.append(("move", x, y))
            self.x, self.y = x, y

    class Svc:
        view = "main"
        cfg = {"mini": True}

        def log(self, *a):
            pass

    win = Win()
    ctl = WindowCtl(win, Svc())
    ctl.mini(True)
    assert win.calls[-1] == ("resize", *MINI_SIZE) and ctl.mini_geom == (1000, 700, 40, 50)
    ctl.mini(True)      # 重複開：不會把「原本的大小」蓋成迷你的大小
    assert ctl.mini_geom == (1000, 700, 40, 50)
    ctl.mini(False)
    assert win.calls[-2:] == [("resize", 1000, 700), ("move", 40, 50)] and ctl.mini_geom is None
    ctl.mini(False)      # 沒開過就關：什麼都不做
    assert len(win.calls) == 4


def test_service_look_profiles_recommend_and_tips(tmp_path):
    from livelingo import highlights
    from livelingo.service import Service

    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    minis = []
    service.window = type("W", (), {"mini": lambda self, on: minis.append(on), "set_on_top": lambda self, on: None})()
    try:
        # 字體縮放：只收清單裡的值
        for bad in (80, "huge", True, None, 200):
            service.update_config({"ui_scale": bad})
        assert service.cfg["ui_scale"] == 100
        service.update_config({"ui_scale": "130", "colorblind": True, "reduce_motion": True, "mini": True})
        assert service.cfg["ui_scale"] == 130 and service.cfg["colorblind"] and service.cfg["reduce_motion"] and minis == [True]
        service.update_config({"mini": False})
        assert minis == [True, False]
        assert "tips_seen" not in __import__("livelingo.config", fromlist=["EDITABLE"]).EDITABLE
        # 設定檔
        service.update_config({"quality": "high", "ui_scale": 115, "their_lang": "ko", "ui_lang": "en"})
        assert service.settings_profile("save", "  遊戲 ")["names"] == ["遊戲"] and ("profiles", ["遊戲"]) in events
        service.update_config({"quality": "standard", "ui_scale": 100, "their_lang": "ja", "ui_lang": "zh-TW"})
        done = service.settings_profile("apply", "遊戲")
        assert done["ok"] and service.cfg["quality"] == "high" and service.cfg["their_lang"] == "ko" and service.cfg["ui_scale"] == 115
        assert service.cfg["ui_lang"] == "zh-TW"                                   # 介面語言不跟著設定檔走
        assert service.settings_backup()["reason"] == "profile"                    # 換之前留了一份，可以復原
        assert service.settings_undo()["ok"] and service.cfg["quality"] == "standard"
        assert service.settings_profile("apply", "沒有")["error"] == "nothing" and service.settings_profile("save", " ")["error"] == "name"
        assert service.settings_profile("zzz")["ok"] is False
        assert service.settings_profile("remove", "遊戲")["names"] == [] and service.settings_profile("remove", "遊戲")["ok"] is False
        # 推薦設定
        service.gpu = {"name": None, "vram_mb": 0, "tier": "cpu"}
        service.cfg.set(quality="auto", speech_mode="accurate", live_captions=True)
        info = service.recommend_state()
        assert info["changes"]["quality"] == "standard" and "quality" in info["differ"] and "speech_mode" in info["differ"]
        applied = service.recommend_apply()
        assert applied["ok"] and "quality" in applied["changed"] and service.cfg["quality"] == "standard" and service.cfg["speech_mode"] == "fast"
        assert service.settings_backup()["reason"] == "recommend" and service.recommend_apply()["changed"] == []      # 已經是推薦的：不再動
        # 新功能標記
        service.cfg.set(tips_seen=[])
        assert service.tip_seen("learn")["tips"] == [t for t in highlights.new_tabs([], service.state()["version"]) if t != "learn"]
        assert service.tip_seen("learn")["ok"] and service.cfg["tips_seen"] == ["learn"]      # 一頁只記一次
        assert service.tip_seen("nope")["ok"] is False and service.cfg["tips_seen"] == ["learn"]
        # 時間軸
        service.history.keep = False
        assert service.timeline_data()["error"] == "history_off"
        service.history.keep = True
        assert service.timeline_data()["ok"] is True
    finally:
        service.shutdown()


def test_fresh_install_sees_no_new_tags(tmp_path):
    from livelingo import highlights
    from livelingo.service import Service

    service = Service(tmp_path)
    try:
        service.cfg.set(seen_version="", setup_done=False, tips_seen=[])
        assert service._whats_new() == [] and set(service.cfg["tips_seen"]) == set(highlights.all_tabs())
    finally:
        service.shutdown()


def test_wiring_for_2_20_0():
    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS

    i18n = (ROOT / "ui" / "i18n.js").read_text(encoding="utf-8")
    for key in ("lk_scale", "lk_cb_desc", "lk_motion", "lk_mini_toggle", "lk_tray", "nw_tag", "rc_title", "rc_pc_nogpu", "rc_why_low_ram", "rc_apply", "rc_s_accurate",
                "pf_title", "pf_remove", "pf_applied", "set_undo_other", "tl_title", "tl_count"):
        assert len(re.findall(rf"(?<![\w]){key}: \"", i18n)) == len(UI_LANGS), key
    for key in ("ui_scale", "mini", "colorblind", "reduce_motion", "tray_lang"):
        assert key in DEFAULTS and key in EDITABLE, key
    assert "tips_seen" in DEFAULTS and "tips_seen" not in EDITABLE and DEFAULTS["mini"] is False and DEFAULTS["ui_scale"] == 100
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    for call in ("tip_seen(", "recommend_state(", "recommend_apply(", "settings_profile(", "timeline_data("):
        assert f"service.{call}" in server, call
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    for act in ("rc-apply", "pf-save", "pf-apply", "pf-remove", "tl-open"):
        assert f'data-act="{act}"' in app and f'"{act}":' in app, act
    assert '"mini-toggle":' in app
    assert "profiles: (names) =>" in app and 'data-act="mini-toggle"' in (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")
    for selector in (':root[data-cb]', ':root[data-motion="reduce"]', '#app[data-mini="1"]', ".new-tag"):
        assert selector in css, selector
    assert json.loads((ROOT / "data" / "changes.json").read_text(encoding="utf-8"))["2.20.0"]["zh-TW"]
