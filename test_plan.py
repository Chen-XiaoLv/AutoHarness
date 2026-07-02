"""读取 rollout 收集的 plan_history.jsonl，调用 Planner Agent 获取决策反馈。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from Core.func import create_planner, build_plan_record

OUTPUT_DIR = ROOT / "outputs"


def find_latest_run() -> Path:
    """找到最近一次 rollout 的输出目录。"""
    runs = sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.name, reverse=True)
    for r in runs:
        if (r / "Plan" / "plan_history.jsonl").exists():
            return r
    raise FileNotFoundError("No plan_history.jsonl found in any run directory")


def test_plan():
    run_dir = find_latest_run()
    history_path = run_dir / "Plan" / "plan_history.jsonl"
    memory_path = run_dir / "Plan" / "plan_memory.jsonl"
    print(f"Reading rollout from: {run_dir.name}")

    # 读取所有轮次
    records = []
    with history_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"Found {len(records)} round(s)\n")

    # 创建 Planner
    planner = create_planner("searchqa")

    # 收集 Planner 输出
    memory_records = []

    # 对每轮记录调用 Planner
    for rec in records:
        round_i = rec["round"]
        print(f"{'='*60}")
        print(f"Round {round_i} — feeding to Planner")
        print(f"{'='*60}")
        print(f"  train_em={rec['train_em']}, dev_em={rec['dev_em']}")
        print(f"  gate={rec['gate_decision']}, gate_reason={rec['gate_reason']}")
        print(f"  skill_diff: {rec['skill_diff'][:80]}...")
        print(f"  n_critic_edits={len(rec.get('aggregated_edits', []))}, n_evolver_actions={len(rec.get('evolver_actions', []))}")

        # 移除已有的 planner_decision，让 Planner 重新决策
        rec.pop("planner_decision", None)

        # 调用 Planner
        decision = planner.run(rec)

        print(f"\n  --- Planner Decision ---")
        print(f"  next_action: {decision.get('next_action')}")
        print(f"  reason:      {decision.get('reason', 'N/A')[:200]}")
        print(f"  evidence:    {decision.get('evidence', [])}")
        print(f"  risk:        {decision.get('risk', 'N/A')[:200]}")
        if "proposed_change" in decision:
            print(f"  proposed_change: {decision['proposed_change'][:200]}...")
        if "hypothesis" in decision:
            print(f"  hypothesis:  {decision['hypothesis'][:200]}...")
        print()

        # 保存到 memory 记录
        memory_records.append({
            "round": round_i,
            "train_em": rec["train_em"],
            "dev_em": rec["dev_em"],
            "gate_decision": rec["gate_decision"],
            "planner_decision": decision,
        })

    # 写入 plan_memory.jsonl
    with memory_path.open("w", encoding="utf-8") as f:
        for rec in memory_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Saved {len(memory_records)} record(s) to {memory_path}")


if __name__ == "__main__":
    test_plan()
