# -*- coding: utf-8 -*-
"""
Trace —— 让每一次运行都能被回放。

对应 PRD 5.6 / FR-002：
- 每次运行一个 trace_id
- span 类型：intent / plan / tool_call / gate / respond
- 每个 span 记录 输入摘要 / 输出摘要 / 耗时 / token / 状态
- 支持输出人类可读时间线
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

# span 类型
SPAN_RUN = "run"
SPAN_INTENT = "intent"
SPAN_PLAN = "plan"
SPAN_TOOL = "tool_call"
SPAN_GATE = "gate"
SPAN_LLM = "llm"
SPAN_RESPOND = "respond"


@dataclass
class Span:
    span_id: str
    parent_id: Optional[str]
    type: str
    name: str
    input_text: str = ""
    output_text: str = ""
    start: float = field(default_factory=time.perf_counter)
    end: Optional[float] = None
    tokens: int = 0
    status: str = "ok"          # ok / failed / repaired
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end is None:
            return 0.0
        return round((self.end - self.start) * 1000, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "type": self.type,
            "name": self.name,
            "input": self.input_text,
            "output": self.output_text,
            "duration_ms": self.duration_ms,
            "tokens": self.tokens,
            "status": self.status,
            "error": self.error,
            "meta": self.meta,
        }


class Tracer:
    """一次运行的链路记录器。"""

    def __init__(self, trace_id: Optional[str] = None) -> None:
        self.trace_id = trace_id or ("tr-" + uuid.uuid4().hex[:12])
        self.spans: List[Span] = []

    def start_span(
        self,
        type: str,
        name: str,
        parent_id: Optional[str] = None,
        input_text: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Span:
        span = Span(
            span_id="sp-" + uuid.uuid4().hex[:8],
            parent_id=parent_id,
            type=type,
            name=name,
            input_text=self._clip(input_text),
            meta=meta or {},
        )
        self.spans.append(span)
        return span

    def end_span(
        self,
        span: Span,
        output_text: str = "",
        status: str = "ok",
        error: str = "",
        tokens: int = 0,
    ) -> Span:
        span.end = time.perf_counter()
        span.output_text = self._clip(output_text)
        span.status = status
        span.error = error
        span.tokens = tokens
        return span

    @contextmanager
    def span(
        self,
        type: str,
        name: str,
        parent_id: Optional[str] = None,
        input_text: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Span]:
        """用 with 块自动记录耗时与异常。"""
        sp = self.start_span(type, name, parent_id, input_text, meta)
        try:
            yield sp
        except Exception as e:  # noqa: BLE001 - Trace 需要捕获一切异常
            self.end_span(sp, status="failed", error=f"{type(e).__name__}: {e}")
            raise
        else:
            if sp.end is None:
                self.end_span(sp)

    # ---------- 输出 ----------

    def to_dict(self) -> Dict[str, Any]:
        return {"trace_id": self.trace_id, "spans": [s.to_dict() for s in self.spans]}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_text(self) -> str:
        """人类可读的时间线，用于终端回放和作品集截图。"""
        lines = [f"Trace {self.trace_id}  （共 {len(self.spans)} 个 span）"]
        icons = {"ok": "+", "failed": "x", "repaired": "~"}
        for s in self.spans:
            mark = icons.get(s.status, "?")
            head = f"[{mark}] {s.type:<10} {s.name:<22} {s.duration_ms:>7.1f}ms"
            if s.tokens:
                head += f"  {s.tokens}tok"
            lines.append(head)
            if s.input_text:
                lines.append(f"        in : {s.input_text}")
            if s.output_text:
                lines.append(f"        out: {s.output_text}")
            if s.error:
                lines.append(f"        err: {s.error}")
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """评测用摘要：耗时、token、失败数、门控次数。"""
        total_ms = round(sum(s.duration_ms for s in self.spans), 2)
        total_tokens = sum(s.tokens for s in self.spans)
        return {
            "trace_id": self.trace_id,
            "span_count": len(self.spans),
            "tool_calls": sum(1 for s in self.spans if s.type == SPAN_TOOL),
            "gate_count": sum(1 for s in self.spans if s.type == SPAN_GATE),
            "failed_spans": sum(1 for s in self.spans if s.status == "failed"),
            "repaired_spans": sum(1 for s in self.spans if s.status == "repaired"),
            "total_ms": total_ms,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _clip(text: Any, limit: int = 400) -> str:
        s = text if isinstance(text, str) else str(text)
        s = s.replace("\n", " ")
        return s if len(s) <= limit else s[:limit] + "…"
