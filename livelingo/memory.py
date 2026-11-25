"""
翻過的句子（翻譯記憶）：每一句翻好的句子記在這台電腦上（memory.db），同一句話再出現就直接用，不用再叫模型翻。
同一部影片看第二次、朋友傳來的翻譯檔，都不用重翻。

兩種來源：
- 我的：這台電腦自己翻的（origin 空的）。教過的（「翻錯了？」改過的）標成 fixed，最優先。
- 匯入的：朋友匯出的翻譯檔（origin = 那一包的 id）。可以整包刪掉，也可以整個不用（只用自己翻的）。

什麼時候不拿記住的翻法：
- 記住那時候用的模型比現在差（標準品質翻的，現在開著高品質）：重翻一次，記新的。
- 記住那時候的詞彙表、用語包跟現在對這句話的不一樣（terms 是那時候的指紋）：重翻。
  匯入的句子不知道朋友的詞彙表，所以只有在我的詞彙表、用語包跟這句話都沒關係的時候才用（人工改過的除外）。

作品（works.py）：每一句也記下是在哪一部影片、哪一個網頁看到的（work_lines），所以
- 同一部再出現，知道之前翻過幾句（我的、朋友的）；
- 同一部裡找不到一模一樣的句子時，找「幾乎一樣」的（語音辨識每次聽到的字可能差一點點）；
- 可以只匯出某一部，朋友匯入之後看同一部就直接有翻譯；
- 某一部可以設成「不用別人的」。

匯出只匯出我自己的；預設不含我自己打的、說的（可能是私人聊天）。不會上傳到任何地方。
分享檔第 2 版多了作品（works），只有匯出某一部的時候才帶標題；第 1 版的檔案照樣可以匯入。
"""

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

FILE = "memory.db"
KIND = "livelingo-translations"         # 分享檔的 kind
TONES = ("casual", "polite", "casual+keep", "polite+keep")      # 記住的翻法分開存的語氣（+keep = 保留語氣，Translator.memory_tone）
MAX_MINE = 100000                       # 我的（沒教過的）最多留這麼多句，再多就丟掉最久沒用到的
MAX_IMPORT = 50000                      # 一個分享檔最多收幾句
MAX_TEXT = 500
MODEL_RANK = {"4b": 1, "12b": 2}        # 模型好壞的順序；不認得的當最差
MINE_VIAS = ("typed", "spoken", "quick")      # 我自己打的、說的（匯出時預設不含）
RANGES = {"2h": 2 * 3600, "today": None, "all": 0}      # 匯出的範圍；today = 今天 0 點起
VERSION = 2                             # 分享檔的版本；第 2 版多了 works
SUBTITLE = 2                            # memory.fixed 的值：從字幕檔匯入的（人翻的，但常常濃縮過）
SHOWN = 3                               # memory.fixed 的值：畫面上雙語字幕現成的翻譯（origin = SCREEN）
SCREEN = "screen"                       # 畫面上現成的翻譯用的 origin（不是一包，不會匯出；清除我翻過的時一起清掉）
NEAR = 0.85                             # 同一部裡「幾乎一樣」的句子要多像才拿來用
NEAR_MIN = 4                            # 太短的句子不找幾乎一樣的（「好」「對啊」差一個字就是別的意思）
NEAR_SCAN = 5000                        # 同一部裡最多比對幾句
MAX_WORKS = 2000                        # 最多記幾部，再多就忘掉最久沒看的（句子留著）


