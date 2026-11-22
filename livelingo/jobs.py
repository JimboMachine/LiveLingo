"""
翻譯的排隊。翻譯引擎一次只翻一句，所以順序很重要：
我自己打的、說的先翻（我在等著貼出去），再來是對方說的，然後是螢幕上讀到的，回譯排最後。同一種裡面先到的先翻。

螢幕上讀到的句子如果排了太久（聊天刷得比翻譯快），就不翻了：翻出來的時候那一行早就捲走了，還會拖慢後面新的句子。
使用者自己按「重翻」「改原文」的句子永遠不會被略過，而且跟「我自己的」一樣優先。
"""

import itertools
import queue
import time

MINE, HEARD, SCREEN, BACK = 0, 1, 2, 3
STALE = 20.0      # 螢幕上讀到的句子最多排隊幾秒


def rank(job: dict) -> int:
    """這個工作排第幾級（數字小的先翻）。"""
    if "back_of" in job:
        return BACK
    via = job.get("via")
    if via in ("typed", "spoken"):
        return MINE
    return SCREEN if via == "screen" else HEARD


class Jobs:
    def __init__(self, now=time.monotonic, stale: float = STALE):
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._order = itertools.count()
        self._now = now
        self.stale = stale

    def put(self, job: dict, asked: bool = False) -> None:
        """asked：使用者自己要求的（重翻、改原文）。排在最前面那一級，也不會因為等太久被略過。"""
        self._queue.put((MINE if asked else rank(job), next(self._order), self._now(), bool(asked), job))

    def get(self, timeout: float = 0.5) -> tuple[dict, bool]:
        """拿下一個要翻的。回傳（工作, 要不要略過）。沒有工作時丟出 queue.Empty。"""
        level, _, at, asked, job = self._queue.get(timeout=timeout)
        late = level == SCREEN and not asked and self._now() - at > self.stale
        return job, late

    def waiting(self) -> int:
        return self._queue.qsize()
