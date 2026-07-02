'''
读取bad case -> 分类；
每类给模型看 <=5 条样本（随机抽样）
让Selector选择最合适few-shot的样本
载入当前的Executor prompt，跑三轮Val对比实验
稳定通过则写入fewshot.jsonl
'''
import json
import random
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).parent

# 从 config 加载配置
import yaml
CONFIG = yaml.safe_load((ROOT / 'config' / 'config.yaml').open('r', encoding='utf-8'))

client = OpenAI(
    api_key=CONFIG['model']['api_key'],
    base_url=CONFIG['model']['base_url'],
    timeout=CONFIG['model'].get('classify_timeout', 300),
)
MODEL = CONFIG['model'].get('evolver_model', 'mimo-v2.5-pro')


def call_llm(system_prompt: str, user_content: str, temperature: float = 0.2) -> str:
    print("\n" + "=" * 80)
    print("[LLM INPUT - System Prompt]")
    print("-" * 40)
    print(system_prompt[:500] + "..." if len(system_prompt) > 500 else system_prompt)
    print("\n[LLM INPUT - User Content]")
    print("-" * 40)
    print(user_content[:1000] + "..." if len(user_content) > 1000 else user_content)
    print("=" * 80)
    
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature,
    )
    content = resp.choices[0].message.content
    if not content:
        raise ValueError("LLM returned empty response")
    
    print("\n" + "=" * 80)
    print("[LLM OUTPUT]")
    print("-" * 40)
    print(content)
    print("=" * 80)
    
    return content.strip()


# ── 分类 Prompt ───────────────────────────────────────────────
CLASSIFY_PROMPT = '''你将看到一组问答系统中的错误样本。每个样本包含：问题、期望答案、模型输出。

你需要根据“模型输出相对于期望答案的错误表现”进行分类。

类别只能从以下六类中选择：

1. task_misunderstanding
模型明显误解了任务目标，输出的内容与期望答案类型或问题意图明显不一致。

2. wrong_granularity
模型大体理解了问题，但答案粒度错误，例如输出类别而不是具体实体，输出过粗、过细、别名不合适。

3. copy_question
模型输出复制了问题内容、问题中的描述片段，或者没有给出真正答案。

4. verbose_answer
模型输出包含正确答案，但加入了多余解释、完整句子、修饰语或无关内容，导致答案过长。

5. format_error
模型答案语义基本正确，但格式不匹配，例如大小写、标点、复数、括号、数字形式等问题。

6. type_or_language_error
模型输出的答案类型或语言错误，例如英文任务输出中文，问人却答地点，问年份却答事件。

分类优先级：
- 如果明显复制问题，优先归为 copy_question。
- 如果答案包含正确内容但太长，优先归为 verbose_answer。
- 如果语义接近但粒度不对，归为 wrong_granularity。
- 如果只是格式差异，归为 format_error。
- 只有当模型明显不知道任务要做什么时，才归为 task_misunderstanding。

请严格返回 JSON，不要输出解释。
JSON key 必须严格使用 sample1、sample2、sample3 这样的编号。
JSON value 必须是上述六个英文类别之一。

输出格式示例：
{
  "sample1": "copy_question",
  "sample2": "wrong_granularity"
}

下面是输入的样本：
'''

# ── Selector Prompt ───────────────────────────────────────────
SELECTOR_PROMPT = '''你是一个Few-shot样本选择器。给定一类错误的多个样本，你需要选择最适合用作few-shot示例的那一个样本。

选择标准：
1. 样本具有代表性，能体现该类错误的典型模式
2. 问题清晰，答案简洁
3. 有助于模型学习正确的回答方式

请从以下候选样本中选择最佳的1个few-shot示例，返回选中的样本ID。

## 输出格式：
直接返回选中的ID，例如：ID_0

下面是候选样本：
'''


def load_bad_cases(run_dir: Path, round_i: int = None) -> list[dict]:
    """加载 bad case 文件。如果指定 round_i 则加载对应轮次，否则加载所有轮次。"""
    executor_dir = run_dir / "Executor"
    bad_cases = []
    
    if round_i is not None:
        bad_path = executor_dir / f"round_{round_i}_bad.jsonl"
        if bad_path.exists():
            with bad_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        bad_cases.append(json.loads(line))
    else:
        # 加载所有轮次的 bad cases
        for bad_path in sorted(executor_dir.glob("round_*_bad.jsonl")):
            with bad_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        bad_cases.append(json.loads(line))
    
    return bad_cases


