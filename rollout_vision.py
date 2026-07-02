import json
import random
from datetime import datetime
from pathlib import Path
from Core.Logger import HarnessLogger
from Core.func import (
    ROOT, CONFIG, init_skill_opt, create_agents,
    load_split, eval_on, aggregate_metrics, aggregate_critic_edits,
)


def run_loop(n_rounds: int = 10, dataset: str = None, n_train_samples: int = None):
    ds_cfg = CONFIG.get("dataset", {})
    if not dataset:
        dataset = ds_cfg.get("active", "gsm8k")
    evo_seed = CONFIG.get("evolution", {}).get("seed", 42)

    # ── 初始化 SkillOpt ─────────────────────────────────────
    skill_opt_dir, init_skill_name = init_skill_opt(dataset)

    executorAgent, criticAgent, evolverAgent, match_mode = create_agents(
        dataset, skill_opt_dir=skill_opt_dir, init_skill_name=init_skill_name,
    )

    loop_tag = datetime.now().strftime("%m%d_%H%M")
    loop_dir = ROOT / CONFIG['path']['out_dir'] / loop_tag

    executor_dir = loop_dir / "Executor"
    critic_dir = loop_dir / "Critic"
    evolver_dir = loop_dir / "Evolver"
    candidates_dir = loop_dir / "Candidates"
    gate_dir = loop_dir / "Gate"
    for d in (executor_dir, critic_dir, evolver_dir, candidates_dir, gate_dir):
        d.mkdir(parents=True, exist_ok=True)

    logger = HarnessLogger(
        log_file=loop_dir / "harness.log",
        event_file=loop_dir / "events.jsonl",
        level=CONFIG.get("logging", {}).get("level", "INFO"),
    )

    # ── 加载数据集 ─────────────────────────────────────────
    evo_cfg = CONFIG.get("evolution", {})
    train_pool_size = evo_cfg.get("train_pool_size")
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
                  payload={"phase": "initial", **test_metrics})
    _m = test_metrics
    if "rouge_l" in _m:
        print(f"[TEST] ROUGE-L: {_m['rouge_l']:.4f} | BLEU-1: {_m['bleu_1']:.4f}")
    else:
        print(f"[TEST] EM: {_m['em']:.2%} | F1: {_m['f1']:.4f} | Sub-EM: {_m['sub_em']:.2%}")

    # ── Memory Set (视觉模式跳过) ──────────────────────────
    is_vision = match_mode == "text_gen"
    memory_set: list[dict] = []
    memory_ids: set[str] = set()
    gate_cfg = CONFIG.get("gate", {})
    memory_sample_size = gate_cfg.get("memory_sample_size", 100)
    memory_threshold = gate_cfg.get("memory_threshold", 0.95)
    memory_persist_path = ROOT / "data" / dataset / "memory_set.jsonl"
    if not is_vision and memory_persist_path.exists():
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
    elif is_vision:
        print("[MEMORY] Vision mode: Memory Set disabled (images too large)")
    history = []
    cached_dev_metrics: dict | None = None
    consecutive_rejects = 0
    no_improve_limit = evo_cfg.get("stop_if_no_improvement_rounds", 5)
    memory_bad_cases: list[dict] = []

    for round_i in range(n_rounds):
        logger.info(f"{'='*40} Round {round_i + 1}/{n_rounds} {'='*40}")
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
                               **train_metrics, "n_bad": len(train_bad)})
        if "rouge_l" in train_metrics:
            print(f"[TRAIN] {train_metrics['passed']}/{train_metrics['total']} | ROUGE-L: {train_metrics['rouge_l']:.4f} | BLEU-1: {train_metrics['bleu_1']:.4f}")
        else:
            print(f"[TRAIN] {train_metrics['passed']}/{train_metrics['total']} | EM: {train_metrics['em']:.2%} | F1: {train_metrics['f1']:.4f} | Sub-EM: {train_metrics['sub_em']:.2%}")

        # ── Memory Set 增量更新 ─────────────────────────────
        new_count = 0
        if not is_vision:
            for g in train_results:
                if g.get("passed") and g["id"] not in memory_ids:
                    memory_ids.add(g["id"])
                    mem_item = {"id": g["id"], "question": g.get("question", ""),
                                "answer": g.get("answer", g.get("expected", ""))}
                    if "frames" in g:
                        mem_item["frames"] = g["frames"]
                    elif "frames" in next((x for x in train_pool if x["id"] == g["id"]), {}):
                        mem_item["frames"] = next(x for x in train_pool if x["id"] == g["id"])["frames"]
                    memory_set.append(mem_item)
                    new_count += 1
            if new_count:
                with memory_persist_path.open("w", encoding="utf-8") as f:
                    for item in memory_set:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
                print(f"[MEMORY] +{new_count} new, total {len(memory_set)} -> {memory_persist_path}")

        if "rouge_l" in train_metrics:
            history.append({
                "round": round_i + 1,
                "train_rouge_l": f"{train_metrics['rouge_l']:.4f}",
                "train_bleu_1": f"{train_metrics['bleu_1']:.4f}",
                "n_bad": len(train_bad),
                "gate": "-",
                "cand_dev_rouge_l": "-",
                "memory_rouge_l": "-",
            })
        else:
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
            _dev_key = "rouge_l" if "rouge_l" in current_dev_metrics else "em"
            print(f"  [DEV CACHE] Reusing previous dev result: {current_dev_metrics[_dev_key]:.4f}")
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

        # 主指标：text_gen 用 rouge_l，QA 用 em
        _dev_key = "rouge_l" if "rouge_l" in cand_dev_metrics else "em"

        # ── Memory Set 验证 ────────────────────────────────
        memory_ok = True
        cand_memory_score = 1.0
        round_memory_bad: list[dict] = []
        if memory_set and not is_vision:
            rng_mem = random.Random(evo_seed + round_i * 7)
            sample_n = min(memory_sample_size, len(memory_set))
            memory_sample = rng_mem.sample(memory_set, sample_n)
            cand_memory_metrics, memory_results = eval_on(executorAgent, memory_sample, desc="Candidate memory")
            cand_memory_score = cand_memory_metrics.get(_dev_key, cand_memory_metrics.get("em", 0.0))
            memory_ok = cand_memory_score >= memory_threshold
            round_memory_bad = [r for r in memory_results if not r.get("passed")]

        # ── Step 7: Gate 决策 ───────────────────────────────
        if is_vision:
            dev_threshold = 0.0
            dev_improved = cand_dev_metrics[_dev_key] >= current_dev_metrics[_dev_key]
        else:
            base_dev_threshold = gate_cfg.get("dev_improve_threshold", 0.01)
            dev_threshold = base_dev_threshold * (dev_decay_factor ** round_i)
            dev_improved = cand_dev_metrics[_dev_key] >= current_dev_metrics[_dev_key] + dev_threshold
        accepted = dev_improved and memory_ok

        gate_record = {
            "round": round_i + 1,
            "dev_threshold": dev_threshold,
            f"current_dev_{_dev_key}": current_dev_metrics[_dev_key],
            f"candidate_dev_{_dev_key}": cand_dev_metrics[_dev_key],
            "memory_size": len(memory_set),
            "memory_sample_n": min(memory_sample_size, len(memory_set)),
            f"candidate_memory_{_dev_key}": cand_memory_score,
            "memory_threshold": memory_threshold,
            "memory_ok": memory_ok,
            "accepted": accepted,
            "has_prompt": has_prompt,
            "has_skills": has_skills,
        }
        gate_path = gate_dir / f"round_{round_i+1}.json"
        gate_path.write_text(json.dumps(gate_record, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.event("gate_decision", loop_tag=loop_tag, round_i=round_i+1, payload=gate_record)

        print(f"\n  [GATE] dev_threshold={dev_threshold:.4f}")
        print(f"  [DEV]    current_{_dev_key}: {current_dev_metrics[_dev_key]:.4f} -> candidate_{_dev_key}: {cand_dev_metrics[_dev_key]:.4f} ({'OK' if dev_improved else 'NO IMPROVE'})")
        if not is_vision:
            print(f"  [MEMORY] candidate_{_dev_key}: {cand_memory_score:.4f} (threshold={memory_threshold:.4f}, size={len(memory_set)}) ({'OK' if memory_ok else 'FORGOTTEN!'})")

        if accepted:
            evolverAgent.apply_candidate(
                candidate_prompt=candidate_prompt_text,
                skill_files=candidate_skill_files if has_skills else None,
            )
            executorAgent.update()
            cached_dev_metrics = cand_dev_metrics
            consecutive_rejects = 0

            if round_memory_bad and not is_vision:
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

            logger.info(f"GATE ACCEPTED (dev: {current_dev_metrics[_dev_key]:.4f} -> {cand_dev_metrics[_dev_key]:.4f}, memory: {cand_memory_score:.4f} >= {memory_threshold:.4f})")
            print(f"  [GATE] ACCEPTED")
        else:
            executorAgent.update()
            cached_dev_metrics = current_dev_metrics
            consecutive_rejects += 1
            logger.info(f"GATE REJECTED (consecutive: {consecutive_rejects})")
            print(f"  [GATE] REJECTED, keeping current prompt & skills")
            memory_bad_cases = []

        logger.event("round_end", loop_tag=loop_tag, round_i=round_i+1, payload={"gate": "accepted" if accepted else "rejected"})
        history[-1]["gate"] = "ACCEPTED" if accepted else "REJECTED"
        history[-1][f"cand_dev_{_dev_key}"] = f"{cand_dev_metrics[_dev_key]:.4f}"
        history[-1][f"memory_{_dev_key}"] = f"{cand_memory_score:.4f}"

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
                  payload={"phase": "final", **final_test_metrics})
    _m = final_test_metrics
    if "rouge_l" in _m:
        print(f"[TEST] ROUGE-L: {_m['rouge_l']:.4f} | BLEU-1: {_m['bleu_1']:.4f}")
    else:
        print(f"[TEST] EM: {_m['em']:.2%} | F1: {_m['f1']:.4f} | Sub-EM: {_m['sub_em']:.2%}")

    # ── 总结 ────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("Evolution Summary")
    print(f"{'='*80}")
    _init_m = test_metrics
    _fin_m = final_test_metrics
    is_text_gen = "rouge_l" in _init_m
    if is_text_gen:
        print(f"  Initial Test: ROUGE-L: {_init_m['rouge_l']:.4f} | BLEU-1: {_init_m['bleu_1']:.4f}")
        for h in history:
            print(f"  Round {h['round']}: train RL={h.get('train_rouge_l','?')} | B1={h.get('train_bleu_1','?')} | bad={h['n_bad']} | gate={h['gate']} | cand_dev={h.get('cand_dev_rouge_l','-')} | memory={h.get('memory_rouge_l','-')}")
        print(f"  Final Test:   ROUGE-L: {_fin_m['rouge_l']:.4f} | BLEU-1: {_fin_m['bleu_1']:.4f}")
        delta = _fin_m["rouge_l"] - _init_m["rouge_l"]
        print(f"  Delta:        ROUGE-L: {delta:+.4f}")
    else:
        print(f"  Initial Test: EM: {_init_m['em']:.2%} | F1: {_init_m['f1']:.4f} | Sub-EM: {_init_m['sub_em']:.2%}")
        for h in history:
            print(f"  Round {h['round']}: train EM={h['train_em']} | F1={h['train_f1']} | bad={h['n_bad']} | gate={h['gate']} | cand_dev={h['cand_dev_em']} | memory={h.get('memory_em','-')}")
        print(f"  Final Test:   EM: {_fin_m['em']:.2%} | F1: {_fin_m['f1']:.4f} | Sub-EM: {_fin_m['sub_em']:.2%}")
        delta = _fin_m["em"] - _init_m["em"]
        print(f"  Delta:        EM: {delta:+.2%}")
    print(f"{'='*80}")

    history.append({"type": "test_initial", **test_metrics})
    history.append({"type": "test_final", **final_test_metrics})
    logger.event("loop_end", loop_tag=loop_tag, payload={"history": history})
    logger.info(f"Loop finished: {loop_tag}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--samples", type=int, default=None, help="train_pool 采样数量（默认用全量）")
    parser.add_argument("--dataset", type=str, default=None, help="数据集名称: gsm8k, searchqa (默认从 config 读取)")
    args = parser.parse_args()
    run_loop(n_rounds=args.rounds, dataset=args.dataset, n_train_samples=args.samples)
