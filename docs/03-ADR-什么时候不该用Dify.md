# ADR-003：什么时候不该用 Dify（以及什么时候该用）

| 项 | 内容 |
|---|---|
| 状态 | **已接受（Accepted）**——定性结论已基于代码级对比 + Dify 云端实测成立；定量 head-to-head 已跑通（可比维度 B 2/2 + D 5/6；A/C 共 16 条设计不可比不计入通过率）|
| 决策人 | 程枢（本项目引擎作者） |
| 时间 | 2026-10-05 |
| 关联 | `docs/00-PRD-受控Agent运行时.md`（FR-003/FR-007 高风险门控、FR-002 Trace）、`docs/04-Dify对照工作流搭建说明.md`、`evals/dify_adapter.py` |

---

## 1. 背景（为什么要有这篇 ADR）

M5 设立一条**对照轨**：在 Dify 云端版（dify.ai）照着自研 harness 的 9 个工具、同一套提示词，搭一个等价的 HR 招聘 Agent，跑**同一套 24 条评测集**，看两条轨道差异在哪。

设立对照轨的目的不是"证明 Dify 不行"——Dify 是成熟的编排平台，能极大缩短从 0 到 1 的时间。目的是**证明选型判断力**：在什么约束下自研 harness 是更对的那个选择，在什么约束下根本不该自己造轮子。

本 ADR 是 M5 的落点文档。结论分两层：**什么时候不该用 Dify（用自研）** 和 **什么时候该用 Dify（别自己造）**。

---

## 2. 决策（一句话）

> 当需求存在**高风险动作需要代码级强制拦截**、**需要 span 级可回放定位故障**、或**工具是自研代码而非现成 HTTP 服务**这三类约束中的任意一种时，Dify 的"软管控 + 会话级观测"模型会暴露短板，**应来自研受控 harness**；否则用 Dify 更快、更稳、更省。

---

## 3. 论据与证据

### 论据一：控制强度 —— mandatory（代码强制）vs advisory（提示词建议）

**自研 harness（mandatory）**

门控写在 `harness/runner.py` 的框架主循环里，**不依赖模型自觉**：

```python
# harness/runner.py L186-202
if step.action in (ACTION_CALL_TOOL, ACTION_REQUEST_CONFIRM):
    spec = self.registry.get(step.tool)
    # 门控：高风险一律拦下（FR-003）
    if spec.risk == RISK_HIGH and not self.auto_confirm:
        draft = self._dry_run(spec.name, step.args, tracer, root.span_id)  # 先出草稿，零副作用
        session.set_pending(spec.name, step.args)
        session.transition(S_AWAITING_CONFIRMATION, reason="高风险动作待人工确认")
        return TurnResult(status=STATUS_AWAITING_CONFIRMATION, ...)
```

关键性质：

- 拦截发生在**框架层**，不是提示词层。即使模型直接输出 `CALL_TOOL send_email`，runner 也因 `risk == RISK_HIGH` 拦下转待确认，并先调 `_dry_run(confirmed=False)` 出草稿，**确认前零副作用**（评测侧断言 `no_side_effect=True`）。
- **删掉提示词也拦得住**。这是"受控"二字的落点，可用评测验证"模型想违规也违规不了"。

**Dify（advisory，实测）**

M5 在 dify.ai 搭 `HRAH-Dify-Control` 时的两次实测：

- **踩坑3（已确认）**：翻遍 Agent 配置（工具菜单、悬停、高级设置），**没有"人工确认 / 工具调用前确认"开关**。该版 Dify Agent 无原生"工具调用前代码级拦截"。
- **踩坑4（已确认）**：输入"发邮件通知候选人面试时间"，Dify Agent 确实**暂停并逐项追问收件人/岗位/时间/形式**，并声明"发送邮件属高风险，未经确认不发出"——但这个"安全"完全由**提示词让模型自觉**驱动。把提示词删了、或换一个没那么听话的模型，Dify 会直接把邮件发出去。
- **补充发现（shell_run，实测）**：用 `scripts/_dify_probe.py` 发同一条 query 时，Dify Agent 除了我们显式添加的 `send_email`，还自行调用了**内置工具 `shell_run`**（在 Dify 沙箱里执行了一段探查脚本，`observation` 回的是沙箱元数据）。说明 Dify Agent 的**可调工具面不完全由用户显式注册控制**——平台内置工具也在其可调范围内。这进一步坐实"控制面在 Dify 手里（advisory）"而非"在我代码里（mandatory）"。

