# VPN Lab — laboratório fechado para medir VPN

> **Integrantes do grupo: comecem pelo [GUIA.md](GUIA.md)** — passo a passo da instalação até a entrega da sua medição.

Um ambiente **100% local e gratuito** que simula usuários e servidores VPN espalhados pelo mundo,
dentro de um único computador Linux. Tudo é construído do zero: a "internet" simulada, os túneis
WireGuard, as sondas de medição, o gerador de carga, o otimizador de rotas e os painéis.

**O que dá para medir e apresentar**

| Pergunta | Como o laboratório responde |
|---|---|
| Qual o ping, jitter e perda de cada usuário até cada servidor? | Sondas UDP contínuas, com e sem VPN → matrizes no Grafana e `./lab matrix` |
| Quanto a VPN acrescenta de latência? | Comparação automática túnel × sem VPN no mesmo caminho |
| Quantos usuários um túnel/servidor aguenta antes de degradar? | `./lab ramp` → curva vazão × usuários, ponto de saturação, latência sob carga |
| Um usuário abusivo prejudica os outros? | `./lab abuse` → antes/depois para os usuários normais |
| Qual servidor intermediário é o melhor para todos? | Otimizador contínuo: ranking por média, pior caso e qualidade de voz (MOS) |
| Vale a pena passar por um relay em vez de ir direto? | Caminhos usuário → servidor A → malha → servidor B, compostos e medidos de ponta a ponta |
| E se um enlace piorar ou cair? | `./lab impair` injeta atraso, perda, jitter, queda ou corte de banda ao vivo |

Custo: **zero**. Só usa software livre (Docker, WireGuard, Prometheus, Grafana, iperf3, Python).

---

## 1. Requisitos

- Linux (testado para Ubuntu/Debian; kernel 5.6+ já traz WireGuard)
- Docker Engine + plugin compose (`docker compose version` deve funcionar)
- 8 GB de RAM / 4 núcleos rodam a topologia padrão (6 servidores, 18 usuários) com folga
- Módulos de kernel `sch_netem`, `sch_htb`, `cls_u32`, `act_police` (no Ubuntu: `sudo apt install linux-modules-extra-$(uname -r)` se faltar).
  Sem módulo `wireguard`, o laboratório usa `wireguard-go` automaticamente.

```bash
./lab check
```

## 2. Subir

```bash
./lab up
```

Na primeira vez baixa as imagens base e constrói as do laboratório (alguns minutos). Depois:

- **Grafana:** http://localhost:3000 (sem login; pasta *VPN Lab*)
- **Prometheus:** http://localhost:9090

Espere ~1 minuto para as primeiras médias e rode `./lab status` — todas as linhas devem mostrar
emulação **ativa** e túneis/handshakes completos.

## 3. Arquitetura

```
                       seu computador (Docker)
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  rede "underlay" 172.30/16 — a internet simulada (isolada, sem saída)    │
 │                                                                          │
 │   cli-sao-1 ─┐   cada nó aplica tc htb+netem POR DESTINO:                │
 │   cli-sao-2 ─┤   atraso, jitter, perda e banda iguais aos da             │
 │   cli-tok-1 ─┤   distância real entre as regiões                         │
 │     ...      │                                                           │
 │              │  túneis WireGuard (um por servidor, em cada cliente)      │
 │              ▼                                                           │
 │   srv-sao  srv-mia  srv-fra  srv-tok  srv-sin  srv-jnb                   │
 │      └────────── malha WireGuard entre servidores (backbone) ──────┘     │
 │                                                                          │
 │  rede "mgmt" 172.31/16 — observabilidade, fora da emulação               │
 │   agentes :9100/metrics ─► Prometheus ─► Grafana                         │
 │                              ▲   └────► otimizador :9200 (rotas/hub)     │
 │                              └──────── labctl (testes, relatórios)       │
 └──────────────────────────────────────────────────────────────────────────┘
```

Endereçamento gerado automaticamente:

| Item | Endereço |
|---|---|
| Servidor *i* na internet simulada | `172.30.1.(10+i)` |
| Cliente *j* na internet simulada | `172.30.2.(10+j)` |
| Rede de clientes do servidor *i* (túnel) | `10.(100+i).0.0/16` — servidor em `.0.1`, cliente *j* em `.0.(j+2)` |
| Malha entre servidores | `10.99.0.(i+1)/24` |

Cada cliente tem uma interface WireGuard para cada servidor (`wg-sao`, `wg-fra`...) e regras de
roteamento por origem, o que permite medir **ao mesmo tempo** todos os servidores e todos os
caminhos com relay, sem trocar de VPN.

