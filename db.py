"""db.py — SQLite 持久化层（标准库 sqlite3，零依赖）。

设计原则：
1. **所有公开函数都不抛异常**。数据库坏掉、目录只读、文件被锁——最坏的情况只是
   返回 None / [] / False，并把原因记进 LAST_ERROR，UI 照常能跑（页面上会提示）。
2. 每次操作开一个新连接（sqlite 连接很轻），不在模块级长期持有连接，
   避免 Streamlit 多线程 rerun 时的跨线程复用问题。
3. 每次对话必定落库：一轮一条 turns，一场一条 sessions。

表结构（按需求文档原样）：
    sessions(id, scene_name, created_at)
    turns(id, session_id, round, user_text, reply, strategy, defense)
    —— 额外补了 evidence / latency_ms / created_at，因为报告和「台词锚点」要用，
       缺了这三列就只能把模型输出丢掉，历史回放会失真。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

import config

# 默认 data/app.db；测试用 SAY_APP_DB 指到临时文件，避免污染演示数据
DB_PATH: Path = Path(os.environ.get("SAY_APP_DB") or (config.ROOT / "data" / "app.db"))
LAST_ERROR: str = ""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_name  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    round       INTEGER NOT NULL,
    user_text   TEXT    NOT NULL,
    reply       TEXT    NOT NULL,
    strategy    TEXT    NOT NULL,
    defense     INTEGER NOT NULL,
    evidence    TEXT    DEFAULT '',
    stage       TEXT    DEFAULT '',
    subtext     TEXT    DEFAULT '',
    fear        TEXT    DEFAULT '',
    anchor      INTEGER DEFAULT 0,
    reaction    TEXT    DEFAULT '',
    delta_reason TEXT   DEFAULT '',
    key_phrase  TEXT    DEFAULT '',
    copy        TEXT    DEFAULT '',
    latency_ms  INTEGER DEFAULT 0,
    created_at  TEXT    DEFAULT '',
    UNIQUE (session_id, round)
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns (session_id, round);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _fail(action: str, exc: BaseException) -> None:
    """统一记录错误：打日志给终端，留 LAST_ERROR 给 UI。"""
    global LAST_ERROR
    LAST_ERROR = f"{action}：{type(exc).__name__}: {exc}"
    print(f"[db] {LAST_ERROR}", file=sys.stderr)


def use_db(path: Path | str) -> None:
    """测试用：换一个数据库文件。"""
    global DB_PATH
    DB_PATH = Path(path)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


# ───────────────────────────── 建表 ─────────────────────────────
_EXTRA_COLUMNS = {
    "stage": "TEXT DEFAULT ''",
    "subtext": "TEXT DEFAULT ''",
    "fear": "TEXT DEFAULT ''",
    "anchor": "INTEGER DEFAULT 0",
    "reaction": "TEXT DEFAULT ''",
    "delta_reason": "TEXT DEFAULT ''",
    "key_phrase": "TEXT DEFAULT ''",
    "copy": "TEXT DEFAULT ''",
}


def _migrate(conn: sqlite3.Connection) -> None:
    """老库升级：缺哪列补哪列（CREATE TABLE IF NOT EXISTS 不会改已有表）。"""
    have = {row["name"] for row in conn.execute("PRAGMA table_info(turns)")}
    for name, ddl in _EXTRA_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE turns ADD COLUMN {name} {ddl}")


def init_db() -> bool:
    """建库建表，可重复调用。失败返回 False，不抛。"""
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
            _migrate(conn)
        return True
    except Exception as exc:  # 磁盘只读、路径非法、文件被锁……
        _fail("初始化数据库失败", exc)
        return False


# ───────────────────────────── 写 ─────────────────────────────
def create_session(scene_name: str) -> int | None:
    """新建一场演练，返回 id。"""
    try:
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO sessions (scene_name, created_at) VALUES (?, ?)",
                (str(scene_name or ""), _now()),
            )
            return int(cur.lastrowid) if cur.lastrowid else None
    except Exception as exc:
        _fail("新建会话失败", exc)
        return None


def add_turn(session_id: int | None, round_no: int, turn: Any) -> bool:
    """把一轮对话写进库。同一场同一轮重复写会覆盖（UNIQUE + UPSERT）。"""
    if not session_id:
        return False
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO turns
                    (session_id, round, user_text, reply, strategy, defense, evidence,
                     stage, subtext, fear, anchor, reaction, delta_reason, key_phrase,
                     copy, latency_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (session_id, round) DO UPDATE SET
                    user_text  = excluded.user_text,
                    reply      = excluded.reply,
                    strategy   = excluded.strategy,
                    defense    = excluded.defense,
                    evidence   = excluded.evidence,
                    stage      = excluded.stage,
                    subtext    = excluded.subtext,
                    fear       = excluded.fear,
                    anchor     = excluded.anchor,
                    reaction   = excluded.reaction,
                    delta_reason = excluded.delta_reason,
                    key_phrase = excluded.key_phrase,
                    copy       = excluded.copy,
                    latency_ms = excluded.latency_ms
                """,
                (
                    int(session_id),
                    int(round_no),
                    str(getattr(turn, "user_text", "")),
                    str(getattr(turn, "reply_text", "")),
                    str(getattr(turn, "strategy", "")),
                    int(getattr(turn, "defense_score", 3) or 3),
                    str(getattr(turn, "evidence", "") or ""),
                    str(getattr(turn, "stage", "") or ""),
                    str(getattr(turn, "subtext", "") or ""),
                    str(getattr(turn, "fear", "") or ""),
                    int(getattr(turn, "anchor", 0) or 0),
                    str(getattr(turn, "reaction", "") or ""),
                    str(getattr(turn, "delta_reason", "") or ""),
                    str(getattr(turn, "key_phrase", "") or ""),
                    str(getattr(turn, "copy_text", "") or ""),
                    int(getattr(turn, "latency_ms", 0) or 0),
                    str(getattr(turn, "created_at", "") or _now()),
                ),
            )
        return True
    except Exception as exc:
        _fail("写入对话失败", exc)
        return False


