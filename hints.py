"""hints.py — 小抄：把用户刚说的那句，改写成对方能接住的一句。

为什么单独一层

原来 app.py 里的 COPY_LINE 是按场景写死的 4 句，不管用户说什么都是那一句——
因为它根本没有「生成」这一步，渲染层直接把常量塞进卡片。这里把它补上。

为什么不在渲染层生成

Streamlit 每次 rerun 都会重跑右栏：在 _side 里调模型，同一轮里小抄会变来变去，
还多一次延迟，断网就彻底没词。所以生成只发生在 app._submit，结果存进
Turn.copy_text（db 里有 copy 列），渲染层只读。

三层结构

L1  fallback_copy()  纯规则、零调用、断网可用。按档位挑句式，从「她最后一句」
                     和「用户最后一句」各摘一个真实片段嵌进去——换输入就会换句子。
L2  polish()         在 L1 之上追加一次独立改写调用（temperature 0.7）。与上一版
                     小抄做 2-gram 判重，不合格重试一次，仍不合格回落到 L1。
L3  调用方（app._submit）生成一次并落库，渲染层不再碰模型。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Sequence

import agent
import config
import scenes

_SPLIT_RE = re.compile(r"[，。！？；、,.!?;…\s]+")
_LEAD_RE = re.compile(
    r"^(?:妈|妈妈|爸|老板|许飞|老婆|老公|我知道|我觉得|其实|就是|那个|这个|嗯|唉|你看|可你|可是|那你)+"
)
_TAIL_RE = re.compile(r"(?:是吧|对不对|行不行|好不好)+$")
_NUM_RE = re.compile(r"(\d+|[一二三四五六七八九十百千万两]+)(块|元|楼|天|次|个月|年|趟|分钟|小时|平米|岁|点)")
_FACT_NUM_RE = re.compile(r"(\d+|[一二三四五六七八九十百千万两]+)(点|点钟|点半|块|元|楼|天|晚|次|个月|年|趟|分钟|小时|平米|岁)")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_QUOTE_RE = re.compile(r"[「」『』“”‘’\[\]【】\"']")

_STRIP_RE = re.compile(r"[\s，。！？、；：,.!?;:「」『』（）()\"'…]")


@dataclass(frozen=True)
class Suggestion:
    """一句小抄 + 两个给答辩用的证据字段。"""

    copy: str
    keeps: str = ""          # 保留了哪个真实细节
    drops: str = ""          # 删掉了哪个施压成分
    source: str = "L1"       # L1 / L2


# ───────────────────────────── 从原话里摘片段 ─────────────────────────────
def _segments(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT_RE.split(text or "") if s.strip()]


def her_clause(reply: str, limit: int = 14) -> str:
    """从她最后一句里挑一段最像「指控/结论」的短句。

    「你嫌我老了是吧？……用不着你操心」→ 「用不着你操心」
    「你要真惦记我，就不会一年只回来两趟」→ 「就不会一年只回来两趟」
    """
    best = ""
    for seg in _segments(reply):
        seg = _TAIL_RE.sub("", seg).strip()
        if 4 <= len(seg) <= limit and len(seg) > len(best):
            best = seg
    return best


def my_clause(text: str, limit: int = 14) -> str:
    """从用户最后一句里摘一段实词短句（去掉称呼和「我知道」这类开头）。"""
    for seg in _segments(text):
        seg = _LEAD_RE.sub("", seg).strip()
        seg = _TAIL_RE.sub("", seg).strip()
        if 4 <= len(seg) <= limit:
            return seg
    return ""


def _current_clause(text: str) -> str:
    """用户最后一句里最长的一段实词，作为这一轮的话题锚点。

    只从用户刚说的话里取，绝不回退到场景初始词（扶手/账/价码……）。
    「妈，别熬夜刷短剧了」→「别熬夜刷短剧」
    「妈，我同事他爸就是浴室滑倒摔骨折的」→「我同事他爸就是浴室滑倒摔骨折的」
    """
    best = ""
    for seg in _segments(text):
        seg = _LEAD_RE.sub("", seg).strip()
        seg = _TAIL_RE.sub("", seg).strip()
        seg = re.sub(r"[了吧啊呀呢]+$", "", seg).strip()
        if len(seg) > len(best):
            best = seg
    return best.strip("「」『』“”\"'")[:18]


def quote(text: str, limit: int = 16) -> str:
    """截一句原话给「Agent 插话」引用，超长就加省略号。"""
    clean = (text or "").strip()
    first = _segments(clean)[0] if _segments(clean) else clean
    return first if len(first) <= limit else first[:limit] + "…"


def number_phrase(text: str) -> str:
    """抓一个带量词的具体数字（八十块 / 5楼 / 三趟），用来替掉场景的通用细节。"""
    match = _NUM_RE.search(text or "")
    return match.group(0) if match else ""


def brief(scene: scenes.Scene) -> dict[str, str]:
    """场景词，缺什么用什么兜底——模板永远不会因为空字段崩。"""
    data = getattr(scene, "copy_brief", None) or {}
    return {
        "noun": str(data.get("noun") or scene.followup_noun or "这件事"),
        "ss": str(data.get("ss") or "先只做一小步"),
        "guess": str(data.get("guess") or "这事被我安排"),
        "detail": str(data.get("detail") or scene.soft_hint or "那一次"),
        "stake": str(data.get("stake") or "自己还能不能做主"),
    }


# ───────────────────────────── 判重 ─────────────────────────────
def has_quote(text: str) -> bool:
    """小抄里出现任何引号/方括号，都说明它在「引用」，不是人话。"""
    return bool(_QUOTE_RE.search(text or ""))


def _norm(text: str) -> str:
    return _STRIP_RE.sub("", text or "")


def _bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)}


def is_too_similar(a: str, b: str) -> bool:
    """开头 4 字相同，或字符 2-gram Jaccard > 0.4，就算复读。"""
    left, right = _norm(a), _norm(b)
    if not left or not right:
        return False
    if left[:4] == right[:4]:
        return True
    grams_a, grams_b = _bigrams(left), _bigrams(right)
    if not grams_a or not grams_b:
        return False
    return len(grams_a & grams_b) / len(grams_a | grams_b) > 0.4


def _grounded(text: str, *sources: str) -> bool:
    """小抄必须踩住原话：和「她的最后一句 / 用户的原话」至少共 2 个汉字。"""
    pool = set("".join(sources))
    hits = sum(1 for ch in set(_norm(text)) if ch in pool and "\u4e00" <= ch <= "\u9fff")
    return hits >= 2


# ───────────────────────────── L1：规则层 ─────────────────────────────
# 只写「怎么说话」，不写示例句——示例句一旦进模板，模型（和人）都会直接照抄。
#
# 不再用「」引用原话：真实聊天里没人会发「你说的话」这种格式。
# 模板只把用户最后一句里的实词 {m} 放进陈述句，读起来就是一句能直接发的话。
_BODY: dict[int, tuple[str, ...]] = {
    5: (
        "行，{m}。这事我不争了，是我没接住。",
        "你说得对，{m}。是我没顾上。",
        "是我急了，{m}。先按你说的来。",
    ),
    4: (
        "{m}。这事你定，我不安排。",
        "行，{m}。按你的来，我不催。",
        "是我心急了，{m}。要不要做你说了算。",
    ),
    3: (
        "{m}。你是不是觉得我没跟你商量？",
        "我想听你说，{m}。你到底怎么想？",
        "是我猜错了？{m}。你直接说。",
    ),
    2: (
        "行，{m}。先不弄，你点头我再动。",
        "{m}。先按你的来，不行再撤。",
        "我不动了，{m}。等你发话。",
    ),
    1: (
        "{m}。我记下了，下一步你定。",
        "好，{m}。我来办，你说了算。",
        "就按你说的，{m}。剩下的你定。",
    ),
}

_PLAIN: dict[int, tuple[str, ...]] = {
    5: (
        "行，{m}。这事我不争了，是我没接住。",
        "你说得对，{m}。是我没顾上。",
    ),
    4: (
        "{m}。这事你定，我不安排。",
        "行，{m}。按你的来，我不催。",
    ),
    3: (
        "{m}。你是不是觉得我没跟你商量？",
        "我想听你说，{m}。你到底怎么想？",
    ),
    2: (
        "行，{m}。先不弄，你点头我再动。",
        "{m}。先按你的来，不行再撤。",
    ),
    1: (
        "{m}。我记下了，下一步你定。",
        "好，{m}。我来办，你说了算。",
    ),
}

_DROP_NOTE = {
    "要求": "删掉了必须/替你决定",
    "解释": "删掉了讲道理/拿别人家举例",
    "共情": "删掉了要一个立刻答复",
    "回避": "删掉了把话咽回去",
}


def _seed(text: str) -> int:
    """让她的原话参与轮换：换了台词，后面的档位动作也跟着换，不会每轮同一句式。"""
    return sum(ord(ch) for ch in (text or "")[:6]) % 97


def _fit(templates: Sequence[str], fields: dict[str, str], start: int) -> str | None:
    """从模板里挑一句：字段齐、长度不超限，就返回。"""
    for offset in range(len(templates)):
        template = templates[(start + offset) % len(templates)]
        needed = _PLACEHOLDER_RE.findall(template)
        if any(not fields.get(key) for key in needed):   # 缺字段就跳过，不拼出「「」这话」
            continue
        text = template.format(**fields)
        if len(text) > config.COPY_MAX_CHARS:
            continue
        return text
    return None


def fallback_copy(
    scene: scenes.Scene,
    turns: Sequence,
    tier: int,
    *,
    last_copy: str = "",
) -> Suggestion:
    """L1：不调模型，按档位 + 用户这一句里的实词拼一句小抄。

    原则：话题只从「用户最后一句」和「对方最后一句」里取，绝不回退到
    copy_brief / scenario 的初始场景词。用户已经在聊短剧，小抄就只聊短剧；
    他/她这一句没提扶手，小抄里就不许出现扶手。
    """
    turns = list(turns or [])
    last = turns[-1] if turns else None
    her = her_clause(last.reply_text) if last else ""
    mine = _current_clause(last.user_text) if last else ""
    fields = {"h": her, "m": mine}
    like = max(1, min(5, int(tier or 3)))
    start = (len(turns) + _seed(her) + _seed(mine)) % 7
    drops = _DROP_NOTE.get(getattr(last, "strategy", ""), "删掉了施压的那半句")

    if her:
        bodies = _BODY.get(like, _BODY[4])
        for offset in range(len(bodies)):
            body = bodies[(start + offset) % len(bodies)]
            needed = _PLACEHOLDER_RE.findall(body)
            if any(not fields.get(key) for key in needed):
                continue
            text = body.format(**fields)
            if len(text) > config.COPY_MAX_CHARS or is_too_similar(text, last_copy):
                continue
            keeps = f"接住了{scene.speaker}说的：{her}"
            if "{m}" in body and mine:
                keeps += f" 和你的原话：{mine}"
            return Suggestion(copy=text, keeps=keeps, drops=drops)

    for offset in range(2):
        text = _fit(_PLAIN.get(like, _PLAIN[4]), fields, start + offset)
        if text and not is_too_similar(text, last_copy):
            return Suggestion(
                copy=text,
                keeps=f"用了你原话里的：{mine}" if mine else "只保留这一轮的语气",
                drops=drops,
            )

    # 两个人都没给出可引用的实词：给一句中性的、绝不硬套场景词。
    return Suggestion(
        copy="这次我先听你说，等你想说再说。",
        keeps="没有硬套场景细节",
        drops="删掉了全部施压",
        source="L1",
    )


def _scene_words(scene: scenes.Scene) -> tuple[str, ...]:
    """场景自带、但用户这一句没提就不该出现的词（用于 L2 幻觉校验）。"""
    data = getattr(scene, "copy_brief", None) or {}
    raw = [data.get("noun"), data.get("detail"), data.get("ss")]
    words: list[str] = []
    for value in raw:
        for part in re.split(r"[，。；、,;]", str(value or "")):
            part = part.strip()
            if len(part) >= 2:
                words.append(part)
    return tuple(dict.fromkeys(words))


def _scene_word_leak(scene: scenes.Scene, text: str, user_text: str) -> str:
    """返回第一个「小抄里有、但用户这一句没提」的场景词；没有返回空串。"""
    current = user_text or ""
    for word in _scene_words(scene):
        if word in text and word not in current:
            return word
    return ""


def _new_number(text: str, *sources: str) -> str:
    """返回小抄里新增、原话里没有的数字/时间；没有返回空串。

    模型最容易在这里编细节：「刷到两点多」「昨晚三点」——用户和对方都没说过，
    这种一句小抄就把整场对话变成假的了。
    """
    source = "".join(s or "" for s in sources)
    for match in _FACT_NUM_RE.finditer(text or ""):
        token = match.group(0)
        if token not in source:
            return token
    return ""


# ───────────────────────────── L2：一次改写调用 ─────────────────────────────
_SYSTEM = """你是「小抄生成器」。你只做一件事：把用户刚才那句没说好的话，改写成对方能接住的一句。

