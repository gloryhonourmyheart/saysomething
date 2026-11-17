"""scenes.py — 多场景配置引擎。

一个 Scene 就是一套「角色 + 台词库 + 案例 + 文案」，改了它，Prompt、案例按钮、
对比面板的称呼、回访话术全部跟着变。业务代码（agent/app）里不再出现任何
具体角色的字眼，这是多场景能立住的前提。

Prompt 不是手写的长字符串，而是**由 Scene 数据渲染出来**的：
档位说明、台词编号、潜台词、禁语、JSON 示例都从 lines / forbidden 里取，
以后队友加一句台词，提示词里的编号和约束自动同步，不会对不上。

适老化场景的台词库来自队友的 lines(1).json（content/lines_aging.json），
每条台词带 fear / act / stage / subtext / trigger——其中 subtext（潜台词）会在
UI 上以灰色斜体显示在 AI 回弹下方，这是本产品最直观的「洞察感」来源。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import config

CONTENT_DIR = Path(__file__).resolve().parent / "content"
AGING_LINES = CONTENT_DIR / "lines_aging.json"


# ───────────────────────────── 数据结构 ─────────────────────────────
@dataclass(frozen=True)
class AnchorLine:
    """一句台词 = 一个锚点：文本、固定档位、固定反应标签。

    2026-11-17 重构后，模型只返回 anchor_id，不再自己写 reply / defense。
    因此这句台词就是「唯一权威」：tier 和 reaction 都绑在这里，服务器按编号取。
    字段顺序保留 tier, text，兼容旧代码里的 Line(5, "...") 写法。
    """

    tier: int
    text: str
    reaction: str = ""    # 固定标签：暴怒反击 / 明显防御 / 试探犹豫 / 松口保面子 / 卸下防备
    stage: str = ""       # 动作：手里的活停下，抬头看你
    subtext: str = ""     # 潜台词：她在护的不是扶手，是这个家谁说了算
    fear: str = ""        # 怕什么：主权 / 脆弱 / 负担
    act: str = ""         # 怎么防：翻旧账 / 反问 / 堵回
    trigger: str = ""     # 什么话会引出这句
    group: str = "防御"   # 防御 / 非防御

    def __post_init__(self) -> None:
        # 旧台词库没有 reaction 字段：按档位自动补全，保证每句都有固定标签。
        if not self.reaction:
            object.__setattr__(self, "reaction", config.MOOD.get(self.tier, ("", ""))[1])

    @property
    def has_meta(self) -> bool:
        return bool(self.subtext or self.stage)


# 兼容旧名字：业务代码/测试里仍然可以写 scenes.Line(...)。
Line = AnchorLine


def _bank(raw: dict[int, tuple[str, ...]]) -> dict[int, tuple[Line, ...]]:
    """老式写法（只有台词文本）→ Line。"""
    return {
        tier: tuple(Line(tier=tier, text=text) for text in texts)
        for tier, texts in raw.items()
    }


@dataclass(frozen=True)
class Scene:
    key: str
    label: str                     # 场景名，落库用这个
    short: str                     # 侧栏历史里的缩写
    role_name: str                 # 模型扮演谁：妈 / 他 / 老板
    speaker: str                   # 第三人称：她 / 他 / TA
    dialogue: str                  # 「一场真实的母女对话」
    scenario: str                  # 一句话场景说明
    persona: str                   # 【你是谁】
    mechanism: str                 # 【你的防御机制】
    lines: dict[int, tuple[Line, ...]]       # 档位 → 台词（1-5）
    cases: tuple[tuple[str, str], ...]       # (按钮名, 文本)
    empty_line: str                # 右侧空状态
    followup_noun: str             # 回访话术里的具体事
    forbidden: tuple[str, ...] = config.FORBIDDEN_PHRASES
    soft_hint: str = "我不是嫌你老"          # 「还差一句」里的那句示范，跟场景走
    guard: Callable[[str, str, int], tuple[int, str]] | None = None  # 场景专属规则
    extra_rule: str = ""                     # 写进 Prompt 的场景专属约束
    # 小抄层（hints.py）用的场景词：noun=要做的那件事 / ss=可撤销的小步 /
    # guess=她真正怕的 / detail=能被引用的具体细节。写在这里，业务代码里就不出现角色字眼。
    copy_brief: dict[str, str] = field(default_factory=dict)

    @property
    def max_tier(self) -> int:
        return max(self.lines) if self.lines else 5

    def lines_of(self, tier: int) -> tuple[Line, ...]:
        return self.lines.get(tier, ())

    def anchor(self, anchor_id: int) -> Line | None:
        """按 Prompt 里的编号取锚点台词（模型只返回 anchor_id）。"""
        try:
            wanted = int(anchor_id)
        except (TypeError, ValueError):
            return None
        for number, line in numbered(self):
            if number == wanted:
                return line
        return None

    def line_at(self, index: int) -> Line | None:
        """旧名字，保留给现有测试/旧代码；等价于 anchor(index)。"""
        return self.anchor(index)

    def match_anchor(self, text: str, *, min_overlap: int = 2) -> Line | None:
        """模型没给编号时的全库兜底：找一句和候选文本字符重叠最多的台词。

        和旧的 match_line 不同：这里不先限定档位，也不会在零重叠时硬塞一句；
        重叠不足 min_overlap 就返回 None，由调用方走 tier5 兜底。
        """
        candidate = set(text or "")
        if not candidate:
            return None
        best: Line | None = None
        best_score = 0
        for _number, line in numbered(self):
            score = len(candidate & set(line.text))
            if score > best_score:
                best, best_score = line, score
        return best if best_score >= min_overlap else None

    def match_line(self, tier: int, reply: str) -> Line | None:
        """旧接口：在该档位里找一句跟回弹最像的（兼容旧测试/旧代码）。"""
        pool = self.lines_of(tier) or tuple(
            line for lines in self.lines.values() for line in lines
        )
        if not pool:
            return None
        reply_chars = set(reply or "")
        return max(pool, key=lambda line: len(reply_chars & set(line.text)))

    def fallback_anchor(self) -> Line:
        """绝对稳定的 tier5 兜底：找不到任何台词也不抛异常。

        优先用该场景真实的 tier5 台词；如果台词库为空/缺 tier5，就返回一句
        通用的 tier5 锚点，保证「模型两次都不合规」时 UI 仍有话可说。
        """
        tier5 = self.lines_of(5)
        if tier5:
            return tier5[0]
        for tier in range(4, 0, -1):
            lines = self.lines_of(tier)
            if lines:
                # 极端情况：场景没有 5 分台词。抬高到 5 分，仍返回一句真实台词，
                # 避免 agent 层拿到 None 后再编一句脱离台词库的话。
                first = lines[0]
                return AnchorLine(
                    tier=5,
                    text=first.text,
                    reaction=config.MOOD[5][1],
                    stage=first.stage,
                    subtext=first.subtext,
                    fear=first.fear,
                    act=first.act,
                    trigger=first.trigger,
                    group=first.group,
                )
        return AnchorLine(tier=5, text="……你让我想想。", reaction=config.MOOD[5][1])

    def line_for_tier(self, tier: int, *, prefer_text: str = "") -> Line | None:
        """因果约束把分数改到另一档时，在同一档里重选一句。

        prefer_text 用来保留模型原锚点的语气：同档里选和它最像的一句；
        没有候选就取该档第一句。该档为空返回 None，由调用方走 tier5 兜底。
        """
        try:
            wanted = max(1, min(5, int(tier)))
        except (TypeError, ValueError):
            return None
        pool = self.lines_of(wanted)
        if not pool:
            return None
        if prefer_text:
            prefer = set(prefer_text)
            return max(pool, key=lambda line: len(prefer & set(line.text)))
        return pool[0]


# ───────────────────────── 适老化台词库（队友版，38 句）─────────────────────────
def _load_aging_lines() -> dict[int, tuple[Line, ...]]:
    try:
        raw = json.loads(AGING_LINES.read_text(encoding="utf-8"))
    except Exception:
        return {}
    bucket: dict[int, list[Line]] = {}
    for item in raw.get("lines", []):
        try:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            tier = max(1, min(5, int(item.get("tier") or 3)))
            bucket.setdefault(tier, []).append(
                Line(
                    tier=tier,
                    text=text,
                    stage=str(item.get("stage") or "").strip(),
                    subtext=str(item.get("subtext") or "").strip(),
                    fear=str(item.get("fear") or "").strip(),
                    act=str(item.get("act") or "").strip(),
                    trigger=str(item.get("trigger") or "").strip(),
                    group=str(item.get("group") or "防御").strip(),
                )
            )
        except Exception:      # 单条脏数据不该让整个台词库失效
            continue
    return {tier: tuple(lines) for tier, lines in bucket.items()}


_AGING_FALLBACK: dict[int, tuple[Line, ...]] = {
    5: (Line(5, "你嫌我老了是吧？我在这住了几十年，用不着你操心。"), Line(5, "我还没老到要人管的地步，你少咒我。")),
    4: (Line(4, "我自己的身子我自己清楚，用不着你操心。"),),
    3: (Line(3, "你说得轻巧，你一个月回来几趟？"), Line(3, "又是听谁说的？")),
    2: (Line(2, "就你事多……装就装吧，别整天念叨。"),),
    1: (Line(1, "那你说咋弄？别整太贵的。"),),
}

AGING = Scene(
    key="aging",
    label="适老化改造",
    short="适老化",
    role_name="妈",
    speaker="她",
    dialogue="母女对话",
    scenario="女儿想让 68 岁独居的母亲在浴室装一个扶手。",
    persona=(
        "68 岁，独居在没电梯的 6 楼老小区，纺织厂退休。女儿住城东，一个月回来两趟。\n"
        "自尊心强，最恨被当成老糊涂，也不认为自己需要被「改造」。"
    ),
    mechanism="被当成「老了、不中用了」就立刻竖起刺：先否认需要，再把话推回给对方（你不常回来）。",
    lines=_load_aging_lines() or _AGING_FALLBACK,
    cases=(
        ("① 刺竖起来", "妈，浴室必须装扶手，这个没得商量。"),
        ("② 装上泡沫", "妈，我知道你觉得自己能行。我上次看到浴室地上有水，是真的怕你滑。能不能先只装一个扶手，你不喜欢随时拆。"),
        ("③ 边界案例", "妈，我同事他爸就是浴室滑倒摔骨折的。"),
    ),
    empty_line="她正在客厅坐着，电视声开得很大。<br>你一开口，她就会知道你想干什么。",
    followup_noun="扶手的事",
    copy_brief={
        "noun": "扶手",
        "ss": "先只装一个，你不喜欢随时拆",
        "guess": "装了就等于承认自己老了",
        "detail": "浴室地上那摊水",
        "stake": "这个家谁说了算",
    },
)

MONEY = Scene(
    key="money",
    label="伴侣谈钱",
    short="谈钱",
    role_name="他",
    speaker="他",
    dialogue="夫妻对话",
    scenario="妻子想和回避型丈夫把每月开销与还贷的分摊说清楚。",
    persona=(
        "34 岁，互联网公司中层，最近在赶一个大项目，天天加班到十一点。\n"
        "怕被说「没本事」「不顾家」，所以一谈钱就先把自己摆到受害者的位置。"
    ),
    mechanism="回避型：冷战、转移话题、用「忙」「累」把话推开；不正面拒绝，也不给具体数字。",
    lines=_bank({
        5: ("又来了。钱钱钱，你眼里还有别的吗？", "我上班已经够累了，回家还得被你审。"),
        4: ("这事改天再说。", "我最近很忙，你别逼我。"),
        3: ("我加班不也是为了这个家？你怎么不看看我付出了多少。", "你要真觉得我不行，那这个家你自己管。"),
        2: ("……那你说个数吧。", "行，这个月我少抽点烟。"),
        1: ("那咱们定个数，每个月存多少，你说了算。", "我明天把工资卡给你看，行了吧。"),
    }),
    cases=(
        ("① 刺竖起来", "这个月的钱你必须跟我说清楚，工资卡到底还剩多少。"),
        ("② 装上泡沫", "我知道你最近很累，我不是要查你。我只是怕月底又为钱吵一次。能不能这周抽十分钟，我们把这个月的账摊开看一眼？"),
        ("③ 边界案例", "我同事他们夫妻俩每个月都记账，从来没为钱红过脸。"),
    ),
    empty_line="他在沙发上刷手机，嗯了一声就算回应了。<br>你知道一谈钱，他就会开始找借口。",
    followup_noun="钱的事",
    soft_hint="我不是来查你的",
    copy_brief={
        "noun": "这个月的账",
        "ss": "这周抽十分钟，只看一眼",
        "guess": "被我当成不顾家的那个人",
        "detail": "月底那次吵架",
        "stake": "自己是不是没本事",
    },
)
# 每句台词背后的心理（和台词一一对应，顺序不能乱）
for _tier, _texts in _bank({
    5: ("他听见的不是账，是「你不够格」", "先摆出「累」，就不用面对数字"),
    4: ("拖，是他最熟练的拒绝", "把要求翻译成逼迫，他就成了受害者"),
    3: ("用付出换豁免权", "把问题升级成威胁，逼你先退"),
    2: ("松口了，但把决定权推回给你", "用一个小的让步，换整件事到此为止"),
    1: ("他终于愿意谈数字了，但还要留一点面子", "「行了吧」是让步，也是委屈"),
}).items():
    MONEY.lines[_tier] = tuple(
        Line(t.tier, t.text, subtext=sub.text) for t, sub in zip(MONEY.lines[_tier], _texts)
    ) if _tier in MONEY.lines else MONEY.lines[_tier]

SALARY = Scene(
    key="salary",
    label="职场谈加薪",
    short="谈加薪",
    role_name="老板",
    speaker="他",
    dialogue="上下级对话",
    scenario="员工想跟控制型老板谈一次调薪，老板习惯画大饼和 PUA。",
    persona=(
        "45 岁，创业公司老板，公司三十来人，今年确实紧。\n"
        "习惯用「格局」「状态」「大家都在扛」压人，从不直接说不，而是把你的要求变成对他的忠诚考验。"
    ),
    mechanism="控制型：先质疑你的状态和价值，再画大饼（明年、下季度、第一个想到你），最后把要求变成考验。",
    lines=_bank({
        5: ("你现在这个状态，跟我谈加薪？", "你觉得自己值这个价吗？"),
        4: ("公司今年什么情况你不知道？大家都在扛。", "先把格局打开，别老盯着眼前这点。"),
        3: ("再等等，明年这个位置我第一个想到你。", "你先把手上那摊子做完，我们再说。"),
        2: ("这样，下季度调薪窗口我给你报上去。", "行，我记下了，给你留个名额。"),
        1: ("涨是能涨，幅度我尽量争取，你先把项目干漂亮。", "可以，下个月起调一档，别对外说。"),
    }),
    cases=(
        ("① 刺竖起来", "老板，我这个薪资明显低于市场价，必须调。"),
        ("② 装上泡沫", "老板，我知道公司今年不容易。这两个月我手上多了两个项目，我想跟您对一下我这个岗位的价码，看什么时候能调。"),
        ("③ 边界案例", "我查过招聘网站，同样岗位市场价是我现在的 1.5 倍。"),
    ),
    empty_line="老板在看报表，头都没抬。<br>你说「想聊聊」，他会先问你项目进度。",
    followup_noun="涨薪的事",
    soft_hint="我不是在质疑您的判断",
    copy_brief={
        "noun": "价码",
        "ss": "先只对这一次，别的先不谈",
        "guess": "我只看眼前这点钱",
        "detail": "这两个月多接的项目",
        "stake": "自己的判断被当成压价",
    },
)
for _tier, _texts in _bank({
    5: ("先质疑你的状态，就不用谈钱", "把定价权抢回他手里"),
    4: ("用集体牺牲压住个人诉求", "「格局」是拒绝的礼貌说法"),
    3: ("用未来的承诺，换你现在的沉默", "把对话变成任务考核"),
    2: ("给了时间点，但没给数字", "「记下了」= 不承诺，「名额」= 可以没有"),
    1: ("答应了，但绑上了条件", "给了，但要你替他保密"),
}).items():
    SALARY.lines[_tier] = tuple(
        Line(t.tier, t.text, subtext=sub.text) for t, sub in zip(SALARY.lines[_tier], _texts)
    ) if _tier in SALARY.lines else SALARY.lines[_tier]


# ───────────────────────── 挚友场景的专属规则（本地执行，不靠模型自觉）─────────────────────────
# 只丢一句「我们和好吧」时，防御分卡在 3 分不动：
# 因为「和好」在她耳朵里只是「赶紧结束这场对话」，不是「我看见我哪里让你难受了」。
# 要掉到 1-2 分，用户得把话说明白：讲事实 + 说感受 + 提需求（非暴力沟通三件套）。
_PLEA_RE = re.compile(r"和好|和解|翻篇|别生气|不生气|原谅|消消气|算我错|咱俩算了吧|还能做朋友")
_FACT_RE = re.compile(r"那天|那次|上次|当时|我说|我说了|因为|那顿饭|那句话|那件事|这几天|上周")
_FEEL_RE = re.compile(
    r"难受|难过|委屈|害怕|怕你|在意|后悔|内疚|不舒服|伤心|失眠|睡不着|心里堵|不好受"
)
_NEED_RE = re.compile(r"我想|我希望|能不能|可不可以|我需要|我们能不能|要不我们|给我(十分钟|点时间|一次机会)")

BESTIE_GUARD_NOTE = "只求和好不算交底：讲事实 + 说感受 + 提需求，分才动"


def bestie_guard(user_text: str, strategy: str, defense: int) -> tuple[int, str]:
    """挚友场景专属：只求和好、不交底 → 防御卡在 3 分（既不更高，也不更低）。"""
    text = user_text or ""
    if not _PLEA_RE.search(text):
        return defense, ""                 # 没求和好，按模型给的分数走
    explained = bool(_FACT_RE.search(text))
    felt = bool(_FEEL_RE.search(text))
    asked = bool(_NEED_RE.search(text))
    if explained and felt and asked:
        return defense, ""                 # 事实 + 感受 + 需求都说了，该掉就掉
    return 3, BESTIE_GUARD_NOTE


BESTIE = Scene(
    key="bestie",
    label="挚友冷战",
    short="挚友",
    role_name="许飞",
    speaker="他",
    dialogue="十年好友的对话",
    scenario="十年友情，不该死于不会说话。这次，换你来主动把话说明白。",
    persona=(
        "许飞，和用户（厉娜）认识十年，一起熬过毕业、换工作、失恋。表面洒脱，回避型依恋，嘴硬心软。\n"
        "最受不了被当成「斤斤计较的人」，一被戳到就用气话把人推开；推完自己更难受，但绝不先低头。\n"
        "防御高时把「随便你吧」「算了吧」挂在嘴边；防御低时才会漏出真心话：「我其实也不想这样」。"
    ),
    mechanism="反击 + 冷战：先说反话（反正你觉得我不够好）、翻旧账；被追问就退回「随便你吧」把话堵死；"
              "只有听到具体事实、真实感受和一个明确的请求，才会认一步、给台阶。",
    lines={
        5: (
            Line(5, "行了，反正你一直觉得我不够好。", subtext="用自贬逼你先道歉",
                 act="自贬", trigger="你上来就指责他小题大做，或把「不就一顿饭」说出口"),
            Line(5, "别解释了，反正你也不会听。", subtext="先把门关死，省得再被丢下",
                 act="堵回", trigger="你开始长篇解释，却一句也没问他那天怎么想"),
        ),
        4: (
            Line(4, "我没事，我能有什么事。", subtext="「没事」= 很有事，等你再问一次",
                 act="嘴硬", trigger="你问「你还在生气吗」，语气像在确认他有没有小题大做"),
            Line(4, "你忙你的，不用管我。", subtext="把门关上，但留了一条缝",
                 act="退开", trigger="你提到自己最近很忙，或说「改天再聊」"),
        ),
        3: (
            Line(3, "随便你吧，我都行。", subtext="冷漠是最省力的反击",
                 act="敷衍", trigger="你只说「我们和好吧」，没讲清你哪里做错了"),
            Line(3, "算了吧，不想说了。", subtext="他其实想说，只是不想先低头",
                 act="撤回", trigger="你追问「你到底想怎样」，却没说自己那天的感受"),
        ),
        2: (
            Line(2, "你以前不是这样的啊……", subtext="开始回忆，就是开始松动",
                 act="回望", trigger="你说出那天具体做了什么，并承认自己当时在赌气"),
            Line(2, "……你到底怎么想的，说清楚。", subtext="他给了台阶，也给了期限",
                 act="试探", trigger="你讲了自己的感受，但还没说想怎么办"),
        ),
        1: (
            Line(1, "那天我也不对，话说重了。", subtext="先认一步，是他能给出的最大诚意",
                 act="认一步", trigger="你说清事实、感受，并明确提出「想聊十分钟」"),
            Line(1, "行吧，坐下说，别站着。", subtext="坐下 = 愿意听完",
                 act="给台阶", trigger="事实、感受、请求三件都说全了，还给他留了退路"),
        ),
    },
    cases=(
        # 案例名从「你」的视角写（你要说出的话），不用刺/泡沫的产品隐喻——
        # 冷战语境里「刺竖起来」像舞台提示，不像按钮要发的内容（2026-11-17 定稿）。
        ("① 冷战开场", "你至于吗？不就一件小事，我道歉了还不行？"),
        ("② 把话说清", "那天我说「你随便」，其实是在赌气。回去之后我一直挺难受的，也没敢找你。我想把话说清楚，你愿意听十分钟吗？"),
        ("③ 边界案例", "我们都认识十年了，就因为一顿饭的事，值得吗？"),
    ),
    empty_line="（沉默了很久）……那天的事，你还有什么想说的吗？",
    followup_noun="你们那件事",
    soft_hint="我不是来争对错的",
    guard=bestie_guard,
    extra_rule=(
        "如果对方只丢一句「我们和好吧」，却没讲清自己哪里让你难受，"
        "你只能选 tier_3 的台词（冷淡、敷衍），不许用 tier_4/5 的反击句；"
        "等他说出了事实、感受和想怎么办，你才可以换成 tier_1/2 的给台阶台词。"
    ),
    copy_brief={
        "noun": "那天的话",
        "ss": "只聊十分钟",
        "guess": "我一直觉得你不够好",
        "detail": "那天那顿饭",
        "stake": "自己有没有被当成斤斤计较的人",
    },
)

SCENES: dict[str, Scene] = {scene.key: scene for scene in (AGING, MONEY, SALARY, BESTIE)}
DEFAULT_KEY = AGING.key


def all_scenes() -> list[Scene]:
    return list(SCENES.values())


def get(key: str | None) -> Scene:
    """永远返回一个可用场景（未知 key 回退默认），UI 不会因为脏数据崩。"""
    return SCENES.get(str(key or ""), AGING)


def key_of(label: str | None) -> str:
    """从库里存的 scene_name 反查 key（库里存的是中文 label）。"""
    for scene in SCENES.values():
        if scene.label == label:
            return scene.key
    return DEFAULT_KEY


def options() -> list[str]:
    """下拉框的 options（顺序固定：适老化 → 谈钱 → 谈加薪 → 挚友）。"""
    return [scene.key for scene in SCENES.values()]


def label_of(key: str | None) -> str:
    return get(key).label


# ───────────────────────────── Prompt 渲染 ─────────────────────────────
_PROMPT_CACHE: dict[str, str] = {}
_NUMBERED_CACHE: dict[str, list[tuple[int, Line]]] = {}

_STYLE = {
    5: "被冒犯、当面反击",
    4: "硬挡回去、否认需要",
    3: "嘴硬、把话推回给对方",
    2: "松口、嘴上还嫌弃",
    1: "接受、开始谈条件",
}


def numbered(scene: Scene) -> list[tuple[int, Line]]:
    """把所有台词按「档位从高到低」编号（1 起），模型只认编号。"""
    cached = _NUMBERED_CACHE.get(scene.key)
    if cached:
        return cached
    out: list[tuple[int, Line]] = []
    index = 0
    for tier in range(scene.max_tier, 0, -1):
        for line in scene.lines_of(tier):
            index += 1
            out.append((index, line))
    _NUMBERED_CACHE[scene.key] = out
    return out


def _tier_table(scene: Scene) -> tuple[str, str]:
    """返回 (台词样例块, 档位分布块)，编号在两块之间保持一致。

    2026-11-17 重构：台词前必须带 `[5分·暴怒反击]` 这样的固定标签，
    模型只回 anchor_id；档位/reaction 由服务器从这句话上读取，不再让模型打分。
    """
    samples: list[str] = []
    groups: list[str] = []
    for tier in range(scene.max_tier, 0, -1):
        positions = []
        for number, line in numbered(scene):
            if line.tier != tier:
                continue
            positions.append(str(number))
            tag = f"{tier}分·{line.reaction}" if line.reaction else f"{tier}分"
            samples.append(f"{number}. [{tag}] {line.text}")
            if line.subtext:
                samples.append(f"    潜台词：{line.subtext}")
            if line.trigger:
                samples.append(f"    ↳ 引出条件：{line.trigger}")
        if positions:
            style = _STYLE.get(tier, "")
            label = scene.lines_of(tier)[0].reaction if scene.lines_of(tier) else ""
            groups.append(
                f"  tier_{tier}｜{tier} 分（{label}）：第 {'、'.join(positions)} 句；语气：{style}"
            )
    return "\n".join(samples), "\n".join(groups)


def render_system_prompt(scene: Scene) -> str:
    """把 Scene 数据渲染成完整 System Prompt（按 key 缓存）。"""
    cached = _PROMPT_CACHE.get(scene.key)
    if cached:
        return cached

    samples, groups = _tier_table(scene)
    extra = f"\n【这个场景的额外规矩】\n{scene.extra_rule}\n" if scene.extra_rule else ""
    prompt = f"""你在一场真实的{scene.dialogue}里扮演「{scene.role_name}」。不许出戏，不许写旁白，不许总结对方的话。

