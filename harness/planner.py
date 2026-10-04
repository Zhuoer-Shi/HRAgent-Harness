# -*- coding: utf-8 -*-
"""
受控规划器 —— 动作空间固定，不允许模型自由发挥。

对应 PRD 5.2 / FR-010：
模型每一步只能输出 4 种动作之一：
    CALL_TOOL        调用注册表中的工具
    ASK_USER         追问缺失信息
    REQUEST_CONFIRM  请求人工确认
    FINISH           结束并给出最终回应

任何不在动作空间内的输出都被拒绝，并允许携带错误信息重试一次；
重试仍失败则记为规划失败（repaired -> failed），由 runner 上报。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .llm import BaseLLM
from .tool_registry import ToolRegistry

# 允许的动作空间（PRD 5.2）
ACTION_CALL_TOOL = "CALL_TOOL"
ACTION_ASK_USER = "ASK_USER"
ACTION_REQUEST_CONFIRM = "REQUEST_CONFIRM"
ACTION_FINISH = "FINISH"

ALLOWED_ACTIONS = (
    ACTION_CALL_TOOL,
    ACTION_ASK_USER,
    ACTION_REQUEST_CONFIRM,
    ACTION_FINISH,
)

SYSTEM_PROMPT = """你是一个受控 Agent 的规划器。你只能从固定的动作空间中选一个动作。

【动作空间】
1. CALL_TOOL       —— 调用一个已注册的工具
2. ASK_USER        —— 信息不足，向用户追问（一次只问最关键的 1~2 项）
3. REQUEST_CONFIRM —— 动作高风险，请求人工确认
4. FINISH          —— 任务完成，给出最终回答

【输出格式】只输出一个 JSON 对象，不要输出任何其他文字：
CALL_TOOL:        {"action":"CALL_TOOL","tool":"工具名","args":{...},"reason":"..."}
ASK_USER:         {"action":"ASK_USER","question":"...","missing":["字段名"],"reason":"..."}
REQUEST_CONFIRM:  {"action":"REQUEST_CONFIRM","tool":"工具名","args":{...},"reason":"..."}
FINISH:           {"action":"FINISH","answer":"...","reason":"..."}

