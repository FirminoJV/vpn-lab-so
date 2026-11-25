#!/usr/bin/env python3
"""Gera os dashboards do Grafana (grafana/dashboards/*.json). Rode após alterar este arquivo."""
import json
import os

DS = {"type": "prometheus", "uid": "prom"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")


class Board:
    def __init__(self, uid, title, desc):
        self.uid, self.title, self.desc = uid, title, desc
        self.panels, self.y, self.id, self.vars = [], 0, 1, []

    def row(self, title):
        self.panels.append({"type": "row", "title": title, "id": self.id, "collapsed": False,
                            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "panels": []})
        self.id += 1
        self.y += 1
        self.x = 0

    def add(self, p, w=12, h=8):
        if getattr(self, "x", 0) + w > 24:
            self.x = 0
            self.y += self.lasth
        p.update(id=self.id, datasource=DS, gridPos={"h": h, "w": w, "x": getattr(self, "x", 0), "y": self.y})
        self.id += 1
        self.x = getattr(self, "x", 0) + w
        self.lasth = h
        if self.x >= 24:
            self.x = 0
            self.y += h
        self.panels.append(p)

    def var(self, name, label, query, multi=True, include_all=True):
        self.vars.append({"name": name, "label": label, "type": "query", "datasource": DS,
                          "query": {"query": query, "refId": "v"}, "definition": query, "refresh": 2,
                          "multi": multi, "includeAll": include_all, "current": {"text": "All", "value": "$__all"} if include_all else {},
                          "sort": 1})

    def save(self):
        d = {"uid": self.uid, "title": self.title, "description": self.desc, "tags": ["vpnlab"], "timezone": "browser",
             "schemaVersion": 39, "version": 1, "refresh": "10s", "time": {"from": "now-30m", "to": "now"},
             "templating": {"list": self.vars}, "panels": self.panels, "editable": True,
             "annotations": {"list": [
                 {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
                  "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
                 {"datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "iconColor": "#eb6834",
                  "name": "Testes do laboratório", "target": {"type": "tags", "tags": ["vpnlab"], "limit": 200}}]},
             "links": [{"title": "Dashboards do VPN Lab", "type": "dashboards", "tags": ["vpnlab"], "asDropdown": True}]}
        with open(os.path.join(OUT, f"{self.uid}.json"), "w") as f:
            json.dump(d, f, indent=1, ensure_ascii=False)


def ts(title, targets, unit="ms", desc="", stack=False, minv=None):
    p = {"type": "timeseries", "title": title, "description": desc,
         "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": l} for i, (e, l) in enumerate(targets)],
         "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10 if stack else 0,
                                                                "stacking": {"mode": "normal" if stack else "none"},
                                                                "showPoints": "never"}}, "overrides": []},
         "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "mean", "max"]},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}
    if minv is not None:
        p["fieldConfig"]["defaults"]["min"] = minv
    return p


def stat(title, expr, unit="none", desc="", legend="", thresholds=None, text_mode="auto"):
    steps = thresholds or [{"color": "blue", "value": None}]
    return {"type": "stat", "title": title, "description": desc,
            "targets": [{"refId": "A", "expr": expr, "instant": True, "legendFormat": legend}],
            "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background" if thresholds else "value",
                        "graphMode": "none", "textMode": text_mode, "justifyMode": "center"}}


def matrix(title, expr, row, col, unit="ms", desc="", maxv=None, decimals=1):
    """Tabela em matriz (linhas x colunas) colorida por valor."""
    d = {"unit": unit, "decimals": decimals, "color": {"mode": "continuous-GrYlRd"},
         "custom": {"align": "center", "cellOptions": {"type": "color-background", "mode": "gradient"}}}
    if maxv is not None:
        d.update(min=0, max=maxv)
    return {"type": "table", "title": title, "description": desc,
            "targets": [{"refId": "A", "expr": expr, "instant": True, "format": "table"}],
            "transformations": [{"id": "groupingToMatrix", "options": {"columnField": col, "rowField": row, "valueField": "Value",
                                                                       "emptyValue": "null"}}],
            "fieldConfig": {"defaults": d, "overrides": [
                {"matcher": {"id": "byRegexp", "options": f"^{row}.*"},
                 "properties": [{"id": "custom.cellOptions", "value": {"type": "auto"}}, {"id": "custom.align", "value": "left"}]}]},
            "options": {"showHeader": True, "cellHeight": "sm"}}


