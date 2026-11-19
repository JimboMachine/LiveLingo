"""把韓文轉成羅馬拼音（國立國語院的標準拼法，照實際發音處理連音），讓不會讀韓文字的人也能照著唸。"""

_INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_VOWELS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu",
           "eu", "ui", "i"]
# 收尾子音拆成一個個子音（用「聲母編號」表示，方便連音時搬到下一個字）
#   聲母編號：ㄱ0 ㄲ1 ㄴ2 ㄷ3 ㄸ4 ㄹ5 ㅁ6 ㅂ7 ㅃ8 ㅅ9 ㅆ10 ㅇ11 ㅈ12 ㅉ13 ㅊ14 ㅋ15 ㅌ16 ㅍ17 ㅎ18
_FINALS = [[], [0], [1], [0, 9], [2], [2, 12], [2, 18], [3], [5], [5, 0], [5, 6], [5, 7], [5, 9], [5, 16], [5, 17],
           [5, 18], [6], [7], [7, 9], [9], [10], [11], [12], [14], [15], [16], [17], [18]]
# 收尾時實際發的音
_CODA = {0: "k", 1: "k", 15: "k", 2: "n", 3: "t", 9: "t", 10: "t", 12: "t", 14: "t", 16: "t", 18: "t",
         5: "l", 6: "m", 7: "p", 17: "p", 11: "ng"}
_ASPIRATE = {0: 15, 3: 16, 7: 17, 12: 14}  # ㄱㄷㅂㅈ 碰到 ㅎ 變成 ㅋㅌㅍㅊ
_IEUNG, _HIEUT, _RIEUL, _NIEUN, _MIEUM = 11, 18, 5, 2, 6


def _is_syllable(ch: str) -> bool:
    return 0xAC00 <= ord(ch) <= 0xD7A3


def _coda_sound(final: list[int]) -> str:
    if not final:
        return ""
    if len(final) == 2:
        # 兩個子音的收尾只發其中一個：ㄺ ㄻ ㄿ 發後面的，其餘發前面的
        keep = final[1] if final in ([5, 0], [5, 6], [5, 17]) else final[0]
        return _CODA[keep]
    return _CODA[final[0]]


def _romanize_word(word: str) -> str:
    syl = []
    for ch in word:
        code = ord(ch) - 0xAC00
        syl.append([code // 588, (code % 588) // 28, list(_FINALS[code % 28])])
    # 第一步：連音、ㅎ 的變化（收尾子音搬到下一個字的開頭）
    for i in range(len(syl) - 1):
        cur, nxt = syl[i], syl[i + 1]
        final = cur[2]
        if not final:
            continue
        if nxt[0] == _IEUNG and final != [_IEUNG]:
            if final[-1] == _HIEUT:          # 좋아 → 조아：ㅎ 不發音
                final.pop()
                if final:
                    nxt[0] = final.pop()
            else:
                moved = final.pop()
                if nxt[1] == 20 and moved == 3:      # 굳이 → 구지
                    moved = 12
                elif nxt[1] == 20 and moved == 16:   # 같이 → 가치
                    moved = 14
                nxt[0] = moved
        elif nxt[0] == _HIEUT and final[-1] in _ASPIRATE:     # 축하 → 추카
            nxt[0] = _ASPIRATE[final.pop()]
        elif final[-1] == _HIEUT and nxt[0] in _ASPIRATE:     # 좋다 → 조타
            final.pop()
            nxt[0] = _ASPIRATE[nxt[0]]
    # 第二步：轉成拼音，並處理鼻音化、ㄹ 的變化
    out = []
    for i, (ini, vow, final) in enumerate(syl):
        coda = _coda_sound(final)
        nxt_ini = syl[i + 1][0] if i + 1 < len(syl) else None
        if coda and nxt_ini is not None:
            if nxt_ini in (_NIEUN, _MIEUM):
                coda = {"k": "ng", "t": "n", "p": "m"}.get(coda, coda)   # 한국말 → 한궁말
                if coda == "l" and nxt_ini == _NIEUN:
                    syl[i + 1][0] = _RIEUL                              # 설날 → 설랄
            elif nxt_ini == _RIEUL:
                if coda == "n":
                    coda = "l"                                          # 신라 → 실라
                elif coda != "l":
                    coda = {"k": "ng", "t": "n", "p": "m"}.get(coda, coda)
                    syl[i + 1][0] = _NIEUN                              # 심리 → 심니
        prev_l = bool(out) and out[-1].endswith("l")
        head = "l" if (ini == _RIEUL and prev_l) else _INITIALS[ini]   # ㄹㄹ 寫成 ll
        out.append(head + _VOWELS[vow] + coda)
    return "".join(out)


def romanize(text: str) -> str:
    """韓文字轉成拼音，其他文字（英文、數字、標點）原樣保留。"""
    out, word = [], ""
    for ch in text:
        if _is_syllable(ch):
            word += ch
        else:
            if word:
                out.append(_romanize_word(word))
                word = ""
            out.append(ch)
    if word:
        out.append(_romanize_word(word))
    return "".join(out)