def classify_bad_cases(bad_cases: list[dict], max_samples: int = None) -> dict[str, list[dict]]:
    """将 bad cases 分类，返回按类别分组的结果。"""
    if not bad_cases:
        return {}
    
    # 采样（max_samples 为 None 则全量）
    if max_samples and max_samples < len(bad_cases):
        sampled = random.sample(bad_cases, max_samples)
    else:
        sampled = bad_cases
    
    # 构建输入
    samples_text = ""
    for i, case in enumerate(sampled):
        question = case.get("question", "")[:200]
        expected = case.get("expected", case.get("answer", ""))
        if isinstance(expected, list):
            expected = expected[0] if expected else ""
        expected = str(expected)[:100]
        pred = case.get("prediction", case.get("pred", ""))[:100]
        samples_text += f"\nsample{i+1}:\n  问题: {question}\n  期望答案: {expected}\n  模型输出: {pred}\n"
    
    # 调用 LLM 分类
    try:
        result = call_llm(CLASSIFY_PROMPT, samples_text)
        print("\n[CLASSIFY RAW RESPONSE]")
        print("-" * 40)
        print(result)
        print("-" * 40)
        
        # 提取 JSON
        if "```json" in result:
            result = result.split("```json")[1].split("```")[0]
        elif "```" in result:
            result = result.split("```")[1].split("```")[0]
        
        print("\n[CLASSIFY EXTRACTED JSON]")
        print("-" * 40)
        print(result.strip())
        print("-" * 40)
        
        classifications = json.loads(result.strip())
        print("\n[CLASSIFY PARSED RESULT]")
        print("-" * 40)
        for k, v in classifications.items():
            print(f"  {k}: {v}")
        print("-" * 40)
    except Exception as e:
        print(f"  [CLASSIFY] Error: {e}")
        # 默认全部归为 task_misunderstanding
        classifications = {f"sample{i+1}": "task_misunderstanding" for i in range(len(sampled))}
    
    # 按类别分组
    grouped = {}
    for i, case in enumerate(sampled):
        key = f"sample{i+1}"
        category = classifications.get(key, "task_misunderstanding")
        if category not in grouped:
            grouped[category] = []
        grouped[category].append(case)
    
    return grouped


def select_few_shots(grouped_cases: dict[str, list[dict]], max_per_class: int = 5) -> dict[str, list[dict]]:
    """为每个类别选择最佳 few-shot 样本（每类随机抽样 ≤5 条，模型选择最好的 1 条）。"""
    selected = {}
    
    for category, cases in grouped_cases.items():
        # 随机抽样 ≤5 条
        sampled = random.sample(cases, min(max_per_class, len(cases)))
        
        if len(sampled) == 1:
            selected[category] = sampled
            continue
        
        # 构建候选样本描述
        candidates_text = ""
        for i, case in enumerate(sampled):
            question = case.get("question", "")[:200]
            expected = case.get("expected", case.get("answer", ""))
            if isinstance(expected, list):
                expected = expected[0] if expected else ""
            expected = str(expected)[:100]
            candidates_text += f"\nID_{i}: 问题: {question}\n  期望答案: {expected}\n"
        
        # 调用 LLM 选择最好的 1 条
        try:
            result = call_llm(SELECTOR_PROMPT, candidates_text)
            # 提取 ID
            result = result.strip()
            if result.startswith("ID_"):
                idx = int(result.replace("ID_", ""))
                if 0 <= idx < len(sampled):
                    selected[category] = [sampled[idx]]
                else:
                    selected[category] = [random.choice(sampled)]
            else:
                selected[category] = [random.choice(sampled)]
        except Exception as e:
            print(f"  [SELECTOR] Error for {category}: {e}")
            selected[category] = [random.choice(sampled)]
    
    return selected