def table(title, targets, desc="", organize=None, units=None, sort=None):
    p = {"type": "table", "title": title, "description": desc,
         "targets": [{"refId": chr(65 + i), "expr": e, "instant": True, "format": "table"} for i, e in enumerate(targets)],
         "transformations": [{"id": "merge", "options": {}}],
         "fieldConfig": {"defaults": {"custom": {"align": "auto"}, "decimals": 1}, "overrides": []},
         "options": {"showHeader": True, "cellHeight": "sm", "sortBy": sort or []}}
    if organize:
        p["transformations"].append({"id": "organize", "options": organize})
    for col, unit in (units or {}).items():
        p["fieldConfig"]["overrides"].append({"matcher": {"id": "byName", "options": col},
                                              "properties": [{"id": "unit", "value": unit}]})
    return p


def hide(*cols):
    return {f"{c}": True for c in cols}


# =============================================================================
# 1. Visão geral
# =============================================================================
b = Board("01-visao-geral", "VPN Lab — 1. Visão geral",
          "Latência, perda e jitter entre usuários e servidores, pela VPN e sem VPN.")
b.var("servidor", "Servidor", 'label_values(vpnlab_probe_rtt_avg_ms{path="tunnel"}, dst)')
b.var("usuario", "Usuário", 'label_values(vpnlab_probe_rtt_avg_ms{path="tunnel"}, src)')
b.row("Saúde do laboratório")
b.add(stat("Nós no ar", 'count(up{job="vpnlab-nodes"} == 1)', desc="Agentes respondendo ao Prometheus"), 4, 4)
b.add(stat("Nós fora", 'count(up{job="vpnlab-nodes"} == 0) or vector(0)',
           thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]), 4, 4)
b.add(stat("Emulação de internet", 'min(vpnlab_emulation_active)', text_mode="value",
           desc="1 = tc/netem ativo em todos os nós. 0 = algum nó sem emulação (veja ./lab status)",
           thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}]), 4, 4)
b.add(stat("Túneis com handshake", 'sum(vpnlab_wg_active_peers{role="client"})', desc="Túneis cliente-servidor ativos (3 min)"), 4, 4)
b.add(stat("Sobrecarga do túnel", 'vpnlab_opt_tunnel_overhead_ms', "ms", "RTT pelo túnel menos RTT sem VPN (média)"), 4, 4)
b.add(stat("Falhas injetadas", 'count(vpnlab_link_impaired) or vector(0)',
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}]), 4, 4)
b.row("Matrizes usuário × servidor (média de 2 min)")
b.add(matrix("RTT pelo túnel (ms)", 'avg_over_time(vpnlab_probe_rtt_avg_ms{path="tunnel"}[2m])', "src", "dst",
             desc="Ida e volta, medido pela VPN de cada usuário até cada servidor"), 12, 11)
b.add(matrix("Perda pelo túnel", 'avg_over_time(vpnlab_probe_loss_ratio{path="tunnel"}[2m])', "src", "dst",
             unit="percentunit", maxv=0.05, decimals=2), 12, 11)
b.add(matrix("Jitter pelo túnel (ms)", 'avg_over_time(vpnlab_probe_jitter_ms{path="tunnel"}[2m])', "src", "dst", maxv=30), 12, 11)
b.add(matrix("Sobrecarga da VPN: túnel − sem VPN (ms)",
             'avg_over_time(vpnlab_probe_rtt_avg_ms{path="tunnel"}[2m]) - ignoring(path) avg_over_time(vpnlab_probe_rtt_avg_ms{path="underlay"}[2m])',
             "src", "dst", maxv=5, decimals=2), 12, 11)
b.row("Ao longo do tempo")
b.add(ts("RTT pelo túnel — usuários → $servidor",
         [('vpnlab_probe_rtt_avg_ms{path="tunnel", dst=~"$servidor", src=~"$usuario"}', "{{src}} → {{dst}}")]), 12, 9)
b.add(ts("RTT p95 pelo túnel", [('vpnlab_probe_rtt_p95_ms{path="tunnel", dst=~"$servidor", src=~"$usuario"}', "{{src}} → {{dst}}")]), 12, 9)
b.add(ts("Perda pelo túnel", [('vpnlab_probe_loss_ratio{path="tunnel", dst=~"$servidor", src=~"$usuario"}', "{{src}} → {{dst}}")],
         "percentunit", minv=0), 12, 9)
