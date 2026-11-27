"""2.21.0 的安全與設定功能：筆電省電模式、安全模式啟動、穩定版和搶先版、免安裝版、敏感資料遮蔽、歷史和單字本加密。"""

import base64
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "livelingo"


# ---------------------------------------------------------------- 敏感資料遮蔽


def test_redact_rules():
    from livelingo import redact

    mask = redact.mask
    # 密碼：有「是」「:」「=」「is」的記號，或密碼詞後面直接黏著、或空白隔開但像密碼（有數字）
    assert mask("我的密碼是 abc123 謝謝") == "我的密碼是 ▒▒▒ 謝謝"
    assert mask("密碼123456") == "密碼▒▒▒" and mask("password: hunter2") == "password: ▒▒▒"
    assert mask("my pin is 4821") == "my pin is ▒▒▒" and mask("pin 1234 ok") == "pin ▒▒▒ ok"
    assert mask("비밀번호는 abc1234") == "비밀번호는 ▒▒▒" and mask("пароль: qwerty1") == "пароль: ▒▒▒"
    # 一般的句子不能被誤遮（遊戲裡常說 pass、pin、password manager…）
    for plain in ("pass me the potion", "pin the message", "pw island", "he lost the password manager", "nice game, gg", "我們去打龍吧", "집에 가자 곧 갈게"):
        assert mask(plain) == plain, plain
    # 信箱、卡號（要通過 Luhn 檢查，隨便一串數字不算）、金鑰、身分證字號
    assert mask("mail me at a.b+c@x-y.example.com now") == "mail me at ▒▒▒ now"
    assert mask("4111 1111 1111 1111") == "▒▒▒" and mask("4111-1111-1111-1111 ok") == "▒▒▒ ok"
    assert mask("4111 1111 1111 1112") == "4111 1111 1111 1112" and mask("score 1234567890123") == "score 1234567890123"
    assert mask("key sk-abcdefghijklmnopqrstuv1234") == "key ▒▒▒" and mask("ghp_" + "a" * 36) == "▒▒▒"
    assert mask("A123456789") == "▒▒▒" and mask("123-45-6789") == "▒▒▒" and mask("900101-1234567") == "▒▒▒"
    assert mask("") == "" and mask(None) == "" and mask("order 2024 items") == "order 2024 items"
    assert mask(mask("password: hunter2")) == "password: ▒▒▒" and redact.has_secret("a@b.co") and not redact.has_secret("hello")
    # 只遮指定的欄位，原本的那一筆不動
    row = {"original": "pw=abc123", "translated": "x@y.co", "place": "p", "who": "me", "at": 1.5}
    out = redact.mask_row(row)
    assert out["original"] == "pw=▒▒▒" and out["translated"] == "▒▒▒" and out["who"] == "me" and row["original"] == "pw=abc123"
    assert redact.mask_row({"original": "x@y.co", "note": "x@y.co"}, ("note",)) == {"original": "x@y.co", "note": "▒▒▒"}


# ---------------------------------------------------------------- 加密（用假的資料保護測：Linux 上也跑得動）


@pytest.fixture
def fake_dpapi(monkeypatch):
    """可逆的假「資料保護」：換一個 key 就打不開，跟真的一樣。回傳一個可以改 key 的 dict。"""
    from livelingo import secure

    state = {"key": 0x5A, "fail": False}

    def protect(data: bytes):
        if state["fail"]:
            return None
        return b"MAGIC" + bytes([state["key"]]) + bytes(b ^ state["key"] for b in data)

    def unprotect(blob: bytes):
        if not blob.startswith(b"MAGIC") or len(blob) < 6 or blob[5] != state["key"]:
            return None
        return bytes(b ^ state["key"] for b in blob[6:])

    monkeypatch.setattr(secure, "available", lambda: True)
    monkeypatch.setattr(secure, "protect", protect)
    monkeypatch.setattr(secure, "unprotect", unprotect)
    return state


def test_secure_seal_roundtrip(fake_dpapi):
    from livelingo import secure

    sealed = secure.seal('{"a": "韓文 한국어 ✓"}')
    assert sealed.startswith(secure.PREFIX) and secure.is_sealed(sealed) and "韓文" not in sealed
    assert secure.unseal(sealed) == '{"a": "韓文 한국어 ✓"}'
    assert secure.unseal(secure.PREFIX + "###") is None and secure.unseal(secure.PREFIX + base64.b64encode(b"junk").decode()) is None
    assert not secure.is_sealed('{"plain": 1}')
    fake_dpapi["key"] = 0x11      # 換了帳號：打不開，不丟錯
    assert secure.unseal(sealed) is None
    fake_dpapi["fail"] = True
    assert secure.seal("x") is None


