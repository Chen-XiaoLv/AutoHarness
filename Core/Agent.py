from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI
from tqdm import tqdm

from Core.Metrics import evaluate, evaluate_text_gen


class Agent:
    def __init__(
        self,
        model: str,
        url: str,
        api_key: str,
        prompt: str | Path,
        temperature: float = 0.0,
        instruction_path: str | Path | None = None,
        timeout: float = 60.0,
    ):
        self.model = model
        self.url = url
        self.api_key = api_key
        self.prompt_path = prompt
        self.prompt_template = prompt.read_text(encoding="utf-8").strip()
        self.temperature = temperature
        self.client = OpenAI(
            api_key=api_key,
            base_url=url,
            timeout=timeout,
        )
        # 加载 Instruction（如果提供）
        self.instruction_path = Path(instruction_path) if instruction_path else None
        self.instruction_content = self._load_instruction()
        self.prompt = self._build_system()

    def _load_instruction(self) -> str:
        """加载 Instruction 文件内容。"""
        if self.instruction_path and self.instruction_path.exists():
            return self.instruction_path.read_text(encoding="utf-8").strip()
        return ""

    def _build_system(self, skill_content: str = "", fewshot_content: str = "") -> str:
        """渲染 prompt 模板，注入 instruction、skill_section 和 fewshot 插槽。"""
        if skill_content and skill_content.strip():
            # 去掉 skill 内容中已有的 # Skill / ## Skill 标题，避免与外层 ## Skill 重复
            import re
            cleaned = re.sub(r"^#{1,2}\s+Skill\s*\n*", "", skill_content.strip())
            skill_section = f"## Skill\n{cleaned}\n\n" if cleaned.strip() else ""
        else:
            skill_section = ""
        if fewshot_content and fewshot_content.strip():
            fewshot_text = fewshot_content.strip()
        else:
            fewshot_text = ""
        instruction = self.instruction_content if self.instruction_content else ""
        result = self.prompt_template
        result = result.replace("{instruction}", instruction)
        result = result.replace("{skill_section}", skill_section)
        result = result.replace("{fewshot_section}", fewshot_text)
        result = result.replace("{fewshot_content}", fewshot_text)
        return result

    def update(self, new_prompt: str | None = None, skill_content: str = "", fewshot_content: str = ""):
        if new_prompt is not None:
            self.prompt_template = new_prompt
        else:
            self.prompt_template = self.prompt_path.read_text(encoding="utf-8").strip()
        self.prompt = self._build_system(skill_content, fewshot_content)

    def call_llm(self, user_content: str, max_retries: int = 3, system_prompt: str | None = None) -> str:
        last_error = None
        sys_content = system_prompt if system_prompt is not None else self.prompt

        for attempt in range(max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": sys_content},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=self.temperature,
                )
                content = resp.choices[0].message.content
                if not content:
                    raise ValueError(f"{self.__class__.__name__} returned empty response")
                return content.strip()

            except Exception as e:
                last_error = e
                time.sleep(1.5 * (attempt + 1))

        raise last_error

    def vision(self, text: str, images: list[str], max_retries: int = 3) -> str:
        """多模态接口：文本 + base64 图片列表。images 为 JPEG base64 编码列表。"""
        # 构建 OpenAI 兼容的 vision 消息格式
        content = [{"type": "text", "text": text}]
        for img_b64 in images:
            if img_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                })

        last_error = None
        for attempt in range(max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.prompt},
                        {"role": "user", "content": content},
                    ],
                    temperature=self.temperature,
                )
                result = resp.choices[0].message.content
                if not result:
                    raise ValueError(f"{self.__class__.__name__} returned empty response")
                return result.strip()

            except Exception as e:
                last_error = e
                time.sleep(1.5 * (attempt + 1))

        raise last_error