b.add(ts("Jitter pelo túnel", [('vpnlab_probe_jitter_ms{path="tunnel", dst=~"$servidor", src=~"$usuario"}', "{{src}} → {{dst}}")]), 12, 9)
b.row("Rede entre servidores (malha / backbone)")
b.add(matrix("RTT servidor ↔ servidor pela malha (ms)", 'avg_over_time(vpnlab_probe_rtt_avg_ms{path="mesh"}[2m])', "src", "dst"), 12, 9)
b.add(table("Configurado × medido sem VPN (ms)", [
    'avg_over_time(vpnlab_probe_rtt_avg_ms{path="underlay", src=~"$usuario", dst=~"$servidor"}[2m])',
    '2 * label_replace(vpnlab_link_delay_ms{node=~"$usuario"}, "src", "$1", "node", "(.*)")'],
    desc="RTT medido sem VPN ao lado do atraso configurado (2 × atraso de ida). Valida a emulação.",
    organize={"excludeByName": hide("Time", "__name__", "instance", "job", "role", "region", "node", "path", "via",
                                    "src_region", "dst_region"),
              "renameByName": {"Value #A": "medido", "Value #B": "configurado", "src": "usuário", "dst": "servidor"}}), 12, 9)
b.save()

# =============================================================================
# 2. Carga e capacidade
# =============================================================================
b = Board("02-carga", "VPN Lab — 2. Carga e capacidade dos túneis",
          "O que acontece quando muitos usuários usam o mesmo túnel/servidor ao mesmo tempo.")
b.var("servidor", "Servidor", 'label_values(vpnlab_node_info{role="server"}, node)')
b.row("Servidor")
b.add(stat("Usuários gerando carga", 'sum(vpnlab_load_active{server=~"$servidor"}) or vector(0)'), 4, 4)
b.add(stat("Vazão agregada agora", 'sum(vpnlab_load_throughput_bps{server=~"$servidor"}) or vector(0)', "bps"), 5, 4)
b.add(stat("Uplink configurado", 'sum(vpnlab_uplink_mbit{node=~"$servidor"}) * 1e6', "bps"), 5, 4)
b.add(stat("Uso do uplink", 'sum(rate(vpnlab_iface_bytes_total{node=~"$servidor", iface="underlay", dir="tx"}[30s])) * 8 / (sum(vpnlab_uplink_mbit{node=~"$servidor"}) * 1e6)',
           "percentunit", thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.7}, {"color": "red", "value": 0.9}]), 5, 4)
b.add(stat("CPU do servidor", 'sum(rate(vpnlab_cpu_seconds_total{node=~"$servidor"}[30s]))', "percentunit",
           desc="1.0 = um núcleo inteiro. Criptografia do WireGuard aparece aqui."), 5, 4)
b.add(ts("Vazão por usuário (teste de carga)", [('sum by (node) (vpnlab_load_throughput_bps{server=~"$servidor"})', "{{node}}")],
         "bps", stack=True, minv=0), 12, 9)
b.add(ts("Tráfego cifrado no servidor (WireGuard)", [
    ('sum(rate(vpnlab_wg_peer_tx_bytes_total{node=~"$servidor", iface="wg0"}[15s])) * 8', "enviado aos clientes"),
    ('sum(rate(vpnlab_wg_peer_rx_bytes_total{node=~"$servidor", iface="wg0"}[15s])) * 8', "recebido dos clientes")], "bps", minv=0), 12, 9)
b.add(ts("Latência pelo túnel durante a carga (bufferbloat)", [
    ('vpnlab_probe_rtt_avg_ms{path="tunnel", dst=~"$servidor"}', "{{src}}")],
    desc="Se a fila do servidor enche, TODOS os usuários sentem a latência subir — inclusive quem não está baixando nada."), 12, 9)
b.add(ts("Fila na saída do servidor", [('vpnlab_queue_backlog_bytes{node=~"$servidor"}', "{{node}}")], "bytes", minv=0), 12, 9)
b.add(ts("Retransmissões TCP (por segundo)", [('sum by (node) (rate(vpnlab_load_retransmits_total{server=~"$servidor"}[30s]))', "{{node}}")],
         "short", minv=0), 12, 8)
b.add(ts("Perda em testes UDP", [('vpnlab_load_udp_loss_ratio{server=~"$servidor"}', "{{node}}")], "percentunit", minv=0), 12, 8)
b.row("Todos os servidores")
b.add(ts("Vazão agregada por servidor", [('sum by (server) (vpnlab_load_throughput_bps)', "{{server}}")], "bps", minv=0), 12, 8)
b.add(ts("CPU por nó", [('topk(10, rate(vpnlab_cpu_seconds_total[30s]))', "{{node}}")], "percentunit", minv=0), 12, 8)
b.add(table("Peers WireGuard (handshake e tráfego)", [
    'vpnlab_wg_peer_handshake_age_seconds{node=~"$servidor"}',
    'vpnlab_wg_peer_rx_bytes_total{node=~"$servidor"}', 'vpnlab_wg_peer_tx_bytes_total{node=~"$servidor"}'],
    organize={"excludeByName": hide("Time", "__name__", "instance", "job", "role", "region"),
              "renameByName": {"Value #A": "handshake há", "Value #B": "recebido", "Value #C": "enviado", "node": "servidor"}},
    units={"handshake há": "s", "recebido": "bytes", "enviado": "bytes"}), 24, 9)
