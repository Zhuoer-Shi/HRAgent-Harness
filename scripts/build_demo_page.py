# -*- coding: utf-8 -*-
"""
把 demo 的 5 个场景跑一遍，捕获真实 Trace，渲染成交互式回放页 portfolio/demo.html。

用法（在 HRAgent-Harness 目录）：
    python scripts/build_demo_page.py            # 用 Stub（不花钱，离线可复现）
    python scripts/build_demo_page.py --live     # 用 DeepSeek 真模型（需 DEEPSEEK_API_KEY）

产出：portfolio/demo.html —— 自包含（数据内嵌），双击即可打开，也能上 GitHub Pages。
面试官点一个场景，就能逐步看到 Agent 的完整决策链路：
意图识别 → 注入工具 → 模型规划 → 决策动作 → 门控拦截 → 工具执行 → 最终响应。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.llm import build_llm  # noqa: E402
from harness.runner import STATUS_AWAITING_CONFIRMATION, AgentRunner  # noqa: E402
from tools.hr_tools import SCHEDULED_INTERVIEWS, SENT_EMAILS, build_registry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "portfolio", "demo.html")

# 5 个场景：输入 + 一句话概念 + 它验证什么
SCENARIOS = [
    ("帮我写一份 Java 后端工程师的 JD", "低风险任务 · 自动完成",
     "低风险任务不该被门控打断，直接调 generate_jd 跑完。"),
    ("帮我筛一下简历", "缺前置条件 · 追问",
     "没有 JD 就筛简历，必须追问而不是硬做（MISSING_PREREQUISITE）。"),
    ("给张三安排明天下午面试", "高风险动作 · 门控拦截",
     "安排面试属高风险：门控拦下、只出草稿，人工确认后才真正执行。"),
    ("发邮件通知候选人面试时间", "缺信息 · 追问（发邮件不可逆）",
     "发邮件不可逆，真模型先追问缺的收件人/主题/正文（MISSING_INFO），而不是盲目发送。门控（MISSING_CONFIRMATION）由场景 3 展示——两者共同构成「受控」。"),
    ("帮我查一下年假制度", "知识检索 · 自动完成",
     "查制度走 search_knowledge，返回带来源的片段。"),
]


def capture(llm) -> dict:
    registry = build_registry()
    scenarios = []
    for i, (text, concept, note) in enumerate(SCENARIOS, 1):
        # 每个场景独立会话 + 清空副作用（BUG-002 教训）
        SCHEDULED_INTERVIEWS.clear()
        SENT_EMAILS.clear()
        runner = AgentRunner(llm, registry, max_steps=6, auto_confirm=False)
        result = runner.run(text)
        ctx = result.context_report or {}

        item = {
            "id": i,
            "input": text,
            "concept": concept,
            "note": note,
            "status": result.status,
            "state": result.state,
            "message": result.message,
            "tools_called": list(result.tools_called),
            "pending_tool": result.pending_tool,
            "tools_selected": list(ctx.get("tools_selected") or []),
            "tools_dropped": list(ctx.get("tools_dropped") or []),
            "trace": _trace_dict(runner),
        }

        # 高风险场景：模拟人工确认，捕获「确认后才执行」的第二段链路
        if result.status == STATUS_AWAITING_CONFIRMATION and result.pending_tool:
            confirm = runner.confirm_and_execute(
                result.pending_tool, result.pending_args or {}
            )
            item["confirm"] = {
                "message": confirm.message,
                "trace": _trace_dict(runner),
                "side_effect": {
                    "scheduled": list(SCHEDULED_INTERVIEWS),
                    "sent": list(SENT_EMAILS),
                },
            }
        scenarios.append(item)

    return {
        "backend": llm.name,
        "side_effect": {
            "scheduled": list(SCHEDULED_INTERVIEWS),
            "sent": list(SENT_EMAILS),
        },
        "scenarios": scenarios,
    }


def _trace_dict(runner: AgentRunner) -> dict | None:
    tracer = getattr(runner, "_last_tracer", None)
    return tracer.to_dict() if tracer else None


# ---------------------------------------------------------------------------
# 渲染：交互式回放页（数据内嵌，纯静态 + 少量内联 JS）
# ---------------------------------------------------------------------------

_CSS = """
  :root {
    --ink-900:#0A0F1A; --ink-800:#101828; --paper:#F7F8FA; --card:#FFFFFF;
    --line:#E6E8EE; --text-hi:#101828; --text-mid:#475467; --text-low:#667085;
    --accent:#0E7490; --accent-bright:#22D3EE; --accent-soft:#E0F2FE;
    --warn:#B45309; --warn-soft:#FEF3C7; --bad:#B91C1C; --bad-soft:#FEE2E2;
    --ok:#15803D; --ok-soft:#DCFCE7; --violet:#7C3AED; --violet-soft:#F3E8FF;
    --radius-sm:9px;
    --font-ui:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    --font-mono:"SF Mono","JetBrains Mono","Consolas",monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:var(--font-ui); background:var(--paper); color:var(--text-hi); line-height:1.7; font-size:15px; }
  .top { background:var(--ink-900); color:#fff; }
  .top .inner { max-width:1100px; margin:0 auto; padding:16px 24px; display:flex; align-items:center; gap:16px; }
  .top a { color:var(--accent-bright); text-decoration:none; font-size:14px; font-family:var(--font-mono); }
  .top .t { font-weight:600; font-size:14px; }
  .top .badge { margin-left:auto; font-family:var(--font-mono); font-size:12px; color:var(--accent-bright); border:1px solid rgba(34,211,238,.4); padding:2px 8px; border-radius:999px; }

  .hero { background:var(--ink-900); color:#fff; border-bottom:1px solid rgba(255,255,255,.08); }
  .hero .inner { max-width:1100px; margin:0 auto; padding:34px 24px 30px; }
  .hero h1 { margin:0 0 8px; font-size:26px; letter-spacing:-.4px; }
  .hero p { margin:0; color:#9AA3B2; font-size:14px; max-width:720px; }
  .hero .trace-line { display:flex; gap:6px; margin-top:18px; }
  .hero .dot { width:9px; height:9px; border-radius:50%; background:#1E293B; }
  .hero .dot.on { background:var(--accent-bright); box-shadow:0 0 8px var(--accent-bright); }

  .layout { max-width:1100px; margin:0 auto; display:flex; gap:24px; align-items:flex-start; padding:24px; }
  .side { flex:0 0 260px; position:sticky; top:16px; }
  .side h2 { font-size:12px; letter-spacing:.8px; text-transform:uppercase; color:var(--text-low); font-family:var(--font-mono); margin:0 0 10px; }
  .scen { display:block; width:100%; text-align:left; border:1px solid var(--line); background:var(--card); border-radius:var(--radius-sm); padding:12px 14px; margin-bottom:8px; cursor:pointer; font-family:var(--font-ui); transition:border-color .15s, box-shadow .15s; }
  .scen:hover { border-color:var(--accent); }
  .scen.active { border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }
  .scen .no { font-family:var(--font-mono); font-size:11px; color:var(--accent); }
  .scen .t { display:block; font-weight:600; font-size:14px; margin-top:2px; }
  .scen .c { display:block; font-size:12px; color:var(--text-mid); margin-top:2px; }

  .main { flex:1 1 auto; min-width:0; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:var(--radius-sm); padding:20px 22px; margin-bottom:16px; }
  .panel h3 { margin:0 0 4px; font-size:15px; }
  .panel .sub { color:var(--text-low); font-size:13px; margin:0 0 12px; }
  .bubble { background:var(--accent-soft); border-left:4px solid var(--accent); padding:12px 16px; border-radius:0 var(--radius-sm) var(--radius-sm) 0; font-size:15px; }

  .controls { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .btn { border:1px solid var(--line); background:var(--card); color:var(--text-hi); padding:7px 14px; border-radius:7px; cursor:pointer; font-size:13px; font-family:var(--font-ui); }
  .btn:hover { border-color:var(--accent); color:var(--accent); }
  .btn.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  .btn:disabled { opacity:.4; cursor:not-allowed; }
  .progress { font-family:var(--font-mono); font-size:13px; color:var(--text-low); margin-left:auto; }

  .timeline { list-style:none; margin:0; padding:0; }
  .step { display:flex; gap:12px; padding:10px 0; border-left:2px solid var(--line); margin-left:7px; padding-left:18px; opacity:.32; transition:opacity .2s; }
  .step.done { opacity:.75; }
  .step.current { opacity:1; border-left-color:var(--accent-bright); }
  .step .node { flex:0 0 10px; height:10px; width:10px; border-radius:50%; background:#Cbd5E1; margin-top:7px; margin-left:-24px; }
  .step.current .node { background:var(--accent-bright); box-shadow:0 0 0 4px var(--accent-soft); }
  .step.done .node { background:var(--accent); }
  .step .body { min-width:0; }
  .step .head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .pill { font-family:var(--font-mono); font-size:11px; padding:1px 8px; border-radius:999px; white-space:nowrap; }
  .pill.run { background:#F1F5F9; color:#475467; }
  .pill.intent { background:var(--accent-soft); color:var(--accent); }
  .pill.llm { background:#CFFAFE; color:#0E7490; }
  .pill.plan { background:var(--warn-soft); color:var(--warn); }
  .pill.gate { background:var(--bad-soft); color:var(--bad); font-weight:700; }
  .pill.tool { background:var(--ok-soft); color:var(--ok); }
  .pill.respond { background:var(--violet-soft); color:var(--violet); }
  .pill.repair { background:var(--warn-soft); color:var(--warn); }
  .step .name { font-family:var(--font-mono); font-size:13px; color:var(--text-hi); }
  .step .meta { font-family:var(--font-mono); font-size:11px; color:var(--text-low); }
  .step .io { margin-top:6px; font-size:13px; color:var(--text-mid); }
  .step .io code { background:var(--paper); border:1px solid var(--line); padding:1px 5px; border-radius:4px; font-family:var(--font-mono); font-size:12px; color:var(--text-hi); }
  .step .io .k { color:var(--text-low); font-size:12px; margin-right:4px; }
  .divider { list-style:none; font-family:var(--font-mono); font-size:12px; color:var(--accent); padding:12px 0 4px; font-weight:600; }
  .gate-callout { background:var(--bad-soft); border:1px solid #FECACA; border-radius:var(--radius-sm); padding:12px 16px; margin:8px 0; font-size:14px; }
  .gate-callout b { color:var(--bad); }

  .result { display:flex; gap:12px; align-items:flex-start; }
  .st { font-family:var(--font-mono); font-size:12px; font-weight:700; padding:3px 10px; border-radius:999px; white-space:nowrap; }
  .st.done { background:var(--ok-soft); color:var(--ok); }
  .st.awaiting_input { background:var(--warn-soft); color:var(--warn); }
  .st.awaiting_confirmation { background:var(--bad-soft); color:var(--bad); }
  .st.failed { background:var(--bad-soft); color:var(--bad); }
  .st.max_steps_exceeded { background:var(--bad-soft); color:var(--bad); }
  .msg { white-space:pre-wrap; font-size:14px; color:var(--text-hi); margin:0; }

  .sidefx { font-size:13px; color:var(--text-mid); }
  .sidefx code { font-family:var(--font-mono); font-size:12px; }

  @media (max-width:820px) {
    .layout { flex-direction:column; }
    .side { position:static; flex:0 0 auto; width:100%; }
  }
"""

_JS = """
const DATA = __DATA_JSON__;
const TYPE = {
  run:       { label: '会话',    cls: 'run',   icon: '' },
  llm:       { label: '模型规划', cls: 'llm',   icon: '' },
  gate:      { label: '门控',    cls: 'gate',  icon: '' },
  tool_call: { label: '工具执行', cls: 'tool',  icon: '' },
  respond:   { label: '最终响应', cls: 'respond', icon: '' },
  plan:      { label: '决策动作', cls: 'plan',  icon: '' },
};
function classify(s) {
  if (s.type === 'plan' && s.name === 'context.build') return { label: '意图识别', cls: 'intent' };
  if (s.type === 'plan' && (s.name || '').startsWith('plan.repair')) return { label: '修复重试', cls: 'repair' };
  return TYPE[s.type] || { label: s.type, cls: 'plan' };
}
function stepsOf(trace) {
  if (!trace || !trace.spans) return [];
  return trace.spans.map((s, i) => {
    const c = classify(s);
    return { i, type: s.type, name: s.name, label: c.label, cls: c.cls,
             input: s.input || '', output: s.output || '', dur: s.duration_ms,
             tok: s.tokens || 0, status: s.status, err: s.error || '' };
  });
}

let CUR = null;   // 当前场景
let STEPS = [];   // 当前场景的步骤（含确认段）
let IDX = -1;     // 当前高亮到的步
let SPLIT = 0;    // 主链路步数（确认段从这之后开始）
let timer = null;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function renderScen(sc, idx) {
  const a = document.createElement('button');
  a.className = 'scen' + (CUR && CUR.id === sc.id ? ' active' : '');
  a.innerHTML = '<span class="no">场景 ' + sc.id + '</span>' +
                '<span class="t">' + esc(sc.input) + '</span>' +
                '<span class="c">' + esc(sc.concept) + '</span>';
  a.onclick = () => load(idx);
  return a;
}

function load(idx) {
  stopAuto();
  const sc = DATA.scenarios[idx];
  CUR = sc;
  // 组装步骤：主链路 + 确认段（确认段在 SPLIT 之后）
  STEPS = stepsOf(sc.trace);
  SPLIT = STEPS.length;
  if (sc.confirm) {
    STEPS = STEPS.concat(stepsOf(sc.confirm.trace));
  }
  IDX = -1;
  // 侧栏高亮
  document.querySelectorAll('.scen').forEach((el, i) => el.classList.toggle('active', i === idx));
  // 场景头
  document.getElementById('scen-title').textContent = '场景 ' + sc.id + ' · ' + sc.concept;
  document.getElementById('scen-note').textContent = sc.note;
  document.getElementById('scen-input').textContent = sc.input;
  document.getElementById('selected-tools').textContent = (sc.tools_selected.length ? sc.tools_selected.join('、') : '（无）');
  updateResult();
  next();
}

function renderSteps() {
  const tl = document.getElementById('timeline');
  tl.querySelectorAll('.step, .divider').forEach(e => e.remove());
  STEPS.forEach((s, i) => {
    // 确认段开始前插一条分界线
    if (i === SPLIT && CUR.confirm) {
      const d = document.createElement('li');
      d.className = 'divider';
      d.textContent = '── 人工确认后，动作才真正执行 ──';
      tl.appendChild(d);
    }
    const li = document.createElement('li');
    li.className = 'step' + (i < IDX ? ' done' : (i === IDX ? ' current' : ''));
    const isGate = s.type === 'gate';
    let io = '';
    if (s.input) io += '<div class="io"><span class="k">in</span><code>' + esc(s.input) + '</code></div>';
    if (s.output) io += '<div class="io"><span class="k">out</span><code>' + esc(s.output) + '</code></div>';
    if (isGate) io += '<div class="gate-callout"><b>⚠ 高风险动作被拦下</b>：先出草稿，绝不直接执行，等人工确认。</div>';
    const meta = (s.dur ? s.dur + 'ms' : '') + (s.tok ? ' · ' + s.tok + 'tok' : '');
    li.innerHTML = '<span class="node"></span><div class="body">' +
      '<div class="head"><span class="pill ' + s.cls + '">' + s.label + '</span>' +
      '<span class="name">' + esc(s.name) + '</span>' +
      '<span class="meta">' + meta + '</span></div>' + io + '</div>';
    tl.appendChild(li);
  });
}

function updateResult() {
  const sc = CUR;
  const st = document.getElementById('final-status');
  st.className = 'st ' + sc.status;
  st.textContent = sc.status;
  document.getElementById('final-msg').textContent = sc.message;
  const tools = document.getElementById('final-tools');
  tools.textContent = '调用工具：' + (sc.tools_called.length ? sc.tools_called.join('、') : '（无）') +
                      (sc.pending_tool ? ' ｜ 挂起待确认：' + sc.pending_tool : '');
  if (sc.confirm) {
    const se = sc.confirm.side_effect || {scheduled:[], sent:[]};
    document.getElementById('confirm-msg').style.display = 'block';
    document.getElementById('confirm-msg').textContent = '确认后：' + sc.confirm.message +
      ' ｜ 副作用：已安排面试 ' + se.scheduled.length + ' 条、已发邮件 ' + se.sent.length + ' 条';
  } else {
    document.getElementById('confirm-msg').style.display = 'none';
  }
}

function next() {
  if (IDX < STEPS.length - 1) { IDX++; }
  renderSteps();
  const p = document.getElementById('progress');
  p.textContent = '步骤 ' + (IDX + 1) + ' / ' + STEPS.length + (CUR && CUR.confirm && IDX >= SPLIT ? '（人工确认段）' : '');
  document.getElementById('btn-prev').disabled = (IDX <= 0);
  document.getElementById('btn-next').disabled = (IDX >= STEPS.length - 1);
}
function prev() {
  if (IDX > 0) { IDX--; }
  renderSteps();
  const p = document.getElementById('progress');
  p.textContent = '步骤 ' + (IDX + 1) + ' / ' + STEPS.length + (CUR && CUR.confirm && IDX >= SPLIT ? '（人工确认段）' : '');
  document.getElementById('btn-prev').disabled = (IDX <= 0);
  document.getElementById('btn-next').disabled = (IDX >= STEPS.length - 1);
}
function reset() { IDX = -1; renderSteps(); document.getElementById('progress').textContent = '步骤 0 / ' + STEPS.length; }
function stopAuto() { if (timer) { clearInterval(timer); timer = null; } }
function play() {
  stopAuto();
  if (IDX >= STEPS.length - 1) { reset(); }
  timer = setInterval(() => { if (IDX >= STEPS.length - 1) { stopAuto(); } else { next(); } }, 900);
}

window.onload = function () {
  document.getElementById('backend').textContent = DATA.backend;
  document.getElementById('backend2').textContent = DATA.backend;
  document.getElementById('sidefx').textContent = '每个场景都是独立会话（互不污染）；高风险动作只在人工确认后才真正落地——场景 3 的确认段能看到 schedule_interview 从 draft 变成 executed。';
  const list = document.getElementById('scen-list');
  DATA.scenarios.forEach((sc, i) => list.appendChild(renderScen(sc, i)));
  document.getElementById('btn-prev').onclick = prev;
  document.getElementById('btn-next').onclick = next;
  document.getElementById('btn-reset').onclick = reset;
  document.getElementById('btn-play').onclick = play;
  load(0);
};
"""

_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>可交互 Demo · HRAgent Harness</title>
<style>__CSS__</style>
</head>
<body>
<div class="top"><div class="inner">
  <a href="index.html">← 返回作品集</a><span class="t">可交互 Demo · 受控 Agent 运行时</span>
  <span class="badge">backend: <span id="backend"></span></span>
</div></div>

<div class="hero"><div class="inner">
  <h1>点一个场景，看 Agent 一步步怎么决策</h1>
  <p>这不是截图，是 <code>python demo.py</code> 真实运行录下来的 Trace 回放——每个节点都是引擎里真实发生的一个 span。点「下一步」逐步走一遍：意图识别 → 注入工具 → 模型规划 → 门控 → 工具执行 → 最终响应。</p>
  <div class="trace-line"><span class="dot on"></span><span class="dot"></span><span class="dot on"></span><span class="dot"></span><span class="dot on"></span><span class="dot"></span><span class="dot on"></span></div>
</div></div>

<div class="layout">
  <aside class="side">
    <h2>场景（5 个）</h2>
    <div id="scen-list"></div>
  </aside>

  <main class="main">
    <div class="panel">
      <h3 id="scen-title"></h3>
      <p class="sub" id="scen-note"></p>
      <div class="bubble" id="scen-input"></div>
    </div>

    <div class="panel">
      <h3>意图识别 → 注入的工具</h3>
      <p class="sub">引擎按意图打分，只把相关工具定义塞进上下文（不相关的不进）。</p>
      <div class="bubble" style="font-family:var(--font-mono);font-size:13px" id="selected-tools"></div>
    </div>

    <div class="panel">
      <h3>决策链路（Trace 回放）</h3>
      <div class="controls">
        <button class="btn" id="btn-prev">‹ 上一步</button>
        <button class="btn primary" id="btn-next">下一步 ›</button>
        <button class="btn" id="btn-play">▶ 自动播放</button>
        <button class="btn" id="btn-reset">↺ 重置</button>
        <span class="progress" id="progress">步骤 0 / 0</span>
      </div>
      <ol class="timeline" id="timeline" style="margin-top:14px"></ol>
    </div>

    <div class="panel">
      <h3>最终结果</h3>
      <div class="result">
        <span class="st" id="final-status"></span>
        <div style="min-width:0">
          <p class="msg" id="final-msg"></p>
          <p class="msg" style="color:var(--text-mid);font-family:var(--font-mono);font-size:13px" id="final-tools"></p>
          <p class="msg" id="confirm-msg" style="color:var(--ok);margin-top:8px"></p>
        </div>
      </div>
    </div>

    <div class="panel sidefx">
      <strong>诚实声明：</strong>本页数据来自 <code>demo.py</code> 的一次真实运行，回放的是那次运行的完整 Trace；模型为 <code><span id="backend2"></span></code>（Stub 为规则模拟、不花钱，DeepSeek 为真模型）。<span id="sidefx"></span>
    </div>
  </main>
</div>

<script>__JS__</script>
</body>
</html>
"""


def render(data: dict) -> str:
    js = _JS.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    html = _HTML.replace("__CSS__", _CSS).replace("__JS__", js)
    return html


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="用 DeepSeek 真模型（需 DEEPSEEK_API_KEY）")
    args = ap.parse_args()

    mode = "deepseek" if args.live else "stub"
    try:
        llm = build_llm(mode)
    except Exception as e:  # noqa: BLE001 —— live 无 key 时回退 stub，保证总能出 demo
        print(f"[warn] {mode} 初始化失败（{e}），回退 Stub")
        llm = build_llm("stub")

    print(f"capturing 5 scenarios ... backend = {llm.name}")
    data = capture(llm)
    html = render(data)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    n_scen = len(data["scenarios"])
    n_spans = sum(len((s["trace"] or {}).get("spans", [])) for s in data["scenarios"])
    print(f"wrote {os.path.relpath(OUT, ROOT)}  (backend={data['backend']}, {n_scen} 场景, {n_spans} spans)")
    for s in data["scenarios"]:
        confirm = "  +确认段" if s.get("confirm") else ""
        print(f"  [场景{s['id']}] {s['status']:<22} 工具={s['tools_called']}{confirm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
