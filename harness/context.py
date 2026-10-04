# -*- coding: utf-8 -*-
"""
上下文管理器 —— 把"塞给模型的内容"当成一笔有预算的开支来管。

对应 PRD 5.4 / FR-004 / FR-005 / FR-006：
- FR-004 按意图只注入相关性得分 > 0 的工具定义
- FR-005 历史对话超预算时先压缩成单行摘要，仍超则丢弃最旧的
- FR-006 预算不足时按固定顺序裁剪；高风险工具永不裁剪（安全优先于预算）

为什么要它：M1 里工具是"固定塞 top_k 个"，得分 0 的无关工具也被填进来
（ISSUE-001：写 JD 时把 draft_email 也塞了进去），白白吃掉 token 还干扰判断。
M2 把它改成分区预算 + 可裁剪 + 可度量（每次运行都能打印占用率）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .tool_registry import RISK_HIGH, ToolRegistry, ToolSpec

# CJK 字符范围（中日韩统一表意文字 + 扩展 A + 兼容表意文字）
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def estimate_tokens(text: Optional[str]) -> int:
    """
    粗估 token 数：CJK 按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token。

    刻意保守高估 —— 宁可多裁一点，也不要把上下文撑爆。
    （不追求和真实分词器一致，够做预算控制就行。）
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return max(1, int(cjk + other / 4))


@dataclass
class Budget:
    """分区预算（PRD 5.4）。total 是本次规划调用可用的窗口大小。"""

    total: int = 6000
    ratios: Dict[str, float] = field(
        default_factory=lambda: {
            "system": 0.15,      # 系统提示词：固定成本
            "tools": 0.20,       # 工具定义：按意图注入
            "retrieval": 0.25,   # 检索片段：RAG 命中
            "history": 0.30,     # 历史对话 + 本轮执行结果（共享这个分区）
            "reserve": 0.10,     # 预留：给模型输出和不可预期的增长
        }
    )

    def limit(self, section: str) -> int:
        return max(1, int(self.total * self.ratios.get(section, 0.0)))


@dataclass
class ContextPackage:
    """一次组装好的上下文，带完整的用量账本。"""

    system: str = ""
    tools: str = ""
    retrieval: str = ""
    history: str = ""
    observations: str = ""
    selected_tools: List[str] = field(default_factory=list)
    dropped_tools: List[str] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    budget: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    history_compacted: int = 0
    history_dropped: int = 0

    @property
    def total_tokens(self) -> int:
        return sum(self.usage.values())

    def to_report(self) -> Dict[str, Any]:
        """评测与 Trace 用的结构化账本。"""
        total_budget = self.budget.get("total", 0)
        used = self.total_tokens
        sections: Dict[str, Any] = {}
        for key in ("system", "tools", "retrieval", "history", "observations"):
            lim = self.budget.get(key, 0)
            u = self.usage.get(key, 0)
            sections[key] = {
                "used": u,
                "limit": lim,
                "ratio": round(u / lim, 2) if lim else 0.0,
            }
        return {
            "total_tokens": used,
            "budget_total": total_budget,
            "utilization": round(used / total_budget, 3) if total_budget else 0.0,
            "sections": sections,
            "tools_selected": list(self.selected_tools),
            "tools_dropped": list(self.dropped_tools),
            "history_compacted": self.history_compacted,
            "history_dropped": self.history_dropped,
            "notes": list(self.notes),
        }

    def to_text(self) -> str:
        """终端可读的账本，也能直接截图进作品集。"""
        r = self.to_report()
        lines = [
            f"上下文预算  {r['total_tokens']} / {r['budget_total']} token"
            f"  （占用 {r['utilization']:.0%}）"
        ]
        for key, v in r["sections"].items():
            lines.append(
                f"  {key:<12} {v['used']:>5} / {v['limit']:<5}  ({v['ratio']:.0%})"
            )
        lines.append(f"  注入工具: {self.selected_tools or '（无）'}")
        if self.dropped_tools:
            lines.append(f"  裁剪工具: {self.dropped_tools}")
        if self.history_compacted or self.history_dropped:
            lines.append(
                f"  历史处理: 压缩 {self.history_compacted} 条 / 丢弃 {self.history_dropped} 条"
            )
        for n in self.notes:
            lines.append(f"  备注: {n}")
        return "\n".join(lines)


