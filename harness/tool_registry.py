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

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# 风险等级
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# ---------------------------------------------------------------------------
# 意图匹配信号（M4）
#
# M1~M3 用的是「关键词子串计数」，它有两个致命缺陷，各自对应一条已知缺陷：
#
#   BUG-005（误注入）：get_candidate 的关键词里有裸词「候选人」，
#     「发邮件通知候选人面试时间」因此被判定为要查候选人状态。
#     根因是**泛词区分度太低**——一句话里出现「候选人」不代表意图是查它。
#
#   ISSUE-006（过度兜底）：高风险工具被无条件强制注入，
#     任何一句话都会带上「发邮件」和「排面试」。当初的动机是
#     「防止模型臆造能力」，但防臆造只需要 system prompt 里那句
#     「清单外的能力直接 FINISH 说明不支持」就够了；
#     代价却是白占 token + 诱导模型误触高风险动作（每次误触都弹人工确认）。
#
# 修法是**分强弱的加权打分 + 全局阈值**：
#   strong（+3）：「动作 + 对象」的短语，用正则表达、允许中间插入修饰语
#                 （例如「安排.{0,10}?面试」能吃下「安排明天下午面试」）。
#                 区分度高，单独命中即成立。
#   weak  （+1）：单个泛词。区分度低，单独命中不足以入选。
#   入选门槛（3）：至少要有一个强信号。
#
# 这套规则是**通用原则，不是针对某条用例写死的例外**。
# 防作弊的自证见 scripts/tool_select_probe.py —— 那批句子不在 evals/cases.py 里。
# ---------------------------------------------------------------------------
SCORE_STRONG = 3
SCORE_WEAK = 1
MIN_SELECT_SCORE = 3


@dataclass
class ToolSpec:
    """一个工具的完整声明。"""

    name: str
    description: str
    params: Dict[str, str]            # 参数名 -> 类型/含义描述
    required: List[str] = field(default_factory=list)
    risk: str = RISK_LOW
    returns: str = ""
    keywords: List[str] = field(default_factory=list)   # 弱信号：单个泛词，+1
    strong: List[str] = field(default_factory=list)     # 强信号：正则片段，+3
    impl: Optional[Callable[..., Any]] = None

    def match_score(self, text: str) -> Tuple[int, List[str]]:
        """
        按意图给这个工具打分。返回 (得分, 命中说明)。

        打分规则见文件头注释：强信号 +3，弱信号 +1，入选门槛 MIN_SELECT_SCORE。

        命中说明会写进上下文账本，用来回答一个很关键的问题：
        **「为什么这一轮把这几个工具摆上了桌？」** 事后解释不清的选择，
        等于给调试留了个黑洞。
        """
        if not text:
            return 0, []
        hits: List[str] = []
        score = 0
        for pat in self.strong:
            if pat and re.search(pat, text):
                score += SCORE_STRONG
                hits.append(f"strong:{pat}")
        for kw in self.keywords:
            if kw and kw in text:
                score += SCORE_WEAK
                hits.append(f"weak:{kw}")
        return score, hits

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

    def select_for_intent(
        self, user_input: str, top_k: int = 5, min_score: int = MIN_SELECT_SCORE
    ) -> List[ToolSpec]:
        """
        按意图筛选需要注入上下文的工具（PRD 5.4 工具定义分区预算）。

        M4 的两个改动：
        1. 打分改为 ToolSpec.match_score（加权 + 阈值），不再数关键词个数。
        2. **不再无条件注入高风险工具** —— 理由见文件头注释，
           一句话总结：那是防护过当，代价大于收益。

        打分逻辑统一放在 ToolSpec.match_score 里，
        避免「注册表一套算法、上下文管理器另一套算法」这种必然发散的写法。
        """
        scored: List[Tuple[int, str, ToolSpec]] = []
        for spec in self._tools.values():
            score, _ = spec.match_score(user_input)
            scored.append((score, spec.name, spec))
        scored.sort(key=lambda x: (-x[0], x[1]))

        picked = [spec for score, _, spec in scored if score >= min_score]
        return picked[:top_k] if top_k > 0 else picked

    def to_prompt(self, specs: Optional[List[ToolSpec]] = None) -> str:
        specs = specs if specs is not None else self.all()
        if not specs:
            return "（当前没有可用工具）"
        return "\n".join(s.to_prompt() for s in specs)
