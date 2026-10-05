# -*- coding: utf-8 -*-
"""
把交付物 Markdown 文档转成带样式的静态 HTML，供作品集站内点击。
零运行时依赖（仅构建时用 markdown 库）。

用法（在 HRAgent-Harness 目录）：
    python scripts/build_portfolio.py

产出：portfolio/docs/*.html（与 portfolio/index.html 同套设计语言）
"""
from __future__ import annotations

import os
import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_OUT = os.path.join(ROOT, "portfolio", "docs")

# 交付物映射：输出文件名 -> (标题, [源 .md 路径...])（多篇会拼接）
DELIVERABLES = {
    "prd-adr.html": (
        "PRD + ADR 文档",
        [
            "docs/00-PRD-受控Agent运行时.md",
            "docs/01-ADR-上下文预算与裁剪策略.md",
            "docs/02-ADR-工具选择策略.md",
            "docs/03-ADR-什么时候不该用Dify.md",
        ],
    ),
    "harness.html": ("可运行 harness 代码", ["README.md"]),
    "report.html": ("评测报告 + Dify 对比", ["reports/dify_vs_harness.md"]),
    "debug.html": ("调试日志", ["docs/99-问题与解决记录.md"]),
}

DOC_CSS = """
  :root {
    --ink-900:#0A0F1A; --ink-800:#101828; --paper:#F7F8FA; --card:#FFFFFF;
    --line:#E6E8EE; --text-hi:#101828; --text-mid:#475467; --text-low:#667085;
    --accent:#0E7490; --accent-bright:#22D3EE; --accent-soft:#E0F2FE;
    --warn:#B45309; --warn-soft:#FEF3C7; --bad:#B91C1C; --bad-soft:#FEE2E2;
    --radius-sm:9px;
    --font-ui:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    --font-mono:"SF Mono","JetBrains Mono","Consolas",monospace;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family:var(--font-ui); background:var(--paper); color:var(--text-hi); line-height:1.75; font-size:15px; }
  .doc-nav { position:sticky; top:0; z-index:20; background:rgba(10,15,26,.9); backdrop-filter:blur(8px); border-bottom:1px solid rgba(255,255,255,.08); }
  .doc-nav .inner { max-width:860px; margin:0 auto; padding:14px 24px; display:flex; align-items:center; gap:16px; }
  .doc-nav a { color:var(--accent-bright); text-decoration:none; font-size:14px; font-family:var(--font-mono); }
  .doc-nav a:hover { text-decoration:underline; }
  .doc-nav .t { color:#fff; font-weight:600; font-size:14px; }
  article.markdown { max-width:860px; margin:0 auto; padding:40px 24px 96px; }
  .markdown h1 { font-size:30px; letter-spacing:-0.5px; margin:0 0 8px; padding-bottom:12px; border-bottom:2px solid var(--line); }
  .markdown h2 { font-size:24px; margin:44px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
  .markdown h3 { font-size:19px; margin:30px 0 10px; }
  .markdown h4 { font-size:16px; margin:22px 0 8px; color:var(--text-mid); }
  .markdown p { margin:12px 0; }
  .markdown a { color:var(--accent); }
  .markdown code { font-family:var(--font-mono); font-size:13px; background:var(--paper); border:1px solid var(--line); padding:1px 6px; border-radius:5px; color:#0E7490; }
  .markdown pre { background:var(--ink-800); color:#D6DEE9; border-radius:var(--radius-sm); padding:16px 18px; overflow-x:auto; line-height:1.65; }
  .markdown pre code { background:none; border:none; padding:0; color:inherit; font-size:13px; }
  .markdown blockquote { margin:16px 0; border-left:4px solid var(--accent); background:var(--accent-soft); padding:12px 18px; border-radius:0 var(--radius-sm) var(--radius-sm) 0; color:var(--ink-800); }
  .markdown blockquote p { margin:4px 0; }
  .markdown table { width:100%; border-collapse:collapse; font-size:14px; background:var(--card); border:1px solid var(--line); border-radius:var(--radius-sm); overflow:hidden; margin:16px 0; }
  .markdown th, .markdown td { text-align:left; padding:10px 14px; border-bottom:1px solid var(--line); vertical-align:top; }
  .markdown th { background:var(--paper); color:var(--text-mid); font-weight:600; font-size:13px; }
  .markdown tr:last-child td { border-bottom:none; }
  .markdown ul, .markdown ol { margin:10px 0; padding-left:24px; }
  .markdown li { margin:5px 0; }
  .markdown hr { border:none; border-top:1px solid var(--line); margin:32px 0; }
  .markdown img { max-width:100%; }
"""


def render_md(paths: list[str]) -> str:
    chunks = []
    for rel in paths:
        full = os.path.join(ROOT, rel.replace("/", os.sep))
        with open(full, "r", encoding="utf-8") as f:
            chunks.append(f.read())
    text = "\n\n---\n\n".join(chunks)
    return markdown.markdown(text, extensions=["tables", "sane_lists", "fenced_code"])


def page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"<title>{title} · HRAgent Harness</title>\n"
        f"<style>{DOC_CSS}</style>\n</head>\n<body>\n"
        "<div class=\"doc-nav\"><div class=\"inner\">"
        "<a href=\"../index.html\">← 返回作品集</a>"
        f"<span class=\"t\">{title}</span></div></div>\n"
        f"<article class=\"markdown\">\n{body}\n</article>\n</body>\n</html>\n"
    )


def main() -> None:
    os.makedirs(DOCS_OUT, exist_ok=True)
    for out_name, (title, paths) in DELIVERABLES.items():
        body = render_md(paths)
        out_path = os.path.join(DOCS_OUT, out_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page(title, body))
        print(f"wrote {os.path.relpath(out_path, ROOT)}  ({len(body)} bytes)")


if __name__ == "__main__":
    main()
