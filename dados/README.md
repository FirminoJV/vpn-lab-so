# Dados das medições

Uma pasta por integrante. Em cada uma: os arquivos brutos gerados por `./lab medir`
(CSV, resumo e JSON), os prints do terminal e do Grafana, e um README com a leitura dos gráficos.

| Pasta | Integrante | Métrica | Servidor | Carga |
|---|---|---|---|---|
| `joao-perda/` | João Vitor | Perda de pacotes (%) | srv-jnb (100 Mbit/s) | UDP 8 Mbit/s por usuário |
| `pedro-latencia/` | Pedro Nicoletti | Latência RTT (ms) | srv-fra (200 Mbit/s) | TCP sem limite |
| `renan-vazao/` | Renan Santos | Vazão por usuário (Mbit/s) | srv-fra (200 Mbit/s) | TCP sem limite |

Condições: 0, 2, 8 e 18 usuários simultâneos (vazão: 2, 8 e 18), 3 repetições, 40 s cada.
Todas as medições foram feitas na mesma máquina (notebook do João, Windows 11 + Ubuntu 24.04 no WSL2,
16 núcleos) e na mesma versão do laboratório.

## Gráfico do grupo

`grafico-grupo.png` — três painéis, um por frente: usuários simultâneos no eixo horizontal, a métrica no
vertical, ponto cheio = média, círculos vazados = cada rodada, linha tracejada = referência
(valor sem carga; na vazão, o teto do servidor dividido pelos usuários). Gerado por `grafico_grupo.py`
(Python + Matplotlib) a partir dos valores das tabelas.
