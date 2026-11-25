# Vazão por usuário — Renan Santos

**Medição:** 24/11/2026, 22:47–22:54 · `./lab medir vazao` · servidor srv-fra (uplink 200 Mbit/s) ·
download TCP sem limite · 2, 8 e 18 usuários · 3 repetições de 40 s.
Medida no notebook do João (mesma máquina e versão das outras frentes).
Com 0 usuários não existe vazão: a base de comparação é a condição de 2 usuários.

**Arquivos brutos** (copiar de `results/`): `medicao-vazao-20261124-225427.csv`, `-resumo.csv`, `.json`.
`valores-transcritos.csv` = valores por rodada, transcritos do terminal (print 01).

## Resultado

| Usuários | Vazão por usuário (Mbit/s) | Rodadas (Mbit/s) | Desvio | Vazão total (≈ por usuário × N) | Variação vs. 2 usuários |
|---|---|---|---|---|---|
| 2 | 90,09 | 90,32 · 90,72 · 89,23 | 0,77 | ~180 Mbit/s | — |
| 8 | 22,61 | 22,58 · 22,61 · 22,63 | 0,03 | ~181 Mbit/s | −74,9% |
| 18 | 10,08 | 10,07 · 10,08 · 10,09 | 0,01 | ~181 Mbit/s | −88,8% |

Hipótese do grupo: a vazão por usuário cai com mais usuários. **Sustentada.**
A vazão total ficou constante em ~180 Mbit/s (teto de 200 menos os cabeçalhos do WireGuard e do TCP);
o que muda é em quantas partes ela é dividida: 180 ÷ 2 = 90, 180 ÷ 8 = 22,5, 180 ÷ 18 = 10.

## Como ler os prints do Grafana (dashboard 4, servidor srv-fra)

- **02 — Usuários simultâneos:** degraus 2 → 8 → 18 (três vezes, ordem alternada).
- **03 — Vazão:** linha amarela = total do servidor, praticamente igual (~180 Mbit/s) em todos os degraus;
  linha verde = média por usuário, ~90 com 2, ~22 com 8, ~10 com 18. O bolo é o mesmo, as fatias diminuem.
- **04 — Fila do servidor:** pequena com 2 usuários (~8 MiB), ~32 MiB com 8, ~78 MiB com 18:
  o TCP de mais usuários mantém mais pacotes esperando, o que aumenta a latência (frente do Pedro).
