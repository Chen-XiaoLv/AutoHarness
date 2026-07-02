#---------------导入框架--------------------#
import json
import sys
from datetime import datetime
from pathlib import Path


class TeeWriter:
    """同时输出到控制台和文件。"""
    def __init__(self, file_path: Path):
        self.file = open(file_path, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, text):
        self.stdout.write(text)
        self.file.write(text)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        self.file.close()

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from Core.func import (
    ROOT as PROJ_ROOT, CONFIG, init_skill_opt, create_agents, create_planner,
    load_split, eval_on, aggregate_metrics, aggregate_critic_edits,
    build_plan_record,
)
from Core.Logger import HarnessLogger

#------------外层约束探索阶段----------------#
'''
实现在auto_rollout.py中，我们需要调用这个接口，来获取第一轮假设以及初始约束
获取假设后，需要执行test_contract.py的内容，来评估假设的正确性
选择得分最高的假设作为初始假设后，执行ab_test.py的内容，来验证假设提升有效性（跑3轮）
另外需要在此时创建Plan Agent
'''

from auto_rollout import main as run_auto_rollout
from test_contract import generate_contract
from ab_test import run_ab_test
from few_shot_opt import run_few_shot_optimization


#-------------内层自进化阶段-----------------#
'''
实现在rollout.py中，确保外层提供的约束等文件生成正确后，调用rollout的接口
跑一轮自进化系统，Train Pool只跑第一次，将结果记录，下一轮跑如果当前有结果，那么就直接跳过Train Pool的实验
正常执行1轮进化过程，收集结果文件
'''

from rollout import run_loop


#--------------Plan 决策阶段----------------#
'''
实现在test_plan.py中，根据前面获取的进化结果，调用Plan Agent，来获取下一轮执行建议
打印出Plan 的Action 和 Suggestion后，停止程序，我们暂时测试到这里
'''

from test_plan import find_latest_run, test_plan


'''
注意，system_log 需要记录整个系统运行的所有输出情况，每一轮都要记录
'''


#--------------主流程整合------------------#