@pytest.mark.skipif(sys.platform != "win32", reason="真的 Windows 資料保護只有 Windows 有")
def test_real_dpapi_roundtrip():
    from livelingo import secure

    assert secure.available()
    sealed = secure.seal("密碼 hunter2 ✓")
    assert sealed and secure.is_sealed(sealed) and "hunter2" not in sealed
    assert secure.unseal(sealed) == "密碼 hunter2 ✓"
    assert secure.unseal(sealed[:-6] + "AAAAAA") is None      # 被改過：打不開，不丟錯


def _msg(n, text="hello there", at=None):
    return {"id": n, "who": "them", "via": "heard", "original": text, "lang": "en", "translated": "你好" + str(n), "target": "zh-TW", "place": "", "at": at or 1000.0 + n}


def test_history_encrypts_and_never_drops_locked_lines(tmp_path, fake_dpapi):
    from livelingo.history import History

    h = History(tmp_path, True, 0, encrypt=True)
    assert h.add(_msg(1, "secret talk")) and h.add(_msg(2))
    raw = (tmp_path / "history.jsonl").read_text(encoding="utf-8")
    assert raw.count("enc1:") == 2 and "secret talk" not in raw and "你好" not in raw      # 檔案裡看不到明文
    again = History(tmp_path, True, 0, encrypt=True)
    assert again.count == 2 and again.search()["items"][-1]["original"] == "secret talk" and again.state()["encrypted"] and again.state()["locked"] == 0
    # 關掉加密：馬上換回一般檔案；開著加密讀到的舊明文行，下次重寫時也加密
    assert again.set_encrypt(False) and "secret talk" in (tmp_path / "history.jsonl").read_text(encoding="utf-8")
    (tmp_path / "history.jsonl").write_text((tmp_path / "history.jsonl").read_text(encoding="utf-8") + json.dumps({"k": "old-1", "at": 5.0, "who": "me", "via": "mic", "original": "plain old", "translated": "舊的", "lang": "en", "target": "zh-TW", "place": ""}) + "\n", encoding="utf-8")
    mixed = History(tmp_path, True, 0, encrypt=True)
    assert mixed.count == 3 and "plain old" not in (tmp_path / "history.jsonl").read_text(encoding="utf-8")
    # 別的帳號加密的行：打不開，但原樣留著，重寫、關加密、整理都不會丟掉；按「全部清除」才刪
    fake_dpapi["key"] = 0x22
    other = History(tmp_path, True, 0, encrypt=True)
    assert other.count == 0 and other.state()["locked"] == 3
    other.add(_msg(9, "new one"))
    assert other.set_encrypt(False) and other.set_encrypt(True)
    fake_dpapi["key"] = 0x5A
    back = History(tmp_path, True, 0, encrypt=True)
    assert back.count == 3 and back.state()["locked"] == 1      # 換回原本的帳號：舊的 3 筆都在，那個帳號新記的 1 筆這邊打不開、也還在
    fake_dpapi["key"] = 0x22
    assert History(tmp_path, True, 0, encrypt=True).count == 1
    fake_dpapi["key"] = 0x33
    stranger = History(tmp_path, True, 0, encrypt=False)
    assert stranger.state()["locked"] == 4 and stranger.clear() == 4 and not (tmp_path / "history.jsonl").exists()


def test_history_encrypt_failure_keeps_plain_file(tmp_path, fake_dpapi):
    from livelingo.history import History

    h = History(tmp_path, True, 0, encrypt=False)
    h.add(_msg(1, "keep me"))
    before = (tmp_path / "history.jsonl").read_text(encoding="utf-8")
    fake_dpapi["fail"] = True
    assert h.set_encrypt(True) is False and h.encrypt is False and (tmp_path / "history.jsonl").read_text(encoding="utf-8") == before
    fake_dpapi["fail"] = False
    h2 = History(tmp_path, True, 0, encrypt=True)
    fake_dpapi["fail"] = True
    assert h2.add(_msg(2, "not leaked")) is True      # 加密失敗：記在記憶體，不把明文寫出去
    assert "not leaked" not in (tmp_path / "history.jsonl").read_text(encoding="utf-8") and h2.error == "encrypt"


