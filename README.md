# AutoHarness

AutoHarness 是一个面向冻结基座模型的自进化 Harness 框架。

它不对大模型进行权重微调，而是通过多 Agent 协作，自动优化任务理解、System Prompt、Skill 规则、Few-shot 示例和执行策略，使模型能够在给定任务上持续提升表现。

系统核心目标是：在新任务场景下，降低人工 Prompt Engineering 和 Bad Case 分析成本，让 Agent 能够自动完成：

```text
Execute → Evaluate → Reflect → Evolve → Plan
```

即自动执行、自动评估、自动反思、自动进化，并由 Planner 决定下一轮 Harness 应该如何更新。

---

## 项目背景

在真实业务场景中，直接使用通用大模型往往难以获得稳定表现。

对于智能客服、多模态文档解析、搜索问答、推荐系统等任务，开发者通常需要手工设计：

- Prompt
- Few-shot 示例
- Skill / Rule 文档
- 工具调用逻辑
- 上下文组织方式
- Bad Case 分析流程
- 验证与回归测试机制

这个过程通常具有较高的人工成本，并且难以迁移到新任务。

AutoHarness 将这些外部运行环境统一视为可进化的 Harness 组件，并通过自动化闭环持续优化它们。

---

## 核心思想

AutoHarness 遵循：

```text
冻结参数 + 框架自优化
```

系统不会训练或微调基座模型参数，而是优化模型外部的执行框架，包括：

```text
Prompt / Skill / Few-shot / Task Contract / Tool / Context Policy
```

也就是说，AutoHarness 希望让 Agent 不只是“会调用大模型”，而是能够根据任务反馈自动改进自己的运行方式。

---

## 系统能力

AutoHarness 当前支持：

- 冻结参数的大模型 / 多模态模型推理
- 从少量样本中自动探索任务语义
- 自动生成 Task Contract
- Executor–Critic–Evolver 内层自进化闭环
- Planner 驱动的外层决策闭环
- Skill 自动进化
- Few-shot 优化
- Validation Gate 与 Regression Gate
- Memory–Bad Case 动态双池机制
- 候选版本接受 / 拒绝 / 回滚
- 实验日志与版本记录
- Streamlit 可视化 Dashboard

---

## 系统架构

AutoHarness 采用双层闭环结构。

```text
                         ┌────────────────────┐
                         │    Task Agent       │
                         │   任务探索与假设生成  │
                         └─────────┬──────────┘
                                   │
                         ┌─────────▼──────────┐
                         │   Task Contract     │
                         │    弱任务语义锚点    │
                         └─────────┬──────────┘
                                   │
        ┌──────────────────────────▼──────────────────────────┐
        │                    Planner Agent                     │
        │       决定下一步行动：Skill / Few-shot / Prompt / Stop │
        └──────────────────────────┬──────────────────────────┘
                                   │
                ┌──────────────────▼──────────────────┐
                │             内层自进化闭环             │
                │                                      │
                │  ┌──────────┐      ┌──────────┐      │
                │  │ Executor │─────▶│  Critic  │      │
                │  └──────────┘      └────┬─────┘      │
                │                         │            │
                │                    ┌────▼─────┐      │
                │                    │ Evolver  │      │
                │                    └────┬─────┘      │
                │                         │            │
                │                    ┌────▼─────┐      │
                │                    │  Gate    │      │
                │                    └────┬─────┘      │
                │                         │            │
                └─────────────────────────┼────────────┘
                                          │
                              接受 / 拒绝 / 回滚
```

---

## 核心模块

### Task Agent

Task Agent 负责进行任务探索。

它会读取少量 Discovery Set 样本，并生成多个候选任务假设，例如：

- 实体识别
- 事实问答
- 上下文推理
- 模式匹配
- 描述到答案的映射

随后，系统会在 Probe Set 上验证这些假设，并选择表现最好的任务认知作为当前临时任务理解。

---

### Task Contract

