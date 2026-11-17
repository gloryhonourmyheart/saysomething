"""theme.py — 「刺与泡沫」温柔莫兰迪主题（纯 CSS 注入，不含任何业务逻辑）。

为什么单独一个文件：
- 原型（app_ui_mock.py）和正式版（app.py）必须长得像，否则合并那天会翻车；
- 颜色、圆角、字号集中在一处，改一次两边都变；
- 换主题只改这个文件，业务代码零改动。

用法（必须在 st.set_page_config 之后调用）：
    import theme
    theme.inject(width=800)      # 聊天流用 800
    theme.inject(width=1040)     # 两栏布局用 1040（800 会把右侧回弹挤成三行）

配色（Morandi）：
    背景 #F4F1EC 燕麦米 / 侧栏 #E8E4DF 雾灰 / 文字 #4A4541 深灰褐（不用纯黑）
    AI 气泡 #E3E2DF 冷奶灰 / 用户气泡 #D8C9C8 豆沙粉
    高防御 #C27A7A 暗玫瑰 / 中防御 #D4B895 燕麦黄 / 低防御 #A3B19B 鼠尾草绿
"""
from __future__ import annotations

import streamlit as st

# ───────────────────────────── 调色板 ─────────────────────────────
INK = "#4A4541"          # 深灰褐，全局正文
INK_SOFT = "#7A746E"     # 次要信息
BG = "#F4F1EC"           # 燕麦米
SIDE_BG = "#E8E4DF"      # 雾灰
LINE = "#DED7CB"

BUBBLE_AI = "#E3E2DF"
BUBBLE_ME = "#D8C9C8"

DEF_HIGH = "#C27A7A"     # 4-5 暗玫瑰红
DEF_MID = "#D4B895"      # 3 灰黄/燕麦
DEF_LOW = "#A3B19B"      # 1-2 鼠尾草绿

ALERT_BG = "#E9D6BC"     # 低饱和脏橘/土黄
ALERT_EDGE = "#C89B6A"

BTN_BG = "#DAD3C9"       # 燕麦灰按钮
BTN_HOVER = "#C9C0B4"
BTN_GRAYBLUE = "#B0B8C1"

# 策略 → (背景, 文字)
STRATEGY_COLORS = {
    "要求": ("#E3C9C9", "#8A5757"),
    "解释": ("#E7D9C0", "#84663F"),
    "共情": ("#CBD4C6", "#57654F"),
    "回避": ("#DEDCD8", "#6C6862"),
}


def band_color(score: int) -> str:
    """防御分 1-5 → 三档莫兰迪色（给 defense_bar / 图表复用）。"""
    try:
        score = int(score)
    except (TypeError, ValueError):
        return DEF_MID
    if score >= 4:
        return DEF_HIGH
    if score == 3:
        return DEF_MID
    return DEF_LOW


