# -*- coding: utf-8 -*-
"""
工具注册表 —— Agent 唯一被允许执行的动作来源。

对应 PRD：
- FR-001 只允许调用注册表中声明的工具
- FR-014 注册表外的请求必须明确拒绝，不得臆造工具
- 5.1 工具清单与风险分级（风险等级驱动人工确认门控）
- 5.4 按意图只注入相关工具定义，服务于上下文 token 预算
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# 风险等级
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"


@dataclass
class ToolSpec:
    """一个工具的完整声明。"""

    name: str
    description: str
    params: Dict[str, str]            # 参数名 -> 类型/含义描述
    required: List[str] = field(default_factory=list)
    risk: str = RISK_LOW
    returns: str = ""
    keywords: List[str] = field(default_factory=list)  # 用于按意图筛选工具
    impl: Optional[Callable[..., Any]] = None

    def validate(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        """参数契约校验。返回 (是否通过, 失败原因)。"""
        if not isinstance(args, dict):
            return False, "参数必须是对象"
        missing = [k for k in self.required if k not in args or args[k] in (None, "", [])]
        if missing:
            return False, "缺少必填参数: " + ", ".join(missing)
        unknown = sorted(set(args) - set(self.params))
        if unknown:
            return False, "出现未声明参数: " + ", ".join(unknown)
        return True, ""

    def to_prompt(self) -> str:
        """把工具声明渲染成给模型看的一行文本（控制长度，服务于 token 预算）。"""
        ps = ", ".join(f"{k}({v})" for k, v in self.params.items()) or "无"
        req = ", ".join(self.required) or "无"
        return (
            f"- {self.name}｜风险:{self.risk}\n"
            f"  用途: {self.description}\n"
            f"  参数: {ps}\n"
            f"  必填: {req}"
        )

    def call(self, **args: Any) -> Any:
        if self.impl is None:
            raise NotImplementedError(f"工具 {self.name} 未绑定实现")
        return self.impl(**args)


class ToolRegistry:
    """工具注册表。"""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def contains(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def all(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def high_risk_names(self) -> List[str]:
        return [s.name for s in self._tools.values() if s.risk == RISK_HIGH]

    def select_for_intent(self, user_input: str, top_k: int = 5) -> List[ToolSpec]:
        """
        按意图筛选需要注入上下文的工具（PRD 5.4 工具定义分区预算）。
        这里用轻量关键词打分，避免把 9 个工具定义全塞进 prompt。
        """
        scored: List[Tuple[int, ToolSpec]] = []
        for spec in self._tools.values():
            score = sum(1 for kw in spec.keywords if kw and kw in user_input)
            scored.append((score, spec))
        scored.sort(key=lambda x: (-x[0], x[1].name))

        picked = [spec for score, spec in scored[:top_k]]
        # 高风险工具永远注入：模型必须知道它们的存在，否则会臆造能力
        for spec in self._tools.values():
            if spec.risk == RISK_HIGH and spec not in picked:
                picked.append(spec)
        return picked

    def to_prompt(self, specs: Optional[List[ToolSpec]] = None) -> str:
        specs = specs if specs is not None else self.all()
        if not specs:
            return "（当前没有可用工具）"
        return "\n".join(s.to_prompt() for s in specs)
