# -*- coding: utf-8 -*-
"""
HRAH 最小可运行演示。

用法：
    python demo.py                 # 用 StubLLM，不联网不花钱
    python demo.py --live          # 用 DeepSeek（需设置环境变量 DEEPSEEK_API_KEY）

它会跑 4 个场景，打印执行结果 + Trace 时间线，验证三件事：
1. 低风险任务可以自动跑完（生成 JD）
2. 缺信息时会追问，而不是瞎猜（筛简历但没有 JD）
3. 高风险动作一定被门控拦下，出草稿等人确认（安排面试 / 发邮件）
"""

from __future__ import annotations

import argparse
import os
import sys

# Windows 终端编码兜底
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness.llm import build_llm  # noqa: E402
from harness.runner import STATUS_AWAITING_CONFIRMATION, AgentRunner  # noqa: E402
from tools.hr_tools import SCHEDULED_INTERVIEWS, SENT_EMAILS, build_registry  # noqa: E402

SCENARIOS = [
    "帮我写一份 Java 后端工程师的 JD",
    "帮我筛一下简历",
    "给张三安排明天下午面试",
    "发邮件通知候选人面试时间",
    "帮我查一下年假制度",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="使用 DeepSeek 真实模型")
    ap.add_argument("--trace", action="store_true", help="打印完整 Trace 时间线")
    ap.add_argument("--context", action="store_true", help="打印上下文预算占用账本（M2）")
    args = ap.parse_args()

    mode = "deepseek" if args.live else "stub"
    llm = build_llm(mode)
    registry = build_registry()

    print("=" * 74)
    print(f"HRAH demo  |  LLM = {llm.name}  |  已注册工具 {len(registry.names())} 个")
    print(f"高风险工具: {registry.high_risk_names()}")
    print("=" * 74)

    for i, text in enumerate(SCENARIOS, 1):
        # 每个场景独立会话：避免上一条的 observations 串味
        runner = AgentRunner(llm, registry, max_steps=6, auto_confirm=False)
        print(f"\n--- 场景 {i} ---")
        print(f"用户: {text}")
        result = runner.run(text)
        print(f"状态: {result.status}  |  会话状态: {result.state}")
        print(f"回复: {result.message}")
        if result.tools_called:
            print(f"调用工具: {result.tools_called}")
        if result.pending_tool:
            print(f"挂起待确认: {result.pending_tool} {result.pending_args}")

        # M2：上下文预算账本
        if args.context:
            pkg = getattr(runner, "last_context_package", None)
            if pkg is not None:
                print(pkg.to_text())

        # 先取出本轮 Trace（含 gate span），确认动作会另起一条 Trace
        trace_text = ""
        if args.trace:
            tracer = getattr(runner, "_last_tracer", None)
            trace_text = tracer.to_text() if tracer else "（无 Trace）"

        # 模拟"人点了确认"
        if result.status == STATUS_AWAITING_CONFIRMATION and result.pending_tool:
            print("  >> 模拟人工确认（真实场景此处由人点击）")
            confirm = runner.confirm_and_execute(
                result.pending_tool, result.pending_args or {}
            )
            print(f"  >> 确认后: {confirm.message}")

        if trace_text:
            print()
            print(trace_text)

    print("\n" + "=" * 74)
    print("副作用检查（验证门控是否真的起作用）")
    print(f"  实际安排的面试: {SCHEDULED_INTERVIEWS}")
    print(f"  实际发出的邮件: {SENT_EMAILS}")
    print("  说明: 只有在人工确认后，上面两个列表才会出现内容。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