Task Contract 是任务层面的弱语义约束。

它不是完整规则库，而是用于防止任务漂移的最小语义锚点。

一个 Task Contract 通常包括：

- Objective
- Input Semantics
- Input–Output Relation
- Evidence Policy
- Output Contract
- Forbidden Behaviors

示例：

```markdown
# Task Contract

## Objective

系统需要根据当前输入中的问题、描述性线索和可用上下文，识别最能回答输入的短答案。

## Output Contract

最终输出必须是简短、精确的答案片段。不要输出推理过程、解释、完整句子、多个候选答案或无关内容。
```

在 AutoHarness 中：

```text
Contract 负责防止任务漂移；
Skill 负责吸收 Bad Case 经验；
Gate 负责验证修改是否真的带来收益。
```

---

### Executor Agent

Executor 负责执行当前任务。

它会读取：

- Task Contract
- Executor Instruction
- 当前 Skill
- 当前 Few-shot 示例
- 输入样本

然后调用大模型生成答案。

默认要求 Executor 将最终答案放在 `<answer>` 标签中，例如：

```text
<answer>Czechoslovakia</answer>
```

---

### Critic Agent

Critic 负责分析 Executor 产生的 Bad Case。

它会读取错误样本，并总结：

- 共性失败类型
- 可能的错误原因
- 是否存在规则冲突
- 是否存在任务理解偏差
- 可执行的修改建议

Critic 通常以 MiniBatch 的方式处理 Bad Case，每个 MiniBatch 会生成一个局部失败总结和修改建议。

示例输出：

```json
{
  "failure_type": "answer_format_error",
  "description": "模型输出了额外解释，而不是最短精确答案。",
  "patch": {
    "edits": [
      "回答时只输出问题直接要求的核心实体或术语，不要添加解释或描述。"
    ]
  }
}
```

---

### Evolver Agent

Evolver 负责将 Critic 的反馈转化为候选 Harness 修改。

当前主要支持：

- Skill 更新
- Prompt Patch
- Few-shot 建议
- Executor Instruction 调整

Evolver 在生成候选修改时必须遵守 Task Contract。  
任何与任务约束冲突的修改都应被拒绝或重新生成。

---

### Gate

Gate 是系统的质量控制模块。

它会在验证集和回归集上对比当前版本与候选版本。

候选版本只有在验证集上取得收益，并且没有明显破坏历史正确样本时，才会被接受。

示例接受规则：

```python
accept = (
    candidate_dev_em >= current_dev_em + 0.01
    and candidate_reg_em >= current_reg_em - 0.005
)
```

Gate 主要用于防止：

- 过拟合当前 Bad Case
- 破坏历史正确能力
- Skill 退化
- Prompt 修改引发任务漂移
- 由于 API 波动导致的伪提升

---

### Planner Agent

Planner 是外层闭环的决策模块。

每一轮 rollout 结束后，Planner 会读取结构化结果，包括：

- 当前 Instruction
- 当前 Skill
- 当前轮 Train / Dev 指标
- 上一轮指标
- Critic 总结
- 聚合后的 Edits
- Evolver 动作
- Gate 接受 / 拒绝结果
- Gate 拒绝原因
- Memory / Regression 状态

然后 Planner 决定下一轮应该采取什么行动。

当前支持的 Planner Action：

| Action | 说明 |
|---|---|
| `CONTINUE_SKILL_EVOLUTION` | 当失败模式具有共性，且适合通过通用 Skill 修复时使用 |
| `ADD_OR_UPDATE_FEWSHOT` | 当失败主要来自输入输出映射理解，抽象规则难以稳定表达时使用 |
| `RERUN_EVALUATION` | 当指标波动较大、Gate 判断不稳定或证据不足时使用 |
| `STOP` | 当连续多轮收益有限，或达到迭代预算时停止 |

Planner 使 AutoHarness 不再是固定的 Skill 优化流程，而是能够根据证据选择下一轮 Harness 进化方向。

---