def save_round_snapshot(base_dir: Path, round_name: str, dataset: str, scores: dict = None, skill_opt_dir: Path = None):
    """保存当前 skillopt.md 和分数到指定轮次目录。

    Args:
        base_dir: 基础输出目录（如 outputs/0701_1814/）
        round_name: 轮次名称（如 "init", "round_1", "round_2"）
        dataset: 数据集名称
        scores: 可选，包含 dev_em / memory_em 等分数信息
        skill_opt_dir: SkillOpt 目录路径（优先使用）
    """
    snapshot_dir = base_dir / "SkillOpt" / round_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # 复制当前 skillopt.md（优先从 skill_opt_dir 读取）
    if skill_opt_dir:
        src = skill_opt_dir / "skillopt.md"
    else:
        src = PROJ_ROOT / "SkillOpt" / dataset / "skillopt.md"
    if src.exists():
        content = src.read_text(encoding="utf-8")
        (snapshot_dir / "skillopt.md").write_text(content, encoding="utf-8")
        print(f"  [SNAPSHOT] skillopt.md -> {snapshot_dir / 'skillopt.md'}")
    else:
        print(f"  [SNAPSHOT] skillopt.md not found at {src}, skipping")

    # 保存分数
    if scores:
        (snapshot_dir / "scores.json").write_text(
            json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  [SNAPSHOT] scores.json -> {snapshot_dir / 'scores.json'}")


def run_baseline_evaluation(dataset: str, output_dir: Path, skill_opt_dir: Path = None, init_skill_name: str = "skillopt") -> dict:
    """用 baseline.md 作为 instruction 跑一次 Test + Dev，记录初始分数到 start.jsonl。"""
    print("\n" + "=" * 80)
    print("Phase 0: Baseline Evaluation (baseline.md)")
    print("=" * 80)

    baseline_path = PROJ_ROOT / "auto_outputs" / "baseline.md"
    if not baseline_path.exists():
        print("  [WARN] baseline.md not found, skipping baseline evaluation")
        return {}

    # 创建 Executor，用 baseline.md 作为 instruction
    executor, _, _, _ = create_agents(
        dataset=dataset,
        skill_opt_dir=skill_opt_dir,
        init_skill_name=init_skill_name,
        instruction_path=baseline_path,
    )
    print(f"Excutor Baseline prompt: {executor.prompt}")

    # 加载数据集
    dev_gate = load_split(dataset, "dev_gate", "test")
    test_pool = load_split(dataset, "test_pool", "test")
    print(f"  Dataset: {dataset} | dev_gate={len(dev_gate)} | test_pool={len(test_pool)}")

    # 在 Test Pool 和 Dev Gate 上评估
    test_metrics, _ = eval_on(executor, test_pool, desc="Baseline Test")
    dev_metrics, _ = eval_on(executor, dev_gate, desc="Baseline Dev")

    baseline_record = {
        "phase": "baseline",
        "instruction": "baseline.md",
        "test_em": round(test_metrics["em"], 4),
        "test_f1": round(test_metrics["f1"], 4),
        "dev_em": round(dev_metrics["em"], 4),
        "dev_f1": round(dev_metrics["f1"], 4),
        "n_test": len(test_pool),
        "n_dev": len(dev_gate),
    }

    # 写入 start.jsonl
    start_path = output_dir / "start.jsonl"
    with start_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(baseline_record, ensure_ascii=False) + "\n")

    print(f"\n  Baseline Results:")
    print(f"    Test EM: {baseline_record['test_em']:.2%}  F1: {baseline_record['test_f1']:.4f}")
    print(f"    Dev  EM: {baseline_record['dev_em']:.2%}  F1: {baseline_record['dev_f1']:.4f}")
    print(f"  Saved to: {start_path}")

    return baseline_record


def run_outer_exploration(n_discover: int = 16, n_probe: int = 32, output_dir: Path = None) -> tuple[dict, dict]:
    """外层约束探索阶段：生成假设、评测、生成 Contract。"""
    print("\n" + "=" * 80)
    print("Phase 1: Outer Constraint Exploration (Hypothesis Generation)")
    print("=" * 80)
    
    # Step 1-3: Task Agent 生成假设 + Tournament + 排行榜
    task_output, scores = run_auto_rollout(n_discover=n_discover, n_probe=n_probe, output_dir=output_dir)
    
    # Step 4: 生成 Task Contract
    print("\n" + "-" * 60)
    print("Generating Task Contract...")
    print("-" * 60)
    generate_contract(output_dir=output_dir)
    
    return task_output, scores


def run_ab_validation(n_samples: int = 50, n_runs: int = 3, output_dir: Path = None) -> dict:
    """A/B 测试验证阶段：对比有无 Contract 的效果。"""
    print("\n" + "=" * 80)
    print("Phase 2: A/B Test Validation")
    print("=" * 80)
    
    ab_results = run_ab_test(n_samples=n_samples, n_runs=n_runs, output_dir=output_dir)
    
    return ab_results


