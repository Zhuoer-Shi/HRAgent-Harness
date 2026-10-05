# -*- coding: utf-8 -*-
"""
评测执行器 —— 跑用例、判分、算指标、出报告、对 baseline。

对应 PRD 5.7 / FR-011~FR-016。三条硬约束：

1. **每条用例、每一轮都必须是全新的会话。**
   BUG-002 已经踩过一次坑：共享 session 会让后面的用例全部被上一轮的状态污染，
   指标全部失真。这里强制每轮新建 runner + 清空工具副作用。

2. **跳过不算通过。**
   需要真模型的用例在 Stub 下会被跳过，既不计入分子也不计入分母。
   把跳过的算成通过，是自欺欺人里最常见的一种。

3. **Trace 全量落盘。**
   没有落盘就做不了跨运行的失败模式分析（ISSUE-003）。
   每次运行的完整 span 链路写进 reports/traces/，随时能单条回放。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from harness.llm import BaseLLM, build_llm
from harness.runner import (
    STATUS_AWAITING_CONFIRMATION,
    STATUS_FAILED,
    STATUS_MAX_STEPS,
    AgentRunner,
)
from tools.hr_tools import SCHEDULED_INTERVIEWS, SENT_EMAILS, build_registry  # noqa: E402

from .cases import CATEGORY_NAMES, KIND_TURN, KIND_UNIT, LEVEL_MODEL, CASES, Case
from .judges import Judgement, LLMJudge, TurnObs, judge_turn, judge_unit

REPORTS_DIR = "reports"
TRACES_DIR = os.path.join(REPORTS_DIR, "traces")
BASELINE_PATH = os.path.join(REPORTS_DIR, "eval_baseline.json")

# 异常终止的状态：出现这些说明这轮没有「受控」地结束
UNCONTROLLED_STATUS = (STATUS_FAILED, STATUS_MAX_STEPS)


@dataclass
class RunRecord:
    round: int
    status: str = ""
    passed: bool = False
    undetermined: bool = False
    trace_id: str = ""
    span_count: int = 0
    score: Optional[int] = None
    tools_called: List[str] = field(default_factory=list)
    tools_selected: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    reason: str = ""

    def signature(self) -> str:
        """本轮结果的指纹，用于算稳定性。"""
        return json.dumps(
            [self.status, self.passed, sorted(self.tools_called), sorted(self.tools_selected)],
            ensure_ascii=False,
            sort_keys=True,
        )


@dataclass
class CaseResult:
    case: Case
    skipped: bool = False
    skip_reason: str = ""
    passed: bool = False
    stable: bool = False
    undetermined: bool = False
    runs: List[RunRecord] = field(default_factory=list)


@dataclass
class EvalReport:
    backend: str
    repeat: int
    total_cases: int
    results: List[CaseResult]

    @property
    def executed(self) -> List[CaseResult]:
        return [r for r in self.results if not r.skipped]

    @property
    def skipped(self) -> List[CaseResult]:
        return [r for r in self.results if r.skipped]

    @property
    def passed_cases(self) -> List[CaseResult]:
        return [r for r in self.executed if r.passed]

    # ---------- 指标 ----------

    @property
    def pass_rate(self) -> float:
        ex = self.executed
        return len([r for r in ex if r.passed]) / len(ex) if ex else 0.0

    @property
    def stability(self) -> float:
        ex = self.executed
        return len([r for r in ex if r.stable]) / len(ex) if ex else 0.0

    @property
    def trace_complete_rate(self) -> float:
        # 单元型断言不走会话，本来就没有 Trace，不计入这个指标
        runs = [rr for r in self.executed if r.case.kind == KIND_TURN for rr in r.runs]
        return len([rr for rr in runs if rr.span_count > 0]) / len(runs) if runs else 0.0

    @property
    def controlled_completion_rate(self) -> float:
        """北极星：这一轮有没有「正常结束 且 全程合规」。"""
        runs = [rr for r in self.executed for rr in r.runs]
        if not runs:
            return 0.0
        good = [rr for rr in runs if rr.status not in UNCONTROLLED_STATUS and rr.passed]
        return len(good) / len(runs)

    @property
    def gate_rate(self) -> float:
        """门控触发率：所有声明了门控断言的运行轮次里，断言通过的比例。"""
        checks: List[bool] = []
        for r in self.executed:
            if "gate_count" not in (r.case.expect or {}):
                continue
            for rr in r.runs:
                checks.append(not any("门控" in f for f in rr.failed))
        return sum(1 for c in checks if c) / len(checks) if checks else 1.0

    @property
    def contract_rate(self) -> float:
        """契约通过率：所有实际调用的工具都必须在注册表内。"""
        runs = [rr for r in self.executed for rr in r.runs]
        if not runs:
            return 1.0
        ok = [rr for rr in runs if _all_registered(rr.tools_called)]
        return len(ok) / len(runs)

    def by_category(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for key, name in CATEGORY_NAMES.items():
            ex = [r for r in self.executed if r.case.category == key]
            out[key] = {
                "name": name,
                "executed": len(ex),
                "passed": len([r for r in ex if r.passed]),
                "skipped": len([r for r in self.results if r.skipped and r.case.category == key]),
            }
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "repeat": self.repeat,
            "total_cases": self.total_cases,
            "executed": len(self.executed),
            "skipped": len(self.skipped),
            "passed": len(self.passed_cases),
            "metrics": {
                "pass_rate": round(self.pass_rate, 4),
                "stability": round(self.stability, 4),
                "trace_complete_rate": round(self.trace_complete_rate, 4),
                "gate_rate": round(self.gate_rate, 4),
                "contract_rate": round(self.contract_rate, 4),
                "controlled_completion_rate": round(self.controlled_completion_rate, 4),
            },
            "cases": [
                {
                    "id": r.case.id,
                    "category": r.case.category,
                    "title": r.case.title,
                    "level": r.case.level,
                    "skipped": r.skipped,
                    "skip_reason": r.skip_reason,
                    "passed": r.passed,
                    "stable": r.stable,
                    "known_issue": r.case.known_issue,
                    "failed_checks": (r.runs[0].failed if r.runs else []),
                }
                for r in self.results
            ],
        }


def _all_registered(tools: List[str]) -> bool:
    from tools.hr_tools import build_registry as _build  # 局部导入，避免循环

    names = set(_build().names())
    return all(t in names for t in tools)


class EvalRunner:
    def __init__(
        self,
        llm: Optional[BaseLLM] = None,
        backend: str = "stub",
        repeat: int = 3,
        save_traces: bool = True,
        root: str = ".",
    ) -> None:
        self.llm = llm or build_llm(backend)
        self.backend = self.llm.name
        self.repeat = repeat
        self.save_traces = save_traces
        self.root = root
        self.judge = LLMJudge(self.llm)
        self.registry = build_registry()

    # ---------- 单轮 ----------

    def _run_once(self, case: Case, round_idx: int) -> RunRecord:
        # 状态隔离：每轮都是全新会话 + 清空副作用（BUG-002 的教训）
        SCHEDULED_INTERVIEWS.clear()
        SENT_EMAILS.clear()
        registry = build_registry()
        runner = AgentRunner(self.llm, registry, max_steps=6, auto_confirm=False)

        result = runner.run(case.user_input)

        # 需要验证「确认后才落地」的用例，走一次人工确认
        confirm_executed = False
        if (case.expect or {}).get("confirm_then_side_effect") and result.pending_tool:
            if result.status == STATUS_AWAITING_CONFIRMATION:
                runner.confirm_and_execute(result.pending_tool, result.pending_args or {})
                confirm_executed = True

        ctx = result.context_report or {}
        obs = TurnObs(
            status=result.status,
            message=result.message,
            state=result.state,
            tools_called=list(result.tools_called),
            pending_tool=result.pending_tool,
            tools_selected=list(ctx.get("tools_selected") or []),
            gate_count=int(result.trace_summary.get("gate_count", 0)),
            span_count=int(result.trace_summary.get("span_count", 0)),
            scheduled=list(SCHEDULED_INTERVIEWS),
            sent=list(SENT_EMAILS),
            confirm_executed=confirm_executed,
            error=result.error,
        )

        if case.category == "D" and self.judge.available:
            j = self.judge.judge(case, result.message)
        else:
            j = judge_turn(case, obs)

        rec = RunRecord(
            round=round_idx,
            status=result.status,
            passed=j.passed,
            undetermined=j.undetermined,
            trace_id=result.trace_id,
            span_count=obs.span_count,
            score=j.score,
            tools_called=obs.tools_called,
            tools_selected=obs.tools_selected,
            failed=[f"{c.name}｜{c.detail}" for c in j.failed_checks],
            reason=j.reason,
        )

        if self.save_traces:
            self._save_trace(case, rec, runner)
        return rec

    def _save_trace(self, case: Case, rec: RunRecord, runner: AgentRunner) -> None:
        tracer = getattr(runner, "_last_tracer", None)
        if tracer is None:
            return
        d = os.path.join(self.root, TRACES_DIR)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{case.id}_r{rec.round}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(tracer.to_json())

    # ---------- 单条用例 ----------

    def run_case(self, case: Case) -> CaseResult:
        res = CaseResult(case=case)

        # 需要真模型能力的用例，在 Stub 下跳过（不算通过、不算失败）
        if case.level == LEVEL_MODEL and not self.judge.available:
            res.skipped = True
            res.skip_reason = "需要真模型能力，当前后端为 Stub"
            return res

        if case.kind == KIND_UNIT:
            j: Judgement = judge_unit(case, self.registry, self.llm)
            rec = RunRecord(
                round=1,
                status="unit",
                passed=j.passed,
                undetermined=j.undetermined,
                failed=[f"{c.name}｜{c.detail}" for c in j.failed_checks],
            )
            res.runs = [rec]
            res.passed = j.passed and not j.undetermined
            res.stable = True
            res.undetermined = j.undetermined
            return res

        res.runs = [self._run_once(case, i + 1) for i in range(self.repeat)]
        res.undetermined = any(rr.undetermined for rr in res.runs)
        # 严格口径：N 轮全部通过才算这条用例通过
        res.passed = all(rr.passed for rr in res.runs) and not res.undetermined
        res.stable = len({rr.signature() for rr in res.runs}) == 1
        return res

    def run_all(self, cases: Optional[List[Case]] = None) -> EvalReport:
        cases = cases if cases is not None else CASES
        results = [self.run_case(c) for c in cases]
        return EvalReport(
            backend=self.backend,
            repeat=self.repeat,
            total_cases=len(cases),
            results=results,
        )


# ======================================================================
# baseline 回归
# ======================================================================

def save_baseline(report: EvalReport, root: str = ".") -> str:
    path = os.path.join(root, BASELINE_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def diff_baseline(report: EvalReport, root: str = ".") -> Optional[Dict[str, Any]]:
    path = os.path.join(root, BASELINE_PATH)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        base = json.load(f)

    old = {c["id"]: c for c in base.get("cases", [])}
    new = {c["id"]: c for c in report.to_dict()["cases"]}

    regressions = [
        cid for cid in new
        if cid in old and old[cid].get("passed") and not new[cid].get("passed")
    ]
    fixes = [
        cid for cid in new
        if cid in old and not old[cid].get("passed") and new[cid].get("passed")
    ]
    new_runs = [cid for cid in new if cid in old and old[cid].get("skipped") and not new[cid].get("skipped")]

    return {
        "old_pass_rate": base.get("metrics", {}).get("pass_rate", 0.0),
        "new_pass_rate": round(report.pass_rate, 4),
        "regressions": regressions,
        "fixes": fixes,
        "newly_executed": new_runs,
    }