def test_wordbook_encrypts_and_never_overwrites_locked(tmp_path, fake_dpapi):
    from livelingo.learn import Wordbook

    wb = Wordbook(tmp_path, encrypt=True)
    wb.add("insane", "en", "瘋狂", "that was insane")
    wb.add_sentence("see you later", "en", "待會見")
    raw = (tmp_path / "wordbook.json").read_text(encoding="utf-8")
    assert raw.startswith("enc1:") and "insane" not in raw
    same = Wordbook(tmp_path, encrypt=True)
    assert [w["w"] for w in same.listing()] == ["insane"] and len(same.sentences) == 1 and not same.locked
    assert same.set_encrypt(False) and json.loads((tmp_path / "wordbook.json").read_text(encoding="utf-8"))["words"][0]["w"] == "insane"
    assert same.set_encrypt(True)
    fake_dpapi["key"] = 0x44
    locked = Wordbook(tmp_path, encrypt=True)
    assert locked.locked and locked.words == [] and locked.error == "locked"
    locked.add("new", "en", "新的")      # 打不開的檔案不能被新的內容蓋掉
    assert (tmp_path / "wordbook.json").read_text(encoding="utf-8") == raw or (tmp_path / "wordbook.json").read_text(encoding="utf-8").startswith("enc1:")
    assert not locked.set_encrypt(False)
    fake_dpapi["key"] = 0x5A
    assert [w["w"] for w in Wordbook(tmp_path, encrypt=True).listing()] == ["insane"]      # 換回來：原本的還在，沒有 new
    fake_dpapi["key"] = 0x44
    assert Wordbook(tmp_path, encrypt=True).clear() == 0 and not (tmp_path / "wordbook.json").exists()


# ---------------------------------------------------------------- 服務：遮蔽、加密開關、省電、安全模式、頻道


def test_service_masks_what_it_saves_but_not_what_it_shows(tmp_path):
    from livelingo.service import Service

    service = Service(tmp_path)
    service.hub.publish = lambda *a, **k: None
    try:
        text = "my password is hunter2 and mail a@b.co"
        msg = _msg(7, text)
        shown = service._post(dict(msg))
        assert shown["original"] == text                                    # 畫面上、浮動字幕照常
        saved = service.history.search()["items"][-1]
        assert "hunter2" not in saved["original"] and "a@b.co" not in saved["original"] and "▒▒▒" in saved["original"]
        with service._lock:
            assert service.messages[-1]["original"] == text
        # 單字本的例句、收藏的整句、對話摘要用的句子也遮
        service.word_action("add", "mail", "en", meaning="信", msg_id=7)
        assert "a@b.co" not in service.wordbook.listing()[0]["example"] and "▒▒▒" in service.wordbook.listing()[0]["example"]
        service.sentence_action("save", msg_id=7)
        assert "hunter2" not in service.wordbook.sentences[-1]["text"]
        service.history.keep = False
        assert all("hunter2" not in r["original"] for r in service._recap_rows())
        # 關掉遮蔽：原樣存
        service.update_config({"redact": False})
        service.history.keep = True
        service._post(dict(_msg(8, "pw=abc123 x@y.co")))
        assert service.history.search()["items"][0]["original"] == "pw=abc123 x@y.co"
        assert "redact" in __import__("livelingo.service", fromlist=["RESET_KEEP"]).RESET_KEEP
    finally:
        service.shutdown()


