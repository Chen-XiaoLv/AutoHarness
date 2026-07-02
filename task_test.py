"""Task Hypothesis Agent 测试脚本 — SearchQA 场景"""
import json
import random
import re
from pathlib import Path
from openai import OpenAI

client = OpenAI(
    api_key="sk-c61yq65rikst7cavlbi29fptekl1xz1fm2u0vo41keibhrjs",
    base_url="https://api.xiaomimimo.com/v1",
    timeout=120.0,
)

ROOT = Path(__file__).parent


def call_llm(system_prompt: str, user_content: str) -> str:
    resp = client.chat.completions.create(
        model="mimo-v2.5",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )
    content = resp.choices[0].message.content
    if not content:
        raise ValueError("LLM returned empty response")
    return content.strip()


def normalize_answer(s: str) -> str:
    """标准化答案：小写、去标点、去多余空格。"""
    s = s.lower().strip()
    s = s.replace(",", "")
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return " ".join(s.split())


def evaluate_qa(expected: str, predicted: str) -> tuple[float, float]:
    """计算 EM 和 F1。"""
    gold = normalize_answer(expected)
    pred = normalize_answer(predicted)
    if not gold and not pred:
        return 1.0, 1.0
    if not gold or not pred:
        return 0.0, 0.0
    em = 1.0 if gold == pred else 0.0
    gold_tokens = gold.split()
    pred_tokens = pred.split()
    common = set(gold_tokens) & set(pred_tokens)
    if not common:
        return em, 0.0
    precision = len(common) / len(pred_tokens)
    recall = len(common) / len(gold_tokens)
    f1 = 2 * precision * recall / (precision + recall)
    return em, f1


def load_samples(path: Path, n: int = 5) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    return lines[:n]


def load_samples_random(path: Path, n: int = 50) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    rng = random.Random(42)
    return rng.sample(lines, min(n, len(lines)))


def build_user_input() -> str:
    """构造 TASK Agent 的输入（人类提供数据路径+目标，自动探索数据）"""
    data_path = ROOT / "data" / "searchqa" / "train_pool.jsonl"
    samples = load_samples_random(data_path, n=50)

    input_info = {
        "business_goal": "根据给定的问题，从问题文本中提取关键信息并推理出正确答案",
        "dataset_path": "data/searchqa/train_pool.jsonl",
        "field_schema": {
            "id": "string, 唯一标识符",
            "question": "string, 问题文本",
            "answer": "string, 正确答案",
            "category": "string, 问题所属分类"
        },
        "valid_fields": ["question", "category"],
        "sample_records": samples,
        "available_models": ["mimo-v2.5"],
        "evaluation_signal": ["EM (Exact Match)", "F1 (token-level overlap)"],
    }
    return json.dumps(input_info, ensure_ascii=False, indent=2)


