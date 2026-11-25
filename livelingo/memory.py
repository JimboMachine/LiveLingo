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

匯出只匯出我自己的；預設不含我自己打的、說的（可能是私人聊天）。不會上傳到任何地方。
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


def terms_print(pairs) -> str:
    """詞彙表、用語包裡跟這句話有關的那幾條的指紋；沒有任何一條有關就是空字串。"""
    pairs = sorted({(str(a), str(b)) for a, b in pairs})
    if not pairs:
        return ""
    return hashlib.sha1(json.dumps(pairs, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def key_text(text: str) -> str:
    """比對用的原文：前後空白拿掉、中間連續的空白當一個。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]


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

    def lookup(self, text: str, lang: str, target: str, tone: str, model: str, terms: str) -> str | None:
        """記住的翻法；沒有能用的就回傳 None。順序：我教過的 > 匯入的人工改過的 > 我翻過的 > 匯入的。"""
        if not (self.mine_on or self.shared_on):
            return None
        src = key_text(text)
        rows = self._run("SELECT origin, dst, model, terms, fixed, at FROM memory WHERE src=? AND lang=? AND target=? AND tone=?",
                         (src, lang, target, tone))
        best, best_score = None, None
        for origin, dst, row_model, row_terms, fixed, at in rows:
            mine = origin == ""
            if (mine and not self.mine_on) or (not mine and not self.shared_on):
                continue
            if not fixed:
                if _rank(row_model) < _rank(model):
                    continue
                if (row_terms if mine else "") != terms:
                    continue
            # 分數：我教過的 3、匯入的人工改過的 2、我翻的 1、匯入的 0
            score = ((3 if mine else 2) if fixed else (1 if mine else 0), _rank(row_model), at)
            if best_score is None or score > best_score:
                best, best_score = (origin, dst), score
        if best is None:
            return None
        self._run("UPDATE memory SET used=? WHERE src=? AND lang=? AND target=? AND tone=? AND origin=?",
                  (self._now(), src, lang, target, tone, best[0]), commit=True)
        return best[1]

    def store(self, text: str, lang: str, target: str, tone: str, dst: str, model: str, terms: str, via: str = "") -> None:
        """記住一句翻好的（我的）。教過的那一句不會被蓋掉。"""
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
        self._stores += 1
        if self._stores % 500 == 0:
            self._prune()

    def _prune(self) -> None:
        rows = self._run("SELECT COUNT(*) FROM memory WHERE origin='' AND fixed=0")
        extra = (rows[0][0] if rows else 0) - MAX_MINE
        if extra > 0:
            self._run("""DELETE FROM memory WHERE rowid IN
                         (SELECT rowid FROM memory WHERE origin='' AND fixed=0 ORDER BY used LIMIT ?)""", (extra,), commit=True)

    def fix(self, text: str, lang: str, target: str, tone: str, dst: str, model: str = "") -> None:
        """使用者教了這一句（「翻錯了？」）：記成人工改過的，匯出時朋友也拿得到。"""
        src, dst = key_text(text), str(dst or "").strip()[:MAX_TEXT * 2]
        if not src or not dst:
            return
        now = self._now()
        self._run("""INSERT INTO memory (src, lang, target, tone, origin, dst, model, terms, fixed, via, at, used)
                     VALUES (?, ?, ?, ?, '', ?, ?, '', 1, '', ?, ?)
                     ON CONFLICT (src, lang, target, tone, origin) DO UPDATE SET dst=excluded.dst, fixed=1, at=excluded.at""",
                  (src, lang, target, tone, dst, str(model or ""), now, now), commit=True)

    def unfix(self, text: str, target: str) -> None:
        """教過的那一句被刪掉了：我記住的那一句（不管哪種語氣）也一起忘掉，下次重翻。"""
        self._run("DELETE FROM memory WHERE origin='' AND src=? AND target=?", (key_text(text), target), commit=True)

    def forget_mine(self, keep_fixed: bool = True) -> int:
        """清除我翻過的句子。keep_fixed：教過的留著（那些也在「我教過的句子」裡）。"""
        rows = self._run("SELECT COUNT(*) FROM memory WHERE origin=''" + (" AND fixed=0" if keep_fixed else ""))
        self._run("DELETE FROM memory WHERE origin=''" + (" AND fixed=0" if keep_fixed else ""), commit=True)
        return rows[0][0] if rows else 0

    # ---- 分享檔

    def export(self, range_key: str = "all", with_mine: bool = False, langs: tuple = ()) -> dict:
        """做出分享檔的內容（只有我自己的）。range_key：RANGES 裡的一個。with_mine：要不要含我自己打的、說的。
        langs：只要這幾種語言之間的（空的 = 全部）。"""
        since = RANGES.get(range_key, 0)
        if since is None:
            lt = time.localtime(self._now())
            since = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        elif since:
            since = self._now() - since
        sql = "SELECT src, dst, lang, target, tone, model, fixed, via FROM memory WHERE origin='' AND (at>=? OR fixed=1)"
        rows = self._run(sql + " ORDER BY at", (since,))
        items = []
        for src, dst, lang, target, tone, model, fixed, via in rows:
            if not with_mine and via in MINE_VIAS:
                continue
            if langs and (lang not in langs or target not in langs):
                continue
            items.append({"src": src, "dst": dst, "lang": lang, "target": target, "tone": tone, "model": model,
                          "fixed": bool(fixed)})
        return {"app": "LiveLingo", "kind": KIND, "version": 1, "made": int(self._now()), "items": items}

    def import_pack(self, data, name: str, sources=(), targets=()) -> dict:
        """匯入朋友的分享檔，當成一包（可以整包刪）。同一個檔案匯入第二次會換掉上一次的，不會重複。
        sources、targets：認得的原文語言和目標語言，別的不收。回傳 {"ok", "added", "skipped", "pack"}。"""
        if not isinstance(data, dict) or data.get("kind") != KIND or not isinstance(data.get("items"), list):
            return {"ok": False, "error": "not_pack"}
        rows, seen = [], set()
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
        skipped = max(0, len(data["items"]) - len(rows))
        if not rows:
            return {"ok": True, "added": 0, "skipped": skipped, "pack": None}
        pack = hashlib.sha1(json.dumps(sorted(rows), ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:60] or pack
        now = self._now()
        with self._lock:
            self._run("DELETE FROM memory WHERE origin=?", (pack,))
            self._run("INSERT OR REPLACE INTO packs (id, name, at) VALUES (?, ?, ?)", (pack, name, now))
            self._run("""INSERT OR REPLACE INTO memory (src, lang, target, tone, origin, dst, model, terms, fixed, via, at, used)
                         VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, '', ?, ?)""",
                      [(s, la, ta, to, pack, d, m, f, now, now) for s, la, ta, to, d, m, f in rows], many=True, commit=True)
        return {"ok": True, "added": len(rows), "skipped": skipped, "pack": {"id": pack, "name": name}}

    def remove_pack(self, pack: str) -> None:
        with self._lock:
            self._run("DELETE FROM memory WHERE origin=? AND origin<>''", (str(pack),))
            self._run("DELETE FROM packs WHERE id=?", (str(pack),), commit=True)

    def state(self) -> dict:
        """給設定畫面：我記住了幾句（其中幾句教過）、匯入了哪幾包。"""
        mine = self._run("SELECT COUNT(*), COALESCE(SUM(fixed), 0) FROM memory WHERE origin=''")
        packs = self._run("""SELECT p.id, p.name, p.at, COUNT(m.src) FROM packs p LEFT JOIN memory m ON m.origin = p.id
                             GROUP BY p.id ORDER BY p.at DESC""")
        count, fixed = (mine[0] if mine else (0, 0))
        return {"mine": count or 0, "fixed": fixed or 0, "error": self.error, "ok": self._db is not None,
                "packs": [{"id": i, "name": n, "at": int(a), "count": c} for i, n, a, c in packs]}
