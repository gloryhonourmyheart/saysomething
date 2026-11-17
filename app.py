"""app.py — 「刺与泡沫」ToC 沉浸式界面。

左边一条窄导航，中间 800px 的对话流，所有图表收进 st.dialog 弹窗。
底层 agent.py / db.py / scenes.py 一行没改：这里只做展示与编排。

四条顺序规则，改代码前先读：

1. 场景下拉框在左导航列里最先出现。任何改写 scene_key 的按钮都必须用 on_click 回调
   —— 下拉框一旦实例化，再写 st.session_state["scene_key"] 就会抛 StreamlitAPIException。
2. 案例按钮不直接调模型，只把话塞进 _queued，由 chat_input 那一处统一消费，
   全程序只有「_submit」一条发送路径。
3. st.chat_input 是触发值，读完就没了。发完必须 pop("chatbox") 再 st.rerun()，
   否则重跑那一轮会把同一句话再读一次 → 同一句被发两遍。
4. 输入框放在 chat 容器最后：Streamlit 在容器里不帮它吸底，靠 theme 里的
   position:sticky + margin-top:auto 把它压到视口底部。
"""
from __future__ import annotations

import html
import json
import re
import time
from collections import Counter
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import agent
import charts
import coach
import config
import db
import hints
import insight
import models
import scenes
import theme

DATA_DIR = Path(__file__).resolve().parent / "data"
FOLLOWUP_FILE = DATA_DIR / "followups.json"
REPORT_DIR = DATA_DIR / "reports"

st.set_page_config(
    page_title=f"{config.APP_NAME} · {config.APP_SUBTITLE}",
    page_icon="🌿",
    layout="wide",
)
theme.inject_toc(800)
db.init_db()

_ss = st.session_state

CHIP_CLASS = {"要求": "req", "解释": "exp", "共情": "emp", "回避": "avoid"}


# ───────────────────────────── 小工具 ─────────────────────────────
def esc(text: str) -> str:
    """用户和模型的话都进 HTML，先转义再拼。

    台词库里的换行写成了字面量 <br>，转义后要还原回来，否则屏幕上会显示「&lt;br&gt;」。
    """
    return html.escape(text or "").replace("&lt;br&gt;", "<br>")


def cquote(text: str) -> str:
    """兼容旧调用：不再给卡片句子套外层引号。

    小抄现在要求是可直接复制发送的人话，任何「」都会让界面变回复读体。
    """
    return (text or "").strip()


def _scene() -> scenes.Scene:
    return scenes.get(_ss.get("scene_key") or scenes.DEFAULT_KEY)


def _session() -> models.Session:
    sess = _ss.get("session")
    if not isinstance(sess, models.Session):  # 热重载后类会变，兜一层
        sess = models.new_session(_scene().key)
        _ss["session"] = sess
    return sess


def _clean_case(name: str) -> str:
    """案例名里自带 ①②③，导航按钮外面还要加 1/2/3，去掉一层避免重复。"""
    return name[1:].strip() if name[:1] in "①②③④⑤" else name


@st.cache_data(ttl=600, show_spinner=False)
def _health_cached() -> tuple[bool, str]:
    return agent.health_check()


def _health() -> tuple[bool, str]:
    if not agent.available():
        return False, "未连模型 · 用本地兜底台词"
    try:
        return _health_cached()
    except Exception:  # noqa: BLE001 - 健康检查永远不该把页面弄崩
        return False, "模型暂不可用 · 用本地兜底台词"


# ───────────────────────────── 头像与刻度 ─────────────────────────────
ASSET_DIR = Path(__file__).resolve().parent / "assets"
AVATAR_FALLBACK = {"me": "🧑", "ai": "🌿", "coach": "🤖"}
SCENE_FALLBACK = {"aging": "👵", "money": "👴", "salary": "🧑‍💼", "bestie": "🧑"}


