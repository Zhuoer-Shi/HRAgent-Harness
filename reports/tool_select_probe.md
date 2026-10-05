# 工具选择探针报告

- 生成时间 2026-10-05 12:05:47
- 探针数 **18** ｜ 通过 **18** ｜ 准确率 **100%**

## 这份报告想证明什么

M4 修改了工具选择策略之后，评测集里 A05 / A06 由红转绿。
这本身不能说明修好了根因——**只要把规则调得刚好能过已知用例也能变绿**。

所以这里用一批**措辞与 `evals/cases.py` 完全不同**的句子重测一遍。
只有当新句子也表现正常时，才能说修的是通用原则而不是记题。

## 结果

| 句子 | 实际注入 | 结果 | 问题 |
|---|---|:---:|---|
| 帮我把这次面试改期 | schedule_interview | ✅ | ok |
| 给王五约个面试，越快越好 | schedule_interview | ✅ | ok |
| 把这封入职通知发出去 | send_email | ✅ | ok |
| 给面试官发一封邮件提醒 | send_email | ✅ | ok |
| 帮我起草邮件的内容 | draft_email | ✅ | ok |
| 发 email 通知候选人下周来面试 | send_email | ✅ | ok |
| 给我出一版 Python 岗位的 JD | generate_jd | ✅ | ok |
| 候选人王五现在进展到哪一步了 | get_candidate | ✅ | ok |
| 帮我看看李四的候选人资料 | get_candidate | ✅ | ok |
| 招聘启事帮我出一版，岗位是数据分析师 | generate_jd | ✅ | ok |
| 我们公司有没有远程办公的规定 | search_knowledge | ✅ | ok |
| 公司差旅报销多久能到账 | search_knowledge | ✅ | ok |
| 年假规定帮我查一下 | search_knowledge | ✅ | ok |
| 给这个岗位定一套评分标准 | generate_rubric | ✅ | ok |
| 这岗位的评分表给我一份 | generate_rubric | ✅ | ok |
| 帮我评估一下这份简历 | screen_resume | ✅ | ok |
| 这份简历请打个分 | screen_resume | ✅ | ok |
| 面试大纲给我列一份，Java 岗的 | generate_interview_plan | ✅ | ok |

## 口径与限制

1. 探针句子同样由本项目自造，**不是真实用户语料**。
   它的作用是**区分「过拟合」与「真修复」**，不能证明真实场景准确率。
2. 加权关键词本质仍是字面匹配，**没有语义理解**。
   典型的失败模式：否定句（「别发邮件」）会被误判为肯定意图。
   这是该方案的已知边界，而非实现缺陷——真正的解法是换成 embedding / LLM 路由，
   那是 M5 与 Dify 对照组要比较的内容。
