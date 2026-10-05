# -*- coding: utf-8 -*-
"""
Dify 云端对照适配器 —— M5 双轨评测的「对照轨」。

目的（见 docs/03-ADR-什么时候不该用Dify.md，跑完对比后写）：
把同一套 24 条用例喂给 Dify 云端工作流，用**同一套裁判**（evals/judges.py）
打分，和自研 harness 横向对比。比的不是"谁更聪明"，而是"谁更可控、更可观测、可回归"。

重要诚实标注（已按 2026-10-05 实测校正，不靠记忆）：
- Dify 云端 `chat-messages` 的 **Agent 类应用只支持 streaming**（blocking 会被 400 拒：
  "Agent App only supports streaming response mode"）。本适配器已改为 streaming + SSE 解析。
- 实测确认：**streaming Agent API 会在 `agent_thought` 事件里暴露 tool / tool_input / observation**，
  所以"调了哪个工具"在流式接口下**是可见的**——这比最初的"完全不可观测"设想要好，
  本文件已从 `raw["thoughts"]` 抽取 `tools_called`。
- 但 Dify **没有 durable span 树、没有 gate_count 概念、不建模 awaiting_confirmation /
  awaiting_input 状态机、也不上报副作用（scheduled / sent）**。因此 C 类（门控、契约）和
  A 类（工具选择清单）这类依赖引擎内部状态机的断言，在 Dify 侧**仍天然不可比**。这部分在
  评测里单独标记为「不可比（设计）」，不计入通过率，不是适配器有 bug。
- 实测新发现：Dify Agent 除我们添加的自定义工具外，**还内置 `shell_run` 等工具**（在 Dify
  沙箱执行），会出现在 agent_thought 中。说明"Agent 能调哪些工具"不完全由用户显式控制——
  这条作为补充证据写进 ADR-003 论据一/二。
- 仍标 [待核实] 的字段：Dify 各事件的完整 schema、日志页是否落 span 级数据，以官方文档为准。
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from evals.cases import CASES, CATEGORY_NAMES, LEVEL_MODEL, Case
from evals.judges import Judgement, LLMJudge, TurnObs, judge_turn
from harness.llm import DeepSeekLLM


# --------------------------------------------------------------------------
# Dify 客户端：只做一件事——把一句话发给 Dify，拿回回答。健壮性按 C4 做满。
# --------------------------------------------------------------------------

class DifyClient:
    """Dify 云端对话客户端（urllib 直连，零第三方依赖）。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 60,
    ) -> None:
        # C6：key 只从环境变量或参数来，绝不硬编码、不打印
        self.api_key = api_key or os.environ.get("DIFY_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DIFY_BASE_URL", "https://api.dify.ai")).rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("未找到 DIFY_API_KEY。请在环境变量设置，或参数传入。")
        # [待核实: Dify 官方 API 文档] 云端聊天消息端点
        self.endpoint = f"{self.base_url}/v1/chat-messages"
        self._conversation_id: Optional[str] = None
        # 实测踩坑：api.dify.ai 前置 Cloudflare 会按 UA 封禁机器人，
        # 默认 Python-urllib UA 直接吃 403（Cloudflare 错误码 1010，cfOrigin;dur=0 表示没到源站）。
        # 显式带浏览器风格 UA 才能到达 Dify（见 docs/99-问题与解决记录.md 踩坑7）。
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HRAH-Eval/1.0"

    def chat(self, user_input: str) -> Tuple[str, Dict[str, Any]]:
        """
        发一句话给 Dify，返回 (回答文本, 原始响应摘要)。

        两个 Dify 云端实测要点（见 docs/99-问题与解决记录.md 踩坑7/8）：
        1) Agent 类应用**只支持 streaming**：blocking 会被 400 拒
           （Agent App only supports streaming response mode）。
        2) Cloudflare 按 UA 拦机器人：默认 urllib UA 会吃 403。
           故显式带浏览器风格 User-Agent。
        失败策略：可重试错误(429/5xx/网络层)退避重试 3 次；4xx 不重试直接报错；
        全失败则抛 RuntimeError，由上层标记为「未判定」而非「失败」。
        """
        # [待核实: Dify 官方 API 文档] 请求体字段
        payload: Dict[str, Any] = {
            "inputs": {},
            "query": user_input,
            "response_mode": "streaming",
            "user": "hra-harness-eval",
            "conversation_id": self._conversation_id or "",
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": self.user_agent,
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        last_err: Exception = RuntimeError("未发起调用")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    answer, meta = self._read_stream(resp)
                return answer, meta
            except urllib.error.HTTPError as e:
                last_err = e
                if attempt < 2 and e.code in (429, 500, 502, 503, 504):
                    time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.3))
                    continue
                detail = e.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"Dify 调用失败 {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.3))
                    continue
                raise RuntimeError(f"Dify 网络错误: {e.reason}") from e
        raise RuntimeError(f"Dify 重试 3 次仍失败: {last_err}") from last_err

    def _read_stream(self, resp: Any) -> Tuple[str, Dict[str, Any]]:
        """
        解析 Dify SSE 流，拼出最终回答；同时采集事件类型与 agent_thought，
        用于判断 Dify Agent 到底暴露了多少内部信息（可观测性对比的关键证据）。
        """
        answer_parts: List[str] = []
        events: List[str] = []
        thoughts: List[Dict[str, Any]] = []
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            ev = data.get("event", "")
            events.append(ev)
            if data.get("conversation_id"):
                self._conversation_id = data["conversation_id"]
            if ev in ("message", "agent_message"):
                answer_parts.append(data.get("answer", ""))
            elif ev == "agent_thought":
                # Agent 类应用把「用了哪个工具/观察结果」放进 agent_thought
                thoughts.append({
                    "tool": data.get("tool"),
                    "tool_input": data.get("tool_input"),
                    "observation": data.get("observation"),
                    "thought": data.get("thought"),
                })
            elif ev == "error":
                raise RuntimeError(f"Dify 流内错误: {data}")
        answer = "".join(answer_parts).strip()
        return answer, {"events": events, "thoughts": thoughts, "event_count": len(events)}


