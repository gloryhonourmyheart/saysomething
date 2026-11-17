"""test_db.py — 数据库层 + 沟通画像的离线自检（不调 API）。

重点不是「正常情况能跑」，而是：
1. 库坏了 / 目录不可写 / 表被删——所有函数都不能抛异常；
2. 同一场同一轮重复写，不能出现两条（UNIQUE + UPSERT）；
3. 画像的百分比、高防御反应、建议，都要能从行数据手算验证。
"""
from __future__ import annotations

import sys
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import insight  # noqa: E402
import models  # noqa: E402
import scenes  # noqa: E402
from models import Turn  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"  → {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  → {detail}" if detail else ""))


def turn(text: str, reply: str, strategy: str, defense: int, **kw) -> Turn:
    return Turn(user_text=text, reply_text=reply, strategy=strategy, defense_score=defense, evidence=f"tier_{defense}", **kw)


tmp = Path(tempfile.mkdtemp(prefix="sf-test-"))
db.use_db(tmp / "app.db")

print("[1] 建表与空库")
check("init_db 成功", db.init_db())
check("重复 init 不炸", db.init_db())
check("空库统计 (0,0)", db.counts() == (0, 0), str(db.counts()))
check("空库会话列表", db.list_sessions() == [])
check("不存在的会话返回 None", db.get_session(999) is None)

print("[2] 写入与读取")
sid = db.create_session("适老化改造")
check("create_session 返回 id", isinstance(sid, int) and sid > 0, str(sid))
check("add_turn 第 1 轮", db.add_turn(sid, 1, turn("必须装扶手", "你嫌我老了是吧", "要求", 5, anchor=1, reaction="暴怒反击", delta_reason="首轮按锚点 5", key_phrase="必须装扶手")))
check("add_turn 第 2 轮", db.add_turn(sid, 2, turn("我知道你能行", "装就装吧", "共情", 2)))
check("统计 (1,2)", db.counts() == (1, 2), str(db.counts()))

rows = db.list_sessions()
check("列表 1 条", len(rows) == 1)
check("首轮防御 5", rows[0]["first_defense"] == 5, str(rows[0]))
check("末轮防御 2", rows[0]["last_defense"] == 2)
check("轮数 2", rows[0]["rounds"] == 2)

data = db.get_session(sid)
check("回放两轮", data is not None and len(data["turns"]) == 2)
check("回放保留 evidence", data["turns"][0]["evidence"] == "tier_5")
check("回放保留 anchor", data["turns"][0]["anchor"] == 1, str(data["turns"][0]))
check("回放保留 reaction", data["turns"][0]["reaction"] == "暴怒反击")
check("回放保留 delta_reason", data["turns"][0]["delta_reason"] == "首轮按锚点 5")
check("回放保留 key_phrase", data["turns"][0]["key_phrase"] == "必须装扶手")

print("[3] 幂等：同一轮重写不产生重复行")
db.add_turn(sid, 2, turn("我知道你能行", "那你说咋弄", "共情", 1))
check("仍然 2 轮", db.counts() == (1, 2), str(db.counts()))
check("末轮被覆盖为 1", db.get_session(sid)["turns"][1]["defense"] == 1)

print("[4] 恢复成 Session 对象（历史点击）")
restored = models.Session.from_rows(data["turns"], session_id=sid, scene_key=scenes.key_of("适老化改造"))
check("恢复轮数", len(restored.turns) == 2)
check("恢复 id", restored.id == sid)
check("恢复场景", restored.scene.label == "适老化改造", restored.scene.label)
check("恢复后 finished", restored.finished)
check("恢复 reaction", restored.turns[0].reaction == "暴怒反击")
check("恢复 anchor", restored.turns[0].anchor == 1)
check("恢复 delta_reason", restored.turns[0].delta_reason == "首轮按锚点 5")
check("脏行被跳过",
      len(models.Session.from_rows([{"user_text": "x"}, {"bad": 1}], session_id=1, scene_key="aging").turns) == 1)

print("[5] 沟通画像：百分比与高防御反应")
db.use_db(tmp / "profile.db")
db.init_db()
s1 = db.create_session("适老化改造")
db.add_turn(s1, 1, turn("必须装", "你嫌我老了", "要求", 5))
db.add_turn(s1, 2, turn("我觉得没必要再说", "……", "回避", 3))
s2 = db.create_session("职场谈加薪")
db.add_turn(s2, 1, turn("我要加薪", "你值这个价吗", "要求", 5))
db.add_turn(s2, 2, turn("我知道公司不容易", "下季度给你报", "共情", 2))