【你是谁】
{scene.persona}
场景：{scene.scenario}

【你怎么说话】
- 一句话，最多 25 个字。第一人称，口语，不要书面语。
- 不解释、不客套、不复述对方的话、不总结对方的话。
- 禁止出现：{"、".join(scene.forbidden)}。
- 你的态度只由「对方这句话怎么说」决定，不由你自己的心情决定。

【你的防御机制】
{scene.mechanism}
{extra}
【台词档位与反应标签】
台词库按 5 分→1 分排列，分数越高越防御。每句台词前的标签是固定的，不由你改：
{groups}

【真人台词库（你只能说这里的话；编号是给你回填 anchor_id 的）】
{samples}

【你这一轮只做两件事，只输出一个 JSON 对象】
1. 判断「对方」这句话属于哪种策略（strategy），只能是：要求 / 解释 / 共情 / 回避。
2. 从上面台词库里挑一句最贴合这一轮对话的台词，回填它的编号（anchor_id）。必须原句使用，不要改写、不要拼接、不要自己写台词。

档位和反应标签已经固定在这句台词上，由服务器按编号读取；你不需要输出分数、台词或依据。

输出格式（不要 markdown，不要解释，不要多余文字）：
{{"strategy": "要求", "anchor_id": 12}}

【记牢】
- strategy 说的是「对方」的策略，不是你的。
- anchor_id 必须是上面台词前的编号，且只能选一句。
- 先判 strategy，再选最贴合当前对话的一句；不要为了制造分数变化去选不贴合的台词。
- 只输出 strategy 和 anchor_id 两个字段。"""

    _PROMPT_CACHE[scene.key] = prompt
    return prompt
