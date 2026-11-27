"""2.17.0 的九個點子：手把快捷鍵、條漫長圖模式、拖圖片進來翻、剪貼簿圖片翻譯、說話直接送進遊戲聊天、
滑鼠靠近變淡、釘選句子、快捷鍵一覽、攝影機翻實物。"""

import base64
import json


def test_gamepad_combos_and_matcher():
    from livelingo import gamepad as g

    assert g.normalize("back + a") == "Back+A" and g.normalize("A+Back") == "Back+A"
    assert g.normalize("rb+lb+y") == "LB+RB+Y"
    for bad in ("A", "A+B", "Back", "Back+Nope", "Back+A+A", "LB+A", "Back+A+B+X", ""):
        assert g.normalize(bad) == "", bad
    assert g.clean({"reply": "back+a", "mic": "Back+A", "nope": "Back+B", "snap": "A+B"}, ("reply", "mic", "snap")) == {"reply": "Back+A"}
    m = g.Matcher({"reply": "Back+A", "snap": "LB+RB+A"})
    back, a, lb, rb = g.BUTTONS["Back"], g.BUTTONS["A"], g.BUTTONS["LB"], g.BUTTONS["RB"]
    assert m.feed(back) == "" and m.feed(back | a) == "reply" and m.feed(back | a) == ""      # 按著不放不重複
    assert m.feed(0) == "" and m.feed(a) == ""                                              # 只按 A：遊戲自己的鍵
    assert m.feed(lb | rb | a) == "snap"

    fired = []
    pad = g.Gamepad(fired.append, read=iter([0, back, back | a, back | a, 0] + [0] * 400).__next__, interval=0.001)
    pad.set({"reply": "Back+A"})
    assert pad.start()
    import time

    end = time.time() + 2
    while not fired and time.time() < end:
        time.sleep(0.01)
    pad.stop()
    assert fired == ["reply"]


def test_cheatsheet_uses_ui_labels():
    from livelingo import cheatsheet

    labels = cheatsheet.labels("zh-TW")
    assert labels["quick"] == "快速輸入框" and labels["clipimg"] and labels["keys"]
    assert cheatsheet.labels("xx")["quick"] == cheatsheet.labels("en")["quick"]
    lines = cheatsheet.lines({"quick": "Ctrl+Shift+Q", "mic": ""}, {"reply": "Back+A"}, "en")
    assert lines == [f"Ctrl+Shift+Q  {cheatsheet.labels('en')['quick']}", f"[Back+A]  {cheatsheet.labels('en')['reply']}"]


def test_image_decoding(tmp_path):
    import cv2
    import numpy as np

    from livelingo import imagetext

    img = np.full((40, 5000, 3), 200, np.uint8)
    ok, png = cv2.imencode(".png", img)
    bgr = imagetext.decode(png.tobytes())
    assert bgr.shape[1] == imagetext.MAX_SIDE                   # 太寬的縮小
    url = "data:image/png;base64," + base64.b64encode(png.tobytes()).decode()
    assert imagetext.from_data_url(url) is not None
    assert imagetext.from_data_url("data:text/plain;base64,aGk=") is None and imagetext.from_data_url("nope") is None
    assert imagetext.decode(b"not an image") is None and imagetext.decode(b"") is None
    assert imagetext.clipboard_image() is None or True          # 不是 Windows：None


