import json
import yaml
from pathlib import Path
from Core.Agent import Executor, Critic, Evolver, Planner

ROOT = Path(__file__).parent.parent

def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

CONFIG = load_config(ROOT / 'config' / 'config.yaml')

MATCH_MODE_MAP = {
    "gsm8k": "numeric",
    "searchqa": "text",
    "soccer": "text_gen",
}

SKILL_INIT_MAP = {
    "searchqa": ("SKILL.md", "skillopt"),
    "soccer": ("soccer_init.md", "soccer_init"),
}

# 数据集名 → Agent 子目录名映射
DATASET_AGENT_DIR = {
    "gsm8k": "gsm8k",
    "searchqa": "Auto",
    "soccer": "SoccerNet",
}


# ---------------------------------------------------------------------------
# 全局聚合层：Critic minibatch reports → 去重/排序/截断
# ---------------------------------------------------------------------------

def _edit_overlap(a: str, b: str) -> float:
    """两条 edit 文本的 token 重叠率。"""
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


def aggregate_critic_edits(
    reports: list[dict],
    max_total_edits: int = 2,
    rule_ignored_threshold: float = 0.7,
    overlap_threshold: float = 0.6,
) -> list[dict]:
    """对所有 Critic minibatch reports 做全局聚合。

    1. 跳过 rule_ignored 主导的 report（占比 ≥ rule_ignored_threshold）
    2. 对剩余 edits 做语义聚类（token overlap > overlap_threshold 视为同簇）
    3. 每簇保留来自最大 batch 的代表 edit
    4. 按簇大小降序排列，截取 top max_total_edits 条
    """
    valid_edits: list[dict] = []
    for report in reports:
        if "error" in report:
            continue
        summaries = report.get("failure_summary", [])
        total_failures = sum(s.get("count", 0) for s in summaries)
        ignored_count = sum(
            s.get("count", 0) for s in summaries if s.get("failure_type") == "rule_ignored"
        )
        if total_failures > 0 and ignored_count / total_failures >= rule_ignored_threshold:
            continue

        patch = report.get("patch", {})
        for edit in patch.get("edits", []):
            content = edit.get("content", "").strip()
            if content and edit.get("op") == "append":
                valid_edits.append({
                    "content": content,
                    "batch_size": report.get("batch_size", 1),
                    "source_ids": report.get("batch_case_ids", []),
                })

    if not valid_edits:
        return []

    clusters: list[list[int]] = []
    assigned = [False] * len(valid_edits)
    for i, edit_i in enumerate(valid_edits):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(valid_edits)):
            if assigned[j]:
                continue
            if _edit_overlap(edit_i["content"], valid_edits[j]["content"]) > overlap_threshold:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)

    representatives: list[tuple[int, dict]] = []
    for cluster in clusters:
        best_idx = max(cluster, key=lambda idx: valid_edits[idx]["batch_size"])
        representatives.append((len(cluster), valid_edits[best_idx]))

    representatives.sort(key=lambda x: x[0], reverse=True)
    return [edit for _, edit in representatives[:max_total_edits]]


def aggregate_metrics(results: list[dict]) -> dict:
    """汇总多条评估结果，返回指标。自动检测 text_gen 或 QA 模式。"""
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))

    # text_gen 模式
    if results and "rouge_l" in results[0]:
        rl_sum = sum(r.get("rouge_l", 0.0) for r in results)
        b1_sum = sum(r.get("bleu_1", 0.0) for r in results)
        return {
            "total": total,
            "passed": passed,
            "accuracy": passed / total if total else 0.0,
            "rouge_l": rl_sum / total if total else 0.0,
            "bleu_1": b1_sum / total if total else 0.0,
        }

    # QA 模式
    em_sum = sum(r.get("em", 0.0) for r in results)
    f1_sum = sum(r.get("f1", 0.0) for r in results)
    sub_em_sum = sum(r.get("sub_em", 0.0) for r in results)
    return {
        "total": total,
        "passed": passed,
        "accuracy": passed / total if total else 0.0,
        "em": em_sum / total if total else 0.0,
        "f1": f1_sum / total if total else 0.0,
        "sub_em": sub_em_sum / total if total else 0.0,
    }


