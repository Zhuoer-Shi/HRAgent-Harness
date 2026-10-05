# HRAgent Harness (HRAH)

一个可观测、可评测的**受控 Agent 运行时**，用 HR 招聘场景验证。

> 一句话：自己写一个 Agent 引擎 + 一套给它打分的评测体系，让 AI 干活"有边界、看得见、测得准"。

## 这不是什么

- 不是一个 HR 系统（没有登录、权限、多租户）
- 不接真实数据库 / 邮件服务 / 向量库
- 尚未完成（当前进度：M4 工具选择策略已重构并通过回归；接 DeepSeek 真模型待 API Key）

## 当前进度

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | PRD | ✅ 完成 |
| M1 | 引擎骨架：工具注册表 / 受控规划器 / Trace | ✅ 完成 |
| M2 | 上下文管理与会话状态机 | ✅ 完成 |
| M3 | 评测体系（24 条用例 + 双裁判 + baseline 回归） | ✅ 完成 |
| M4 | 工具选择策略重构 + 接 DeepSeek 真模型 | 🔶 策略部分已跑通并通过回归；接真模型待 API Key |
| M5 | Dify 对照轨道 + 对比 ADR | ⬜ 待做 |
| M6 | 作品集网站 | ⬜ 待做 |

## 快速开始

```bash
# 零依赖、不联网、不花钱
python demo.py

# 看完整 Trace 时间线
python demo.py --trace

# 看上下文预算账本（M2）
python demo.py --context

# 跑上下文预算基准测试（基线在脚本内冻结复算，不依赖当前实现）
python scripts/context_benchmark.py

# 跑工具选择探针（用一批不在 eval 用例集里的新句子，自证修复不是过拟合）
python scripts/tool_select_probe.py

# 跑评测（Stub 后端，不花钱，跑 13 条引擎层用例）
python scripts/run_evals.py

# 存基线 + 下次回归对比
python scripts/run_evals.py --save-baseline
python scripts/run_evals.py --baseline

# 接真实模型跑全部 24 条（含 LLM 裁判，需要环境变量）
export DEEPSEEK_API_KEY=sk-xxx
python demo.py --live
python scripts/run_evals.py --live
```

`demo.py` 会跑 5 个场景，验证三件事：

1. **低风险任务自动跑完** —— 写 JD
2. **缺信息会追问，不瞎猜** —— 没有 JD 就要筛简历
3. **高风险动作必被拦下** —— 安排面试 / 发邮件只出草稿，确认后才执行

## 目录结构

```
HRAgent-Harness/
├── harness/              # 引擎（本项目本体）
│   ├── tool_registry.py  # 工具注册表：参数契约 + 风险等级 + 强弱信号打分（M4）
│   ├── planner.py        # 受控规划器：动作空间固定为 4 种
│   ├── context.py        # 上下文管理：分区预算 + 裁剪 + 历史压缩（M2）
│   ├── state.py          # 会话状态机：显式转移表，非法转移直接报错（M2）
│   ├── runner.py         # 会话主循环：状态机 + 门控 + 工具执行
│   ├── tracer.py         # Trace：trace_id + span 树，可回放
│   └── llm.py            # LLM 客户端：DeepSeek / Stub
├── evals/                # 评测（给引擎考试的那一层，M3）
│   ├── cases.py          # 24 条用例，四类各 6 条，标注 framework / model 级别
│   ├── judges.py         # 规则裁判 + LLM 裁判 + 单元型断言
│   └── runner.py         # 执行器：重复运行、稳定性、指标、baseline 回归
├── tools/
│   └── hr_tools.py       # HR 场景工具（mock 实现）
├── scripts/
│   ├── context_benchmark.py  # 上下文预算基准测试（旧逻辑基线在脚本内冻结复算）
│   ├── tool_select_probe.py  # 工具选择探针（自证修复不是过拟合）
│   └── run_evals.py          # 评测入口
├── reports/
│   ├── context_benchmark.md  # 基准测试报告
│   ├── tool_select_probe.md  # 探针报告
│   ├── eval_latest.md        # 评测报告（人读）
│   ├── eval_latest.json      # 评测结果（机读，baseline 回归用）
│   └── traces/               # 每次运行的完整 Trace 链路
├── docs/                 # PRD / ADR / 问题与解决记录
└── demo.py
```

## 三个核心设计

### 1. 受控：动作空间固定

规划器只能输出 4 种动作：`CALL_TOOL` / `ASK_USER` / `REQUEST_CONFIRM` / `FINISH`。
任何不在动作空间内的输出都被拒绝，可携带错误信息重试一次，仍失败则记为规划失败。