def test_images_go_through_the_reader(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    from livelingo import imagetext
    from livelingo.core.ocr import Line
    from livelingo.service import Service

    service = Service(tmp_path)
    sent = []
    service.submit = lambda via, text, lang=None, asked=False, **kw: (sent.append((via, text, asked)), len(sent))[1]
    service._album_keep = lambda bgr, shown: None
    cards = []
    service._card_show = lambda items: cards.append(items)

    class Ocr:
        lines = [Line("메뉴 김치찌개", 10, 10, 20, 0.95, 200)]

        def read(self, bgr):
            return list(self.lines)

    service._load_ocr = lambda: Ocr()
    ok, png = cv2.imencode(".png", np.full((60, 200, 3), 255, np.uint8))
    url = "data:image/png;base64," + base64.b64encode(png.tobytes()).decode()
    try:
        assert service.image_translate(url) == {"ok": False, "error": "not_ready"}
        service.phase = "ready"
        assert service.image_translate("bad") == {"ok": False, "error": "bad_image"}
        assert service.image_translate(url) == {"ok": True, "n": 1} and sent[-1] == ("screen", "메뉴 김치찌개", True)
        assert cards == []                                                         # 拖進來的：只在對話裡
        monkeypatch.setattr(imagetext, "clipboard_image", lambda: np.full((60, 200, 3), 255, np.uint8))
        assert service.clipboard_translate()["ok"] and len(cards) == 1             # 剪貼簿：小卡（人在遊戲裡）
        monkeypatch.setattr(imagetext, "clipboard_image", lambda: None)
        assert service.clipboard_translate()["error"] == "clip_empty"
        monkeypatch.setattr(imagetext, "camera_frame", lambda index: None)
        assert service.camera_translate()["error"] == "camera_failed"
        got = []
        monkeypatch.setattr(imagetext, "camera_frame", lambda index: (got.append(index), np.full((60, 200, 3), 255, np.uint8))[1])
        service.update_config({"camera_index": 1})
        assert service.camera_translate()["ok"] and got == [1]
        assert "camera_index" not in service.update_config({"camera_index": 42}).get("changed", [])
        Ocr.lines = []
        assert service.image_translate(url) == {"ok": False, "error": "no_text"}
    finally:
        service.shutdown()


def test_comic_mode_one_bubble_each():
    from livelingo.core import watcher as w
    from livelingo.core.ocr import Line

    sw = w.ScreenWatcher.__new__(w.ScreenWatcher)
    got = []
    sw.on_message = lambda text, lang, score, region: got.append(text)
    sw.source_langs = {"ko"}
    sw.grouper = lambda raw: [r.text for r in raw]
    slot = w._Slot({"id": "r1", "comic": True})
    sw._comic(slot, [Line("어디 가는 거야?", 0, 0, 20, 0.9), Line("Hello", 0, 50, 20, 0.9), Line("집에 가자", 0, 100, 20, 0.9)])
    sw._comic(slot, [Line("집에 가자", 0, 0, 20, 0.9), Line("내일 봐", 0, 60, 20, 0.9)])      # 捲動：看過的不再翻
    assert got == ["어디 가는 거야?", "집에 가자", "내일 봐"]
    assert slot.stats["repeat"] == 1 and slot.stats["other"] == 1


def test_comic_flag_is_kept_on_regions(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    try:
        service.cfg.set(regions=[{"id": "r1", "name": "", "on": True, "app": "", "anchor": None, "left": 0, "top": 0, "width": 200, "height": 60}])
        service.update_config({"regions": [dict(service.cfg["regions"][0], comic=True)]})
        assert service.cfg["regions"][0]["comic"] is True
        service.update_config({"regions": [dict(service.cfg["regions"][0], comic=False)]})
        assert "comic" not in service.cfg["regions"][0]
    finally:
        service.shutdown()


def test_pins_hover_fade_and_compose(tmp_path):
    from PIL import Image

    from livelingo import chatpaint
    from livelingo.service import MAX_PINS, Service

    service = Service(tmp_path)
    try:
        for n in range(5):
            service._post({"id": n + 1, "who": "them", "via": "heard", "original": f"o{n}", "translated": f"t{n}", "pending": False, "error": "", "lang": "ko"})
        service._post({"id": 9, "who": "them", "via": "heard", "original": "x", "translated": "", "pending": True, "error": "", "lang": "ko"})
        assert service.pin_action(9)["ok"] is False and service.pin_action("x")["ok"] is False
        for n in range(1, 5):
            assert service.pin_action(n)["ok"]
        assert service.pins == [2, 3, 4] and len(service.overlay.pins) == MAX_PINS
        assert not any(m.get("pinned") for m in service.messages if m["id"] == 1)
        assert service.pin_action(3, False)["pins"] == [2, 4]
        service._drop(4)
        assert service.pins == [2] and [p["text"] for p in service.overlay.pins] == ["t1"]
        service.clear_messages()
        assert service.pins == [] and service.overlay.pins == []

        # 滑鼠在浮動字幕上：變淡
        ov = service.overlay
        ov.rect = [100, 100, 400, 200]
        ov.cursor = lambda: (150, 150)
        assert ov.hovered()
        ov.cursor = lambda: (10, 10)
        assert not ov.hovered()
        ov.cursor = lambda: None
        assert not ov.hovered()
    finally:
        service.shutdown()

    pin = Image.new("RGBA", (100, 30), (255, 0, 0, 255))
    msg = Image.new("RGBA", (100, 30), (0, 0, 255, 255))
    canvas = chatpaint.compose([(msg, 1.0)] * 5, (100, 120), pinned=[(pin, 1.0)])
    assert canvas.getpixel((50, 10))[0] > 200 and canvas.getpixel((50, 110))[2] > 200      # 釘選的在最上面，其他的在下面


def test_chat_send_only_my_speech(tmp_path, monkeypatch):
    from livelingo import oswin
    from livelingo.service import Service

    service = Service(tmp_path)
    sent = []
    monkeypatch.setattr(oswin, "send_chat", lambda text, open_key, submit: (sent.append((text, open_key, submit)), True)[1])
    monkeypatch.setattr(oswin, "foreground_is_self", lambda: False)
    import time

    def run(msg, out="hi"):
        service._chat_send(msg, out)
        time.sleep(0.15)

    try:
        assert service.cfg["chat_send"] is False
        run({"who": "me", "via": "spoken"})
        assert sent == []                                                       # 預設關
        service.update_config({"chat_send": True, "chat_send_open": "slash", "chat_send_enter": False})
        assert "chat_send_open" not in service.update_config({"chat_send_open": "f12"}).get("changed", [])
        run({"who": "me", "via": "typed"})
        run({"who": "them", "via": "heard"})
        assert sent == []                                                       # 只送我用麥克風說的
        run({"who": "me", "via": "spoken"}, "안녕")
        assert sent == [("안녕", "slash", False)]
        monkeypatch.setattr(oswin, "foreground_is_self", lambda: True)
        run({"who": "me", "via": "spoken"}, "again")
        assert len(sent) == 1                                                   # LiveLingo 在最前面：不送
    finally:
        service.shutdown()


def test_hotkeys_and_gamepad_in_the_service(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    events = []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    try:
        assert service.show_keys() and events[-1][0] == "keys_card"
        assert any("Ctrl+Shift+F1" in line for line in events[-1][1]["lines"])
        service.update_config({"gamepad_keys": {"reply": "back+a", "snap": "A"}})
        assert service.cfg["gamepad_keys"] == {"reply": "Back+A"}
        fired = []
        service._hotkey = fired.append
        service._pad_action("reply")
        service._pad_action("nope")
        assert fired == ["reply"]
        assert service.state()["gamepad"]["defaults"]["reply"] == "Back+A"
    finally:
        service.shutdown()


def test_wiring_for_2_17_0():
    import re
    from pathlib import Path

    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS

    root = Path(__file__).resolve().parent.parent / "livelingo"
    i18n = (root / "ui" / "i18n.js").read_text(encoding="utf-8")
    for key in ("pin_on", "comic_title", "img_title", "img_err_no_text", "cam_shoot", "hk_clipimg", "hk_keys_desc", "pad_title", "keys_title",
                "hover_title", "cs2_warn"):
        assert len(re.findall(rf"(?<![\w]){key}: \"", i18n)) == len(UI_LANGS), key
    for key in ("gamepad_on", "gamepad_keys", "overlay_hover_fade", "chat_send", "chat_send_open", "chat_send_enter", "camera_index"):
        assert key in DEFAULTS and key in EDITABLE, key
    assert DEFAULTS["chat_send"] is False and DEFAULTS["gamepad_on"] is False
    server = (root / "server.py").read_text(encoding="utf-8")
    for route, call in (("/api/image", "image_translate("), ("/api/clipimage", "clipboard_translate("), ("/api/camera", "camera_translate("),
                        ("/api/pin", "pin_action("), ("/api/keys", "show_keys(")):
        assert f'route == "{route}"' in server and f"service.{call}" in server, route
    app = (root / "ui" / "app.js").read_text(encoding="utf-8")
    for act in ("pin", "clip-image", "camera", "camera-index", "keys-card", "region-comic"):
        assert f'data-act="{act}"' in app and f'"{act}":' in app, act
    assert '"drop"' in app and "gamepad: (d) =>" in app and "keys_card: (d) =>" in app
    assert json.loads((root / "data" / "changes.json").read_text(encoding="utf-8"))
