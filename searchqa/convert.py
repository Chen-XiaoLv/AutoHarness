"""将 JEOPARDY_QUESTIONS1.json 转为项目标准 JSONL 格式"""
import json, random
from pathlib import Path

src = Path(__file__).parent / "JEOPARDY_QUESTIONS1.json"
data = json.loads(src.read_text(encoding="utf-8"))
random.seed(42)
random.shuffle(data)

split = int(len(data) * 0.8)
train_data = data[:split]
test_data = data[split:]

for name, items in [("train", train_data), ("test", test_data)]:
    out = Path(__file__).parent / f"{name}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for i, item in enumerate(items):
            question = item["question"].strip("'\"")
            answer = item["answer"]
            record = {
                "id": f"{name}_{i:05d}",
                "question": question,
                "answer": f"{answer}\n#### {answer}",
                "category": item.get("category", ""),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"{name}: {len(items)} 条 -> {out}")

print(f"\n总计: {len(data)} 条")
print(f"字段示例:")
print(f"  question: {test_data[0]['question']}")
print(f"  answer:   {test_data[0]['answer']}")
print(f"  category: {test_data[0]['category']}")
