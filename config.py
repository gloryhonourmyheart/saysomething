"""config.py — 常量配置：密钥、模型、提示词模板、台词库。

全部常量集中在这里，改 Prompt / 换模型 / 调台词都不用动业务代码。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ───────────────────────────── 环境变量 ─────────────────────────────
def _load_dotenv() -> None:
    """.env 存在就读进来，不覆盖已有环境变量。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "").strip()
BASE_URL: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip()
TEMPERATURE: float = float(os.environ.get("DEEPSEEK_TEMPERATURE", "0.3"))
TIMEOUT: int = int(os.environ.get("DEEPSEEK_TIMEOUT", "30"))
MAX_TOKENS: int = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "320"))

# 只带最近 4 条对话，防止上下文失控
HISTORY_WINDOW: int = 4

# ── 小抄（hints.py）─────────────────────────────
# 改写层的 temperature 要高于主对话：主对话要稳（0.3），改写要多样（0.7）。
COPY_TEMPERATURE: float = float(os.environ.get("SAY_COPY_TEMPERATURE", "0.7"))
# 卡上限用 52：Prompt 要 20-38 字，但实测模型常写到 45-50，直接拒掉会白白回落 L1。
COPY_MAX_CHARS: int = int(os.environ.get("SAY_COPY_MAX_CHARS", "52"))
# 实测偶发「返回空内容」：JSON 前的推理会吃掉 token，留 600 才稳（超出部分不单独计费）。
COPY_MAX_TOKENS: int = int(os.environ.get("SAY_COPY_MAX_TOKENS", "600"))
# 断网/省钱/测试开关：置 0 就只走 L1 规则层，完全不发请求。
COPY_MODEL: bool = os.environ.get("SAY_COPY_MODEL", "1").strip().lower() not in (
    "0", "false", "off", "no", ""
)


# ───────────────────────────── 业务常量 ─────────────────────────────
APP_NAME = "刺与泡沫"
APP_SUBTITLE = "关系演练场 · 在你开口之前，先替你试一次雷"

STRATEGIES = ("要求", "解释", "共情", "回避")

# 策略 → 防御分区间。旧版用它把模型给的分夹回边界内；
# 2026-11-17 重构后最终分数由「锚点台词档位 + constrain_defense」决定，
# 这张表只保留给旧测试/旧数据兼容，不再参与新链路，迁移完成后删除。
DEFENSE_BAND: dict[str, tuple[int, int]] = {
    "要求": (4, 5),
    "解释": (3, 3),
    "共情": (1, 2),
    "回避": (2, 3),
}

# Dashboard 的纵轴用同一套四字说法，避免和右栏/对话流出现两套话术。
DEFENSE_LABELS: dict[int, str] = {
    5: "暴怒反击",
    4: "明显防御",
    3: "试探犹豫",
    2: "松口保面子",
    1: "卸下防备",
}

# 给 C 端用户看的说法：分数是给评委的，情绪才是给人看的。
# 键与 DEFENSE_LABELS 完全对齐，渲染层只读这张表，打分逻辑一行没动。
MOOD: dict[int, tuple[str, str]] = {
    5: ("😡", "暴怒反击"),
    4: ("😠", "明显防御"),
    3: ("🤔", "试探犹豫"),
    2: ("🙂", "松口保面子"),
    1: ("🥺", "卸下防备"),
}

# 五格刻度的严格配色：5 最重（暗玫瑰红）→ 1 最轻（深绿）。
# UI（app.status_line）、图表（charts.BAR_COLORS）都只从这里取值，避免两套色板。
TIER_COLORS: dict[int, str] = {
    5: "#8C3B45",
    4: "#B8615B",
    3: "#D9B36A",
    2: "#8FAE8B",
    1: "#2F6B4F",
}

# 窄栏里的短反应（完整四字仍用 MOOD[tier][1] 给大卡片/复盘页）。
REACTION_SHORT: dict[int, str] = {
    5: "反击",
    4: "硬挡",
    3: "试探",
    2: "松口",
    1: "接受",
}

# 角色设定、台词库、Prompt 全部在 scenes.py 里（多场景），这里只留与场景无关的常量。
# 禁语是全局兜底，单个场景可以再加自己的（见 Scene.forbidden）。
FORBIDDEN_PHRASES = ("理解您的担忧", "我明白您的意思", "这是为你好", "您年纪大了", "建议您")

# 降级兜底：解析失败时用它，保证 UI 永远有话可显示
FALLBACK_REPLY = "……你让我想想。"
FALLBACK_DEFENSE = 3
FALLBACK_STRATEGY = "解释"

DISCLAIMER = (
    "本产品仅用于沟通情景仿真演练，不是心理咨询、家庭治疗或任何形式的医疗辅助工具，"
    "不提供诊断与治疗建议。回访功能在 Demo 阶段为本地模拟。"
)

