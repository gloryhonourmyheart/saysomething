"""charts.py — Plotly 防御曲线。

只依赖 plotly。图表的唯一职责：把「4 分降到 2 分」画成一条看得见的曲线。
数据不足时返回 None，调用方负责显示占位文案——图表本身永不抛异常。
"""
from __future__ import annotations

from typing import Sequence

import plotly.graph_objects as go

import config

# 1 分最浅、5 分最深：颜色本身就是「刺的硬度」
RED_SCALE = [
    (0.0, "#f6cdc7"),
    (0.25, "#e69b92"),
    (0.5, "#d06a5e"),
    (0.75, "#b03a2e"),
    (1.0, "#7f1d17"),
]

# 严格跟随 config 的五个档位色，图表和 UI 不再各有一套色板。
BAR_COLORS = config.TIER_COLORS
CAUSE_DOWN = config.TIER_COLORS[1]
CAUSE_UP = config.TIER_COLORS[4]

# 四种策略的莫兰迪配色：页面图例和饼图共用一套，改这里两边一起变
PIE_COLORS = {"要求": "#C9A6A0", "解释": "#D9C39F", "共情": "#AEBCA4", "回避": "#CFCBC4"}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _html_escape(text: str) -> str:
    """Plotly 的 annotation 文本按 HTML 渲染，用户原话先转义。"""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ring(fig: go.Figure, x: int, y: int, color: str, *, compact: bool) -> None:
    """在数据点外面套一个空心圆：远看也能一眼找到「哪一轮变了」。"""
    fig.add_trace(
        go.Scatter(
            x=[x],
            y=[y],
            mode="markers",
            marker=dict(
                size=34 if compact else 42,
                color="rgba(0,0,0,0)",
                line=dict(color=color, width=3),
            ),
            hoverinfo="skip",
            showlegend=False,
        )
    )


def _annotate_causality(fig: go.Figure, turns: Sequence, scores: list[int], *, compact: bool) -> None:
    """把「随机波动」翻译成因果：观众要看到的是『你的话让她软下去』。

    只标两个点，多标就变成噪点：
    - 全场降幅最大的一轮 → 绿圈「关键软化句」——这就是拔刺的那句话；
    - 全场升幅最大的一轮 → 红圈「再次施压」——解释为什么曲线会反弹。
    中间的平段（3→3→3）不标：横盘本身就是"她还在等"的意思，靠走势自明。
    """
    if len(turns) < len(scores):
        turns = list(turns)[: len(scores)]
    drops = [(i, scores[i - 1] - scores[i]) for i in range(1, len(scores)) if scores[i] < scores[i - 1]]
    rises = [(i, scores[i] - scores[i - 1]) for i in range(1, len(scores)) if scores[i] > scores[i - 1]]
    size = 9 if compact else 12

    if drops:
        i, delta = max(drops, key=lambda p: p[1])
        said = _html_escape((getattr(turns[i], "user_text", "") or "").strip())
        if compact:
            text = f"<b>关键软化句</b> · -{delta}"
        else:
            if len(said) > 10:
                said = said[:10] + "…"
            text = f"<b>关键软化句</b> · 掉 {delta} 分" + (f"<br>你说「{said}」" if said else "")
        _ring(fig, i, scores[i], CAUSE_DOWN, compact=compact)
        fig.add_annotation(
            x=i, y=scores[i], ax=0, ay=38,
            text=text, showarrow=True, arrowhead=2, arrowcolor=CAUSE_DOWN,
            font=dict(size=size, color=CAUSE_DOWN),
            bgcolor="#F2F7EF", bordercolor="#AEBCA4", borderwidth=1, borderpad=3,
        )
    if rises:
        i, delta = max(rises, key=lambda p: p[1])
        strat = _html_escape(getattr(turns[i], "strategy", "") or "")
        if compact:
            text = f"<b>再次施压</b> · +{delta}"
        else:
            text = f"<b>再次施压</b> · 升 {delta} 分" + (f"<br>你用「{strat}」" if strat else "")
        _ring(fig, i, scores[i], CAUSE_UP, compact=compact)
        fig.add_annotation(
            x=i, y=scores[i], ax=0, ay=-38,
            text=text, showarrow=True, arrowhead=2, arrowcolor=CAUSE_UP,
            font=dict(size=size, color=CAUSE_UP),
            bgcolor="#FBF1EF", bordercolor="#D8A49C", borderwidth=1, borderpad=3,
        )


