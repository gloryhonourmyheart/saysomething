"""把 docs/*.md 渲染成可直接双击查看的单文件 HTML（带样式、可打印）。

用法：python scripts/build_docs.py
"""
from __future__ import annotations

import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 56px 28px 96px;
  background: #ffffff; color: #1f2933;
  font: 16px/1.78 "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", system-ui, sans-serif;
  max-width: 900px; margin-left: auto; margin-right: auto;
  -webkit-font-smoothing: antialiased;
}
h1 { font-size: 2em; line-height: 1.3; margin: 0 0 .8em; padding-bottom: .4em; border-bottom: 2px solid #1f2933; letter-spacing: .02em; }
h2 { font-size: 1.35em; margin: 2.6em 0 .9em; padding-left: 12px; border-left: 5px solid #e0432f; line-height: 1.4; }
h3 { font-size: 1.1em; margin: 2em 0 .6em; color: #111827; }
h4 { font-size: 1em; margin: 1.6em 0 .5em; }
p { margin: .8em 0; }
strong { color: #111827; }
hr { border: none; border-top: 1px solid #e5e9ef; margin: 2.6em 0; }
a { color: #c2412d; }
ul, ol { padding-left: 1.5em; }
li { margin: .35em 0; }
table { border-collapse: collapse; width: 100%; margin: 1.2em 0; font-size: .9em; }
th, td { border: 1px solid #e1e7ee; padding: 9px 12px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 700; white-space: nowrap; }
tr:nth-child(even) td { background: #fcfdfe; }
code { background: #f2f4f7; padding: 2px 6px; border-radius: 5px; font-size: .9em;
       font-family: "JetBrains Mono", Consolas, "Courier New", monospace; color: #b3301c; }
pre { background: #0f1720; color: #e6edf3; padding: 16px 18px; border-radius: 10px;
      overflow-x: auto; line-height: 1.55; font-size: .86em; }
pre code { background: none; color: inherit; padding: 0; }
blockquote { margin: 1.2em 0; padding: 12px 18px; background: #fffbf0;
             border-left: 4px solid #f2b84b; color: #4a3a10; }
blockquote p { margin: .2em 0; }
.doc-nav { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 36px; font-size: .88em; }
.doc-nav a { text-decoration: none; padding: 6px 14px; border: 1px solid #e1e7ee;
             border-radius: 999px; color: #374151; }
.doc-nav a:hover { border-color: #c2412d; color: #c2412d; }
.doc-nav a.active { background: #1f2933; color: #fff; border-color: #1f2933; }
.foot { margin-top: 64px; padding-top: 18px; border-top: 1px solid #e5e9ef; color: #8a94a3; font-size: .82em; }
@media print {
  body { padding: 0; max-width: none; font-size: 12pt; }
  .doc-nav { display: none; }
  h2 { page-break-after: avoid; }
  table, pre, blockquote { page-break-inside: avoid; }
}
"""

PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 刺与泡沫</title>
<style>{css}</style></head>
<body>
<nav class="doc-nav">
  <a href="index.html">文档目录</a>
  <a href="PRD.html"{a1}>PRD 产品需求</a>
  <a href="PLAN.html"{a2}>冲刺作战计划</a>
</nav>
{body}
<p class="foot">刺与泡沫（关系演练场）· 虹桥国际科技周黑客松 AI+Agent 赛道 · 本文档由 Markdown 生成，改内容请改 docs/{src}</p>
</body></html>
"""

INDEX = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>刺与泡沫 · 文档</title><style>{css}
.card {{ display: block; text-decoration: none; color: inherit; border: 1px solid #e1e7ee;
        border-radius: 14px; padding: 22px 24px; margin: 16px 0; transition: .18s; }}
.card:hover {{ border-color: #c2412d; transform: translateY(-2px); box-shadow: 0 12px 30px -18px #000; }}
.card h3 {{ margin: 0 0 6px; font-size: 1.15em; }}
.card p {{ margin: 0; color: #5b6675; font-size: .9em; }}
.badge {{ font-size: .72em; color: #8a94a3; letter-spacing: .14em; text-transform: uppercase; }}
</style></head>
<body>
<h1>刺与泡沫 · 文档</h1>
<p>虹桥国际科技周黑客松 · AI+Agent 赛道（11 月 21 日）· 两人份执行文档 · 更新于 11/17 14:50。</p>
<a class="card" href="PRD.html">
  <span class="badge">PRD v3.1</span>
  <h3>产品需求文档</h3>
  <p>一句话、Agent 三能力、核心机制、功能清单、小抄设计（F17）、演示脚本、答辩话术。发给队友看的那份。</p>
</a>
<a class="card" href="PLAN.html">
  <span class="badge">PLAN v3.1</span>
  <h3>冲刺作战计划</h3>
  <p>大白话版：现在能演什么、今天还剩什么、后面三天怎么排、上台前 checklist、时间不够先砍谁。</p>
</a>
<p class="foot">改内容请改 docs/PRD.md 与 docs/PLAN.md，然后运行 python scripts/build_docs.py 重新生成。</p>
</body></html>
"""


def render(src: Path, *, active: int) -> str:
    text = src.read_text(encoding="utf-8")
    html = markdown.markdown(
        text, extensions=["tables", "fenced_code", "sane_lists", "nl2br"], output_format="html5"
    )
    html = html.replace("<li>[ ] ", "<li>☐ ").replace("<li>[x] ", "<li>☑ ")
    body = re.sub(r"<p>\s*</p>\n?", "", html)
    title = "产品需求文档 PRD" if active == 1 else "冲刺作战计划"
    return PAGE.format(
        title=title,
        css=CSS,
        body=body,
        src=src.name,
        a1=' class="active"' if active == 1 else "",
        a2=' class="active"' if active == 2 else "",
    )


def main() -> None:
    targets = [(DOCS / "PRD.md", 1, "PRD.html"), (DOCS / "PLAN.md", 2, "PLAN.html")]
    for src, active, out in targets:
        if not src.exists():
            print(f"跳过（不存在）：{src}")
            continue
        (DOCS / out).write_text(render(src, active=active), encoding="utf-8")
        print(f"生成：docs/{out}")
    (DOCS / "index.html").write_text(INDEX.format(css=CSS), encoding="utf-8")
    print("生成：docs/index.html")


if __name__ == "__main__":
    main()
