# -*- coding: utf-8 -*-
"""
工具选择探针 —— 自证 M4 的修复是「通用原则」而不是「针对已知用例硬编码」。

## 它解决什么问题

M4 改了工具选择策略之后，A05 / A06 两条红用例变绿了。但这会带来一个合理的质疑：

> 你是把规则调得刚好能过那 24 条用例，还是真的修好了根因？

如果只能过老题、换个说法就崩，那叫**过拟合**，不叫修复。

所以这批探针句子的第一条设计要求是：
**它们的措辞必须与 evals/cases.py 里的句子不同。**（对照清单见下方 PROBES 注释）

## 怎么用

    python scripts/tool_select_probe.py              # 打印表格 + 写报告
    python scripts/tool_select_probe.py --verbose    # 打印每个工具的得分明细

产出：reports/tool_select_probe.md
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.context import ContextManager  # noqa: E402
from tools.hr_tools import build_registry  # noqa: E402

REPORT_PATH = os.path.join("reports", "tool_select_probe.md")


@dataclass
class Probe:
    """一条探针：一句话 + 期望注入 / 期望不注入的工具。"""

    sentence: str
    include: Tuple[str, ...] = ()
    exclude: Tuple[str, ...] = ()
    note: str = ""


# ---------------------------------------------------------------------------
# 探针集（措辞刻意避开 evals/cases.py 里的句子）
#
# cases.py 里已有的说法（这里一律不用）：
#   "帮我写一份 Java 后端工程师的 JD" / "帮我查一下年假制度" / "帮我筛一下这份简历"
#   "给张三安排明天下午面试" / "发邮件通知候选人面试时间" / "帮我准备一下 Java 岗的面试问题"
#   "帮我筛一下简历" / "给李四安排后天上午面试" / "帮我给这份简历打个分"
# ---------------------------------------------------------------------------
PROBES: List[Probe] = [
    # --- 高风险：排面试 ---
    Probe("帮我把这次面试改期", include=("schedule_interview",)),
    Probe("给王五约个面试，越快越好", include=("schedule_interview",)),
    # --- 高风险：发邮件 ---
    Probe("把这封入职通知发出去", include=("send_email",)),
    Probe("给面试官发一封邮件提醒", include=("send_email",)),
    Probe("帮我起草邮件的内容", include=("draft_email",), exclude=("send_email",)),
    # --- BUG-005 修复点：句子里有「候选人」但不是要查状态 ---
    Probe("发 email 通知候选人下周来面试", include=("send_email",), exclude=("get_candidate",)),
    Probe("给我出一版 Python 岗位的 JD", include=("generate_jd",),
          exclude=("get_candidate", "schedule_interview", "send_email")),
    # --- get_candidate 仍要能被选中（不能为了修误注入把正例也打死）---
    Probe("候选人王五现在进展到哪一步了", include=("get_candidate",)),
    Probe("帮我看看李四的候选人资料", include=("get_candidate",)),
    # --- ISSUE-006 修复点：不该被高风险工具污染的低风险任务 ---
    Probe("招聘启事帮我出一版，岗位是数据分析师", include=("generate_jd",),
          exclude=("schedule_interview", "send_email")),
    Probe("我们公司有没有远程办公的规定", include=("search_knowledge",),
          exclude=("schedule_interview", "send_email")),
    # --- 其余工具 ---
    Probe("公司差旅报销多久能到账", include=("search_knowledge",)),
    Probe("年假规定帮我查一下", include=("search_knowledge",)),
    Probe("给这个岗位定一套评分标准", include=("generate_rubric",)),
    Probe("这岗位的评分表给我一份", include=("generate_rubric",)),
    Probe("帮我评估一下这份简历", include=("screen_resume",)),
    Probe("这份简历请打个分", include=("screen_resume",)),
    Probe("面试大纲给我列一份，Java 岗的", include=("generate_interview_plan",),
          exclude=("schedule_interview",)),
]


def main() -> int:
    verbose = "--verbose" in sys.argv
    cm = ContextManager(build_registry())

    rows: List[Tuple[str, List[str], bool, str]] = []
    passed = 0

    for p in PROBES:
        picked, _dropped, _used, notes = cm.select_tools(p.sentence)
        names = [s.name for s in picked]
        miss = [t for t in p.include if t not in names]
        bad = [t for t in p.exclude if t in names]
        ok = not miss and not bad
        passed += 1 if ok else 0
        reason = ""
        if miss:
            reason += "漏选 " + ", ".join(miss)
        if bad:
            reason += ("；" if reason else "") + "误选 " + ", ".join(bad)
        rows.append((p.sentence, names, ok, reason or ("ok" if ok else "")))

    total = len(rows)
    acc = passed / total if total else 0.0

    print("=" * 76)
    print("工具选择探针（句子不在 evals/cases.py 里，用于自证不是过拟合）")
    print("=" * 76)
    for sentence, names, ok, reason in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {sentence}")
        print(f"       注入: {names or '（空）'}")
        if reason and not ok:
            print(f"       原因: {reason}")
    print("-" * 76)
    print(f"通过 {passed}/{total}  准确率 {acc:.0%}")
    print("=" * 76)

    if verbose:
        print()
        print("得分明细")
        for sentence, _names, _ok, _r in rows:
            print(f"\n{sentence}")
            for score, name, _spec, hits in cm.score_tools(sentence):
                if score:
                    print(f"   {name:<26} {score:>2} 分  {' '.join(hits)}")

    # ---- 写报告 ----
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    lines = [
        "# 工具选择探针报告",
        "",
        f"- 生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 探针数 **{total}** ｜ 通过 **{passed}** ｜ 准确率 **{acc:.0%}**",
        "",
        "## 这份报告想证明什么",
        "",
        "M4 修改了工具选择策略之后，评测集里 A05 / A06 由红转绿。",
        "这本身不能说明修好了根因——**只要把规则调得刚好能过已知用例也能变绿**。",
        "",
        "所以这里用一批**措辞与 `evals/cases.py` 完全不同**的句子重测一遍。",
        "只有当新句子也表现正常时，才能说修的是通用原则而不是记题。",
        "",
        "## 结果",
        "",
        "| 句子 | 实际注入 | 结果 | 问题 |",
        "|---|---|:---:|---|",
    ]
    for sentence, names, ok, reason in rows:
        lines.append(
            f"| {sentence} | {', '.join(names) or '（空）'} |"
            f" {'✅' if ok else '❌'} | {reason} |"
        )
    lines += [
        "",
        "## 口径与限制",
        "",
        "1. 探针句子同样由本项目自造，**不是真实用户语料**。",
        "   它的作用是**区分「过拟合」与「真修复」**，不能证明真实场景准确率。",
        "2. 加权关键词本质仍是字面匹配，**没有语义理解**。",
        "   典型的失败模式：否定句（「别发邮件」）会被误判为肯定意图。",
        "   这是该方案的已知边界，而非实现缺陷——真正的解法是换成 embedding / LLM 路由，",
        "   那是 M5 与 Dify 对照组要比较的内容。",
        "",
    ]
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写入 {REPORT_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