def test_service_encrypt_switch(tmp_path, monkeypatch, fake_dpapi):
    from livelingo.service import Service

    service = Service(tmp_path)
    events, notices = [], []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    service.notice = lambda code, **p: notices.append(code)
    try:
        service._post(dict(_msg(1, "hello world")))
        service.wordbook.add("insane", "en", "瘋狂")
        service.update_config({"encrypt_data": True})
        assert service.cfg["encrypt_data"] and (tmp_path / "history.jsonl").read_text(encoding="utf-8").startswith("enc1:")
        assert (tmp_path / "wordbook.json").read_text(encoding="utf-8").startswith("enc1:") and service.state()["secure"] is True
        service._post(dict(_msg(2, "second")))
        assert (tmp_path / "history.jsonl").read_text(encoding="utf-8").count("enc1:") == 2      # 新記的也加密
        service.update_config({"encrypt_data": False})
        assert "hello world" in (tmp_path / "history.jsonl").read_text(encoding="utf-8") and json.loads((tmp_path / "wordbook.json").read_text(encoding="utf-8"))
        # 加密做不到：設定換回去、檔案不動、說一聲
        before = (tmp_path / "history.jsonl").read_text(encoding="utf-8")
        fake_dpapi["fail"] = True
        service.update_config({"encrypt_data": True})
        assert service.cfg["encrypt_data"] is False and notices == ["encrypt_failed"] and (tmp_path / "history.jsonl").read_text(encoding="utf-8") == before
        # 不是 Windows：開不了
        fake_dpapi["fail"] = False
        monkeypatch.setattr("livelingo.secure.available", lambda: False)
        service.update_config({"encrypt_data": True})
        assert service.cfg["encrypt_data"] is False and notices[-1] == "encrypt_unavailable" and service.state()["secure"] is False
    finally:
        service.shutdown()


def test_battery_saver_enters_and_restores_only_untouched_settings(tmp_path, monkeypatch):
    from livelingo import power
    from livelingo.service import Service

    monkeypatch.setattr(power, "on_battery", lambda: None)      # 「查不到電源」：power_check() 不給參數時就是去查，Windows 沒有電池的機器會答「有插電」，所以固定成 None
    service = Service(tmp_path)
    events, notices = [], []
    service.hub.publish = lambda name, data=None: events.append((name, data))
    service.notice = lambda code, **p: notices.append(code)
    service.watcher = type("Watcher", (), {"interval": 0.7, "stop": lambda self: None})()
    try:
        service.cfg.set(interval=0.7)
        service.update_config({"speech_mode": "accurate", "live_captions": True, "denoise": False})
        assert service.power_check(True) is False      # 功能沒開：靠電池也不動
        service.update_config({"battery_saver": True})
        assert service.power_check() is False and service.power_check(False) is False      # 查不到、有插電：不動
        assert service.power_check(True) is True
        assert service.cfg["speech_mode"] == "fast" and service.cfg["live_captions"] is False and service.cfg["denoise"] is False
        assert service.cfg["saver_saved"] == {"values": {"speech_mode": "accurate", "live_captions": True}}      # 只記有被換掉的
        assert abs(service.watcher.interval - 0.7 * power.SAVER_SLOW) < 1e-9 and ("saver", {"on": True}) in events and notices == ["saver_on"]
        assert service.power_check(True) is True and notices == ["saver_on"]      # 已經在省電：不重做
        service.update_config({"speech_mode": "accurate"})      # 省電期間自己改回去
        assert service.power_check() is True      # 查不到電源：維持現狀
        assert service.power_check(False) is False
        assert service.cfg["speech_mode"] == "accurate" and service.cfg["live_captions"] is True      # 沒被動過的換回去，被動過的不蓋掉
        assert service.cfg["saver_saved"] == {} and service.watcher.interval == 0.7 and notices == ["saver_on", "saver_off"] and ("saver", {"on": False}) in events
        # 靠電池的時候把功能關掉：馬上換回來
        service.power_check(True)
        service.update_config({"battery_saver": False})
        assert service.power_check(True) is False and service.cfg["live_captions"] is True
        # 靠電池時關掉程式、設定還是省電那一組：重開之後，插上電會換回來；還靠電池就接著省電（讀螢幕也照樣放慢）
        service.update_config({"battery_saver": True})
        service.power_check(True)
        saved_state = dict(service.cfg["saver_saved"])
        assert saved_state
    finally:
        service.watcher = None
        service.shutdown()
    reopened = Service(tmp_path)
    reopened.hub.publish = lambda *a, **k: None
    try:
        assert reopened._saver is True and reopened._screen_interval() == float(reopened.cfg["interval"]) * power.SAVER_SLOW
        assert reopened.power_check(False) is False and reopened.cfg["live_captions"] is True and reopened._screen_interval() == float(reopened.cfg["interval"])
    finally:
        reopened.shutdown()


def test_power_status_never_raises():
    from livelingo import power

    assert power.on_battery() in (True, False, None)
    assert power.SAVER_SET == {"speech_mode": "fast", "live_captions": False, "denoise": False}


