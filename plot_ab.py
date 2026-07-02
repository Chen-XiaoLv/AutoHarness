import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
from pathlib import Path

matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

data = json.loads(Path("auto_outputs/ab_test.json").read_text(encoding="utf-8"))

categories = ["EM", "F1", "Sub-EM"]
no_vals = [
    data["no_contract"]["em_mean"] * 100,
    data["no_contract"]["f1_mean"] * 100,
    data["no_contract"]["sub_em_mean"] * 100,
]
yes_vals = [
    data["with_contract"]["em_mean"] * 100,
    data["with_contract"]["f1_mean"] * 100,
    data["with_contract"]["sub_em_mean"] * 100,
]
no_std = [
    data["no_contract"]["em_std"] * 100,
    data["no_contract"]["f1_std"] * 100,
    data["no_contract"]["sub_em_std"] * 100,
]
yes_std = [
    data["with_contract"]["em_std"] * 100,
    data["with_contract"]["f1_std"] * 100,
    data["with_contract"]["sub_em_std"] * 100,
]

x = np.arange(len(categories))
width = 0.32

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
fig.patch.set_facecolor('#ffffff')
ax.set_facecolor('#fafafa')

bars1 = ax.bar(x - width/2, no_vals, width, label='No Contract',
               color='#5B8FF9', alpha=0.9, edgecolor='white', linewidth=1.2,
               yerr=no_std, capsize=5, error_kw={'linewidth': 1.2, 'color': '#5B8FF9'})
bars2 = ax.bar(x + width/2, yes_vals, width, label='With Contract',
               color='#5AD8A6', alpha=0.9, edgecolor='white', linewidth=1.2,
               yerr=yes_std, capsize=5, error_kw={'linewidth': 1.2, 'color': '#5AD8A6'})

for bar in bars1:
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2.5,
            f'{bar.get_height():.1f}%', ha='center', va='bottom',
            fontsize=11, fontweight='bold', color='#5B8FF9')
for bar in bars2:
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2.5,
            f'{bar.get_height():.1f}%', ha='center', va='bottom',
            fontsize=11, fontweight='bold', color='#5AD8A6')

ax.set_ylabel('Score (%)', fontsize=12, fontweight='bold')
ax.set_title('A/B Test: Contract vs No Contract\nSearchQA | 215 samples × 5 runs',
             fontsize=15, fontweight='bold', pad=15)
ax.set_xticks(x)
ax.set_xticklabels(categories, fontsize=12, fontweight='bold')
ax.set_ylim(0, 85)
ax.legend(fontsize=11, loc='upper left', framealpha=0.9)
ax.grid(axis='y', alpha=0.3, linestyle='--')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

plt.tight_layout()
out = Path("auto_outputs/ab_test.png")
plt.savefig(str(out), bbox_inches='tight', facecolor='white')
plt.close()
print(f"Chart saved to {out}")
