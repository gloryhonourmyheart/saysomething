"""models.py — 数据结构。

用标准库 dataclass，不引入 pydantic，保证在任何 Python 3.10+ 上都能跑。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import config

_TIER_RE = re.compile(r"tier[\s_\-]?([1-5])", re.I)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Turn:
    """一轮完整演练：她说了什么、妈回了什么、策略判定、防御分。"""

    user_text: str
    reply_text: str
    strategy: str = config.FALLBACK_STRATEGY
    defense_score: int = config.FALLBACK_DEFENSE
    evidence: str = ""
    # 台词锚点带回来的「人味」：动作、潜台词、怕什么。由本地台词库按编号取，
    # 不是模型编的——评委问「这句潜台词哪来的」，答案是「台词库第 12 条」。
    stage: str = ""
    subtext: str = ""
    fear: str = ""
    anchor: int = 0                 # 最终台词编号（1 起，0 = 没给）
    reaction: str = ""              # 最终台词固定的反应标签：暴怒反击 / 明显防御…
    # 因果链：这一轮为什么升/降/不降，以及触发变化的那句关键话。
    delta_reason: str = ""
    key_phrase: str = ""
    # 这一轮生成的小抄（hints.py 的产物）。只在这一轮提交时生成一次，
    # 渲染层只读，刷新页面 / 切场景回来都不会变。
    copy_text: str = ""
    degraded: bool = False          # True = 走了兜底，不是模型正常输出
    degraded_reason: str = ""
    latency_ms: int = 0
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.strategy not in config.STRATEGIES:
            self.strategy = config.FALLBACK_STRATEGY
        self.defense_score = max(1, min(5, int(self.defense_score or config.FALLBACK_DEFENSE)))

    @property
    def is_soft(self) -> bool:
        """刺软下去了吗（≤2 才算软）。"""
        return self.defense_score <= 2

    @property
    def defense_label(self) -> str:
        return config.DEFENSE_LABELS.get(self.defense_score, "")

    @property
    def anchor_tier(self) -> int:
        """这一轮实际使用的台词档位（从 evidence 里读，读不到就用防御分）。"""
        match = _TIER_RE.search(self.evidence or "")
        return int(match.group(1)) if match else self.defense_score

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Turn":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Session:
    """一次演练（最多两轮）。

    session_state 里放的是「当前正在练的这一场」，它同时也是数据库里的一行：
    id 为空 = 还没落库（第一轮发出时创建），id 有值 = 已经在 sessions 表里。
    """

    turns: list[Turn] = field(default_factory=list)
    id: int | None = None
    scene_key: str = "aging"
    followup_when: str = ""
    followup_question: str = ""

    @property
    def scene(self):
        import scenes  # 延迟导入，避免模块级循环

        return scenes.get(self.scene_key)

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)

    @classmethod
    def from_rows(cls, rows: list[dict], *, session_id: int | None, scene_key: str) -> "Session":
        """从数据库行恢复一场（历史记录点击用）。脏数据一律跳过，不抛异常。"""
        session = cls(id=session_id, scene_key=scene_key)
        for row in rows or []:
            try:
                user_text = str(row.get("user_text", "") or "")
                reply = str(row.get("reply", "") or "")
                if not user_text and not reply:  # 空行直接跳过，不污染回放
                    continue
                session.add(
                    Turn(
                        user_text=user_text,
                        reply_text=reply,
                        strategy=str(row.get("strategy", "")),
                        defense_score=int(row.get("defense", 3) or 3),
                        evidence=str(row.get("evidence", "") or ""),
                        stage=str(row.get("stage", "") or ""),
                        subtext=str(row.get("subtext", "") or ""),
                        fear=str(row.get("fear", "") or ""),
                        anchor=int(row.get("anchor", 0) or 0),
                        reaction=str(row.get("reaction", "") or ""),
                        delta_reason=str(row.get("delta_reason", "") or ""),
                        key_phrase=str(row.get("key_phrase", "") or ""),
                        copy_text=str(row.get("copy", "") or ""),
                        latency_ms=int(row.get("latency_ms", 0) or 0),
                        created_at=str(row.get("created_at", "") or ""),
                    )
                )
            except Exception:  # 单行坏了不该毁掉整场回放
                continue
        return session

    @property
    def latest(self) -> Turn | None:
        return self.turns[-1] if self.turns else None

    @property
    def first(self) -> Turn | None:
        return self.turns[0] if self.turns else None

    @property
    def round_index(self) -> int:
        """下一轮是第几轮。"""
        return len(self.turns) + 1

    @property
    def finished(self) -> bool:
        return len(self.turns) >= 2

    @property
    def defense_delta(self) -> int:
        """两轮防御分差（负数=下降）。"""
        if len(self.turns) < 2:
            return 0
        # 无限聊天之后，比较的是「第一句」和「最新一句」，不再写死第 1、2 轮
        return self.turns[-1].defense_score - self.turns[0].defense_score

    def __post_init__(self) -> None:
        # 场景 key 非法（脏数据 / 老版本存档）时静默回退默认场景，不让页面崩
        try:
            self.scene_key = self.scene.key
        except Exception:
            self.scene_key = "aging"

    def reset(self) -> None:
        self.turns.clear()
        self.id = None
        self.followup_when = ""
        self.followup_question = ""


def new_session(scene_key: str = "aging") -> Session:
    """构造一场新演练。

    带一层兼容：如果进程里缓存的是旧版 models 模块（Session 还没有 scene_key 参数，
    典型场景是 Streamlit 热重载只重跑主脚本、不重导模块），这里退化成默认场景，
    而不是把 TypeError 甩到页面上。现场演示最怕的就是这种红字。
    """
    try:
        return Session(scene_key=scene_key)
    except TypeError:
        return Session()