def delete_session(session_id: int) -> bool:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (int(session_id),))
        return True
    except Exception as exc:
        _fail("删除会话失败", exc)
        return False


def clear_all() -> bool:
    """清空全部历史（侧栏「清空历史」用）。"""
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM turns")
            conn.execute("DELETE FROM sessions")
        return True
    except Exception as exc:
        _fail("清空历史失败", exc)
        return False


# ───────────────────────────── 读 ─────────────────────────────
def list_sessions(limit: int = 15) -> list[dict[str, Any]]:
    """最近若干场，带「首轮分 → 末轮分」方便侧栏直接显示。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT id, scene_name, created_at FROM sessions ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                turns = conn.execute(
                    "SELECT round, strategy, defense FROM turns WHERE session_id = ? ORDER BY round",
                    (row["id"],),
                ).fetchall()
                if not turns:
                    continue
                out.append(
                    {
                        "id": int(row["id"]),
                        "scene_name": row["scene_name"],
                        "created_at": row["created_at"],
                        "rounds": len(turns),
                        "first_defense": int(turns[0]["defense"]),
                        "last_defense": int(turns[-1]["defense"]),
                        "first_strategy": turns[0]["strategy"],
                        "last_strategy": turns[-1]["strategy"],
                    }
                )
            return out
    except Exception as exc:
        _fail("读取历史列表失败", exc)
        return []


def get_session(session_id: int) -> dict[str, Any] | None:
    """一场完整数据（含 turns），给「点击历史恢复页面」用。"""
    try:
        with _connect() as conn:
            head = conn.execute(
                "SELECT id, scene_name, created_at FROM sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
            if head is None:
                return None
            turns = conn.execute(
                """
                SELECT round, user_text, reply, strategy, defense, evidence,
                       stage, subtext, fear, anchor, reaction, delta_reason, key_phrase,
                       copy, latency_ms, created_at
                FROM turns WHERE session_id = ? ORDER BY round
                """,
                (int(session_id),),
            ).fetchall()
            return {
                "id": int(head["id"]),
                "scene_name": head["scene_name"],
                "created_at": head["created_at"],
                "turns": [dict(row) for row in turns],
            }
    except Exception as exc:
        _fail("读取会话失败", exc)
        return None


def all_turns() -> list[dict[str, Any]]:
    """全库 turns（按场次、轮次排好序），给「沟通画像」统计用。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT t.session_id, t.round, t.user_text, t.reply, t.strategy, t.defense,
                       t.reaction, t.delta_reason, t.key_phrase,
                       s.scene_name, s.created_at AS session_created_at
                FROM turns t JOIN sessions s ON s.id = t.session_id
                ORDER BY t.session_id, t.round
                """
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        _fail("读取全部对话失败", exc)
        return []


def counts() -> tuple[int, int]:
    """(场次, 轮次) —— 侧栏和自检用。"""
    try:
        with _connect() as conn:
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            return int(sessions), int(turns)
    except Exception as exc:
        _fail("统计失败", exc)
        return 0, 0


def export_rows(session_id: int) -> Sequence[dict[str, Any]]:
    """给报告导出用的行数据。"""
    data = get_session(session_id)
    return (data or {}).get("turns", [])
