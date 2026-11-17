"""insight.py — 个人沟通画像：统计 + 规则洞察（纯代码，不调 LLM）。

为什么不上模型：
1. 统计口径必须可解释。评委问「60% 怎么来的」，你能当场指着 12 句说 7 句是要求；
   模型给的百分比，你答不上来。
2. 结论要稳定。同一个库跑两次，画像不能变。
3. 零成本、零延迟，侧栏每次 rerun 都能重算。

「洞察」的写法是规则短路：命中哪条说哪条，最多四句，绝不堆砌。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

import config


@dataclass
class Profile:
    session_count: int = 0
    total_turns: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    pct: dict[str, int] = field(default_factory=dict)
    top: str = ""
    reactions: dict[str, int] = field(default_factory=dict)  # 高防御之后，下一步用了什么
    high_def_events: int = 0
    avg_drop: float | None = None
    lines: list[str] = field(default_factory=list)
    empty: bool = True


def _pct(part: int, whole: int) -> int:
    return int(round(part * 100 / whole)) if whole else 0


def build_profile(rows: Sequence[dict]) -> Profile:
    """rows 来自 db.all_turns()，已按 (session_id, round) 排序。"""
    rows = list(rows or [])
    if not rows:
        return Profile(
            empty=True,
            lines=["还没有数据。跑两轮演练，这里就会长出你的沟通画像。"],
        )

    counts: Counter[str] = Counter()
    sessions: dict[int, list[dict]] = {}
    for row in rows:
        strategy = str(row.get("strategy", "")).strip()
        if strategy in config.STRATEGIES:
            counts[strategy] += 1
        sessions.setdefault(int(row.get("session_id", 0)), []).append(row)

    total = sum(counts.values())
    pct = {s: _pct(counts.get(s, 0), total) for s in config.STRATEGIES}

    # 占比重、并列时按固定顺序，保证每次跑出来一致
    top = max(config.STRATEGIES, key=lambda s: (counts.get(s, 0), -config.STRATEGIES.index(s)))

    # 「被扎了一下之后你怎么反应」：上一轮防御 ≥4，看下一轮用了什么策略
    reactions: Counter[str] = Counter()
    drops: list[int] = []
    for turns in sessions.values():
        for prev, cur in zip(turns, turns[1:]):
            if int(prev.get("defense", 0) or 0) >= 4:
                strategy = str(cur.get("strategy", "")).strip()
                if strategy in config.STRATEGIES:
                    reactions[strategy] += 1
        if len(turns) >= 2:
            drops.append(int(turns[0].get("defense", 0)) - int(turns[-1].get("defense", 0)))

    avg_drop = round(sum(drops) / len(drops), 1) if drops else None

    profile = Profile(
        session_count=len(sessions),
        total_turns=total,
        counts=dict(counts),
        pct=pct,
        top=top,
        reactions=dict(reactions),
        high_def_events=sum(reactions.values()),
        avg_drop=avg_drop,
        empty=False,
    )
    profile.lines = _lines(profile)
    return profile


def _lines(profile: Profile) -> list[str]:
    lines: list[str] = []

    # 1) 最常用的策略 + 一句性格化翻译
    flavor = {
        "要求": "话一出口，先替对方做了主。",
        "解释": "你更习惯讲道理、摆事实。",
        "共情": "你习惯先接住对方，再提要求。",
        "回避": "你常常绕开正面冲突。",
    }.get(profile.top, "")
    lines.append(
        f"你最爱用【{profile.top}】策略（占 {profile.pct.get(profile.top, 0)}%，"
        f"共 {profile.counts.get(profile.top, 0)} 句）。{flavor}"
    )

    # 2) 高防御之后的下意识反应
    if profile.high_def_events:
        reacted = max(profile.reactions.items(), key=lambda kv: kv[1])[0]
        times = profile.reactions[reacted]
        line = (
            f"当对方防御 ≥4 时，你下一步更常转向【{reacted}】"
            f"（{profile.high_def_events} 次中的 {times} 次）。"
        )
        if reacted == "回避" and times >= 2:
            line += "这是最容易被读成「你不在乎」的一步。"
        elif reacted == "解释" and times >= 2:
            line += "越解释，对方越听见「你该听我的」。"
        lines.append(line)

    # 3) 一句可执行的建议（只给一条，给多了没人做）
    if profile.pct.get("共情", 0) < 25:
        lines.append("建议多练习「先共情，再提需求」：先承认对方能行，再说你怕什么。")
    elif profile.pct.get("要求", 0) >= 50:
        lines.append("建议把「必须」「没得商量」换成「我怕」——同样的意思，对方不用竖起刺。")
    elif profile.high_def_events and max(profile.reactions, key=lambda k: profile.reactions[k]) == "解释":
        lines.append("你已经会后退了，但退成了讲道理。下一次试着只说感受，不摆证据。")
    else:
        lines.append("节奏已经对了：先接住对方，再提要求，最后给一条退路。")

    # 4) 战绩（有完整两轮才说）
    if profile.avg_drop is not None:
        if profile.avg_drop > 0:
            lines.append(f"{profile.session_count} 场演练里，防御分平均下降 {profile.avg_drop:.1f} 分。")
        elif profile.avg_drop < 0:
            lines.append(f"{profile.session_count} 场演练里，防御分平均反而上升 {abs(profile.avg_drop):.1f} 分。")
        else:
            lines.append(f"{profile.session_count} 场演练里，防御分平均没有变化——说法变了，对方没动。")
    return lines