## 工作流程

### Phase 1：任务探索

系统会从训练池中采样少量样本，构造：

- Discovery Set
- Probe Set

Task Agent 根据 Discovery Set 生成多个任务假设。  
每个假设都会被转化为 Executor Instruction，并在相同 Probe Set 上验证。

最终选择得分最高的假设生成 Task Contract。

```text
Discovery Set → Task Hypotheses → Probe Evaluation → Best Hypothesis → Task Contract
```

---

### Phase 2：Baseline 评估

系统使用初始 Prompt、Task Contract 和空 Skill 运行 Executor，得到初始基线分数。

```text
Initial Prompt + Contract → Executor → Baseline Metrics
```

---

### Phase 3：内层 Skill Evolution

内层闭环流程如下：

```text
Executor → Bad Cases → Critic → Evolver → Candidate Skill → Gate
```

具体步骤：

1. Executor 在训练子集上执行任务。
2. 系统收集错误样本，组成 Bad Set。
3. Critic 对 Bad Set 进行 MiniBatch 分析。
4. 系统聚合、去重、聚类 Critic 生成的 edits。
5. Evolver 生成候选 Skill。
6. Gate 在 Dev Set 和 Regression Set 上验证候选 Skill。
7. 如果通过 Gate，则接受候选 Skill。
8. 如果未通过 Gate，则拒绝候选版本并回滚。

---

### Phase 4：Planner 决策

每轮内层进化结束后，Planner 会读取 rollout summary，并决定下一轮行为。

示例：

```json
{
  "next_action": "ADD_OR_UPDATE_FEWSHOT",
  "reason": "当前 Skill 更新被 Gate 拒绝，说明抽象规则未带来稳定收益。错误主要集中在答案精确性与输入输出映射，适合通过 few-shot 示例进行强化。",
  "risk": "few-shot 示例可能引入局部偏差，导致模型过拟合特定题型。",
  "suggestion": "从高频 Bad Case 聚类中选择代表性样本，构造简短、精确的输入输出示例。"
}
```

Router 会根据 Planner 的决策执行下一步操作。

---

### Phase 5：最终测试

当达到迭代预算，或 Planner 输出 `STOP` 后，系统会在 Test Set 上评估最终版本。

---

## 关键概念

### Skill

Skill 是系统自动学习到的外部行为规则文档。

它记录从 Bad Case 中总结出的可复用任务经验。

示例：

```markdown
# Question Answering Skill

## Learned Rules

1. 只输出最短、最直接的答案。
2. 不要输出解释、推理过程或完整句子。
3. 如果问题以描述方式间接指向某个实体，应推理出该实体并输出其名称。
```

Skill 由 Evolver 自动更新，并由 Gate 验证是否接受。

---

### Bad Set

Bad Set 是当前版本回答错误的样本集合。

它是 Critic 进行失败归因和规则总结的主要输入。

---

### Memory Set

Memory Set 保存历史上曾经回答正确的样本。

但 AutoHarness 不将 Memory Set 视为永久正确集合。  
由于真实模型 API 即使在 `temperature=0` 时仍可能存在非确定性，历史正确样本在后续重跑时也可能答错。

因此，AutoHarness 使用动态 Memory–Bad Case 双池机制：

- 当前仍回答正确的样本保留在 Memory Set
- 重新答错的样本转回 Bad Set
- 新做对的样本可以加入 Memory Set

该机制同时承担经验回放和回归检测作用。

---

### Regression Gate

Regression Gate 用于防止候选修改破坏已有能力。

它会从 Memory Set 中采样样本，对候选版本进行回归验证。

即使候选版本修复了部分 Bad Case，只要它明显破坏历史正确样本，也会被拒绝。

---

### Few-shot Optimization

当 Skill 进化进入饱和，或抽象规则难以稳定表达某类输入输出映射时，Planner 可以选择 `ADD_OR_UPDATE_FEWSHOT`。

