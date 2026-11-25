# Perda de pacotes — João Vitor

**Medição:** 23/11/2026, 12:36–12:47 · `./lab medir perda` · servidor srv-jnb (uplink 100 Mbit/s) ·
UDP a 8 Mbit/s por usuário · 0, 2, 8 e 18 usuários · 3 repetições de 40 s.

**Arquivos brutos** (copiados de `results/`):
`medicao-perda-20261123-124637.csv`, `-resumo.csv`, `.json`.
`valores-transcritos.csv` = os mesmos valores por rodada, transcritos do terminal (print 02).

## Resultado

| Usuários | Perda pela sonda (%) | Perda iperf3 UDP (%) | Vazão total (Mbit/s) | Latência (ms) |
|---|---|---|---|---|
| 0 | 0,40 | — | — | 242 |
| 2 | 0,33 | 0,26 | 16,0 | 245 |
| 8 | 0,43 | 0,20 | 63,9 | 289 |
| 18 | 86,93 | 27,45 | 90,7 | 1.908 |

Hipótese (antes de medir): perda perto de zero até 8 usuários e alta com 18. **Sustentada.**

## Como ler os prints do Grafana (dashboard 4, servidor srv-jnb)

- **03 — Usuários simultâneos:** os degraus 0 → 2 → 8 → 18, três vezes (ordem crescente, decrescente, crescente).
  Cada faixa laranja é uma condição.
- **04 — Perda:** fica colada no zero com 0, 2 e 8 usuários e dá um salto exatamente nos degraus de 18.
  Linha verde = sondas de todos os 18 usuários (conta como perdido o que demora mais de 2 s);
  linha amarela = iperf3 dos usuários com carga (perda real, ~27%).
- **05 — Latência:** mesmo padrão: ~240 ms até 8 usuários, ~2–3 s com 18 (os pacotes esperam na fila).
- **06 — Vazão total:** sobe 16 → 64 Mbit/s e trava em ~91 Mbit/s com 18: é o teto de 100 Mbit/s
  (menos os cabeçalhos do WireGuard). Pediram 144, couberam 91.
- **07 — Fila do servidor:** praticamente vazia até 8 usuários; com 18 enche até ~122 MiB.
  É o buffer limitado (produtor-consumidor) cheio — a causa da espera e do descarte.

## Rodada descartada

`rodada-descartada-23nov-manha/` guarda a primeira tentativa, que **não foi usada**. O print 04 mostra o
servidor entregando 147 Mbit/s num limite de 100: havia um erro na configuração do limite de banda,
então o servidor nunca saturava e a perda não mudava. O erro foi corrigido e a medição refeita.
