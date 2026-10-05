# -*- coding: utf-8 -*-
"""
Trace 观测看板 —— 从 SQLite Trace 库生成一张纯静态 HTML 看板。

为什么是静态 HTML：
- 作品集是纯静态发布（无后端、无运行时查询），所以看板必须**预渲染**，
  把数据在生成时直接烘进 HTML，用 <details> 做 span 树的展开交互，零 JS 依赖。

用法（在 HRAgent-Harness 目录，先跑过 run_evals.py 落库）：
    python scripts/build_trace_dashboard.py

产出：portfolio/docs/traces.html（作品集第 5 个交付物）
数据源：reports/traces.db（由 evals/runner.py + harness/trace_store.py 落库）
"""
from __future__ import annotations

import html
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness.trace_store import TraceStore  # noqa: E402

DB_PATH = os.path.join(ROOT, "reports", "traces.db")
OUT_PATH = os.path.join(ROOT, "portfolio", "docs", "traces.html")

CSS = """
  :root {
    --ink-900:#0A0F1A; --ink-800:#101828; --paper:#F7F8FA; --card:#FFFFFF;
    --line:#E6E8EE; --text-hi:#101828; --text-mid:#475467; --text-low:#667085;
    --accent:#0E7490; --accent-bright:#22D3EE; --accent-soft:#E0F2FE;
    --ok:#15803D; --ok-soft:#DCFCE7; --warn:#B45309; --warn-soft:#FEF3C7;
    --bad:#B91C1C; --bad-soft:#FEE2E2;
    --radius:14px; --radius-sm:9px;
    --font-ui:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    --font-mono:"SF Mono","JetBrains Mono","Consolas",monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:var(--font-ui); background:var(--paper); color:var(--text-hi); line-height:1.7; font-size:15px; }
  a { color:var(--accent); text-decoration:none; }
  .nav { position:sticky; top:0; z-index:20; background:rgba(10,15,26,.9); backdrop-filter:blur(8px); border-bottom:1px solid rgba(255,255,255,.08); }
  .nav .inner { max-width:1040px; margin:0 auto; padding:14px 24px; display:flex; align-items:center; gap:16px; }
  .nav a { color:var(--accent-bright); font-family:var(--font-mono); font-size:14px; }
  .nav .t { color:#fff; font-weight:600; font-size:14px; }

  header.hero { background:linear-gradient(180deg,var(--ink-900),var(--ink-800)); color:#fff; padding:56px 0 48px; }
  header.hero .wrap { max-width:1040px; margin:0 auto; padding:0 24px; }
  header.hero h1 { margin:0 0 8px; font-size:32px; letter-spacing:-.5px; }
  header.hero h1 span { color:var(--accent-bright); }
  header.hero p { color:#9AA3B2; margin:0 0 28px; max-width:680px; }
  .metrics { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
  @media (max-width:680px){ .metrics{grid-template-columns:repeat(2,1fr);} }
  .m { border:1px solid rgba(255,255,255,.12); border-radius:var(--radius-sm); padding:14px 16px; background:rgba(255,255,255,.03); }
  .m .v { font-family:var(--font-mono); font-size:24px; font-weight:700; color:var(--accent-bright); }
  .m .v.bad { color:#FCA5A5; }
  .m .k { font-size:12px; color:#8B95A7; margin-top:2px; }

  main { max-width:1040px; margin:0 auto; padding:32px 24px 96px; }
  section { margin-bottom:36px; }
  h2 { font-size:22px; margin:0 0 4px; }
  .note { color:var(--text-mid); font-size:14px; margin:0 0 16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:22px 24px; box-shadow:0 1px 2px rgba(16,24,40,.05); }

  .bars { display:flex; flex-direction:column; gap:10px; }
  .bar-row { display:flex; align-items:center; gap:12px; }
  .bar-row .label { width:110px; font-family:var(--font-mono); font-size:13px; color:var(--text-mid); text-align:right; flex:0 0 auto; }
  .bar-track { flex:1; height:20px; background:var(--paper); border-radius:6px; overflow:hidden; }
  .bar-fill { height:100%; background:linear-gradient(90deg,var(--accent),var(--accent-bright)); border-radius:6px; }
  .bar-row .num { font-family:var(--font-mono); font-size:13px; color:var(--text-hi); flex:0 0 auto; }

  table { width:100%; border-collapse:collapse; font-size:13.5px; background:var(--card); border:1px solid var(--line); border-radius:var(--radius-sm); overflow:hidden; }
  th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { background:var(--paper); color:var(--text-mid); font-weight:600; font-size:12px; }
  tr:last-child td { border-bottom:none; }
  .num { text-align:right; font-variant-numeric:tabular-nums; font-family:var(--font-mono); }
  .pill { display:inline-block; font-family:var(--font-mono); font-size:11.5px; font-weight:600; padding:2px 8px; border-radius:6px; }
  .pill.ok { background:var(--ok-soft); color:var(--ok); }
  .pill.bad { background:var(--bad-soft); color:var(--bad); }
  .pill.warn { background:var(--warn-soft); color:var(--warn); }
  .pill.neutral { background:var(--paper); color:var(--text-mid); border:1px solid var(--line); }
  .mono { font-family:var(--font-mono); }

  details.trace { border:1px solid var(--line); border-radius:var(--radius-sm); margin-top:8px; background:var(--card); }
  details.trace summary { cursor:pointer; padding:10px 14px; font-weight:600; font-size:13.5px; list-style:none; }
  details.trace summary::-webkit-details-marker { display:none; }
  details.trace summary .tag { float:right; font-family:var(--font-mono); font-size:12px; color:var(--text-low); }
  .span-tree { padding:0 14px 14px; }
  .span-node { border-left:2px solid var(--line); margin-left:6px; padding:6px 0 6px 12px; position:relative; }
  .span-node .row { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; }
  .span-node .ty { font-family:var(--font-mono); font-size:12px; color:var(--accent); font-weight:700; }
  .span-node .nm { font-family:var(--font-mono); font-size:12.5px; color:var(--text-hi); }
  .span-node .dur { font-family:var(--font-mono); font-size:11.5px; color:var(--text-low); }
  .span-node .st { font-family:var(--font-mono); font-size:11px; font-weight:700; padding:0 6px; border-radius:4px; }
  .span-node .st.ok { background:var(--ok-soft); color:var(--ok); }
  .span-node .st.failed { background:var(--bad-soft); color:var(--bad); }
  .span-node .st.repaired { background:var(--warn-soft); color:var(--warn); }
  .span-node .io { font-family:var(--font-mono); font-size:11.5px; color:var(--text-low); margin-top:2px; word-break:break-all; }
  .span-node .err { font-family:var(--font-mono); font-size:11.5px; color:var(--bad); margin-top:2px; }

  .empty { padding:24px; text-align:center; color:var(--text-low); }
  .callout { border-left:4px solid var(--accent); background:var(--accent-soft); padding:14px 18px; border-radius:0 var(--radius-sm) var(--radius-sm) 0; font-size:13.5px; color:var(--ink-800); margin-top:14px; }
  footer { background:var(--ink-900); color:#8B95A7; padding:40px 0 56px; font-size:13px; }
  footer .wrap { max-width:1040px; margin:0 auto; padding:0 24px; }
  footer strong { color:#fff; }
"""


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _span_tree(spans: list[dict]) -> str:
    """把平铺 span 列表按 parent_id 组装成树，渲染成嵌套 <div>。"""
    children: dict = defaultdict(list)
    roots: list = []
    ids = {s["span_id"] for s in spans}
    for s in spans:
        pid = s.get("parent_id")
        if pid and pid in ids:
            children[pid].append(s)
        else:
            roots.append(s)

    def node(s: dict) -> str:
        st = s.get("status", "ok")
        parts = [f'<div class="row">']
        parts.append(f'<span class="ty">{_esc(s.get("type"))}</span>')
        parts.append(f'<span class="nm">{_esc(s.get("name"))}</span>')
        parts.append(f'<span class="dur">{float(s.get("duration_ms") or 0):.1f}ms</span>')
        if s.get("tokens"):
            parts.append(f'<span class="dur">{s["tokens"]}tok</span>')
        parts.append(f'<span class="st {_esc(st)}">{_esc(st)}</span>')
        parts.append("</div>")
        if s.get("input"):
            parts.append(f'<div class="io">in : {_esc(s["input"])}</div>')
        if s.get("output"):
            parts.append(f'<div class="io">out: {_esc(s["output"])}</div>')
        if s.get("error"):
            parts.append(f'<div class="err">err: {_esc(s["error"])}</div>')
        kids = children.get(s["span_id"], [])
        inner = "".join(f'<div class="span-node">{node(k)}</div>' for k in kids)
        return "".join(parts) + inner

    return "".join(f'<div class="span-node">{node(r)}</div>' for r in roots)