Few-shot 示例通常从代表性 Bad Case 聚类中选择。

好的 few-shot 示例应满足：

- 来自高频失败类型
- 不与 Task Contract 冲突
- 覆盖关键输入输出模式
- 不与已有 few-shot 重复
- 能在 Gate 验证中带来稳定收益

---

## 实验结果

### SearchQA

AutoHarness 已在 SearchQA 风格的短答案任务上完成验证。

在该任务中，模型需要根据描述性问题或搜索片段推理出目标答案，并输出简短精确的答案。

示例：

```text
Input: In 1993 this country split into Slovakia & the Czech Republic
Output: Czechoslovakia
```

阶段性结果如下：

| Setting | EM | F1 | Sub-EM |
|---|---:|---:|---:|
| 初始 Skill | 50.7% | 58.3% | 61.4% |
| 进化后 Skill | 62.3% | 69.9% | 72.6% |
| 人工 Skill | 63.2% | 71.3% | 75.8% |
| SkillOpt 完整训练设定 | 68.8% | 77.6% | 80.9% |

结果表明，在不微调模型参数的前提下，AutoHarness 能够仅通过外部 Harness 进化带来显著性能提升。

---

## 支持的数据集

| Dataset | 状态 | 任务类型 |
|---|---|---|
| `searchqa` | 已支持 | 短答案 / 实体识别 / 描述到答案 |
| `soccernet` | 实验支持 | 多模态 / 体育视频理解 |
| `fever` | 计划支持 | 事实验证 |

---

## 快速开始

### 环境要求

- Python 3.12+
- OpenAI-compatible API
- 推荐使用虚拟环境

可支持的模型接口包括：

- OpenAI-compatible endpoint
- DeepSeek
- Qwen / DashScope-compatible endpoint
- 其他自定义 LLM / VLM API

---

### 安装

```bash
git clone https://github.com/Chen-XiaoLv/AutoHarness.git
cd AutoHarness

pip install -r requirements.txt
```

---

### 配置环境变量

复制环境变量模板：

```bash
cp .env.example .env
```

填写 API Key：

```env
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com

OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
```

只需要配置实际使用的模型提供方即可。

---

### 配置模型与路径

编辑：

```text
config/config.yaml
```

示例：

```yaml
model:
  fast: "deepseek-chat"
  strong: "deepseek-reasoner"

paths:
  agent_dir: "Agent/Auto"
  out_dir: "outputs"
  data_dir: "data"

evolution:
  n_rounds: 1
  max_total_edits: 3
  dev_decay_factor: 0.85
```

---

## 运行

### 运行完整流程

```bash
python AutoHarness.py --dataset searchqa
```

### 跳过外层探索和 AB 测试，仅运行 Planner Loop

```bash
python AutoHarness.py --skip-outer --skip-ab --max-plan-rounds 20 --dataset searchqa
```

### 将日志保存到文件

```bash
python AutoHarness.py --skip-outer --skip-ab --max-plan-rounds 20 --dataset searchqa > auto_run.log 2>&1
```

### 运行原始 Rollout

```bash
python rollout.py --rounds 20 --dataset searchqa
```

### 启动可视化 Dashboard

```bash
python -m streamlit run dashboard/app.py --server.port 8501
```

---

## 项目结构

```text
AutoHarness/
├── AutoHarness.py
├── rollout.py
├── rollout_vision.py
├── few_shot_opt.py
├── test_plan.py
│
├── Core/
│   ├── func.py
│   ├── agents.py
│   └── prompt_loader.py
│
├── Agent/
│   └── Auto/
│       ├── EXECUTOR.md
│       ├── EVOLVER.md
│       ├── PLANNER.md
│       └── CRITIC.md
│
├── config/
│   └── config.yaml
│
├── data/
│   └── {dataset}/
│       ├── train.jsonl
│       ├── dev.jsonl
│       └── test.jsonl
│
├── dashboard/
│   └── app.py
│
├── outputs/
│   └── {timestamp}/
│       ├── SkillOpt/
│       │   └── skillopt.md
│       ├── prompt.md
│       ├── auto_harness.log
│       ├── Critic/
│       ├── Evolver/
│       ├── Candidates/
│       ├── Gate/
│       ├── Plan/
│       └── Memory/
│
├── requirements.txt
└── README.md
```

