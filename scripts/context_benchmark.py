# -*- coding: utf-8 -*-
"""
上下文预算基准测试 —— 验证「ContextManager 是否真的省下了上下文」。

对比两个做法：
  baseline（旧逻辑）：关键词计数打分 + 固定取 top_k=5（得分 0 也塞进来）
                    + 高风险工具强制注入 + 历史对话全量拼接
  now（当前逻辑）   ：强弱加权打分 + 全局阈值 + 不做强制注入
                    + 分区预算裁剪 + 历史压缩/丢弃

## 一条方法论约束（这个脚本曾经踩过坑）

**baseline 必须自己实现，不许调用 registry.select_for_intent。**

早期版本偷懒调了它，结果 M4 改造工具选择之后，baseline 那一栏也跟着变瘦了 ——
所谓"对比"变成了跟一个不断变化��影子比，数字再漂亮也没有意义。

所以这里把旧规则的关键词表原样抄进来（`LEGACY_KEYWORDS`），
连同"计数打分 / 固定 top_k / 强制注入"三条旧规则一起复算。

产出：终端表格 + reports/context_benchmark.md
用法：python scripts/context_benchmark.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.context import Budget, ContextManager, estimate_tokens  # noqa: E402
from harness.planner import SYSTEM_PROMPT  # noqa: E402
from harness.tool_registry import RISK_HIGH, ToolSpec  # noqa: E402
from tools.hr_tools import build_registry  # noqa: E402

SCENARIOS = [
    "帮我写一份 Java 后端工程师的 JD",
    "帮我筛一下简历",
    "给张三安排明天下午面试",
    "发邮件通知候选人面试时间",
    "帮我查一下年假制度",
]

# M1/M2 时期每个工具登记的关键词。原样保存，用于复算旧基线。
LEGACY_KEYWORDS = {
    "generate_jd": ["JD", "jd", "岗位描述", "招聘启事", "写一份"],
    "generate_rubric": ["评分标准", "评分", "rubric", "维度"],
    "screen_resume": ["筛", "简历", "评估", "打分", "筛选"],
    "generate_interview_plan": ["面试方案", "面试问题", "面评", "方案", "问题"],
    "search_knowledge": ["制度", "规定", "知识", "假期", "报销"],
    "get_candidate": ["候选人", "状态", "查询"],
    "draft_email": ["邮件草稿", "起草"],
    "schedule_interview": ["安排面试", "约面试", "面试时间", "预约", "面试"],
    "send_email": ["发邮件", "发通知", "邮件通知", "通知候选人"],
}
# 旧逻辑下被无条件注入的高风险工具
LEGACY_FORCED_HIGH_RISK = ["schedule_interview", "send_email"]

# 模拟一段逐步变长的对话历史
BASE_HISTORY = [
    {"role": "user", "content": "你好，我想招一个 Java 后端工程师，主要负责交易链路的服务端开发。"},
    {"role": "assistant", "content": "好的，我先为你生成一份 JD 初稿，包含岗位职责、任职要求与加分项三部分。"},
    {"role": "user", "content": "薪资范围先按 25 到 40K，工作地点在上海张江，要求本科及以上。"},
    {"role": "assistant", "content": "已更新 JD：薪资 25-40K，地点上海张江，学历要求本科及以上，请确认是否需要调整。"},
    {"role": "user", "content": "再加一条：有高并发系统调优经验的优先，另外团队规模大概 12 人。"},
    {"role": "assistant", "content": "已加入加分项「有高并发系统调优经验」，并在团队介绍中写明团队规模 12 人。"},
]
HISTORY_LEVELS = [0, 6, 12, 24]


def make_history(n: int):
    if n <= 0:
        return []
    out = []
    i = 0
    while len(out) < n:
        out.append(dict(BASE_HISTORY[i % len(BASE_HISTORY)]))
        i += 1
    return out


# ---------------------------------------------------------------------------
# baseline：旧逻辑，全部在本地复算，不依赖当前 registry 的行为
# ---------------------------------------------------------------------------

def legacy_select(registry, user_input: str, top_k: int = 5):
    """旧逻辑：关键词计数 -> 固定取前 top_k -> 再强制塞入高风险工具。"""

    def score(spec: ToolSpec) -> int:
        kws = LEGACY_KEYWORDS.get(spec.name, [])
        return sum(1 for kw in kws if kw and kw in user_input)

    all_specs = registry.all()
    ranked = sorted(all_specs, key=lambda s: (-score(s), s.name))
    picked = ranked[:top_k]
    for name in LEGACY_FORCED_HIGH_RISK:
        spec = registry.get(name)
        if spec is not None and spec not in picked:
            picked.append(spec)
    return picked


def baseline_measure(registry, user_input, history):
    specs = legacy_select(registry, user_input)
    tools_text = registry.to_prompt(specs)
    hist_text = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    return {
        "tools_tokens": estimate_tokens(tools_text),
        "history_tokens": estimate_tokens(hist_text),
        "tool_names": [s.name for s in specs],
    }


# ---------------------------------------------------------------------------
# now：当前逻辑
# ---------------------------------------------------------------------------

def now_measure(registry, user_input, history):
    cm = ContextManager(registry, Budget())
    pkg = cm.build(user_input=user_input, history=history, system_prompt=SYSTEM_PROMPT)
    r = pkg.to_report()
    return {
        "tools_tokens": r["sections"]["tools"]["used"],
        "history_tokens": r["sections"]["history"]["used"],
        "tool_names": list(pkg.selected_tools),
        "compacted": pkg.history_compacted,
        "dropped": pkg.history_dropped,
    }


def main() -> int:
    registry = build_registry()
    rows = []
    for text in SCENARIOS:
        for n in HISTORY_LEVELS:
            hist = make_history(n)
            a = baseline_measure(registry, text, hist)
            b = now_measure(registry, text, hist)
            base_total = a["tools_tokens"] + a["history_tokens"]
            now_total = b["tools_tokens"] + b["history_tokens"]
            cut = 1 - (now_total / base_total) if base_total else 0.0
            rows.append({
                "scenario": text,
                "hist": n,
                "base_tools": a["tools_tokens"],
                "now_tools": b["tools_tokens"],
                "base_hist": a["history_tokens"],
                "now_hist": b["history_tokens"],
                "base_total": base_total,
                "now_total": now_total,
                "cut": cut,
                "base_names": a["tool_names"],
                "now_names": b["tool_names"],
                "compacted": b["compacted"],
                "dropped": b["dropped"],
            })

    print("=" * 108)
    print("上下文占用对比：旧逻辑（计数+固定 top_k+强制注入） vs 当前（加权阈值+预算裁剪+历史压缩）")
    print("=" * 108)
    print(
        f"{'场景':<26}{'历史':>4}{'旧工具':>8}{'新工具':>8}"
        f"{'旧历史':>8}{'新历史':>8}{'旧合计':>8}{'新合计':>8}{'降幅':>8}"
    )
    print("-" * 108)
    for r in rows:
        s = r["scenario"] if len(r["scenario"]) <= 24 else r["scenario"][:24] + "…"
        print(
            f"{s:<26}{r['hist']:>4}{r['base_tools']:>8}{r['now_tools']:>8}"
            f"{r['base_hist']:>8}{r['now_hist']:>8}{r['base_total']:>8}"
            f"{r['now_total']:>8}{r['cut']:>7.0%}"
        )
    print("-" * 108)
    avg_cut = sum(r["cut"] for r in rows) / len(rows)
    avg_tools_base = sum(len(r["base_names"]) for r in rows if r["hist"] == 0) / max(
        1, len([r for r in rows if r["hist"] == 0])
    )
    avg_tools_now = sum(len(r["now_names"]) for r in rows if r["hist"] == 0) / max(
        1, len([r for r in rows if r["hist"] == 0])
    )
    print(f"平均降幅: {avg_cut:.1%}")
    print(f"注入工具数（历史 0 条时平均）: {avg_tools_base:.1f} → {avg_tools_now:.1f}")
    print()

    print("工具注入对比（历史 0 条）")
    for r in rows:
        if r["hist"] == 0:
            print(f"  场景: {r['scenario']}")
            print(f"    旧: {len(r['base_names'])} 个 {r['base_names']}")
            print(f"    新: {len(r['now_names'])} 个 {r['now_names']}")
    print()

    print("长会话下的历史处理（历史 24 条）")
    for r in rows:
        if r["hist"] == 24:
            print(
                f"  {r['scenario'][:20]:<22} 压缩 {r['compacted']} 条 / 丢弃 {r['dropped']} 条"
                f"  → 历史 token {r['base_hist']} → {r['now_hist']}"
            )
    print()

    # ---- 写报告 ----
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports"
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "context_benchmark.md")

    lines = [
        "# 上下文预算基准测试",
        "",
        "对比两代做法的上下文占用：",
        "",
        "- **旧逻辑**：关键词计数打分 + 固定取 `top_k=5`（得分 0 也塞进来）"
        " + 高风险工具强制注入 + 历史对话全量拼接",
        "- **当前**：强弱信号加权打分 + 全局阈值 + 不做强制注入"
        " + 分区预算裁剪 + 历史压缩/丢弃",
        "",
        f"**平均降幅：{avg_cut:.1%}** ｜ 注入工具数平均 {avg_tools_base:.1f} → {avg_tools_now:.1f}",
        "",
        "| 场景 | 历史条数 | 旧工具 | 新工具 | 旧历史 | 新历史 | 旧合计 | 新合计 | 降幅 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['hist']} | {r['base_tools']} | {r['now_tools']} | "
            f"{r['base_hist']} | {r['now_hist']} | {r['base_total']} | {r['now_total']} |"
            f" {r['cut']:.0%} |"
        )
    lines += ["", "## 工具注入对比（历史 0 条）", ""]
    for r in rows:
        if r["hist"] == 0:
            lines.append(f"- **{r['scenario']}**")
            lines.append(f"  - 旧：{len(r['base_names'])} 个 —— {', '.join(r['base_names'])}")
            lines.append(f"  - 新：{len(r['now_names'])} 个 —— {', '.join(r['now_names']) or '（空）'}")
    lines += ["", "## 长会话表现（历史 24 条）", ""]
    for r in rows:
        if r["hist"] == 24:
            lines.append(
                f"- **{r['scenario']}**：压缩 {r['compacted']} 条、丢弃 {r['dropped']} 条，"
                f"历史 token {r['base_hist']} → {r['now_hist']}"
            )
    lines += [
        "",
        "## 口径与限制（必读）",
        "",
        "1. **旧基线是在本脚本里独立复算的**，不调用当前 `registry.select_for_intent`。",
        "   这一点很关键：如果让基线跟着当前代码走，那对比就变成了跟自己比。",
        "   脚本里保留了旧关键词表的副本（`LEGACY_KEYWORDS`）用于复算。",
        "2. token 为粗估值（CJK 按 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token），",
        "   **用于预算控制与趋势观察，不做精确计费**。",
        "3. 场景、历史、工具定义全部由本项目自造，**不代表真实线上分布**。",
        "",
    ]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