class Executor(Agent):
    def __init__(self, *args, match_mode: str = "numeric", skills_dir: str | Path | None = None, init_skill_name: str | None = None, fewshot_content: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.match_mode = match_mode
        self.skills_dir = Path(skills_dir) if skills_dir else None
        self.init_skill_name = init_skill_name
        self.fewshot_content = fewshot_content
        # 用 skills 重新渲染 prompt
        self.prompt = self._build_system(self._load_skills(), fewshot_content)

    def _load_skills(self) -> str:
        """从 skills_dir 加载所有 .md Skill 文件并拼接。"""
        if not self.skills_dir or not self.skills_dir.exists():
            return ""
        parts = []
        for f in sorted(self.skills_dir.glob("*.md")):
            parts.append(f.read_text(encoding="utf-8").strip())
        return "\n\n".join(parts)

    def update(self, new_prompt: str | None = None, skill_content: str | None = None, fewshot_content: str = ""):
        if new_prompt is not None:
            self.prompt_template = new_prompt
        else:
            self.prompt_template = self.prompt_path.read_text(encoding="utf-8").strip()
        if skill_content is None:
            skill_content = self._load_skills()
        if not fewshot_content:
            fewshot_content = self.fewshot_content
        self.prompt = self._build_system(skill_content, fewshot_content)

    def build_candidate_skills(self, skill_files: dict[str, str]) -> str:
        """将当前 Skills 与候选变更合并，生成候选 skill_content。"""
        current: dict[str, str] = {}
        if self.skills_dir and self.skills_dir.exists():
            for f in self.skills_dir.glob("*.md"):
                current[f.stem] = f.read_text(encoding="utf-8").strip()

        for name, content in skill_files.items():
            # 初始 skill 的候选键名映射到 skillopt
            mapped = "skillopt" if name == self.init_skill_name else name
            if content == "__DELETE__":
                current.pop(mapped, None)
            else:
                current[mapped] = content

        parts = []
        for name in sorted(current.keys()):
            parts.append(f"## {name}\n{current[name]}")
        return "\n\n".join(parts)

    def _run_one(self, item: dict[str, Any]) -> dict[str, Any]:
        question = item["question"]
        raw_answer = item["answer"]
        gold_answers = [a.strip() for a in raw_answer.split("####") if a.strip()]

        try:
            # vision 模式：item 含 frames 字段且 match_mode == text_gen
            if self.match_mode == "text_gen" and "frames" in item:
                pred = self.vision(question, item["frames"])
                metrics = evaluate_text_gen(pred, raw_answer.strip())
                result = {
                    "id": item["id"],
                    "question": question,
                    "expected": raw_answer.strip(),
                    "prediction": pred,
                    "predicted_answer": metrics["predicted_answer"],
                    "rouge_l": metrics["rouge_l"],
                    "bleu_1": metrics["bleu_1"],
                    "passed": metrics["passed"],
                }
            else:
                pred = self.call_llm(question)
                metrics = evaluate(pred, gold_answers)
                result = {
                    "id": item["id"],
                    "question": question,
                    "expected": raw_answer.strip(),
                    "prediction": pred,
                    "predicted_answer": metrics["predicted_answer"],
                    "em": metrics["em"],
                    "f1": metrics["f1"],
                    "sub_em": metrics["sub_em"],
                    "passed": metrics["em"] == 1.0,
                }
            return result

        except Exception as e:
            base = {
                "id": item.get("id"),
                "question": question,
                "expected": raw_answer.strip(),
                "prediction": "",
                "predicted_answer": "",
                "passed": False,
                "error": str(e),
            }
            if self.match_mode == "text_gen":
                base.update({"rouge_l": 0.0, "bleu_1": 0.0})
            else:
                base.update({"em": 0.0, "f1": 0.0, "sub_em": 0.0})
            return base

    def run(
        self,
        items: list[dict[str, Any]],
        desc: str = "Evaluating",
        max_workers: int = 20,
    ) -> tuple[list, list]:
        results = []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(self._run_one, item) for item in items]

            for future in tqdm(as_completed(futures), total=len(futures), desc=desc):
                results.append(future.result())

        results.sort(key=lambda x: str(x.get("id", "")))
        bad_cases = [r for r in results if not r.get("passed")]

        return results, bad_cases


