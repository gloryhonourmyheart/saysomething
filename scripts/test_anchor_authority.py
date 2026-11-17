"""test_anchor_authority.py — 第 4 步验收：台词是唯一权威。

覆盖：
1. 模型返回的 defense 被彻底忽略；
2. 5 分暴怒绝不会被标成 3 分；
3. anchor 缺失 / 非法 → tier5 兜底；
4. 因果规则 constrain_defense 的四条公式；
5. 解释降档后在允许档重选台词；
6. 挚友 guard 优先级最高；
7. 最终 defense_score == 最终台词 tier，reply == 最终台词 text。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
import rules  # noqa: E402
import scenes  # noqa: E402
from models import Turn  # noqa: E402


def anchor_of_tier(scene: scenes.Scene, tier: int) -> int:
    for number, line in scenes.numbered(scene):
        if line.tier == tier:
            return number
    raise AssertionError(f"{scene.key} 没有 tier {tier}")


def fake_reply(payload: dict) -> mock._patch:
    return mock.patch.object(
        agent,
        "_chat",
        return_value=json.dumps(payload, ensure_ascii=False),
    )


class AnchorAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scene = scenes.get("aging")
        patcher = mock.patch.object(agent, "available", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_validate_ignores_defense_and_reply(self) -> None:
        got = agent.validate_payload(
            {
                "strategy": "要求",
                "anchor_id": 1,
                "defense": 1,
                "reply": "模型自己写的话",
                "evidence": "tier_1",
            }
        )
        self.assertEqual(got, ("要求", 1))

    def test_validate_accepts_legacy_anchor_key(self) -> None:
        self.assertEqual(agent.validate_payload({"strategy": "共情", "anchor": "12"}), ("共情", 12))

    def test_validate_missing_anchor_is_zero(self) -> None:
        self.assertEqual(agent.validate_payload({"strategy": "要求"}), ("要求", 0))

    def test_validate_rejects_bad_strategy(self) -> None:
        self.assertIsNone(agent.validate_payload({"strategy": "撒娇", "anchor_id": 1}))

    def test_constrain_require_never_drops(self) -> None:
        score, reason = rules.constrain_defense(5, "要求", 1)
        self.assertEqual(score, 5)
        self.assertIn("不降", reason)

    def test_constrain_avoid_never_drops(self) -> None:
        self.assertEqual(rules.constrain_defense(4, "回避", 1)[0], 4)

    def test_constrain_explain_drops_at_most_one(self) -> None:
        self.assertEqual(rules.constrain_defense(5, "解释", 1)[0], 4)
        self.assertEqual(rules.constrain_defense(5, "解释", 5)[0], 5)
        self.assertEqual(rules.constrain_defense(3, "解释", 1)[0], 2)

    def test_constrain_empathy_can_drop_but_not_rise(self) -> None:
        self.assertEqual(rules.constrain_defense(5, "共情", 1)[0], 1)
        self.assertEqual(rules.constrain_defense(2, "共情", 5)[0], 2)

    def test_constrain_first_turn_uses_anchor(self) -> None:
        self.assertEqual(rules.constrain_defense(None, "要求", 4)[0], 4)

    def test_defense_field_is_ignored_and_anchor_wins(self) -> None:
        # anchor 1 是 tier5；模型同时谎报 defense=3，最终必须还是 5。
        with fake_reply({"strategy": "要求", "anchor_id": 1, "defense": 3, "reply": "不是台词库的话"}):
            turn = agent.run_turn("妈，浴室必须装扶手。", scene=self.scene)
        self.assertEqual(turn.defense_score, 5)
        self.assertEqual(turn.reply_text, self.scene.anchor(1).text)
        self.assertEqual(turn.reaction, self.scene.anchor(1).reaction)
        self.assertNotEqual(turn.reply_text, "不是台词库的话")
        self.assertIn("tier_5", turn.evidence)

    def test_missing_anchor_falls_back_to_tier5(self) -> None:
        with fake_reply({"strategy": "要求", "defense": 3, "reply": "模型瞎写"}):
            turn = agent.run_turn("妈，你必须听我的。", scene=self.scene)
        self.assertEqual(turn.defense_score, 5)
        self.assertEqual(turn.reply_text, self.scene.fallback_anchor().text)
        self.assertTrue(turn.degraded)

    def test_invalid_anchor_falls_back_to_tier5(self) -> None:
        with fake_reply({"strategy": "共情", "anchor_id": 9999}):
            turn = agent.run_turn("我知道你难受。", scene=self.scene)
        self.assertEqual(turn.defense_score, 5)
        self.assertEqual(turn.reply_text, self.scene.fallback_anchor().text)
        self.assertTrue(turn.degraded)

    def test_two_invalid_json_falls_back_to_tier5(self) -> None:
        with mock.patch.object(agent, "_chat", return_value="这不是 JSON"):
            turn = agent.run_turn("随便说点什么", scene=self.scene)
        self.assertEqual(turn.defense_score, 5)
        self.assertEqual(turn.reply_text, self.scene.fallback_anchor().text)
        self.assertTrue(turn.degraded)

    def test_explain_reselects_line_in_allowed_tier(self) -> None:
        tier1 = anchor_of_tier(self.scene, 1)
        prev = Turn("上一句", "上一句回复", "要求", 5)
        with fake_reply({"strategy": "解释", "anchor_id": tier1}):
            turn = agent.run_turn("我解释一下，我是怕你滑。", [prev], self.scene)
        self.assertEqual(turn.defense_score, 4)
        self.assertNotEqual(turn.anchor, tier1)
        self.assertEqual(
            turn.reply_text,
            self.scene.line_for_tier(4, prefer_text=self.scene.anchor(tier1).text).text,
        )
        self.assertIn("降", turn.delta_reason)

    def test_friend_guard_has_highest_priority(self) -> None:
        bestie = scenes.get("bestie")
        tier5 = anchor_of_tier(bestie, 5)
        prev = Turn("上一句", "上一句回复", "要求", 5)
        with fake_reply({"strategy": "要求", "anchor_id": tier5}):
            turn = agent.run_turn("我们和好吧，别生气了。", [prev], bestie)
        self.assertEqual(turn.defense_score, 3)
        self.assertEqual(turn.reply_text, bestie.line_for_tier(3).text)
        self.assertIn("规则锁定", turn.evidence)

    def test_final_score_always_matches_final_line_tier(self) -> None:
        cases = [
            ("aging", "要求", 5),
            ("aging", "解释", 5),
            ("money", "共情", 2),
            ("salary", "回避", 3),
            ("bestie", "要求", 5),
        ]
        for key, strategy, prev_score in cases:
            scene = scenes.get(key)
            prev = Turn("上一句", "上一句回复", "要求", prev_score)
            anchor_id = anchor_of_tier(scene, 5)
            with fake_reply({"strategy": strategy, "anchor_id": anchor_id, "defense": 1}):
                turn = agent.run_turn("我现在只想把话说明白。", [prev], scene)
            line = scene.anchor(turn.anchor)
            self.assertIsNotNone(line, f"{key}: 最终 anchor 必须能取到台词")
            self.assertEqual(turn.defense_score, line.tier, f"{key}: 分数必须等于最终台词档")
            self.assertEqual(turn.reply_text, line.text, f"{key}: reply 必须等于最终台词")
            self.assertEqual(turn.reaction, line.reaction, f"{key}: reaction 必须跟台词走")


if __name__ == "__main__":
    unittest.main(verbosity=2)
