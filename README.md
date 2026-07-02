# AutoHarness

AutoHarness 是一个自动化的 Prompt 工程与 Skill 进化系统。通过多 Agent 协作（Executor、Evolver、Gate、Critic、Planner），自动优化 LLM 的 System Prompt、Skill 规则和 Few-shot 示例，在给定任务上持续提升模型表现。

## Architecture

```
                    ┌─────────────┐
                    │  Planner    │  决策下一步行动
                    └──────┬──────┘
                           │ CONTINUE / RERUN / FEWSHOT / STOP
                    ┌──────▼──────┐
                    │   Evolver   │  基于 Critic 反馈进化 Skill
                    │   + Gate    │  Gate: 验证候选 Skill 是否优于当前版本
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Critic    │  分析 bad cases，生成改进建议
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  Executor   │  用当前 Prompt + Skill + Few-shot 执行任务
                    └─────────────┘
```

### Core Agents

| Agent | Role |
|-------|------|
| **Executor** | 用当前配置执行推理任务，输出 `<answer>` 标签 |
| **Evolver** | 综合 Critic 报告，生成候选 Skill 更新 |
| **Gate** | 在 dev 集上对比当前 vs 候选 Skill，决定是否接受 |
| **Planner** | 观察历史表现，决策下一轮行动（进化 / 重评估 / Few-shot / 停止） |

### Workflow

1. **Baseline** — 用默认 Prompt 建立基准分数
2. **Phase 3: Inner Evolution Loop** — Executor 执行 → Critic 分析 → Evolver 生成候选 → Gate 验证
3. **Phase 4: Plan Decision Loop** — Planner 根据历史决策下一步，循环执行 Phase 3
4. **Final Test** — 在测试集上评估最终效果

## Quick Start

### Prerequisites

- Python 3.12+
- OpenAI-compatible API (DeepSeek, OpenAI, etc.)

### Install

```bash
pip install -r requirements.txt
```

### Configure

1. Copy `.env.example` to `.env` and fill in your API keys
2. Edit `config/config.yaml` to adjust model, temperature, and paths

### Run

```bash
# Full pipeline
python AutoHarness.py --dataset searchqa

# Skip outer loop and AB testing, 20 plan rounds
python AutoHarness.py --skip-outer --skip-ab --max-plan-rounds 20 --dataset searchqa

# Log output to file
python AutoHarness.py --skip-outer --skip-ab --max-plan-rounds 20 --dataset searchqa > auto_run.log 2>&1
```

## Project Structure

```
AutoHarness/
├── AutoHarness.py          # Main entry point & orchestration
├── rollout.py              # Core evolution loop (Executor → Critic → Evolver → Gate)
├── Core/
│   ├── func.py             # Agent creation, dataset loading, skill management
│   ├── agents.py           # Agent implementations
│   └── prompt_loader.py    # Prompt template loader
├── Agent/
│   └── Auto/
│       ├── EXECUTOR.md     # Executor system prompt template
│       ├── EVOLVER.md      # Evolver system prompt template
│       ├── PLANNER.md      # Planner system prompt template
│       └── CRITIC.md       # Critic system prompt template
├── config/
│   └── config.yaml         # Model, path, and parameter configuration
├── data/                   # Datasets (train, dev, test splits)
│   └── {dataset}/
│       ├── train.jsonl
│       ├── dev.jsonl
│       └── test.jsonl
├── outputs/                # Run outputs (per-run directory)
│   └── {timestamp}/
│       ├── SkillOpt/       # Working skill directory
│       │   └── skillopt.md # Current skill (auto-evolved)
│       ├── prompt.md       # Full prompt snapshots per round
│       ├── auto_harness.log
│       ├── Critic/         # Critic analysis reports
│       ├── Evolver/        # Evolution records
│       ├── Candidates/     # Candidate skill files
│       ├── Gate/           # Gate evaluation records
│       ├── Plan/           # Planner decision history
│       └── Memory/         # Memory set snapshots
└── README.md
```

## Configuration

### config.yaml

```yaml
model:
  fast: "deepseek-chat"              # Fast model for Executor
  strong: "deepseek-reasoner"        # Strong model for Evolver/Planner/Critic

paths:
  agent_dir: "Agent/Auto"            # Agent prompt templates
  out_dir: "outputs"                 # Output directory
  data_dir: "data"                   # Dataset directory

evolution:
  n_rounds: 1                        # Rounds per evolution cycle
  max_total_edits: 3                 # Max edits per round
  dev_decay_factor: 0.85             # Gate threshold decay
```

### Environment Variables (.env)

```
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=your_key_here        # Optional fallback
```

## Key Concepts

### Skill (skillopt.md)

Skill 是系统自动学习到的行为规则，指导 Executor 如何更好地完成任务。例如：

```markdown
## Learned Rules

1. **Answer Concisely**: Output only the most direct and concise answer.
2. **Identify Answer Type**: First identify the answer type, then output the entity directly.
```

Skill 通过 Evolver + Gate 机制自动进化：Evolver 基于 Critic 的 bad case 分析生成候选 Skill，Gate 在 dev 集上验证后决定是否接受。

### Gate Decision

Gate 是质量控制的核心：
- 在 dev 集上对比当前 Skill vs 候选 Skill 的 Exact Match
- 只有当候选 Skill 超过阈值（默认 0.5%，随轮次衰减）时才接受
- 防止 Skill 退化或过拟合

### Few-shot Optimization

当 Skill 进化停滞时，Planner 可能选择优化 Few-shot 示例：
- 从 bad cases 中分类错误模式
- 每个 pattern 选择代表性示例
- 在 dev 集上验证 Few-shot 效果

## Supported Datasets

| Dataset | Task |
|---------|------|
| searchqa | Entity identification from descriptions |
| fever | Fact verification |

## License

MIT
