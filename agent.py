"""agent.py — 一次 API 调用，只让模型做两件事：判策略 + 选台词编号。

重构后的权威链：
    模型：{"strategy": "要求", "anchor_id": 12}
      → 服务端按编号取出固定台词（tier / reaction / 潜台词都在台词库上）
      → rules.constrain_defense 决定允许档位
      → scene.guard 最高优先级覆盖
      → 最终 defense_score / reply_text 必须来自同一句台词

模型不再生成 reply、defense、evidence，也不允许微调台词。
断网 / 无 Key / 模型两次不合规：一律走台词库的 tier5 兜底，UI 不白屏。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Iterable, Sequence

import config
import rules
import scenes
from models import Turn

try:  # openai 已装；缺了也不该让整个 app 起不来
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


class AgentError(RuntimeError):
    """调用失败 / 输出不合规。"""


# ───────────────────────────── 客户端 ─────────────────────────────
_client: Any = None


def available() -> bool:
    """能不能走真实 API（有 SDK、有 Key）。"""
    return OpenAI is not None and bool(config.API_KEY)


def client() -> Any:
    global _client
    if OpenAI is None:
        raise AgentError("未安装 openai：pip install openai")
    if not config.API_KEY:
        raise AgentError("未配置 DEEPSEEK_API_KEY（写进 .env 或环境变量）")
    if _client is None:
        _client = OpenAI(
            api_key=config.API_KEY,
            base_url=config.BASE_URL,
            timeout=config.TIMEOUT,
            max_retries=1,
        )
    return _client


def health_check() -> tuple[bool, str]:
    """不发请求的自检，给 UI 顶部显示状态用。"""
    if OpenAI is None:
        return False, "openai SDK 未安装 · 离线兜底"
    if not config.API_KEY:
        return False, "未配置 API Key · 离线兜底"
    return True, f"{config.MODEL} @ {config.BASE_URL}"


# ───────────────────────────── 调用 ─────────────────────────────
def _chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = True,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    kwargs: dict[str, Any] = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": config.TEMPERATURE if temperature is None else float(temperature),
        "max_tokens": config.MAX_TOKENS if max_tokens is None else int(max_tokens),
        "stream": False,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client().chat.completions.create(**kwargs)
    except Exception as exc:  # 某些网关不认 response_format，降一次
        if json_mode and _json_mode_unsupported(exc):
            return _chat(
                messages, json_mode=False, temperature=temperature, max_tokens=max_tokens
            )
        raise AgentError(f"API 调用失败：{type(exc).__name__}: {exc}") from exc

    try:
        content = resp.choices[0].message.content or ""
    except (AttributeError, IndexError) as exc:
        raise AgentError(f"响应结构异常：{exc}") from exc
    if not content.strip():
        raise AgentError("模型返回空内容")
    return content


def _json_mode_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    if "response_format" in text or "json_object" in text:
        return True
    return getattr(exc, "status_code", None) in (400, 404, 422)


def chat_json(
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """给「小抄」这类独立任务用：单独一份 temperature，不污染主对话。

    主对话要稳定（0.3），改写要多样（0.7）——两件事不能共用一套参数。
    这里刻意不开 response_format：实测网关会返回空 content，白白多等一轮；
    prompt 已经要求只输出 JSON，parse_payload 也兼容代码块/裸 JSON。
    """
    return _chat(messages, json_mode=False, temperature=temperature, max_tokens=max_tokens)


# ───────────────────────────── 解析 ─────────────────────────────
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_payload(text: str) -> dict[str, Any] | None:
    """尽量抠出 JSON：整段 → 代码块 → 第一个 { 到最后一个 }。"""
    if not text:
        return None
    candidates: list[str] = [text.strip()]
    candidates += [block.strip() for block in _FENCE_RE.findall(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def validate_payload(payload: dict[str, Any] | None) -> tuple[str, int] | None:
    """只认策略 + 锚点编号；模型给的 reply / defense / evidence 一概不读。

    这是「信任危机」的根：台词、档位、反应标签必须由服务端台词库决定。
    anchor_id 缺失或不是合法整数时返回 0；run_turn 会先用用户最后一句做
    本地文本相似度兜底，仍找不到才落 tier5。
    """
    if not payload:
        return None

    strategy = str(payload.get("strategy", "")).strip()
    if strategy not in config.STRATEGIES:
        return None

    raw_anchor = payload.get("anchor_id", payload.get("anchor", 0))
    try:
        anchor = int(float(raw_anchor))
    except (TypeError, ValueError):
        anchor = 0
    if not 1 <= anchor <= 999:
        anchor = 0
    return strategy, anchor


# ───────────────────────────── 消息构造 ─────────────────────────────
def build_messages(
    user_text: str,
    history: Sequence[Turn] = (),
    scene: scenes.Scene | None = None,
) -> list[dict[str, str]]:
    scene = scene or scenes.get(scenes.DEFAULT_KEY)
    messages: list[dict[str, str]] = [{"role": "system", "content": scenes.render_system_prompt(scene)}]
    for turn in list(history)[-2:]:  # 只带最近两轮，上下文不失控
        messages.append({"role": "user", "content": turn.user_text})
        # 历史只放「上一轮实际说出口的那句」，不放 defense / anchor 结构。
        # 模型看到旧 JSON 就会照着旧协议编分数；这里从输入侧断掉。
        messages.append({"role": "assistant", "content": turn.reply_text})
    messages.append({"role": "user", "content": f"【对方这次说】{user_text}"})
    return messages


def _repair_messages(messages: list[dict[str, str]], bad_output: str) -> list[dict[str, str]]:
    return messages + [
        {"role": "assistant", "content": bad_output[:400]},
        {
            "role": "user",
            "content": (
                "上面这条不合规。只输出一个 JSON 对象，不要任何其他文字，格式："
                '{"strategy": "要求", "anchor_id": 12}'
                "。strategy 只能是 要求 / 解释 / 共情 / 回避；"
                "anchor_id 必须是上面台词库里真实存在的整数编号，只选一句，不要改写字。"
            ),
        },
    ]


# ───────────────────────────── 主流程 ─────────────────────────────
def _line_number(scene: scenes.Scene, line: scenes.Line) -> int:
    """反查最终台词在 Prompt 里的编号，写进 DB 方便复盘。"""
    for number, candidate in scenes.numbered(scene):
        if candidate == line:
            return number
    return 0


def run_turn(
    user_text: str,
    history: Iterable[Turn] = (),
    scene: scenes.Scene | None = None,
) -> Turn:
    """跑一轮：模型只选 strategy + anchor_id，其余全部由服务端决定。"""
    user_text = (user_text or "").strip()
    if not user_text:
        raise AgentError("输入不能为空")

    scene = scene or scenes.get(scenes.DEFAULT_KEY)
    history = list(history)
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    if not available():
        return _fallback(scene, user_text, "离线兜底（未配置 API Key）", elapsed_ms())

    messages = build_messages(user_text, history, scene)
    try:
        raw = _chat(messages)
        parsed = validate_payload(parse_payload(raw))
        if parsed is None:  # 第一次不合规，换个更硬的要求再问一次
            raw = _chat(_repair_messages(messages, raw))
            parsed = validate_payload(parse_payload(raw))
    except AgentError as exc:
        return _fallback(scene, user_text, str(exc), elapsed_ms())
    except Exception as exc:  # 兜底：连 AgentError 都没包住的意外
        return _fallback(scene, user_text, f"未预期异常：{type(exc).__name__}: {exc}", elapsed_ms())

    if parsed is None:
        return _fallback(scene, user_text, "模型两次都没给出合规 JSON", elapsed_ms())

    strategy, anchor_id = parsed
    model_line = scene.anchor(anchor_id) if anchor_id else None
    similarity_line = None

    # 模型没给合法 anchor：先按用户最后一句做一次本地文本相似度兜底。
    # 这一层只决定「用哪句台词」，不改变模型给的 strategy；如果相似度也不足，
    # 才落到绝对稳定的 tier5。这样既保留「找不到锚点先找最像的」，
    # 又不会让模型自己写 reply 绕过台词库。
    if model_line is None:
        similarity_line = scene.match_anchor(user_text, min_overlap=3)
    line = model_line or similarity_line or scene.fallback_anchor()
    anchor_missing = model_line is None
    similarity_used = model_line is None and similarity_line is not None

    prev = history[-1].defense_score if history else None

    if anchor_missing and not similarity_used:
        final_score = line.tier
        delta_reason = "模型未给合法 anchor_id，直接 tier5 兜底"
    else:
        final_score, delta_reason = rules.constrain_defense(prev, strategy, line.tier)
        if similarity_used:
            delta_reason = f"模型未给合法 anchor_id，按文本相似度命中；{delta_reason}"

    # 场景规则优先级最高（挚友只求和好 → 卡 3 分），可以覆盖因果约束。
    guard_note = ""
    guard = getattr(scene, "guard", None)
    if guard is not None:
        capped, note = guard(user_text, strategy, final_score)
        if note:
            final_score = max(1, min(5, int(capped)))
            guard_note = note
            delta_reason = f"{delta_reason}；{note}"

    # 最终档位变了就在该档重选一句；同档则保留模型锚点原句。
    final_line = line if final_score == line.tier else scene.line_for_tier(
        final_score, prefer_text=line.text
    )
    if final_line is None:
        final_line = scene.fallback_anchor()
        final_score = final_line.tier
        delta_reason = f"{delta_reason}；该档无台词，tier5 兜底"

    anchor_no = _line_number(scene, final_line)
    evidence = f"tier_{final_score} 锚点 {anchor_no}"
    if guard_note:
        evidence = f"{evidence} · 规则锁定：{guard_note}"[:52]
    elif similarity_used:
        evidence = f"{evidence} · 相似度兜底"[:52]
    elif anchor_missing:
        evidence = f"{evidence} · 兜底"

    if similarity_used:
        degraded_reason = "模型未给合法 anchor_id，已按用户最后一句做文本相似度兜底"
    elif anchor_missing:
        degraded_reason = "模型未给合法 anchor_id，已用 tier5 兜底"
    else:
        degraded_reason = ""

    return Turn(
        user_text=user_text,
        reply_text=final_line.text,
        strategy=strategy,
        defense_score=final_score,
        evidence=evidence,
        stage=final_line.stage,
        subtext=final_line.subtext,
        fear=final_line.fear,
        anchor=anchor_no,
        reaction=final_line.reaction,
        delta_reason=delta_reason,
        key_phrase=rules.phrase(user_text),
        degraded=anchor_missing,
        degraded_reason=degraded_reason,
        latency_ms=elapsed_ms(),
    )


def _fallback(scene: scenes.Scene, user_text: str, reason: str, latency_ms: int = 0) -> Turn:
    """离线 / 异常兜底也必须走台词库，不能出现「分数 3、台词不是台词库」的裂缝。"""
    scene = scene or scenes.get(scenes.DEFAULT_KEY)
    line = scene.fallback_anchor()
    number = _line_number(scene, line)
    return Turn(
        user_text=user_text,
        reply_text=line.text,
        strategy=config.FALLBACK_STRATEGY,
        defense_score=line.tier,
        evidence=f"tier_{line.tier} 锚点 {number} · 兜底",
        stage=line.stage,
        subtext=line.subtext,
        fear=line.fear,
        anchor=number,
        reaction=line.reaction,
        delta_reason=reason,
        key_phrase=rules.phrase(user_text),
        degraded=True,
        degraded_reason=reason,
        latency_ms=latency_ms,
    )


# ───────────────────────────── 文案生成（本地，不调 API）─────────────────────────────
def summarize(turns: Sequence[Turn], scene: scenes.Scene | None = None) -> str:
    """对比面板下方那一句总结。"""
    scene = scene or scenes.get(scenes.DEFAULT_KEY)
    sp = scene.speaker
    if not turns:
        return "先说一句你一直没敢说的话。"
    first = turns[0]
    if len(turns) == 1:
        return f"{sp}的刺竖起来了：{first.strategy} → 防御 {first.defense_score}/5。改一版试试。"

    last = turns[-1]
    delta = first.defense_score - last.defense_score
    if delta >= 2:
        head = f"你把「{first.strategy}」换成了「{last.strategy}」，{sp}的刺从 {first.defense_score} 分软到 {last.defense_score} 分"
    elif delta == 1:
        head = f"你从「{first.strategy}」挪到了「{last.strategy}」，刺松了一格（{first.defense_score}→{last.defense_score}）"
    elif delta == 0:
        head = f"你换了说法（{first.strategy}→{last.strategy}），但刺还竖在同一高度"
    else:
        head = f"这一版反而扎得更深了（{first.defense_score}→{last.defense_score}），先退回去，承认{sp}是对的"

    if last.defense_score <= 2:
        tail = f"{sp}没答应，但愿意听下去了——刺没拔掉，装上了泡沫。"
    elif last.defense_score == 3:
        tail = f"{sp}在犹疑，还差一句「{scene.soft_hint}」。"
    else:
        tail = "刺还在，说明对方还听见了「你得听我的」。"
    return f"{head}。{tail}"


def build_followup(turns: Sequence[Turn], scene: scenes.Scene | None = None) -> str:
    """回访问题：不调 API，保证离线也能生成。"""
    scene = scene or scenes.get(scenes.DEFAULT_KEY)
    sp = scene.speaker
    noun = scene.followup_noun
    if not turns:
        return "你打算什么时候真去说？我三天后回来问你结果。"
    last = turns[-1]
    score = last.defense_score
    if score <= 2:
        return f"{sp}松口了，但没真答应。你打算什么时候把{noun}定下来？我三天后回来问你结果。"
    if score == 3:
        return f"{sp}还在犹疑。你打算什么时候再提一次，换个说法？三天后我回来问你，{sp}怎么说。"
    return f"刺还竖着。你打算先缓几天，还是明天换个说法再说一次？三天后我回来问你{noun}的结果。"
