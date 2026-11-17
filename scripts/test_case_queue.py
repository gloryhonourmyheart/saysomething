"""scripts/test_case_queue.py — 左侧演示案例按钮的回归测试。

只测「点了按钮，发出去的必须是案例原文」，不调真实模型。
覆盖：三个案例逐个点击；输入框里已有残留文本时，案例仍然优先。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEST_DB = Path(tempfile.mkdtemp(prefix="sf-case-")) / "app.db"
os.environ["SAY_APP_DB"] = str(_TEST_DB)
os.environ["SAY_COPY_MODEL"] = "0"

from streamlit.testing.v1 import AppTest  # noqa: E402

import agent  # noqa: E402
import models  # noqa: E402
import scenes  # noqa: E402

FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  → {detail}" if detail else ""))
    if not cond:
        FAIL += 1


def no_exception(at: AppTest, label: str) -> None:
    if at.exception:
        for e in at.exception:
            print(f"  !! {label} 抛异常：{e.message}")
        check(f"{label} 无异常", False)


captured: list[str] = []
_orig_run_turn = agent.run_turn


def _fake(text, history=(), scene=None):
    captured.append(text)
    return models.Turn(
        user_text=text,
        reply_text="你嫌我老了是吧？",
        strategy="要求",
        defense_score=5,
        evidence="tier_5 锚点 1",
        anchor=1,
        reaction="暴怒反击",
    )


agent.run_turn = _fake
try:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    no_exception(at, "首屏")

    expected = [case[1] for case in scenes.get("aging").cases]
    check("三个案例都有原文", len(expected) == 3, str(expected))

    for i, want in enumerate(expected, 1):
        at.button(key=f"case{i}_aging").click()
        at.run()
        no_exception(at, f"点击案例 {i}")
        got = at.session_state["session"].turns[-1].user_text if at.session_state["session"].turns else ""
        check(f"案例 {i} 发的是原文", got == want, repr(got))
        check(f"案例 {i} 没有方括号占位", "【" not in got and "】" not in got, repr(got))
        at.button(key="reset").click()
        at.run()

    # 回归：输入框里有浏览器自动填充/残留时，点案例仍然发案例原文。
    at.chat_input[0].set_value("浏览器残留的怪句子")
    at.button(key="case1_aging").click()
    at.run()
    no_exception(at, "输入框有残留时点案例")
    got = at.session_state["session"].turns[-1].user_text if at.session_state["session"].turns else ""
    check("案例优先于输入框残留", got == expected[0], repr(got))
finally:
    agent.run_turn = _orig_run_turn

print(f"\n{'全部通过' if FAIL == 0 else f'{FAIL} 项失败'}")
sys.exit(1 if FAIL else 0)
