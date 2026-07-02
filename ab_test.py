"""A/B 测试：对比有无 Contract 在 test_pool 上的差异（5次，记录均值和标准差）。"""
import json
import sys
import statistics
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from Core.func import (
    ROOT as PROJ_ROOT, CONFIG, init_skill_opt, create_agents,
    load_split, eval_on,
)

CONTRACT_PATH = PROJ_ROOT / "auto_outputs" / "TASK_Contract.md"
BASELINE_PATH = PROJ_ROOT / "auto_outputs" / "baseline.md"
INSTRUCTION_PATH = PROJ_ROOT / "auto_outputs" / "instruct_init.md"
OUTPUT_PATH = PROJ_ROOT / "auto_outputs" / "ab_test.json"


def run_ab_test(n_samples: int = 50, n_runs: int = 5, output_dir: Path = None):
    out_dir = output_dir or PROJ_ROOT / "auto_outputs"
    contract_path = out_dir / "TASK_Contract.md"
    baseline_path = out_dir / "baseline.md"
    instruction_path = out_dir / "instruct_init.md"
    output_path = out_dir / "ab_test.json"

    dataset = "searchqa"
    test_pool = load_split(dataset, "test_pool", "test")
    if n_samples and n_samples < len(test_pool):
        test_pool = test_pool[:n_samples]

    skill_opt_dir, init_skill_name = init_skill_opt(dataset)

    # 加载已有结果（支持断点续跑）
    if output_path.exists():
        saved = json.loads(output_path.read_text(encoding="utf-8"))
        all_em_no = saved.get("no_contract_runs", {}).get("em", [])
        all_em_yes = saved.get("with_contract_runs", {}).get("em", [])
        all_f1_no = saved.get("no_contract_runs", {}).get("f1", [])
        all_f1_yes = saved.get("with_contract_runs", {}).get("f1", [])
        all_sub_no = saved.get("no_contract_runs", {}).get("sub_em", [])
        all_sub_yes = saved.get("with_contract_runs", {}).get("sub_em", [])
        done_runs = min(len(all_em_no), len(all_em_yes))
        print(f"[RESUME] Found {done_runs} completed runs, resuming from run {done_runs + 1}")
    else:
        all_em_no, all_em_yes = [], []
        all_f1_no, all_f1_yes = [], []
        all_sub_no, all_sub_yes = [], []
        done_runs = 0

    for run_i in range(done_runs, n_runs):
        print(f"\n{'='*60}")
        print(f"Run {run_i + 1}/{n_runs}")
        print(f"{'='*60}")

        for label, instruction in [("Baseline", baseline_path), ("With Instruction", instruction_path)]:
            executor, _, _, _ = create_agents(
                dataset,
                skill_opt_dir=skill_opt_dir,
                init_skill_name=init_skill_name,
                instruction_path=instruction,
            )
            executor.update()
            metrics, _ = eval_on(executor, test_pool, desc=f"{label} #{run_i+1}")
            if label == "Baseline":
                all_em_no.append(metrics["em"])
                all_f1_no.append(metrics["f1"])
                all_sub_no.append(metrics["sub_em"])
            else:
                all_em_yes.append(metrics["em"])
                all_f1_yes.append(metrics["f1"])
                all_sub_yes.append(metrics["sub_em"])
            print(f"  [{label}] EM: {metrics['em']:.2%} | F1: {metrics['f1']:.4f} | Sub-EM: {metrics['sub_em']:.2%}")

        # 每轮保存（断点续跑）
        _save_results(all_em_no, all_f1_no, all_sub_no, all_em_yes, all_f1_yes, all_sub_yes, n_samples, run_i + 1, output_path)

    _print_summary(all_em_no, all_f1_no, all_sub_no, all_em_yes, all_f1_yes, all_sub_yes, n_samples, n_runs, output_path)


def _save_results(all_em_no, all_f1_no, all_sub_no, all_em_yes, all_f1_yes, all_sub_yes, n_samples, n_runs, output_path):
    data = {
        "n_runs": n_runs,
        "n_samples": n_samples,
        "no_contract_runs": {"em": [round(v, 4) for v in all_em_no], "f1": [round(v, 4) for v in all_f1_no], "sub_em": [round(v, 4) for v in all_sub_no]},
        "with_contract_runs": {"em": [round(v, 4) for v in all_em_yes], "f1": [round(v, 4) for v in all_f1_yes], "sub_em": [round(v, 4) for v in all_sub_yes]},
    }
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_summary(all_em_no, all_f1_no, all_sub_no, all_em_yes, all_f1_yes, all_sub_yes, n_samples, n_runs, output_path):
    print(f"\n{'='*60}")
    print(f"A/B Test Summary ({n_runs} runs, {n_samples} samples each)")
    print(f"{'='*60}")
    print(f"{'Condition':<20} {'EM mean':>8} {'EM std':>8} {'F1 mean':>8} {'F1 std':>8} {'Sub mean':>8} {'Sub std':>8}")
    print("-" * 76)

    summary = {}
    for label, em, f1, sub in [
        ("No Contract", all_em_no, all_f1_no, all_sub_no),
        ("With Contract", all_em_yes, all_f1_yes, all_sub_yes),
    ]:
        em_mean = statistics.mean(em) if em else 0
        em_std = statistics.stdev(em) if len(em) > 1 else 0
        f1_mean = statistics.mean(f1) if f1 else 0
        f1_std = statistics.stdev(f1) if len(f1) > 1 else 0
        sub_mean = statistics.mean(sub) if sub else 0
        sub_std = statistics.stdev(sub) if len(sub) > 1 else 0
        summary[label] = {
            "em_mean": round(em_mean, 4), "em_std": round(em_std, 4),
            "f1_mean": round(f1_mean, 4), "f1_std": round(f1_std, 4),
            "sub_em_mean": round(sub_mean, 4), "sub_em_std": round(sub_std, 4),
        }
        print(f"{label:<20} {em_mean:>7.2%} {em_std:>7.2%} {f1_mean:>7.4f} {f1_std:>7.4f} {sub_mean:>7.2%} {sub_std:>7.2%}")

    delta_em = summary["With Contract"]["em_mean"] - summary["No Contract"]["em_mean"]
    print(f"\nContract impact: EM {delta_em:+.2%}")

    # 保存最终结果
    data = {
        "n_runs": n_runs,
        "n_samples": n_samples,
        "no_contract": summary["No Contract"],
        "with_contract": summary["With Contract"],
        "delta_em": round(delta_em, 4),
        "no_contract_runs": {"em": [round(v, 4) for v in all_em_no], "f1": [round(v, 4) for v in all_f1_no], "sub_em": [round(v, 4) for v in all_sub_no]},
        "with_contract_runs": {"em": [round(v, 4) for v in all_em_yes], "f1": [round(v, 4) for v in all_f1_yes], "sub_em": [round(v, 4) for v in all_sub_yes]},
    }
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results saved to {output_path}")


if __name__ == "__main__":
    run_ab_test(n_samples=215, n_runs=5)
