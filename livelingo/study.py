"""學習工具（2.19.0）：用自己的對話出題、跟讀打分、匯出 Anki、問「為什麼這樣說」、韓文漢字詞對照、對話回放練習。
全部只用單字本、歷史和本機的翻譯模型。"""

import difflib
import random
import re
import time
import unicodedata
from pathlib import Path

# ---- 用自己的對話出題（59）


def _blank(example: str, word: str) -> str:
    """例句裡把那個字換成 ＿＿（找不到就不能出填空題）。"""
    i = example.lower().find(word.lower())
    return "" if i < 0 else example[:i] + "＿＿" + example[i + len(word):]


def quiz(words: list[dict], n: int = 10, rng: random.Random | None = None) -> list[dict]:
    """從單字本出題：有例句的出填空（選哪個字），其他的出選擇（選意思）。至少要 4 個有意思的字才出得了題。
    回傳 [{kind: fill/meaning, prompt, hint, choices, answer, w, lang}]。"""
    rng = rng or random.Random()
    usable = [w for w in words if w.get("meaning")]
    if len(usable) < 4:
        return []
    picked = rng.sample(usable, min(n, len(usable)))
    out = []
    for w in picked:
        same = [x for x in usable if x["lang"] == w["lang"] and x["w"] != w["w"]] or [x for x in usable if x["w"] != w["w"]]
        others = rng.sample(same, min(3, len(same)))
        blank = _blank(w.get("example") or "", w["w"]) if w.get("example") else ""
        if blank:
            choices = [w["w"]] + [x["w"] for x in others]
            item = {"kind": "fill", "prompt": blank, "hint": w.get("example_tr") or "", "answer": w["w"]}
        else:
            choices = [w["meaning"]] + [x["meaning"] for x in others if x["meaning"] != w["meaning"]]
            item = {"kind": "meaning", "prompt": w["w"], "hint": "", "answer": w["meaning"]}
        rng.shuffle(choices)
        out.append(dict(item, choices=choices, w=w["w"], lang=w["lang"]))
    return out


# ---- 跟讀打分（60）


def _units(text: str, lang: str) -> list[str]:
    """比對用的單位：中日韓泰一個字一個字比，其他語言一個詞一個詞比。大小寫、標點不算。"""
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = "".join(ch if not unicodedata.category(ch).startswith(("P", "S")) else " " for ch in text)
    if str(lang).split("-")[0] in ("zh", "ja", "ko", "th", "yue"):
        return [ch for ch in text if not ch.isspace()]
    return text.split()


def shadow_score(expected: str, heard: str, lang: str) -> dict:
    """念的跟原句比：0~100 分，和沒念到（或念錯）的地方。"""
    want, got = _units(expected, lang), _units(heard, lang)
    if not want:
        return {"score": 0, "missed": []}
    match = difflib.SequenceMatcher(None, want, got, autojunk=False)
    score = round(100 * match.ratio())
    missed, joiner = [], "" if str(lang).split("-")[0] in ("zh", "ja", "ko", "th", "yue") else " "
    for op, a1, a2, _b1, _b2 in match.get_opcodes():
        if op in ("delete", "replace"):
            missed.append(joiner.join(want[a1:a2]))
    return {"score": score, "missed": missed[:5]}


# ---- 匯出到 Anki（64）


def anki_text(words: list[dict], sentences: list[dict]) -> str:
    """Anki 的「匯入檔案」看得懂的純文字：一行一張卡，tab 分開（正面、背面、例句）。"""
    def clean(text: str) -> str:
        return " ".join(str(text or "").replace("\t", " ").split())

    lines = ["#separator:tab", "#html:false", "#columns:Front\tBack\tExample", "#tags column:4"]
    for w in words:
        example = clean(w.get("example"))
        if w.get("example_tr"):
            example = f"{example} → {clean(w['example_tr'])}" if example else clean(w["example_tr"])
        lines.append(f"{clean(w['w'])}\t{clean(w.get('meaning'))}\t{example}\tLiveLingo {w.get('lang', '')}")
    for s in sentences:
        back = clean(s.get("tr"))
        if s.get("note"):
            back = f"{back} ({clean(s['note'])})" if back else clean(s["note"])
        lines.append(f"{clean(s['text'])}\t{back}\t\tLiveLingo {s.get('lang', '')} sentence")
    return "\n".join(lines) + "\n"