def _asset_avatar(name: str) -> str:
    """本地头像读不到/不是图片就返回空串，调用方用 Emoji 兜底。

    Streamlit 的 avatar 参数在部分版本里对坏文件不会抛 Python 异常，而是把
    前端渲染成空白。所以这里先用 Pillow verify 一次，别把风险留到浏览器。
    """
    try:
        path = ASSET_DIR / f"{name}.png"
        if not path.is_file() or path.stat().st_size <= 0:
            return ""
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        return str(path)
    except Exception:  # noqa: BLE001 - 头像永远不该让页面白屏
        return ""


def _scene_avatar(scene: scenes.Scene) -> str:
    return _asset_avatar(scene.key) or SCENE_FALLBACK.get(scene.key, "🌿")


def _dots_html(score: int) -> str:
    score = max(1, min(5, int(score or 1)))
    return (
        '<span class="dot on"></span>' * score
        + '<span class="dot"></span>' * (5 - score)
    )


def _reaction_of(turn: models.Turn) -> str:
    """窄栏只放两字：暴怒反击 → 反击；旧数据没有 reaction 时从档位补。"""
    return config.REACTION_SHORT.get(turn.defense_score, "") or (turn.reaction or "")


def _status_html(turn: models.Turn, speaker: str) -> str:
    score = max(1, min(5, int(turn.defense_score or 1)))
    color = config.TIER_COLORS.get(score, "#8A837B")
    chip = CHIP_CLASS.get(turn.strategy, "avo")
    return (
        f'<div class="statusline">'
        f'<span class="expr">你的表达：'
        f'<span class="chip {chip}">{esc(turn.strategy)}</span></span>'
        f'<span class="def">{esc(speaker)}的防御：<b>{score}/5</b>'
        f' · {esc(_reaction_of(turn))}'
        f'<span class="dots" style="--dot:{color}">{_dots_html(score)}</span></span>'
        f"</div>"
    )


# ───────────────────────────── 回调 ─────────────────────────────
def _reset() -> None:
    _ss["session"] = models.new_session(_scene().key)
    _ss.pop("last_report", None)


def _queue(text: str) -> None:
    # 案例按钮只认自己这份原文；先清掉输入框里的残留，避免聊天框里的
    # 旧文本（浏览器自动填充、上一次没发出去的话）抢在案例前面被发送。
    _ss["_queued"] = (text or "").strip()
    _ss.pop("chatbox", None)


def _open_dash() -> None:
    _ss["show_dash"] = True


def _restore(session_id: int) -> None:
    row = db.get_session(session_id)
    if not row:
        return
    key = scenes.key_of(row.get("scene_name"))
    _ss["scene_key"] = key
    _ss["session"] = models.Session.from_rows(
        row.get("turns") or [], session_id=session_id, scene_key=key
    )
    _ss.pop("last_report", None)


def _clear() -> None:
    db.clear_all()
    _ss["session"] = models.new_session(_scene().key)
    _ss.pop("last_report", None)


def _export() -> None:
    sess = _ss.get("session")
    if isinstance(sess, models.Session) and sess.turns:
        _ss["last_report"] = build_report(sess)


def _followup() -> None:
    sess = _session()
    if not sess.turns:
        return
    question = agent.build_followup(sess.turns, sess.scene)
    sess.followup_question = question
    sess.followup_when = sess.followup_when or "三天后"
    _push_followup(sess, question)


def _push_followup(sess: models.Session, question: str) -> None:
    try:
        rows = json.loads(FOLLOWUP_FILE.read_text(encoding="utf-8")) if FOLLOWUP_FILE.exists() else []
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        rows = []
    rows.append(
        {
            "session_id": sess.id,
            "scene": sess.scene.label,
            "when": sess.followup_when,
            "question": question,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "delivered": False,
        }
    )
    try:
        FOLLOWUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        FOLLOWUP_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ───────────────────────────── 报告 ─────────────────────────────
