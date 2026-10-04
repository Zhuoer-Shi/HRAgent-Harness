# -*- coding: utf-8 -*-
"""
HR 场景工具集（mock 实现）。

说明：本项目的重点是 Agent 运行时，不是 HR 业务本身。
所以工具实现刻意保持简单------返回结构化假数据即可，不接真实系统、不发真实邮件。
这样项目可以脱离数据库、SMTP、向量库独立运行（PRD 2.2）。

高风险工具（schedule_interview / send_email）支持 confirmed 开关：
- confirmed=False 时只返回草稿，不产生任何副作用（FR-007）
- confirmed=True  时才真正"执行"（这里落到内存与文件，不做真实发送）
"""

from __future__ import annotations

from typing import Any, Dict, List

from harness.tool_registry import RISK_HIGH, RISK_LOW, RISK_MEDIUM, ToolRegistry, ToolSpec

# 内存中的"副作用"记录，用于验证高风险动作是否真的被门控拦住
SENT_EMAILS: List[Dict[str, str]] = []
SCHEDULED_INTERVIEWS: List[Dict[str, str]] = []


# ---------------- 低风险工具 ----------------

def generate_jd(role: str, level: str = "中级", city: str = "上海") -> Dict[str, Any]:
    return {
        "title": f"{role}（{level}）",
        "city": city,
        "sections": {
            "岗位职责": [
                f"负责公司核心业务的 {role} 相关设计与开发",
                "参与需求评审，输出技术方案并推动落地",
                "与产品、测试协作，保障交付质量",
            ],
            "任职要求": [
                f"{level}及以上水平，相关经验 3~5 年",
                "具备良好的工程习惯与协作意识",
                "有复杂项目落地经验者优先",
            ],
            "加分项": ["有开源贡献", "有跨端经验"],
        },
        "note": "本内容为 AI 生成初稿，需人工确认后使用",
    }


def generate_rubric(role: str) -> Dict[str, Any]:
    return {
        "role": role,
        "dimensions": [
            {"name": "技术能力", "weight": 35},
            {"name": "项目经验", "weight": 25},
            {"name": "教育背景", "weight": 10},
            {"name": "综合素质", "weight": 20},
            {"name": "岗位匹配", "weight": 10},
        ],
        "note": "权重可由 HR 调整后确认",
    }


def screen_resume(candidate: str, jd: str) -> Dict[str, Any]:
    return {
        "candidate": candidate,
        "total": 78,
        "detail": {
            "技术能力": 27,
            "项目经验": 20,
            "教育背景": 8,
            "综合素质": 15,
            "岗位匹配": 8,
        },
        "reason": f"与 JD 中『{str(jd)[:20]}』的核心要求匹配度较好；项目经历扎实，但缺少跨端经验。",
        "note": "评分仅供参考，录用决定由人做出",
    }


def generate_interview_plan(candidate: str, role: str) -> Dict[str, Any]:
    return {
        "candidate": candidate,
        "role": role,
        "plan": {
            "目标": "验证工程能力与协作意识",
            "流程": ["自我介绍 5min", "项目深挖 20min", "编码/设计 25min", "候选人提问 5min"],
            "核心问题": [
                "介绍一个你主导的复杂项目，遇到过什么取舍？",
                "如何定位一个线上性能问题？",
            ],
            "风险点": "需重点确认项目经历的真实性",
        },
    }


def search_knowledge(query: str) -> Dict[str, Any]:
    return {
        "query": query,
        "hits": [
            {"source": "员工手册.md", "snippet": "年假按司龄计算，满 1 年 5 天，满 3 年 10 天。"},
            {"source": "报销制度.md", "snippet": "差旅报销需在行程结束后 15 个工作日内提交。"},
        ],
        "note": "无依据时应明确说明资料不足，不硬编",
    }


def get_candidate(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "status": "待安排面试",
        "applied_role": "后端工程师",
        "resume_score": 78,
    }


# ---------------- 中风险工具 ----------------

def draft_email(to: str, subject: str, body: str) -> Dict[str, Any]:
    """只出草稿，不发送。"""
    return {"status": "draft", "to": to, "subject": subject, "body": body}


# ---------------- 高风险工具 ----------------

def schedule_interview(candidate: str, when: str, confirmed: bool = False) -> Dict[str, Any]:
    if not confirmed:
        return {
            "status": "draft",
            "preview": f"将为 {candidate} 安排面试，时间：{when}",
            "hint": "这是高风险动作，需人工确认后才会执行",
        }
    SCHEDULED_INTERVIEWS.append({"candidate": candidate, "when": when})
    return {"status": "executed", "candidate": candidate, "when": when}


