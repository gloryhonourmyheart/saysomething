"""scripts/test_hints.py — 小抄层的回归测试（不连模型、不碰演示库）。

跑法：在项目根目录执行  python scripts/test_hints.py

覆盖 6 件最容易回归的事：
1. 换输入 → 小抄跟着变（这是这次要修的核心 bug）
2. 换她的台词 → 小抄开头不一样
3. 5 档 vs 2 档 → 策略不同（5 不辩解，2 给台阶）
4. 上一版小抄当负样本 → 不会给出同一句
5. 所有输出都不含禁语、长度不超限
6. L2 脏输出/超时 → 静默回落到 L1，不抛异常
7. copy 字段能落库、能读回（L3）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import hints  # noqa: E402
import models  # noqa: E402
import scenes  # noqa: E402
import config  # noqa: E402


def turn(user: str, reply: str, strategy: str = "要求", defense: int = 5) -> models.Turn:
    return models.Turn(user_text=user, reply_text=reply, strategy=strategy, defense_score=defense)


class TestFallback(unittest.TestCase):
    def setUp(self):
        self.scene = scenes.get("aging")

    def test_a_different_input_different_copy(self):
        a = hints.fallback_copy(
            self.scene, [turn("妈，浴室必须装扶手，这个没得商量。", "你嫌我老了是吧？用不着你操心。")], 5
        ).copy
        b = hints.fallback_copy(
            self.scene, [turn("妈，我下周要出差，怕你一个人在家", "你要真惦记我，就不会一年只回来两趟。")], 5
        ).copy
        self.assertNotEqual(a, b, f"换输入小抄必须变：{a!r} vs {b!r}")

    def test_b_different_her_line_different_opening(self):
        first = hints.fallback_copy(
            self.scene, [turn("妈，装个扶手吧", "你别管我了，我自己能行。")], 4
        ).copy
        second = hints.fallback_copy(
            self.scene, [turn("妈，装个扶手吧", "你要真惦记我，就不会一年只回来两趟。")], 4
        ).copy
        self.assertNotEqual(first[:6], second[:6], f"她的台词换了，开头该换：{first!r} vs {second!r}")

    def test_c_tier5_vs_tier2_strategy_differs(self):
        high = hints.fallback_copy(self.scene, [turn("妈，必须装", "你嫌我老了。", "要求", 5)], 5).copy
        low = hints.fallback_copy(self.scene, [turn("妈，我先只装一个", "……装就装吧。", "共情", 2)], 2).copy
        self.assertNotEqual(high, low)
        # 5 档不能出现「给台阶」的关键词，2 档不能出现「我不辩」式的硬接
        self.assertNotIn("听你的", high)
        self.assertNotIn("不辩", low)

    def test_d_negative_sample(self):
        turns = [turn("妈，浴室必须装扶手", "你嫌我老了是吧？用不着你操心。")]
        first = hints.fallback_copy(self.scene, turns, 5).copy
        second = hints.fallback_copy(self.scene, turns, 5, last_copy=first).copy
        self.assertFalse(hints.is_too_similar(second, first), f"上一版当负样本后仍然复读：{first!r} / {second!r}")

    def test_e_all_scenes_all_tiers_safe(self):
        for key in scenes.SCENES:
            scene = scenes.get(key)
            for tier in range(1, 6):
                for turns in (
                    [turn("你是不是觉得我不懂事", "随便你吧。")],
                    [turn("我跟你说个事", "嗯。")],
                    [turn("必须这样，没得商量", "你少来这套。")],
                ):
                    text = hints.fallback_copy(scene, turns, tier).copy
                    self.assertTrue(8 <= len(text) <= config.COPY_MAX_CHARS, f"{key}/{tier}: {text!r}")
                    self.assertFalse(hints.has_quote(text), f"{key}/{tier} 仍有引号：{text!r}")
                    for phrase in config.FORBIDDEN_PHRASES:
                        self.assertNotIn(phrase, text, f"{key}/{tier} 命中禁语 {phrase}: {text!r}")

    def test_f_short_drama_never_scene_word(self):
        """用户已经在聊短剧，L1 不能再把初始场景的「扶手」拉回来。"""
        turns = [turn("妈，别熬夜刷短剧了", "你管好你自己。", "要求", 5)]
        for tier in range(1, 6):
            text = hints.fallback_copy(self.scene, turns, tier).copy
            self.assertNotIn("扶手", text, f"tier {tier} 跑题：{text!r}")
            self.assertTrue("短剧" in text or "熬夜" in text, f"tier {tier} 没咬住短剧：{text!r}")


class TestPolish(unittest.TestCase):
    def setUp(self):
        self.scene = scenes.get("aging")
        self.turns = [turn("妈，浴室必须装扶手", "你嫌我老了是吧？用不着你操心。")]
        # 测试机可能没装 openai / 没配 key：available() 会直接短路成 None，
        # 那样测的就不是「解析与校验」而是「短路」了，所以这里必须显式打开。
        self._avail = hints.agent.available
        hints.agent.available = lambda: True

    def tearDown(self):
        hints.agent.available = self._avail

    def test_bad_json_falls_back(self):
        original = hints.agent.chat_json
        hints.agent.chat_json = lambda *a, **k: "这不是 JSON"
        try:
            self.assertIsNone(hints.polish(self.scene, self.turns, 5))
        finally:
            hints.agent.chat_json = original

    def test_repeat_line_rejected(self):
        original = hints.agent.chat_json
        dup = "你嫌我老了是吧，这话我认，我不辩。"
        hints.agent.chat_json = lambda *a, **k: (
            '{"copy": "%s", "keeps": "她的原话", "drops": "辩解"}' % dup
        )
        try:
            self.assertIsNone(hints.polish(self.scene, self.turns, 5, last_copy=dup))
        finally:
            hints.agent.chat_json = original

    def test_scene_word_hallucination_rejected(self):
        """用户在聊短剧，模型却把小抄拉回「扶手」——必须判幻觉、回落 L1。"""
        original = hints.agent.chat_json
        bad = "你管好你自己这话我认，扶手的事先放下。"
        hints.agent.chat_json = lambda *a, **k: (
            '{"copy": "%s", "keeps": "她的原话", "drops": "辩解"}' % bad
        )
        turns = [turn("妈，别熬夜刷短剧了", "你管好你自己。")]
        try:
            self.assertIsNone(hints.polish(self.scene, turns, 5))
        finally:
            hints.agent.chat_json = original

    def test_new_number_rejected(self):
        """用户和对方都没说过「三点」，小抄里编出这个时间就是新增事实。"""
        original = hints.agent.chat_json
        bad = "你管好你自己这话我认，可昨晚三点你屋里还亮着。"
        hints.agent.chat_json = lambda *a, **k: (
            '{"copy": "%s", "keeps": "她的原话", "drops": "辩解"}' % bad
        )
        turns = [turn("妈，别熬夜刷短剧了", "你管好你自己。")]
        try:
            self.assertIsNone(hints.polish(self.scene, turns, 5))
        finally:
            hints.agent.chat_json = original

    def test_quote_rejected(self):
        """小抄里出现引号就是机器复读体，直接废弃。"""
        original = hints.agent.chat_json
        bad = "「你嫌我老了」这话我认，我不辩。"
        hints.agent.chat_json = lambda *a, **k: (
            '{"copy": "%s", "keeps": "她的原话", "drops": "辩解"}' % bad
        )
        try:
            self.assertIsNone(hints.polish(self.scene, self.turns, 5))
        finally:
            hints.agent.chat_json = original

    def test_good_json_accepted_and_grounded(self):
        original = hints.agent.chat_json
        good = "用不着你操心这句话我听进去了，扶手的事先放下，你定。"
        hints.agent.chat_json = lambda *a, **k: (
            '{"copy": "%s", "keeps": "她的原话", "drops": "讲道理"}' % good
        )
        try:
            out = hints.polish(self.scene, self.turns, 5)
            self.assertIsNotNone(out)
            self.assertEqual(out.source, "L2")
            self.assertEqual(out.copy, good)
        finally:
            hints.agent.chat_json = original

    def test_exception_falls_back(self):
        original = hints.agent.chat_json

        def boom(*a, **k):
            raise RuntimeError("超时")

        hints.agent.chat_json = boom
        try:
            self.assertIsNone(hints.polish(self.scene, self.turns, 5))
        finally:
            hints.agent.chat_json = original


class TestStorage(unittest.TestCase):
    def test_copy_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db.use_db(Path(tmp) / "test.db")
            self.assertTrue(db.init_db())
            sid = db.create_session("适老化改造")
            self.assertIsNotNone(sid)
            t = turn("妈，浴室必须装扶手", "你嫌我老了是吧？", "要求", 5)
            t.copy_text = "用不着你操心这句我认，我不辩。"
            self.assertTrue(db.add_turn(sid, 1, t))
            rows = (db.get_session(sid) or {}).get("turns", [])
            self.assertEqual(rows[0]["copy"], t.copy_text)
            restored = models.Session.from_rows(rows, session_id=sid, scene_key="aging")
            self.assertEqual(restored.turns[0].copy_text, t.copy_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