def build_report(sess: models.Session) -> tuple[Path, str]:
    """把这一场演练导成 Markdown：表格 + 防御变化 + 点评 + 总结 + 免责声明。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = REPORT_DIR / f"演练报告-{stamp}.md"
    scene = sess.scene
    first, last = sess.turns[0], sess.turns[-1]
    delta = last.defense_score - first.defense_score

    lines = [
        f"# 演练报告 · {scene.label}",
        "",
        f"- 记录号：#{sess.id}",
        f"- 场景：{scene.label}（{scene.role_name}）",
        f"- 轮次：{len(sess.turns)}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 每一轮",
        "",
        "| 轮次 | 你的原话 | 策略 | 防御 | 回弹 | 小抄 | 潜台词 | 锚点 | 耗时 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, turn in enumerate(sess.turns, 1):
        lines.append(
            "| {n} | {u} | {s} | {d}/5 | {r} | {cp} | {sub} | tier_{t} | {ms}ms |".format(
                n=i,
                u=turn.user_text.replace("|", "｜"),
                s=turn.strategy,
                d=turn.defense_score,
                r=turn.reply_text.replace("|", "｜"),
                cp=(turn.copy_text or "—").replace("|", "｜"),
                sub=(turn.subtext or "—").replace("|", "｜"),
                t=turn.anchor_tier,
                ms=turn.latency_ms,
            )
        )

    verdict, direction = coach.verdict(sess.turns, scene.speaker)
    lines += [
        "",
        "## 防御变化",
        "",
        f"{first.defense_score}/5 → {last.defense_score}/5（{delta:+d}）",
        "",
        verdict,
        "",
        "## 教练点评",
        "",
        coach.critique(sess.turns, scene.speaker),
        "",
        "## 一句总结",
        "",
        agent.summarize(sess.turns, scene),
        "",
    ]
    if sess.followup_question:
        lines += ["## 回访", "", f"{sess.followup_when}：{sess.followup_question}", ""]
    lines += ["---", "", config.DISCLAIMER, ""]

    text = "\n".join(lines)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return path, text


# ───────────────────────────── 气泡 ─────────────────────────────
def _bubble(kind: str, text: str, avatar: str | None = None) -> None:
    """聊天气泡。avatar 优先用 assets/ 图片，任何异常都降级成 Emoji。

    头像只是装饰，绝不允许它把整页弄崩：图片不存在、损坏、Streamlit 不认
    本地路径，都退回 Emoji。
    """
    role = "user" if kind == "me" else "assistant"
    fallback = AVATAR_FALLBACK["me" if kind == "me" else "ai"]
    chosen = avatar or fallback
    body = f'<div class="bub {kind}">{esc(text)}</div>'
    try:
        with st.chat_message(role, avatar=chosen):
            st.markdown(body, unsafe_allow_html=True)
    except Exception:  # noqa: BLE001 - 头像失败不能白屏
        with st.chat_message(role, avatar=fallback):
            st.markdown(body, unsafe_allow_html=True)


def mood_of(score: int) -> tuple[str, str]:
    """分数 → (emoji, 情绪词)。给 C 端看的说法，不知道分数的存在。"""
    return config.MOOD.get(score, config.MOOD[3])


def status_line(turn: models.Turn, idx: int, speaker: str = "她") -> None:
    """回弹气泡下面那一行：左边你的表达，右边她的防御 + 五格刻度。

    刻度用 ●●●○○ 而不是进度条：格数就是防御分、颜色就是严重度，
    观众一眼读出「几格」而不是去猜条长（条长在窄栏里几乎看不出差别）。
    """
    st.markdown(_status_html(turn, speaker), unsafe_allow_html=True)
    if turn.degraded:
        st.markdown(
            f'<div class="why">⚠ {esc(turn.degraded_reason or "这次没接上模型，用的是兜底台词")}</div>',
            unsafe_allow_html=True,
        )
    if turn.subtext:
        # 潜台词默认收起：提前剧透会替用户把「猜」这一步做掉，演练就空了。
        # 复盘页（体检报告弹窗）里统一铺开。
        with st.expander(f"看{speaker}没说出口的话", expanded=False):
            st.markdown(f'<div class="sub">💡 {esc(turn.subtext)}</div>', unsafe_allow_html=True)


def _intervene(scene: scenes.Scene, turns: list[models.Turn]) -> None:
    """插话：引用用户真实原话，建议句和小抄共用同一份 copy。

    原来这里是两处各自写死的模板（「试试先说一句『我知道你能行』」+ 下面那张卡片），
    改一处另一处还露馅。现在优先读 turn.copy_text；老会话没有就现场用 L1 补一句。
    """
    turn = turns[-1]
    tip = turn.copy_text or hints.fallback_copy(scene, turns, turn.defense_score).copy
    streak = 0
    for prev in reversed(turns):
        if prev.strategy in ("要求", "解释") and prev.defense_score >= 4:
            streak += 1
        else:
            break
    body = (
        f'<div class="intervene"><b>Agent 插话</b> · 你已经连着 {streak} 句停在「{esc(turn.strategy)}」上，'
        f"{esc(scene.speaker)}的防御一直没下来。你最后那句「{esc(hints.quote(turn.user_text))}」——"
        f"试试改成：<br>{esc(tip)}</div>"
    )
    coach_avatar = _asset_avatar("coach") or AVATAR_FALLBACK["coach"]
    try:
        with st.chat_message("assistant", avatar=coach_avatar):
            st.markdown(body, unsafe_allow_html=True)
    except Exception:  # noqa: BLE001 - 头像失败不能白屏
        with st.chat_message("assistant", avatar=AVATAR_FALLBACK["coach"]):
            st.markdown(body, unsafe_allow_html=True)


def _intervention_at(turns: list[models.Turn]) -> int | None:
    """F13：返回「第一次」满足连续 3 句高防御的轮次下标；同一场只插一次。

    旧实现只看最后 3 轮并返回 len(turns)-1，第 4、5 轮会每轮重复插话。
    这里从头扫，第一次命中就固定下来，后面即使继续高防御也不再打断。
    """
    for i in range(2, len(turns)):
        recent = turns[i - 2 : i + 1]
        if all(t.strategy in ("要求", "解释") and t.defense_score >= 4 for t in recent):
            return i
    return None


def _greeting(scene: scenes.Scene) -> str:
    """F15：开场先读一次历史，主动把上一次的落点说出来。

    只认同一个场景的上一次——在挚友场景里提「适老化那场」会把气氛带跑。
    """
    row = next(
        (r for r in db.list_sessions(limit=12) if (r.get("scene_name") or "") == scene.label),
        None,
    )
    if not row:
        return ""
    first, last = row.get("first_defense"), row.get("last_defense")
    if first is None or last is None:
        return ""
    return (
        f"上次在「{row.get('scene_name') or scene.label}」那场，你从 {first} 分走到 {last} 分。"
        f"今天接着练这个，还是换个话题？"
    )


# ───────────────────────────── 弹窗 ─────────────────────────────
def _card(title: str, turn: models.Turn, tag: str, speaker: str = "她") -> None:
    st.markdown(
        f'<div class="card"><div class="who">{esc(tag)} · {esc(title)}</div>'
        f'<div class="said">{esc(turn.user_text)}</div>'
        f'<div class="said reply">「{esc(turn.reply_text)}」</div>'
        f"{_status_html(turn, speaker)}</div>",
        unsafe_allow_html=True,
    )


def _modal() -> None:
    sess = _ss["session"]
    scene = sess.scene
    st.markdown("#### 本场防御分走势")
    curve = charts.defense_curve(sess.turns, height=220)
    if curve is not None:
        st.plotly_chart(curve, width="stretch", config={"displayModeBar": False}, key="modal_curve")
        first, last = sess.turns[0], sess.turns[-1]
        delta = last.defense_score - first.defense_score
        cls = "down" if delta < 0 else ("up" if delta > 0 else "flat")
        st.markdown(
            f'<div class="bignum {cls}">{delta:+d}<small> 分</small></div>'
            f'<div class="muted">从 {first.defense_score}/5 到 {last.defense_score}/5，共 {len(sess.turns)} 轮</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("至少聊完两轮，曲线才画得出来。")

    st.markdown("#### 你的策略分布")
    counts = Counter(turn.strategy for turn in sess.turns)
    pie = charts.strategy_pie(dict(counts), height=200)
    if pie is not None:
        st.plotly_chart(pie, width="stretch", config={"displayModeBar": False}, key="modal_pie")
    else:
        st.info("还没有数据。")

    # 两张原始对照卡默认收起：评委先看曲线和饼图，需要追问再展开。
    if len(sess.turns) >= 2:
        with st.expander("看原始两轮对照", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                _card(sess.turns[0].strategy, sess.turns[0], "第 1 轮", scene.speaker)
            with c2:
                _card(sess.turns[-1].strategy, sess.turns[-1], f"第 {len(sess.turns)} 轮", scene.speaker)

    st.markdown("#### 教练点评")
    if len(sess.turns) >= 2:
        tip = coach.critique(sess.turns, scene.speaker)
        st.markdown(f'<div class="coach">{esc(tip)}</div>', unsafe_allow_html=True)
        verdict, direction = coach.verdict(sess.turns, scene.speaker)
        # 两条规则偶尔得出同一句话（比如「解释太多」既是最常见策略又是本场走势），
        # 重复渲染会让评委以为界面出错，所以只在不同的时候再画一条。
        if verdict.strip() != tip.strip():
            st.markdown(f'<div class="coach {direction}">{esc(verdict)}</div>', unsafe_allow_html=True)
        st.success(agent.summarize(sess.turns, scene))
    else:
        st.markdown(f'<div class="coach flat">再多聊两句，点评才有依据。</div>', unsafe_allow_html=True)

    # 潜台词统一在复盘页铺开：对话流里默认收起（别剧透），这里才给答案。
    subs = [(i, t) for i, t in enumerate(sess.turns, 1) if t.subtext]
    if subs:
        with st.expander(f"看{scene.speaker}没说出口的话", expanded=True):
            body = "".join(
                f'<div class="subrow"><b>第 {i} 轮</b> · 你说「{esc(t.user_text)}」'
                f"<br>💡 {esc(t.subtext)}</div>"
                for i, t in subs
            )
            st.markdown(body, unsafe_allow_html=True)

    profile = insight.build_profile(db.all_turns())
    st.markdown("#### 跨会话的沟通画像")
    if profile.empty:
        st.markdown('<div class="insight">还没有数据，先聊两句再说。</div>', unsafe_allow_html=True)
    else:
        body = "<br>".join(esc(line) for line in profile.lines)
        st.markdown(f'<div class="insight">{body}</div>', unsafe_allow_html=True)


_modal_dialog = st.dialog("你的沟通体检报告", width="large")(_modal)


# ───────────────────────────── 左侧导航 ─────────────────────────────
def _nav_top() -> None:
    st.markdown(
        '<div class="logo">刺与泡沫<i>。</i></div>'
        '<div class="tagline">关系演练场 · 在你开口之前，先替你试一次雷</div>',
        unsafe_allow_html=True,
    )
    st.selectbox(
        "场景",
        scenes.options(),
        key="scene_key",
        format_func=scenes.label_of,
        label_visibility="collapsed",
    )


def _nav_body(scene: scenes.Scene) -> None:
    st.button("＋ 开始新一场", key="reset", on_click=_reset, use_container_width=True, type="primary")
    st.button(
        "📊 查看我的沟通画像",
        key="dash",
        on_click=_open_dash,
        use_container_width=True,
        type="primary",
    )
    st.markdown('<div class="navsep"></div><div class="navhint">演示案例 · 点一下直接发出</div>', unsafe_allow_html=True)
    # 用带 key 的容器把三个案例按钮圈起来，CSS 才能只作用于它们（不影响上面两个主按钮）。
    with st.container(key="cases"):
        for i, case in enumerate(scene.cases[:3], 1):
            name, text = case[0], case[1]
            st.button(
                f"{i} · {_clean_case(name)}",
                # key 必须带场景：只用 case1 的话，切场景后 Streamlit 会复用同一个
                # widget，标签和 on_click 的 args 都还是上一个场景的（2026-11-17 实测）。
                key=f"case{i}_{scene.key}",
                on_click=_queue,
                args=(text,),
                use_container_width=True,
            )

    st.markdown('<div class="navsep"></div>', unsafe_allow_html=True)
    with st.expander("历史演练记录", expanded=False):
        rows = db.list_sessions(limit=8)
        if not rows:
            st.caption("还没有记录。")
        for row in rows:
            short = scenes.get(scenes.key_of(row.get("scene_name"))).short
            stamp = (row.get("created_at") or "")[5:16]
            st.button(
                f"{stamp} · {short} · {row.get('first_defense')}→{row.get('last_defense')}",
                key=f"hist_{row['id']}",
                on_click=_restore,
                args=(row["id"],),
                use_container_width=True,
            )
    with st.expander("更多", expanded=False):
        st.button("导出本轮演练报告", key="export", on_click=_export, use_container_width=True)
        if _ss.get("last_report"):
            path, text = _ss["last_report"]
            st.download_button(
                "下载 Markdown",
                text,
                file_name=path.name,
                key="download_report",
                use_container_width=True,
            )
        if st.checkbox("我确定要清空所有历史", key="confirm_clear"):
            st.button("清空历史与画像", key="clear_db", on_click=_clear, use_container_width=True)

    ok, detail = _health()
    st.caption(("🟢 " if ok else "⚪ ") + detail)
    st.markdown(f'<div class="muted" style="font-size:.72rem">{esc(config.DISCLAIMER)}</div>', unsafe_allow_html=True)


# ───────────────────────────── 中间对话流 ─────────────────────────────
def _chat(scene: scenes.Scene) -> None:
    sess = _session()

    if not sess.turns:
        memory = _greeting(scene)
        if memory:
            _bubble("ai", memory, avatar=_scene_avatar(scene))
            st.markdown('<div class="muted">上一次的你 · 已存进 SQLite</div>', unsafe_allow_html=True)
        _bubble("ai", scene.empty_line, avatar=_scene_avatar(scene))
        st.markdown(f'<div class="muted">{esc(scene.role_name)} · 还没开口</div>', unsafe_allow_html=True)

    for i, turn in enumerate(sess.turns):
        _bubble("me", turn.user_text, avatar=_asset_avatar("user") or AVATAR_FALLBACK["me"])
        _bubble("ai", turn.reply_text, avatar=_scene_avatar(scene))
        status_line(turn, i, scene.speaker)
        if _intervention_at(sess.turns[: i + 1]) == i:
            _intervene(scene, sess.turns[: i + 1])

    if sess.followup_question:
        _bubble("ai", sess.followup_question, avatar=_scene_avatar(scene))
        st.caption(f"{sess.followup_when} 的回访 · 本地模拟")
    elif sess.turns:
        st.button("三天后回来问我结果", key="followup", on_click=_followup)

    prompt = st.chat_input(f"对{scene.role_name}说点什么……", key="chatbox")
    # 案例按钮的 _queued 优先于输入框：点了演示案例就发案例原文，
    # 不让任何自动填充/残留文本劫持这一句。
    queued = _ss.pop("_queued", "")
    incoming = (queued or prompt or "").strip()
    if incoming:
        _submit(incoming)
        _ss.pop("chatbox", None)
        st.rerun()


def _submit(text: str) -> None:
    sess = _session()
    scene = sess.scene
    with st.spinner(f"{scene.role_name}在斟酌怎么回你……"):
        turn = agent.run_turn(text, sess.turns, scene)
        # L3：小抄只在这一处生成一次——渲染层只读 turn.copy_text，刷新/切场景都不会变，
        # 也不会每次 rerun 再打一遍模型。上一版的原文当负样本传进去，防止连着两轮同一句。
        # 放在 spinner 里：L2 实测要 1-6 秒，别让页面看起来像卡死。
        last_copy = sess.turns[-1].copy_text if sess.turns else ""
        turn.copy_text = hints.suggest(
            scene,
            [*sess.turns, turn],
            turn.defense_score,
            last_copy=last_copy,
            allow_model=not turn.degraded,
        ).copy
    sess.add(turn)
    if sess.id is None:
        sess.id = db.create_session(scene.label)
    if sess.id is not None:
        db.add_turn(sess.id, len(sess.turns), turn)


STYLE_NAME = {"要求": "霸道总裁型", "解释": "讲道理型", "共情": "共情高手型", "回避": "讨好型"}
STYLE_NOTE = {
    "要求": "你以为是关心，其实是压迫——对方听见的是「你该听我的」。下次试试把「你必须」换成「我需要」。",
    "解释": "你一直在讲道理，可对方要的不是道理，是被看见。下次先说一句「我懂你的顾虑」，再讲事实。",
    "共情": "你已经在做最难的那件事：先承认对方能行，再说自己的担心。保持住，别急着要一个答复。",
    "回避": "你把话咽回去了，冲突暂时没了，但对方也没听懂你。下次试着只多说一句：你到底怕什么。",
}


def _verdict(score: int | None, speaker: str) -> tuple[str, str, str] | None:
    """本场结论：只看最新一轮的情绪，直接定性，不给用户自己解读的空间。"""
    if score is None:
        return None
    if score >= 4:
        return ("bad", "❌ 沟通失败", f"你一直在用语言扎{speaker}")
    if score == 3:
        return ("warn", "⚠️ 沟通僵持", f"{speaker}还在等你一句软话")
    return ("good", "✅ 沟通有效", f"你成功帮{speaker}拔掉了刺")


def _style_block(counts: Counter, total: int) -> None:
    """把策略饼图翻译成一个人的性格标签 + 一句诊断。"""
    if not total:
        return
    top, top_n = counts.most_common(1)[0]
    pct = round(top_n / total * 100)
    st.markdown(
        f'<div class="styletag">你的沟通风格：{esc(STYLE_NAME.get(top, "还在摸索型"))}</div>'
        f'<div class="stylediag">诊断：你 {pct}% 的时间都在用【{esc(top)}】。'
        f"{esc(STYLE_NOTE.get(top, ''))}</div>",
        unsafe_allow_html=True,
    )


def _side(scene: scenes.Scene) -> None:
    """右栏：一个有温度的 AI 私教。先给结论，再给情绪曲线，最后给一句能抄的话。"""
    sess = _session()
    n = len(sess.turns)
    latest = sess.turns[-1].defense_score if sess.turns else None
    st.markdown('<div class="sidehead">AI 沟通私教</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="sidenote">{esc(scene.short)} · 本场 {n} 轮</div>', unsafe_allow_html=True
    )

    verdict = _verdict(latest, scene.speaker)
    if verdict is None:
        st.markdown(
            '<div class="verdict idle"><b>等你先开口</b>'
            '<span>说第一句话，我就能告诉你对方现在是什么状态。</span></div>',
            unsafe_allow_html=True,
        )
    else:
        tone, head, sub = verdict
        st.markdown(
            f'<div class="verdict {tone}"><b>{head}</b><span>{esc(sub)}</span></div>',
            unsafe_allow_html=True,
        )

    # 气头上才出现的「抄作业」，位置在结论下面，第一屏就能看到。
    # 文本只读这一轮提交时存下的小抄：same round → same copy，刷新也不会变。
    latest_turn = sess.turns[-1] if sess.turns else None
    if latest_turn is not None and latest_turn.defense_score >= 4:
        line = latest_turn.copy_text or hints.fallback_copy(
            scene, sess.turns, latest_turn.defense_score
        ).copy
        st.markdown(
            f'<div class="copybox"><b>🚨 {esc(scene.speaker)}正在气头上</b>'
            f"<span>试试这样说：</span>"
            f'<i>{esc(line)}</i></div>',
            unsafe_allow_html=True,
        )
        st.button(
            "就用这句发过去",
            # key 必须带轮次：静态 key 会和上一轮复用同一个 widget，文案换了按钮还指向旧句。
            key=f"copy_{n}",
            on_click=_queue,
            args=(line,),
            width="stretch",
        )

    st.markdown(f'<div class="subhead">{esc(scene.speaker)}的情绪</div>', unsafe_allow_html=True)
    curve = charts.defense_curve(sess.turns, height=185, compact=True)
    if curve is not None:
        st.plotly_chart(curve, width="stretch", config={"displayModeBar": False}, key="side_curve")
        emoji, word = mood_of(sess.turns[-1].defense_score)
        st.markdown(
            f'<div class="sidehint">现在：{emoji} {esc(word)}</div>', unsafe_allow_html=True
        )
    else:
        st.markdown(
            '<div class="sidehint">聊上两句，这条线才会动。</div>', unsafe_allow_html=True
        )

    counts = Counter(turn.strategy for turn in sess.turns)
    pie = charts.strategy_pie(dict(counts), height=160, bare=True)
    if pie is not None:
        st.markdown('<div class="subhead">你说话的方式</div>', unsafe_allow_html=True)
        st.plotly_chart(pie, width="stretch", config={"displayModeBar": False}, key="side_pie")
        _style_block(counts, n)
    else:
        st.markdown(
            '<div class="sidehint">你说的每句话都会被归进四类：要求 / 解释 / 共情 / 回避。</div>',
            unsafe_allow_html=True,
        )


def _scroll_chat_to_bottom() -> None:
    """Streamlit 不会自己滚到最新一条：用 0 高度组件反复把中栏拉到底。

    组件 iframe 带 allow-same-origin，可以直接改父页面；但 React 是异步挂载的，
    只跑一次会在内容进 DOM 之前就执行完，所以连推 3 秒。
    """
    components.html(
        """<script>
        (function () {
          var doc = window.parent.document, tries = 0;
          function toBottom(box) { box.scrollTop = box.scrollHeight; }
          function attach() {
            var box = doc.querySelector('.st-key-chat');
            if (!box) {
              if (++tries < 40) { setTimeout(attach, 150); }
              return;
            }
            // 消息是异步挂上去的：监听子树变化，一长出来就贴底。
            new MutationObserver(function () { toBottom(box); })
              .observe(box, { childList: true, subtree: true });
            toBottom(box);
            var n = 0;
            (function nudge() { toBottom(box); if (++n < 20) { setTimeout(nudge, 500); } })();
          }
          attach();
        })();
        </script>""",
        height=0,
    )


# ───────────────────────────── 主流程 ─────────────────────────────
def _sync_scene() -> scenes.Scene:
    """下拉框实例化之后才读值：换场景就是开新的一场，旧的已经落库。"""
    scene = scenes.get(_ss.get("scene_key") or scenes.DEFAULT_KEY)
    sess = _ss.get("session")
    if not isinstance(sess, models.Session) or sess.scene_key != scene.key:
        _ss["session"] = models.new_session(scene.key)
        _ss.pop("last_report", None)
    return scene


def main() -> None:
    _ss.setdefault("scene_key", scenes.DEFAULT_KEY)
    left, mid, right = st.columns([1.2, 3.5, 1.5], gap="medium")

    with left:
        with st.container(key="nav"):
            _nav_top()
            scene = _sync_scene()
            _nav_body(scene)

    with mid:
        with st.container(key="chat"):
            _chat(scene)

    with right:
        with st.container(key="side"):
            _side(scene)

    if _ss.pop("show_dash", False):
        _modal_dialog()

    _scroll_chat_to_bottom()


main()
