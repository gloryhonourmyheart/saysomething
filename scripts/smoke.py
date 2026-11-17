"""scripts/smoke.py — 真实 API 冒烟：三个演示案例各跑一遍，打印期望 vs 实际。

用法：填好 .env 后
    python scripts/smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
import config  # noqa: E402
from models import Session  # noqa: E402

CASES = [
    {
        "name": "案例一 · 刺竖起来",
        "text": "妈，浴室必须装扶手，这个没得商量。",
        "expect": ("要求", (4, 5)),
    },
    {
        "name": "案例二 · 装上泡沫（接在案例一后面）",
        "text": "妈，我知道你觉得自己能行。我上次看到浴室地上有水，是真的怕你滑。能不能先只装一个扶手，你不喜欢随时拆。",
        "expect": ("共情", (1, 2)),
    },
    {
        "name": "案例三 · 边界（拿别人家举例）",
        "text": "妈，我同事他爸就是浴室滑倒摔骨折的。",
        "expect": ("解释", (3, 3)),
    },
]

ok, status = agent.health_check()
print(f"状态：{status}")
if not ok:
    print("\n!! 没有可用的 API Key。在项目根目录建 .env：")
    print("   DEEPSEEK_API_KEY=sk-xxxx")
    print("   DEEPSEEK_BASE_URL=https://api.deepseek.com")
    print("   DEEPSEEK_MODEL=deepseek-flash")
    sys.exit(2)

session = Session()
bad = 0

for case in CASES:
    expect_strategy, (low, high) = case["expect"]
    print(f"\n{'=' * 56}\n{case['name']}\n她说：{case['text']}")
    started = time.perf_counter()
    turn = agent.run_turn(case["text"], session.turns)
    wall = (time.perf_counter() - started) * 1000

    print(f"妈说：{turn.reply_text}")
    print(
        f"策略：{turn.strategy}（期望 {expect_strategy}）· "
        f"防御：{turn.defense_score}/5（期望 {low}-{high}）· "
        f"{turn.latency_ms}ms（墙钟 {wall:.0f}ms）"
    )
    if turn.evidence:
        print(f"依据：{turn.evidence}")
    if turn.degraded:
        print(f"!! 降级：{turn.degraded_reason}")
        bad += 1
    if turn.strategy != expect_strategy:
        print(f"!! 策略不符，期望 {expect_strategy}")
        bad += 1
    if not low <= turn.defense_score <= high:
        print(f"!! 防御分超区间（夹分后仍不符，说明夹分没生效）")
        bad += 1
    session.add(turn)

print(f"\n{'=' * 56}\n对比面板文案：{agent.summarize(session.turns[:2])}")
print(f"回访问题：{agent.build_followup(session.turns[:2])}")
print(f"\n{'冒烟通过' if bad == 0 else f'{bad} 处不符合预期'}  ·  model={config.MODEL}")
sys.exit(1 if bad else 0)