def terms_print(pairs) -> str:
    """詞彙表、用語包裡跟這句話有關的那幾條的指紋；沒有任何一條有關就是空字串。"""
    pairs = sorted({(str(a), str(b)) for a, b in pairs})
    if not pairs:
        return ""
    return hashlib.sha1(json.dumps(pairs, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def key_text(text: str) -> str:
    """比對用的原文：前後空白拿掉、中間連續的空白當一個。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]


def _tier(mine: bool, fixed: int) -> int:
    """誰比較優先：我教過的 5、朋友教過的 4、字幕檔 3、畫面上現成的 2、我翻的 1、朋友機器翻的 0。"""
    if fixed == SUBTITLE:
        return 3
    if fixed == SHOWN:
        return 2
    if fixed:
        return 5 if mine else 4
    return 1 if mine else 0


def _rank(model: str) -> int:
    return MODEL_RANK.get(str(model or ""), 0)


class Memory:
    def __init__(self, folder: Path, now=time.time):
        self.path = folder / FILE
        self._now = now
        self._lock = threading.RLock()
        self.mine_on = True        # 記住、使用我自己翻過的（設定 remember_translations）
        self.shared_on = True      # 使用匯入的（設定 use_shared）
        self.error = ""
        self._stores = 0
        self._db = None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5)
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS memory (
                    src TEXT NOT NULL, lang TEXT NOT NULL, target TEXT NOT NULL, tone TEXT NOT NULL,
                    origin TEXT NOT NULL DEFAULT '', dst TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
                    terms TEXT NOT NULL DEFAULT '', fixed INTEGER NOT NULL DEFAULT 0, via TEXT NOT NULL DEFAULT '',
                    at REAL NOT NULL, used REAL NOT NULL,
                    PRIMARY KEY (src, lang, target, tone, origin));
                CREATE TABLE IF NOT EXISTS packs (id TEXT PRIMARY KEY, name TEXT NOT NULL, at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS memory_origin ON memory (origin, used);
                CREATE TABLE IF NOT EXISTS works (key TEXT PRIMARY KEY, title TEXT NOT NULL, seen REAL NOT NULL DEFAULT 0,
                    at REAL NOT NULL, own_only INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS work_lines (work TEXT NOT NULL, src TEXT NOT NULL, lang TEXT NOT NULL,
                    target TEXT NOT NULL, tone TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (work, src, lang, target, tone, origin));
                CREATE INDEX IF NOT EXISTS work_lines_origin ON work_lines (origin);
            """)
            self._db.commit()
        except sqlite3.Error as ex:
            self.error = str(ex)
            self._db = None

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def _run(self, sql: str, args=(), many: bool = False, commit: bool = False):
        """執行一句 SQL；資料庫壞了、寫不進去都不能讓翻譯失敗（當作沒記住）。"""
        with self._lock:
            if self._db is None:
                return []
            try:
                cur = self._db.executemany(sql, args) if many else self._db.execute(sql, args)
                rows = cur.fetchall()
                if commit:
                    self._db.commit()
                return rows
            except sqlite3.Error as ex:
                self.error = str(ex)
                return []

    # ---- 翻譯時用

    def lookup(self, text: str, lang: str, target: str, tone: str, model: str, terms: str, work: str = "") -> str | None:
        """記住的翻法；沒有能用的就回傳 None。"""
        found = self.find(text, lang, target, tone, model, terms, work)
        return found[0] if found else None

    def find(self, text: str, lang: str, target: str, tone: str, model: str, terms: str, work: str = ""):
        """記住的翻法和它是哪來的：(譯文, "mine" | "shared" | "subtitle", 是不是同一部裡幾乎一樣的句子)；沒有就 None。
        順序：我教過的 > 匯入的人工改過的 > 我翻過的 > 匯入的；同一級裡同一部的先。
        work：現在在看的那一部（works.work_key）；那一部設了「不用別人的」就只用我的。"""
        if not (self.mine_on or self.shared_on):
            return None
        shared_on = self.shared_on and not (work and self.own_only(work))
        src = key_text(text)
        rows = self._run("SELECT origin, dst, model, terms, fixed, at FROM memory WHERE src=? AND lang=? AND target=? AND tone=?",
                         (src, lang, target, tone))
        here = set()
        if work and rows:
            here = {o for (o,) in self._run("SELECT origin FROM work_lines WHERE work=? AND src=? AND lang=? AND target=? AND tone=?",
                                            (work, src, lang, target, tone))}
        best, best_score = None, None
        for origin, dst, row_model, row_terms, fixed, at in rows:
            mine = origin in ("", SCREEN)
            if (mine and not self.mine_on) or (not mine and not shared_on):
                continue
            if not fixed:
                if _rank(row_model) < _rank(model):
                    continue
                if (row_terms if mine else "") != terms:
                    continue
            score = (_tier(mine, fixed), origin in here, _rank(row_model), at)
            if best_score is None or score > best_score:
                best, best_score = (origin, dst, src, fixed), score
        near = False
        if best is None and work and not terms and len(src) >= NEAR_MIN:
            best, near = self._near(src, lang, target, tone, model, work, shared_on), True
        if best is None:
            return None
        origin, dst, row_src, fixed = best
        self._run("UPDATE memory SET used=? WHERE src=? AND lang=? AND target=? AND tone=? AND origin=?",
                  (self._now(), row_src, lang, target, tone, origin), commit=True)
        kind = "screen" if origin == SCREEN else "mine" if origin == "" else "subtitle" if fixed == SUBTITLE else "shared"
        return dst, kind, near

    def _near(self, src: str, lang: str, target: str, tone: str, model: str, work: str, shared_on: bool):
        """同一部裡「幾乎一樣」的句子（語音辨識差一兩個字）：(origin, 譯文, 那一句的原文, fixed)；沒有就 None。
        只用詞彙表、用語包跟它沒關係的（terms 空的），人工改過的除外。
        像不像用一個字一個字比（difflib）：11 個字差 1 個大約 0.91，差 2 個大約 0.82。"""
        from difflib import SequenceMatcher

        rows = self._run("""SELECT m.origin, m.dst, m.src, m.model, m.terms, m.fixed FROM work_lines w
                            JOIN memory m ON m.src = w.src AND m.lang = w.lang AND m.target = w.target AND m.tone = w.tone AND m.origin = w.origin
                            WHERE w.work=? AND w.lang=? AND w.target=? AND w.tone=? ORDER BY m.used DESC LIMIT ?""",
                         (work, lang, target, tone, NEAR_SCAN))
        best, best_score, best_rank = None, NEAR, (0.0, -1)
        for origin, dst, other, row_model, row_terms, fixed in rows:
            mine = origin in ("", SCREEN)
            if (mine and not self.mine_on) or (not mine and not shared_on) or abs(len(other) - len(src)) > max(3, len(src) // 4):
                continue
            if not fixed and (_rank(row_model) < _rank(model) or row_terms):
                continue
            score = SequenceMatcher(None, src, other, autojunk=False).ratio()
            if score >= best_score and (best is None or (score, _tier(mine, fixed)) > best_rank):
                best, best_score, best_rank = (origin, dst, other, fixed), NEAR, (score, _tier(mine, fixed))
        return best

    def store(self, text: str, lang: str, target: str, tone: str, dst: str, model: str, terms: str, via: str = "",
              work: str = "") -> None:
        """記住一句翻好的（我的）。教過的那一句不會被蓋掉。work：在哪一部看到的（沒有就空的）。"""
        src, dst = key_text(text), str(dst or "").strip()[:MAX_TEXT * 2]
        if not self.mine_on or not src or not dst or not lang or not target:
            return
        now = self._now()
        self._run("""INSERT INTO memory (src, lang, target, tone, origin, dst, model, terms, fixed, via, at, used)
                     VALUES (?, ?, ?, ?, '', ?, ?, ?, 0, ?, ?, ?)
                     ON CONFLICT (src, lang, target, tone, origin) DO UPDATE SET
                       dst=excluded.dst, model=excluded.model, terms=excluded.terms, via=excluded.via, at=excluded.at, used=excluded.used
                     WHERE memory.fixed = 0""",
                  (src, lang, target, tone, dst, str(model or ""), terms, str(via or ""), now, now), commit=True)
        if work:
            self._run("INSERT OR IGNORE INTO work_lines (work, src, lang, target, tone, origin) VALUES (?, ?, ?, ?, ?, '')",
                      (work, src, lang, target, tone), commit=True)
        self._stores += 1
        if self._stores % 500 == 0:
            self._prune()

    def _prune(self) -> None:
        rows = self._run("SELECT COUNT(*) FROM memory WHERE origin='' AND fixed=0")
        extra = (rows[0][0] if rows else 0) - MAX_MINE
        if extra > 0:
            self._run("""DELETE FROM memory WHERE rowid IN
                         (SELECT rowid FROM memory WHERE origin='' AND fixed=0 ORDER BY used LIMIT ?)""", (extra,), commit=True)
        self._tidy_lines()

    def _tidy_lines(self) -> None:
        """句子被丟掉之後，指向它的作品記錄也拿掉；作品太多就忘掉最久沒看的。"""
        self._run("""DELETE FROM work_lines WHERE NOT EXISTS (SELECT 1 FROM memory m WHERE m.src = work_lines.src AND m.lang = work_lines.lang
                     AND m.target = work_lines.target AND m.tone = work_lines.tone AND m.origin = work_lines.origin)""")
        rows = self._run("SELECT COUNT(*) FROM works")
        extra = (rows[0][0] if rows else 0) - MAX_WORKS
        if extra > 0:
            old = [k for (k,) in self._run("SELECT key FROM works ORDER BY MAX(seen, at) LIMIT ?", (extra,))]
            self._run("DELETE FROM work_lines WHERE work IN (%s)" % ",".join("?" * len(old)), old)
            self._run("DELETE FROM works WHERE key IN (%s)" % ",".join("?" * len(old)), old)
        self._run("DELETE FROM works WHERE seen=0 AND NOT EXISTS (SELECT 1 FROM work_lines w WHERE w.work = works.key)", commit=True)

    def fix(self, text: str, lang: str, target: str, tone: str, dst: str, model: str = "", work: str = "") -> None:
        """使用者教了這一句（「翻錯了？」）：記成人工改過的，匯出時朋友也拿得到。work：在哪一部看到的。"""
        src, dst = key_text(text), str(dst or "").strip()[:MAX_TEXT * 2]
        if not src or not dst:
            return
        now = self._now()
        self._run("""INSERT INTO memory (src, lang, target, tone, origin, dst, model, terms, fixed, via, at, used)
                     VALUES (?, ?, ?, ?, '', ?, ?, '', 1, '', ?, ?)
                     ON CONFLICT (src, lang, target, tone, origin) DO UPDATE SET dst=excluded.dst, fixed=1, at=excluded.at""",
                  (src, lang, target, tone, dst, str(model or ""), now, now), commit=True)
        if work:
            self._run("INSERT OR IGNORE INTO work_lines (work, src, lang, target, tone, origin) VALUES (?, ?, ?, ?, ?, '')",
                      (work, src, lang, target, tone), commit=True)

    def unfix(self, text: str, target: str) -> None:
        """教過的那一句被刪掉了：我記住的那一句（不管哪種語氣）也一起忘掉，下次重翻。"""
        self._run("DELETE FROM memory WHERE origin='' AND src=? AND target=?", (key_text(text), target), commit=True)

    def forget_mine(self, keep_fixed: bool = True) -> int:
        """清除我翻過的句子。keep_fixed：教過的留著（那些也在「我教過的句子」裡）。"""
        rows = self._run("SELECT COUNT(*) FROM memory WHERE origin=''" + (" AND fixed=0" if keep_fixed else ""))
        self._run("DELETE FROM memory WHERE origin=''" + (" AND fixed=0" if keep_fixed else ""), commit=True)
        self._run("DELETE FROM memory WHERE origin=?", (SCREEN,), commit=True)
        self._tidy_lines()
        return rows[0][0] if rows else 0

    # ---- 分享檔

    def export(self, range_key: str = "all", with_mine: bool = False, langs: tuple = (), work: str = "", title: str = "") -> dict:
        """做出分享檔的內容（只有我自己的）。range_key：RANGES 裡的一個。with_mine：要不要含我自己打的、說的。
        langs：只要這幾種語言之間的（空的 = 全部）。
        work：只匯出這一部（不管什麼時候翻的），標題也寫進去，朋友匯入後看同一部就認得；title：改用這個名字。
        沒選某一部的時候不帶任何標題（你看過哪些東西不會跟著出去）。"""
        since = RANGES.get(range_key, 0)
        if since is None:
            lt = time.localtime(self._now())
            since = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        elif since:
            since = self._now() - since
        works = []
        if work:
            rows = self._run("""SELECT m.src, m.dst, m.lang, m.target, m.tone, m.model, m.fixed, m.via FROM work_lines w
                                JOIN memory m ON m.src = w.src AND m.lang = w.lang AND m.target = w.target AND m.tone = w.tone AND m.origin = ''
                                WHERE w.work=? AND w.origin='' ORDER BY m.at""", (work,))
            named = self._run("SELECT title FROM works WHERE key=?", (work,))
            name = re.sub(r"\s+", " ", str(title or "")).strip()[:120] or (named[0][0] if named else "")
            works = [{"key": work, "title": name}]
        else:
            sql = "SELECT src, dst, lang, target, tone, model, fixed, via FROM memory WHERE origin='' AND (at>=? OR fixed=1)"
            rows = self._run(sql + " ORDER BY at", (since,))
        items = []
        for src, dst, lang, target, tone, model, fixed, via in rows:
            if not with_mine and via in MINE_VIAS:
                continue
            if langs and (lang not in langs or target not in langs):
                continue
            item = {"src": src, "dst": dst, "lang": lang, "target": target, "tone": tone, "model": model, "fixed": bool(fixed)}
            if works:
                item["works"] = [0]
            items.append(item)
        return {"app": "LiveLingo", "kind": KIND, "version": VERSION, "made": int(self._now()), "works": works, "items": items}

    def import_pack(self, data, name: str, sources=(), targets=()) -> dict:
        """匯入朋友的分享檔，當成一包（可以整包刪）。同一個檔案匯入第二次會換掉上一次的，不會重複。
        sources、targets：認得的原文語言和目標語言，別的不收。回傳 {"ok", "added", "skipped", "pack", "works"}。
        第 2 版的檔案帶著作品：句子記在那一部底下，之後看到同一部就認得。作品的 key 用標題重算，不信檔案裡寫的。"""
        from .works import clean_title, work_key

        if not isinstance(data, dict) or data.get("kind") != KIND or not isinstance(data.get("items"), list):
            return {"ok": False, "error": "not_pack"}
        works = []                       # 檔案裡第幾部 → (key, 標題)；認不得的是 None
        for w in (data.get("works") if isinstance(data.get("works"), list) else [])[:200]:
            title = clean_title(w.get("title")) if isinstance(w, dict) else ""
            key = work_key(title)
            works.append((key, title) if key else None)
        rows, links, seen = [], set(), set()
        for item in data["items"][:MAX_IMPORT]:
            if not isinstance(item, dict):
                continue
            src, dst = key_text(item.get("src")), str(item.get("dst") or "").strip()[:MAX_TEXT * 2]
            lang, target, tone = str(item.get("lang") or ""), str(item.get("target") or ""), str(item.get("tone") or "casual")
            if (not src or not dst or src == dst or lang not in sources or target not in targets
                    or tone not in TONES or (src, lang, target, tone) in seen):
                continue
            seen.add((src, lang, target, tone))
            model = str(item.get("model") or "")
            rows.append((src, lang, target, tone, dst, model if model in MODEL_RANK else "", 1 if item.get("fixed") is True else 0))
            for i in (item.get("works") if isinstance(item.get("works"), list) else [])[:20]:
                if isinstance(i, int) and 0 <= i < len(works) and works[i]:
                    links.add((works[i][0], src, lang, target, tone))
        skipped = max(0, len(data["items"]) - len(rows))
        if not rows:
            return {"ok": True, "added": 0, "skipped": skipped, "pack": None, "works": 0}
        pack = hashlib.sha1(json.dumps([sorted(rows), sorted(links)], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:60] or pack
        now = self._now()
        used = {k: t for k, t in (w for w in works if w)}
        used = {k: t for k, t in used.items() if any(link[0] == k for link in links)}
        with self._lock:
            self._run("DELETE FROM memory WHERE origin=?", (pack,))
            self._run("DELETE FROM work_lines WHERE origin=?", (pack,))
            self._run("INSERT OR REPLACE INTO packs (id, name, at) VALUES (?, ?, ?)", (pack, name, now))
            self._run("""INSERT OR REPLACE INTO memory (src, lang, target, tone, origin, dst, model, terms, fixed, via, at, used)
                         VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, '', ?, ?)""",
                      [(s, la, ta, to, pack, d, m, f, now, now) for s, la, ta, to, d, m, f in rows], many=True)
            self._run("INSERT OR IGNORE INTO works (key, title, seen, at) VALUES (?, ?, 0, ?)",
                      [(k, t, now) for k, t in used.items()], many=True)
            self._run("INSERT OR IGNORE INTO work_lines (work, src, lang, target, tone, origin) VALUES (?, ?, ?, ?, ?, ?)",
                      [(*link, pack) for link in links], many=True, commit=True)
        return {"ok": True, "added": len(rows), "skipped": skipped, "pack": {"id": pack, "name": name}, "works": len(used)}

    def seen_on_screen(self, text: str, lang: str, target: str, dst: str, work: str = "") -> None:
        """畫面上雙語字幕現成的翻譯：記下來（兩種語氣都記），排在字幕檔後面、機器翻的前面。不會匯出。"""
        src, dst = key_text(text), str(dst or "").strip()[:MAX_TEXT * 2]
        if not self.mine_on or not src or not dst or src == dst:
            return
        now = self._now()
        self._run("""INSERT OR REPLACE INTO memory (src, lang, target, tone, origin, dst, model, terms, fixed, via, at, used)
                     VALUES (?, ?, ?, ?, ?, ?, '', '', ?, 'shown', ?, ?)""",
                  [(src, lang, target, tone, SCREEN, dst, SHOWN, now, now) for tone in TONES], many=True, commit=True)
        if work:
            self._run("INSERT OR IGNORE INTO work_lines (work, src, lang, target, tone, origin) VALUES (?, ?, ?, ?, ?, ?)",
                      [(work, src, lang, target, tone, SCREEN) for tone in TONES], many=True, commit=True)

    def import_subtitles(self, pairs, lang: str, target: str, name: str, work: str = "", title: str = "") -> dict:
        """匯入字幕檔配好的句子 [(原文, 翻譯)]，當成一包（可以整包刪），記在 work 那一部底下。
        字幕是人翻的：比機器翻的優先，比教過的後面（fixed = SUBTITLE）。兩種語氣都記（字幕不分語氣）。"""
        rows, seen = [], set()
        for src, dst in list(pairs)[:MAX_IMPORT]:
            src, dst = key_text(src), str(dst or "").strip()[:MAX_TEXT * 2]
            if not src or not dst or src == dst or src in seen:
                continue
            seen.add(src)
            rows.append((src, dst))
        if not rows:
            return {"ok": False, "error": "no_pairs"}
        pack = "s" + hashlib.sha1(json.dumps([lang, target, work, rows], ensure_ascii=False).encode("utf-8")).hexdigest()[:11]
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:60] or pack
        now = self._now()
        with self._lock:
            self._run("DELETE FROM memory WHERE origin=?", (pack,))
            self._run("DELETE FROM work_lines WHERE origin=?", (pack,))
            self._run("INSERT OR REPLACE INTO packs (id, name, at) VALUES (?, ?, ?)", (pack, name, now))
            self._run("""INSERT OR REPLACE INTO memory (src, lang, target, tone, origin, dst, model, terms, fixed, via, at, used)
                         VALUES (?, ?, ?, ?, ?, ?, '', '', ?, 'subtitle', ?, ?)""",
                      [(s, lang, target, tone, pack, d, SUBTITLE, now, now) for s, d in rows for tone in TONES], many=True)
            if work:
                self._run("INSERT OR IGNORE INTO works (key, title, seen, at) VALUES (?, ?, 0, ?)", (work, str(title or "")[:120], now))
                self._run("INSERT OR IGNORE INTO work_lines (work, src, lang, target, tone, origin) VALUES (?, ?, ?, ?, ?, ?)",
                          [(work, s, lang, target, tone, pack) for s, _ in rows for tone in TONES], many=True)
            self._run("SELECT 1", commit=True)
        return {"ok": True, "added": len(rows), "pack": {"id": pack, "name": name}}

    def remove_pack(self, pack: str) -> None:
        with self._lock:
            self._run("DELETE FROM memory WHERE origin=? AND origin<>''", (str(pack),))
            self._run("DELETE FROM work_lines WHERE origin=? AND origin<>''", (str(pack),))
            self._run("DELETE FROM works WHERE seen=0 AND NOT EXISTS (SELECT 1 FROM work_lines w WHERE w.work = works.key)")
            self._run("DELETE FROM packs WHERE id=?", (str(pack),), commit=True)

    # ---- 作品（哪一部影片、哪一個網頁）

    def saw(self, key: str, title: str) -> None:
        """現在在看這一部（讀取來源的視窗標題整理過的）。"""
        if not key:
            return
        now = self._now()
        self._run("""INSERT INTO works (key, title, seen, at) VALUES (?, ?, ?, ?)
                     ON CONFLICT (key) DO UPDATE SET title=excluded.title, seen=excluded.seen""",
                  (key, str(title or "")[:120], now, now), commit=True)

    def work_info(self, key: str) -> dict | None:
        """這一部記了幾句：{"key", "title", "mine", "shared", "packs": [名稱], "own_only"}；沒記過就 None。"""
        named = self._run("SELECT title, own_only FROM works WHERE key=?", (key,))
        if not named:
            return None
        counts = self._run("""SELECT w.origin, COUNT(DISTINCT w.src || char(31) || w.lang || char(31) || w.target), p.name FROM work_lines w LEFT JOIN packs p ON p.id = w.origin
                              WHERE w.work=? GROUP BY w.origin""", (key,))
        mine = sum(c for o, c, _ in counts if o in ("", SCREEN))
        shared = sum(c for o, c, _ in counts if o not in ("", SCREEN))
        return {"key": key, "title": named[0][0], "mine": mine, "shared": shared, "own_only": bool(named[0][1]),
                "packs": [n for o, c, n in counts if o not in ("", SCREEN) and n]}

    def own_only(self, key: str) -> bool:
        rows = self._run("SELECT own_only FROM works WHERE key=?", (key,))
        return bool(rows and rows[0][0])

    def set_own_only(self, key: str, on: bool) -> None:
        """這一部不用別人的翻譯（只用我自己翻的、教過的）。"""
        self._run("UPDATE works SET own_only=? WHERE key=?", (1 if on else 0, key), commit=True)

    def forget_work(self, key: str) -> None:
        """忘掉這一部：標題、哪些句子是在這部看到的、我在這部翻的（沒教過的）句子都刪掉。朋友那一包的句子留著。"""
        with self._lock:
            self._run("""DELETE FROM memory WHERE origin='' AND fixed=0 AND EXISTS (SELECT 1 FROM work_lines w WHERE w.work=?
                         AND w.origin='' AND w.src = memory.src AND w.lang = memory.lang AND w.target = memory.target AND w.tone = memory.tone)
                         AND NOT EXISTS (SELECT 1 FROM work_lines o WHERE o.work<>? AND o.origin='' AND o.src = memory.src
                         AND o.lang = memory.lang AND o.target = memory.target AND o.tone = memory.tone)""", (key, key))
            self._run("DELETE FROM work_lines WHERE work=?", (key,))
            self._run("DELETE FROM works WHERE key=?", (key,), commit=True)

    def works(self, limit: int = 50) -> list[dict]:
        """記過的作品，最近看的在前：[{"key", "title", "mine", "shared", "seen", "own_only"}]。"""
        rows = self._run("""SELECT k.key, k.title, k.seen, k.at, k.own_only,
                              COUNT(DISTINCT CASE WHEN w.origin IN ('', 'screen') THEN w.src || char(31) || w.lang || char(31) || w.target END),
                              COUNT(DISTINCT CASE WHEN w.origin NOT IN ('', 'screen') THEN w.origin || char(31) || w.src || char(31) || w.lang || char(31) || w.target END)
                            FROM works k LEFT JOIN work_lines w ON w.work = k.key
                            GROUP BY k.key ORDER BY MAX(k.seen, k.at) DESC LIMIT ?""", (int(limit),))
        return [{"key": k, "title": t, "seen": int(seen), "own_only": bool(o), "mine": m, "shared": sh}
                for k, t, seen, at, o, m, sh in rows]

    def state(self) -> dict:
        """給設定畫面：我記住了幾句（其中幾句教過）、匯入了哪幾包。"""
        mine = self._run("SELECT COUNT(*), COALESCE(SUM(fixed), 0) FROM memory WHERE origin=''")
        packs = self._run("""SELECT p.id, p.name, p.at, COUNT(DISTINCT m.src || char(31) || m.lang || char(31) || m.target) FROM packs p LEFT JOIN memory m ON m.origin = p.id
                             GROUP BY p.id ORDER BY p.at DESC""")
        count, fixed = (mine[0] if mine else (0, 0))
        return {"mine": count or 0, "fixed": fixed or 0, "error": self.error, "ok": self._db is not None,
                "packs": [{"id": i, "name": n, "at": int(a), "count": c} for i, n, a, c in packs], "works": self.works()}
