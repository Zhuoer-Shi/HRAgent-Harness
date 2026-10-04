# 上下文预算基准测试

对比 M1（固定注入 top_k=5 个工具 + 历史全量拼接）与
M2（分区预算 + 相关性阈值 + 超预算裁剪 + 历史压缩）的上下文占用。

token 数为粗估值（CJK 按 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token），用于预算控制而非精确计费。

**平均降幅：36.0%**

| 场景 | 历史条数 | M1 工具 | M2 工具 | M1 历史 | M2 历史 | M1 合计 | M2 合计 | 降幅 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 帮我写一份 Java 后端工程师的 JD | 0 | 356 | 179 | 0 | 0 | 356 | 179 | 50% |
| 帮我写一份 Java 后端工程师的 JD | 6 | 356 | 179 | 191 | 177 | 547 | 356 | 35% |
| 帮我写一份 Java 后端工程师的 JD | 12 | 356 | 179 | 383 | 328 | 739 | 507 | 31% |
| 帮我写一份 Java 后端工程师的 JD | 24 | 356 | 179 | 766 | 629 | 1122 | 808 | 28% |
| 帮我筛一下简历 | 0 | 377 | 185 | 0 | 0 | 377 | 185 | 51% |
| 帮我筛一下简历 | 6 | 377 | 185 | 191 | 177 | 568 | 362 | 36% |
| 帮我筛一下简历 | 12 | 377 | 185 | 383 | 328 | 760 | 513 | 32% |
| 帮我筛一下简历 | 24 | 377 | 185 | 766 | 629 | 1143 | 814 | 29% |
| 给张三安排明天下午面试 | 0 | 320 | 128 | 0 | 0 | 320 | 128 | 60% |
| 给张三安排明天下午面试 | 6 | 320 | 128 | 191 | 177 | 511 | 305 | 40% |
| 给张三安排明天下午面试 | 12 | 320 | 128 | 383 | 328 | 703 | 456 | 35% |
| 给张三安排明天下午面试 | 24 | 320 | 128 | 766 | 629 | 1086 | 757 | 30% |
| 发邮件通知候选人面试时间 | 0 | 263 | 164 | 0 | 0 | 263 | 164 | 38% |
| 发邮件通知候选人面试时间 | 6 | 263 | 164 | 191 | 177 | 454 | 341 | 25% |
| 发邮件通知候选人面试时间 | 12 | 263 | 164 | 383 | 328 | 646 | 492 | 24% |
| 发邮件通知候选人面试时间 | 24 | 263 | 164 | 766 | 629 | 1029 | 793 | 23% |
| 帮我查一下年假制度 | 0 | 362 | 169 | 0 | 0 | 362 | 169 | 53% |
| 帮我查一下年假制度 | 6 | 362 | 169 | 191 | 177 | 553 | 346 | 37% |
| 帮我查一下年假制度 | 12 | 362 | 169 | 383 | 328 | 745 | 497 | 33% |
| 帮我查一下年假制度 | 24 | 362 | 169 | 766 | 629 | 1128 | 798 | 29% |

## 工具注入对比（历史 0 条）

- **帮我写一份 Java 后端工程师的 JD**
  - M1 注入 7 个：generate_jd, draft_email, generate_interview_plan, generate_rubric, get_candidate, schedule_interview, send_email
  - M2 注入 3 个：generate_jd, schedule_interview, send_email
- **帮我筛一下简历**
  - M1 注入 7 个：screen_resume, draft_email, generate_interview_plan, generate_jd, generate_rubric, schedule_interview, send_email
  - M2 注入 3 个：screen_resume, schedule_interview, send_email
- **给张三安排明天下午面试**
  - M1 注入 6 个：schedule_interview, draft_email, generate_interview_plan, generate_jd, generate_rubric, send_email
  - M2 注入 2 个：schedule_interview, send_email
- **发邮件通知候选人面试时间**
  - M1 注入 5 个：send_email, schedule_interview, get_candidate, draft_email, generate_interview_plan
  - M2 注入 3 个：send_email, schedule_interview, get_candidate
- **帮我查一下年假制度**
  - M1 注入 7 个：search_knowledge, draft_email, generate_interview_plan, generate_jd, generate_rubric, schedule_interview, send_email
  - M2 注入 3 个：search_knowledge, schedule_interview, send_email

## 长会话表现（历史 24 条）

- **帮我写一份 Java 后端工程师的 JD**：压缩 20 条、丢弃 0 条，历史 token 766 → 629
- **帮我筛一下简历**：压缩 20 条、丢弃 0 条，历史 token 766 → 629
- **给张三安排明天下午面试**：压缩 20 条、丢弃 0 条，历史 token 766 → 629
- **发邮件通知候选人面试时间**：压缩 20 条、丢弃 0 条，历史 token 766 → 629
- **帮我查一下年假制度**：压缩 20 条、丢弃 0 条，历史 token 766 → 629

## 说明

- 高风险工具（`schedule_interview` / `send_email`）在 M2 中受保护：
  即使超出工具分区预算也保留，避免模型因不知道它们存在而臆造能力。
  这是刻意的取舍：**安全优先于预算**。
- 历史分区与本轮执行结果共享额度，执行结果优先（它是当前决策的直接依据）。
- 用例与历史均由本项目自造，用于验证机制是否生效，不代表真实线上分布。
