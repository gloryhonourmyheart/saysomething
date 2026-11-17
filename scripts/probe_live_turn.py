"""scripts/probe_live_turn.py — 真机演练：真实模型跑一整轮，检查右栏小抄是否对得上。

会真的调用一次主对话 + 一次 L2 改写（花钱，约 5-10 秒），但用临时库，不污染演示数据。
用法：
    python scripts/probe_live_turn.py "妈，浴室必须装扶手，这个没得商量。"
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import db 之前设置
os.environ["SAY_APP_DB"] = str(Path(tempfile.mkdtemp(prefix="sf-probe-")) / "app.db")

from streamlit.testing.v1 import AppTest  # noqa: E402

user_text = sys.argv[1] if len(sys.argv) > 1 else "妈，浴室必须装扶手，这个没得商量。"

at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
at.run()
at.chat_input[0].set_value(user_text)
at.run()
at.run()

if at.exception:
    for e in at.exception:
        print(f"异常：{e.message}")
    raise SystemExit(1)

sess = at.session_state["session"]
turn = sess.turns[-1]
print(f"\n你的原话：{turn.user_text}")
print(f"她回：{turn.reply_text}")
print(f"判定：{turn.strategy} / 防御 {turn.defense_score} / 耗时 {turn.latency_ms}ms")
print(f"右栏小抄：{turn.copy_text}")
print(f"按钮文案：{[b.label for b in at.button if b.label == '就用这句发过去']}")
md = " ".join(m.value for m in at.markdown)
print(f"小抄卡出现在页面上：{turn.copy_text in md}")
print(f"小抄引用了她这句原话的片段：{any(seg in turn.copy_text for seg in turn.reply_text.replace('，', '。').split('。') if len(seg) >= 4)}")