**结论（精确表述，不夸大）**：不是"Dify 没安全"，而是 Dify 在这条轨道上提供的是 **advisory（建议性）控制**——安全靠提示词 + 模型自觉，且可调工具面含平台内置项；harness 提供的是 **mandatory（强制性）控制**——安全靠代码，删提示词也生效，且工具面完全由 `ToolRegistry` 显式注册。对发邮件 / 改生产数据 / 转账这类**不可逆高风险动作**，mandatory 是更稳的选择。

---

### 论据二：可观测性 —— span 级（可回放）vs 会话级（仅摘要）

**自研 harness（span 级）**

`harness/tracer.py` 每次运行一个 `trace_id`，span 类型覆盖 `run / plan / llm / tool_call / gate / respond`，每个 span 记输入、输出、耗时、token、状态。`summary()` 直接给可度量信号：

```python
# harness/tracer.py L155-168
"span_count": len(self.spans),
"tool_calls": sum(1 for s in self.spans if s.type == SPAN_TOOL),
"gate_count": sum(1 for s in self.spans if s.type == SPAN_GATE),
"failed_spans": sum(1 for s in self.spans if s.status == "failed"),
"repaired_spans": sum(1 for s in self.spans if s.status == "repaired"),
```

失败能定位到"哪一步、哪个工具、入参出参是什么"。这正是 PRD 北极星指标**失败可定位率**的来源，也是 M4 能靠 Trace 把 BUG-007（tracer 遮蔽内置 `type`）从 SSL EOF 的表象里挖出来的原因。

**Dify（流式事件，实测校正）**

- **踩坑5（实测）**：打开 Dify 的「日志 / 监控」页面，用户报告"日志显示暂无日志"；即便有数据，日志表头列是**会话级**字段：标题 / 来源 / 终端用户 / 消息数 / 用户评分 / 操作率 / 更新时间 / 创建时间——**没有工具调用明细列、没有 token 列、没有 span 树列、没有门控次数列**。
- **校正（2026-10-05 实测，重要）**：此前版本以为 Dify "完全看不到工具调用"。实测用 `scripts/_dify_probe.py` 跑 streaming 后发现：**Dify Agent 的 streaming 响应会在 `agent_thought` 事件里带 `tool` / `tool_input` / `observation`**——也就是说"调了哪个工具、观察结果是什么"在**流式传输时是可见的**。这一点比最初设想好，适配器已据此从 `agent_thought` 抽取 `tools_called`（见 `evals/dify_adapter.py` 的 `_read_stream` / `_normalize`）。
- **但** Dify 暴露的工具调用是**一次性流式事件**，不是**持久化、可查询、带类型的 span 树**：
  1. 没有 `gate_count` 概念——"门控有没有触发"无从断言；
  2. 没有 span 类型（run/plan/llm/tool/gate…）——失败只能看整段对话，定位不到"哪一步、哪个工具、入参出参"；
  3. 不建模 `awaiting_confirmation` / `awaiting_input` / 副作用（scheduled/sent）——控制流信号缺失；
  4. 日志页只落**会话级**摘要（踩坑5），流式事件不进可回放日志。
- 适配器侧印证：`_normalize()` 现在**会**从 `agent_thought` 抽 `tools_called`，但 `gate_count=0` / `span_count=1` / `scheduled=[]` / `sent=[]` 仍无法从 Dify 取得——这正是 C 类在 Dify 侧标记「不可比（设计）」、不计入通过率的根因（`evals/dify_adapter.py` 的 `_dify_incomparable`）。

**结论（精确表述，不夸大）**：不是"Dify 看不到工具调用"，而是 Dify 给的是**传输时的流式事件**（工具可见、但无类型、无门控、无状态机、不可回放）；harness 给的是**持久化、带类型、可回放的 span 树**（含 `gate_count` / `failed` / `repaired`）。当故障需要"定位到具体一次工具调用为什么会失败"时，流式事件不够用——尤其 `gate_count` 这类控制流断言完全无法从 Dify 取得。

---

### 论据三：工具接入模型 —— 自研代码 vs 必须包装成真实 HTTP API

**踩坑2（已确认）**：Dify 点工具面板右上「+」进「创建自定义工具」，该框**只接受 OpenAPI / Swagger schema**，面向"调真实 HTTP API"。**不能直接粘贴一段 mock / 自研代码**。