### 2. 门控在框架层，不靠模型自觉

即使模型直接输出"调用 send_email"，runner 也会因为该工具风险等级为 `high` 把它拦下，
转成待确认状态并先出草稿。**模型想违规也违规不了** —— 这一点可以用评测验证。

### 3. 一切可回放

每次运行生成一个 `trace_id`，span 类型为 `run / plan / llm / tool_call / gate / respond`，
记录输入、输出、耗时、token、状态，可输出人类可读时间线：

```
Trace tr-xxx  （共 5 个 span）
[+] run        run                         12.4ms
[+] llm        planner.decide               8.1ms  320tok
[+] plan       plan[0] CALL_TOOL            0.0ms
[+] gate       gate:schedule_interview      0.1ms
```

### 4. 上下文是一笔有额度的开支（M2）

每次请求按 5 个分区配额度：system 15% / tools 20% / retrieval 25% / history 30% / reserve 10%。
超预算时按固定顺序裁剪；**已被选中（确实相关）的高风险工具不会被裁剪**——安全优先于预算。
每次运行都产出账本，其中含「为什么选中它」的命中说明：

```
上下文预算  342 / 6000 token  （占用 6%）
  system         291 / 900    (32%)
  tools           51 / 1200   (4%)
  history          0 / 1800   (0%)
  注入工具: ['generate_jd']
  工具命中: generate_jd=9 分
```

实测（5 场景 × 4 档历史长度）：上下文 token **平均降幅 52.8%**，
注入工具数 **6.4 → 1.2 个**。
详见 `reports/context_benchmark.md`、`docs/01-ADR-上下文预算与裁剪策略.md`、
`docs/02-ADR-工具选择策略.md`。

> 基线是在 benchmark 脚本里**冻结复算**的，不调用当前实现。
> 让基线依赖被测代码，度量就会跟着代码一起漂移——这个坑见调试日志 `BUG-006`。

### 5. 评测：能被打分，才算真的可控（M3）

24 条用例，四类各 6 条：A 意图与工具选择 / B 条件缺口 / C 门控与契约 / D 内容质量。

三个刻意的设计决定：

1. **用例分 `framework` / `model` 两级**。只依赖引擎确定性行为的用例（门控、契约、Trace、
   工具选择）在 Stub 下就能测；需要真模型语言能力的用例自动跳过，**跳过既不算通过也不算失败**。
   拿 Stub 去测「JD 写得好不好」，测出来的是我自己的规则对不对，那是假指标。
2. **A 类测「上下文注入了哪些工具」，不测「最终调了哪个工具」**——工具选择是引擎的职责，
   不是模型的职责，所以这部分指标在换模型后依然可比。
3. **漏选和误选一起测**。A04 防「该选的没选」；A05/A06 防「不该选的混进来」。
   只测一头，等于把问题从一边推到另一边还自我感觉良好。

当前实测（**Stub 后端**，13 条 × 3 轮；B/D 类需真模型，M4 之后才会真正跑起来）：

| 指标 | 实测 | 目标 | 性质 |
|---|---:|---:|---|
| 契约通过率 | 100% | 100% | 硬指标 |
| 门控触发率 | 100% | 100% | 硬指标 |
| Trace 完整率 | 100% | 100% | 硬指标 |
| 稳定性 | 100% | ≥90% | 软指标 |
| 可控完成率 | 100% | ≥80% | 北极星 |
| 用例通过率 | 100% | — | 参考 |

### 一次完整的优化闭环（M3 → M4）

M3 首次评测时有 2 条红用例，分别指向两个已知缺陷（BUG-005 误注入 / ISSUE-006 过度兜底）。
M4 改工具选择策略后重跑，并与 M3 的基线自动对比：

```
通过率 85% → 100%
新增失败：无
新修好  ：A05、A06
```

**关键在于负向验证也通过了**：C 类（门控与契约）6 条全绿——
说明取消强制注入之后，该拦的照样拦，安全性没有被削弱。

以及一个必须做的自证：改完规则让红用例变绿，本身不能证明修好了根因
（把规则调得刚好能过已知用例，同样能变绿）。所以 `scripts/tool_select_probe.py`
用一批**措辞与 `evals/cases.py` 完全不同**的句子重测，当前 18/18。

目标值为本项目自设，用于观察趋势，**不是行业标准、也不是上线验收标准**。

## 项目性质

个人独立项目，非公司产品，未上生产。设计思路参考了一门 AI 产品落地课程的 HR Agent 案例材料，
代码与评测集为独立编写。课程参考实现在本项目中仅作为对照基线（M5）。
