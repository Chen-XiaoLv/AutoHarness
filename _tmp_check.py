import json
lines = [json.loads(l) for l in open('e:/Tace项目/outputs/0701_1646/Executor/test_final.jsonl', encoding='utf-8')]
total = len(lines)
has_bracket = sum(1 for r in lines if '<answer>' in r.get('prediction', ''))
print(f"Total: {total}")
print(f"Responses with <answer> tag: {has_bracket}")

# Sub-EM on bracket answers
bracket_em = 0
for r in lines:
    pred = r.get('prediction', '')
    expected = r.get('expected', '')
    gold = [a.strip() for a in expected.split('####') if a.strip()]
    # Extract bracket answer
    import re
    matches = re.findall(r'<answer>(.*?)</answer>', pred, re.DOTALL | re.IGNORECASE)
    if matches:
        answer = matches[-1].strip().lower()
        answer = "".join(ch for ch in answer if ch not in '.,;:!?')
        for g in gold:
            g_norm = "".join(ch for ch in g.lower() if ch not in '.,;:!?')
            if g_norm == answer:
                bracket_em += 1
                break
print(f"Bracket EM: {bracket_em}/{total} = {bracket_em/total*100:.1f}%")

# Sub-EM on bracket answers
bracket_subem = 0
for r in lines:
    pred = r.get('prediction', '')
    expected = r.get('expected', '')
    gold = [a.strip() for a in expected.split('####') if a.strip()]
    matches = re.findall(r'<answer>(.*?)</answer>', pred, re.DOTALL | re.IGNORECASE)
    if matches:
        answer = matches[-1].strip().lower()
        for g in gold:
            g_norm = g.lower().strip()
            if g_norm in answer or answer in g_norm:
                bracket_subem += 1
                break
print(f"Bracket Sub-EM: {bracket_subem}/{total} = {bracket_subem/total*100:.1f}%")
