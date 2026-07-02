"""
从 SearchQA test.jsonl 随机抽取 930 条，固定划分为：
  - train_pool.jsonl   (500 条)
  - dev_gate.jsonl     (215 条)
  - test_pool.jsonl    (215 条)
seed=42，保证可复现。
"""
import json
import random

SEED = 42
TOTAL = 930
TRAIN = 500
DEV = 215
TEST = 215

random.seed(SEED)

with open("data/searchqa/test.jsonl", "r", encoding="utf-8") as f:
    all_items = [json.loads(line) for line in f]

sampled = random.sample(all_items, TOTAL)

train_pool = sampled[:TRAIN]
dev_gate = sampled[TRAIN:TRAIN + DEV]
test_pool = sampled[TRAIN + DEV:]

assert len(train_pool) == TRAIN
assert len(dev_gate) == DEV
assert len(test_pool) == TEST

for name, data in [("train_pool", train_pool), ("dev_gate", dev_gate), ("test_pool", test_pool)]:
    path = f"data/searchqa/{name}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"{path}: {len(data)} items")

print("Done.")