def init_skill_opt(dataset: str, output_dir: Path = None) -> tuple[Path, str | None]:
    """初始化 SkillOpt 目录，将初始 Skill 复制到 output_dir/SkillOpt/。"""
    if output_dir:
        skill_opt_dir = output_dir / "SkillOpt"
    else:
        skill_opt_dir = ROOT / "SkillOpt" / dataset
    skill_opt_dir.mkdir(parents=True, exist_ok=True)

    agent_dir_name = DATASET_AGENT_DIR.get(dataset, "SearchQA")
    agent_base = ROOT / "Agent" / agent_dir_name

    if dataset in SKILL_INIT_MAP:
        src_name, skill_name = SKILL_INIT_MAP[dataset]
        # 嵌套结构: Agent/{dir}/Executor/Skills/{src_name}
        src_path = agent_base / "Executor" / "Skills" / src_name
        if not src_path.exists():
            # 扁平结构: Agent/{dir}/{src_name}
            src_path = agent_base / src_name
        if src_path.exists():
            content = src_path.read_text(encoding="utf-8")
            (skill_opt_dir / "skillopt.md").write_text(content, encoding="utf-8")
            return skill_opt_dir, skill_name

    return skill_opt_dir, None


def load_few_shots(fewshot_path: Path) -> str:
    """从 fewshot.jsonl 加载 few-shot 样本并格式化为 prompt 文本。"""
    if not fewshot_path or not fewshot_path.exists():
        return ""
    
    examples = []
    with fewshot_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    
    if not examples:
        return ""
    
    lines = []
    for ex in examples:
        question = ex.get("question", "")[:300]
        answer = ex.get("answer", "")
        if isinstance(answer, list):
            answer = answer[0] if answer else ""
        answer = str(answer)[:100]
        category = ex.get("category", "")
        if category:
            lines.append(f"### {category}")
        lines.append(f"Q: {question}")
        lines.append(f"A: <answer>{answer}</answer>\n")
    
    return "\n".join(lines)


def create_agents(dataset: str = None, skill_opt_dir: Path = None, init_skill_name: str = None, instruction_path: Path | None = None, plan_suggestion: str = "", fewshot_path: Path | None = None):
    """根据数据集创建 Agents。instruction_path 可选，用于注入任务指令到 Executor prompt 中。plan_suggestion 可选，用于注入 Planner 建议到 Evolver prompt 中。"""
    ds_cfg = CONFIG.get("dataset", {})
    if not dataset:
        dataset = ds_cfg.get("active", "gsm8k")
    match_mode = MATCH_MODE_MAP.get(dataset, "numeric")
    agent_dir_name = DATASET_AGENT_DIR.get(dataset, "SearchQA")
    agent_base = ROOT / "Agent" / agent_dir_name

    # 自动检测目录结构：嵌套 vs 扁平
    nested_executor = agent_base / "Executor" / "AGENT.md"
    if nested_executor.exists():
        executor_prompt = nested_executor
        critic_prompt = agent_base / "Critic" / "AGENT.md"
        evolver_prompt = agent_base / "Evolver" / "AGENT.md"
        skills_dir = agent_base / "Executor" / "Skills"
    else:
        executor_prompt = agent_base / "EXECUTOR.md"
        critic_prompt = agent_base / "CRITIC.md"
        evolver_prompt = agent_base / "EVOLVER.md"
        # 扁平结构下 skills_dir 设为空（skill 通过 SkillOpt 管理）
        skills_dir = None

    timeout = CONFIG['model'].get('timeout', 60)
    
    # 加载 few-shot 样本
    fewshot_content = ""
    if fewshot_path:
        fewshot_content = load_few_shots(fewshot_path)
    
    executorAgent = Executor(
        model=CONFIG['model']['executor_model'],
        url=CONFIG['model']['base_url'],
        api_key=CONFIG['model']['api_key'],
        prompt=executor_prompt,
        temperature=CONFIG['model']['temperature'],
        match_mode=match_mode,
        skills_dir=skill_opt_dir or skills_dir,
        init_skill_name=init_skill_name,
        instruction_path=instruction_path,
        timeout=timeout,
        fewshot_content=fewshot_content,
    )
    critic_cfg = CONFIG.get("critic", {})
    criticAgent = Critic(
        model=CONFIG['model']['critic_model'],
        url=CONFIG['model']['base_url'],
        api_key=CONFIG['model']['api_key'],
        prompt=critic_prompt,
        temperature=CONFIG['model']['temperature'],
        minibatch_size=critic_cfg.get('minibatch_size', 4),
        edit_budget=critic_cfg.get('edit_budget', 3),
        seed=critic_cfg.get('seed', 42),
        skills_dir=skill_opt_dir or skills_dir,
        timeout=timeout,
    )
    evolverAgent = Evolver(
        model=CONFIG['model']['evolver_model'],
        url=CONFIG['model']['base_url'],
        api_key=CONFIG['model']['api_key'],
        prompt=evolver_prompt,
        temperature=CONFIG['model']['temperature'],
        prompt_path=executor_prompt,
        skills_dir=skill_opt_dir or skills_dir,
        init_skill_name=init_skill_name,
        plan_suggestion=plan_suggestion,
        timeout=timeout,
    )
    return executorAgent, criticAgent, evolverAgent, match_mode