### Componentes

| Pasta | O que é |
|---|---|
| `topology.toml` | **O único arquivo que você edita**: regiões, RTT entre elas, perdas, servidores, usuários |
| `gen/` | Gerador: chaves WireGuard (X25519 implementado do zero), configs, compose, Prometheus |
| `node/agent.py` | Agente de cada máquina: emulação de rede, túneis, sondas, carga, métricas, API |
| `ctl/optimizer.py` | Otimizador contínuo (hub, relays, pares de usuários) exportando métricas |
| `ctl/labctl.py` | Controlador dos testes (chamado por `./lab ...`) |
| `ctl/report.py` | Relatório HTML autocontido |
| `grafana/` | 3 dashboards prontos + provisionamento |
| `tests/` | Testes automáticos e um harness que roda tudo em *network namespaces*, sem Docker |
| `results/` | Saída dos testes: JSON, CSV e relatórios HTML |

## 4. O que é medido

**Sondas** (cada nó, a cada 10 s, 10 pacotes UDP espaçados de 100 ms, com eco em todos os nós):

| Caminho | De → para | Para quê |
|---|---|---|
| `underlay` | cliente → servidor, sem VPN | linha de base; valida a emulação |
| `tunnel` | cliente → servidor, pelo WireGuard | a experiência real do usuário |
| `mesh` | servidor → servidor, pela malha | o "backbone" entre servidores |
| `relay` | cliente → servidor A → malha → servidor B | rota alternativa medida de ponta a ponta (a cada 30 s) |

Para cada caminho: RTT mín/médio/mediana/p95/máx, jitter (variação entre pacotes consecutivos),
perda, pacotes fora de ordem.

**Por nó:** bytes e handshakes por peer WireGuard, vazão por interface, fila e descartes na saída
(bufferbloat), CPU e memória do contêiner, parâmetros de rede configurados (para comparar com o medido).

**Carga (iperf3):** vazão por segundo, retransmissões TCP, perda e jitter UDP, justiça entre usuários
(índice de Jain: 1,0 = todos recebem o mesmo).

**Otimizador:** ranking de hubs, ganho de cada relay, melhor caminho por par de usuários, sobrecarga do túnel,
qualidade de voz estimada (MOS 1–4,5 pelo modelo E simplificado da ITU-T G.107).

## 5. Roteiro de testes

**Protocolo do grupo (uma frente por integrante):**

```bash
./lab medir latencia   # RTT (ms) de todos os usuários até srv-fra, com 0/2/8/18 baixando por TCP
./lab medir vazao      # vazão por usuário (Mbit/s) em srv-fra com 2/8/18 usuários TCP
./lab medir perda      # perda (%) de todos os usuários até srv-jnb, com 0/2/8/18 recebendo UDP a 8 Mbit/s
./lab report           # junta as três frentes num gráfico de variação %
```

Cada um roda 3 repetições por condição (ordem alternada) e grava CSV/JSON em `results/`.

**Outros testes:**

```bash
# Latência de todos para todos
./lab matrix                        # pelo túnel
./lab matrix --path underlay        # sem VPN
./lab matrix --metric loss

# Melhor servidor intermediário
./lab optimize                      # ranking + relays + pares
./lab optimize --objective pior_caso

# Muitos usuários no mesmo túnel
./lab load --server srv-fra --clients all --rate 10M          # 18 usuários a 10 Mbit/s cada
./lab ramp --server srv-fra --steps 1,2,4,8,12,18             # curva até saturar
./lab ramp --server srv-jnb --proto udp --rate 8M             # UDP: mostra perda e jitter
./lab abuse --server srv-fra --heavy cli-mia-1 --normal 6     # um abusivo contra 6 normais

# Falhas ao vivo (acompanhe o dashboard 3 trocar o hub)
./lab impair --node srv-mia --add-delay-ms 200                # srv-mia fica 200 ms mais lento
./lab impair --node cli-sao-1 --dst srv-fra --loss-pct 5      # perda em um enlace
./lab impair --node srv-fra --dst srv-sin --down --both       # derruba um enlace da malha
./lab impair --node srv-fra --uplink-mbit 20                  # corta a banda do servidor
./lab reset

# Apresentação
./lab report                         # results/relatorio-mais-recente.html (imprima em PDF se quiser)
```

Durante os testes o controlador marca faixas no Grafana (anotações), então os gráficos mostram
exatamente quando cada teste começou e terminou.

### O que esperar da topologia padrão

