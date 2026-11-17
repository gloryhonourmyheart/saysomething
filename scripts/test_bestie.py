"""test_bestie.py — 挚友冷战场景的专项测试（不联网）。

覆盖：场景注册、下拉顺序、Prompt 是否带专属规矩、3 分锁定规则的三类输入、
其它场景不受影响、summarize/回访话术用的是场景化称呼。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
import scenes  # noqa: E402
from models import Session, Turn  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}{('  ' + extra) if extra else ''}")


def main() -> int:
    bestie = scenes.get("bestie")

    # ── 1. 注册与配置 ──────────────────────────────────────────
    check("场景能取到", bestie.key == "bestie")
    check("下拉框包含挚友", "bestie" in scenes.options(), str(scenes.options()))
    check("默认场景没变", scenes.DEFAULT_KEY == "aging" and scenes.options()[0] == "aging")
    check("label 落库名正确", bestie.label == "挚友冷战" and scenes.key_of("挚友冷战") == "bestie")
    check("介绍文案是那句台词", bestie.scenario.startswith("十年友情，不该死于不会说话"))
    check("五档台词齐全", sorted(bestie.lines) == [1, 2, 3, 4, 5])
    check("每档至少两句", all(len(v) >= 2 for v in bestie.lines.values()))
    check("三个案例齐全", len(bestie.cases) == 3)

    # ── 2. Prompt ─────────────────────────────────────────────
    prompt = scenes.render_system_prompt(bestie)
    check("Prompt 含额外规矩段", "这个场景的额外规矩" in prompt)
    check("Prompt 要求只求和好时走 tier_3", "tier_3" in prompt and "不许用 tier_4/5" in prompt)
    check("Prompt 用了挚友人设", "十年" in prompt)
    check("Prompt 有缓存且一致", scenes.render_system_prompt(bestie) == prompt)

    # ── 3. 三分锁定规则 ─────────────────────────────────────────
    guard = bestie.guard
    check("guard 已挂上", callable(guard))

    score, note = guard("我们和好吧", "要求", 5)
    check("只求和好：5 分压到 3", score == 3, f"got {score}")
    check("只求和好：带规则说明", note.startswith("只求和好不算交底"), note)

    score, note = guard("我们和好吧", "共情", 2)
    check("只求和好：2 分抬到 3（卡住而不是松口）", score == 3, f"got {score}")
    check("只求和好：即使模型给 1 也卡 3", guard("咱俩和好吧", "共情", 1)[0] == 3)

    full = "那天我说「你随便」是我在赌气，回去之后我一直挺难受，我想跟你好好说十分钟，行吗？"
    score, note = guard(full, "共情", 2)
    check("事实+感受+需求齐了：放行模型分", score == 2 and note == "", f"got {score}, {note!r}")

    score, note = guard("那天我说的话确实过了，我想跟你聊聊，我们和好吧", "共情", 2)
    check("缺「感受」仍然卡 3", score == 3, f"got {score}")

    score, note = guard("你至于吗？不就一件小事", "要求", 5)
    check("不求和好：不动分数", score == 5 and note == "", f"got {score}, {note!r}")

    # ── 4. 其它场景不受影响 ─────────────────────────────────────
    for key in ("aging", "money", "salary"):
        scene = scenes.get(key)
        check(f"{key} 没有 guard", scene.guard is None)
    aging = scenes.get("aging")
    check("适老化仍是「嫌你老」提示", aging.soft_hint == "我不是嫌你老")
    check("挚友用自己的提示", bestie.soft_hint == "我不是来争对错的")

    # ── 5. 文案生成用的是场景称呼 ───────────────────────────────
    turns = [
        Turn(user_text="你至于吗", reply_text="算了吧", strategy="要求", defense_score=5, evidence="tier_5"),
        Turn(user_text="我们和好吧", reply_text="随便你吧", strategy="共情", defense_score=3, evidence="tier_3"),
    ]
    summary = agent.summarize(turns, bestie)
    check("summarize 用「他」而不是「她」", "他" in summary and "她" not in summary, summary)
    check("summarize 用挚友的软话提示", "我不是来争对错的" in summary, summary)

    follow = agent.build_followup(turns, bestie)
    check("回访用挚友的称呼", "他" in follow and "三天后" in follow, follow)

    # ── 6. 会话回放不认场景也不崩 ───────────────────────────────
    session = Session.from_rows(
        [
            {
                "user_text": "我们和好吧",
                "reply": "算了吧",
                "strategy": "共情",
                "defense": 3,
                "evidence": "tier_3",
                "latency_ms": 0,
                "created_at": "",
            }
        ],
        session_id=7,
        scene_key="bestie",
    )
    check("Session 记住挚友场景", session.scene.label == "挚友冷战")
    check("Session 回放出 1 轮", len(session.turns) == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
