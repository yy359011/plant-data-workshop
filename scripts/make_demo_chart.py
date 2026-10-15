"""Regenerate the synthetic OCR example figure (requires matplotlib only here)."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

values = [12.4, 18.2, 21.6]
fig, ax = plt.subplots(figsize=(9, 5), dpi=120)
fig.patch.set_facecolor('#ffffff')
bars = ax.bar(['8 h', '12 h', '16 h'], values, color=['#7cc6bd', '#36a69b', '#187b74'], width=.52)
ax.set_ylim(0, 27)
ax.set_ylabel('Mean leaf area (mm²)', color='#40536d', fontsize=11)
ax.set_xlabel('Daily light duration', color='#40536d', fontsize=11, labelpad=12)
ax.set_title('Arabidopsis seedlings · light treatment', loc='left', color='#142344', fontsize=16, pad=28)
ax.text(0, 1.04, 'SYNTHETIC DEMO DATA  |  n = 6 per group', transform=ax.transAxes, color='#aa6b17', fontsize=10)
ax.spines[['top', 'right']].set_visible(False)
ax.spines[['bottom', 'left']].set_color('#d8e1eb')
ax.tick_params(colors='#526983', labelsize=11)
ax.grid(axis='y', color='#e7edf3', linewidth=.8)
ax.set_axisbelow(True)
ax.bar_label(bars, fmt='%.1f', padding=8, color='#142344', fontsize=12)
fig.text(.5, .035, 'Illustrative values only. No scientific inference or real experiment.', ha='center', color='#7f8da0', fontsize=9)
fig.subplots_adjust(left=.115, right=.96, bottom=.19, top=.79)
target = Path(__file__).resolve().parents[1] / 'backend' / 'demo-assets' / 'seedling-growth.png'
target.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(target)
print(target)