class Critic(Agent):
    def __init__(
        self,
        *args,
        minibatch_size: int = 4,
        edit_budget: int = 3,
        seed: int = 42,
        skills_dir: str | Path | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.minibatch_size = minibatch_size
        self.edit_budget = edit_budget
        self.seed = seed
        self.skills_dir = Path(skills_dir) if skills_dir else None

    def _build_minibatch_input(self, batch: list[dict[str, Any]]) -> str:
        """构建 minibatch 的上下文包，含当前 skill 和多条失败轨迹摘要。"""
        skill_content = ""
        if self.skills_dir and self.skills_dir.exists():
            parts = []
            for f in sorted(self.skills_dir.glob("*.md")):
                parts.append(f.read_text(encoding="utf-8").strip())
            skill_content = "\n\n".join(parts)

        summaries = []
        for case in batch:
            summaries.append({
                "id": case.get("id"),
                "question": str(case.get("question", ""))[:200],
                "expected": str(case.get("expected", "")),
                "predicted_answer": str(case.get("predicted_answer", case.get("prediction", "")))[:200],
                "em": case.get("em", 0),
                "f1": case.get("f1", 0),
            })

        return json.dumps({
            "current_skill": skill_content,
            "edit_budget": self.edit_budget,
            "batch_size": len(batch),
            "failed_cases": summaries,
        }, ensure_ascii=False)

    def _critic_batch(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        """对一个 minibatch 执行批量 Critic。bad case 通过 user message 传入。"""
        case_ids = [c.get("id") for c in batch]
        try:
            user_input = self._build_minibatch_input(batch)
            raw = self.call_llm(user_input)
            report = safe_json_loads(raw)
            report["batch_case_ids"] = case_ids
            report["batch_size"] = len(batch)
            return report
        except Exception as e:
            print(f"  [WARN] Critic batch parse failed for {case_ids[:3]}...: {e}")
            return {
                "batch_case_ids": case_ids,
                "batch_size": len(batch),
                "error": str(e),
            }

    @staticmethod
    def _deduplicate_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """后过滤/去重：过滤 replace_full，合并高度相似的 append 规则。"""
        seen_contents: list[str] = []
        for report in reports:
            if "error" in report:
                continue
            patch = report.get("patch", {})
            if not patch:
                continue
            edits = patch.get("edits", [])
            # 强制过滤：只保留 append 操作
            edits = [e for e in edits if e.get("op") == "append"]
            unique_edits = []
            for edit in edits:
                content = edit.get("content", "").strip()
                if not content:
                    continue
                # 简单去重：规范化后比较
                normalized = " ".join(content.lower().split())
                is_dup = False
                for seen in seen_contents:
                    # 如果新规则是已见规则的子串或高度重叠，跳过
                    if normalized in seen or seen in normalized:
                        is_dup = True
                        break
                    # token 重叠率 > 80% 视为重复
                    tokens_a = set(normalized.split())
                    tokens_b = set(seen.split())
                    if tokens_a and tokens_b:
                        overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
                        if overlap > 0.8:
                            is_dup = True
                            break
                if not is_dup:
                    unique_edits.append(edit)
                    seen_contents.append(normalized)
            patch["edits"] = unique_edits
        return reports

    def run(
        self,
        bad_cases: list[dict[str, Any]],
        candidate: Path,
    ) -> list[dict[str, Any]]:
        # 固定 seed shuffle 后切桶
        rng = random.Random(self.seed)
        shuffled = list(bad_cases)
        rng.shuffle(shuffled)

        batches = []
        for i in range(0, len(shuffled), self.minibatch_size):
            batches.append(shuffled[i : i + self.minibatch_size])

        print(f"  [CRITIC] {len(bad_cases)} bad cases -> {len(batches)} minibatches (size={self.minibatch_size})")

        # 并行批量 Critic
        critic_reports = []
        max_workers = getattr(self, 'max_workers', 20)
        with ThreadPoolExecutor(max_workers=min(len(batches), max_workers)) as pool:
            futures = [pool.submit(self._critic_batch, batch) for batch in batches]
            for future in tqdm(as_completed(futures), total=len(futures), desc="Critic"):
                critic_reports.append(future.result())

        # 后过滤/去重
        critic_reports = self._deduplicate_reports(critic_reports)

        # 保存
        candidate.parent.mkdir(parents=True, exist_ok=True)
        with candidate.open("w", encoding="utf-8") as f:
            for report in critic_reports:
                f.write(json.dumps(report, ensure_ascii=False) + "\n")

        n_valid = sum(1 for r in critic_reports if "error" not in r)
        print(f"  [CRITIC] {n_valid}/{len(batches)} batches produced valid reports")
        print(f"critic_report -> {candidate}")
        return critic_reports


class Evolver(Agent):
    def __init__(
        self,
        model: str,
        url: str,
        api_key: str,
        prompt: str | Path,
        prompt_path: Path,
        skills_dir: str | Path | None = None,
        temperature: float = 0.0,
        init_skill_name: str | None = None,
        instruction_path: str | Path | None = None,
        plan_suggestion: str = "",
        timeout: float = 60.0,
    ):
        # 必须在 super().__init__() 之前设置，因为父类 __init__ 会调用 _build_system()
        self.plan_suggestion = plan_suggestion
        super().__init__(
            model=model,
            url=url,
            api_key=api_key,
            prompt=prompt,
            temperature=temperature,
            instruction_path=instruction_path,
            timeout=timeout,
        )
        self.prompt_path = prompt_path
        self.skills_dir = Path(skills_dir) if skills_dir else None
        self.init_skill_name = init_skill_name

    def _build_system(self, skill_content: str = "") -> str:
        """渲染 prompt 模板，注入 instruction、skill_section 和 plan_suggestion 插槽。"""
        if skill_content and skill_content.strip():
            skill_section = f"## Skill\n{skill_content.strip()}\n\n"
        else:
            skill_section = ""
        instruction = self.instruction_content if self.instruction_content else ""
        result = self.prompt_template
        result = result.replace("{instruction}", instruction)
        result = result.replace("{skill_section}", skill_section)
        result = result.replace("{plan_suggestion}", self.plan_suggestion)
        return result

    def run(self, critic_reports_path: Path, aggregated_edits: list[dict] | None = None) -> dict[str, Any]:
        executor_prompt = self.prompt_path.read_text(encoding="utf-8").strip()

        # 加载当前 Skills
        current_skills = ""
        if self.skills_dir and self.skills_dir.exists():
            skill_files = sorted(self.skills_dir.glob("*.md"))
            parts = []
            if len(skill_files) > 1:
                for f in skill_files:
                    parts.append(f"### {f.stem}\n{f.read_text(encoding='utf-8').strip()}")
            else:
                for f in skill_files:
                    parts.append(f.read_text(encoding='utf-8').strip())
            current_skills = "\n\n".join(parts)

        if aggregated_edits:
            # 使用聚合后的 edits（全局去重+截断后的结果）
            evolver_input = {
                "current_prompt": executor_prompt,
                "current_skills": current_skills,
                "aggregated_edits": aggregated_edits,
                "critic_reports": [],
                "aggregated_failure_summary": [],
            }
        else:
            # 兜底：读取原始 reports 并自行聚合（向后兼容）
            reports = []
            with critic_reports_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    reports.append(r)

            valid_reports = [r for r in reports if "error" not in r]
            if not valid_reports:
                return {"actions": []}

            all_summaries = []
            all_edits = []
            for report in valid_reports:
                for fs in report.get("failure_summary", []):
                    all_summaries.append(fs)
                patch = report.get("patch", {})
                for edit in patch.get("edits", []):
                    all_edits.append(edit)

            evolver_input = {
                "current_prompt": executor_prompt,
                "current_skills": current_skills,
                "critic_reports": valid_reports,
                "aggregated_failure_summary": all_summaries,
                "aggregated_edits": all_edits,
            }

        # critic patch 通过 user message 传入
        raw = self.call_llm(json.dumps(evolver_input, ensure_ascii=False))
        try:
            result = safe_json_loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  [WARN] Evolver JSON parse failed: {e}")
            print(f"  [WARN] Raw response (first 500 chars): {raw[:500]}")
            return {"actions": []}

        # 兼容旧格式：如果 LLM 输出了 updated_skill 而不是 actions
        if "updated_skill" in result and "actions" not in result:
            result = {
                "actions": [{
                    "target_component": "skill",
                    "evolve_action": "update_skill",
                    "skill_name": "skillopt",
                    "new_content": result["updated_skill"],
                }]
            }
        return result

    def save_candidate(self, evolution: dict, save_dir: Path) -> dict[str, Any]:
        """
        保存候选 prompt 和 skill 到 save_dir。
        返回: {"prompt_path": Path|None, "skill_files": {name: content}}
        """
        save_dir.mkdir(parents=True, exist_ok=True)
        current_prompt = self.prompt_path.read_text(encoding="utf-8").strip()

        result: dict[str, Any] = {"prompt_path": None, "skill_files": {}}

        for action in evolution.get("actions", []):
            target = action.get("target_component")

            if target == "prompt":
                new_content = action.get("new_content", "").strip()
                if not new_content:
                    continue
                if new_content == current_prompt:
                    print("  [SKIP] Candidate prompt is identical to current prompt.")
                    continue
                prompt_path = save_dir / "AGENT.md"
                prompt_path.write_text(new_content, encoding="utf-8")
                result["prompt_path"] = prompt_path
                print(f"  [CANDIDATE] Prompt saved -> {prompt_path}")

            elif target == "skill":
                self._save_skill_action(action, save_dir, result)

        return result

    def _save_skill_action(self, action: dict, save_dir: Path, result: dict):
        """处理单个 skill action，保存候选 skill 文件。"""
        act = action.get("evolve_action", "")
        name = action.get("skill_name", "").strip()
        content = action.get("new_content", "").strip()

        if not name:
            return

        skills_dir = save_dir / "Skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        # 初始 skill 的候选重定向到 skillopt.md
        if name == self.init_skill_name:
            skill_path = skills_dir / "skillopt.md"
        else:
            skill_path = skills_dir / f"{name}.md"

        if act in ("add_skill", "update_skill") and content:
            skill_path.write_text(content, encoding="utf-8")
            result["skill_files"][name] = content
            print(f"  [CANDIDATE] Skill {act}: {name} -> {skill_path}")
        elif act == "delete_skill":
            result["skill_files"][name] = "__DELETE__"
            print(f"  [CANDIDATE] Skill delete: {name}")

    @staticmethod
    def gate_decision(current_score: float, candidate_score: float) -> bool:
        return candidate_score >= current_score

    def apply_candidate(self, candidate_prompt: str | None = None, skill_files: dict[str, str] | None = None):
        """将通过 Gate 的候选 prompt 和 skills 写入 Executor 目录。"""
        if candidate_prompt:
            self.prompt_path.parent.mkdir(parents=True, exist_ok=True)
            self.prompt_path.write_text(candidate_prompt, encoding="utf-8")

        if skill_files and self.skills_dir:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            for name, content in skill_files.items():
                # 初始 skill 的更新重定向到 skillopt.md
                if name == self.init_skill_name:
                    target = self.skills_dir / "skillopt.md"
                else:
                    target = self.skills_dir / f"{name}.md"

                if content == "__DELETE__":
                    if target.exists():
                        target.unlink()
                        print(f"  [SKILL] Deleted: {target}")
                else:
                    target.write_text(content, encoding="utf-8")
                    print(f"  [SKILL] Applied: {target}")


def safe_json_loads(raw: str) -> dict[str, Any]:
    raw = raw.strip()

    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            return json.loads(raw[start:end + 1])
        raise


class Planner(Agent):
    """根据每轮 Rollout 结果，决定下一轮优化方向。"""

    def _build_system(self, skill_content: str = "", fewshot_content: str = "") -> str:
        result = super()._build_system(skill_content, fewshot_content)
        contract = self.instruction_content if self.instruction_content else "(未提供 Contract)"
        return result.replace("{contract}", contract)

    def run(self, plan_record: dict[str, Any]) -> dict[str, Any]:
        """将本轮状态发给 LLM，返回 next_action 决策。"""
        raw = self.call_llm(json.dumps(plan_record, ensure_ascii=False))
        try:
            result = safe_json_loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  [WARN] Planner JSON parse failed: {e}")
            return {"next_action": "CONTINUE_SKILL_EVOLUTION", "reason": "JSON 解析失败，默认继续 Skill 迭代", "evidence": [], "risk": "无"}
        # 校验 next_action 合法性
        valid_actions = {"CONTINUE_SKILL_EVOLUTION", "ADD_OR_UPDATE_FEWSHOT", "RERUN_EVALUATION", "STOP"}
        if result.get("next_action") not in valid_actions:
            print(f"  [WARN] Planner returned invalid action: {result.get('next_action')}, defaulting to CONTINUE_SKILL_EVOLUTION")
            result["next_action"] = "CONTINUE_SKILL_EVOLUTION"
        return result