def create_planner(dataset: str = None, instruction_path: Path | None = None) -> Planner:
    """创建 Planner Agent。"""
    planner_prompt = ROOT / "Agent" / "PLAN.md"
    return Planner(
        model=CONFIG['model'].get('planner_model', CONFIG['model'].get('evolver_model', 'mimo-v2.5-pro')),
        url=CONFIG['model']['base_url'],
        api_key=CONFIG['model']['api_key'],
        prompt=planner_prompt,
        temperature=0.0,
        instruction_path=instruction_path,
    )


def build_plan_record(
    round_i: int,
    skill_content: str,
    instruction_content: str,
    train_metrics: dict,
    dev_metrics: dict | None,
    skill_diff: str,
    critic_summary: list[dict],
    aggregated_edits: list[dict],
    evolver_actions: list[dict],
    gate_decision: str,
    gate_reason: str,
    memory_em: float,
    plan_round: int = 1,
    action: str = "",
) -> dict:
    """构建 Planner 的输入记录。"""
    return {
        "plan_round": plan_round,
        "inner_round": round_i,
        "action": action,
        "round": round_i,
        "current_skill": skill_content[:3000],
        "current_instruction": instruction_content[:1000],
        "train_em": round(train_metrics.get("em", 0), 4),
        "train_f1": round(train_metrics.get("f1", 0), 4),
        "dev_em": round(dev_metrics["em"], 4) if dev_metrics else None,
        "dev_f1": round(dev_metrics["f1"], 4) if dev_metrics else None,
        "skill_diff": skill_diff[:2000],
        "critic_summary": critic_summary[:10],
        "aggregated_edits": aggregated_edits[:5],
        "evolver_actions": evolver_actions[:5],
        "gate_decision": gate_decision,
        "gate_reason": gate_reason,
        "memory_em": round(memory_em, 4),
    }


def load_split(dataset: str, split_name: str, fallback_key: str = "test") -> list[dict]:
    """加载数据集的某个 split (train_pool / dev_gate / test_pool)。"""
    ds_cfg = CONFIG.get("dataset", {}).get(dataset, {})
    rel_path = ds_cfg.get(split_name, ds_cfg.get(fallback_key))
    if not rel_path:
        raise ValueError(f"Dataset '{dataset}' has no '{split_name}' or '{fallback_key}' config")
    path = ROOT / rel_path
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def eval_on(executor: Executor, items: list[dict], desc: str = "Eval") -> dict:
    """在给定数据集上评估，返回聚合指标。"""
    results, _ = executor.run(items, desc=desc)
    return aggregate_metrics(results), results