对策（已跑通）：把 `servers.url` 指向公共回显 `https://httpbin.org`，path `/anything/send_email`，无需自建服务器即把工具接进 Dify。

对比 harness：`harness/tool_registry.py` 的 `ToolRegistry` 接收任意 Python 可调用对象（`spec.call(**args)`），9 个 HR 工具全是本地 mock，`hr_tools.py` 一行 `build_registry()` 即注册，零部署。

**结论**：Dify 接**现成 HTTP 服务**极顺；接**自研逻辑 / mock / 还没暴露成服务的内部能力**要先补一层 API 包装。harness 在这类"工具还在我代码里"的场景下零摩擦。

---

### 论据四（次要）：可回归性 —— 自带评测闭环

harness 每层改动都能用 `scripts/run_evals.py`（24 条 × 3 轮 + baseline 回归）量化回归，M3→M4 就是靠它从 85% 拉到 100% 并自证没过拟合（探针 18/18）。Dify 工作流的"改完还安全吗"只能靠人工回归或另搭评测，没有内建的、可冻结基线的评测闭环。

---

## 4. 定量对比现状（诚实标注未完成部分）

| 维度 | 自研 harness（主轨） | Dify 对照轨 | 状态 |
|---|---|---|---|
| 门控性质 | mandatory，代码强制（删提示词也拦） | advisory，提示词建议 + 平台内置工具面 | 代码级 + 实测确认 |
| 观测粒度 | durable span 树（run/plan/tool/gate…，含 gate_count/failed/repaired） | streaming `agent_thought` 可见 tool/obs，但无 durable span 树、无 gate 信号、无状态机、不可回放 | 代码级 + 实测校正 |
| 工具接入 | 任意 Python 可调用 | 自定义工具须 OpenAPI 真实 HTTP；另有平台内置工具（如 shell_run） | 实测确认 |
| 24 条 head-to-head 通过率 | 88%（live，21/24，D03-05 标 MODEL-QUALITY） | **已跑通**：可比维度 = B 类 2/2 + D 类 5/6（同套 LLM 裁判）；A/C 共 16 条设计不可比（不计入通过率）。Dify 内容质量 5/6 vs harness 3/6，差异来自所选模型/提示词，非引擎 | 🔶 可比维度完成 |

诚实说明：M5 的**定性结论（论据一~三）已基于代码对比 + 云端实测成立**。定量 head-to-head **已跑通**（2026-10-05 用有效 key 实跑 24 条 ×1，D 类补 `DEEPSEEK_API_KEY`）：
- A 类（工具清单）/C 类（门控·契约）共 16 条，因依赖 Dify 不建模的信号（工具清单 / 状态机 / gate / 副作用 / 引擎内部单元）标记「设计不可比」，**不计入通过率**；
- B 类仅 2 条 message 级断言可比（B05 考验"知识缺口下不编造"、B06 考验"用户取消正常收尾"）→ **通过 2/2**；其中 B06 因 Dify 归一化 status 恒为 `done` 而平凡满足，B05 才是有效信号；
- **D 类 6 条用同套 LLM 裁判打分 → 通过 5/6**（仅 D04 邮件草稿未达标）。这是唯一"真模型内容质量"可比维度。

关键对照（同裁判、不同引擎/模型）：

| 维度 | harness 主轨 | Dify 对照轨 |
|---|---|---|
| D 类内容质量（同 LLM 裁判） | 3/6（D03/D04/D05 标 MODEL-QUALITY） | 5/6（仅 D04 未过） |

> 逐条 24 行并排明细（含每条可比性判定与备注）见 `reports/dify_vs_harness.md`。

**怎么读这个数**：Dify 在内容质量上 5/6 略高于 harness 3/6，但这是**所选模型与提示词**的差异（Dify 侧用 `DeepSeek V4.1 Flash` `[待核实]`，harness 用 `deepseek-chat`），**不是引擎能力差异**——harness 的卖点从来不是"写得更漂亮"，而是 mandatory 门控 / span 级可观测 / 自研代码工具。Dify 内容质量更高，反而**强化**了本文结论："低风险内容生成用 Dify 更快更好；高风险可控动作才需自研 harness"。

Dify 对照轨仅搭 1 个自定义工具（send_email）+ 平台内置 shell_run，**未接知识库**——故 B05 考验的是模型"不编造"能力而非检索；与 harness 侧（接 mock KB）非完全同构对比，已如实标注。不把定性结论伪装成定量胜出。