def test_safe_mode(tmp_path, monkeypatch):
    from livelingo import safemode
    from livelingo.service import RESET_KEEP, Service

    monkeypatch.delenv("LIVELINGO_SAFE", raising=False)
    monkeypatch.setattr(safemode, "shift_held", lambda: False)
    assert not safemode.requested([]) and safemode.requested(["x", "--safe"])
    monkeypatch.setenv("LIVELINGO_SAFE", "1")
    assert safemode.requested([])
    monkeypatch.delenv("LIVELINGO_SAFE")
    monkeypatch.setattr(safemode, "shift_held", lambda: True)
    assert safemode.requested([])

    service = Service(tmp_path)
    service.hub.publish = lambda *a, **k: None
    started = []
    service.set_source = lambda key, on: started.append(key)
    try:
        service.cfg.set(setup_done=True, sources={"screen": False, "listen": True, "mic": True})
        service.update_config({"quality": "high", "their_lang": "ko", "my_lang": "zh-TW", "ui_scale": 130, "redact": False})
        service.safe_start()
        assert service.safe_mode and service.state()["safe_mode"] is True
        assert service.cfg["quality"] == "auto" and service.cfg["ui_scale"] == 100          # 預設設定
        assert service.cfg["their_lang"] == "ko" and service.cfg["redact"] is False and "redact" in RESET_KEEP      # 語言、敏感資料的開關不動
        service._resume_sources()
        assert started == []                                                              # 安全模式：不自動打開收音和讀螢幕
        undone = service.safe_action(keep=False)
        assert undone["ok"] and service.cfg["quality"] == "high" and service.cfg["ui_scale"] == 130 and not service.safe_mode
        service.safe_start()
        kept = service.safe_action(keep=True)
        assert kept["ok"] and service.cfg["quality"] == "auto" and not service.safe_mode and service.settings_backup() is None
        service.safe_mode = False
        service._resume_sources()
        assert started == ["listen", "mic"]                                               # 平常開啟：照常接著上次的來源
    finally:
        service.shutdown()


def _release(tag, base, prerelease=False, draft=False, installer=True):
    assets = [{"name": "LiveLingo-Setup.exe", "size": 1, "browser_download_url": f"{base}/file/{tag}"}] if installer else []
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "assets": assets}


