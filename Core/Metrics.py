"""SearchQA evaluation metrics: Exact Match, F1, and Substring Match.

Normalization follows the SQuAD convention:
  - lowercase
  - remove punctuation
  - remove articles (a, an, the)
  - collapse whitespace

Answer extraction looks for <answer>...</answer> XML tags,
falling back to the last non-empty line of the response.
"""
from __future__ import annotations

import re
import string
from collections import Counter


def normalize_answer(s: str) -> str:
    """Normalize answer string (SQuAD convention)."""
    s = s.lower()
    s = "".join(ch for ch in s if ch not in string.punctuation)
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    s = " ".join(s.split())
    return s.strip()


def extract_answer(text: str) -> str:
    """Extract answer from <answer>...</answer> tags.

    Fallback: last non-empty line, then full response stripped.
    """
    matches = re.findall(r"<answer>(.*?)</answer>", text, re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[-1].strip()
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if lines:
        return lines[-1]
    return text.strip()


def exact_match(prediction: str, gold_answers: list[str]) -> float:
    norm_pred = normalize_answer(prediction)
    for gold in gold_answers:
        if normalize_answer(gold) == norm_pred:
            return 1.0
    return 0.0


def f1_score(prediction: str, gold_answers: list[str]) -> float:
    """Token-level F1 (SQuAD-style), max across all gold answers."""
    norm_pred = normalize_answer(prediction)
    pred_tokens = norm_pred.split()

    if not pred_tokens:
        for gold in gold_answers:
            if not normalize_answer(gold).split():
                return 1.0
        return 0.0

    best_f1 = 0.0
    for gold in gold_answers:
        gold_tokens = normalize_answer(gold).split()
        if not gold_tokens:
            continue
        common = Counter(pred_tokens) & Counter(gold_tokens)
        n_common = sum(common.values())
        if n_common == 0:
            continue
        precision = n_common / len(pred_tokens)
        recall = n_common / len(gold_tokens)
        f1 = 2 * precision * recall / (precision + recall)
        best_f1 = max(best_f1, f1)

    return best_f1


def sub_em(prediction: str, gold_answers: list[str]) -> float:
    """1.0 if any normalized gold is a substring of prediction, or vice versa."""
    norm_pred = normalize_answer(prediction)
    for gold in gold_answers:
        norm_gold = normalize_answer(gold)
        if norm_gold in norm_pred or norm_pred in norm_gold:
            return 1.0
    return 0.0


def evaluate(prediction_text: str, gold_answers: list[str]) -> dict:
    """Evaluate a single QA prediction against gold answers.

    Returns dict with: em, f1, sub_em, predicted_answer, gold_answers.
    """
    answer = extract_answer(prediction_text)
    return {
        "em": exact_match(answer, gold_answers),
        "f1": f1_score(answer, gold_answers),
        "sub_em": sub_em(answer, gold_answers),
        "predicted_answer": answer,
        "gold_answers": gold_answers,
    }


# ---------------------------------------------------------------------------
# Text Generation Metrics (SoccerNet commentary task)
# ---------------------------------------------------------------------------

def _lcs_length(x: list[str], y: list[str]) -> int:
    """Longest Common Subsequence length (DP, O(mn))."""
    m, n = len(x), len(y)
    if m == 0 or n == 0:
        return 0
    # 空间优化：只保留上一行
    prev = [0] * (n + 1)
    for i in range(m):
        curr = [0] * (n + 1)
        for j in range(n):
            if x[i] == y[j]:
                curr[j + 1] = prev[j] + 1
            else:
                curr[j + 1] = max(curr[j], prev[j + 1])
        prev = curr
    return prev[n]


def rouge_l(prediction: str, reference: str) -> float:
    """ROUGE-L F1 score based on Longest Common Subsequence."""
    pred_tokens = normalize_answer(prediction).split()
    ref_tokens = normalize_answer(reference).split()
    if not pred_tokens or not ref_tokens:
        return 0.0 if (pred_tokens or ref_tokens) else 1.0
    lcs = _lcs_length(pred_tokens, ref_tokens)
    precision = lcs / len(pred_tokens) if pred_tokens else 0.0
    recall = lcs / len(ref_tokens) if ref_tokens else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def bleu_1(prediction: str, reference: str) -> float:
    """BLEU-1 (unigram precision) with brevity penalty."""
    pred_tokens = normalize_answer(prediction).split()
    ref_tokens = normalize_answer(reference).split()
    if not pred_tokens:
        return 0.0 if ref_tokens else 1.0
    if not ref_tokens:
        return 0.0
    pred_counts = Counter(pred_tokens)
    ref_counts = Counter(ref_tokens)
    clipped = sum(min(pred_counts[t], ref_counts[t]) for t in pred_counts)
    precision = clipped / len(pred_tokens) if pred_tokens else 0.0
    # Brevity penalty
    bp = min(1.0, len(pred_tokens) / len(ref_tokens)) if ref_tokens else 0.0
    return bp * precision


def evaluate_text_gen(prediction: str, gold: str) -> dict:
    """Evaluate text generation (commentary task) with ROUGE-L and BLEU-1.

    'passed' defined as ROUGE-L >= 0.3 (loose threshold for free-form text).
    """
    rl = rouge_l(prediction, gold)
    b1 = bleu_1(prediction, gold)
    return {
        "rouge_l": rl,
        "bleu_1": b1,
        "predicted_answer": prediction.strip(),
        "gold_answers": [gold],
        "passed": rl >= 0.3,
    }
