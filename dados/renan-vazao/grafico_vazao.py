"""Vazão total do servidor x vazão por usuário (Renan). Total por rodada = vazão por usuário x N."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 15, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.edgecolor": "#8a8984"})
rod = {2: [90.317, 90.717, 89.225], 8: [22.579, 22.613, 22.627], 18: [10.066, 10.075, 10.092]}
ns = [2, 8, 18]
per = [sum(rod[n]) / 3 for n in ns]
tot = [p * n for p, n in zip(per, ns)]
C_TOT, C_USR = "#2a78d6", "#eb6834"

fig, ax = plt.subplots(figsize=(13, 7.2))
w = 0.36
x = range(len(ns))
b1 = ax.bar([i - w / 2 - 0.01 for i in x], tot, w, color=C_TOT, label="Vazão total do servidor")
b2 = ax.bar([i + w / 2 + 0.01 for i in x], per, w, color=C_USR, label="Vazão de cada usuário")
for i, n in enumerate(ns):  # rodadas
    ax.scatter([i - w / 2 - 0.01] * 3, [v * n for v in rod[n]], s=45, facecolors="white", edgecolors="#0b0b0b", zorder=3)
    ax.scatter([i + w / 2 + 0.01] * 3, rod[n], s=45, facecolors="white", edgecolors="#0b0b0b", zorder=3)
for bars, vals in ((b1, tot), (b2, per)):
    for r, v in zip(bars, vals):
        ax.text(r.get_x() + r.get_width() / 2, v + 4, f"{v:.1f}".replace(".", ","), ha="center", va="bottom",
                fontsize=15, fontweight="bold")
ax.axhline(200, color="#52514e", ls="--", lw=1.5)
ax.text(-0.45, 203, "limite do servidor: 200 Mbit/s", ha="left", va="bottom", color="#52514e", fontsize=13)
ax.set_xticks(list(x), [f"{n} usuários" for n in ns])
ax.set_xlabel("Usuários simultâneos no servidor srv-fra")
ax.set_ylabel("Vazão (Mbit/s)")
ax.set_ylim(0, 255)
ax.grid(axis="y", color="#e6e5e0"); ax.set_axisbelow(True)
ax.legend(loc="upper right", bbox_to_anchor=(1, 1.02), ncol=2, frameon=False)
ax.set_title("O servidor entrega sempre ~180 Mbit/s; com mais usuários, a fatia de cada um encolhe",
             loc="left", fontsize=17, fontweight="bold", pad=16)
fig.text(0.01, 0.01, "Download TCP sem limite, 3 repetições de 40 s (círculos = cada rodada). "
         "Vazão total = vazão por usuário × número de usuários. 24/11/2026.", fontsize=12, color="#52514e")
fig.tight_layout(rect=(0, 0.03, 1, 1))
fig.savefig("grafico-vazao-servidor-x-usuario.png", dpi=150)