def defense_curve(turns: Sequence, *, height: int = 340, compact: bool = False) -> go.Figure | None:
    """两轮（或更多）防御分的折线图。少于 2 轮直接返回 None。

    compact=True 用于右侧 300px 窄栏：字号收紧、左边距压到最小。
    """
    scores = [int(getattr(t, "defense_score", 0) or 0) for t in (turns or [])]
    if len(scores) < 2:
        return None

    labels = [f"第{i}轮" for i in range(1, len(scores) + 1)]

    # 用密集小圆点插值出一条「渐变红线」：颜色随分数变化，分数越高越深
    samples_x: list[float] = []
    samples_y: list[float] = []
    steps = 48
    for i in range(len(scores) - 1):
        for s in range(steps):
            t = s / steps
            samples_x.append(_lerp(i, i + 1, t))
            samples_y.append(_lerp(scores[i], scores[i + 1], t))
    samples_x.append(float(len(scores) - 1))
    samples_y.append(float(scores[-1]))

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=samples_x,
            y=samples_y,
            mode="markers",
            marker=dict(
                size=9,
                color=samples_y,
                colorscale=RED_SCALE,
                cmin=1,
                cmax=5,
                line=dict(width=0),
            ),
            hoverinfo="skip",
            showlegend=False,
        )
    )

    fig.add_trace(
        go.Scatter(
            x=list(range(len(scores))),
            y=scores,
            mode="markers+text",
            marker=dict(
                size=30,
                color=scores,
                colorscale=RED_SCALE,
                cmin=1,
                cmax=5,
                line=dict(color="#ffffff", width=3),
            ),
            text=[str(s) for s in scores],
            textposition="middle center",
            textfont=dict(size=15, color="#ffffff", family="Arial Black, sans-serif"),
            hovertemplate="%{x}：防御 %{y}/5<extra></extra>",
            showlegend=False,
        )
    )

    fig.update_layout(
        height=height,
        margin=dict(l=2, r=4, t=8, b=2) if compact else dict(l=8, r=8, t=14, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        font=dict(
            family="PingFang SC, Microsoft YaHei, sans-serif",
            size=10 if compact else 13,
            color="#3b424b",
        ),
    )
    fig.update_xaxes(
        tickmode="array",
        tickvals=list(range(len(scores))),
        ticktext=labels,
        range=[-0.35, len(scores) - 0.65],
        showgrid=False,
        zeroline=False,
        fixedrange=True,
        tickfont=dict(size=9 if compact else 14),
        linecolor="#e3e6ea",
    )
    fig.update_yaxes(
        range=[0, 6],
        tickvals=[1, 2, 3, 4, 5],
        # 窄栏：emoji 一行、情绪词一行，比截断成「松口保面」这种半截话好读
        ticktext=[
            (
                f"{config.MOOD[i][0]}<br>{config.MOOD[i][1]}"
                if compact
                else config.DEFENSE_LABELS[i]
            )
            for i in (1, 2, 3, 4, 5)
        ],
        showgrid=True,
        gridcolor="#f0f2f4",
        zeroline=False,
        fixedrange=True,
        tickfont=dict(size=9 if compact else 12),
    )

    # 因果标注放在布局之后：annotation 允许画出坐标区外，靠 margin 留出呼吸空间
    fig.update_layout(margin=dict(l=2, r=4, t=30 if compact else 36, b=30) if compact
                      else dict(l=8, r=8, t=36, b=36))
    _annotate_causality(fig, turns, scores, compact=compact)
    return fig


def strategy_pie(counts: dict[str, int], *, height: int = 200, bare: bool = False) -> go.Figure | None:
    """F14 仪表盘用的策略饼图：四类策略的占比。没有数据时返回 None。

    bare=True 给右栏窄条用：关掉图例，颜色对应关系由页面上的小圆点图例说明。
    """
    palette = PIE_COLORS
    items = [(k, int((counts or {}).get(k, 0) or 0)) for k in ("要求", "解释", "共情", "回避")]
    items = [(k, v) for k, v in items if v > 0]
    if not items:
        return None
    fig = go.Figure(
        go.Pie(
            labels=[k for k, _ in items],
            values=[v for _, v in items],
            hole=0.58,
            marker=dict(
                colors=[palette.get(k, "#CFCBC4") for k, _ in items],
                line=dict(color="#F4F1EC", width=3),
            ),
            # 只剩一类时不要把标签塞进扇区：位置算在圆弧中点，窄栏里会被裁掉一半。
            textinfo="none" if len(items) == 1 else ("label+percent" if bare else "percent"),
            textfont=dict(size=10 if bare else 12, color="#4A4541"),
            hovertemplate="%{label}：%{value} 句（%{percent}）<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin=dict(l=2, r=2, t=4, b=4),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=not bare,
        legend=dict(orientation="h", yanchor="bottom", y=-0.06, x=0.5, xanchor="center",
                    font=dict(size=11), itemwidth=60),
        font=dict(family="PingFang SC, Microsoft YaHei, sans-serif", color="#4A4541"),
    )
    # 只有一类时把名字写进圆心，否则右栏只剩一个没有信息的圆环。
    if len(items) == 1:
        k = items[0][0]
        fig.add_annotation(
            text=f"<b>{k}</b><br>100%",
            showarrow=False,
            font=dict(size=11, color="#5B5550"),
            x=0.5,
            y=0.5,
        )
    return fig


def sparkline(turns: Sequence, *, height: int = 110) -> go.Figure | None:
    """侧栏用的小图：同样数据，去掉一切装饰。"""
    scores = [int(getattr(t, "defense_score", 0) or 0) for t in (turns or [])]
    if len(scores) < 2:
        return None
    fig = go.Figure(
        go.Scatter(
            x=list(range(len(scores))),
            y=scores,
            mode="lines+markers",
            line=dict(color="#b03a2e", width=2),
            marker=dict(size=9, color=scores, colorscale=RED_SCALE, cmin=1, cmax=5),
        )
    )
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(range=[0, 6], visible=False)
    return fig
