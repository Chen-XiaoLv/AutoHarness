"""Task Hypothesis Agent 测试脚本 — SearchQA 场景

流程：
1. 读取 TASK.md 作为 Task Agent 的 system prompt
2. 读取 discover.jsonl 样本，组装输入
3. 调用 Task Agent 生成 3 个假设（含 executor_instruction）
4. 对每个假设，用 executor_instruction 作为 system prompt，在 probe.jsonl 上评测
5. 用 EM / F1 / sub-EM 评分，打印排行榜并保存结果
"""
import json
import re
import sys
from pathlib import Path

from openai import OpenAI

# ── 路径 ──────────────────────────────────────────────────────
ROOT = Path(__file__).parent
AUTO_DIR = ROOT / "Agent" / "Auto"
TASK_PROMPT_PATH = AUTO_DIR / "TASK.md"
DISCOVER_PATH = ROOT / "data" / "Discover" / "Data" / "discover.jsonl"
PROBE_PATH = ROOT / "data" / "Discover" / "Data" / "probe.jsonl"
OUTPUT_DIR = ROOT / "auto_outputs"

# ── LLM 客户端 ────────────────────────────────────────────────
client = OpenAI(
    api_key="sk-c61yq65rikst7cavlbi29fptekl1xz1fm2u0vo41keibhrjs",
    base_url="https://api.xiaomimimo.com/v1",
    timeout=120.0,
)
MODEL = "mimo-v2.5"
TASK_MODEL = "mimo-v2.5-pro"