b.save()

# =============================================================================
# 3. Rotas e servidor intermediário
# =============================================================================
b = Board("03-rotas", "VPN Lab — 3. Melhor servidor intermediário e rotas",
          "Resultado contínuo do otimizador: qual servidor atende melhor todos os usuários e onde um relay ajuda.")
b.row("Melhor hub para todos os usuários")
b.add(stat("Melhor hub — RTT médio", 'vpnlab_opt_best_hub{objective="media"}', legend="{{server}}", text_mode="name"), 8, 4)
b.add(stat("Melhor hub — pior caso", 'vpnlab_opt_best_hub{objective="pior_caso"}', legend="{{server}}", text_mode="name"), 8, 4)
b.add(stat("Melhor hub — qualidade de voz (MOS)", 'vpnlab_opt_best_hub{objective="mos"}', legend="{{server}}", text_mode="name"), 8, 4)
b.add({"type": "bargauge", "title": "RTT médio dos usuários até cada hub candidato",
       "targets": [{"refId": "A", "expr": 'sort(vpnlab_opt_hub_rtt_ms{stat="mean"})', "instant": True, "legendFormat": "{{server}}"}],
       "fieldConfig": {"defaults": {"unit": "ms", "min": 0, "color": {"mode": "continuous-GrYlRd"}}, "overrides": []},
       "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True, "reduceOptions": {"calcs": ["lastNotNull"]}}}, 12, 9)
b.add({"type": "bargauge", "title": "RTT do usuário mais distante até cada hub",
       "targets": [{"refId": "A", "expr": 'sort(vpnlab_opt_hub_rtt_ms{stat="max"})', "instant": True, "legendFormat": "{{server}}"}],
       "fieldConfig": {"defaults": {"unit": "ms", "min": 0, "color": {"mode": "continuous-GrYlRd"}}, "overrides": []},
       "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True, "reduceOptions": {"calcs": ["lastNotNull"]}}}, 12, 9)
b.add(table("Ranking completo dos hubs", [
    'vpnlab_opt_hub_rank{objective="media"}', 'vpnlab_opt_hub_rtt_ms{stat="mean"}', 'vpnlab_opt_hub_rtt_ms{stat="p95"}',
    'vpnlab_opt_hub_rtt_ms{stat="max"}', 'vpnlab_opt_hub_loss_ratio', 'vpnlab_opt_hub_mos{stat="mean"}', 'vpnlab_opt_hub_mos{stat="min"}',
    'vpnlab_opt_hub_pair_rtt_ms{stat="mean"}', 'vpnlab_opt_hub_coverage_ratio'],
    organize={"excludeByName": hide("Time", "__name__", "instance", "job", "objective", "stat"),
              "renameByName": {"server": "servidor", "Value #A": "posição", "Value #B": "RTT médio", "Value #C": "RTT p95",
                               "Value #D": "RTT máx", "Value #E": "perda", "Value #F": "MOS médio", "Value #G": "MOS mín",
                               "Value #H": "usuário↔usuário", "Value #I": "cobertura"}},
    units={"RTT médio": "ms", "RTT p95": "ms", "RTT máx": "ms", "usuário↔usuário": "ms", "perda": "percentunit", "cobertura": "percentunit"},
    sort=[{"displayName": "posição", "desc": False}]), 24, 8)
b.add(ts("Evolução do RTT médio por hub candidato", [('vpnlab_opt_hub_rtt_ms{stat="mean"}', "{{server}}")],
         desc="Injete uma falha (./lab impair) e veja o ranking mudar."), 24, 8)
b.row("Relays: usuário → servidor A → malha → servidor de saída B")
b.add(table("Rotas onde o relay ganha do caminho direto", [
    'sort_desc(vpnlab_opt_route_gain_ms > 1)',
    'vpnlab_opt_route_rtt_ms{route="direct"}',
    'label_replace(vpnlab_opt_route_rtt_ms{route="best"}, "via", "$1", "via", "(.*)")'],
    organize={"excludeByName": hide("Time", "__name__", "instance", "job", "route"),
              "renameByName": {"client": "usuário", "exit": "saída", "Value #A": "ganho", "Value #B": "direto", "Value #C": "com relay"}},
    units={"ganho": "ms", "direto": "ms", "com relay": "ms"}, sort=[{"displayName": "ganho", "desc": True}]), 12, 10)
b.add(ts("Medido ponta a ponta pelo relay", [('vpnlab_probe_rtt_avg_ms{path="relay"}', "{{src}} → {{via}} → {{dst}}")],
         desc="Sondas reais que atravessam dois servidores"), 12, 10)
b.row("Entre usuários")
b.add(matrix("RTT usuário ↔ usuário pelo melhor caminho (ms)", 'vpnlab_opt_pair_rtt_ms{route="best"}', "a", "b"), 12, 11)
b.add(table("Melhor caminho entre cada par", ['vpnlab_opt_pair_rtt_ms{route="best"}'],
            organize={"excludeByName": hide("Time", "__name__", "instance", "job", "route"),
                      "renameByName": {"path": "caminho", "Value": "RTT"}}, units={"RTT": "ms"},
            sort=[{"displayName": "RTT", "desc": True}]), 12, 11)
b.save()

# =============================================================================
# 4. Medição do grupo (bloco M2)
# =============================================================================
b = Board("04-medicao", "VPN Lab — 4. Medição do grupo (usuários x desempenho)",
          "Acompanhe ./lab medir: os quatro gráficos dividem o mesmo eixo de tempo. Cada faixa laranja é uma condição (N usuários).")
b.vars.append({"name": "servidor", "label": "Servidor", "type": "custom", "query": "srv-jnb,srv-fra",
               "current": {"text": "srv-jnb", "value": "srv-jnb"}, "options": [
                   {"text": "srv-jnb", "value": "srv-jnb", "selected": True}, {"text": "srv-fra", "value": "srv-fra", "selected": False}],
               "description": "srv-jnb = frente perda (João) · srv-fra = latência (Pedro) e vazão (Renan)"})
b.row("Agora")
b.add(stat("Usuários simultâneos no túnel", 'sum(vpnlab_load_active{server="$servidor"}) or vector(0)'), 6, 4)
b.add(stat("Perda pelo túnel (todos os usuários)", 'avg(avg_over_time(vpnlab_probe_loss_ratio{path="tunnel", dst="$servidor"}[1m])) * 100',
           "percent", "Média de 1 min das sondas de todos os 18 usuários até o servidor"), 6, 4)
b.add(stat("Latência pelo túnel (todos os usuários)", 'avg(avg_over_time(vpnlab_probe_rtt_avg_ms{path="tunnel", dst="$servidor"}[1m]))',
           "ms"), 6, 4)
b.add(stat("Vazão por usuário com carga", 'avg(vpnlab_load_throughput_bps{server="$servidor"} > 0) or vector(0)', "bps"), 6, 4)
b.row("Ao longo da medição (mesmo eixo de tempo)")
p = ts("1. Usuários simultâneos usando o servidor", [('sum(vpnlab_load_active{server="$servidor"}) or vector(0)', "usuários")],
       "short", minv=0)
p["fieldConfig"]["defaults"]["custom"]["lineInterpolation"] = "stepAfter"
p["options"]["legend"]["displayMode"] = "list"
b.add(p, 24, 6)
b.add(ts("2. Perda de pacotes pelo túnel (%) — frente do João", [
    ('avg(avg_over_time(vpnlab_probe_loss_ratio{path="tunnel", dst="$servidor"}[30s])) * 100', "sondas: média dos 18 usuários"),
    ('avg(vpnlab_load_udp_loss_ratio{server="$servidor"}) * 100', "iperf3 UDP: usuários com carga")],
    "percent", minv=0), 24, 8)
b.add(ts("3. Latência pelo túnel (ms) — frente do Pedro", [
    ('avg(vpnlab_probe_rtt_avg_ms{path="tunnel", dst="$servidor"})', "média dos 18 usuários"),
    ('max(vpnlab_probe_rtt_avg_ms{path="tunnel", dst="$servidor"})', "pior usuário")], "ms", minv=0), 24, 8)
b.add(ts("4. Vazão por usuário (Mbit/s) — frente do Renan", [
    ('avg(vpnlab_load_throughput_bps{server="$servidor"} > 0)', "média por usuário"),
    ('sum(vpnlab_load_throughput_bps{server="$servidor"})', "total do servidor")], "bps", minv=0), 24, 8)
b.add(ts("5. Fila na saída do servidor (o buffer que enche e descarta)", [
    ('vpnlab_queue_backlog_bytes{node="$servidor"}', "bytes na fila"),
], "bytes", minv=0), 24, 6)
b.save()
print("dashboards gerados em", OUT)
