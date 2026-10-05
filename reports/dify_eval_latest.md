# Dify 对照评测报告

- 生成时间 2026-10-05 17:12:30 ｜ 每条重复 1 轮（final run：`--repeat 1`；harness 主轨为 ×3，稳定性不可直接比较）
- 执行 24 条 ｜ 通过 7 ｜ 未判定 0 ｜ 不可比(设计) 16

## 分类结果

| 类别 | 执行 | 通过 | 未判定 | 不可比 |
|---|---:|---:|---:|---:|
| A · 意图与工具选择 | 6 | 0 | 0 | 6 |
| B · 条件缺口与追问 | 6 | 2 | 0 | 4 |
| C · 门控与契约 | 6 | 0 | 0 | 6 |
| D · 内容质量 | 6 | 5 | 0 | 0 |

> 通过率口径：只算「可比且裁判可用」的条数。**未判定**=Dify API 调用失败或 LLM 裁判不可用（需 DEEPSEEK_API_KEY）；**不可比**=该用例依赖 Dify 不建模的信号（门控/状态机/副作用/工具清单），见 ADR-003 论据一/二，不计入通过率。

## 不可比明细（设计上不可比，不计入通过率）

- A01: 依赖 Dify 不可建模的信号: tools_selected_include
- A02: 依赖 Dify 不可建模的信号: tools_selected_include
- A03: 依赖 Dify 不可建模的信号: tools_selected_include
- A04: 依赖 Dify 不可建模的信号: tools_selected_include
- A05: 依赖 Dify 不可建模的信号: tools_selected_exclude, tools_selected_include
- A06: 依赖 Dify 不可建模的信号: tools_selected_exclude, tools_selected_include
- B01: 依赖状态机状态 awaiting_input，Dify 不建模 awaiting_*
- B02: 依赖状态机状态 awaiting_input，Dify 不建模 awaiting_*
- B03: 依赖状态机状态 awaiting_input，Dify 不建模 awaiting_*
- B04: 依赖状态机状态 awaiting_input，Dify 不建模 awaiting_*
- C01: 依赖 Dify 不可建模的信号: gate_count, pending_tool, no_side_effect
- C02: 依赖 Dify 不可建模的信号: no_side_effect
- C03: 依赖 Dify 不可建模的信号: confirm_then_side_effect, pending_tool
- C04: 依赖 Dify 不可建模的信号: gate_count
- C05: 引擎内部单元断言（契约/规划器），Dify 无对应内部 API
- C06: 引擎内部单元断言（契约/规划器），Dify 无对应内部 API

## 口径与限制（必读）

1. Dify streaming Agent API **会**在 `agent_thought` 暴露 tool/observation（工具调用可见），
   但**没有** durable span 树、gate_count、awaiting_* 状态机、副作用上报。
   依赖后者的 C 类（门控/契约）与 A 类（工具清单）断言在 Dify 侧天然不可比。
2. D 类（内容质量）走同一套 LLM 裁判，是**真正可比**的维度，但需要 DEEPSEEK_API_KEY。
3. 本报告是与 harness 侧做**横向对比**的输入，不是单独给 Dify 打分。最终对比见 ADR-003。