def call_llm(system_prompt: str, user_content: str, temperature: float = 0.2, retries: int = 3, model: str = MODEL) -> str:
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=temperature,
            )
            content = resp.choices[0].message.content
            if not content:
                raise ValueError("LLM returned empty response")
            return content.strip()
        except Exception as e:
            if attempt < retries - 1:
                import time
                wait = 5 * (attempt + 1)
                print(f"  [RETRY] LLM call failed ({e}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


# ── 评测指标（复用 Core.Metrics 的逻辑）─────────────────────────
sys.path.insert(0, str(ROOT))
from Core.Metrics import evaluate, extract_answer


# ── 数据加载 ──────────────────────────────────────────────────
def load_jsonl(path: Path) -> list[dict]:
    items = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def safe_json_loads(text: str) -> dict:
    """尝试从 LLM 输出中提取 JSON。"""
    # 先尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试提取第一个 { ... } 块
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法从 LLM 输出中提取 JSON:\n{text[:500]}")


# ── Step 1: Task Agent 生成假设 ──────────────────────────────
def build_task_input(discover_samples: list[dict], prev_hypotheses: list[dict] | None = None) -> str:
    """组装 Task Agent 的 user 输入，匹配 Agent/Auto/TASK.md 中定义的输入格式。"""
    questions = [s["question"] for s in discover_samples]
    answers = [s.get("answer", "") for s in discover_samples]

    payload = {
        "questions": questions,
        "answer": answers,
    }

    if prev_hypotheses:
        payload["hypotheses"] = prev_hypotheses
        payload["score"] = {
            h["id"]: {"em": h.get("actual_em"), "f1": h.get("actual_f1"), "sub_em": h.get("actual_sub_em")}
            for h in prev_hypotheses
        }

    return (
        "注意：你不需要回答这些问题。你的任务是分析这些样本的结构特征，"
        "然后提出3个关于任务本质的竞争假设。请严格按照 TASK.md 中定义的 JSON 格式输出。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def run_task_agent(system_prompt: str, user_input: str) -> dict:
    """调用 Task Agent，返回解析后的 JSON（含 observations 和 hypotheses）。"""
    raw = call_llm(system_prompt, user_input, temperature=0.3, model=TASK_MODEL)
    print(f"  [DEBUG] Raw LLM response ({len(raw)} chars):\n{raw[:800]}\n---")
    result = safe_json_loads(raw)
    # 如果返回的是列表，包装成标准格式
    if isinstance(result, list):
        result = {"observations": [], "hypotheses": result}
    # 尝试从不同 key 结构中提取假设
    hypotheses = result.get("hypotheses", result.get("hypothesis", result.get("assumptions", []))) or []
    if not hypotheses and isinstance(result, dict):
        # 如果 key 不匹配，把整个 dict 当作一条假设处理
        for k, v in result.items():
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                hypotheses = v
                break
    result["hypotheses"] = hypotheses
    print(f"  [DEBUG] Task Agent raw keys: {list(result.keys())}")
    if len(hypotheses) != 3:
        print(f"  [WARN] Task Agent 返回了 {len(hypotheses)} 个假设，期望 3 个")
    return result


# ── Step 2: Tournament Runner ────────────────────────────────
def run_executor(system_prompt: str, question: str) -> str:
    """用给定的 system prompt 作为 Executor，回答单个问题。"""
    return call_llm(system_prompt, question, temperature=0.0)


def run_tournament(
    executor_prompts: dict[str, str],
    probe_samples: list[dict],
) -> dict[str, dict]:
    """在 probe 集上对多组 executor prompt 做 tournament 评测。

    executor_prompts: {label: system_prompt}
    返回: {label: {"em": ..., "f1": ..., "sub_em": ..., "results": [...]}}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    all_scores: dict[str, dict] = {}

    for label, prompt in executor_prompts.items():
        print(f"\n  Evaluating [{label}] on {len(probe_samples)} probe samples...")
        results = []

        def _eval_one(item):
            try:
                pred = run_executor(prompt, item["question"])
                gold_answers = [a.strip() for a in item["answer"].split("####") if a.strip()]
                metrics = evaluate(pred, gold_answers)
                return {
                    "id": item["id"],
                    "question": item["question"],
                    "expected": item["answer"].strip(),
                    "prediction": pred,
                    "predicted_answer": metrics["predicted_answer"],
                    "em": metrics["em"],
                    "f1": metrics["f1"],
                    "sub_em": metrics["sub_em"],
                }
            except Exception as e:
                return {
                    "id": item.get("id"),
                    "question": item.get("question", ""),
                    "expected": item.get("answer", ""),
                    "prediction": "",
                    "predicted_answer": "",
                    "em": 0.0,
                    "f1": 0.0,
                    "sub_em": 0.0,
                    "error": str(e),
                }

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_eval_one, item) for item in probe_samples]
            for future in tqdm(as_completed(futures), total=len(futures), desc=f"  [{label}]"):
                results.append(future.result())

        results.sort(key=lambda x: str(x.get("id", "")))
        n = len(results)
        em_avg = sum(r["em"] for r in results) / n if n else 0
        f1_avg = sum(r["f1"] for r in results) / n if n else 0
        sub_em_avg = sum(r["sub_em"] for r in results) / n if n else 0

        all_scores[label] = {
            "em": round(em_avg * 100, 2),
            "f1": round(f1_avg * 100, 2),
            "sub_em": round(sub_em_avg * 100, 2),
            "n_samples": n,
            "n_errors": sum(1 for r in results if "error" in r),
            "results": results,
        }
        print(f"  [{label}] EM: {em_avg:.2%} | F1: {f1_avg:.4f} | Sub-EM: {sub_em_avg:.2%}")

    return all_scores


# ── Step 3: 排行榜 & 保存 ────────────────────────────────────
def print_leaderboard(scores: dict[str, dict]):
    print(f"\n{'='*60}")
    print("Tournament Leaderboard")
    print(f"{'='*60}")
    print(f"{'Prompt':<20} {'EM':>8} {'F1':>8} {'Sub-EM':>8}")
    print("-" * 52)
    # 按 EM 降序
    for label, s in sorted(scores.items(), key=lambda x: x[1]["em"], reverse=True):
        print(f"{label:<20} {s['em']:>7.1f}% {s['f1']:>7.1f}% {s['sub_em']:>7.1f}%")
    print(f"{'='*60}")


def save_results(
    output_dir: Path,
    task_output: dict,
    scores: dict[str, dict],
):
    """保存假设和评测结果。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 保存 Task Agent 原始输出
    (output_dir / "task_output.json").write_text(
        json.dumps(task_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 保存排行榜
    leaderboard = {}
    for label, s in scores.items():
        leaderboard[label] = {
            "em": s["em"],
            "f1": s["f1"],
            "sub_em": s["sub_em"],
            "n_samples": s["n_samples"],
            "n_errors": s["n_errors"],
        }
    (output_dir / "leaderboard.json").write_text(
        json.dumps(leaderboard, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 保存每组的详细评测结果
    for label, s in scores.items():
        safe_label = label.replace(" ", "_")
        (output_dir / f"eval_{safe_label}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in s["results"]),
            encoding="utf-8",
        )

    print(f"\nResults saved to {output_dir}")


# ── Main ─────────────────────────────────────────────────────
def main(n_discover: int = 5, n_probe: int = 0, prev_results_path: Path | None = None, output_dir: Path | None = None):
    """
    n_discover: 从 discover.jsonl 采样多少条给 Task Agent 观察
    n_probe: 从 probe.jsonl 采样多少条做评测
    prev_results_path: 上一轮结果路径，用于迭代进化
    output_dir: 输出目录，默认 auto_outputs/
    """
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # 加载数据
    print("Loading data...")
    discover_samples = load_jsonl(DISCOVER_PATH)
    probe_samples = load_jsonl(PROBE_PATH)

    # 采样
    if n_discover < len(discover_samples):
        import random
        discover_samples = random.sample(discover_samples, n_discover)
    if n_probe and n_probe < len(probe_samples):
        import random
        probe_samples = random.sample(probe_samples, n_probe)

    print(f"Discover samples: {len(discover_samples)}, Probe samples: {len(probe_samples)}")

    # 加载上一轮结果（如果有）
    prev_hypotheses = None
    if prev_results_path and prev_results_path.exists():
        prev = json.loads(prev_results_path.read_text(encoding="utf-8"))
        prev_hypotheses = []
        for h in prev.get("task_output", {}).get("hypotheses", []):
            label = h.get("id", "?")
            lb = prev.get("leaderboard", {}).get(label, {})
            prev_hypotheses.append({
                "id": label,
                "task_type": h.get("task_type"),
                "executor_instruction": h.get("executor_instruction"),
                "prior_confidence": h.get("prior_confidence"),
                "actual_em": lb.get("em"),
                "actual_f1": lb.get("f1"),
                "actual_sub_em": lb.get("sub_em"),
            })

    # ── Step 1: Task Agent 生成假设 ──
    print("\n" + "="*60)
    print("Step 1: Task Agent 生成假设")
    print("="*60)
    system_prompt = TASK_PROMPT_PATH.read_text(encoding="utf-8").strip()
    user_input = build_task_input(discover_samples, prev_hypotheses)
    task_output = run_task_agent(system_prompt, user_input)

    hypotheses = task_output.get("hypotheses", [])
    observations = task_output.get("observations", [])

    print(f"\nTask Agent 输出了 {len(observations)} 条观察, {len(hypotheses)} 个假设:")
    for h in hypotheses:
        hid = h.get("id", h.get("hypothesis_id", "?"))
        htype = h.get("task_type", h.get("name", "?"))
        conf = h.get("prior_confidence", h.get("confidence", "?"))
        instr = h.get("executor_instruction", h.get("instruction", ""))
        print(f"  {hid}: {htype} (prior_confidence={conf})")
        if instr:
            print(f"    → {instr[:80]}...")

    # 补生成缺失的 executor_instruction
    for h in hypotheses:
        if not h.get("executor_instruction") and not h.get("instruction"):
            hname = h.get("task_type", h.get("name", "unknown"))
            hdesc = h.get("hypothesis", h.get("description", ""))
            print(f"  [INFO] Hypothesis '{hname}' missing executor_instruction, generating...")
            gen_prompt = (
                f"Based on this task hypothesis, write a complete English system prompt "
                f"for an Executor agent that answers trivia questions.\n\n"
                f"Hypothesis: {hname}\nDescription: {hdesc}\n\n"
                f"The prompt should be a complete system prompt that can be directly used. "
                f"Output ONLY the system prompt text, nothing else."
            )
            instr = call_llm("You are a prompt engineer.", gen_prompt, temperature=0.3, model=TASK_MODEL)
            h["executor_instruction"] = instr
            print(f"    → Generated: {instr[:80]}...")

    # ── Step 2: Tournament ──
    print("\n" + "="*60)
    print("Step 2: Tournament Runner")
    print("="*60)

    # Neutral Baseline: 简单直接的 QA 指令
    neutral_prompt = (
        "You are a question answering assistant. "
        "Given a question, provide a concise and accurate answer. "
        "Put your final answer inside <answer>...</answer> tags."
    )

    executor_prompts = {"Neutral": neutral_prompt}
    for h in hypotheses:
        hid = h.get("id", h.get("hypothesis_id", f"H{len(executor_prompts)}"))
        instr = h.get("executor_instruction", h.get("instruction", ""))
        if instr:
            executor_prompts[hid] = instr

    scores = run_tournament(executor_prompts, probe_samples)

    # ── Step 3: 排行榜 & 保存 ──
    print_leaderboard(scores)
    save_results(out_dir, task_output, scores)

    # 找到最佳假设
    best_label = max(scores, key=lambda k: scores[k]["em"])
    print(f"\nBest hypothesis: {best_label} (EM={scores[best_label]['em']}%)")

    # ── Step 4: 用 getContract.md + 获胜假设生成 Contract ──
    print("\n" + "="*60)
    print("Step 4: 生成 Task Contract")
    print("="*60)
    contract_prompt_path = AUTO_DIR / "GetContract.md"
    if not contract_prompt_path.exists():
        print("  [WARN] getContract.md not found, skipping contract generation")
    else:
        # 提取获胜假设的完整 JSON
        best_hypothesis = None
        for h in hypotheses:
            if h.get("id", h.get("hypothesis_id", "")) == best_label:
                best_hypothesis = h
                break
        if best_hypothesis:
            contract_system = contract_prompt_path.read_text(encoding="utf-8").strip()
            contract_user = json.dumps(best_hypothesis, ensure_ascii=False, indent=2)
            print(f"  获胜假设: {best_label} ({best_hypothesis.get('task_type', '?')})")
            print(f"  正在调用 LLM 生成 Contract...")
            contract_md = call_llm(contract_system, contract_user, temperature=0.1, model=TASK_MODEL)

            # 保存 Contract.md
            contract_path = out_dir / "TASK_Contract.md"
            contract_path.write_text(contract_md, encoding="utf-8")
            print(f"  Contract saved to {contract_path}")

            # 保存来源元数据
            contract_meta = {
                "version": "task_v1",
                "source_hypothesis": best_label,
                "status": "provisional",
                "probe_em": scores[best_label]["em"],
                "probe_f1": scores[best_label]["f1"],
                "probe_sub_em": scores[best_label]["sub_em"],
            }
            meta_path = out_dir / "contract_meta.json"
            meta_path.write_text(json.dumps(contract_meta, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  Meta saved to {meta_path}")
        else:
            print("  [ERROR] 未找到获胜假设，跳过 Contract 生成")

    return task_output, scores


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Task Hypothesis Tournament")
    parser.add_argument("--n-discover", type=int, default=5, help="Discover 样本数")
    parser.add_argument("--n-probe", type=int, default=16, help="Probe 评测样本数")
    parser.add_argument("--prev-results", type=str, default=None, help="上一轮结果路径")
    args = parser.parse_args()

    prev_path = Path(args.prev_results) if args.prev_results else None
    main(n_discover=args.n_discover, n_probe=args.n_probe, prev_results_path=prev_path)
