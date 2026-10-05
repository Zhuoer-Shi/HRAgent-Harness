# -*- coding: utf-8 -*-
"""
Dify 对照评测入口 —— M5 双轨评测的「对照轨」CLI。

用法：
    python scripts/run_dify_eval.py                # 跑全部 24 条 × 3 轮（需要 DIFY_API_KEY）
    python scripts/run_dify_eval.py --category C   # 只跑 C 类（先小批量联调，省 Dify 额度）
    python scripts/run_dify_eval.py --repeat 1     # 每条只跑 1 轮

环境变量：
    DIFY_API_KEY    必填，Dify 应用 API Key（云端默认 https://api.dify.ai）
    DIFY_BASE_URL   可选，默认 https://api.dify.ai
    DEEPSEEK_API_KEY 必填，D 类内容质量用同一套 LLM 裁判（DeepSeek）打分

产出：
    reports/dify_eval_latest.md   人读报告
    reports/dify_eval_latest.json 机读结果

注意：Dify 阻塞式对话 API 默认不暴露「调了哪个工具 / 是否走人工确认 / 有无副作用」，
所以 C 类（门控、契约）这类依赖引擎内部控制流的断言在 Dify 侧天然不可比。
对比报告（docs/03-ADR-什么时候不该用Dify.md）会如实说明这一局限，不硬凑可比数字。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.cases import CATEGORY_NAMES  # noqa: E402
from evals.dify_adapter import run_dify_eval  # noqa: E402

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def _summarize(results) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.judgement and r.judgement.passed)
    undetermined = sum(1 for r in results if r.undetermined)
    incomparable = sum(1 for r in results if r.incomparable)
    by_cat: dict[str, dict] = {}
    for r in results:
        cat = r.case_id[0]
        d = by_cat.setdefault(cat, {"exec": 0, "pass": 0, "und": 0, "inc": 0})
        d["exec"] += 1
        if r.judgement and r.judgement.passed:
            d["pass"] += 1
        if r.undetermined:
            d["und"] += 1
        if r.incomparable:
            d["inc"] += 1
    return {
        "total": total, "passed": passed,
        "undetermined": undetermined, "incomparable": incomparable,
        "by_cat": by_cat,
    }


def _render_md(summary: dict, results=None, repeat: int = 3) -> str:
    results = results or []
    lines = ["# Dify 对照评测报告", ""]
    lines.append(f"- 生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')} ｜ 每条重复 {repeat} 轮")
    lines.append(f"- 执行 {summary['total']} 条 ｜ 通过 {summary['passed']} ｜ "
                 f"未判定 {summary['undetermined']} ｜ 不可比(设计) {summary['incomparable']}")
    lines.append("")
    lines.append("## 分类结果")
    lines.append("")
    lines.append("| 类别 | 执行 | 通过 | 未判定 | 不可比 |")
    lines.append("|---|---:|---:|---:|---:|")
    for cat, d in summary["by_cat"].items():
        lines.append(f"| {cat} · {CATEGORY_NAMES.get(cat, cat)} | {d['exec']} | {d['pass']} | {d['und']} | {d['inc']} |")
    lines.append("")
    lines.append("> 通过率口径：只算「可比且裁判可用」的条数。"
                 "**未判定**=Dify API 调用失败或 LLM 裁判不可用（需 DEEPSEEK_API_KEY）；"
                 "**不可比**=该用例依赖 Dify 不建模的信号（门控/状态机/副作用/工具清单），"
                 "见 ADR-003 论据一/二，不计入通过率。")
    lines.append("")
    # 异常明细：把每条调用级失败原样列出，避免把「调用失败」误读成「设计上不可比」
    errs = [(r.case_id, r.error) for r in results if r.error]
    if errs:
        lines.append("## 异常明细（调用级失败，非设计不可比）")
        lines.append("")
        for cid, err in errs:
            lines.append(f"- {cid}: `{err}`")
        lines.append("")
        lines.append("> ⚠️ 以上为 Dify API 调用失败（多为 401/403 认证或网络），不是 Dify 业务行为。"
                     "修复后再跑，别拿本报告当对比结论。")
        lines.append("")
    # 不可比明细：逐条说明为什么不可比，证明我们没有偷偷把不可比算成失败
    incs = [(r.case_id, r.incompat_reason) for r in results if r.incompat_reason]
    if incs:
        lines.append("## 不可比明细（设计上不可比，不计入通过率）")
        lines.append("")
        for cid, reason in incs:
            lines.append(f"- {cid}: {reason}")
        lines.append("")
    lines.append("## 口径与限制（必读）")
    lines.append("")
    lines.append("1. Dify streaming Agent API **会**在 `agent_thought` 暴露 tool/observation（工具调用可见），")
    lines.append("   但**没有** durable span 树、gate_count、awaiting_* 状态机、副作用上报。")
    lines.append("   依赖后者的 C 类（门控/契约）与 A 类（工具清单）断言在 Dify 侧天然不可比。")
    lines.append("2. D 类（内容质量）走同一套 LLM 裁判，是**真正可比**的维度，但需要 DEEPSEEK_API_KEY。")
    lines.append("3. 本报告是与 harness 侧做**横向对比**的输入，不是单独给 Dify 打分。最终对比见 ADR-003。")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--category", type=str, default=None, help="只跑某类：A/B/C/D")
    args = ap.parse_args()

    if not os.environ.get("DIFY_API_KEY"):
        print("❌ 未设置 DIFY_API_KEY，无法跑 Dify 对照。先设环境变量：")
        print("   PowerShell:  $env:DIFY_API_KEY=\"app-xxx\"")
        print("   cmd:         set DIFY_API_KEY=app-xxx")
        sys.exit(2)

    print("▶ 跑 Dify 对照评测（可能花费 Dify 云端额度）...")
    results = run_dify_eval(repeat=args.repeat, category=args.category)
    summary = _summarize(results)

    md = _render_md(summary, results)
    md_path = os.path.join(REPORTS_DIR, "dify_eval_latest.md")
    json_path = os.path.join(REPORTS_DIR, "dify_eval_latest.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([{
            "case_id": r.case_id,
            "passed": bool(r.judgement and r.judgement.passed),
            "undetermined": r.undetermined,
            "incomparable": r.incomparable,
            "incompat_reason": r.incompat_reason,
            "error": r.error,
        } for r in results], f, ensure_ascii=False, indent=2)

    print(f"执行 {summary['total']} ｜ 通过 {summary['passed']} ｜ "
          f"未判定 {summary['undetermined']} ｜ 不可比 {summary['incomparable']}")
    for cat, d in summary["by_cat"].items():
        print(f"  {cat} · {CATEGORY_NAMES.get(cat, cat)}: 执行 {d['exec']} 通过 {d['pass']} "
              f"未判定 {d['und']} 不可比 {d['inc']}")
    errs = [(r.case_id, r.error) for r in results if r.error]
    if errs:
        print("⚠️ 调用级异常（非设计不可比）：")
        for cid, err in errs[:5]:
            print(f"  {cid}: {err}")
        if len(errs) > 5:
            print(f"  …共 {len(errs)} 条，详见 {json_path}")
    print(f"报告: {md_path}")
    print(f"结果: {json_path}")


if __name__ == "__main__":
    main()