def send_email(to: str, subject: str, body: str, confirmed: bool = False) -> Dict[str, Any]:
    if not confirmed:
        return {
            "status": "draft",
            "preview": {"to": to, "subject": subject, "body": body},
            "hint": "这是高风险动作，需人工确认后才会发送",
        }
    SENT_EMAILS.append({"to": to, "subject": subject, "body": body})
    return {"status": "sent", "to": to, "subject": subject}


# ---------------- 注册表 ----------------

def build_registry() -> ToolRegistry:
    """构造本项目的工具注册表（PRD 5.1 的 9 个工具）。"""
    reg = ToolRegistry()

    reg.register(ToolSpec(
        name="generate_jd",
        description="根据岗位名称/级别/城市生成 JD 初稿",
        params={"role": "岗位名称", "level": "级别，如中级", "city": "工作城市"},
        required=["role"],
        risk=RISK_LOW,
        returns="JD 结构化初稿",
        keywords=["JD", "jd", "岗位描述", "招聘启事", "写一份"],
        impl=generate_jd,
    ))
    reg.register(ToolSpec(
        name="generate_rubric",
        description="根据岗位生成简历评分标准（维度与权重）",
        params={"role": "岗位名称"},
        required=["role"],
        risk=RISK_LOW,
        returns="评分维度与权重",
        keywords=["评分标准", "评分", "rubric", "维度"],
        impl=generate_rubric,
    ))
    reg.register(ToolSpec(
        name="screen_resume",
        description="结合 JD 与评分标准给候选人简历打分，必须给出理由",
        params={"candidate": "候选人姓名", "jd": "JD 内容或标题"},
        required=["candidate", "jd"],
        risk=RISK_LOW,
        returns="总分 + 维度分 + 理由",
        keywords=["筛", "简历", "评估", "打分", "筛选"],
        impl=screen_resume,
    ))
    reg.register(ToolSpec(
        name="generate_interview_plan",
        description="为候选人生成结构化面试方案",
        params={"candidate": "候选人姓名", "role": "岗位名称"},
        required=["candidate", "role"],
        risk=RISK_LOW,
        returns="面试目标/流程/问题/风险点",
        keywords=["面试方案", "面试问题", "面评"],
        impl=generate_interview_plan,
    ))
    reg.register(ToolSpec(
        name="search_knowledge",
        description="检索公司制度知识库，返回带来源的片段",
        params={"query": "检索问题"},
        required=["query"],
        risk=RISK_LOW,
        returns="命中片段 + 来源",
        keywords=["制度", "规定", "知识", "假期", "报销"],
        impl=search_knowledge,
    ))
    reg.register(ToolSpec(
        name="get_candidate",
        description="查询候选人当前状态与评分",
        params={"name": "候选人姓名"},
        required=["name"],
        risk=RISK_LOW,
        returns="候选人状态",
        keywords=["候选人", "状态", "查询"],
        impl=get_candidate,
    ))
    reg.register(ToolSpec(
        name="draft_email",
        description="生成邮件草稿（只出草稿，不发送）",
        params={"to": "收件人", "subject": "主题", "body": "正文"},
        required=["to", "subject", "body"],
        risk=RISK_MEDIUM,
        returns="邮件草稿",
        keywords=["邮件草稿", "起草"],
        impl=draft_email,
    ))
    reg.register(ToolSpec(
        name="schedule_interview",
        description="安排面试（高风险：会对外产生实际约定，需人工确认）",
        params={"candidate": "候选人姓名", "when": "面试时间", "confirmed": "是否已人工确认"},
        required=["candidate", "when"],
        risk=RISK_HIGH,
        returns="安排结果或草稿预览",
        keywords=["安排面试", "约面试", "面试时间", "预约"],
        impl=schedule_interview,
    ))
    reg.register(ToolSpec(
        name="send_email",
        description="发送邮件（高风险：不可撤销，需人工确认）",
        params={"to": "收件人", "subject": "主题", "body": "正文", "confirmed": "是否已人工确认"},
        required=["to", "subject", "body"],
        risk=RISK_HIGH,
        returns="发送结果或草稿预览",
        keywords=["发邮件", "发通知", "邮件通知", "通知候选人"],
        impl=send_email,
    ))

    return reg
