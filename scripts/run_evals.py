# -*- coding: utf-8 -*-
"""
评测入口 —— 跑一遍全套用例，出终端摘要 + Markdown 报告 + JSON 结果。

用法：
    python scripts/run_evals.py                  # Stub 后端（不花钱，跑 framework 层用例）
    python scripts/run_evals.py --live           # DeepSeek 真模型（跑全部 24 条 + LLM 裁判）
    python scripts/run_evals.py --repeat 3       # 每条用例重复 3 轮，算稳定性
    python scripts/run_evals.py --save-baseline  # 把本次结果存为基线
    python scripts/run_evals.py --baseline       # 与基线对比，看有没有回归

产出：
    reports/eval_latest.md      人读的报告（作品集直接用）
    reports/eval_latest.json    机读结果（baseline 回归用）
    reports/traces/*.json       每次运行的完整 Trace 链路
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.cases import CATEGORY_NAMES, CASES  # noqa: E402
from evals.runner import (  # noqa: E402
    BASELINE_PATH,
    REPORTS_DIR,
    EvalReport,
    EvalRunner,
    diff_baseline,
    save_baseline,
)

# 本项目自设的目标值（PRD 5.7）——**不是行业标准**，只用于自查趋势
TARGETS = [
    ("契约通过率", "contract_rate", 1.0, "硬指标"),
    ("门控触发率", "gate_rate", 1.0, "硬指标"),
    ("Trace 完整率", "trace_complete_rate", 1.0, "硬指标"),
    ("稳定性", "stability", 0.90, "软指标"),
    ("可控完成率", "controlled_completion_rate", 0.80, "北极星"),
]


def _pct(x: float) -> str:
    return f"{x:.0%}"


def render_markdown(report: EvalReport, diff: dict | None) -> str:
    m = report.to_dict()["metrics"]
    lines: list[str] = []

    lines.append("# HRAH 评测报告")
    lines.append("")
    lines.append(
        f"- 后端：**{report.backend}** ｜ 每条用例重复 **{report.repeat}** 轮"
        f" ｜ 生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    lines.append(
        f"- 执行 **{len(report.executed)}** 条，跳过 **{len(report.skipped)}** 条"
        f"（用例总数 {report.total_cases}）"
    )
    _u = report.usage or {}
    if _u.get("llm_calls"):
        lines.append(
            f"- LLM 调用 **{_u['llm_calls']}** 次 ｜ token **{_u['total_tokens']}**"
            f"（输入 {_u['prompt_tokens']} / 输出 {_u['completion_tokens']}）"
            f" ｜ 耗时 {_u['latency_ms']:.0f} ms"
            + (f" ｜ 调用异常 **{_u['llm_errors']}** 次" if _u.get("llm_errors") else "")
        )
    lines.append("")

    lines.append("## 一、核心指标")
    lines.append("")
    lines.append("| 指标 | 实测 | 目标 | 是否达标 | 性质 |")
    lines.append("|---|---:|---:|:---:|---|")
    for label, key, target, kind in TARGETS:
        got = m[key]
        ok = got >= target - 1e-9
        lines.append(f"| {label} | {_pct(got)} | {_pct(target)} | {'✅' if ok else '❌'} | {kind} |")
    lines.append(f"| 用例通过率 | {_pct(report.pass_rate)} | — | — | 参考 |")
    lines.append("")
    lines.append("> 目标值为**本项目自设**，用于观察优化前后的趋势，**不是行业标准、也不是上线验收标准**。")
    lines.append("")

    lines.append("## 二、分类结果")
    lines.append("")
    lines.append("| 类别 | 执行 | 通过 | 跳过 |")
    lines.append("|---|---:|---:|---:|")
    for key, name in CATEGORY_NAMES.items():
        v = report.by_category()[key]
        lines.append(f"| {key} · {name} | {v['executed']} | {v['passed']} | {v['skipped']} |")
    lines.append("")

    lines.append("## 三、用例明细")
    lines.append("")
    lines.append("| 编号 | 类别 | 用例 | 结果 | 失败断言 | 已知问题 |")
    lines.append("|---|---|---|:---:|---|---|")
    for r in report.results:
        if r.skipped:
            lines.append(
                f"| {r.case.id} | {r.case.category} | {r.case.title} | ⬜ 跳过 | {r.skip_reason} | |"
            )
            continue
        mark = "✅" if r.passed else "❌"
        failed = (r.runs[0].failed or [])[:1]
        detail = failed[0] if failed else ""
        detail = detail.replace("|", "/")[:70]
        lines.append(
            f"| {r.case.id} | {r.case.category} | {r.case.title} | {mark} | {detail} |"
            f" {r.case.known_issue or ''} |"
        )
    lines.append("")

    if diff:
        lines.append("## 四、与基线的对比")
        lines.append("")
        lines.append(f"- 通过率：{_pct(diff['old_pass_rate'])} → {_pct(diff['new_pass_rate'])}")
        lines.append(f"- 新增失败（回归）：{diff['regressions'] or '无'}")
        lines.append(f"- 新修好：{diff['fixes'] or '无'}")
        if diff["newly_executed"]:
            lines.append(f"- 本次新纳入执行：{diff['newly_executed']}")
        lines.append("")
        n = "五"
    else:
        n = "四"

    lines.append(f"## {n}、口径与限制（必读）")
    lines.append("")
    lines.append("1. 用例由本项目自造，**不是真实线上流量**，也不代表真实用户分布。")
    lines.append("2. 需要真模型能力的用例（B02-B06 / D01-D06）在 Stub 后端下**跳过**，" "既不计入通过也不计入失败。")
    lines.append("3. token 与耗时均为粗估值，用于观察趋势，不做精确计费。")
    lines.append("4. 所有目标值为自设，用于回归对比，不作为任何外部验收依据。")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="HRAH 评测入口")
    ap.add_argument("--live", action="store_true", help="使用 DeepSeek 真实模型（需要 DEEPSEEK_API_KEY）")
    ap.add_argument("--repeat", type=int, default=3, help="每条用例重复轮数，默认 3")
    ap.add_argument("--category", default="", help="只跑指定类别，如 A,C")
    ap.add_argument("--save-baseline", action="store_true", help="把本次结果存为基线")
    ap.add_argument("--baseline", action="store_true", help="与已有基线对比")
    ap.add_argument("--no-traces", action="store_true", help="不落盘 Trace")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    cases = CASES
    if args.category:
        wanted = {c.strip().upper() for c in args.category.split(",") if c.strip()}
        cases = [c for c in CASES if c.category in wanted]

    runner = EvalRunner(
        backend="deepseek" if args.live else "stub",
        repeat=args.repeat,
        save_traces=not args.no_traces,
        root=root,
    )

    print("=" * 78)
    print(f"HRAH 评测  |  后端 = {runner.backend}  |  重复 {args.repeat} 轮  |  用例 {len(cases)} 条")
    print("=" * 78)

    report = runner.run_all(cases)

    # ---- 终端摘要 ----
    print()
    print("【核心指标】")
    m = report.to_dict()["metrics"]
    for label, key, target, kind in TARGETS:
        got = m[key]
        flag = "OK " if got >= target - 1e-9 else "!! "
        print(f"  {flag}{label:<12} {_pct(got):>5}   目标 {_pct(target):>5}   {kind}")
    print(f"     用例通过率     {_pct(report.pass_rate):>5}")
    _u = report.usage or {}
    if _u.get("llm_calls"):
        print(
            f"     LLM 调用       {_u['llm_calls']:>5} 次 ｜"
            f" token {_u['total_tokens']}（入 {_u['prompt_tokens']} / 出 {_u['completion_tokens']}）"
            f" ｜ 耗时 {_u['latency_ms']:.0f} ms"
            + (f" ｜ 异常 {_u['llm_errors']} 次" if _u.get("llm_errors") else "")
        )

    print()
    print("【分类结果】")
    for key, name in CATEGORY_NAMES.items():
        v = report.by_category()[key]
        if v["executed"] + v["skipped"] == 0:
            continue
        print(f"  {key} · {name:<12} 执行 {v['executed']}  通过 {v['passed']}  跳过 {v['skipped']}")

    print()
    print("【失败用例】")
    failures = [r for r in report.executed if not r.passed]
    if not failures:
        print("  无")
    for r in failures:
        print(f"  ❌ {r.case.id} {r.case.title}" + (f"  [{r.case.known_issue}]" if r.case.known_issue else ""))
        for f in (r.runs[0].failed or []):
            print(f"       - {f}")

    # ---- 落盘 ----
    out_dir = os.path.join(root, REPORTS_DIR)
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "eval_latest.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)

    diff = None
    if args.baseline:
        diff = diff_baseline(report, root)
        if diff is None:
            print(f"\n  未找到基线文件 {BASELINE_PATH}，跳过对比")
        else:
            print()
            print("【与基线对比】")
            print(f"  通过率 {_pct(diff['old_pass_rate'])} → {_pct(diff['new_pass_rate'])}")
            print(f"  新增失败: {diff['regressions'] or '无'}")
            print(f"  新修好  : {diff['fixes'] or '无'}")

    if args.save_baseline:
        p = save_baseline(report, root)
        print(f"\n  基线已保存: {p}")

    md_path = os.path.join(out_dir, "eval_latest.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(report, diff))

    print()
    print(f"  报告: {md_path}")
    print(f"  结果: {json_path}")
    if not args.no_traces:
        print(f"  Trace: {os.path.join(root, 'reports', 'traces')}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