---

## 5. 后果（选错会失去 / 付出什么）

**选 harness（自研）会付出的代价**

- 要自己写引擎、状态机、Trace、门控——工作量远大于在 Dify 拖节点。
- 要自己维护工具注册、上下文裁剪、评测闭环。
- 非高风险、工具都是现成 HTTP 服务、对观测粒度要求不高的场景，自研是**过度工程**。

**选 Dify 会失去的（即 harness 的卖点）**

- 高风险动作失去代码级强制拦截（只剩提示词建议）。
- 故障失去 span 级回放，只能看会话摘要。
- 自研逻辑要先包成 HTTP API 才能接进来。
- 改动失去内建可冻结基线的回归评测。

---

## 6. 被否决的方案

- **方案 A：用 Dify 替代 harness 做主轨**。否决理由：主轨要展示的是"引擎可控性"，而 Dify 的管控是 advisory，无法用评测证明"模型想违规也违规不了"，与项目北极星（可控完成率 / 失败可定位率）冲突。
- **方案 B：两条轨道都不要，纯用 Dify**。否决理由：失去 M5 要证明的"选型判断力"证据，也失去 span 级观测这个面试可讲的设计落点。
- **方案 C：在 Dify 里手写代码节点做门控**。云端版 Agent 模式不支持直接贴代码（踩坑2），需自建 API 服务，成本接近自研且更绕。

---

## 7. 决策规则（给面试官的"选型检查表"）

| 条件 | 选 Dify | 选自研 harness |
|---|---|---|
| 高风险动作需代码级强制拦截 | | ✅ |
| 需要 span 级故障回放 | | ✅ |
| 工具是自研代码 / mock / 未暴露成服务 | | ✅ |
| 要内建可冻结基线的回归评测 | | ✅（已建） |
| 从 0 到 1 要快、工具都是现成 HTTP 服务 | ✅ | |
| 对观测粒度要求不高、团队不会 Python 引擎 | ✅ | |
| 要借平台生态（知识库 / 多模型 / 发布渠道） | ✅ | |

一句话：**Dify 是"快上线"的默认解；当出现"不可逆动作 + 必须拦得住 + 出事要能定位到那一步"时，自研受控 harness 才值回票价。**

---

## 8. 推翻条件（什么情况下本 ADR 要重议）

1. Dify 后续版本在 Agent 模式加了**原生工具调用前确认 / 代码级门控**（API 可断言），则论据一弱化，需重测。
2. Dify 开放**工具调用 / token / span 级**日志 API（非仅会话级），则论据二弱化。
3. 本项目需求从"招聘高风险动作"转向"低风险问答机器人"，mandatory 控制不再是硬约束，则主轨可改回 Dify。
4. 拿到 `DIFY_API_KEY` 跑通 head-to-head 后，若 Dify 定量通过率与 harness 持平且管控可接受，则本 ADR 结论从"不该用"降级为"可平替，按团队熟悉度选"。

---

## 9. 待核实清单（首次联调按实测校正，不当前置结论）

- ✅ **已校正（streaming 暴露工具调用）**：原假设"Dify 完全看不到工具调用"已推翻——`chat-messages` 的 **Agent 应用只支持 streaming**，且 streaming 的 `agent_thought` 事件携带 `tool` / `tool_input` / `observation`。论据二已据实测改写为"流式事件可见 vs durable span 树"。（`[待核实: Dify 官方 API 文档]` 仍建议核对各事件的完整 schema 与字段含义）
- ✅ **已校正（无 mandatory 门控）**：Dify Agent App **只支持 streaming**，streaming 响应里**没有 gate / confirmation 信号字段**；门控纯靠提示词（踩坑4）。论据一从"advisory"坐实为"API 侧无强制门控信号"。
- `[待核实]` Dify 日志页面"暂无日志"是"预览会话不写日志"还是"该版确实不落日志"——不影响会话级结论，但影响能否拿到持久化样本做离线分析。
- `[待核实: DeepSeek 模型名]` Dify 侧选用的 `DeepSeek V4.1 Flash` 是否为 dify.ai 当前可用且支持 Function Calling 的准确名称（搭建时 `gpt-5.2` 因不支持工具调用被标不兼容，已切换）。
- `[待核实]` `shell_run` 这类**平台内置工具**在 Agent 模式下的可调范围与开关项——本 ADR 仅实测到它会被自动调用，其完整清单与禁用方式以 Dify 官方文档为准。
