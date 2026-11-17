"""coach.py — 教练点评：纯规则，不调 LLM。

为什么不用模型：① 省一次调用，现场更快；② 规则输出稳定，答辩时不会突然说怪话；
③ 「因为你的具体改动（策略 X→Y、分数 4→2）所以结论是这样」这条因果能讲清楚。
"""
from __future__ import annotations

from typing import Sequence

MAX_LEN = 50


def critique(turns: Sequence, speaker: str = "她") -> str:
    """两轮跑完后的点评，控制在 50 字内。数据不足时返回引导语。"""
    turns = list(turns or [])
    if not turns:
        return "先说一句你一直没敢说的话。"

    first = turns[0]
    if len(turns) == 1:
        return f"刺竖起来了（{first.defense_score} 分）。改一版：把「你该」换成「我怕」。"

    last = turns[-1]
    delta = first.defense_score - last.defense_score
    fs, ls = first.strategy, last.strategy

    # 教科书路径：要求 → 共情，且防御下降
    if fs == "要求" and ls == "共情" and delta > 0:
        return "你从「要求」转向了「共情」，有效降低了对方防御。这就是「装上泡沫」的瞬间。"

    if ls == "共情" and delta > 0:
        return f"从「{fs}」到「共情」：先承认对方能行，再说你怕什么，刺就软了。"

    if delta > 0 and ls == "解释":
        return "解释再多也是「你该听我的」。对方听见了道理，没听见你的担心。"

    if delta < 0:
        return "这一版扎得更深：越解释越像下命令。退回去，先认对方的本事。"

    if delta == 0:
        if fs == ls == "要求":
            return "两次都在替对方做主。先说「你当然能行」，再提要求。"
        return f"说法换了，刺没动。{speaker}要的不是理由，是你真的听懂了。"

    return f"「{fs}」→「{ls}」，防御 {first.defense_score}→{last.defense_score}。"


def verdict(turns: Sequence, speaker: str = "她") -> tuple[str, str]:
    """给 UI 用：(文案, down/up/flat)。"""
    turns = list(turns or [])
    text = critique(turns, speaker)
    if len(turns) < 2:
        return text, "flat"
    delta = turns[0].defense_score - turns[-1].defense_score
    return text, ("down" if delta > 0 else ("up" if delta < 0 else "flat"))
