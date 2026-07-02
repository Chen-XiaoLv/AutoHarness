import json
import random
import shutil
from datetime import datetime
from pathlib import Path
from Core.Logger import HarnessLogger
from Core.func import (
    ROOT, CONFIG, init_skill_opt, create_agents, create_planner,
    load_split, eval_on, aggregate_metrics, aggregate_critic_edits,
    build_plan_record,
)


def run_loop(n_rounds: int = 10, dataset: str = None, n_train_samples: int = None, instruction_path: Path | None = None, skip_initial_test: bool = False, skip_final_test: bool = False, test_result_path: Path | None = None, fewshot_path: Path | None = None, plan_suggestion: str = "", output_dir: Path | None = None, plan_round: int = 1, action: str = "", skill_opt_dir: Path | None = None, init_skill_name: str = "skillopt"):
    ds_cfg = CONFIG.get("dataset", {})
    if not dataset:
        dataset = ds_cfg.get("active", "gsm8k")
    evo_seed = CONFIG.get("evolution", {}).get("seed", 42)

    # ── 初始化 SkillOpt（仅首次） ─────────────────────────
    if skill_opt_dir is None:
        skill_opt_dir, init_skill_name = init_skill_opt(dataset, output_dir=output_dir)

    loop_tag = datetime.now().strftime("%m%d_%H%M")
    loop_dir = output_dir if output_dir else ROOT / CONFIG['path']['out_dir'] / loop_tag

    executor_dir = loop_dir / "Executor"
    critic_dir = loop_dir / "Critic"
    evolver_dir = loop_dir / "Evolver"
    candidates_dir = loop_dir / "Candidates"
    gate_dir = loop_dir / "Gate"
    plan_dir = loop_dir / "Plan"
    for d in (executor_dir, critic_dir, evolver_dir, candidates_dir, gate_dir, plan_dir):
        d.mkdir(parents=True, exist_ok=True)
    plan_history_path = plan_dir / "plan_history.jsonl"
    
    # fewshot.jsonl：优先使用外部传入的，否则在 loop_dir 创建空文件
    if fewshot_path and fewshot_path.exists() and fewshot_path.stat().st_size > 0:
        print(f"[FEWSHOT] Using external fewshot: {fewshot_path}")
    else:
        fewshot_path = loop_dir / "fewshot.jsonl"
        if not fewshot_path.exists():
            fewshot_path.touch()
    
    executorAgent, criticAgent, evolverAgent, match_mode = create_agents(
        dataset, skill_opt_dir=skill_opt_dir, init_skill_name=init_skill_name,
        instruction_path=instruction_path, fewshot_path=fewshot_path,
        plan_suggestion=plan_suggestion,
    )
    plannerAgent = create_planner(dataset, instruction_path=instruction_path)

    # ── 存储各 Agent 的渲染后 system prompt ──────────────────
    prompt_dump = loop_dir / "prompt.md"
    with prompt_dump.open("w", encoding="utf-8") as pf:
        for name, agent in [("Executor", executorAgent), ("Critic", criticAgent), ("Evolver", evolverAgent), ("Planner", plannerAgent)]:
            pf.write(f"# {name} Agent\n\n")
            pf.write("```\n")
            pf.write(agent.prompt)
            pf.write("\n```\n\n---\n\n")
    print(f"[PROMPT] Agent system prompts saved to {prompt_dump}")

    logger = HarnessLogger(
        log_file=loop_dir / "harness.log",
        event_file=loop_dir / "events.jsonl",
        level=CONFIG.get("logging", {}).get("level", "INFO"),
    )

    # ── 加载数据集 ─────────────────────────────────────────
    evo_cfg = CONFIG.get("evolution", {})
    train_pool_size = evo_cfg.get("train_pool_size")
    sample_ratio = evo_cfg.get("sample_ratio")
    if train_pool_size:
        train_pool_full = load_split(dataset, "train", "train")
        rng_pool = random.Random(evo_seed)
        if train_pool_size < len(train_pool_full):
            train_pool_full = rng_pool.sample(train_pool_full, train_pool_size)
        print(f"[INIT] Loaded {len(train_pool_full)} from train (pool_size={train_pool_size})")
    else:
        train_pool_full = load_split(dataset, "train_pool", "train")
    dev_gate = load_split(dataset, "dev_gate", "test")
    test_pool = load_split(dataset, "test_pool", "test")

    # sample_ratio: 对所有数据集统一采样（用于快速测试）
    if sample_ratio and 0 < sample_ratio < 1.0:
        rng_sr = random.Random(evo_seed)
        train_pool_full = rng_sr.sample(train_pool_full, max(1, int(len(train_pool_full) * sample_ratio)))
        rng_sr2 = random.Random(evo_seed + 1)
        dev_gate = rng_sr2.sample(dev_gate, max(1, int(len(dev_gate) * sample_ratio)))
        rng_sr3 = random.Random(evo_seed + 2)
        test_pool = rng_sr3.sample(test_pool, max(1, int(len(test_pool) * sample_ratio)))
        print(f"[SAMPLE_RATIO] Applied {sample_ratio}: train_pool={len(train_pool_full)}, dev_gate={len(dev_gate)}, test_pool={len(test_pool)}")

    train_sample_size = evo_cfg.get("train_sample_size")
    if n_train_samples:
        train_sample_size = n_train_samples
    max_total_edits = evo_cfg.get("max_total_edits", 2)
    dev_decay_factor = evo_cfg.get("dev_decay_factor", 1.0)

    logger.info(f"Loop started: {loop_tag}, n_rounds={n_rounds}, dataset={dataset}, match_mode={match_mode}")
    logger.info(f"Splits: train_pool_full={len(train_pool_full)}, train_sample_size={train_sample_size}, dev_gate={len(dev_gate)}, test_pool={len(test_pool)}")
    logger.event("loop_start", loop_tag=loop_tag, payload={
        "n_rounds": n_rounds, "dataset": dataset, "match_mode": match_mode,
        "train_pool_full": len(train_pool_full), "train_sample_size": train_sample_size,
        "dev_gate": len(dev_gate), "test_pool": len(test_pool),
        "max_total_edits": max_total_edits, "dev_decay_factor": dev_decay_factor,
        "executor_model": CONFIG['model']['executor_model'],
        "critic_model": CONFIG['model']['critic_model'],
    })
    train_desc = f"train_sample={train_sample_size}/{len(train_pool_full)}" if train_sample_size else f"train_pool={len(train_pool_full)}"
    print(f"Dataset: {dataset} | {train_desc} | dev_gate={len(dev_gate)} | test_pool={len(test_pool)}")
    print(f"Config: max_total_edits={max_total_edits}, dev_decay_factor={dev_decay_factor}")

    # ── Initial Test Pool 评估 ──────────────────────────────
    if skip_initial_test and test_result_path and test_result_path.exists():
        # 从已有文件加载 initial test 结果
        saved = json.loads(test_result_path.read_text(encoding="utf-8"))
        test_metrics = saved.get("initial", {})
        test_results = []
        if not test_metrics or "em" not in test_metrics:
            print(f"[TEST] WARNING: test_result file missing 'initial' data, running initial test")
            executorAgent.update()
            test_metrics, test_results = eval_on(executorAgent, test_pool, desc="Initial test")
        else:
            print(f"[TEST] Skipped initial test, loaded from {test_result_path.name}")
            print(f"[TEST] EM: {test_metrics['em']:.2%} | F1: {test_metrics['f1']:.4f} | Sub-EM: {test_metrics['sub_em']:.2%}")
    else:
        print(f"\n{'='*60}")
        print("Initial Test Pool Evaluation")
        print(f"{'='*60}")
        executorAgent.update()
        test_metrics, test_results = eval_on(executorAgent, test_pool, desc="Initial test")
        test_path = executor_dir / "test_initial.jsonl"
        with test_path.open("w", encoding="utf-8") as f:
            for r in test_results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        logger.event("test_eval", loop_tag=loop_tag, round_i=0,
                      payload={"phase": "initial", "em": test_metrics["em"], "f1": test_metrics["f1"], "sub_em": test_metrics["sub_em"]})
        print(f"[TEST] EM: {test_metrics['em']:.2%} | F1: {test_metrics['f1']:.4f} | Sub-EM: {test_metrics['sub_em']:.2%}")
        # 保存初始测试结果到 test_result_path，供后续轮次跳过
        if test_result_path:
            save_data = {"initial": test_metrics}
            test_result_path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Memory Set ──────────────────────────────────────────
    memory_set: list[dict] = []
    memory_ids: set[str] = set()
    gate_cfg = CONFIG.get("gate", {})
    memory_sample_size = gate_cfg.get("memory_sample_size", 100)
    memory_threshold = gate_cfg.get("memory_threshold", 0.95)
    memory_persist_path = ROOT / "data" / dataset / "memory_set.jsonl"
    if memory_persist_path.exists():
        with memory_persist_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if item["id"] not in memory_ids:
                    memory_ids.add(item["id"])
                    memory_set.append(item)
        print(f"[MEMORY] Loaded {len(memory_set)} items from {memory_persist_path}")
    history = []
    cached_dev_metrics: dict | None = None
    consecutive_rejects = 0
    no_improve_limit = evo_cfg.get("stop_if_no_improvement_rounds", 5)
    memory_bad_cases: list[dict] = []

    for round_i in range(n_rounds):
        logger.info(f"{'='*40} Round {round_i + 1}/{n_rounds} {'='*40}")

        # ── 追加本轮 Executor Prompt 到 prompt.md ────────────
        with prompt_dump.open("a", encoding="utf-8") as pf:
            pf.write(f"\n# Round {round_i + 1} - Executor Prompt\n\n")
            pf.write("```\n")
            pf.write(executorAgent.prompt)
            pf.write("\n```\n\n---\n")
        print(f"[PROMPT] Round {round_i + 1} Executor prompt appended to {prompt_dump}")
        logger.event("round_start", loop_tag=loop_tag, round_i=round_i+1)
        print(f"\n{'='*60}")
        print(f"Round {round_i + 1}/{n_rounds}")
        print(f"{'='*60}")

        # ── Step 1: Train Pool rollout ──────────────────────
        if train_sample_size and train_sample_size < len(train_pool_full):
            rng_round = random.Random(evo_seed + round_i * 1000)
            train_pool = rng_round.sample(train_pool_full, train_sample_size)
            print(f"[SAMPLE] round_{round_i+1}: sampled {train_sample_size}/{len(train_pool_full)} from train_pool")
        else:
            train_pool = train_pool_full
        executorAgent.update()
        train_results, train_bad = executorAgent.run(train_pool, desc="Train pool")
        train_metrics = aggregate_metrics(train_results)
        print(f"\n{'='*60}")
        print("Train Pool Evaluation")
        print(f"{'='*60}")
        run_path = executor_dir / f"round_{round_i+1}.jsonl"
        bad_path = executor_dir / f"round_{round_i+1}_bad.jsonl"
        with run_path.open("w", encoding="utf-8") as f:
            for r in train_results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with bad_path.open("w", encoding="utf-8") as f:
            for r in train_bad:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        logger.event("executor_done", loop_tag=loop_tag, round_i=round_i+1, agent="executor",
                      model=CONFIG['model']['executor_model'],
                      payload={"score": f"{train_metrics['passed']}/{train_metrics['total']}",
                               "em": train_metrics["em"], "f1": train_metrics["f1"],
                               "sub_em": train_metrics["sub_em"], "n_bad": len(train_bad)})
        print(f"[TRAIN] {train_metrics['passed']}/{train_metrics['total']} | EM: {train_metrics['em']:.2%} | F1: {train_metrics['f1']:.4f} | Sub-EM: {train_metrics['sub_em']:.2%}")

        # ── Memory Set 增量更新 ─────────────────────────────
        new_count = 0
        for g in train_results:
            if g.get("passed") and g["id"] not in memory_ids:
                memory_ids.add(g["id"])
                memory_set.append({"id": g["id"], "question": g.get("question", ""),
                                    "answer": g.get("answer", g.get("expected", ""))})
                new_count += 1
        if new_count:
            with memory_persist_path.open("w", encoding="utf-8") as f:
                for item in memory_set:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            print(f"[MEMORY] +{new_count} new, total {len(memory_set)} -> {memory_persist_path}")

        history.append({
            "round": round_i + 1,
            "train_em": f"{train_metrics['em']:.2%}",
            "train_f1": f"{train_metrics['f1']:.4f}",
            "train_sub_em": f"{train_metrics['sub_em']:.2%}",
            "n_bad": len(train_bad),
            "gate": "-",
            "cand_dev_em": "-",
            "memory_em": "-",
        })

        if not train_bad:
            logger.info("All train pool passed, skip Critic & Evolver.")
            print("All train pool passed, skip Critic & Evolver.")
            continue

        # ── Step 2: Bad Case + Memory 错题 → Critic ─────────
        critic_bad = list(train_bad)
        if memory_bad_cases:
            existing_critic_ids = {c["id"] for c in critic_bad}
            mem_inject = [c for c in memory_bad_cases if c["id"] not in existing_critic_ids]
            critic_bad.extend(mem_inject)
            if mem_inject:
                print(f"[CRITIC] Injecting {len(mem_inject)} memory mistakes + {len(train_bad)} train bad = {len(critic_bad)} total")
            else:
                print(f"[CRITIC] Using all {len(train_bad)} bad cases (memory mistakes already in train bad)")
        else:
            print(f"[CRITIC] Using all {len(train_bad)} bad cases")

        # ── Step 3: Critic 归因分析 ─────────────────────────
        critic_path = critic_dir / f"round_{round_i+1}.jsonl"
        criticAgent.run(critic_bad, critic_path)
        logger.event("critic_done", loop_tag=loop_tag, round_i=round_i+1, agent="critic",
                      model=CONFIG['model']['critic_model'],
                      payload={"n_cases": len(critic_bad), "output": str(critic_path)})

        # ── Step 3.5: 全局聚合 ─────────────────────────────
        raw_reports = []
        with critic_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    raw_reports.append(json.loads(line))

        aggregated_edits = aggregate_critic_edits(raw_reports, max_total_edits=max_total_edits)
        agg_path = critic_dir / f"round_{round_i+1}_aggregated.jsonl"
        with agg_path.open("w", encoding="utf-8") as f:
            for edit in aggregated_edits:
                f.write(json.dumps(edit, ensure_ascii=False) + "\n")
        logger.event("aggregate_done", loop_tag=loop_tag, round_i=round_i+1,
                      payload={"raw_reports": len(raw_reports),
                               "aggregated_edits": len(aggregated_edits)})
        print(f"[AGGREGATE] {len(raw_reports)} reports -> {len(aggregated_edits)} unique edits (max={max_total_edits})")
        if not aggregated_edits:
            print("  [SKIP] No new edits after aggregation, skip Evolver.")
            continue

        # ── Step 4: Evolver 生成候选方案 ────────────────────
        print("\nEvolving...")
        try:
            evolution = evolverAgent.run(critic_path, aggregated_edits=aggregated_edits)
        except Exception as e:
            logger.error(f"Evolution failed: {e}")
            print(f"  [ERROR] Evolution failed: {e}")
            continue

        evolve_path = evolver_dir / f"round_{round_i+1}.json"
        evolve_path.write_text(json.dumps(evolution, ensure_ascii=False, indent=2), encoding="utf-8")
        n_actions = len(evolution.get("actions", []))
        logger.event("evolver_done", loop_tag=loop_tag, round_i=round_i+1, agent="evolver",
                      model=CONFIG['model']['evolver_model'],
                      payload={"n_actions": n_actions, "output": str(evolve_path)})
        print(f"evolution -> {evolve_path}")

        # ── Step 5: 保存候选 ────────────────────────────────
        candidate_dir = candidates_dir / f"round_{round_i+1}"
        candidate = evolverAgent.save_candidate(evolution, candidate_dir)
        candidate_prompt_path = candidate.get("prompt_path")
        candidate_skill_files = candidate.get("skill_files", {})

        has_prompt = candidate_prompt_path is not None
        has_skills = bool(candidate_skill_files)

        if not has_prompt and not has_skills:
            logger.info("No candidate changes generated, skip gate.")
            print("  [SKIP] No candidate changes generated, skip gate.")
            continue

        # ── Step 6: Dev Gate + Memory 验证 ──────────────────
        candidate_prompt_text = candidate_prompt_path.read_text(encoding="utf-8").strip() if has_prompt else None
        candidate_skill_content = executorAgent.build_candidate_skills(candidate_skill_files) if has_skills else None

        if cached_dev_metrics is not None:
            current_dev_metrics = cached_dev_metrics
            print(f"  [DEV CACHE] Reusing previous dev result: {current_dev_metrics['em']:.2%}")
        else:
            executorAgent.update()
            current_dev_metrics, _ = eval_on(executorAgent, dev_gate, desc="Current dev")

        if has_prompt and has_skills:
            executorAgent.update(new_prompt=candidate_prompt_text, skill_content=candidate_skill_content)
        elif has_prompt:
            executorAgent.update(new_prompt=candidate_prompt_text)
        elif has_skills:
            executorAgent.update(skill_content=candidate_skill_content)

        cand_dev_metrics, _ = eval_on(executorAgent, dev_gate, desc="Candidate dev")

        # ── Memory Set 验证 ────────────────────────────────
        memory_ok = True
        cand_memory_em = 1.0
        round_memory_bad: list[dict] = []
        if memory_set:
            rng_mem = random.Random(evo_seed + round_i * 7)
            sample_n = min(memory_sample_size, len(memory_set))
            memory_sample = rng_mem.sample(memory_set, sample_n)
            cand_memory_metrics, memory_results = eval_on(executorAgent, memory_sample, desc="Candidate memory")
            cand_memory_em = cand_memory_metrics["em"]
            memory_ok = cand_memory_em >= memory_threshold
            round_memory_bad = [r for r in memory_results if not r.get("passed")]

        # ── Step 7: Gate 决策 ───────────────────────────────
        base_dev_threshold = gate_cfg.get("dev_improve_threshold", 0.01)
        dev_threshold = base_dev_threshold * (dev_decay_factor ** round_i)
        dev_improved = cand_dev_metrics["em"] >= current_dev_metrics["em"] + dev_threshold
        accepted = dev_improved and memory_ok

        gate_record = {
            "round": round_i + 1,
            "dev_threshold": dev_threshold,
            "current_dev_em": current_dev_metrics["em"],
            "current_dev_f1": current_dev_metrics["f1"],
            "candidate_dev_em": cand_dev_metrics["em"],
            "candidate_dev_f1": cand_dev_metrics["f1"],
            "memory_size": len(memory_set),
            "memory_sample_n": min(memory_sample_size, len(memory_set)),
            "candidate_memory_em": cand_memory_em,
            "memory_threshold": memory_threshold,
            "memory_ok": memory_ok,
            "accepted": accepted,
            "has_prompt": has_prompt,
            "has_skills": has_skills,
        }
        gate_path = gate_dir / f"round_{round_i+1}.json"
        gate_path.write_text(json.dumps(gate_record, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.event("gate_decision", loop_tag=loop_tag, round_i=round_i+1, payload=gate_record)

        print(f"\n  [GATE] dev_threshold={dev_threshold:.4f} (base={base_dev_threshold}, decay={dev_decay_factor}^{round_i})")
        print(f"  [DEV]    current_em: {current_dev_metrics['em']:.2%} -> candidate_em: {cand_dev_metrics['em']:.2%} ({'OK' if dev_improved else 'NO IMPROVE'})")
        print(f"  [MEMORY] candidate_em: {cand_memory_em:.2%} (threshold={memory_threshold:.2%}, size={len(memory_set)}) ({'OK' if memory_ok else 'FORGOTTEN!'})")

        if accepted:
            evolverAgent.apply_candidate(
                candidate_prompt=candidate_prompt_text,
                skill_files=candidate_skill_files if has_skills else None,
            )
            executorAgent.update()
            cached_dev_metrics = cand_dev_metrics
            consecutive_rejects = 0

            if round_memory_bad:
                bad_ids = {r["id"] for r in round_memory_bad}
                before = len(memory_set)
                memory_set = [m for m in memory_set if m["id"] not in bad_ids]
                memory_ids -= bad_ids
                removed = before - len(memory_set)
                with memory_persist_path.open("w", encoding="utf-8") as f:
                    for item in memory_set:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
                print(f"  [MEMORY] Pruned {removed} forgotten items, {len(memory_set)} remain -> {memory_persist_path}")
                memory_bad_cases = list(round_memory_bad)

            logger.info(f"GATE ACCEPTED (dev: {current_dev_metrics['em']:.2%} -> {cand_dev_metrics['em']:.2%}, memory: {cand_memory_em:.2%} >= {memory_threshold:.2%})")
            print(f"  [GATE] ACCEPTED")
            # 复制最新 skill 到输出目录
            src = skill_opt_dir / f"{init_skill_name}.md"
            dst = loop_dir / "skillopt.md"
            if src.exists():
                shutil.copy2(str(src), str(dst))
                print(f"  [SKILL] Copied -> {dst}")
        else:
            executorAgent.update()
            cached_dev_metrics = current_dev_metrics
            consecutive_rejects += 1
            logger.info(f"GATE REJECTED (consecutive: {consecutive_rejects})")
            print(f"  [GATE] REJECTED, keeping current prompt & skills")
            memory_bad_cases = []

        logger.event("round_end", loop_tag=loop_tag, round_i=round_i+1, payload={"gate": "accepted" if accepted else "rejected"})
        history[-1]["gate"] = "ACCEPTED" if accepted else "REJECTED"
        history[-1]["cand_dev_em"] = f"{cand_dev_metrics['em']:.2%}"
        

        # ── Step 8: Planner 决策 ─────────────────────────────
        # 收集当前状态
        current_skill_content = ""
        if skill_opt_dir.exists():
            for f in sorted(skill_opt_dir.glob("*.md")):
                current_skill_content = f.read_text(encoding="utf-8").strip()
        current_instruction = instruction_path.read_text(encoding="utf-8").strip() if instruction_path and instruction_path.exists() else ""

        # skill diff: 新增内容 vs 上一轮
        skill_diff = ""
        if has_skills and accepted:
            for evolve_act in evolution.get("actions", []):
                if evolve_act.get("evolve_action") in ("update_skill", "add_skill"):
                    new_c = evolve_act.get("new_content", "")
                    old_c = executorAgent._load_skills()
                    # 简单 diff: 新内容中有但旧内容中没有的行
                    old_lines = set(old_c.split("\n"))
                    diff_lines = [l for l in new_c.split("\n") if l.strip() and l not in old_lines]
                    skill_diff = "\n".join(diff_lines[:20])
        elif has_skills and not accepted:
            skill_diff = "(Gate rejected, skill not changed)"

        # critic summary
        critic_summary = []
        for edit in aggregated_edits:
            critic_summary.append({
                "content": edit.get("content", "")[:200],
                "source_count": len(edit.get("source_ids", [])),
            })

        gate_reason = f"dev_em {current_dev_metrics['em']:.2%} -> {cand_dev_metrics['em']:.2%}, threshold={dev_threshold:.4f}, memory={cand_memory_em:.2%}"

        plan_record = build_plan_record(
            round_i=round_i + 1,
            skill_content=current_skill_content,
            instruction_content=current_instruction,
            train_metrics=train_metrics,
            dev_metrics=cand_dev_metrics,
            skill_diff=skill_diff,
            critic_summary=critic_summary,
            aggregated_edits=aggregated_edits,
            evolver_actions=evolution.get("actions", []),
            gate_decision="ACCEPTED" if accepted else "REJECTED",
            gate_reason=gate_reason,
            memory_em=cand_memory_em,
            plan_round=plan_round,
            action=action,
        )

        # 调用 Planner
        try:
            planner_decision = plannerAgent.run(plan_record)
        except Exception as e:
            print(f"  [WARN] Planner failed: {e}, defaulting to CONTINUE")
            planner_decision = {"next_action": "CONTINUE_SKILL_EVOLUTION", "reason": str(e), "evidence": [], "risk": "无"}

        plan_record["planner_decision"] = planner_decision
        with plan_history_path.open("a", encoding="utf-8") as pf:
            pf.write(json.dumps(plan_record, ensure_ascii=False) + "\n")

        print(f"  [PLAN] next_action={planner_decision['next_action']}")
        print(f"  [PLAN] reason={planner_decision.get('reason', '')[:100]}")
        logger.event("planner_decision", loop_tag=loop_tag, round_i=round_i+1, payload=planner_decision)

        if planner_decision["next_action"] == "STOP":
            logger.info(f"Planner decided STOP: {planner_decision.get('reason', '')}")
            print(f"\n[PLANNER STOP] {planner_decision.get('reason', '')}")
            break

        if no_improve_limit > 0 and consecutive_rejects >= no_improve_limit:
            logger.info(f"EARLY STOP: {consecutive_rejects} consecutive rounds with no improvement (limit={no_improve_limit})")
            print(f"\n[EARLY STOP] {consecutive_rejects} consecutive rounds with no improvement, stopping.")
            break

    # ── 合并 Skills ────────────────────────────────────────
    if skill_opt_dir.exists():
        all_skills = {}
        for f in sorted(skill_opt_dir.glob("*.md")):
            all_skills[f.stem] = f.read_text(encoding="utf-8").strip()
        merged = []
        if len(all_skills.keys()) > 1:
            for name in sorted(all_skills.keys()):
                merged.append(f"## {name}\n{all_skills[name]}")
        else:
            merged.append(list(all_skills.values())[0])
        (skill_opt_dir / "skillopt.md").write_text("\n\n".join(merged), encoding="utf-8")
        for f in skill_opt_dir.glob("*.md"):
            if f.name != "skillopt.md":
                f.unlink()
        logger.info(f"Skills consolidated into {skill_opt_dir / 'skillopt.md'}")

    # ── Final Test Pool 评估 ────────────────────────────────
    if skip_final_test:
        print(f"\n[TEST] Skipped final test (intermediate round)")
        final_test_metrics = None
    else:
        print(f"\n{'='*60}")
        print("Final Test Pool Evaluation")
        print(f"{'='*60}")
        executorAgent.update()
        final_test_metrics, final_test_results = eval_on(executorAgent, test_pool, desc="Final test")
        final_test_path = executor_dir / "test_final.jsonl"
        with final_test_path.open("w", encoding="utf-8") as f:
            for r in final_test_results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        logger.event("test_eval", loop_tag=loop_tag, round_i=n_rounds,
                      payload={"phase": "final", "em": final_test_metrics["em"],
                               "f1": final_test_metrics["f1"], "sub_em": final_test_metrics["sub_em"]})
        print(f"[TEST] EM: {final_test_metrics['em']:.2%} | F1: {final_test_metrics['f1']:.4f} | Sub-EM: {final_test_metrics['sub_em']:.2%}")

        # 保存到共享的 test_result_path
        if test_result_path:
            test_record = {
                "initial": test_metrics,
                "final": final_test_metrics,
                "delta_em": final_test_metrics["em"] - test_metrics["em"],
            }
            with test_result_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(test_record, ensure_ascii=False, indent=2) + "\n")
            print(f"[TEST] Results saved to {test_result_path}")

    # ── 总结 ────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("Evolution Summary")
    print(f"{'='*80}")
    print(f"  Initial Test: EM: {test_metrics['em']:.2%} | F1: {test_metrics['f1']:.4f} | Sub-EM: {test_metrics['sub_em']:.2%}")
    for h in history:
        print(f"  Round {h['round']}: train EM={h['train_em']} | F1={h['train_f1']} | bad={h['n_bad']} | gate={h['gate']} | cand_dev={h['cand_dev_em']} | memory={h.get('memory_em', '-')}")
    if final_test_metrics:
        print(f"  Final Test:   EM: {final_test_metrics['em']:.2%} | F1: {final_test_metrics['f1']:.4f} | Sub-EM: {final_test_metrics['sub_em']:.2%}")
        delta = final_test_metrics["em"] - test_metrics["em"]
        print(f"  Delta:        EM: {delta:+.2%}")
    else:
        print(f"  Final Test:   Skipped (intermediate round)")
    print(f"{'='*80}")

    history.append({"type": "test_initial", "em": test_metrics["em"], "f1": test_metrics["f1"], "sub_em": test_metrics["sub_em"]})
    if final_test_metrics:
        history.append({"type": "test_final", "em": final_test_metrics["em"], "f1": final_test_metrics["f1"], "sub_em": final_test_metrics["sub_em"]})
    logger.event("loop_end", loop_tag=loop_tag, payload={"history": history})
    logger.info(f"Loop finished: {loop_tag}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--samples", type=int, default=None, help="train_pool 采样数量（默认用全量）")
    parser.add_argument("--dataset", type=str, default=None, help="数据集名称: gsm8k, searchqa (默认从 config 读取)")
    parser.add_argument("--instruction", type=str, default=None, help="Instruction 文件路径，注入任务指令到 Executor prompt")
    args = parser.parse_args()
    ip = Path(args.instruction) if args.instruction else None
    run_loop(n_rounds=args.rounds, dataset=args.dataset, n_train_samples=args.samples, instruction_path=ip)