def format_few_shot_prompt(few_shots: dict[str, list[dict]]) -> str:
    """将 few-shot 样本格式化为 prompt 文本。"""
    if not few_shots:
        return ""
    
    lines = []
    for category, cases in few_shots.items():
        for case in cases:
            question = case.get("question", "")[:300]
            expected = case.get("expected", case.get("answer", ""))
            if isinstance(expected, list):
                expected = expected[0] if expected else ""
            expected = str(expected)
            # 只取 #### 前面的部分作为答案
            if "####" in expected:
                expected = expected.split("####")[0].strip()
            expected = expected[:100]
            lines.append(f"Input: \n{question}\n")
            lines.append(f"Output: \n<answer>{expected}</answer>\n")
    
    return "\n".join(lines)


def validate_few_shots(
    run_dir: Path,
    few_shots: dict[str, list[dict]],
    dataset: str = "searchqa",
    n_rounds: int = 3,
    sample_size: int = 21,
) -> tuple[float, float]:
    """验证 few-shot 效果，返回 (baseline_em, fewshot_em)。"""
    from Core.func import create_agents, load_config
    from Core.Metrics import evaluate
    
    # 加载 dev_gate 数据
    ds_cfg = CONFIG.get("dataset", {}).get(dataset, {})
    dev_gate_path = ROOT / ds_cfg.get("dev_gate", "")
    dev_gate = []
    with dev_gate_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                dev_gate.append(json.loads(line))
    
    # 采样
    if sample_size and sample_size < len(dev_gate):
        dev_gate = random.sample(dev_gate, sample_size)
    
    # 加载 instruction_path
    instruct_path = ROOT / "auto_outputs" / "instruct_init.md"
    instruction_path = instruct_path if instruct_path.exists() else None
    if instruction_path:
        print(f"  [VALIDATE] Using instruction: {instruction_path}")
    else:
        print(f"  [VALIDATE] No instruction file found")
    
    # 格式化 few-shot
    fewshot_text = format_few_shot_prompt(few_shots)
    
    # 运行对比实验
    baseline_scores = []
    fewshot_scores = []
    
    for round_i in range(n_rounds):
        print(f"  [VALIDATE] Round {round_i + 1}/{n_rounds}")
        
        # Baseline（无 few-shot）
        executorAgent, _, _, match_mode = create_agents(dataset=dataset, instruction_path=instruction_path)
        
        print("\n" + "=" * 80)
        print("[BASELINE SYSTEM PROMPT]")
        print("-" * 40)
        print(executorAgent.prompt)
        print("=" * 80)
        
        baseline_results, _ = executorAgent.run(dev_gate, desc=f"Baseline R{round_i+1}")
        baseline_em = sum(1 for r in baseline_results if r.get("passed")) / len(baseline_results)
        baseline_scores.append(baseline_em)
        
        # Few-shot（注入 few-shot 到 prompt）
        executorAgent_fs, _, _, _ = create_agents(dataset=dataset, instruction_path=instruction_path)
        # 使用 update 方法注入 few-shot
        executorAgent_fs.update(fewshot_content=fewshot_text)
        
        print("\n" + "=" * 80)
        print("[FEW-SHOT SYSTEM PROMPT]")
        print("-" * 40)
        print(executorAgent_fs.prompt)
        print("=" * 80)
        
        fewshot_results, _ = executorAgent_fs.run(dev_gate, desc=f"Few-shot R{round_i+1}")
        fewshot_em = sum(1 for r in fewshot_results if r.get("passed")) / len(fewshot_results)
        fewshot_scores.append(fewshot_em)
        
        print(f"    Baseline EM: {baseline_em:.2%} | Few-shot EM: {fewshot_em:.2%}")
    
    avg_baseline = sum(baseline_scores) / len(baseline_scores)
    avg_fewshot = sum(fewshot_scores) / len(fewshot_scores)
    
    return avg_baseline, avg_fewshot


def save_few_shots(few_shots: dict[str, list[dict]], output_path: Path):
    """保存 few-shot 样本到 JSONL 文件。"""
    with output_path.open("w", encoding="utf-8") as f:
        for category, cases in few_shots.items():
            for case in cases:
                answer_raw = case.get("expected", case.get("answer", ""))
                if isinstance(answer_raw, list):
                    answer_raw = answer_raw[0] if answer_raw else ""
                answer_raw = str(answer_raw)
                if "####" in answer_raw:
                    answer_raw = answer_raw.split("####")[0].strip()
                record = {
                    "category": category,
                    "question": case.get("question", ""),
                    "answer": answer_raw,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"  [SAVE] Few-shots saved to {output_path}")


