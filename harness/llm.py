# -*- coding: utf-8 -*-
"""
LLM 客户端。

两种模式：
1. DeepSeekLLM —— 走 DeepSeek OpenAI 兼容接口（零第三方依赖，用 urllib直连）
2. StubLLM     —— 规则模拟，不花钱不联网，用于跑通评测框架和回归测试

之所以保留 Stub：评测框架、Trace、失败统计必须与"模型好不好"解耦。
先用 Stub 把骨架跑通，再换真模型，前后对比本身就是一次回归实验。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class BaseLLM(ABC):
    name = "base"

    @abstractmethod
    def complete(self, system: str, user: str, temperature: float = 0.0) -> LLMResult:
        ...


class DeepSeekLLM(BaseLLM):
    """DeepSeek 客户端（OpenAI 兼容接口）。需要环境变量 DEEPSEEK_API_KEY。"""

    name = "deepseek"

    def __init__(
        self,
        model: str = "deepseek-chat",
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com",
        timeout: int = 60,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError(
                "未找到 DEEPSEEK_API_KEY。请设置环境变量，或改用 StubLLM（不花钱）。"
            )

    def complete(self, system: str, user: str, temperature: float = 0.0) -> LLMResult:
        url = f"{self.base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "stream": False,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        t0 = time.perf_counter()
        last_err: Exception = RuntimeError("未发起调用")
        # 退避重试：网络抖动（SSL EOF 等）与 5xx/429 可重试；4xx 是请求本身有误，不重试
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                last_err = e
                if attempt < 2 and e.code in (429, 500, 502, 503, 504):
                    time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.3))
                    continue
                detail = e.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"DeepSeek 调用失败 {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.3))
                    continue
                raise RuntimeError(f"DeepSeek 网络错误: {e.reason}") from e
        else:
            raise RuntimeError(f"DeepSeek 重试 3 次仍失败: {last_err}") from last_err
        latency = round((time.perf_counter() - t0) * 1000, 2)

        usage = body.get("usage", {}) or {}
        text = ""
        choices = body.get("choices") or []
        if choices:
            text = (choices[0].get("message") or {}).get("content", "") or ""
        return LLMResult(
            text=text,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency,
            model=self.model,
        )


class StubLLM(BaseLLM):
    """
    规则模拟模型：不联网、不花钱，用于在开发期跑通链路。

    它刻意"笨"一些 —— 只会按关键词做判断，
    这样才能验证框架层的门控与校验是否真的兜住了模型的不可靠。
    """

    name = "stub"

    def complete(self, system: str, user: str, temperature: float = 0.0) -> LLMResult:
        text = json.dumps(self._route(user), ensure_ascii=False)
        return LLMResult(
            text=text,
            prompt_tokens=max(1, len(system + user) // 3),
            completion_tokens=max(1, len(text) // 3),
            latency_ms=1.0,
            model="stub",
        )

    @classmethod
    def _route(cls, user: str) -> Dict[str, Any]:
        """
        Stub 的"策略"很笨：只要看到已经执行过工具，就收尾。
        这样能验证 runner 的主循环与收尾逻辑，也避免无限重复调用。
        """
        if "【已执行步骤与结果】" in user:
            seg = user.split("【已执行步骤与结果】")[1].split("【用户最新输入】")[0].strip()
            return {
                "action": "FINISH",
                "answer": "已完成。执行结果：" + seg[:300],
                "reason": "已有工具执行结果，收尾",
            }
        marker = "【用户最新输入】"
        idx = user.find(marker)
        u = user[idx + len(marker):].strip() if idx >= 0 else user
        return cls._decide(u)

    @staticmethod
    def _decide(user: str) -> Dict[str, Any]:
        # 切掉 planner 拼在末尾的提示语，否则它会被当成用户原话传进工具参数
        u = (user or "").split("请只输出一个 JSON 对象")[0].strip()

        # 高风险动作：先出草稿 + 请求确认
        # 注意顺序：邮件优先，否则"发邮件通知候选人面试时间"会被误判成安排面试
        if any(k in u for k in ("发邮件", "发通知", "邮件通知", "邮件")):
            return {
                "action": "CALL_TOOL",
                "tool": "send_email",
                "args": {
                    "to": "candidate@example.com",
                    "subject": "面试通知",
                    "body": "您好，已为您安排面试，请查收。",
                    "confirmed": False,
                },
                "reason": "发送邮件属于高风险动作，需人工确认",
            }
        if "面试" in u and any(k in u for k in ("安排", "约", "预约", "定", "排期")):
            return {
                "action": "CALL_TOOL",
                "tool": "schedule_interview",
                "args": {
                    "candidate": _extract_candidate(u),
                    "when": _extract_when(u),
                    "confirmed": False,
                },
                "reason": "安排面试属于高风险动作，需人工确认",
            }
        # 缺前置：没有 JD 就要筛简历
        if any(k in u for k in ("筛", "评估简历", "简历打分")) and "jd" not in u.lower():
            return {
                "action": "ASK_USER",
                "question": "筛选简历需要先有 JD 和评分标准。请提供岗位 JD，或先让我生成一份？",
                "missing": ["jd"],
                "reason": "缺少前置产物",
            }

        # 缺信息：要写 JD 但没说岗位
        if any(k in u for k in ("jd", "岗位描述", "招聘启事", "写一份")):
            role = _extract_role(u)
            if not role:
                return {
                    "action": "ASK_USER",
                    "question": "请告诉我具体岗位名称（例如 Java 后端工程师）？",
                    "missing": ["role"],
                    "reason": "缺少必要输入",
                }
            return {
                "action": "CALL_TOOL",
                "tool": "generate_jd",
                "args": {"role": role, "level": "中级", "city": "上海"},
                "reason": "信息齐全，生成 JD",
            }

        if any(k in u for k in ("制度", "假期", "报销", "知识", "规定")):
            return {
                "action": "CALL_TOOL",
                "tool": "search_knowledge",
                "args": {"query": u},
                "reason": "查询知识库",
            }

        return {
            "action": "FINISH",
            "answer": "我可以帮你：生成 JD、生成评分标准、筛选简历、生成面试方案、安排面试、发送通知。请告诉我具体要做什么。",
            "reason": "未匹配到具体任务",
        }


def _extract_role(u: str) -> str:
    """极简的岗位名抽取：命中常见技术岗位词即返回。"""
    roles = [
        "Java 后端工程师", "Java后端工程师", "Java 后端", "Java后端", "Java",
        "前端工程师", "前端", "算法工程师", "算法",
        "数据分析师", "数据分析", "产品经理", "测试工程师", "测试",
        "运维工程师", "运维", "HRBP", "招聘专员",
    ]
    for r in roles:
        if r in u:
            return r
    return ""


def _extract_candidate(u: str) -> str:
    """从『给张三安排面试』里抽出『张三』。"""
    m = re.search(r"(?:给|为)\s*([^\s，,。、]{1,6}?)\s*(?:安排|约|预约|定|发)", u)
    if m:
        return m.group(1)
    m = re.search(r"([^\s，,。、]{1,6}?)\s*(?:的)?\s*(?:面试|简历)", u)
    return m.group(1) if m else "候选人"


def _extract_when(u: str) -> str:
    """抽出面试时间，抽不到就给个默认值。"""
    for kw in ("明天下午", "明天上午", "明天晚上", "后天下午", "明天", "后天", "下周一", "下周"):
        if kw in u:
            return kw
    return "明天下午"


def build_llm(mode: str = "stub", **kwargs: Any) -> BaseLLM:
    """工厂方法：mode = stub | deepseek"""
    if mode == "stub":
        return StubLLM()
    if mode == "deepseek":
        return DeepSeekLLM(**kwargs)
    raise ValueError(f"未知 LLM 模式: {mode}")
