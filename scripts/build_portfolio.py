# -*- coding: utf-8 -*-
"""
把交付物 Markdown 文档转成带样式的静态 HTML，供作品集站内点击。
零运行时依赖（仅构建时用 markdown 库）。

用法（在 HRAgent-Harness 目录）：
    python scripts/build_portfolio.py

产出：portfolio/docs/*.html（与 portfolio/index.html 同套设计语言）
新增：每页自动生成「目录」侧栏（TOC，取 h2/h3，中文标题锚点），移动端折叠为页首块。
"""
from __future__ import annotations

import os
import re
import markdown
from markdown.extensions.toc import TocExtension

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
    "failure-modes.html": ("失败模式手册", ["docs/05-失败模式手册.md"]),
    "prompt-iteration.html": ("Prompt 迭代记录", ["docs/06-Prompt迭代记录.md"]),
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
  .doc-nav .inner { max-width:1100px; margin:0 auto; padding:14px 24px; display:flex; align-items:center; gap:16px; }
  .doc-nav a { color:var(--accent-bright); text-decoration:none; font-size:14px; font-family:var(--font-mono); }
  .doc-nav a:hover { text-decoration:underline; }
  .doc-nav .t { color:#fff; font-weight:600; font-size:14px; }

  .doc-layout { max-width:1100px; margin:0 auto; display:flex; gap:36px; align-items:flex-start; padding:0 24px 96px; }

  .toc-side { position:sticky; top:76px; flex:0 0 236px; max-height:calc(100vh - 100px); overflow-y:auto; padding:28px 0 4px; }
  .toc-side summary { list-style:none; cursor:pointer; font-family:var(--font-mono); font-size:12px; font-weight:700; color:var(--text-low); letter-spacing:.6px; text-transform:uppercase; user-select:none; margin-bottom:10px; }
  .toc-side summary::-webkit-details-marker { display:none; }
  .toc ul { list-style:none; margin:0; padding:0; }
  .toc ul ul { margin-left:13px; padding-left:11px; border-left:1px solid var(--line); }
  .toc li { margin:2px 0; }
  .toc a { color:var(--text-mid); font-size:13px; line-height:1.55; text-decoration:none; display:block; padding:2px 0; }
  .toc a:hover { color:var(--accent); }
  .toc ul ul a { color:var(--text-low); font-size:12.5px; }

  article.markdown { flex:1 1 auto; min-width:0; padding:40px 0 0; }
  .markdown h1 { font-size:30px; letter-spacing:-0.5px; margin:0 0 8px; padding-bottom:12px; border-bottom:2px solid var(--line); }
  .markdown h2 { font-size:24px; margin:44px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
  .markdown h3 { font-size:19px; margin:30px 0 10px; }
  .markdown h4 { font-size:16px; margin:22px 0 8px; color:var(--text-mid); }
  .markdown h1, .markdown h2, .markdown h3, .markdown h4 { scroll-margin-top: 76px; }
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

  @media (max-width: 900px) {
    .doc-layout { flex-direction:column; gap:8px; }
    .toc-side { position:static; flex:0 0 auto; width:100%; max-height:none; padding:20px 0 0; border-bottom:1px solid var(--line); }
    .toc ul ul { display:none; }
    article.markdown { padding:24px 0 0; }
  }
"""


def _slugify(value: str, separator: str = "-") -> str:
    """中文标题友好的锚点 slug：保留汉字/英文/数字，其余转 separator。"""
    value = value.strip().lower()
    value = re.sub(r"[^\w\u4e00-\u9fff]+", separator, value, flags=re.UNICODE)
    return value.strip(separator)


def render_md(paths: list[str]) -> tuple[str, str]:
    """返回 (body_html, toc_html)。toc_html 是 <ul>…</ul>（无 h2/h3 时为空串）。"""
    chunks = []
    for rel in paths:
        full = os.path.join(ROOT, rel.replace("/", os.sep))
        with open(full, "r", encoding="utf-8") as f:
            chunks.append(f.read())
    text = "\n\n---\n\n".join(chunks)

    md = markdown.Markdown(
        extensions=[
            "tables",
            "sane_lists",
            "fenced_code",
            TocExtension(toc_depth="2-3", slugify=_slugify),
        ]
    )
    body = md.convert(text)
    toc_raw = getattr(md, "toc", "")
    m = re.search(r"<ul>.*</ul>", toc_raw, re.S)
    toc_html = m.group(0) if m else ""
    return body, toc_html


def page(title: str, body: str, toc_html: str) -> str:
    toc_block = ""
    if toc_html:
        toc_block = (
            '<aside class="toc-side"><details class="toc" open>'
            f"<summary>目录</summary>\n{toc_html}\n"
            "</details></aside>"
        )
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"<title>{title} · HRAgent Harness</title>\n"
        f"<style>{DOC_CSS}</style>\n</head>\n<body>\n"
        "<div class=\"doc-nav\"><div class=\"inner\">"
        "<a href=\"../index.html\">← 返回作品集</a>"
        f"<span class=\"t\">{title}</span></div></div>\n"
        f"<div class=\"doc-layout\">\n{toc_block}\n"
        f"<article class=\"markdown\">\n{body}\n</article>\n</div>\n"
        "</body>\n</html>\n"
    )


def main() -> None:
    os.makedirs(DOCS_OUT, exist_ok=True)
    for out_name, (title, paths) in DELIVERABLES.items():
        body, toc_html = render_md(paths)
        out_path = os.path.join(DOCS_OUT, out_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page(title, body, toc_html))
        n_toc = toc_html.count("<li>")
        print(f"wrote {os.path.relpath(out_path, ROOT)}  ({len(body)} bytes, {n_toc} toc items)")


if __name__ == "__main__":
    main()