---

## 输出目录说明

每次运行都会生成一个带时间戳的输出目录。

示例：

```text
outputs/2026-06-26_12-00-00/
```

目录内容包括：

| 目录 / 文件 | 说明 |
|---|---|
| `SkillOpt/skillopt.md` | 当前进化得到的 Skill |
| `prompt.md` | 当前 Prompt 快照 |
| `auto_harness.log` | 主运行日志 |
| `Critic/` | Critic 分析报告 |
| `Evolver/` | Evolver 更新记录 |
| `Candidates/` | 候选 Skill 文件 |
| `Gate/` | Gate 验证记录 |
| `Plan/` | Planner 决策历史 |
| `Memory/` | Memory Set 快照 |

---

## 配置说明

### 模型配置

```yaml
model:
  fast: "deepseek-chat"
  strong: "deepseek-reasoner"
```

其中：

- `fast` 通常用于 Executor
- `strong` 通常用于 Critic、Evolver 和 Planner

---

### 进化配置

```yaml
evolution:
  n_rounds: 1
  max_total_edits: 3
  dev_decay_factor: 0.85
```

| 字段 | 说明 |
|---|---|
| `n_rounds` | 每个进化周期中的内层迭代轮数 |
| `max_total_edits` | 每轮最多接受的 Skill 修改数量 |
| `dev_decay_factor` | Gate 阈值衰减系数 |

---

## 设计原则

### 1. 冻结基座模型

AutoHarness 不更新模型权重。

所有性能提升都来自 Harness 层面的优化：

- Prompt
- Skill
- Few-shot
- Task Contract
- Context Policy
- Tool / Function

---

### 2. Contract 防止任务漂移

Task Contract 只提供弱语义约束。

它不应该变成过强的规则库，否则会限制模型能力，并压缩后续 Skill / Few-shot / Prompt 的进化空间。

---

### 3. Skill 吸收 Bad Case 经验

Skill 用于记录从 Bad Case 中总结出的可复用规则。

它应当简洁、通用，并且必须经过 Gate 验证。

---

### 4. Gate 控制退化风险

候选修改不仅要带来收益，还要保证安全。

AutoHarness 更偏好稳定的真实提升，而不是单次评估中的偶然分数波动。

---

### 5. Planner 决定下一步优化方向

并不是所有问题都应该通过 Skill 修复。

部分失败更适合通过以下方式解决：

- Few-shot 示例
- Prompt 修改
- Context Policy 调整
- Tool / Function 生成
- 重新评估

Planner 根据证据决定下一轮要优化哪个 Harness 组件。

---

## Roadmap

- [x] Executor / Critic / Evolver 内层闭环
- [x] Skill 自动进化
- [x] Gate 候选版本接受机制
- [x] Memory–Bad Case 动态双池
- [x] Task Discovery
- [x] Task Contract 自动生成
- [x] Planner 决策闭环
- [x] Streamlit Dashboard
- [x] SearchQA 支持
- [x] 视觉模型接口接入
- [ ] 更稳定的 paired evaluation
- [ ] Case-level fixed / regressed 分析
- [ ] Few-shot Evolution 接入统一闭环
- [ ] Prompt Patch Evolution
- [ ] Context Policy Evolution
- [ ] Tool / Function 自动生成
- [ ] 更多多模态 Benchmark 支持

---

## 相关方向

AutoHarness 受到以下自进化 Agent 和 Harness 优化方向启发：

- SkillOpt
- Self-Harness
- SkillOS
- SkillAdaptor
- Memento-Skill
- Agentic Systems as Boosting Weak Reasoning Models

---

## License

MIT
