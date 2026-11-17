"""test_scenes.py — 锚点台词 / 编号 / Prompt 契约（不调 API）。

第 2 步验收：每句台词都带 tier + reaction；模型只认 anchor_id；
fallback_anchor 永远给 tier5；Prompt 用 `1. [5分·暴怒反击] ...` 标编号。
"""
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scenes


def main() -> None:
    all_scenes = scenes.all_scenes()
    assert len(all_scenes) >= 4, f"场景不足：{len(all_scenes)}"
    total = 0
    print(f"场景数：{len(all_scenes)}")
    for scene in all_scenes:
        counts = {tier: len(scene.lines_of(tier)) for tier in range(5, 0, -1)}
        assert all(counts.values()), (scene.key, counts)
        assert scene.fallback_anchor().tier == 5, (scene.key, "fallback tier")
        assert scene.anchor(1) is not None, (scene.key, "anchor 1")
        assert scene.anchor(999999) is None, (scene.key, "anchor 999999")
        assert scene.line_for_tier(3) is not None, (scene.key, "tier 3")
        assert scene.line_for_tier(3).tier == 3, (scene.key, "tier 3 mismatch")
        assert scene.match_anchor("你嫌我老了") is not None, (scene.key, "match anchor")

        for tier, lines in scene.lines.items():
            assert 1 <= tier <= 5, (scene.key, tier)
            for line in lines:
                assert line.tier == tier, (scene.key, tier, line.text)
                assert line.reaction, (scene.key, line.text)
                total += 1

        prompt = scenes.render_system_prompt(scene)
        assert "anchor_id" in prompt, scene.key
        assert "defense" not in prompt, scene.key
        assert '"reply"' not in prompt, scene.key
        assert "1. [5分·" in prompt, (scene.key, prompt[:200])
        assert scene.anchor(1).text in prompt, scene.key

        nums = [n for n, _ in scenes.numbered(scene)]
        assert nums == list(range(1, len(nums) + 1)), (scene.key, nums[:10])
        print(f"  {scene.key:<7} lines={sum(counts.values()):<2} tiers={counts}")

    # 极端空库也不能炸，必须给 tier5
    empty = replace(scenes.AGING, lines={})
    fallback = empty.fallback_anchor()
    assert fallback.tier == 5 and fallback.reaction, fallback
    assert fallback.text, fallback

    first = next(
        line for line in scenes.render_system_prompt(scenes.AGING).splitlines()
        if line.startswith("1. ")
    )
    print(f"总台词：{total}")
    print(f"首条样例：{first}")
    print("全部通过")


if __name__ == "__main__":
    main()