def _build(store: TraceStore) -> str:
    stats = store.stats()
    traces = store.list_traces()
    failed_spans = store.failed_span_breakdown()

    # ---- 失败断言聚合（失败归因的核心） ----
    failed_traces = [t for t in traces if not t["passed"]]
    check_counter: Counter = Counter()
    for t in failed_traces:
        for c in t.get("failed_checks") or []:
            # 失败断言形如 "门控｜xxx" 或 "状态｜xxx"，取断言名（｜前）
            check_counter[c.split("｜")[0] if "｜" in c else c] += 1

    # ---- metrics 卡片 ----
    n_fail = stats["n_failed_traces"]
    metrics = (
        f'<div class="m"><div class="v">{stats["n_traces"]}</div><div class="k">Trace 条数</div></div>'
        f'<div class="m"><div class="v">{stats["n_spans"]}</div><div class="k">Span 总数</div></div>'
        f'<div class="m"><div class="v{" bad" if n_fail else ""}">{n_fail}</div><div class="k">失败 Trace</div></div>'
        f'<div class="m"><div class="v{" bad" if stats["n_failed_spans"] else ""}">{stats["n_failed_spans"]}</div><div class="k">失败 Span</div></div>'
    )

    # ---- span 类型分布条形图 ----
    span_types = stats["span_types"]
    max_n = max(span_types.values()) if span_types else 1
    bars = "".join(
        f'<div class="bar-row"><span class="label">{_esc(t)}</span>'
        f'<span class="bar-track"><span class="bar-fill" style="width:{n / max_n * 100:.0f}%"></span></span>'
        f'<span class="num">{n}</span></div>'
        for t, n in sorted(span_types.items(), key=lambda x: -x[1])
    )

    # ---- 失败归因区 ----
    if n_fail == 0 and not failed_spans:
        attribution = (
            '<div class="empty">本次回归 <b>0 失败</b> —— 引擎层在当前后端下全绿。'
            '接真模型跑 <code>--live</code> 后，内容质量层的失败会在这里按类型归因。</div>'
        )
    else:
        if failed_spans:
            rows = "".join(
                f'<tr><td class="mono">{_esc(r["type"])}</td><td class="mono">{_esc(r["name"])}</td>'
                f'<td><span class="pill {"warn" if r["status"]=="repaired" else "bad"}">{_esc(r["status"])}</span></td>'
                f'<td class="num">{r["c"]}</td></tr>'
                for r in failed_spans
            )
            fail_span_tbl = (
                "<table><thead><tr><th>span 类型</th><th>span 名</th><th>状态</th><th class=\"num\">次数</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )
        else:
            # 引擎层零故障：失败全在裁判判定层（内容质量），这本身就是归因结论
            fail_span_tbl = (
                '<div class="callout"><strong>引擎层 span 零故障。</strong>'
                '没有一条 run / llm / plan / tool_call / gate span 失败——'
                '全部失败发生在<b>裁判判定层</b>（内容质量不达标）。'
                '这正是「失败不在引擎、在模型内容」的数据证据。</div>'
            )
        if check_counter:
            ck_rows = "".join(
                f'<tr><td class="mono">{_esc(k)}</td><td class="num">{v}</td></tr>'
                for k, v in check_counter.most_common()
            )
            check_tbl = (
                "<table><thead><tr><th>失败断言</th><th class=\"num\">涉及 Trace 数</th></tr></thead>"
                f"<tbody>{ck_rows}</tbody></table>"
            )
        else:
            check_tbl = '<div class="empty">无失败断言记录</div>'
        attribution = (
            '<div style="margin-bottom:8px"><strong>引擎层</strong> · 哪类 span 挂了</div>' + fail_span_tbl
            + '<div style="margin:22px 0 8px"><strong>判定层</strong> · 哪个断言没过</div>' + check_tbl
        )

    # ---- trace 列表 + span 树 ----
    trace_rows = []
    for t in traces:
        pill = '<span class="pill ok">通过</span>' if t["passed"] else '<span class="pill bad">失败</span>'
        fcs = "；".join(t.get("failed_checks") or []) or "—"
        trace_rows.append(
            f'<tr><td class="mono">{_esc(t["trace_id"])}</td>'
            f'<td class="mono">{_esc(t["case_id"])} <span style="color:var(--text-low)">r{t["round"]}</span></td>'
            f'<td>{_esc(t["case_title"])}</td><td>{pill}</td>'
            f'<td class="num">{t["span_count"]}</td>'
            f'<td class="num">{t["gate_count"]}</td>'
            f'<td class="num" style="color:{"var(--bad)" if t["failed_spans"] else "inherit"}">{t["failed_spans"]}</td>'
            f'<td style="font-size:12px;color:var(--text-mid)">{_esc(fcs)}</td></tr>'
        )
    trace_tbl = (
        "<table><thead><tr><th>trace_id</th><th>用例</th><th>标题</th><th>结果</th>"
        '<th class="num">span</th><th class="num">门控</th><th class="num">失败 span</th><th>失败断言</th></tr></thead>'
        f"<tbody>{''.join(trace_rows)}</tbody></table>"
    )

    details = ""
    for t in traces[:60]:  # 只展开最近 60 条，避免页面过重
        full = store.get_trace(t["trace_id"])
        tree = _span_tree(full["spans"]) if full else ""
        pill = "通过" if t["passed"] else "失败"
        details += (
            f'<details class="trace"><summary>{_esc(t["case_id"])} r{t["round"]} · '
            f'{_esc(t["case_title"])} <span class="tag">{_esc(t["trace_id"])} · {pill}</span></summary>'
            f'<div class="span-tree">{tree}</div></details>'
        )

    body = f"""
<header class="hero"><div class="wrap">
  <h1>Trace <span>观测看板</span></h1>
  <p>每次运行的完整 span 链路落进 SQLite：失败可查询、可聚合、可归因。这是「看得见」三字从终端文本到可查数据的落地。</p>
  <div class="metrics">{metrics}</div>
</div></header>
<main>
  <section>
    <h2>Span 类型分布</h2>
    <p class="note">每个 span 类型对应引擎的一个环节，分布能看出「一次运行都经过了什么」。</p>
    <div class="card"><div class="bars">{bars}</div></div>
  </section>
  <section>
    <h2>失败归因</h2>
    <p class="note">区分「哪类 span 挂了」和「哪个断言没过」——前者指向引擎，后者指向用例/模型。</p>
    <div class="card">{attribution}</div>
    <div class="callout"><strong>怎么读：</strong>失败 span 集中在 <code>llm</code> 指向模型/网络，集中在 <code>tool_call</code> 指向工具执行；失败断言指向的是「评测预期」而非引擎缺陷。三桶要分开看，别混。</div>
  </section>
  <section>
    <h2>Trace 列表（可点开回放 span 树）</h2>
    <p class="note">每条 trace 一行；点下面的条目可展开该次运行的完整 span 链路。</p>
    <div class="card">{trace_tbl}</div>
    <div style="margin-top:12px">{details}</div>
  </section>
</main>
<footer><div class="wrap">
  <strong>诚实声明：</strong>这是轻量自建的可观测存储（sqlite3 标准库），不是 OpenTelemetry / 生产级 Trace 平台。
  数据来自离线固定用例集，非真实线上流量。看板为「验证受控 Agent 运行时」而建，用于展示失败可定位能力。
</div></footer>
"""
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        '<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        "<title>Trace 观测看板 · HRAgent Harness</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        '<div class="nav"><div class="inner"><a href="index.html">← 返回作品集</a>'
        '<span class="t">Trace 观测看板</span></div></div>\n'
        f"{body}\n</body>\n</html>\n"
    )


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"未找到 Trace 库 {DB_PATH}，请先跑 python scripts/run_evals.py")
        return 1
    store = TraceStore(DB_PATH)
    try:
        page = _build(store)
    finally:
        store.close()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {os.path.relpath(OUT_PATH, ROOT)}  ({len(page)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
