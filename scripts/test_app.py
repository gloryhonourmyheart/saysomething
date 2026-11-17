"""scripts/test_app.py — 用 Streamlit 官方 AppTest 端到端跑一遍 UI（会真实调 2 次 API）。

覆盖：首屏 → 两轮演练 → 曲线/点评/总结 → 导出报告 → 回访 → 历史恢复 → 切场景 → 重开。

注意：测试用的是临时数据库（SAY_APP_DB），不会污染 data/app.db 里的演示记录。

    python scripts/test_app.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import db 之前设置：db 在导入时读取这个环境变量
_TEST_DB = Path(tempfile.mkdtemp(prefix="sf-app-")) / "app.db"
os.environ["SAY_APP_DB"] = str(_TEST_DB)
# AppTest 里不让小抄层额外打模型：主对话已经真实调用 2 次，L2 再调一次既慢又容易受网络影响。
os.environ["SAY_COPY_MODEL"] = "0"

from streamlit.testing.v1 import AppTest  # noqa: E402

import db  # noqa: E402
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


print("[1] 首次渲染")
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
at.run()
no_exception(at, "首屏")
check("标题存在", any("刺与泡沫" in m.value for m in at.markdown), f"{len(at.markdown)} 个 markdown 块")
check("输入框存在", len(at.chat_input) == 1)
check("输入框可用", not at.chat_input[0].disabled)
check("首屏有引导气泡", any("还没开口" in m.value for m in at.markdown), "")
check("场景下拉框存在", len(at.selectbox) == 1)
check("默认场景=适老化", at.session_state["session"].scene.label == "适老化改造")
check("空库时仍有画像入口", any("沟通画像" in b.label for b in at.button))

print("[2] 第一轮：要求 → 防御 4-5")
at.chat_input[0].set_value("妈，浴室必须装扶手，这个没得商量。")
at.run()
no_exception(at, "第一轮发送")
check("气泡里出现这句话", any("必须装扶手" in m.value for m in at.markdown), "")
session = at.session_state["session"]
check("记录了 1 轮", len(session.turns) == 1, str(len(session.turns)))
first = session.turns[0]
check("策略=要求", first.strategy == "要求", first.strategy)
check("防御 4-5", 4 <= first.defense_score <= 5, f"{first.defense_score}/5")
check("未降级", not first.degraded, first.degraded_reason)
check("首轮已生成小抄", bool(first.copy_text), first.copy_text)
check("小抄没有引号", not any(q in first.copy_text for q in "「」[]"), first.copy_text)
print(f"      妈说：{first.reply_text}")

print("[3] 落库：一轮就进 sessions + turns")
check("session.id 已生成", session.id is not None, str(session.id))
check("库里 1 场 1 轮", db.counts() == (1, 1), str(db.counts()))
row = db.get_session(session.id) if session.id else None
check("回放拿到原话", bool(row) and row["turns"][0]["user_text"] == "妈，浴室必须装扶手，这个没得商量。")

print("[4] 第二轮：共情 → 防御 1-2 + 对比面板")
at.chat_input[0].set_value(
    "妈，我知道你觉得自己能行。我上次看到浴室地上有水，是真的怕你滑。能不能先只装一个扶手，你不喜欢随时拆。"
)
at.run()
no_exception(at, "第二轮后")
session = at.session_state["session"]
check("记录了 2 轮", len(session.turns) == 2, str(len(session.turns)))
last = session.turns[1]
check("策略=共情", last.strategy == "共情", last.strategy)
check("防御 1-2", 1 <= last.defense_score <= 2, f"{last.defense_score}/5")
check("防御下降≥2", session.defense_delta <= -2, str(session.defense_delta))
print(f"      妈说：{last.reply_text}")
check("库里 2 轮", db.counts() == (1, 2), str(db.counts()))
markdown_text = " ".join(m.value for m in at.markdown)
check("右栏常驻两张图（曲线 + 饼图）", len(at.get("plotly_chart")) == 2, f"{len(at.get('plotly_chart'))} 个")
check("两轮后仍能继续聊（F5 无限聊天）", not at.chat_input[0].disabled)

print("[4b] 弹窗式沟通画像")
at.button(key="dash").click()
at.run()
no_exception(at, "打开弹窗后")
dialog_md = " ".join(m.value for m in at.markdown)
charts_in_dialog = at.get("plotly_chart")
check("弹窗里再加两条图（曲线 + 饼图）", len(charts_in_dialog) == 4, f"{len(charts_in_dialog)} 个")
check("弹窗标题区", "沟通体检" in dialog_md or "本场防御分走势" in dialog_md)
check("弹窗有对比卡", "第 1 轮 · 要求" in dialog_md and "第 2 轮 · 共情" in dialog_md)
check("弹窗有教练点评", "教练点评" in dialog_md)
check("弹窗有总结", len(at.success) == 1, " ".join(s.value for s in at.success)[:60])
check("弹窗有洞察文案", "你最爱用" in dialog_md or "还没有数据" in dialog_md)

print("[5] 导出报告")
reports_dir = ROOT / "data" / "reports"
before = sorted(reports_dir.glob("*.md")) if reports_dir.exists() else []
at.button(key="export").click()
at.run()
no_exception(at, "导出后")
after = sorted(reports_dir.glob("*.md")) if reports_dir.exists() else []
check("报告落盘", len(after) == len(before) + 1, f"{len(before)} → {len(after)}")
if after:
    report = after[-1].read_text(encoding="utf-8")
    check("报告含第一轮原话", session.turns[0].user_text[:8] in report)
    check("报告含第二轮原话", session.turns[1].user_text[:8] in report)
    check(
        "报告含防御变化",
        "防御变化" in report and f"{session.turns[0].defense_score}/5 → {session.turns[1].defense_score}/5" in report,
        "",
    )
    check("报告含锚点", "tier_" in report)
    check("报告含点评", "教练点评" in report)
    check("报告含场景与记录号", "适老化改造" in report and f"#{session.id}" in report)
    print(f"      报告：{after[-1].name}（{len(report)} 字符）")
try:
    check("有下载按钮", len(at.get("download_button")) == 1, f"{len(at.get('download_button'))} 个")
except Exception:
    pass

print("[6] 回访队列")
queue_file = ROOT / "data" / "followups.json"
at.button(key="followup").click()
at.run()
no_exception(at, "回访后")
check("生成回访问题", bool(at.session_state["session"].followup_question), at.session_state["session"].followup_question[:40])
check("写入 followups.json", queue_file.exists())
if queue_file.exists():
    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    check("队列里是最新一条", queue[-1]["question"] == at.session_state["session"].followup_question, str(len(queue)) + " 条")

print("[7] 点历史记录：恢复两轮 + 曲线")
restored_id = session.id
at.button(key=f"hist_{restored_id}").click()
at.run()
no_exception(at, "恢复历史后")
restored = at.session_state["session"]
check("恢复的 session_id 一致", restored.id == restored_id, str(restored.id))
check("恢复两轮", len(restored.turns) == 2, str(len(restored.turns)))
check("恢复策略", [t.strategy for t in restored.turns] == ["要求", "共情"], str([t.strategy for t in restored.turns]))
at.button(key="dash").click()
at.run()
check("恢复后弹窗曲线还在", len(at.get("plotly_chart")) >= 1)
check("恢复后仍能继续聊", not at.chat_input[0].disabled)

print("[8] 切换场景：新的一场，旧场已入库")
at.selectbox(key="scene_key").set_value("salary")
at.run()
no_exception(at, "切场景后")
switched = at.session_state["session"]
check("场景=职场谈加薪", switched.scene.label == "职场谈加薪", switched.scene.label)
check("新的一场为空", len(switched.turns) == 0)
check("旧场仍在库里", db.counts() == (1, 2), str(db.counts()))
check("Prompt 换成老板", "老板" in scenes.render_system_prompt(scenes.get("salary")))
check("案例按钮跟着换", any("1 ·" in b.label for b in at.button), "; ".join(b.label for b in at.button)[:80])

print("[9] 重开")
at.button(key="reset").click()
at.run()
no_exception(at, "重开后")
check("清空轮次", len(at.session_state["session"].turns) == 0)
check("输入框恢复", not at.chat_input[0].disabled)

print("[10] v3：潜台词 / 主动干预 / 无限聊天")
import agent as agent_mod

_orig_run_turn = agent_mod.run_turn


def _fake_angry(text, history=(), scene=None):
    """固定返回「要求 5 分」，用来触发 F13 的连续三句判定。"""
    return models.Turn(
        user_text=text,
        reply_text="你嫌我老了是吧？",
        strategy="要求",
        defense_score=5,
        evidence="tier_5 锚点 1",
        stage="手里的活停下，抬头看你",
        subtext="她在护的不是扶手，是这个家谁说了算",
        anchor=1,
    )


agent_mod.run_turn = _fake_angry
try:
    for _ in range(3):
        at.chat_input[0].set_value("妈，浴室必须装扶手，这个没得商量。")
        at.run()
        at.run()
finally:
    agent_mod.run_turn = _orig_run_turn

no_exception(at, "连发三句后")
long_session = at.session_state["session"]
check("三句都进了 session", len(long_session.turns) == 3, str(len(long_session.turns)))
long_md = " ".join(m.value for m in at.markdown)
check("潜台词渲染出来了（💡 + 小字注释）", "💡" in long_md and "谁说了算" in long_md)
check("黑话字段已下线", "台词库 #" not in long_md and "依据「" not in long_md)
check("F13 主动干预插话", "Agent 插话" in long_md)
check("干预只在连续三句后出现", long_md.count("Agent 插话") == 1, str(long_md.count("Agent 插话")))
check("三句后仍能继续（无限聊天）", long_session.round_index == 4, str(long_session.round_index))
check("无限聊天：输入框一直亮着", not at.chat_input[0].disabled)
check("三句都落了库", db.counts()[1] >= 3, str(db.counts()))

print("[10.5] 小抄：跟着这一轮变，且只生成一次")
copies = [t.copy_text for t in long_session.turns]
check("每轮都写了小抄", all(copies), str(copies))
check("同样输入也不会复读同一句", len(set(copies)) >= 2, str(copies))
check("右栏渲染的就是这一轮的小抄", copies[-1] in long_md, copies[-1])
check("小抄卡带「就用这句发过去」按钮",
      any(b.label == "就用这句发过去" for b in at.button), str([b.label for b in at.button]))

print("[11] F14 沟通体检报告（Dashboard）")
at.button(key="dash").click()
at.run()
no_exception(at, "打开 Dashboard 后")
check("Dashboard 有数据看板", "本场防御分走势" in " ".join(m.value for m in at.markdown))
at.button(key="reset").click()
at.run()

print(f"\n{'全部通过' if FAIL == 0 else f'{FAIL} 项失败'}")
sys.exit(1 if FAIL else 0)
