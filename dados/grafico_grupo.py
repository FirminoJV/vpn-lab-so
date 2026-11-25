"""Gráfico do grupo: uma série (painel) por frente, eixo X = usuários simultâneos,
média + valor de cada rodada + uma referência. Valores das tabelas em dados/*/valores-transcritos.csv."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FONT = "DejaVu Sans"
plt.rcParams.update({"font.family": FONT, "font.size": 14, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#8a8984", "axes.labelcolor": "#0b0b0b", "xtick.color": "#52514e",
                     "ytick.color": "#52514e", "axes.titleweight": "bold", "axes.titlesize": 17})

frentes = [
    {"nome": "Latência pelo túnel — Pedro", "unid": "Latência (ms)", "cor": "#2a78d6",
     "rod": {0: [156.237, 155.555, 155.608], 2: [161.164, 164.555, 160.545],
             8: [359.581, 371.495, 380.142], 18: [956.708, 909.934, 936.643]},
     "ref": ("sem carga: 156 ms", 155.8), "fmt": "{:.0f} ms"},
    {"nome": "Vazão por usuário — Renan", "unid": "Vazão por usuário (Mbit/s)", "cor": "#eb6834",
     "rod": {2: [90.317, 90.717, 89.225], 8: [22.579, 22.613, 22.627], 18: [10.066, 10.075, 10.092]},
     "ref": ("limite do servidor ÷ usuários (200 ÷ N)", None), "fmt": "{:.1f}"},
    {"nome": "Perda de pacotes — João", "unid": "Perda de pacotes (%)", "cor": "#1baf7a",
     "rod": {0: [0.2727, 0.4545, 0.4722], 2: [0.3714, 0.2424, 0.3692],
             8: [0.4118, 0.3429, 0.5231], 18: [88.7692, 85.2923, 86.7222]},
     "ref": ("sem carga: 0,40%", 0.40), "fmt": "{:.1f}%"},
]

fig, axes = plt.subplots(1, 3, figsize=(19, 6.6))
fig.suptitle("Dividir o túnel é quase de graça até o servidor saturar; depois a latência e a perda disparam "
             "e a vazão de cada um despenca", fontsize=17, fontweight="bold", x=0.02, ha="left", y=0.99)
for ax, f in zip(axes, frentes):
    xs = sorted(f["rod"])
    medias = [sum(f["rod"][x]) / 3 for x in xs]
    # referência
    if f["ref"][1] is not None:
        ax.axhline(f["ref"][1], color="#52514e", ls="--", lw=1.5)
        ax.text(18.6, f["ref"][1], " " + f["ref"][0], va="bottom", ha="right", color="#52514e", fontsize=12)
    else:
        rx = [x / 10 for x in range(20, 186)]
        ax.plot(rx, [200 / x for x in rx], color="#52514e", ls="--", lw=1.5)
        ax.text(9.5, 52, "teto do servidor ÷ usuários\n(200 Mbit/s ÷ N)", color="#52514e", fontsize=12)
    # rodadas e média
    for x in xs:
        ax.scatter([x] * 3, f["rod"][x], s=70, facecolors="white", edgecolors=f["cor"], linewidths=2, zorder=3)
    ax.plot(xs, medias, color=f["cor"], lw=3, marker="o", ms=11, zorder=4)
    for x, m in zip(xs, medias):
        off, ha = ((14, 4), "left") if (x == 2 and 0 in xs) else ((0, 14), "center")
        ax.annotate(f["fmt"].format(m).replace(".", ","), (x, m), textcoords="offset points", xytext=off,
                    ha=ha, va="bottom", fontsize=14, fontweight="bold", color="#0b0b0b")
    ax.set_title(f["nome"], loc="left", pad=14)
    ax.set_xticks([0, 2, 8, 18])
    ax.set_xlim(-1, 19.5)
    ax.set_xlabel("Usuários simultâneos no mesmo servidor")
    ax.set_ylabel(f["unid"])
    ax.set_ylim(0, max(max(v) for v in f["rod"].values()) * 1.2)
    ax.grid(axis="y", color="#e6e5e0")
    ax.set_axisbelow(True)

fig.text(0.02, 0.01, "Ponto cheio = média de 3 repetições; círculos vazados = cada rodada. Latência e vazão: srv-fra "
         "(200 Mbit/s, TCP). Perda: srv-jnb (100 Mbit/s, UDP 8 Mbit/s por usuário; sonda com limite de 2 s).",
         fontsize=12, color="#52514e")
fig.tight_layout(rect=(0, 0.04, 1, 0.95))
fig.savefig("grafico-grupo.png", dpi=150)
print("ok")