def _css(width: int) -> str:
    side = "".join(
        f'.chip.{k}, .pill.{k} {{ background:{bg} !important; color:{fg} !important; }}'
        for (bg, fg), k in zip(
            STRATEGY_COLORS.values(), ("req", "exp", "emp", "avo")
        )
    )
    return f"""
<style>
/* ══════════════ 1. 全局：燕麦底 + 深灰褐字，不用纯黑 ══════════════ */
html, body, .stApp {{ background: {BG} !important; color: {INK} !important; }}
.stApp, .stApp * {{
  font-family: "Nunito", "HarmonyOS Sans SC", "PingFang SC",
               "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
  color: {INK};
}}
h1, h2, h3, h4, h5 {{ color: {INK} !important; font-weight: 600 !important; letter-spacing: .01em; }}
p, li, label, span {{ color: inherit; }}
hr {{ border-color: {LINE} !important; opacity: .8; }}
[data-testid="stHeader"] {{ background: transparent !important; }}
.block-container {{ max-width: {width}px; margin: 0 auto; padding-top: 1.9rem; padding-bottom: 7.5rem; }}

/* ══════════════ 2. 侧栏：稍深的雾灰 ══════════════ */
[data-testid="stSidebar"] {{ background: {SIDE_BG} !important; border-right: none !important; }}
[data-testid="stSidebar"] > div {{ padding-top: 1.2rem; }}
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] label, [data-testid="stSidebar"] div {{ color: {INK} !important; }}
[data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small {{ color: {INK_SOFT} !important; }}

/* ══════════════ 3. 气泡：微信式，无边框无阴影 ══════════════ */
div[data-testid="stChatMessage"] {{ padding: .12rem 0 .3rem 0 !important; gap: .45rem !important; }}
.bub {{ display: block; width: fit-content; max-width: 92%; padding: 11px 16px; border-radius: 16px;
        font-size: 1.02rem; line-height: 1.66; }}
.bub p {{ margin: 0 !important; }}
.bub.ai {{ background: {BUBBLE_AI}; color: {INK}; border-bottom-left-radius: 4px;
           box-shadow: none; border: none; }}
.bub.me {{ margin-left: auto; background: {BUBBLE_ME}; color: #4A433F; border-bottom-right-radius: 4px;
           box-shadow: none; border: none; }}
.bub-row.me {{ display: flex; justify-content: flex-end; }}
.status {{ font-size: .82rem; color: {INK_SOFT} !important; margin: 2px 0 0 2px; }}
/* 潜台词：心理学注释，不抢戏——小一号、灰、斜体、无边框 */
.sub {{ font-size: .78rem; color: #9C9691 !important; font-style: italic; line-height: 1.55;
        margin: 5px 0 0 2px; max-width: 92%; letter-spacing: .01em; }}
.sub::before {{ content: ""; }}
/* F13 主动干预：土黄气泡，比 st.warning 更像「对方忍不住插一句」 */
.intervene {{ background: {ALERT_BG}; border-left: 4px solid {ALERT_EDGE}; border-radius: 14px;
              padding: 12px 16px; margin: 10px 0 4px; max-width: 92%;
              font-size: .98rem; line-height: 1.62; color: #6B5844 !important; }}
.intervene b {{ color: #8A4F2B !important; }}

/* ══════════════ 4. 卡片（正式版 app.py 用） ══════════════ */
.card {{ background: #FBF9F4; border: 1px solid {LINE}; border-radius: 18px;
         padding: 18px 20px; margin-bottom: 14px; box-shadow: none; }}
.card.dim {{ background: #EFEBE4; }}
.said {{ font-size: 1.24rem; line-height: 1.7; color: {INK}; }}
.said.reply {{ font-size: 1.42rem; font-weight: 600; }}
.who {{ font-size: .78rem; letter-spacing: .14em; color: {INK_SOFT};
        text-transform: uppercase; margin-bottom: 8px; }}
.hero h1 {{ font-size: 2.4rem; margin-bottom: .2rem; }}
.hero p {{ font-size: 1.1rem; color: {INK_SOFT} !important; margin-top: 0; }}
.muted {{ color: {INK_SOFT} !important; font-size: .9rem; }}
.bignum {{ font-size: 3.4rem; font-weight: 700; line-height: 1; }}
.bignum small {{ font-size: 1.05rem; font-weight: 500; color: {INK_SOFT}; }}
.down {{ color: #8A9A83 !important; }} .up {{ color: {DEF_HIGH} !important; }}
.flat {{ color: {INK_SOFT} !important; }}
.coach {{ border-left: 5px solid #B6C0AE; background: #EDF0E9; padding: 16px 20px;
          border-radius: 0 14px 14px 0; font-size: 1.14rem; line-height: 1.62; margin: 6px 0 4px; }}
.coach.up {{ border-color: {DEF_HIGH}; background: #F3E3E1; }}
.coach.flat {{ border-color: #C7C2B8; background: #EFECE6; }}
.coach-tag {{ display: block; font-size: .76rem; letter-spacing: .16em;
              color: {INK_SOFT} !important; text-transform: uppercase; margin-bottom: 6px; }}
.insight {{ font-size: .93rem; line-height: 1.62; color: #55504A !important; padding: 8px 12px;
            margin-bottom: 8px; background: #F3EFE8; border-left: 3px solid #C9A6A0;
            border-radius: 0 10px 10px 0; }}

/* ══════════════ 5. 策略胶囊：莫兰迪，不刺眼 ══════════════ */
.chip, .pill {{ display: inline-block; padding: 3px 12px; border-radius: 999px;
                font-size: .84rem; font-weight: 600; margin-right: 6px; border: none; }}
{side}
.chip.avoid {{ background: #DEDCD8 !important; color: #6C6862 !important; }}
.pill.def {{ background: #EDE8E1 !important; color: {INK_SOFT} !important; }}

/* ══════════════ 6. 五格情绪刻度 ●●●○○ ══════════════ */
/* 格数 = 防御分，颜色 = 严重度（--dot 由 status_line 内联写入）。
   不再用 st.progress：条长在窄栏里几乎拉不开差距，观众只能靠颜色猜。
   15px 是投影仪下的下限：12px 在 1080p 后排已经看不清。 */
.dots {{ display: flex; align-items: center; height: 16px; margin: 3px 0 0 2px; }}
.dots .dot {{ width: 15px; height: 15px; border-radius: 50%; display: inline-block;
             margin-right: 6px; background: #DAD2C7; flex: 0 0 auto; }}
.dots .dot.on {{ background: var(--dot); }}
/* 左「你的表达」 / 右「她的防御 + 五格」：同一条状态行，不再用两列各渲染一半 */
.statusline {{ display: flex; align-items: center; justify-content: space-between;
               gap: 10px; flex-wrap: wrap; margin: 6px 2px 0 2px; }}
.statusline .expr {{ font-size: .78rem; color: #817970; white-space: nowrap; }}
.statusline .expr .chip {{ margin-right: 0; padding: 2px 9px; font-size: .78rem; }}
.statusline .def {{ display: flex; align-items: center; gap: 7px; white-space: nowrap;
                    font-size: .8rem; color: #5B5550; }}
.statusline .def b {{ font-size: .9rem; color: #3F3A36; }}
.statusline .def .dots {{ margin: 0; height: 16px; }}
.card .statusline {{ margin-top: 12px; padding-top: 10px; border-top: 1px solid #E8E1D5; }}
/* 潜台词默认收起：expander 做成一行低调灰字，不抢对话的戏 */
[data-testid="stExpander"] {{ border: none !important; background: transparent !important;
                              padding: 0 !important; margin-top: 2px !important; }}
[data-testid="stExpander"] summary {{ padding: .2rem 0 !important; }}
[data-testid="stExpander"] summary span {{
  font-size: .78rem !important; color: #A39C94 !important; font-style: italic; }}
/* 复盘页里统一铺开的潜台词卡片 */
.subrow {{ font-size: .8rem; color: #7C756E; padding: 7px 12px; background: #F6F2EB;
           border-radius: 10px; margin-bottom: 6px; line-height: 1.6; }}
.subrow b {{ color: #5B5550; }}

/* ══════════════ 7. 悬浮输入框：像一块软糖 ══════════════ */
div[data-testid="stChatInput"] {{ background: transparent !important; padding-bottom: .4rem; }}
div[data-testid="stChatInput"] > div {{
  background: #FFFCF6 !important; border: 1px solid #EDE6DA !important;
  border-radius: 24px !important; box-shadow: 0 10px 28px rgba(74, 69, 65, .10) !important;
  padding: 4px 10px !important; }}
div[data-testid="stChatInput"] textarea {{
  background: transparent !important; color: {INK} !important; font-size: 1rem !important; }}
div[data-testid="stChatInput"] textarea::placeholder {{ color: {INK_SOFT} !important; }}
div[data-testid="stBottom"] > div {{ background: {BG} !important; }}

/* ══════════════ 8. 按钮 ══════════════ */
.stButton > button, [data-testid="stSidebar"] button {{
  background: {BTN_BG} !important; color: {INK} !important; border: none !important;
  border-radius: 12px !important; font-weight: 500 !important;
  transition: background .18s ease, transform .18s ease; }}
.stButton > button:hover, [data-testid="stSidebar"] button:hover {{
  background: {BTN_HOVER} !important; color: {INK} !important; transform: translateY(-1px); }}
.stButton > button[kind="primary"] {{
  background: #C08C86 !important; color: #FFF8F3 !important; }}
.stButton > button[kind="primary"]:hover {{ background: #B57E78 !important; }}

/* ══════════════ 9. 提示条：脏橘 / 土黄，不刺眼 ══════════════ */
div[data-testid="stAlert"] {{
  background: {ALERT_BG} !important; border: none !important;
  border-left: 4px solid {ALERT_EDGE} !important; border-radius: 16px !important;
  color: #6B5844 !important; box-shadow: none !important; }}
div[data-testid="stAlert"] p, div[data-testid="stAlert"] div {{ color: #6B5844 !important; }}

/* ══════════════ 10. 其余控件统一色温 ══════════════ */
div[data-baseweb="select"] > div {{ background: #FBF8F2 !important; border-color: {LINE} !important;
                                    border-radius: 12px !important; }}
[data-testid="stMetricValue"] {{ color: {INK} !important; }}
div[data-testid="stSpinner"] p {{ color: {INK_SOFT} !important; }}
.stSuccess, div[data-testid="stNotification"] {{ border-radius: 14px !important; }}
</style>
"""