def run_inner_evolution(n_rounds: int = 10, dataset: str = None, n_train_samples: int = None,
                        skip_initial_test: bool = False, skip_final_test: bool = False,
                        test_result_path: Path = None, fewshot_path: Path = None,
                        plan_suggestion: str = "", output_dir: Path = None,
                        plan_round: int = 1, action: str = "",
                        skill_opt_dir: Path = None, init_skill_name: str = "skillopt") -> Path:
    """内层自进化阶段：执行多轮进化过程。"""
    print("\n" + "=" * 80)
    print("Phase 3: Inner Evolution Loop")
    print("=" * 80)
    
    # 获取 instruction 路径（优先从 output_dir 读取）
    instruction_path = None
    if output_dir:
        candidate = output_dir / "instruct_init.md"
        if candidate.exists():
            instruction_path = candidate
    if not instruction_path:
        candidate = PROJ_ROOT / "auto_outputs" / "instruct_init.md"
        if candidate.exists():
            instruction_path = candidate
    if not instruction_path:
        if output_dir:
            candidate = output_dir / "TASK_Contract.md"
            if candidate.exists():
                instruction_path = candidate
    if not instruction_path:
        candidate = PROJ_ROOT / "auto_outputs" / "TASK_Contract.md"
        if candidate.exists():
            instruction_path = candidate
    
    run_loop(n_rounds=n_rounds, dataset=dataset, n_train_samples=n_train_samples, 
             instruction_path=instruction_path,
             skip_initial_test=skip_initial_test,
             skip_final_test=skip_final_test,
             test_result_path=test_result_path,
             fewshot_path=fewshot_path,
             plan_suggestion=plan_suggestion,
             output_dir=output_dir,
             skill_opt_dir=skill_opt_dir,
             init_skill_name=init_skill_name,
             plan_round=plan_round,
             action=action)
    
    # 如果指定了 output_dir，直接返回；否则查找最新目录
    if output_dir:
        return output_dir
    out = PROJ_ROOT / CONFIG['path']['out_dir']
    runs = sorted(out.iterdir(), key=lambda p: p.name, reverse=True)
    for r in runs:
        if (r / "Plan" / "plan_history.jsonl").exists():
            return r
    raise FileNotFoundError("No plan_history.jsonl found after evolution")


def run_plan_decision(instruction_path: Path = None, run_dir: Path = None, current_plan_round: int = None) -> dict:
    """Plan 决策阶段：获取下一轮执行建议。"""
    print("\n" + "=" * 80)
    print("Phase 4: Plan Decision")
    print("=" * 80)
    
    if not run_dir:
        run_dir = find_latest_run()
    history_path = run_dir / "Plan" / "plan_history.jsonl"
    memory_path = run_dir / "Plan" / "plan_memory.jsonl"
    fewshot_history_path = run_dir / "Plan" / "fewshot_history.jsonl"
    
    print(f"Reading rollout from: {run_dir.name}")
    
    # 读取所有轮次
    all_records = []
    with history_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_records.append(json.loads(line))
    
    # 读取 fewshot 历史（如果存在）
    fewshot_by_round = {}
    if fewshot_history_path.exists():
        with fewshot_history_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    fs_rec = json.loads(line)
                    fewshot_by_round[fs_rec["round"]] = fs_rec
    
    # 按 plan_round 过滤：只看当前 plan_round 的记录
    # 但把之前 plan_round 的最终状态作为上下文
    records = []
    prev_plan_summary = []
    for rec in all_records:
        pr = rec.get("plan_round", 1)
        if current_plan_round is not None and pr == current_plan_round:
            records.append(rec)
        elif current_plan_round is not None and pr < current_plan_round:
            # 只保留每个旧 plan_round 的最后一条作为摘要
            prev_plan_summary = [r for r in all_records if r.get("plan_round", 1) == pr]
    
    # 如果过滤后没有记录，回退到全部记录
    if not records:
        records = all_records
    
    # 构建最终传给 Planner 的记录：之前的摘要 + 当前轮次
    planner_records = []
    if prev_plan_summary:
        for rec in prev_plan_summary:
            rec.pop("planner_decision", None)
            planner_records.append(rec)
    for rec in records:
        rec.pop("planner_decision", None)
        planner_records.append(rec)
    
    print(f"Found {len(all_records)} total round(s), feeding {len(planner_records)} to Planner (current plan_round={current_plan_round})\n")
    
    # 创建 Planner
    dataset = CONFIG.get("dataset", {}).get("active", "searchqa")
    planner = create_planner(dataset, instruction_path=instruction_path)
    
    # 收集 Planner 输出
    memory_records = []
    
    # 对每轮记录调用 Planner
    for rec in planner_records:
        round_i = rec["round"]
        plan_r = rec.get("plan_round", "?")
        action_tag = rec.get("action", "")
        print(f"{'=' * 60}")
        print(f"Plan Round {plan_r} | Inner Round {round_i} | Action: {action_tag}")
        print(f"{'=' * 60}")
        print(f"  train_em={rec['train_em']}, dev_em={rec['dev_em']}, memory_em={rec.get('memory_em', 'N/A')}")
        print(f"  gate={rec['gate_decision']}, gate_reason={rec.get('gate_reason', 'N/A')}")
        print(f"  skill_diff: {rec.get('skill_diff', 'N/A')[:80]}...")
        print(f"  n_critic_edits={len(rec.get('aggregated_edits', []))}, n_evolver_actions={len(rec.get('evolver_actions', []))}")
        
        # 附加 fewshot 信息（如果该轮有 fewshot 记录）
        fs_rec = fewshot_by_round.get(plan_r)
        if fs_rec:
            rec["fewshot"] = {
                "content": fs_rec.get("fewshot_content", ""),
                "baseline_em": fs_rec.get("baseline_em"),
                "fewshot_em": fs_rec.get("fewshot_em"),
                "categories": fs_rec.get("categories", []),
                "state": fs_rec.get("state", ""),
            }
            print(f"  fewshot: state={fs_rec.get('state')}, baseline_em={fs_rec.get('baseline_em')}, fewshot_em={fs_rec.get('fewshot_em')}")
        
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
            "memory_em": rec.get("memory_em"),
            "gate_decision": rec["gate_decision"],
            "planner_decision": decision,
        })
    
    # 写入 plan_memory.jsonl
    with memory_path.open("w", encoding="utf-8") as f:
        for rec in memory_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Saved {len(memory_records)} record(s) to {memory_path}")
    
    return memory_records


