# -*- coding: utf-8 -*-
"""
会话状态机 —— 把"会话现在处于哪个阶段"显式建模，而不是散落的赋值语句。

对应 PRD 5.5 / FR-011 / FR-012：
- FR-011 会话状态只能沿允许的边转移
- FR-012 非法转移必须被拒绝并记录，不能悄悄改一个字符串

为什么单独抽成模块：M1 里状态就是 runner 里一行 `session.state = S_XXX`，
谁都能改、改错了也没人知道。长会话里最难排查的 bug 就是"状态悄悄跑偏了"。
M2 把它变成一张显式的转移表：非法转移直接抛错，转移轨迹可回放。

状态图：
    IDLE ──(工具筛选完成)──> INTENT_RESOLVED ──> PLANNING
                                                  │
                    ┌─────────────────────────────┼─────────────────┐
                    ▼                             ▼                 ▼
              EXECUTING                    AWAITING_INPUT   AWAITING_CONFIRMATION
                    │                             │                 │
                    └──────────> PLANNING <───────┘                 │
                                    │                               │
                                    ▼                               ▼
                                  DONE <───────────────────────────┘
                    FAILED / ESCALATED（异常出口）
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

S_IDLE = "IDLE"
S_INTENT_RESOLVED = "INTENT_RESOLVED"
S_PLANNING = "PLANNING"
S_EXECUTING = "EXECUTING"
S_AWAITING_INPUT = "AWAITING_INPUT"
S_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
S_DONE = "DONE"
S_FAILED = "FAILED"
S_ESCALATED = "ESCALATED"

TERMINAL_STATES = (S_DONE, S_ESCALATED)

# 显式转移表：不在表里的转移一律拒绝
ALLOWED_TRANSITIONS: Dict[str, set] = {
    S_IDLE: {S_INTENT_RESOLVED, S_PLANNING, S_FAILED, S_ESCALATED},
    S_INTENT_RESOLVED: {S_PLANNING, S_AWAITING_INPUT, S_FAILED, S_ESCALATED},
    S_PLANNING: {
        S_INTENT_RESOLVED,      # 拿到新信息后重新解析意图
        S_EXECUTING,
        S_AWAITING_INPUT,
        S_AWAITING_CONFIRMATION,
        S_DONE,
        S_FAILED,
        S_ESCALATED,
    },
    S_EXECUTING: {S_PLANNING, S_AWAITING_CONFIRMATION, S_DONE, S_FAILED, S_ESCALATED},
    S_AWAITING_INPUT: {S_INTENT_RESOLVED, S_PLANNING, S_DONE, S_FAILED, S_ESCALATED},
    S_AWAITING_CONFIRMATION: {S_EXECUTING, S_DONE, S_FAILED, S_ESCALATED},
    S_DONE: {S_INTENT_RESOLVED, S_PLANNING},
    S_FAILED: {S_INTENT_RESOLVED, S_PLANNING, S_ESCALATED},
    S_ESCALATED: set(),
}


class InvalidStateTransition(Exception):
    """非法状态转移。"""

    def __init__(self, frm: str, to: str, allowed: List[str]) -> None:
        super().__init__(f"非法状态转移: {frm} -> {to}（允许: {allowed or '无，终态'}）")
        self.frm = frm
        self.to = to
        self.allowed = allowed


@dataclass
class SessionState:
    """一次会话的完整状态：阶段 + 槽位 + 历史 + 待确认动作 + 转移轨迹。"""

    session_id: str = field(default_factory=lambda: "s-" + uuid.uuid4().hex[:8])
    state: str = S_IDLE
    slots: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, str]] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    pending_tool: Optional[str] = None
    pending_args: Optional[Dict[str, Any]] = None
    transitions: List[Dict[str, str]] = field(default_factory=list)

    # ---------- 转移 ----------

    def can(self, to: str) -> bool:
        return to in ALLOWED_TRANSITIONS.get(self.state, set())

    def transition(self, to: str, reason: str = "") -> str:
        """执行状态转移；非法则抛 InvalidStateTransition。"""
        if to == self.state:
            return self.state
        if not self.can(to):
            raise InvalidStateTransition(
                self.state, to, sorted(ALLOWED_TRANSITIONS.get(self.state, set()))
            )
        self.transitions.append({"from": self.state, "to": to, "reason": reason})
        self.state = to
        return self.state

    # ---------- 数据 ----------

    def add_message(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})

    def add(self, role: str, content: str) -> None:
        """兼容 M1 的写法（runner 里原来是 session.add）。"""
        self.add_message(role, content)

    def add_observation(self, text: str) -> None:
        self.observations.append(text)

    def merge_slots(self, **kv: Any) -> None:
        for k, v in kv.items():
            if v not in (None, "", []):
                self.slots[k] = v

    def set_pending(self, tool: str, args: Optional[Dict[str, Any]]) -> None:
        self.pending_tool = tool
        self.pending_args = dict(args or {})

    def clear_pending(self) -> None:
        self.pending_tool = None
        self.pending_args = None

    # ---------- 输出 ----------

    def snapshot(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "slots": dict(self.slots),
            "history_turns": len(self.history),
            "observations": len(self.observations),
            "pending_tool": self.pending_tool,
            "transitions": [f"{t['from']}→{t['to']}" + (f"（{t['reason']}）" if t["reason"] else "")
                            for t in self.transitions],
        }
