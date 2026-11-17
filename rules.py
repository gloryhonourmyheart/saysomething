"""rules.py — 服务端因果规则（纯函数，不碰模型、不碰 UI）。

这一层只回答一个问题：**上一轮多少分、这一轮用了什么策略、锚点想给多少分，
最终允许落到多少分？**

规则是产品语义，不是模型自由心证：
- 要求 / 回避：可以维持，不能降（不能靠一句「要求」把刺说软）；
- 解释：最多降 1 格（解释是讲道理，不是道歉）；
- 共情：允许下降，但不能反弹（共情不是交换条件）。

`constrain_defense` 不负责选台词；最终台词必须由调用方按返回的档位重选，
保证「台词档位 == 最终防御分」这条红线。
"""
from __future__ import annotations

from typing import Final

import config

# 策略分组只用于可读性；真正的公式在 constrain_defense 里
_NO_DROP: Final = ("要求", "回避")
_EXPLAIN: Final = "解释"
_EMPATHY: Final = "共情"


def _clamp(score: int) -> int:
    return max(1, min(5, int(score)))


def constrain_defense(
    prev: int | None,
    strategy: str,
    proposed: int,
) -> tuple[int, str]:
    """把模型想给的 `proposed` 夹进服务端允许的因果区间。

    返回 `(final_score, reason)`；`reason` 直接写进 Turn.delta_reason，
    图表/报告可以拿它解释「为什么这一轮没降」。
    """
    proposed = _clamp(proposed or config.FALLBACK_DEFENSE)

    if prev is None or int(prev) <= 0:
        return proposed, f"首轮按锚点 {proposed} 分"

    prev = _clamp(int(prev))

    if strategy in _NO_DROP:
        final = max(prev, proposed)
        if final == prev:
            return final, f"{strategy}不降：上一轮 {prev} 分，维持 {final} 分"
        return final, f"{strategy}不降：从 {prev} 分回到 {final} 分"

    if strategy == _EXPLAIN:
        floor = max(1, prev - 1)
        final = max(floor, proposed)
        if final == prev:
            return final, f"解释最多降 1：上一轮 {prev} 分，维持 {final} 分"
        return final, f"解释最多降 1：从 {prev} 分只降到 {final} 分"

    if strategy == _EMPATHY:
        final = min(prev, proposed)
        if final == prev:
            return final, f"共情不反弹：上一轮 {prev} 分，维持 {final} 分"
        return final, f"共情允许下降：从 {prev} 分降到 {final} 分"

    # 理论上 validate_payload 已挡掉非法策略；这里只做兜底，不让流程崩
    final = _clamp(proposed)
    return final, f"未知策略 {strategy}：按锚点 {final} 分"


def phrase(text: str, limit: int = 18) -> str:
    """从用户这句话里截一个「关键话」，写进 DB 给图表标注用。"""
    flat = " ".join((text or "").split())
    return flat[:limit]