【禁止】
- 禁止通用安慰句、鸡汤、教科书句式：如「我理解你的感受」「别生气」「我是为你好」「大家都这样」
- 禁止只给空洞承认（例如「我知道你不容易」）却不接任何具体细节
- 禁止与【上一版小抄】在开头 6 个字或句式结构上相似——必须换一个新说法
- 禁止评价用户、说教、解释心理学概念
- 禁止新增事实、新增承诺、新增数字
- 绝对禁止在小抄中使用「」、[] 或任何引号去引用对方的话。必须用陈述句直接表达，或者用自然的短句复述。小抄要像用户能直接复制发出去的一句话，不能像台词引用。
- 只准围绕用户最后一句和对方最后一句里正在聊的事；禁止把话题拉回场景设定里的初始物件、地点或事件。用户这一句没提的词，你绝不能替对方提。

【这一档该怎么写】
{tier_rule}

【必须】
- 一句话，20-38 字，口语，像家里人真会说出口的；超过 40 字就自己重写
- 必须踩住一个真实细节：要么复述对方刚说的话（不加引号），要么复述用户原话里的具体事实
- 说完这句，对方能接一句，而不是被堵死
- 称呼、语气贴合【关系】

只输出 JSON，不要多余文字，两个字段都要短：
{{"copy": "一句话小抄（20-38 字）", "keeps": "保留了哪个细节（不超过 15 字）"}}"""

_TIER_RULE = {
    5: "对方在气头上：先接住对方的指控，不辩解、不解释、不反驳；可以承认对方话里的具体事实。",
    4: "对方在硬挡：撤掉「安排 / 命令」，把决定权还给对方；给一个可以撤销的小步。",
    3: "对方在试探：命名对方的顾虑，用问句，别替对方下定义。",
    2: "对方松口但要保面子：给台阶、留退路，落到具体动作，不催、不要答复。",
    1: "对方已卸下防备：只落到具体动作、时间或选择权。",
}


def _polish_messages(
    scene: scenes.Scene,
    turns: Sequence,
    tier: int,
    *,
    last_copy: str,
    retry: bool,
) -> list[dict[str, str]]:
    turns = list(turns or [])
    last = turns[-1] if turns else None
    recent = turns[-3:]
    numbered = "\n".join(
        f"{i}. {t.user_text}" for i, t in enumerate(recent, 1)
    ) or "（还没有）"
    system = _SYSTEM.format(tier_rule=_TIER_RULE.get(int(tier or 3), _TIER_RULE[3]))
    user = (
        f"【关系】{scene.dialogue}｜你面对的称呼是：{scene.role_name}\n"
        f"【人物底色，仅供理解，不要复述】{scene.mechanism}\n"
        f"【{scene.speaker}的状态】防御 {tier}/5（{config.DEFENSE_LABELS.get(int(tier or 3), '')}）；"
        f"{scene.speaker}最后一句：{getattr(last, 'reply_text', '')}\n\n"
        f"【用户最近 3 轮原话】\n{numbered}\n"
        f"（要改的是最后这条）\n\n"
        f"【上一版小抄（严禁相似）】{last_copy or '（还没有）'}"
    )
    if retry:
        user += "\n\n【上一次不合规】必须换一个开头，并且用陈述句复述对方刚说的一句或用户原话里的一个事实，不许加任何引号。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def polish(
    scene: scenes.Scene,
    turns: Sequence,
    tier: int,
    *,
    last_copy: str = "",
) -> Suggestion | None:
    """L2：一次独立改写。任何异常 / 不合规都返回 None，由调用方回落到 L1。"""
    if not agent.available():
        return None
    turns_list = list(turns or [])
    last = turns_list[-1] if turns_list else None
    her = her_clause(getattr(last, "reply_text", ""))
    mine = my_clause(getattr(last, "user_text", ""))

    started = time.perf_counter()
    for attempt in range(3):
        # 软截止：已经花了 12 秒就不再拖，直接回落到 L1；一次空返回仍值得再试。
        if time.perf_counter() - started > 12:
            return None
        try:
            raw = agent.chat_json(
                _polish_messages(scene, turns_list, tier, last_copy=last_copy, retry=attempt > 0),
                temperature=config.COPY_TEMPERATURE,
                max_tokens=config.COPY_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 - 小抄失败不该影响主流程
            # 超时说明网关已经卡住，再试一次只会让提交更慢，直接回落；
            # 空内容 / 脏 JSON 则值得再试一次（实测偶发，重试基本能拿到）。
            if "超时" in str(exc) or "timeout" in str(exc).lower():
                return None
            continue
        data = agent.parse_payload(raw) or {}
        text = str(data.get("copy") or "").strip()
        if not (8 <= len(text) <= config.COPY_MAX_CHARS):
            continue
        if has_quote(text):
            # 模型又写成「引用体」：直接废弃，不让它进 DB 和右栏。
            continue
        if any(phrase in text for phrase in config.FORBIDDEN_PHRASES):
            continue
        if is_too_similar(text, last_copy):
            continue
        if not _grounded(text, her, mine):
            continue
        if _scene_word_leak(scene, text, getattr(last, "user_text", "")):
            # 用户在聊短剧，模型却把「扶手」拉回来——这不是小抄，是幻觉。
            continue
        if _new_number(text, getattr(last, "user_text", ""), getattr(last, "reply_text", "")):
            # 编出一个「昨晚三点」这种具体时间，比空话更伤可信度。
            continue
        return Suggestion(
            copy=text,
            keeps=str(data.get("keeps") or "").strip()[:20],
            # drops 不让模型写：它会为了凑字段写很长，把 JSON 撑到截断。
            # 这一项本来就只做答辩证据，本地按策略算就是准的。
            drops=_DROP_NOTE.get(getattr(last, "strategy", ""), "删掉了施压的那半句"),
            source="L2",
        )
    return None


def suggest(
    scene: scenes.Scene,
    turns: Sequence,
    tier: int,
    *,
    last_copy: str = "",
    allow_model: bool = True,
) -> Suggestion:
    """对外只调这一个：先 L1，够格再叠一次 L2，失败就留着 L1。"""
    base = fallback_copy(scene, turns, tier, last_copy=last_copy)
    if allow_model and config.COPY_MODEL and int(tier or 3) >= 4:
        polished = polish(scene, turns, tier, last_copy=last_copy)
        if polished is not None:
            return polished
    return base
