# M5 · Dify 云端对照工作流搭建说明

> **用途**：在 dify.ai 上搭一个和自研 harness **做同一件事**的 HR 助手，
> 让 `scripts/run_dify_eval.py` 能把同一套 24 条评测用例喂进去，和 harness 横向对比。
>
> **目标不是"复刻 harness"，而是"用现成编排平台做到同样的效果"**——
> 这样才能公平回答 ADR-003《什么时候不该用 Dify》。

---

## 一、搭之前先明确：比什么

harness 侧有 4 个引擎能力，Dify 侧要对标上才公平：

| harness 能力 | Dify 里的对应做法 |
|---|---|
| 9 个 HR 工具（mock 实现） | Dify 的「工具」节点，用代码/API 工具接同样的 mock 逻辑 |
| 高风险动作人工确认（框架层强制门控） | Dify 工具配置里的「人工确认」开关 |
| 知识库检索 | Dify 知识库 + 检索节点 |
| 意图识别 + 规划 | Dify Agent 模式的 LLM 自主选工具（或 Chatflow 的 LLM 节点） |

---

## 二、步骤（在 dify.ai 网页端操作）

### 第 1 步：新建 App
- 类型选 **「智能体 Agent」**（最贴近 harness：LLM 自己决定调哪个工具）。
  - 若你更熟 Chatflow，也可以选 Chatflow + 工具节点，效果类似，二选一即可。
- 名称：`HRAH-Dify-Control`（随便起，能认出就行）。

### 第 2 步：接入 9 个工具
- 打开「工具」→「自定义」→ 用「代码工具」或「API 工具」逐个加。
- **逻辑直接照搬** `harness/tools/hr_tools.py`（9 个工具的入参/出参/风险等级都在里面，这是唯一真相源，别凭记忆写）。
- 重点：**`send_email` 和 `schedule_interview` 标记为高风险**，下一步开人工确认。

### 第 3 步：给高风险工具开「人工确认」
- 在这两个工具的配置里打开 **「人工确认 / Human Confirmation」** 开关。
- ⚠️ **重要诚实提醒（待你实测确认）**：Dify 的「人工确认」在**网页对话界面**里是明确的——
> 但走 **API（我们评测用的 blocking 模式）时，确认环节是否真的拦截、还是会直接执行，
> 取决于 Dify 版本与配置**。这一点首次联调时要专门验证，它就是 ADR 要讲的核心差异之一：
> **harness 的门控是引擎层强制的（代码保证），Dify 的确认是平台/UI 层的（API 下未必强制）。**

### 第 4 步：接知识库（给 `search_knowledge` 用）
- 建一个知识库，上传一份**假制度文档**（写点年假、报销的示例条文即可，别用真公司数据）。
- 在 Agent/工作流里挂上这个知识库作为检索来源。

### 第 5 步：发布 + 拿密钥
- 点「发布」→「访问 API」。
- 复制 **API Key**（形如 `app-xxxx`），云端默认地址 `https://api.dify.ai`（不用改）。
- 本地设环境变量后就能跑评测：
  ```powershell
  # PowerShell
  $env:DIFY_API_KEY="app-你的key"
  $env:DEEPSEEK_API_KEY="sk-你的key"   # D 类内容质量要用同一套 LLM 裁判打分
  python scripts/run_dify_eval.py --category C   # 先小批量联调，省额度
  ```

---

## 三、额度提醒（省着点用）

- Dify 云端免费版对话额度有限。24 条 × 3 轮 = **72 次调用**，可能触限额。
- **建议联调顺序**：
  1. `python scripts/run_dify_eval.py --category C --repeat 1`（6 次，先确认 API 通、key 对）
  2. 再 `--category A --repeat 1`（6 次，看工具选择）
  3. 最后全量 `--repeat 3`
- 跑之前先确认 dify.ai 后台还剩多少额度，别一口气跑满被发现中断。

---

## 四、你搭完告诉我两件事

1. **Dify 工作流搭好了**（App 已发布、9 工具 + 2 高风险确认 + 知识库都接上）。
2. **给我 DIFY_API_KEY**（或你自己本地设好环境变量跑 `run_dify_eval.py` 把报告贴给我）。

拿到后我来做：跑通 Dify 对照评测 → 生成 `reports/dify_eval_latest.md` →
和 harness 的 `reports/eval_latest.md` 横向对比 → 写 **`docs/03-ADR-什么时候不该用Dify.md`**。

---

## 五、如果 Dify 侧「人工确认」在 API 下不强制（很可能）

那对比结论会是：
- **harness 赢**：门控是代码强制（确认前 100% 无副作用，可断言验证）；
  Dify 的确认依赖平台/UI，API 下可能「模型说发就发了」，harness 的评测更可信。
- **Dify 赢**：搭建速度（可视化、半小时 vs 写代码）、上手成本。
- 这不是谁碾压谁，而是**「要可控性就自己写 harness，要快就 Dify」**——ADR 会把边界讲清楚。