# --------------------------------------------------------------------------
# 把 Dify 的回答规整成同一套 TurnObs，交给同一套裁判
# --------------------------------------------------------------------------

@dataclass
class DifyRunResult:
    case_id: str
    obs: TurnObs
    judgement: Optional[Judgement] = None
    undetermined: bool = False          # 调用失败 / 裁判不可用 → 不计入通过率
    incomparable: bool = False          # 设计不可比（Dify 不建模该信号）→ 不计入通过率
    incompat_reason: str = ""           # 不可比原因（给人看的）
    error: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def _normalize(case: Case, answer: str, raw: Dict[str, Any]) -> TurnObs:
    """
    把 Dify 回答转成 TurnObs。
    streaming Agent API 的 agent_thought 里能看到 tool/tool_input/observation，
    所以「调了哪个工具」**可抽取**（见本函数 tools_called）。
    但 Dify 没有 durable span 树、没有 gate_count、不建模 awaiting_* 状态机、
    也不上报副作用——这些信号统一留空/置 0，并在上层标记为「设计不可比」。
    """
    thoughts: List[Dict[str, Any]] = raw.get("thoughts", []) or []
    tools_called = [t["tool"] for t in thoughts if t.get("tool")]
    # 去重保序
    seen = set()
    deduped: List[str] = []
    for t in tools_called:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return TurnObs(
        status="done",               # Dify 不显式建模状态机，统一记为 done（对比时说明局限）
        message=answer,
        tools_called=deduped,        # streaming agent_thought 可观测 → 抽取
        pending_tool=None,           # Dify 不建模"挂起的高风险动作"
        tools_selected=[],           # 规划器候选清单 Dify 不暴露
        gate_count=0,                # Dify 无门控概念
        span_count=1,                # Dify 侧无 span 树，记 1 表示"有一次对话"
        scheduled=[],
        sent=[],
        confirm_executed=False,      # Dify 不建模"确认后落地"
    )