def main():
    # 加载 TASK.md 作为 system prompt
    task_prompt = (ROOT / "Agent" / "TASK.md").read_text(encoding="utf-8").strip()

    # 构造用户输入
    user_input = build_user_input()

    print("=" * 60)
    print("Task Hypothesis Agent — SearchQA 测试")
    print("=" * 60)
    print(f"\n[System Prompt] {len(task_prompt)} chars")
    print(f"[User Input] {len(user_input)} chars")
    print(f"\n调用 mimo-2.5 ...")

    raw_output = call_llm(task_prompt, user_input)

    # 解析 JSON
    try:
        # 有些模型会在 JSON 前后加 ```json ... ```
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        hypotheses = json.loads(cleaned.strip())
        # 兼容各种返回格式
        if isinstance(hypotheses, dict):
            # 如果是 dict，尝试取 values 或按 key 排序
            if "hypotheses" in hypotheses:
                hypotheses = hypotheses["hypotheses"]
            else:
                hypotheses = list(hypotheses.values())
        if hypotheses and isinstance(hypotheses[0], str):
            hypotheses = [json.loads(h) for h in hypotheses]
    except json.JSONDecodeError as e:
        print(f"\n[ERROR] JSON 解析失败: {e}")
        print(f"\n[RAW OUTPUT (first 2000 chars)]\n{raw_output[:2000]}")
        return

    # 打印结果
    print(f"\n{'=' * 60}")
    print(f"生成了 {len(hypotheses)} 个假设")
    print(f"{'=' * 60}")

    total_confidence = 0
    for h in hypotheses:
        total_confidence += h.get("prior_confidence", 0)
        print(f"\n--- {h['id']}: {h['task_type']} (confidence: {h.get('prior_confidence', 0):.2f}) ---")
        print(f"  假设: {h['hypothesis'][:120]}...")
        print(f"  策略: {h['execution_strategy'][:100]}...")
        print(f"  证据: {h.get('supporting_evidence', [])[:2]}")
        print(f"  不确定: {h.get('uncertainties', [])[:2]}")
        predictions = h.get("testable_predictions", [])
        if predictions:
            print(f"  预测: {predictions[0].get('prediction', '')[:100]}...")
        falsif = h.get("falsification_conditions", [])
        if falsif:
            print(f"  推翻条件: {falsif[0][:100]}...")

    print(f"\nConfidence sum: {total_confidence:.2f}")
    assert abs(total_confidence - 1.0) < 0.01, f"Confidence sum should be 1.0, got {total_confidence}"

    # 保存结果
    out_path = ROOT / "outputs" / "task_hypotheses_searchqa.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(hypotheses, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")

    # ── 根据假设调用 LLM 执行验证 ──────────────────────────
    # 选 confidence 最高的假设
    best = max(hypotheses, key=lambda h: h.get("prior_confidence", 0))
    executor_prompt = best["executor_instruction"]
    print(f"\n{'=' * 60}")
    print(f"执行验证: {best['id']} - {best['task_type']} (confidence={best['prior_confidence']:.2f})")
    print(f"{'=' * 60}")
    print(f"Executor Prompt: {executor_prompt[:200]}...")

    # 随机抽取 50 条测试样本
    test_path = ROOT / "data" / "searchqa" / "test_pool.jsonl"
    with test_path.open("r", encoding="utf-8") as f:
        all_test = [json.loads(line) for line in f if line.strip()]
    rng = random.Random(42)
    eval_samples = rng.sample(all_test, min(50, len(all_test)))
    print(f"评估样本: {len(eval_samples)} 条 (从 {len(all_test)} 条中随机抽取)\n")

    # 逐条执行
    results = []
    for i, sample in enumerate(eval_samples, 1):
        question = sample["question"]
        expected = sample["answer"].split("\n####")[0].strip()

        # 构造用户输入：只给 question + category（valid_fields）
        user_msg = f"Category: {sample.get('category', 'N/A')}\nQuestion: {question}"

        try:
            prediction = call_llm(executor_prompt, user_msg)
        except Exception as e:
            prediction = ""

        # 提取 answer 标签内容（如果模型用 <answer> 包裹）
        ans_match = re.search(r"<answer>\s*(.*?)\s*</answer>", prediction, re.DOTALL)
        if ans_match:
            prediction = ans_match.group(1).strip()

        em, f1 = evaluate_qa(expected, prediction)
        results.append({
            "id": sample["id"],
            "question": question[:80],
            "expected": expected,
            "prediction": prediction[:200],
            "em": em,
            "f1": f1,
        })

        if i % 10 == 0 or i == len(eval_samples):
            cur_em = sum(r["em"] for r in results) / len(results)
            cur_f1 = sum(r["f1"] for r in results) / len(results)
            print(f"  [{i:2d}/{len(eval_samples)}] EM: {cur_em:.1%} | F1: {cur_f1:.4f}")

    # 最终结果
    final_em = sum(r["em"] for r in results) / len(results)
    final_f1 = sum(r["f1"] for r in results) / len(results)
    print(f"\n{'=' * 60}")
    print(f"最终结果 ({best['id']}: {best['task_type']})")
    print(f"  EM:  {final_em:.1%}")
    print(f"  F1:  {final_f1:.4f}")
    print(f"{'=' * 60}")

    # 保存执行结果
    exec_out = ROOT / "outputs" / "task_exec_searchqa.json"
    with exec_out.open("w", encoding="utf-8") as f:
        json.dump({
            "hypothesis": best,
            "metrics": {"em": final_em, "f1": final_f1},
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"执行结果已保存: {exec_out}")


if __name__ == "__main__":
    main()
