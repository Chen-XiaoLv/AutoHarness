"""快速测试：用 getContract.md + 获胜假设生成 Task Contract"""
import json
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).parent

# 路径配置
AUTO_DIR = ROOT / "Agent" / "Auto"
OUTPUT_DIR = ROOT / "auto_outputs"
TASK_OUTPUT_PATH = OUTPUT_DIR / "task_output.json"
LEADERBOARD_PATH = OUTPUT_DIR / "leaderboard.json"
CONTRACT_PATH = OUTPUT_DIR / "TASK_Contract.md"
META_PATH = OUTPUT_DIR / "contract_meta.json"

# LLM 客户端
client = OpenAI(
    api_key="sk-c61yq65rikst7cavlbi29fptekl1xz1fm2u0vo41keibhrjs",
    base_url="https://api.xiaomimimo.com/v1",
    timeout=120.0,
)
MODEL = "mimo-v2.5"


def call_llm(system_prompt: str, user_content: str, temperature: float = 0.1) -> str:
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
    return content.strip()


def find_best_hypothesis(task_output: dict, leaderboard: dict) -> dict:
    """从 task_output 和 leaderboard 中找到得分最高的假设。"""
    hypotheses = task_output.get("hypotheses", [])
    
    # 找到 EM 最高的假设
    best_h = None
    best_em = -1
    
    for h in hypotheses:
        hid = h.get("id", h.get("hypothesis_id", ""))
        if hid in leaderboard:
            em = leaderboard[hid].get("em", 0)
            if em > best_em:
                best_em = em
                best_h = h
    
    # 如果没有找到，尝试用第一个假设
    if not best_h and hypotheses:
        best_h = hypotheses[0]
    
    return best_h


def generate_contract(output_dir: Path = None):
    """生成 Task Contract。"""
    out_dir = output_dir or OUTPUT_DIR
    task_output_path = out_dir / "task_output.json"
    leaderboard_path = out_dir / "leaderboard.json"
    contract_path = out_dir / "TASK_Contract.md"
    meta_path = out_dir / "contract_meta.json"

    # 检查必要文件
    if not task_output_path.exists():
        print(f"  [ERROR] task_output.json not found at {task_output_path}")
        return
    
    # 读取 task_output
    task_output = json.loads(task_output_path.read_text(encoding="utf-8"))
    
    # 读取 leaderboard
    leaderboard = {}
    if leaderboard_path.exists():
        leaderboard = json.loads(leaderboard_path.read_text(encoding="utf-8"))
    
    # 找到最佳假设
    best_hypothesis = find_best_hypothesis(task_output, leaderboard)
    if not best_hypothesis:
        print("  [ERROR] No hypothesis found")
        return
    
    best_id = best_hypothesis.get("id", best_hypothesis.get("hypothesis_id", "?"))
    print(f"  Best hypothesis: {best_id}")
    print(f"  Task type: {best_hypothesis.get('task_type', '?')}")
    
    # 读取 GetContract.md
    contract_prompt_path = AUTO_DIR / "GetContract.md"
    if not contract_prompt_path.exists():
        print(f"  [WARN] GetContract.md not found at {contract_prompt_path}")
        return
    
    contract_prompt = contract_prompt_path.read_text(encoding="utf-8").strip()
    
    # 调用 LLM 生成 Contract
    print("  Generating Contract...")
    contract_user = json.dumps(best_hypothesis, ensure_ascii=False, indent=2)
    contract_md = call_llm(contract_prompt, contract_user)
    
    # 保存 Contract
    contract_path.write_text(contract_md, encoding="utf-8")
    print(f"  Contract saved to {contract_path}")
    
    # 保存来源元数据
    meta = {
        "version": "task_v1",
        "source_hypothesis": best_id,
        "status": "provisional",
        "probe_em": leaderboard.get(best_id, {}).get("em"),
        "probe_f1": leaderboard.get(best_id, {}).get("f1"),
        "probe_sub_em": leaderboard.get(best_id, {}).get("sub_em"),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Meta saved to {meta_path}")


if __name__ == "__main__":
    generate_contract()