# Dify 归一化后**永远无法提供**的引擎内部信号：依赖它们做断言的用例在 Dify 侧不可比。
_INCOMPARABLE_KEYS = {
    "gate_count",            # Dify 无门控概念
    "pending_tool",          # Dify 不建模"挂起的高风险动作"
    "no_side_effect",        # Dify 不上报副作用（scheduled/sent）
    "confirm_then_side_effect",
    "scheduled",
    "sent",
    "tools_selected_include",   # 规划器候选清单 Dify 不暴露
    "tools_selected_exclude",
}

# status 期望值若不是 "done"（即依赖 awaiting_* 状态机），Dify 不可比
_INCOMPARABLE_STATUS_VALUES = {"awaiting_confirmation", "awaiting_input"}


def _dify_incomparable(case: Case) -> tuple[bool, str]:
    """
    判断某条用例在 Dify 对照轨是否「设计不可比」。
    返回 (是否不可比, 原因)。
    注意：D 类（LLM 裁判打分）和只查 message 关键词的用例是可比的，不在此列。
    """
    exp = case.expect or {}
    if case.kind == "unit":
        return True, "引擎内部单元断言（契约/规划器），Dify 无对应内部 API"
    hits = [k for k in _INCOMPARABLE_KEYS if k in exp]
    if hits:
        return True, f"依赖 Dify 不可建模的信号: {', '.join(hits)}"
    if exp.get("status") in _INCOMPARABLE_STATUS_VALUES:
        return True, f"依赖状态机状态 {exp.get('status')}，Dify 不建模 awaiting_*"
    if any(s in _INCOMPARABLE_STATUS_VALUES for s in exp.get("status_in", [])):
        return True, f"依赖状态机状态集合 {exp.get('status_in')}，Dify 不建模 awaiting_*"
    return False, ""


def run_dify_eval(
    repeat: int = 3,
    category: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    judge_llm: Optional[DeepSeekLLM] = None,
) -> List[DifyRunResult]:
    """
    跑 Dify 对照评测。C5：循环有界（用例数 × repeat），不是无限循环。
    每条用例内部异常隔离：单个失败不拖垮整轮。
    """
    client = DifyClient(api_key=api_key, base_url=base_url)
    cases = [c for c in CASES if category is None or c.category == category]
    llm_judge = None  # 延迟构造：只有 model 级用例才需要 LLM 裁判，避免 C 类联调也索要 DEEPSEEK_API_KEY

    results: List[DifyRunResult] = []
    for case in cases:
        incompat, reason = _dify_incomparable(case)
        for r in range(repeat):
            # 设计不可比：Dify 不建模该信号（门控/状态机/副作用/工具清单/引擎内部单元）。
            # 不联网、不跑裁判——联网只会拿到无意义的回答或 400（单元用例无 query），
            # 跑规则裁判只会得到误导性失败。直接标记不可比，不计入通过率。
            if incompat:
                results.append(DifyRunResult(
                    case.id, TurnObs(), None, incomparable=True, incompat_reason=reason,
                ))
                continue
            try:
                answer, raw = client.chat(case.user_input)
                obs = _normalize(case, answer, raw)
                if case.level == LEVEL_MODEL and case.rubric:
                    if llm_judge is None:
                        llm_judge = LLMJudge(judge_llm or DeepSeekLLM())
                    j = llm_judge.judge(case, answer)
                else:
                    j = judge_turn(case, obs)
                results.append(DifyRunResult(case.id, obs, j, j.undetermined, raw=raw))
            except Exception as e:  # noqa: BLE001 - 单条隔离
                results.append(DifyRunResult(
                    case.id, TurnObs(), None, undetermined=True,
                    error=f"{type(e).__name__}: {str(e)[:120]}",
                ))
    return results