def extract_plan_suggestion(memory_records: list[dict]) -> tuple[str, str]:
    """从 Plan Memory 中提取最新的 proposed_change 作为下一轮的 plan_suggestion。"""
    if not memory_records:
        return ""
    
    # 取最后一轮的 proposed_change
    last_record = memory_records[-1]
    decision = last_record.get("planner_decision", {})
    suggestion = decision.get("suggestion", "")
    action=decision.get('next_action','')
    
    if suggestion:
        print(f"\n[Plan Agent] Suggestion for next round:")
        print(f"  {suggestion[:200]}...")
    
    if action:
        print(f"\n[Plan Agent] Action for next round:")
        print(f"  {action[:200]}...")
    
    return suggestion,action


def main(
    n_discover: int = 5,
    n_probe: int = 16,
    n_ab_samples: int = 50,
    n_ab_runs: int = 3,
    n_evolution_rounds: int = 10,
    dataset: str = None,
    n_train_samples: int = None,
    skip_outer: bool = False,
    skip_ab: bool = False,

    max_plan_rounds: int = None,
):
    """
    主入口：整合整个 AutoHarness 流程。
    
    Args:
        n_discover: Task Agent 观察的 Discover 样本数
        n_probe: Tournament 评测的 Probe 样本数
        n_ab_samples: A/B 测试样本数
        n_ab_runs: A/B 测试运行次数
        n_evolution_rounds: 内层进化轮数
        dataset: 数据集名称
        n_train_samples: 每轮训练采样数
        skip_outer: 跳过外层探索（使用已有 Contract）
        skip_ab: 跳过 A/B 测试
        max_plan_rounds: 外层 Plan 最大轮数（覆盖 config 中的 autoharness.max_plan_rounds）
    """
    # ── 创建统一输出目录 ──
    log_tag = datetime.now().strftime("%m%d_%H%M")
    run_output_dir = PROJ_ROOT / "outputs" / log_tag
    run_output_dir.mkdir(parents=True, exist_ok=True)

    # ── 设置日志文件（写入统一目录） ──
    log_path = run_output_dir / "auto_harness.log"
    tee = TeeWriter(log_path)
    old_stdout = sys.stdout
    sys.stdout = tee

    try:
        print("\n" + "#" * 80)
        print("# AutoHarness - Integrated System")
        print(f"# Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"# Log file: {log_path}")

        # ── 初始化 SkillOpt（仅一次） ──
        skill_opt_dir, init_skill_name = init_skill_opt(dataset, output_dir=run_output_dir)
        print(f"[INIT] SkillOpt: {skill_opt_dir / (init_skill_name + '.md') if init_skill_name else skill_opt_dir}")
        print(f"# Output dir: {run_output_dir}")
        print("#" * 80)
        
        ds = dataset or CONFIG.get("dataset", {}).get("active", "searchqa")
        round_plan = 1
        base_dir = run_output_dir  # 所有输出统一到此目录

        # ── Phase 0: 基线评估 ──
        run_baseline_evaluation(ds, run_output_dir, skill_opt_dir=skill_opt_dir, init_skill_name=init_skill_name)

        # ── 结果记录文件 ──
        res_record_path = run_output_dir / "res_record.jsonl"
        
        # 获取 instruction 路径（优先从统一目录读取）
        instruction_path = run_output_dir / "instruct_init.md"
        if not instruction_path.exists():
            instruction_path = PROJ_ROOT / "auto_outputs" / "instruct_init.md"
        if not instruction_path.exists():
            instruction_path = run_output_dir / "TASK_Contract.md"
        if not instruction_path.exists():
            instruction_path = PROJ_ROOT / "auto_outputs" / "TASK_Contract.md"
        instruction_path = instruction_path if instruction_path.exists() else None
        
        # ── Phase 1: 外层约束探索 ──
        if not skip_outer:
            task_output, scores = run_outer_exploration(n_discover=n_discover, n_probe=n_probe, output_dir=run_output_dir)
        else:
            print("\n[SKIP] Outer exploration skipped, using existing Contract")
        
        # ── Phase 2: A/B 测试验证 ──
        if not skip_ab:
            ab_results = run_ab_validation(n_samples=n_ab_samples, n_runs=n_ab_runs, output_dir=run_output_dir)
        else:
            print("\n[SKIP] A/B test skipped")
        
        # ── 保存初始 skillopt.md 到临时文件 ──
        init_skill_src = skill_opt_dir / "skillopt.md"
        init_skill_content = init_skill_src.read_text(encoding="utf-8") if init_skill_src.exists() else ""
        
        # ── Phase 3: 第一轮内层自进化（完整测试） ──
        print(f"\n[Plan Agent] Round {round_plan} — CONTINUE_SKILL_EVOLUTION (initial)")
        test_result_path = base_dir / "Test_result.jsonl"
        run_inner_evolution(n_rounds=1, dataset=dataset, 
                            n_train_samples=n_train_samples,
                            test_result_path=test_result_path,
                            skip_final_test=True,
                            output_dir=run_output_dir,
                            plan_round=round_plan, action="CONTINUE_SKILL_EVOLUTION",
                            skill_opt_dir=skill_opt_dir, init_skill_name=init_skill_name)
        print(f"\n[EVOLUTION] Completed. Results saved to: {run_output_dir}")
        
        # 保存初始 skillopt.md 到 base_dir/SkillOpt/init/
        init_dir = base_dir / "SkillOpt" / "init"
        init_dir.mkdir(parents=True, exist_ok=True)
        (init_dir / "skillopt.md").write_text(init_skill_content, encoding="utf-8")
        print(f"  [SNAPSHOT] init skillopt.md -> {init_dir / 'skillopt.md'}")
        
        # 保存 round_1 快照（进化后）
        memory_records = run_plan_decision(instruction_path=instruction_path, run_dir=base_dir, current_plan_round=round_plan)
        last_scores = {}
        if memory_records:
            last = memory_records[-1]
            last_scores = {"dev_em": last.get("dev_em"), "memory_em": last.get("memory_em")}
        save_round_snapshot(base_dir, f"round_{round_plan}", ds, scores=last_scores, skill_opt_dir=skill_opt_dir)
        
        # ── 提取 Plan Suggestion ──
        plan_suggestion, action = extract_plan_suggestion(memory_records)
        
        # 记录本轮结果
        rec = {"round": round_plan, "action": "CONTINUE_SKILL_EVOLUTION", **last_scores}
        with res_record_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        
        # ── Plan Agent 迭代循环 ──
        if max_plan_rounds is None:
            max_plan_rounds = CONFIG.get("autoharness", {}).get("max_plan_rounds", 20)
        while action != "STOP" and round_plan < max_plan_rounds:
            round_plan += 1
            print(f"\n{'='*60}")
            print(f"[Plan Agent] Round {round_plan}")
            print(f"  Action: {action}")
            print(f"{'='*60}")
            
            # 检查 action 格式，无效则重新请求
            while action == "" or action not in ['CONTINUE_SKILL_EVOLUTION', 'ADD_OR_UPDATE_FEWSHOT', 'RERUN_EVALUATION', 'STOP', 'CHALLENGE_CONTRACT']:
                memory_records = run_plan_decision(instruction_path=instruction_path, run_dir=base_dir, current_plan_round=round_plan)
                plan_suggestion, action = extract_plan_suggestion(memory_records)
            
            if action == "CHALLENGE_CONTRACT":
                print(f"\n[Plan Agent] CHALLENGE_CONTRACT — stopping for contract review")
                break
            elif action == "STOP":
                break
            elif action == "RERUN_EVALUATION":
                # 用上一轮的 skillopt 重新评估，不修改 skill，重做算一轮
                print(f"\n[Plan Agent] RERUN_EVALUATION — re-running with current skill")
                existing_fewshot = base_dir / "fewshot.jsonl"
                run_dir = run_inner_evolution(n_rounds=1, dataset=dataset, 
                                              n_train_samples=n_train_samples,
                                              skip_initial_test=True, skip_final_test=True,
                                              test_result_path=test_result_path,
                                              fewshot_path=existing_fewshot if existing_fewshot.exists() else None,
                                              plan_suggestion=plan_suggestion,
                                              output_dir=run_output_dir,
                                              plan_round=round_plan, action=action,
                                              skill_opt_dir=skill_opt_dir, init_skill_name=init_skill_name)
                memory_records = run_plan_decision(instruction_path=instruction_path, run_dir=base_dir, current_plan_round=round_plan)
                last_scores = {}
                if memory_records:
                    last = memory_records[-1]
                    last_scores = {"dev_em": last.get("dev_em"), "memory_em": last.get("memory_em")}
                save_round_snapshot(base_dir, f"round_{round_plan}", ds, scores=last_scores, skill_opt_dir=skill_opt_dir)
                plan_suggestion, action = extract_plan_suggestion(memory_records)
                rec = {"round": round_plan, "action": "RERUN_EVALUATION", **last_scores}
                with res_record_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            elif action == "CONTINUE_SKILL_EVOLUTION":
                existing_fewshot = base_dir / "fewshot.jsonl"
                run_dir = run_inner_evolution(n_rounds=1, dataset=dataset, 
                                              n_train_samples=n_train_samples,
                                              skip_initial_test=True, skip_final_test=True,
                                              test_result_path=test_result_path,
                                              fewshot_path=existing_fewshot if existing_fewshot.exists() else None,
                                              plan_suggestion=plan_suggestion,
                                              output_dir=run_output_dir,
                                              plan_round=round_plan, action=action,
                                              skill_opt_dir=skill_opt_dir, init_skill_name=init_skill_name)
                print(f"\n[EVOLUTION] Completed. Results saved to: {run_output_dir}")
                memory_records = run_plan_decision(instruction_path=instruction_path, run_dir=base_dir, current_plan_round=round_plan)
                last_scores = {}
                if memory_records:
                    last = memory_records[-1]
                    last_scores = {"dev_em": last.get("dev_em"), "memory_em": last.get("memory_em")}
                save_round_snapshot(base_dir, f"round_{round_plan}", ds, scores=last_scores, skill_opt_dir=skill_opt_dir)
                plan_suggestion, action = extract_plan_suggestion(memory_records)
                rec = {"round": round_plan, "action": "CONTINUE_SKILL_EVOLUTION", **last_scores}
                with res_record_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            elif action == "ADD_OR_UPDATE_FEWSHOT":
                print(f"\n[Plan Agent] ADD_OR_UPDATE_FEWSHOT — running few-shot optimization")
                fewshot_result = run_few_shot_optimization(
                    run_dir=base_dir,
                    dataset=ds,
                    n_validate_rounds=3,
                )

                # 读取当前 skill 和 instruction 内容
                current_skill = ""
                read_skill_dir = skill_opt_dir  # 使用外层的 outputs/<tag>/SkillOpt/
                if read_skill_dir.exists():
                    for f in sorted(read_skill_dir.glob("*.md")):
                        current_skill = f.read_text(encoding="utf-8").strip()[:3000]
                current_instr = ""
                if instruction_path and instruction_path.exists():
                    current_instr = instruction_path.read_text(encoding="utf-8").strip()[:1000]

                # 构建 fewshot 记录并写入 fewshot_history.jsonl
                fewshot_history_path = base_dir / "Plan" / "fewshot_history.jsonl"
                if fewshot_result:
                    fewshot_record = {
                        "round": round_plan,
                        "current_skill": current_skill,
                        "current_instruction": current_instr,
                        "fewshot_content": fewshot_result["fewshot_content"],
                        "baseline_em": round(fewshot_result["baseline_em"], 4),
                        "fewshot_em": round(fewshot_result["fewshot_em"], 4),
                        "categories": fewshot_result["categories"],
                        "state": "Accepted",
                    }
                    with fewshot_history_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(fewshot_record, ensure_ascii=False) + "\n")

                    fewshot_path = base_dir / "fewshot.jsonl"
                    print(f"[FEWSHOT] Optimization passed, using {fewshot_path}")
                    run_inner_evolution(
                        n_rounds=1, dataset=dataset,
                        n_train_samples=n_train_samples,
                        skip_initial_test=True, skip_final_test=True,
                        test_result_path=test_result_path,
                        fewshot_path=fewshot_path,
                        plan_suggestion=plan_suggestion,
                        output_dir=run_output_dir,
                        plan_round=round_plan, action=action,
                        skill_opt_dir=skill_opt_dir, init_skill_name=init_skill_name,
                    )
                    memory_records = run_plan_decision(instruction_path=instruction_path, run_dir=base_dir, current_plan_round=round_plan)
                    last_scores = {}
                    if memory_records:
                        last = memory_records[-1]
                        last_scores = {"dev_em": last.get("dev_em"), "memory_em": last.get("memory_em")}
                    save_round_snapshot(base_dir, f"round_{round_plan}", ds, scores=last_scores, skill_opt_dir=skill_opt_dir)
                    plan_suggestion, action = extract_plan_suggestion(memory_records)
                    rec = {"round": round_plan, "action": "ADD_OR_UPDATE_FEWSHOT", **last_scores}
                    with res_record_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                else:
                    # 未通过也记录，标记为 Rejected
                    fewshot_record = {
                        "round": round_plan,
                        "current_skill": current_skill,
                        "current_instruction": current_instr,
                        "fewshot_content": "",
                        "baseline_em": 0,
                        "fewshot_em": 0,
                        "categories": [],
                        "state": "Rejected",
                    }
                    with fewshot_history_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(fewshot_record, ensure_ascii=False) + "\n")

                    print("[FEWSHOT] Optimization did not improve, re-deciding")
                    memory_records = run_plan_decision(instruction_path=instruction_path, run_dir=base_dir, current_plan_round=round_plan)
                    plan_suggestion, action = extract_plan_suggestion(memory_records)
        
        # ── 收尾：最后一次 Final Test ──
        print(f"\n{'='*60}")
        print("Final Test Pool Evaluation (closing)")
        print(f"{'='*60}")
        existing_fewshot = base_dir / "fewshot.jsonl"
        run_inner_evolution(n_rounds=0, dataset=dataset, n_train_samples=n_train_samples,
                            skip_initial_test=True, skip_final_test=False,
                            test_result_path=test_result_path,
                            fewshot_path=existing_fewshot if existing_fewshot.exists() else None,
                            plan_suggestion=plan_suggestion,
                            output_dir=run_output_dir,
                            plan_round=round_plan, action="FINAL_TEST",
                            skill_opt_dir=skill_opt_dir, init_skill_name=init_skill_name)
        
        # 记录 Final Test 结果
        if test_result_path.exists():
            tr = json.loads(test_result_path.read_text(encoding="utf-8"))
            final_data = tr.get("final", tr)
            final_rec = {
                "round": "final",
                "action": "FINAL_TEST",
                "test_em": final_data.get("em"),
                "test_f1": final_data.get("f1"),
                "sub_em": final_data.get("sub_em"),
                "test_n": final_data.get("total"),
            }
            with res_record_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(final_rec, ensure_ascii=False) + "\n")
        
        # ── 总结 ──
        print("\n" + "#" * 80)
        print("# AutoHarness - Execution Complete")
        print("#" * 80)
        print(f"  Output dir: {run_output_dir}")
        print(f"  Total plan rounds: {round_plan}")
        print(f"  SkillOpt snapshots: {base_dir / 'SkillOpt'}")
        if plan_suggestion:
            print(f"  Last suggestion: {plan_suggestion[:100]}...")
        print()
        
        return {
            "base_dir": base_dir,
            "run_dir": run_output_dir,
            "memory_records": memory_records,
            "plan_suggestion": plan_suggestion,
            "round_plan": round_plan,
        }
    finally:
        sys.stdout = old_stdout
        tee.close()
        print(f"Log saved to: {log_path}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AutoHarness - Integrated System")
    parser.add_argument("--n-discover", type=int, default=5, help="Discover 样本数")
    parser.add_argument("--n-probe", type=int, default=16, help="Probe 评测样本数")
    parser.add_argument("--n-ab-samples", type=int, default=50, help="A/B 测试样本数")
    parser.add_argument("--n-ab-runs", type=int, default=3, help="A/B 测试运行次数")
    parser.add_argument("--n-rounds", type=int, default=10, help="内层进化轮数")
    parser.add_argument("--max-plan-rounds", type=int, default=None, help="外层 Plan 最大轮数")
    parser.add_argument("--dataset", type=str, default=None, help="数据集名称")
    parser.add_argument("--n-train-samples", type=int, default=None, help="每轮训练采样数")
    parser.add_argument("--skip-outer", action="store_true", help="跳过外层探索")
    parser.add_argument("--skip-ab", action="store_true", help="跳过 A/B 测试")
    args = parser.parse_args()
    
    main(
        n_discover=args.n_discover,
        n_probe=args.n_probe,
        n_ab_samples=args.n_ab_samples,
        n_ab_runs=args.n_ab_runs,
        n_evolution_rounds=args.n_rounds,
        dataset=args.dataset,
        n_train_samples=args.n_train_samples,
        skip_outer=args.skip_outer,
        skip_ab=args.skip_ab,
        max_plan_rounds=args.max_plan_rounds,
    )
