# Latência pelo túnel — Pedro Nicoletti

**Medição:** 24/11/2026, 22:30–22:41 · `./lab medir latencia` · servidor srv-fra (uplink 200 Mbit/s) ·
download TCP sem limite · 0, 2, 8 e 18 usuários · 3 repetições de 40 s.
Medida no notebook do João (mesma máquina e versão das outras frentes).

**Arquivos brutos** (copiar de `results/`): `medicao-latencia-<data>.csv`, `-resumo.csv`, `.json`.
`valores-transcritos.csv` = valores por rodada, transcritos do terminal (print 01).

## Resultado

RTT médio pelo túnel de todos os 18 usuários até o srv-fra (sondas UDP, 50 pacotes por rodada).

| Usuários | Latência média (ms) | Rodadas (ms) | Desvio (ms) | Variação vs. ociosa |
|---|---|---|---|---|
| 0 | 155,8 | 156,2 · 155,6 · 155,6 | 0,4 | — |
| 2 | 162,1 | 161,2 · 164,6 · 160,5 | 2,2 | +4,0% |
| 8 | 370,4 | 359,6 · 371,5 · 380,1 | 10,3 | +137,7% |
| 18 | 934,4 | 956,7 · 909,9 · 936,6 | 23,5 | +499,8% |

Hipótese do grupo: a latência sobe com mais usuários. **Sustentada** — e a latência sextuplicou com 18.

## Como ler os prints do Grafana (dashboard 4, servidor srv-fra)

- **02 — Usuários simultâneos:** os degraus 0 → 2 → 8 → 18 (três vezes, ordem alternada).
- **03 — Latência:** linha verde = média dos 18 usuários; amarela = o usuário mais distante.
  Plana em ~156 ms sem carga e com 2 usuários; sobe para ~400 ms com 8 e ~1,4 s com 18,
  sempre junto com os degraus do gráfico 02.
- **04 — Fila do servidor:** a explicação do gráfico 03. Quase vazia com 2 usuários, ~32 MiB com 8,
  ~78 MiB com 18. Cada pacote espera a fila andar: fila maior = latência maior.