def save_anki(folder: Path, words: list[dict], sentences: list[dict]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stem = "LiveLingo Anki " + time.strftime("%Y-%m-%d")
    path, n = folder / f"{stem}.txt", 2
    while path.exists():
        path, n = folder / f"{stem} ({n}).txt", n + 1
    path.write_text(anki_text(words, sentences), encoding="utf-8")
    return path


# ---- 問「為什麼這樣說」（66）

_NAMES = {"zh-TW": "Traditional Chinese (Taiwan)", "zh-CN": "Simplified Chinese", "en": "English", "ko": "Korean", "ja": "Japanese",
          "vi": "Vietnamese", "th": "Thai", "id": "Indonesian", "es": "Spanish", "pt": "Portuguese", "ru": "Russian", "de": "German",
          "fr": "French"}


def explain_prompt(sentence: str, translated: str, my_lang: str) -> str:
    mine = _NAMES.get(my_lang) or _NAMES.get(str(my_lang).split("-")[0]) or "English"
    sentence = " ".join(str(sentence or "").split())[:300]
    translated = " ".join(str(translated or "").split())[:300]
    return (f"A learner saw this sentence in an online game chat or voice call: \"{sentence}\" (meaning: \"{translated}\").\n"
            f"In {mine}, explain in at most 3 short lines why it is said this way: the key grammar, ending or word choice, "
            f"and how polite or casual it sounds. No greeting, no repeating the sentence.")


def parse_explain(out: str) -> str:
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", x).strip() for x in str(out or "").splitlines()]
    lines = [x for x in lines if x][:3]
    return "\n".join(x[:160] for x in lines)


# ---- 韓文漢字詞對照（67）：常見的漢字詞直接查表；表裡沒有的才問模型，問到的要每個字都是漢字、字數一樣才收

HANJA = dict(x.split("=") for x in """
학교=學校 학생=學生 선생님=先生님 회사=會社 사람=- 시간=時間 문제=問題 생각=- 사용=使用 경우=境遇 정도=程度 가능=可能 필요=必要
중요=重要 시작=始作 준비=準備 연습=練習 공격=攻擊 방어=防禦 전투=戰鬪 전략=戰略 작전=作戰 정보=情報 위치=位置 상대=相對 동료=同僚
친구=親舊 가족=家族 부모=父母 형제=兄弟 결혼=結婚 생일=生日 축하=祝賀 감사=感謝 미안=未安 실례=失禮 부탁=付託 약속=約束 연락=連絡
전화=電話 문자=文字 사진=寫眞 영상=映像 음악=音樂 영화=映畫 게임=- 경기=競技 시합=試合 승리=勝利 패배=敗北 무승부=無勝負 점수=點數
순위=順位 등급=等級 레벨=- 경험=經驗 능력=能力 기술=技術 장비=裝備 무기=武器 방패=防牌 회복=回復 체력=體力 마법=魔法 공간=空間
지도=地圖 방향=方向 동쪽=東쪽 서쪽=西쪽 남쪽=南쪽 북쪽=北쪽 중앙=中央 주변=周邊 근처=近處 거리=距離 속도=速度 시작점=始作點
목표=目標 목적=目的 이유=理由 결과=結果 원인=原因 방법=方法 계획=計劃 의견=意見 질문=質問 대답=對答 설명=說明 이해=理解 기억=記憶
공부=工夫 숙제=宿題 시험=試驗 수업=授業 대학=大學 졸업=卒業 직업=職業 회의=會議 출근=出勤 퇴근=退勤 휴가=休暇 주말=週末 오전=午前
오후=午後 매일=每日 매주=每週 다음주=-週 지난주=-週 내년=來年 작년=昨年 올해=- 시계=時計 일기=日記 날씨=- 기분=氣分 행복=幸福
걱정=- 안전=安全 위험=危險 사고=事故 병원=病院 의사=醫師 간호사=看護師 약국=藥局 건강=健康 운동=運動 식사=食事 음식=飮食 요리=料理
식당=食堂 주문=注文 계산=計算 가격=價格 무료=無料 유료=有料 할인=割引 상점=商店 시장=市場 은행=銀行 교통=交通 지하철=地下鐵 공항=空港
여행=旅行 호텔=- 도시=都市 국가=國家 한국=韓國 중국=中國 일본=日本 미국=美國 대만=臺灣 세계=世界 문화=文化 역사=歷史 언어=言語
한국어=韓國語 중국어=中國語 영어=英語 일본어=日本語 번역=飜譯 통역=通譯 의미=意味 단어=單語 문장=文章 발음=發音 문법=文法 대화=對話
연결=連結 접속=接續 서버=- 채팅=- 신고=申告 차단=遮斷 초대=招待 참가=參加 참여=參與 모집=募集 협력=協力 도움=- 지원=支援 응원=應援
실수=失手 성공=成功 실패=失敗 완료=完了 보상=補償 선물=膳物 상품=商品 아이템=- 확인=確認 취소=取消 변경=變更 설정=設定 화면=畫面
소리=- 음성=音聲 자막=字幕 방송=放送 시청=視聽 구독=購讀 동영상=動映像 최고=最高 최악=最惡 인기=人氣 유명=有名 정말=正말
진짜=眞짜 사랑=- 감정=感情 표정=表情 남자=男子 여자=女子 학년=學年 동생=同生 선배=先輩 후배=後輩 사장=社長 부장=部長 직원=職員
""".split() if "=-" not in x)


def hanja_of(word: str) -> str:
    """韓文字的漢字（表裡有的）。純韓文固有詞、外來語回傳空字串。"""
    return HANJA.get(str(word or "").strip(), "")


def hanja_ok(word: str, hanja: str) -> bool:
    """模型說的漢字能不能信：跟韓文一樣長、每個字都是漢字（或原本就是韓文字）。"""
    word, hanja = str(word or ""), str(hanja or "").strip()
    if not hanja or len(hanja) != len(word) or hanja == word:
        return False
    return all("一" <= h <= "鿿" or h == w for h, w in zip(hanja, word)) and any("一" <= h <= "鿿" for h in hanja)


def hanja_prompt(word: str) -> str:
    return (f"Is the Korean word \"{word}\" a Sino-Korean word? If yes, answer with its Hanja only (same number of characters). "
            f"If it is a native Korean or foreign word, answer with -")


# ---- 對話回放練習（68）：從歷史挑一段真實對話，一句一句練聽力


def practice_sessions(rows: list[dict], gap: float, my_family: str, least: int = 4) -> list[dict]:
    """歷史 → 一段一段（最新的在前面），只留對方說的、不是我的語言的句子夠多的。"""
    from .recap import sessions

    out = []
    for lines in sessions(sorted(rows, key=lambda r: float(r.get("at") or 0)), gap):
        theirs = [r for r in lines if r.get("who") == "them" and r.get("original")
                  and str(r.get("lang") or "").split("-")[0] != my_family]
        if len(theirs) >= least:
            out.append({"id": str(theirs[0].get("k") or theirs[0].get("at")), "at": float(theirs[0].get("at") or 0), "count": len(theirs),
                        "lines": [{"original": r["original"], "translated": r.get("translated", ""), "lang": r.get("lang", "")} for r in theirs][:80]})
    out.reverse()
    return out[:20]


# ---- 每日複習提醒（57）：工具列通知的文字（介面語言）

_REMIND = {"zh-TW": "今天有 {n} 個字該複習了，打開 LiveLingo 的「學習」頁複習一下吧。",
           "zh-CN": "今天有 {n} 个字该复习了，打开 LiveLingo 的「学习」页复习一下吧。",
           "en": "{n} words are due for review today. Open the Learn page in LiveLingo.",
           "ko": "오늘 복습할 단어가 {n}개 있어요. LiveLingo의 학습 페이지에서 복습해 보세요.",
           "ja": "今日復習する単語が {n} 個あります。LiveLingo の「学習」ページで復習しましょう。",
           "vi": "Hôm nay có {n} từ cần ôn. Mở trang Học trong LiveLingo nhé.",
           "th": "วันนี้มีคำที่ต้องทบทวน {n} คำ เปิดหน้าเรียนใน LiveLingo ได้เลย",
           "id": "Ada {n} kata yang perlu diulang hari ini. Buka halaman Belajar di LiveLingo.",
           "es": "Hoy tienes {n} palabras para repasar. Abre la página Aprender de LiveLingo.",
           "pt": "Hoje há {n} palavras para revisar. Abra a página Aprender do LiveLingo.",
           "ru": "Сегодня нужно повторить слов: {n}. Откройте страницу «Учёба» в LiveLingo.",
           "de": "Heute sind {n} Wörter zur Wiederholung fällig. Öffne die Seite Lernen in LiveLingo.",
           "fr": "{n} mots sont à réviser aujourd'hui. Ouvrez la page Apprendre de LiveLingo."}


def remind_text(ui_lang: str, n: int) -> str:
    return (_REMIND.get(ui_lang) or _REMIND["en"]).format(n=n)