def run_few_shot_optimization(
    run_dir: Path,
    dataset: str = "searchqa",
    max_classify_samples: int = None,
    max_per_class: int = 5,
    n_validate_rounds: int = 3,
    improvement_threshold: float = 0.01,
) -> dict | None:
    """运行 few-shot 优化流程。
    
    Args:
        run_dir: 运行输出目录
        dataset: 数据集名称
        max_classify_samples: 分类时最大采样数
        max_per_class: 每类最大 few-shot 数
        n_validate_rounds: 验证轮数
        improvement_threshold: 最小提升阈值
    
    Returns:
        如果通过验证，返回包含 few-shot 信息的字典；否则返回 None
        返回格式: {"selected": dict, "baseline_em": float, "fewshot_em": float, "fewshot_content": str, "categories": list[str]}
    """
    print("\n" + "=" * 60)
    print("Few-shot Optimization")
    print("=" * 60)
    
    # 1. 加载 bad cases
    bad_cases = load_bad_cases(run_dir)
    print(f"[LOAD] Found {len(bad_cases)} bad cases")
    
    if len(bad_cases) < 3:
        print("[SKIP] Too few bad cases for few-shot optimization")
        return None
    
    # 2. 分类
    print("[CLASSIFY] Classifying bad cases...")
    grouped = classify_bad_cases(bad_cases, max_samples=max_classify_samples)
    print(f"[CLASSIFY] Found {len(grouped)} categories:")
    for cat, cases in grouped.items():
        print(f"  {cat}: {len(cases)} cases")
    
    # 3. 选择 few-shot 样本
    print("[SELECT] Selecting few-shot samples...")
    selected = select_few_shots(grouped, max_per_class=max_per_class)
    total_selected = sum(len(cases) for cases in selected.values())
    print(f"[SELECT] Selected {total_selected} samples across {len(selected)} categories")
    
    # 4. 验证（全量 dev_gate）
    print("[VALIDATE] Running comparison experiments...")
    baseline_em, fewshot_em = validate_few_shots(
        run_dir, selected, dataset=dataset, n_rounds=n_validate_rounds, sample_size=None
    )
    
    print(f"\n[RESULT] Baseline EM: {baseline_em:.2%}")
    print(f"[RESULT] Few-shot EM: {fewshot_em:.2%}")
    print(f"[RESULT] Delta: {fewshot_em - baseline_em:+.2%}")
    
    # 5. 判断是否通过
    fewshot_content = format_few_shot_prompt(selected)
    if fewshot_em - baseline_em >= improvement_threshold:
        print(f"[PASS] Improvement >= {improvement_threshold:.2%}, saving few-shots")
        fewshot_path = run_dir / "fewshot.jsonl"
        save_few_shots(selected, fewshot_path)
        return {
            "selected": selected,
            "baseline_em": baseline_em,
            "fewshot_em": fewshot_em,
            "fewshot_content": fewshot_content,
            "categories": list(selected.keys()),
        }
    else:
        print(f"[FAIL] Improvement < {improvement_threshold:.2%}, skipping")
        return None


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Few-shot Optimization")
    parser.add_argument("--run-dir", type=str, required=True, help="Run output directory")
    parser.add_argument("--dataset", type=str, default="searchqa", help="Dataset name")
    parser.add_argument("--max-samples", type=int, default=None, help="Max samples for classification (None = all)")
    parser.add_argument("--max-per-class", type=int, default=5, help="Max few-shots per class")
    parser.add_argument("--n-rounds", type=int, default=3, help="Validation rounds")
    parser.add_argument("--threshold", type=float, default=0.01, help="Improvement threshold")
    
    args = parser.parse_args()
    
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"Error: Run directory not found: {run_dir}")
        exit(1)
    
    result = run_few_shot_optimization(
        run_dir=run_dir,
        dataset=args.dataset,
        max_classify_samples=args.max_samples,
        max_per_class=args.max_per_class,
        n_validate_rounds=args.n_rounds,
        improvement_threshold=args.threshold,
    )
    
    if result:
        print("\nFew-shot optimization completed successfully!")
    else:
        print("\nFew-shot optimization did not produce improvements.")