class ContextManager:
    """按分区预算组装上下文，并在超预算时按固定顺序裁剪。"""

    def __init__(
        self,
        registry: ToolRegistry,
        budget: Optional[Budget] = None,
        min_tool_score: int = 1,
        keep_recent_messages: int = 4,
        max_observations: int = 6,
    ) -> None:
        self.registry = registry
        self.budget = budget or Budget()
        self.min_tool_score = min_tool_score
        self.keep_recent_messages = keep_recent_messages
        self.max_observations = max_observations

    # ---------- 工具选择 ----------

    def score_tools(self, user_input: str) -> List[Tuple[int, str, ToolSpec]]:
        """关键词命中打分（轻量、可解释、不花钱）。"""
        scored: List[Tuple[int, str, ToolSpec]] = []
        for spec in self.registry.all():
            score = sum(1 for kw in spec.keywords if kw and kw in user_input)
            scored.append((score, spec.name, spec))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored

    def select_tools(
        self, user_input: str
    ) -> Tuple[List[ToolSpec], List[str], int, List[str]]:
        """
        选出要注入的工具定义。
        返回 (选中, 被裁掉的工具名, 占用 token, 备注)。
        """
        notes: List[str] = []
        scored = self.score_tools(user_input)
        relevant = [(s, n, sp) for s, n, sp in scored if s >= self.min_tool_score]

        # 一个都没命中：只给得分最高的 1 个，让模型能 FINISH 说"不支持"
        if not relevant and scored:
            s0, n0, sp0 = scored[0]
            relevant = [(s0, n0, sp0)]
            notes.append("无工具命中关键词，仅注入得分最高的 1 个（模型应 FINISH 说明不支持）")

        ordered: List[ToolSpec] = []
        seen = set()
        for _, name, spec in relevant:
            if name not in seen:
                seen.add(name)
                ordered.append(spec)
        # 高风险工具强制注入：模型必须知道它们存在，否则会臆造能力
        for spec in self.registry.all():
            if spec.risk == RISK_HIGH and spec.name not in seen:
                seen.add(spec.name)
                ordered.append(spec)

        limit = self.budget.limit("tools")
        picked: List[ToolSpec] = []
        dropped: List[str] = []
        used = 0
        for spec in ordered:
            cost = estimate_tokens(spec.to_prompt())
            # 高风险工具受保护：即使超预算也保留（安全优先于预算）
            if spec.risk == RISK_HIGH or used + cost <= limit:
                picked.append(spec)
                used += cost
            else:
                dropped.append(spec.name)
        if dropped:
            notes.append(f"工具分区预算不足，按相关性从低到高裁剪掉 {len(dropped)} 个")
        return picked, dropped, used, notes

    # ---------- 历史压缩 ----------

    def compact_history(
        self, history: List[Dict[str, str]], limit: int
    ) -> Tuple[str, int, int, int]:
        """
        历史对话压缩：保留最近 keep_recent_messages 条原文，更早的压成单行摘要。
        返回 (文本, token, 压缩条数, 丢弃条数)。
        """
        if not history:
            return "", 0, 0, 0

        keep = self.keep_recent_messages
        recent = history[-keep:]
        older = history[:-keep]

        older_lines: List[str] = []
        for m in older:
            role = m.get("role", "?")
            content = (m.get("content") or "").replace("\n", " ")
            clipped = content[:28] + ("…" if len(content) > 28 else "")
            older_lines.append(f"{role}: {clipped}")
        recent_lines = [f"{m.get('role', '?')}: {m.get('content', '')}" for m in recent]

        lines = older_lines + recent_lines
        compacted = len(older_lines)
        dropped = 0
        text = "\n".join(lines)
        while older_lines and estimate_tokens(text) > limit:
            older_lines = older_lines[1:]
            lines = older_lines + recent_lines
            dropped += 1
            text = "\n".join(lines)
        if dropped:
            text = f"（已省略更早的 {dropped} 条消息）\n" + text
        return text, estimate_tokens(text), compacted, dropped

    # ---------- 组装 ----------

    def build(
        self,
        user_input: str,
        slots: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        observations: Optional[List[str]] = None,
        retrieval: Optional[List[str]] = None,
        system_prompt: str = "",
    ) -> ContextPackage:
        """组装一次完整的上下文，并产出账本。"""
        slots = slots or {}
        history = history or []
        observations = observations or []
        notes: List[str] = []

        limits = {k: self.budget.limit(k) for k in ("system", "tools", "retrieval", "history")}
        limits["total"] = self.budget.total
        limits["reserve"] = self.budget.limit("reserve")
        # 历史与执行结果共享同一个分区额度，展示时给同一个上限
        limits["observations"] = limits["history"]

        # 1) system：固定成本，不裁剪
        sys_tokens = estimate_tokens(system_prompt)
        if sys_tokens > limits["system"]:
            notes.append(
                f"system 提示词超出分区预算 {sys_tokens}>{limits['system']}（固定成本，不裁剪）"
            )

        # 2) tools
        picked, dropped, tool_tokens, tool_notes = self.select_tools(user_input)
        notes.extend(tool_notes)

        # 3) observations（本轮执行结果优先于历史对话，先扣预算）
        obs_items = list(observations[-self.max_observations :])
        obs_text = "\n".join(f"{i+1}. {o}" for i, o in enumerate(obs_items))
        obs_tokens = estimate_tokens(obs_text)
        hist_limit = limits["history"]
        if obs_tokens > hist_limit:
            obs_items = [str(o)[:120] + "…" for o in obs_items]
            obs_text = "\n".join(f"{i+1}. {o}" for i, o in enumerate(obs_items))
            obs_tokens = estimate_tokens(obs_text)
            notes.append("执行结果占满历史分区，已把每条截断到 120 字")

        # 4) history（用历史分区剩下的额度）
        hist_left = max(0, hist_limit - obs_tokens)
        hist_text, hist_tokens, compacted, dropped_hist = self.compact_history(history, hist_left)
        if compacted:
            notes.append(
                f"历史对话压缩 {compacted} 条为单行摘要，保留最近 {self.keep_recent_messages} 条原文"
            )
        if dropped_hist:
            notes.append(f"压缩后仍超预算，丢弃最旧的 {dropped_hist} 条")

        # 5) retrieval
        ret_text = ""
        if retrieval:
            kept: List[str] = []
            used = 0
            for snip in retrieval:
                cost = estimate_tokens(snip)
                if used + cost <= limits["retrieval"]:
                    kept.append(snip)
                    used += cost
                else:
                    notes.append("检索片段超出分区预算，丢弃尾部片段")
                    break
            ret_text = "\n".join(kept)
        ret_tokens = estimate_tokens(ret_text)

        return ContextPackage(
            system=system_prompt,
            tools=self.registry.to_prompt(picked) if picked else "（当前没有可用工具）",
            retrieval=ret_text,
            history=hist_text,
            observations=obs_text,
            selected_tools=[s.name for s in picked],
            dropped_tools=dropped,
            usage={
                "system": sys_tokens,
                "tools": tool_tokens,
                "retrieval": ret_tokens,
                "history": hist_tokens,
                "observations": obs_tokens,
            },
            budget=limits,
            notes=notes,
            history_compacted=compacted,
            history_dropped=dropped_hist,
        )