def inject(width: int = 800) -> None:
    """把主题塞进页面。必须在 st.set_page_config 之后、其它内容之前调用。"""
    st.markdown(_css(width), unsafe_allow_html=True)


# ───────────────────────── ToC 沉浸式外壳 ─────────────────────────
# 2026-11-17：从「后台系统」改成「一个 App」——藏掉侧栏/顶栏，左导航 + 中间对话流，
# 所有图表收进弹窗。这段 CSS 只改外观，不动任何数据逻辑。
_TOC_CSS = """
/* 0. 藏掉 Streamlit 的壳，让它像全屏 App */
[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
#MainMenu, footer { display: none !important; }
[data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] > .main,
html, body { background: #F9F8F6 !important; }
body, .stApp { color: #4A4541; line-height: 1.6; }
[data-testid="stMainBlockContainer"] { padding: 1.2rem 1.6rem 2rem; max-width: 1240px; margin: 0 auto; }

/* 图标字体必须救回来：上面的 * { font-family } 会把 Material 图标变成一串 "_arrow_right" 文字 */
[data-testid="stIconMaterial"], [data-testid="stExpanderToggleIcon"],
.material-symbols-rounded, .material-icons, span[class*="material"] {
    font-family: "Material Symbols Rounded", "Material Icons", "Segoe UI Symbol" !important;
    font-feature-settings: "liga" !important;
}

/* 1. 左导航：贴着滚动，越滚越轻 */
.st-key-nav { position: sticky; top: 12px; padding-right: 6px; }
.logo { font-size: 1.32rem; font-weight: 700; letter-spacing: .16em; color: #4A4541; }
.logo i { color: #B08C74; font-style: normal; }
.tagline { font-size: .74rem; color: #8E8780; letter-spacing: .04em; margin: 4px 0 18px; }
.navsep { height: 1px; background: #E7E2DA; margin: 16px 0 12px; }
.navhint { font-size: .71rem; color: #A29B94; letter-spacing: .06em; margin: 0 0 6px; }
.st-key-nav .stButton > button { text-align: left; justify-content: flex-start; width: 100%;
    background: transparent; border: 1px solid transparent; color: #5B5550;
    font-size: .82rem; padding: 3px 10px; border-radius: 10px; min-height: 2rem; }
.st-key-nav .stButton > button:hover { background: #F1ECE4; border-color: #E5DED4; color: #3E3936; }
.st-key-nav .stButton > button[kind="primary"] { background: #EAE1D5; border-color: #DED3C3;
    color: #4A4541; font-weight: 600; }
.st-key-nav .stButton > button[kind="primary"]:hover { background: #E2D7C8; }
.st-key-nav [data-testid="stExpander"] details { border: none; background: transparent; }
.st-key-nav [data-testid="stExpander"] summary { font-size: .74rem; color: #9A938C; letter-spacing: .04em; }
.st-key-nav [data-baseweb="select"] > div { background: #F3EEE6 !important; border-color: #E3DCD2 !important; }

/* 案例 = 导航条目，不是胶囊：6px 小圆角、透明底、强制左对齐、悬浮才变深 */
.st-key-cases [data-testid="stVerticalBlock"] { gap: 1px !important; }
.st-key-cases [data-testid="stElementContainer"] { margin: 0 !important; }
.st-key-cases .stButton > button { width: 100%; justify-content: flex-start; text-align: left;
    background: transparent !important; box-shadow: none !important;
    border: 1px solid transparent !important; border-radius: 6px !important;
    padding: 5px 10px 5px 10px !important; min-height: 1.85rem !important;
    color: #5B5550 !important; font-size: .82rem !important; font-weight: 400 !important; }
.st-key-cases .stButton > button:hover { background: #EFE9E0 !important; border-color: #E7E0D6 !important;
    color: #3E3936 !important; }
.st-key-cases .stButton > button:active { background: #E7DFD3 !important; }
.st-key-cases .stButton > button > div { justify-content: flex-start !important; width: 100%; }
.st-key-cases .stButton > button p { text-align: left !important; margin: 0; font-size: .82rem !important; }

/* 2. 中间：独立滚动的对话流（只有这一栏动，左右两栏钉住） */
.st-key-chat { max-width: 820px; margin: 0 auto;
    height: 78vh; min-height: 420px; max-height: 80vh; overflow-y: auto; overflow-x: hidden;
    display: flex; flex-direction: column; padding-right: 8px;
    scrollbar-width: thin; scrollbar-color: #DCD5CB transparent; }
.st-key-chat::-webkit-scrollbar { width: 6px; }
.st-key-chat::-webkit-scrollbar-thumb { background: #DCD5CB; border-radius: 99px; }
.st-key-chat::-webkit-scrollbar-track { background: transparent; }
.st-key-chat > div, .st-key-chat > div > div[data-testid="stVerticalBlock"] {
    display: flex; flex-direction: column; flex: 1 1 auto; }
/* 每条 st.chat_message 外面套了一层 stLayoutWrapper，Streamlit 给它 flex:1 1 auto，
   于是 5 个孩子平分整屏高度，中间出现 300px 级别的空白（实测：h=315/342）。
   布局容器必须收回 0，只让最后一个（输入框）靠 margin-top:auto 顶到视口底。 */
.st-key-chat > [data-testid="stLayoutWrapper"] { flex: 0 0 auto; }
.st-key-chat > [data-testid="stElementContainer"] { flex: 0 0 auto; }
.st-key-chat > div:last-child { margin-top: auto; }
.st-key-chat [data-testid="stChatInputContainer"],
.st-key-chat [data-testid="stChatInput"] { position: sticky; bottom: 0; z-index: 6;
    background: #F9F8F6; padding: 10px 0 6px; }
.st-key-chat [data-testid="stChatInput"] { margin-top: auto; }
.st-key-chat [data-testid="stChatMessage"] { background: transparent; padding: 0; }
.st-key-chat [data-testid="stVerticalBlockBorderWrapper"] { border: none !important; }

/* 2b. 右栏：实时洞察，钉在视口里，画面随对话自己长 */
.st-key-side { position: sticky; top: 12px; padding-left: 4px; }
.sidehead { font-size: .76rem; letter-spacing: .14em; color: #A08A76; text-transform: uppercase;
    margin: 4px 0 2px; }
.sidenote { font-size: .74rem; color: #A29B94; margin: 0 0 10px; }
.subhead { font-size: .78rem; font-weight: 600; color: #6F6862; margin: 10px 0 2px;
    letter-spacing: .04em; }
.sidehint { font-size: .74rem; color: #A8A19A; line-height: 1.6; margin: 2px 0 0; }
.sidebig { font-size: 1.5rem; font-weight: 700; line-height: 1.1; color: #7A746E; }
.sidebig.down { color: #6E8068; }
.sidebig.up { color: #B4756E; }
.lgrow { display: flex; flex-wrap: wrap; gap: 2px 12px; margin: 2px 0 0; }
.lg { font-size: .72rem; color: #8C857E; display: inline-flex; align-items: center; }
.lg i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 5px; }

/* 2c. 私教的三块牌子：结论卡 / 抄作业 / 性格诊断 */
.verdict { border-radius: 14px; padding: 12px 14px; margin: 2px 0 10px; }
.verdict b { display: block; font-size: 1.02rem; font-weight: 700; letter-spacing: .01em; }
.verdict span { display: block; font-size: .76rem; margin-top: 4px; opacity: .92; line-height: 1.5; }
/* 注意：这段是 _TOC_CSS，普通字符串而不是 f-string，颜色必须写死十六进制，
   写 {DEF_HIGH} 会变成字面量、整条声明被浏览器丢掉（2026-11-17 踩过：结论卡只剩浅底）。 */
.verdict.bad { background: #B8615B; color: #FFF9F7; }
.verdict.warn { background: #D9B36A; color: #463618; }
.verdict.good { background: #7E9573; color: #F7FAF4; }
.verdict.idle { background: #EFEAE2; color: #7A746E; }
.verdict.idle b { font-size: .92rem; font-weight: 600; }

.copybox { border: 1px solid #E4B9B3; background: #FBEDEA; border-radius: 14px;
    padding: 12px 14px; margin: 0 0 6px; }
.copybox b { display: block; font-size: .84rem; color: #9E4B41; margin-bottom: 6px; }
.copybox span { display: block; font-size: .74rem; color: #A5807A; margin-bottom: 4px; }
.copybox i { display: block; font-size: .84rem; color: #6B4A44; font-style: normal;
    line-height: 1.6; background: #FFF9F7; border-radius: 10px; padding: 8px 10px; }

.styletag { font-size: .92rem; font-weight: 700; color: #4A4541; margin: 6px 0 4px;
    line-height: 1.45; }
.stylediag { font-size: .76rem; color: #7E7770; line-height: 1.65; }

.statusline .mood { font-size: .82rem; font-weight: 600; color: #5B5550; white-space: nowrap; }

/* 3. 弹窗：居中、圆一点、带柔光 */
[data-testid="stDialog"] div[role="dialog"] { background: #FFFDFA; border-radius: 22px;
    box-shadow: 0 26px 70px rgba(74, 69, 65, .20); padding: 8px 6px 4px; }
[data-testid="stDialog"] h2 { font-size: 1.28rem; letter-spacing: .02em; }
[data-testid="stDialog"] .tagline { margin-bottom: 6px; }
"""


def inject_toc(width: int = 800) -> None:
    """ToC 沉浸式版：主题 + 外壳。app.py 用这个，app_ui_mock.py 用 inject()。

    _css() 自带 <style>…</style> 包裹，所以这里把外壳 CSS 插到闭合标签**之前**，
    否则 _TOC_CSS 会跑到 style 外面，被当成正文打印出来（2026-11-17 踩过）。
    """
    st.markdown(_css(width).replace("</style>", _TOC_CSS + "</style>"), unsafe_allow_html=True)