profile = insight.build_profile(db.all_turns())
check("总句数 4", profile.total_turns == 4, str(profile.total_turns))
check("场次 2", profile.session_count == 2)
check("要求占 50%", profile.pct["要求"] == 50, str(profile.pct))
check("共情占 25%", profile.pct["共情"] == 25)
check("top=要求", profile.top == "要求")
check("高防御事件 2", profile.high_def_events == 2, str(profile.reactions))
check("高防御后回避 1 次", profile.reactions.get("回避") == 1)
check("平均下降 2.5", profile.avg_drop == 2.5, str(profile.avg_drop))
check("洞察句 ≤4 条", len(profile.lines) <= 4, str(len(profile.lines)))
check("洞察含策略名", "要求" in profile.lines[0])
check("空库返回 empty", insight.build_profile([]).empty)

print("[6] 损坏的库不抛异常")
db.use_db(tmp / "broken.db")
(tmp / "broken.db").write_bytes(b"this is not a sqlite file")
check("init_db 返回 False 不抛", db.init_db() is False)
check("create_session 返回 None", db.create_session("x") is None)
check("add_turn 返回 False", db.add_turn(1, 1, turn("a", "b", "解释", 3)) is False)
check("list_sessions 返回 []", db.list_sessions() == [])
check("get_session 返回 None", db.get_session(1) is None)
check("all_turns 返回 []", db.all_turns() == [])
check("counts 返回 (0,0)", db.counts() == (0, 0))
check("LAST_ERROR 有记录", bool(db.LAST_ERROR))

print("[7] 只读目录也不抛")
db.use_db(Path("Z:/nope/app.db"))
check("init_db 失败返回 False", db.init_db() is False)
check("函数仍不抛", db.list_sessions() == [] and db.counts() == (0, 0))

print("[8] 多场景：Prompt 随场景变，编号与档位一致")
for scene in scenes.all_scenes():
    prompt = scenes.render_system_prompt(scene)
    check(f"{scene.label} 含 tier_5", "tier_5" in prompt)
    check(f"{scene.label} 含 role", f"扮演「{scene.role_name}」" in prompt)
    check(f"{scene.label} 台词全覆盖", all(line.text in prompt for lines in scene.lines.values() for line in lines))
    check(f"{scene.label} 禁语生效", scene.forbidden[0] in prompt)
check("场景 key 唯一", len({s.key for s in scenes.all_scenes()}) == len(scenes.all_scenes()))
check("场景数量 ≥ 4", len(scenes.all_scenes()) >= 4)
check("挚友场景已注册", scenes.get("bestie").label == "挚友冷战")
check("未知 key 回退默认", scenes.get("nope").key == scenes.DEFAULT_KEY)
check("label 反查 key", scenes.key_of("职场谈加薪") == "salary")
check("每个场景 3 个案例", all(len(s.cases) == 3 for s in scenes.all_scenes()))
check("每个场景 5 档台词齐", all(set(s.lines) == {1, 2, 3, 4, 5} for s in scenes.all_scenes()))

print("[9] 老库自动 ALTER TABLE 补列，历史数据不丢")
old_db = tmp / "old.db"
conn = sqlite3.connect(old_db)
conn.executescript(
    """
    CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT,
                           scene_name TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id INTEGER NOT NULL, round INTEGER NOT NULL,
                        user_text TEXT NOT NULL, reply TEXT NOT NULL,
                        strategy TEXT NOT NULL, defense INTEGER NOT NULL,
                        evidence TEXT DEFAULT '', stage TEXT DEFAULT '',
                        subtext TEXT DEFAULT '', fear TEXT DEFAULT '',
                        copy TEXT DEFAULT '', latency_ms INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT '',
                        UNIQUE (session_id, round));
    INSERT INTO sessions (scene_name, created_at) VALUES ('适老化改造', '2026-01-01 00:00:00');
    INSERT INTO turns (session_id, round, user_text, reply, strategy, defense, evidence)
        VALUES (1, 1, '必须装扶手', '你嫌我老了', '要求', 5, 'tier_5');
    """
)
conn.commit()
conn.close()
db.use_db(old_db)
check("老库 init_db 成功", db.init_db())
old_data = db.get_session(1)
check("老库数据没丢", old_data is not None and old_data["turns"][0]["user_text"] == "必须装扶手")
check("老库补出 anchor 默认 0", old_data["turns"][0]["anchor"] == 0)
check("老库补出 reaction 默认空", old_data["turns"][0]["reaction"] == "")
check("老库补出 delta_reason 默认空", old_data["turns"][0]["delta_reason"] == "")
check("老库补出 key_phrase 默认空", old_data["turns"][0]["key_phrase"] == "")

print()
print(f"全部通过 {PASS} 项" if FAIL == 0 else f"失败 {FAIL} 项 / 通过 {PASS} 项")
sys.exit(1 if FAIL else 0)