def test_update_channels(tmp_path, monkeypatch):
    from livelingo import __version__, updater
    from livelingo.service import Service

    class Hub(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path == "/latest":
                body = _release("v9.0.0", base)
            else:
                body = [_release("v9.2.0", base, draft=True), _release("v9.1.0", base, prerelease=True), _release("v9.0.0", base),
                        _release("v9.3.0", base, prerelease=True, installer=False), _release("v1.0.0", base), "junk"]
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Hub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setenv("LIVELINGO_UPDATE_API", base + "/latest")
    monkeypatch.setenv("LIVELINGO_RELEASES_API", base + "/list")
    try:
        assert updater.CHANNELS == ("stable", "beta")
        assert updater.check()["version"] == "9.0.0"                               # 穩定版：看 latest（不含預先發行）
        beta = updater.check(channel="beta")
        assert beta["version"] == "9.1.0" and beta["url"].endswith("/file/v9.1.0")  # 搶先版：預先發行也算，草稿、沒有安裝檔的、比現在舊的不算，拿最新的
        assert updater.check(channel="stable")["version"] == "9.0.0"
        # 服務：穩定版呼叫沒有參數的 check()（舊測試換掉它的假函式沒有 channel 參數），搶先版才帶 channel
        calls = []
        monkeypatch.setattr(updater, "check", lambda *a, **k: calls.append((a, k)))
        service = Service(tmp_path)
        service.hub.publish = lambda *a, **k: None
        try:
            service._check_update()
            service.update_config({"update_channel": "beta"})
            service._check_update()
            service.update_config({"update_channel": "nightly"})
            assert service.cfg["update_channel"] == "beta"                          # 不認得的頻道不收
        finally:
            service.shutdown()
        assert calls[0] == ((), {}) and calls[1] == ((), {"channel": "beta"})
        assert __version__
    finally:
        httpd.shutdown()


def test_portable_build(tmp_path, monkeypatch):
    from livelingo import paths, updater
    from livelingo.__main__ import main

    app = tmp_path / "app"
    app.mkdir()
    assert paths.portable(app) is False
    (app / paths.PORTABLE_MARKER).write_text("x", encoding="utf-8")
    assert paths.portable(app) is True and paths.portable() is False      # 從原始碼跑的不算免安裝
    monkeypatch.delenv("LIVELINGO_DATA", raising=False)
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(paths, "app_dir", lambda: app)
    assert paths.portable() is True and paths.data_dir() == app / "data" and (app / "data").is_dir()
    monkeypatch.setenv("LIVELINGO_DATA", str(tmp_path / "forced"))
    assert paths.data_dir() == tmp_path / "forced"                          # 環境變數指定的優先（測試用）
    monkeypatch.delenv("LIVELINGO_DATA")
    monkeypatch.setenv("LIVELINGO_UPDATE_API", "http://127.0.0.1:1/x")
    assert updater.enabled() is False                                      # 免安裝版沒有安裝程式可以換檔案：不自動更新
    monkeypatch.setattr(paths, "app_dir", lambda: tmp_path)                # 旁邊沒有 portable.txt：照常
    assert updater.enabled() is True
    # 發行流程用 --data-dir 確認解壓縮出來的程式把資料放在旁邊
    monkeypatch.setattr(paths, "app_dir", lambda: app)
    out = tmp_path / "where.txt"
    monkeypatch.setattr(sys, "argv", ["livelingo", "--data-dir", str(out)])
    assert main() == 0 and Path(out.read_text(encoding="utf-8")) == app / "data"


def test_release_workflow_has_beta_and_portable():
    build = (ROOT.parent / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert "branches: [main, beta]" in build and "LiveLingo-Portable.zip" in build and "portable.txt" in build
    assert "--prerelease" in build and "refs/heads/beta" in build and "--data-dir" in build
    assert "gh release upload" in build and "release delete" in build
    for line in build.splitlines():
        assert "continue-on-error" not in line      # 免安裝版做壞了要讓整個流程失敗，不能默默放過


# ---------------------------------------------------------------- 接線


def test_wiring_for_2_21_0():
    from livelingo import highlights
    from livelingo.config import DEFAULTS, EDITABLE, UI_LANGS
    from livelingo.service import SETTINGS_SKIP

    i18n = (ROOT / "ui" / "i18n.js").read_text(encoding="utf-8")
    keys = ("sf_text", "sf_desc", "sf_undo", "sf_keep", "sf_kept", "sf_hint", "bs_title", "bs_hint", "bs_switch", "bs_desc", "bs_state_on", "bs_state_battery",
            "bs_state_ac", "bs_state_none", "n_saver_on", "n_saver_off", "rx_title", "rx_hint", "rx_switch", "rx_desc", "ec_switch", "ec_desc", "ec_unavailable",
            "ec_locked", "n_encrypt_unavailable", "n_encrypt_failed", "ch_title", "ch_stable", "ch_beta", "ch_desc", "ch_beta_note", "pt_title", "pt_about")
    for key in keys:
        assert len(re.findall(rf"(?<![\w]){key}: \"", i18n)) == len(UI_LANGS), key
    for key in ("battery_saver", "update_channel", "redact", "encrypt_data"):
        assert key in DEFAULTS and key in EDITABLE, key
    assert DEFAULTS["battery_saver"] is False and DEFAULTS["update_channel"] == "stable" and DEFAULTS["redact"] is True and DEFAULTS["encrypt_data"] is False
    assert "saver_saved" in DEFAULTS and "saver_saved" not in EDITABLE and "saver_saved" in SETTINGS_SKIP
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "service.safe_action(" in server and '"/api/safe"' in server
    main = (ROOT / "__main__.py").read_text(encoding="utf-8")
    assert "safemode.requested()" in main and "service.safe_start()" in main
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    for act in ("safe-undo", "safe-keep"):
        assert f'data-act="{act}"' in app and f'"{act}":' in app, act
    assert "saver: (d) =>" in app and 'id="safe-row"' in (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    for call in ('switchRow("battery_saver"', 'switchRow("redact"', 'switchRow("encrypt_data"', 'choiceRow("update_channel"'):
        assert call in app, call
    assert highlights.HIGHLIGHTS["2.21.0"] == ["general", "history", "about"]
    notes = json.loads((ROOT / "data" / "changes.json").read_text(encoding="utf-8"))["2.21.0"]
    assert len(notes["zh-TW"]) == 8 and set(notes) == set(UI_LANGS)
    notice_codes = set(re.findall(r'self\.notice\("([a-z_]+)"', (ROOT / "service.py").read_text(encoding="utf-8")))
    assert {"saver_on", "saver_off", "encrypt_unavailable", "encrypt_failed"} <= notice_codes
