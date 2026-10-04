# -*- coding: utf-8 -*-
"""
上下文预算基准测试 —— 把 M2 相对 M1 的收益量化出来。

对比两种做法：
  M1（基线）：固定注入 top_k=5 个工具（相关性得分 0 的也被填进来）+ 历史对话全量拼接
  M2（现在）：分区预算 + 相关性阈值 + 超预算按序裁剪 + 历史压缩/丢弃

产出：终端表格 + reports/context_benchmark.md（作品集可直接引用）
用法：python scripts/context_benchmark.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.context import Budget, ContextManager, estimate_tokens  # noqa: E402
from harness.planner import SYSTEM_PROMPT  # noqa: E402
from tools.hr_tools import build_registry  # noqa: E402

SCENARIOS = [
    "帮我写一份 Java 后端工程师的 JD",
    "帮我筛一下简历",
    "给张三安排明天下午面试",
    "发邮件通知候选人面试时间",
    "帮我查一下年假制度",
]

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


def m1_measure(registry, user_input, history):
    """M1 做法：固定 top_k=5 注入 + 历史全量拼接。"""
    specs = registry.select_for_intent(user_input, top_k=5)
    tools_text = registry.to_prompt(specs)
    hist_text = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    return {
        "tools_tokens": estimate_tokens(tools_text),
        "history_tokens": estimate_tokens(hist_text),
        "tool_count": len(specs),
        "tool_names": [s.name for s in specs],
    }


def m2_measure(registry, user_input, history):
    """M2 做法：分区预算 + 裁剪 + 压缩。"""
    cm = ContextManager(registry, Budget())
    pkg = cm.build(user_input=user_input, history=history, system_prompt=SYSTEM_PROMPT)
    r = pkg.to_report()
    return {
        "tools_tokens": r["sections"]["tools"]["used"],
        "history_tokens": r["sections"]["history"]["used"],
        "tool_count": len(pkg.selected_tools),
        "tool_names": pkg.selected_tools,
        "compacted": pkg.history_compacted,
        "dropped": pkg.history_dropped,
        "total": pkg.total_tokens,
        "budget": pkg.budget.get("total", 0),
    }


def main() -> int:
    registry = build_registry()
    rows = []
    for text in SCENARIOS:
        for n in HISTORY_LEVELS:
            hist = make_history(n)
            a = m1_measure(registry, text, hist)
            b = m2_measure(registry, text, hist)
            m1_total = a["tools_tokens"] + a["history_tokens"]
            m2_total = b["tools_tokens"] + b["history_tokens"]
            cut = 1 - (m2_total / m1_total) if m1_total else 0.0
            rows.append(
                {
                    "scenario": text,
                    "hist": n,
                    "m1_tools": a["tools_tokens"],
                    "m2_tools": b["tools_tokens"],
                    "m1_hist": a["history_tokens"],
                    "m2_hist": b["history_tokens"],
                    "m1_total": m1_total,
                    "m2_total": m2_total,
                    "cut": cut,
                    "m1_names": a["tool_names"],
                    "m2_names": b["tool_names"],
                    "compacted": b["compacted"],
                    "dropped": b["dropped"],
                }
            )

    # ---- 终端输出 ----
    print("=" * 100)
    print("上下文预算对比：M1（固定 top_k 注入）vs M2（分区预算 + 裁剪 + 压缩）")
    print("=" * 100)
    print(f"{'场景':<26}{'历史':>4}{'M1工具':>8}{'M2工具':>8}{'M1历史':>8}{'M2历史':>8}{'M1合计':>8}{'M2合计':>8}{'降幅':>8}")
    print("-" * 100)
    for r in rows:
        s = r["scenario"]
        s = s if len(s) <= 24 else s[:24] + "…"
        print(
            f"{s:<26}{r['hist']:>4}{r['m1_tools']:>8}{r['m2_tools']:>8}"
            f"{r['m1_hist']:>8}{r['m2_hist']:>8}{r['m1_total']:>8}{r['m2_total']:>8}{r['cut']:>7.0%}"
        )
    print("-" * 100)
    avg_cut = sum(r["cut"] for r in rows) / len(rows)
    print(f"平均降幅: {avg_cut:.1%}")
    print()

    print("工具注入对比（历史 0 轮时）")
    for r in rows:
        if r["hist"] == 0:
            print(f"  场景: {r['scenario']}")
            print(f"    M1 注入 {len(r['m1_names'])} 个: {r['m1_names']}")
            print(f"    M2 注入 {len(r['m2_names'])} 个: {r['m2_names']}")
    print()

    print("长会话下的历史处理（历史 24 条时）")
    for r in rows:
        if r["hist"] == 24:
            print(
                f"  场景: {r['scenario'][:20]:<22} 压缩 {r['compacted']} 条 / 丢弃 {r['dropped']} 条"
                f"  → 历史 token {r['m1_hist']} → {r['m2_hist']}"
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
        "对比 M1（固定注入 top_k=5 个工具 + 历史全量拼接）与",
        "M2（分区预算 + 相关性阈值 + 超预算裁剪 + 历史压缩）的上下文占用。",
        "",
        "token 数为粗估值（CJK 按 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token），用于预算控制而非精确计费。",
        "",
        f"**平均降幅：{avg_cut:.1%}**",
        "",
        "| 场景 | 历史条数 | M1 工具 | M2 工具 | M1 历史 | M2 历史 | M1 合计 | M2 合计 | 降幅 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['hist']} | {r['m1_tools']} | {r['m2_tools']} | "
            f"{r['m1_hist']} | {r['m2_hist']} | {r['m1_total']} | {r['m2_total']} | {r['cut']:.0%} |"
        )
    lines += ["", "## 工具注入对比（历史 0 条）", ""]
    for r in rows:
        if r["hist"] == 0:
            lines.append(f"- **{r['scenario']}**")
            lines.append(f"  - M1 注入 {len(r['m1_names'])} 个：{', '.join(r['m1_names'])}")
            lines.append(f"  - M2 注入 {len(r['m2_names'])} 个：{', '.join(r['m2_names'])}")
    lines += ["", "## 长会话表现（历史 24 条）", ""]
    for r in rows:
        if r["hist"] == 24:
            lines.append(
                f"- **{r['scenario']}**：压缩 {r['compacted']} 条、丢弃 {r['dropped']} 条，"
                f"历史 token {r['m1_hist']} → {r['m2_hist']}"
            )
    lines += [
        "",
        "## 说明",
        "",
        "- 高风险工具（`schedule_interview` / `send_email`）在 M2 中受保护：",
        "  即使超出工具分区预算也保留，避免模型因不知道它们存在而臆造能力。",
        "  这是刻意的取舍：**安全优先于预算**。",
        "- 历史分区与本轮执行结果共享额度，执行结果优先（它是当前决策的直接依据）。",
        "- 用例与历史均由本项目自造，用于验证机制是否生效，不代表真实线上分布。",
        "",
    ]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
