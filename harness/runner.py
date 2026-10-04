# -*- coding: utf-8 -*-
"""
会话主循环 —— 把注册表、规划器、上下文、状态机、Trace、门控串起来。

对应 PRD：
- 5.4 上下文分区预算（M2）
- 5.5 会话状态机（M2）
- 5.6 Trace
- FR-003 / FR-007 / FR-008 高风险动作的人工确认门控
- FR-009 工具失败重试一次
- FR-010 非法动作拒绝

关键设计：**门控在框架层强制，不依赖模型自觉。**
即使模型直接输出 CALL_TOOL 去调用高风险工具，runner 也会把它拦下来转成待确认状态。
这是"受控"二字的落点 —— 评测时可以验证"模型想违规也违规不了"。

M2 相对 M1 的两处变化：
1. 上下文不再"固定塞 top_k 个工具"，改由 ContextManager 按分区预算组装与裁剪，
   每次运行都产出可度量的账本（占用率 / 注入工具 / 裁剪工具 / 历史压缩条数）。
2. 会话状态不再是散落的 `session.state = XXX`，改由 SessionState 按显式转移表推进，
   非法转移直接抛错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .context import Budget, ContextManager, ContextPackage
from .llm import BaseLLM
from .planner import (
    ACTION_ASK_USER,
    ACTION_CALL_TOOL,
    ACTION_FINISH,
    ACTION_REQUEST_CONFIRM,
    SYSTEM_PROMPT,
    Planner,
    PlanError,
)
from .state import (
    S_AWAITING_CONFIRMATION,
    S_AWAITING_INPUT,
    S_DONE,
    S_EXECUTING,
    S_FAILED,
    S_INTENT_RESOLVED,
    S_PLANNING,
    SessionState,
)
from .tool_registry import RISK_HIGH, ToolRegistry
from .tracer import SPAN_GATE, SPAN_PLAN, SPAN_RESPOND, SPAN_RUN, SPAN_TOOL, Tracer

# 一轮交互的结束状态
STATUS_DONE = "done"
STATUS_AWAITING_INPUT = "awaiting_input"
STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
STATUS_FAILED = "failed"
STATUS_MAX_STEPS = "max_steps_exceeded"


@dataclass
class TurnResult:
    status: str
    message: str
    state: str
    trace_id: str
    tools_called: List[str] = field(default_factory=list)
    pending_tool: Optional[str] = None
    pending_args: Optional[Dict[str, Any]] = None
    error: str = ""
    trace_summary: Dict[str, Any] = field(default_factory=dict)
    context_report: Dict[str, Any] = field(default_factory=dict)


class AgentRunner:
    def __init__(
        self,
        llm: BaseLLM,
        registry: ToolRegistry,
        max_steps: int = 6,
        auto_confirm: bool = False,
        budget: Optional[Budget] = None,
        session: Optional[SessionState] = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.planner = Planner(llm, registry)
        self.max_steps = max_steps
        self.auto_confirm = auto_confirm
        self.budget = budget or Budget()
        self.context = ContextManager(registry, self.budget)
        self.session = session or SessionState()
        self.last_context_package: Optional[ContextPackage] = None

    # ---------- 对外主入口 ----------

    def run(self, user_input: str) -> TurnResult:
        tracer = Tracer()
        self._last_tracer = tracer
        self.planner.tracer = tracer
        session = self.session
        session.add_message("user", user_input)

        root = tracer.start_span(SPAN_RUN, "run", input_text=user_input)
        tools_called: List[str] = []

        # ---- M2：上下文按分区预算组装（替代 M1 的固定 top-k 注入）----
        pkg = self.context.build(
            user_input=user_input,
            slots=session.slots,
            history=session.history[:-1],   # 刚加进来的这条用户输入不再重复计入历史
            observations=session.observations,
            system_prompt=SYSTEM_PROMPT,
        )
        self.last_context_package = pkg

        ctx_span = tracer.start_span(
            SPAN_PLAN,
            "context.build",
            parent_id=root.span_id,
            input_text=user_input,
            meta={"budget": pkg.to_report()},
        )
        tracer.end_span(
            ctx_span,
            output_text=(
                f"注入 {len(pkg.selected_tools)} 个工具"
                + (f"，裁剪 {len(pkg.dropped_tools)} 个" if pkg.dropped_tools else "")
            ),
            tokens=pkg.total_tokens,
        )

        # 工具筛选完成 = 意图解析完成（PRD 5.5：IDLE → INTENT_RESOLVED → PLANNING）
        session.transition(S_INTENT_RESOLVED, reason="按意图筛选工具定义")
        session.transition(S_PLANNING, reason="开始规划")

        for step_idx in range(self.max_steps):
            try:
                step, repairs = self.planner.decide(
                    user_input=user_input,
                    slots=session.slots,
                    tools_prompt=pkg.tools,
                    observations=session.observations,
                    parent_span_id=root.span_id,
                    history_text=pkg.history,
                    retrieval_text=pkg.retrieval,
                    system_prompt=pkg.system or SYSTEM_PROMPT,
                )
            except PlanError as e:
                session.transition(S_FAILED, reason=f"规划失败: {e.kind}")
                tracer.end_span(root, status="failed", error=str(e))
                return TurnResult(
                    status=STATUS_FAILED,
                    message=f"规划失败：{e}",
                    state=session.state,
                    trace_id=tracer.trace_id,
                    tools_called=tools_called,
                    error=e.kind,
                    trace_summary=tracer.summary(),
                    context_report=pkg.to_report(),
                )

            if repairs:
                rp = tracer.start_span(
                    SPAN_PLAN,
                    "plan.repair",
                    parent_id=root.span_id,
                    input_text=str(repairs),
                )
                tracer.end_span(rp, output_text="按错误信息修正后重新输出", status="repaired")

            span = tracer.start_span(
                SPAN_PLAN,
                f"plan[{step_idx}] {step.action}",
                parent_id=root.span_id,
                input_text=step.reason,
                meta={"tool": step.tool} if step.tool else {},
            )
            tracer.end_span(span, output_text=step.tool or step.question or step.answer)

            # ---- 动作 1：调用工具 ----
            if step.action in (ACTION_CALL_TOOL, ACTION_REQUEST_CONFIRM):
                spec = self.registry.get(step.tool)  # type: ignore[arg-type]
                assert spec is not None  # planner 已校验

                # 门控：高风险一律拦下（FR-003）
                if spec.risk == RISK_HIGH and not self.auto_confirm:
                    draft = self._dry_run(spec.name, step.args, tracer, root.span_id)
                    session.set_pending(spec.name, step.args)
                    session.transition(S_AWAITING_CONFIRMATION, reason="高风险动作待人工确认")
                    tracer.end_span(root, output_text="等待人工确认")
                    return TurnResult(
                        status=STATUS_AWAITING_CONFIRMATION,
                        message=f"【需人工确认】即将执行高风险动作 {spec.name}。\n" + str(draft),
                        state=session.state,
                        trace_id=tracer.trace_id,
                        tools_called=tools_called,
                        pending_tool=spec.name,
                        pending_args=step.args,
                        trace_summary=tracer.summary(),
                        context_report=pkg.to_report(),
                    )

                session.transition(S_EXECUTING, reason=f"执行 {spec.name}")
                result = self._execute(spec.name, step.args, tracer, root.span_id)
                tools_called.append(spec.name)
                session.add_observation(f"{spec.name} -> {result}")
                session.transition(S_PLANNING, reason="执行完毕，继续规划")

                # 执行完再规划一次，让模型决定是继续还是收尾
                continue

            # ---- 动作 2：追问 ----
            if step.action == ACTION_ASK_USER:
                session.transition(S_AWAITING_INPUT, reason="缺失信息，追问用户")
                tracer.end_span(root, output_text=step.question)
                return TurnResult(
                    status=STATUS_AWAITING_INPUT,
                    message=step.question,
                    state=session.state,
                    trace_id=tracer.trace_id,
                    tools_called=tools_called,
                    trace_summary=tracer.summary(),
                    context_report=pkg.to_report(),
                )

            # ---- 动作 3：结束 ----
            if step.action == ACTION_FINISH:
                session.transition(S_DONE, reason="任务完成")
                session.add_message("assistant", step.answer)
                tracer.start_span(
                    SPAN_RESPOND, "respond", parent_id=root.span_id, input_text=step.answer
                )
                tracer.end_span(root, output_text=step.answer)
                return TurnResult(
                    status=STATUS_DONE,
                    message=step.answer,
                    state=session.state,
                    trace_id=tracer.trace_id,
                    tools_called=tools_called,
                    trace_summary=tracer.summary(),
                    context_report=pkg.to_report(),
                )

        session.transition(S_FAILED, reason="超出最大步数")
        tracer.end_span(root, status="failed", error="超出最大步数")
        return TurnResult(
            status=STATUS_MAX_STEPS,
            message=f"超出最大步数 {self.max_steps}，已中止。",
            state=session.state,
            trace_id=tracer.trace_id,
            tools_called=tools_called,
            error="max_steps_exceeded",
            trace_summary=tracer.summary(),
            context_report=pkg.to_report(),
        )

    def confirm_and_execute(self, pending_tool: str, pending_args: Dict[str, Any]) -> TurnResult:
        """人工确认后真正执行挂起的高风险动作。"""
        tracer = Tracer()
        self._last_tracer = tracer
        session = self.session
        root = tracer.start_span(SPAN_RUN, "confirm", input_text=pending_tool)
        args = dict(pending_args)
        args["confirmed"] = True

        session.transition(S_EXECUTING, reason=f"人工确认后执行 {pending_tool}")
        result = self._execute(pending_tool, args, tracer, root.span_id)
        session.add_observation(f"{pending_tool} -> {result}")
        session.clear_pending()
        session.transition(S_DONE, reason="确认动作执行完毕")
        tracer.end_span(root, output_text=str(result))

        return TurnResult(
            status=STATUS_DONE,
            message=f"已确认并执行 {pending_tool}：{result}",
            state=session.state,
            trace_id=tracer.trace_id,
            tools_called=[pending_tool],
            trace_summary=tracer.summary(),
        )

    # ---------- 内部 ----------

    def _execute(self, name: str, args: Dict[str, Any], tracer: Tracer, parent_id: str) -> Any:
        spec = self.registry.get(name)
        assert spec is not None
        span = tracer.start_span(
            SPAN_TOOL, f"tool:{name}", parent_id=parent_id, input_text=str(args)[:200]
        )
        try:
            result = spec.call(**args)
            tracer.end_span(span, output_text=str(result)[:300])
            return result
        except Exception as e:  # noqa: BLE001
            # FR-009：失败重试一次
            tracer.end_span(span, status="failed", error=f"{type(e).__name__}: {e}")
            retry = tracer.start_span(
                SPAN_TOOL, f"tool:{name}:retry", parent_id=parent_id, input_text=str(args)[:200]
            )
            try:
                result = spec.call(**args)
                tracer.end_span(retry, output_text=str(result)[:300], status="repaired")
                return result
            except Exception as e2:  # noqa: BLE001
                tracer.end_span(retry, status="failed", error=f"{type(e2).__name__}: {e2}")
                raise

    def _dry_run(self, name: str, args: Dict[str, Any], tracer: Tracer, parent_id: str) -> Any:
        """高风险动作先出草稿，不产生副作用（FR-007）。"""
        spec = self.registry.get(name)
        assert spec is not None
        span = tracer.start_span(
            SPAN_GATE, f"gate:{name}", parent_id=parent_id, input_text=str(args)[:200]
        )
        draft_args = dict(args)
        draft_args["confirmed"] = False
        try:
            draft = spec.call(**draft_args)
        except Exception as e:  # noqa: BLE001
            tracer.end_span(span, status="failed", error=str(e))
            raise
        tracer.end_span(span, output_text=str(draft)[:300])
        return draft
