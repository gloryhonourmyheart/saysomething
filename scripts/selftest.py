"""scripts/selftest.py — 离线自检：不烧 token，验证解析、夹分、降级、文案。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
import config  # noqa: E402
from models import Session, Turn  # noqa: E402

FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  → {detail}" if detail else ""))
    if not cond:
        FAIL += 1


print(f"[1] health_check: {agent.health_check()}")

print("[2] 离线降级（临时清空 Key，避免真调 API）")
_saved_key = config.API_KEY
config.API_KEY = ""
try:
    check("available() 为假", not agent.available())
    turn = agent.run_turn("妈，浴室必须装扶手，这个没得商量。")
finally:
    config.API_KEY = _saved_key
check("degraded=True", turn.degraded, turn.degraded_reason)
check("defense=5（tier5 兜底）", turn.defense_score == 5, str(turn.defense_score))
check("reply 非空", bool(turn.reply_text), turn.reply_text)
check("reaction 跟台词走", bool(turn.reaction), turn.reaction)

print("[3] parse_payload 三种脏输出")
raw1 = '{"reply":"你嫌我老了是吧？","strategy":"要求","defense":4,"evidence":"必须·没商量"}'
raw2 = '好的，我来演：\n```json\n' + raw1 + "\n```"
raw3 = "她说了：" + raw1 + " 就这样。"
for i, raw in enumerate([raw1, raw2, raw3], 1):
    data = agent.parse_payload(raw)
    check(f"case{i} 能解析", data is not None and data.get("strategy") == "要求")

print("[4] validate_payload 只认 strategy + anchor_id")
got = agent.validate_payload(
    {"reply": "模型瞎写", "strategy": "要求", "defense": 1, "anchor_id": 7, "evidence": "tier_1"}
)
check("返回 (strategy, anchor)", got == ("要求", 7), str(got))
check("模型 defense 被忽略", agent.validate_payload(
    {"strategy": "共情", "anchor_id": 2, "defense": 5}
) == ("共情", 2))
check("缺 anchor → 0", agent.validate_payload({"strategy": "要求"}) == ("要求", 0))
check("非法 strategy 被拒", agent.validate_payload({"strategy": "撒娇", "anchor_id": 1}) is None)

print("[5] 两轮对比 + 总结 + 回访")
first = Turn("妈，浴室必须装扶手，这个没得商量。", "你嫌我老了是吧？", "要求", 4)
second = Turn("妈，我知道你觉得自己能行，但我怕你滑。", "就你事多……装就装吧。", "共情", 2)
session = Session()
session.add(first)
session.add(second)
print("   " + agent.summarize(session.turns))
print("   " + agent.build_followup(session.turns))
check("delta=-2", session.defense_delta == -2, str(session.defense_delta))
check("finished", session.finished)
check("is_soft", second.is_soft and not first.is_soft)

print("[6] Turn 序列化往返")
back = Turn.from_dict(second.to_dict())
check("roundtrip", back.reply_text == second.reply_text and back.defense_score == 2)

print("[7] build_messages 只带最近两轮")
msgs = agent.build_messages("第三句", [first, second])
check("条数=1+4+1", len(msgs) == 6, str(len(msgs)))
check("system 在首位", msgs[0]["role"] == "system")

print("[8] 台词锚点契约")
got = agent.validate_payload({"reply": "行吧", "strategy": "要求", "defense": 5, "anchor_id": 1})
check("锚点编号照收", got == ("要求", 1), str(got))
check("Turn.anchor_tier 可读", Turn("a", "b", "共情", 1, "tier_1 锚点").anchor_tier == 1)
check("anchor_tier 缺省回退", Turn("a", "b", "共情", 2, "").anchor_tier == 2)

print("[9] 防御曲线")
import charts  # noqa: E402

check("单轮不出图", charts.defense_curve([first]) is None)
check("空数据不出图", charts.defense_curve([]) is None)
fig = charts.defense_curve([first, second])
check("两轮出图", fig is not None)
if fig is not None:
    check("端点值 [4,2]", list(fig.data[1].y) == [4, 2], str(list(fig.data[1].y)))
    check("数据标签", list(fig.data[1].text) == ["4", "2"], str(list(fig.data[1].text)))
    check("纵轴 0-6", tuple(fig.layout.yaxis.range) == (0, 6), str(fig.layout.yaxis.range))
    check("横轴两条", len(fig.data[1].x) == 2)

# 降幅和回升各自一个空心圈，颜色与文案不能串。
up_fig = charts.defense_curve(
    [
        Turn("a", "b", "要求", 5),
        Turn("c", "d", "要求", 2, key_phrase="浴室扶手"),
        Turn("e", "f", "要求", 4, key_phrase="你怎么又来了"),
    ]
)
if up_fig is not None:
    anns = [a.text for a in up_fig.layout.annotations]
    check("降幅标关键软化句", any("关键软化句" in t for t in anns), str(anns))
    check("降幅用 key_phrase", any("浴室扶手" in t for t in anns), str(anns))
    check("回升标再次施压", any("再次施压" in t for t in anns), str(anns))
    check("回升用 key_phrase", any("你怎么又来了" in t for t in anns), str(anns))
    check("降幅圈用绿色", any("2F6B4F" in str(a.font.color) for a in up_fig.layout.annotations))
    check("回升圈用红色", any("B8615B" in str(a.font.color) for a in up_fig.layout.annotations))
    check("两条标注各自一个圈", len(up_fig.data) == 4, f"{len(up_fig.data)} traces")

print("[10] 教练点评")
import coach  # noqa: E402

text = coach.critique([first, second])
print("   " + text)
check("命中要求→共情规则", text.startswith("你从「要求」"), text[:12])
check("≤50 字", len(text) <= 50, f"{len(text)} 字")
check("单轮给引导", coach.critique([first]).startswith("刺竖起来了"))
check("无数据不崩", coach.critique([]) == "先说一句你一直没敢说的话。")
rising = Turn("x", "y", "解释", 5)
check("上升时改口", "更深" in coach.critique([second, rising]))

print(f"\n{'全部通过' if FAIL == 0 else f'{FAIL} 项失败'}  ·  model={config.MODEL}")
sys.exit(1 if FAIL else 0)