【硬性约束】
- 只能使用工具清单中列出的工具名，绝不臆造工具。
- 缺少必要信息时用 ASK_USER，不要靠猜测填参数。
- 需要调用清单以外的能力时，直接 FINISH 并说明不支持。
"""


@dataclass
class PlanStep:
    """一次规划结果。"""

    action: str
    tool: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    question: str = ""
    missing: List[str] = field(default_factory=list)
    answer: str = ""
    reason: str = ""
    raw: str = ""


class PlanError(Exception):
    """规划失败（动作非法 / 参数不合法 / 工具未注册 / 重试耗尽）。"""

    def __init__(self, message: str, kind: str, raw: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.raw = raw


class Planner:
    def __init__(
        self,
        llm: BaseLLM,
        registry: ToolRegistry,
        max_repair: int = 1,
        tracer: Any = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.max_repair = max_repair
        self.tracer = tracer

    def decide(
        self,
        user_input: str,
        slots: Optional[Dict[str, Any]] = None,
        tools_prompt: str = "",
        observations: Optional[List[str]] = None,
        parent_span_id: Optional[str] = None,
    ) -> Tuple[PlanStep, List[Dict[str, Any]]]:
        """
        产出下一步动作。
        返回 (PlanStep, 修复记录)；失败时抛 PlanError。
        """
        slots = slots or {}
        observations = observations or []
        repairs: List[Dict[str, Any]] = []

        prompt = self._build_prompt(user_input, slots, tools_prompt, observations)
        last_error = ""

        for attempt in range(self.max_repair + 1):
            user_msg = prompt
            if last_error:
                user_msg += f"\n\n[上一次输出被拒绝] 原因: {last_error}\n请修正后重新只输出一个 JSON 对象。"

            text = self._call_llm(user_msg, parent_span_id)
            try:
                step = self._parse_and_validate(text)
            except PlanError as e:
                last_error = e.args[0]
                repairs.append({"attempt": attempt + 1, "error": last_error, "raw": e.raw})
                if attempt == self.max_repair:
                    raise PlanError(
                        f"规划失败（已重试 {self.max_repair} 次）: {last_error}",
                        kind=e.kind,
                        raw=e.raw,
                    )
                continue

            step.raw = text
            if repairs:
                step.reason = (step.reason or "") + f"｜经 {len(repairs)} 次修复后通过"
            return step, repairs

        raise PlanError("规划失败：未知原因", kind="unknown")

    # ---------- 内部 ----------

    def _call_llm(self, user_msg: str, parent_span_id: Optional[str]) -> str:
        if self.tracer is not None:
            with self.tracer.span(
                "llm", "planner.decide", parent_id=parent_span_id, input_text=user_msg[:200]
            ) as sp:
                result = self.llm.complete(SYSTEM_PROMPT, user_msg)
                self.tracer.end_span(
                    sp,
                    output_text=result.text[:200],
                    tokens=result.total_tokens,
                )
                return result.text
        result = self.llm.complete(SYSTEM_PROMPT, user_msg)
        return result.text

    @staticmethod
    def _build_prompt(
        user_input: str,
        slots: Dict[str, Any],
        tools_prompt: str,
        observations: List[str],
    ) -> str:
        parts = [
            "【可用工具清单】\n" + (tools_prompt or "（无）"),
            "\n【已收集信息】\n" + (json.dumps(slots, ensure_ascii=False) if slots else "（空）"),
        ]
        if observations:
            parts.append("\n【已执行步骤与结果】\n" + "\n".join(f"{i+1}. {o}" for i, o in enumerate(observations)))
        parts.append("\n【用户最新输入】\n" + user_input)
        parts.append("\n请只输出一个 JSON 对象。")
        return "".join(parts)

    def _parse_and_validate(self, text: str) -> PlanStep:
        data = self._extract_json(text)
        if data is None:
            raise PlanError("输出不是合法 JSON", kind="bad_json", raw=text[:200])

        action = data.get("action")
        if action not in ALLOWED_ACTIONS:
            raise PlanError(
                f"动作 '{action}' 不在允许的动作空间 {list(ALLOWED_ACTIONS)} 内",
                kind="invalid_action",
                raw=text[:200],
            )

        if action == ACTION_CALL_TOOL or action == ACTION_REQUEST_CONFIRM:
            tool = data.get("tool")
            if not tool:
                raise PlanError("缺少 tool 字段", kind="missing_field", raw=text[:200])
            spec = self.registry.get(tool)
            if spec is None:
                raise PlanError(
                    f"工具 '{tool}' 未注册，注册表内只有: {self.registry.names()}",
                    kind="tool_not_registered",
                    raw=text[:200],
                )
            args = data.get("args") or {}
            ok, why = spec.validate(args)
            if not ok:
                raise PlanError(f"工具 {tool} 参数不合法: {why}", kind="bad_args", raw=text[:200])
            return PlanStep(action=action, tool=tool, args=args, reason=data.get("reason", ""))

        if action == ACTION_ASK_USER:
            question = data.get("question") or "请补充必要信息"
            return PlanStep(
                action=action,
                question=question,
                missing=list(data.get("missing") or []),
                reason=data.get("reason", ""),
            )

        # FINISH
        return PlanStep(action=ACTION_FINISH, answer=data.get("answer", ""), reason=data.get("reason", ""))

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        """从可能带有 ```json 包裹或前后废话的输出里抠出 JSON 对象。"""
        if not text:
            return None
        s = text.strip()
        s = re.sub(r"^```(?:json)?", "", s).strip()
        s = re.sub(r"```$", "", s).strip()
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:  # noqa: BLE001
            pass
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(s[start:end + 1])
                return obj if isinstance(obj, dict) else None
            except Exception:  # noqa: BLE001
                return None
        return None