- O link Tóquio → Joanesburgo foi configurado com peering ruim (365 ms), pior que Tóquio → Singapura →
  Joanesburgo (70 + 250 ms). O otimizador deve apontar **srv-sin como relay** para usuários de Tóquio
  saindo em srv-jnb, com ganho de ~40 ms — e a sonda de relay confirma medindo de verdade.
- O servidor de Joanesburgo tem metade do uplink (100 Mbit/s): na rampa ele satura antes dos outros.
- O usuário `cli-sao-3` simula 4G (45 ms de acesso, 1% de perda): aparece como pior caso em quase tudo.

## 6. Dashboards

1. **Visão geral** — saúde, matrizes de RTT/perda/jitter/sobrecarga, evolução no tempo, malha, configurado × medido.
2. **Carga e capacidade** — vazão por usuário, uso do uplink, CPU, fila (bufferbloat), latência sob carga, retransmissões, peers WireGuard.
3. **Melhor servidor intermediário e rotas** — vencedor por objetivo, ranking, relays que ganham do caminho direto, matriz usuário ↔ usuário.

Os dashboards são gerados por `grafana/build_dashboards.py`; edite pelo Grafana ou pelo script.

## 7. Personalizar

Edite `topology.toml` e rode `./lab apply`. Exemplos:

```toml
[[region]]
code = "lon"
name = "Londres"

[rtt_ms]
"lon-fra" = 15
"lon-sao" = 190
# ... um valor para cada par de regiões

[[server]]
name = "srv-fra2"      # segundo servidor na mesma região
region = "fra"
uplink_mbit = 1000

[[client]]
region = "lon"
count = 5
access_rtt_ms = 20
access_loss_pct = 0.5
uplink_mbit = 20
downlink_mbit = 100
```

Os valores de RTT padrão são aproximações da internet pública; troque por medições reais se tiver.
Com 8 GB de RAM mantenha até ~30 usuários; cada cliente usa ~30 MB e cada servidor ~100 MB (mais se o kernel não tiver WireGuard e o wireguard-go for usado).

Para acessar o Grafana de outra máquina (apresentação na rede local), coloque `bind_address = "0.0.0.0"` em `[lab]`.

## 8. API dos agentes (para automações próprias)

Dentro da rede de gerência (ex.: `./lab shell cli-sao-1` ou um contêiner na rede `vpnlab_mgmt`):

| Método | Rota | Corpo |
|---|---|---|
| GET | `/metrics` | métricas Prometheus |
| GET | `/state` | estado completo em JSON |
| POST | `/load` | `{"server":"srv-fra","duration":30,"parallel":1,"proto":"tcp","direction":"down","rate":"0"}` |
| POST | `/load/stop` | `{"id":"..."}` (ou vazio para todas) |
| POST | `/impair` | `{"dst":"srv-fra","add_delay_ms":100,"jitter_ms":5,"loss_pct":2,"down":false,"uplink_mbit":50}` |
| POST | `/impair/reset` | `{}` |

## 9. Problemas comuns

| Sintoma | Causa / solução |
|---|---|
| `emulação INATIVA` no `./lab status` | falta `sch_netem`/`sch_htb`: instale `linux-modules-extra-$(uname -r)` e rode `./lab down && ./lab up` |
| RTT sem VPN muito diferente do configurado | mesma causa acima; o `./lab matrix` avisa |
| `túneis ok` incompleto | veja `./lab logs <nó>`; sem módulo wireguard o agente usa wireguard-go (precisa de `/dev/net/tun`) |
| handshakes 0 | normal nos primeiros ~20 s; clientes mandam keepalive a cada 15 s |
| Grafana vazio | espere 1–2 min; confira os alvos em http://localhost:9090/targets |
| Vazão menor que o uplink configurado | CPU do computador é o limite (WireGuard + iperf3); veja "CPU por nó" no dashboard 2 |

## 10. Testes

```bash
./lab test                                    # unidade: X25519 (RFC 7748) e otimizador
sudo python3 tests/netns_harness.py up tests/mini.toml   # integração sem Docker (precisa de iproute2, wg, iperf3)
sudo python3 tests/netns_harness.py down
```

## 11. Limitações conhecidas

- O atraso é emulado por destino na saída de cada nó; é um modelo de "internet com latências fixas
  mais jitter", não reproduz congestionamento real de terceiros nem rotas BGP mudando sozinhas
  (use `./lab impair` para simular essas mudanças).
- O jitter do netem pode reordenar pacotes, como em redes reais com caminhos múltiplos; a sonda conta os fora de ordem.
- A vazão máxima depende da CPU do seu computador, pois todos os nós dividem os mesmos núcleos.
