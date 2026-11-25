#!/usr/bin/env python3
"""Otimizador de rotas do VPN Lab.

A partir das medições no Prometheus, responde:
  1. Qual servidor é o melhor ponto intermediário (hub) para TODOS os usuários?
     (por média de RTT, pelo pior caso e por qualidade de voz estimada — MOS)
  2. Para cada usuário e cada servidor de saída: ir direto ou passar por um relay?
  3. Para cada par de usuários: qual o melhor caminho entre eles
     (um hub em comum ou dois servidores ligados pela malha)?

Roda como serviço (`--serve`, exporta /metrics para o Grafana) ou é importado pelo labctl.
"""
import itertools
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROM_URL = os.environ.get("PROM_URL", "http://prometheus:9090")
TOPO_PATH = os.environ.get("VPNLAB_TOPOLOGY", "/etc/vpnlab/topology.json")
WINDOW = os.environ.get("OPT_WINDOW", "2m")
OBJECTIVES = {
    "media": "menor RTT médio entre todos os usuários",
    "pior_caso": "menor RTT do usuário mais distante (minimax)",
    "mos": "maior qualidade de voz média estimada (MOS, modelo E simplificado)",
}


# ----------------------------------------------------------------------------
# Prometheus
# ----------------------------------------------------------------------------
def prom_query(expr, at=None, url=None):
    params = {"query": expr}
    if at:
        params["time"] = str(at)
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url or PROM_URL}/api/v1/query?{q}", timeout=15) as r:
        data = json.load(r)
    return [(s["metric"], float(s["value"][1])) for s in data["data"]["result"]]


def prom_range(expr, start, end, step, url=None):
    q = urllib.parse.urlencode({"query": expr, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(f"{url or PROM_URL}/api/v1/query_range?{q}", timeout=30) as r:
        data = json.load(r)
    return [(s["metric"], [(float(t), float(v)) for t, v in s["values"]]) for s in data["data"]["result"]]


def fetch_measurements(window=WINDOW, at=None, url=None):
    """Devolve {(src, dst, path, via): {rtt, loss, jitter, p95}} com médias na janela."""
    out = {}
    for field, metric in (("rtt", "vpnlab_probe_rtt_avg_ms"), ("p95", "vpnlab_probe_rtt_p95_ms"),
                          ("jitter", "vpnlab_probe_jitter_ms"), ("loss", "vpnlab_probe_loss_ratio")):
        for lb, v in prom_query(f"avg_over_time({metric}[{window}])", at, url):
            if math.isnan(v):
                continue
            key = (lb.get("src"), lb.get("dst"), lb.get("path"), lb.get("via", ""))
            out.setdefault(key, {})[field] = v
    # caminho sem nenhuma resposta: só tem perda
    return {k: v for k, v in out.items() if "rtt" in v or v.get("loss") == 1.0}


# ----------------------------------------------------------------------------
# Modelo de qualidade
# ----------------------------------------------------------------------------
def mos(rtt_ms, jitter_ms=0.0, loss_ratio=0.0):
    """MOS estimado (1–4.5) por uma simplificação amplamente usada do modelo E (ITU-T G.107)."""
    eff = rtt_ms / 2 + 2 * jitter_ms + 10
    r = 93.2 - eff / 40 if eff < 160 else 93.2 - (eff - 120) / 10
    r -= 2.5 * loss_ratio * 100
    r = max(0.0, min(100.0, r))
    return round(1 + 0.035 * r + 7e-6 * r * (r - 60) * (100 - r), 3)


def combine(*paths):
    """Compõe trechos em série: soma RTT/jitter, combina perda."""
    keep = 1.0
    for p in paths:
        keep *= 1 - p.get("loss", 0.0)
    return {"rtt": sum(p["rtt"] for p in paths), "jitter": sum(p.get("jitter", 0.0) for p in paths),
            "loss": 1 - keep}


def pct(vals, p):
    s = sorted(vals)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    return s[f] + (s[c] - s[f]) * (k - f)


# ----------------------------------------------------------------------------
# Otimização (funções puras, testáveis)
# ----------------------------------------------------------------------------
def analyze(meas, servers, clients):
    T = {c: {} for c in clients}          # cliente -> servidor (pelo túnel)
    M = {a: {a: {"rtt": 0.0, "jitter": 0.0, "loss": 0.0}} for a in servers}  # malha servidor <-> servidor
    R = {}                                  # (cliente, via, saída) medido de ponta a ponta
    U = {c: {} for c in clients}           # cliente -> servidor sem VPN
    for (src, dst, path, via), v in meas.items():
        if "rtt" not in v:
            v = {**v, "rtt": float("inf")}
        if path == "tunnel" and src in T:
            T[src][dst] = v
        elif path == "underlay" and src in U:
            U[src][dst] = v
        elif path == "mesh" and src in M:
            M[src][dst] = v
        elif path == "relay":
            R[(src, via, dst)] = v
    # malha simétrica: média dos dois sentidos quando houver
    for a, b in itertools.permutations(servers, 2):
        if b in M[a] and a in M.get(b, {}):
            x, y = M[a][b], M[b][a]
            avg = {k: (x.get(k, 0) + y.get(k, 0)) / 2 for k in ("rtt", "jitter", "loss")}
            M[a][b] = M[b][a] = avg

    # ---- 1. melhor hub para todos ----
    hubs = []
    for s in servers:
        legs = [T[c][s] for c in clients if s in T[c] and math.isfinite(T[c][s]["rtt"])]
        if not legs:
            continue
        rtts = [l["rtt"] for l in legs]
        moses = [mos(l["rtt"], l.get("jitter", 0), l.get("loss", 0)) for l in legs]
        pairs = []
        for a, b in itertools.combinations([c for c in clients if s in T[c] and math.isfinite(T[c][s]["rtt"])], 2):
            p = combine(T[a][s], T[b][s])
            pairs.append(p)
        hubs.append({
            "server": s, "coverage": len(legs) / max(1, len(clients)),
            "rtt_mean": sum(rtts) / len(rtts), "rtt_p95": pct(rtts, .95), "rtt_max": max(rtts),
            "loss_mean": sum(l.get("loss", 0) for l in legs) / len(legs),
            "mos_mean": sum(moses) / len(moses), "mos_min": min(moses),
            "pair_rtt_mean": (sum(p["rtt"] for p in pairs) / len(pairs)) if pairs else 0.0,
            "pair_rtt_max": max((p["rtt"] for p in pairs), default=0.0),
            "pair_mos_mean": (sum(mos(p["rtt"], p["jitter"], p["loss"]) for p in pairs) / len(pairs)) if pairs else 0.0,
        })
    # hubs sem cobertura total são penalizados (um usuário sem rota é inaceitável)
    def key(obj):
        return {"media": lambda h: (-h["coverage"], h["rtt_mean"]),
                "pior_caso": lambda h: (-h["coverage"], h["rtt_max"]),
                "mos": lambda h: (-h["coverage"], -h["mos_mean"])}[obj]
    ranking = {obj: [h["server"] for h in sorted(hubs, key=key(obj))] for obj in OBJECTIVES}

    # ---- 2. direto x relay, por usuário e servidor de saída ----
    routes = []
    for c in clients:
        for exit_ in servers:
            direct = T[c].get(exit_)
            cands = []
            for via in servers:
                if via == exit_ or via not in T[c] or exit_ not in M.get(via, {}):
                    continue
                comp = combine(T[c][via], M[via][exit_])
                measured = R.get((c, via, exit_))
                use = measured if measured and math.isfinite(measured.get("rtt", float("inf"))) else comp
                cands.append({"via": via, "rtt": use["rtt"], "loss": use.get("loss", 0), "jitter": use.get("jitter", 0),
                              "composed_rtt": comp["rtt"], "measured_rtt": measured.get("rtt") if measured else None})
            best = min(cands, key=lambda x: x["rtt"], default=None)
            d_rtt = direct["rtt"] if direct else float("inf")
            entry = {"client": c, "exit": exit_, "direct_rtt": d_rtt, "direct_loss": direct.get("loss", 0) if direct else 1.0,
                     "underlay_rtt": U[c].get(exit_, {}).get("rtt"), "best_via": "", "best_rtt": d_rtt, "gain_ms": 0.0}
            if best and best["rtt"] < d_rtt:
                entry.update(best_via=best["via"], best_rtt=best["rtt"], gain_ms=d_rtt - best["rtt"],
                             best_measured=best["measured_rtt"], best_composed=best["composed_rtt"])
            routes.append(entry)

    # ---- 3. melhor caminho entre cada par de usuários ----
    pairs = []
    best_hub = ranking["media"][0] if ranking["media"] else None
    for a, b in itertools.combinations(clients, 2):
        options = []
        for sa in T[a]:
            for sb in T[b]:
                if sb == sa:
                    p = combine(T[a][sa], T[b][sb])
                    path = sa
                elif sb in M.get(sa, {}):
                    p = combine(T[a][sa], M[sa][sb], T[b][sb])
                    path = f"{sa}>{sb}"
                else:
                    continue
                if math.isfinite(p["rtt"]):
                    options.append((p["rtt"], path, p))
        if not options:
            continue
        rtt, path, p = min(options, key=lambda x: x[0])
        hub_rtt = None
        if best_hub and best_hub in T[a] and best_hub in T[b]:
            hub_rtt = combine(T[a][best_hub], T[b][best_hub])["rtt"]
        pairs.append({"a": a, "b": b, "best_path": path, "best_rtt": rtt, "best_mos": mos(p["rtt"], p["jitter"], p["loss"]),
                      "hub": best_hub, "hub_rtt": hub_rtt})

    overhead = []
    for c in clients:
        for s in servers:
            if s in T[c] and s in U[c] and math.isfinite(T[c][s]["rtt"]):
                overhead.append(T[c][s]["rtt"] - U[c][s]["rtt"])

    return {"hubs": hubs, "ranking": ranking, "routes": routes, "pairs": pairs,
            "tunnel_overhead_ms": (sum(overhead) / len(overhead)) if overhead else None,
            "samples": len(meas), "ts": time.time()}


def load_topology(path=TOPO_PATH):
    with open(path) as f:
        t = json.load(f)
    return t, [s["name"] for s in t["servers"]], [c["name"] for c in t["clients"]]


def run_once(window=WINDOW, at=None, url=None, topo_path=TOPO_PATH):
    _, servers, clients = load_topology(topo_path)
    return analyze(fetch_measurements(window, at, url), servers, clients)


# ----------------------------------------------------------------------------
# Serviço: exporta o resultado como métricas
# ----------------------------------------------------------------------------
def to_metrics(res):
    L = []

    def g(name, help_, rows):
        L.append(f"# HELP {name} {help_}")
        L.append(f"# TYPE {name} gauge")
        for labels, v in rows:
            if v is None or (isinstance(v, float) and not math.isfinite(v)):
                continue
            lab = ",".join(f'{k}="{val}"' for k, val in labels.items())
            L.append(f"{name}{{{lab}}} {round(v, 4)}")

    h = res["hubs"]
    g("vpnlab_opt_hub_rtt_ms", "RTT dos usuários até o hub candidato",
      [({"server": x["server"], "stat": st}, x[f"rtt_{st}"]) for x in h for st in ("mean", "p95", "max")])
    g("vpnlab_opt_hub_pair_rtt_ms", "RTT usuário<->usuário passando pelo hub",
      [({"server": x["server"], "stat": st}, x[f"pair_rtt_{st}"]) for x in h for st in ("mean", "max")])
    g("vpnlab_opt_hub_mos", "Qualidade de voz estimada (MOS) até o hub",
      [({"server": x["server"], "stat": st}, x[f"mos_{st}"]) for x in h for st in ("mean", "min")])
    g("vpnlab_opt_hub_loss_ratio", "Perda média até o hub", [({"server": x["server"]}, x["loss_mean"]) for x in h])
    g("vpnlab_opt_hub_coverage_ratio", "Fração de usuários com medição até o hub",
      [({"server": x["server"]}, x["coverage"]) for x in h])
    g("vpnlab_opt_hub_rank", "Posição do hub no ranking (1 = melhor)",
      [({"server": s, "objective": o}, i + 1) for o, lst in res["ranking"].items() for i, s in enumerate(lst)])
    g("vpnlab_opt_best_hub", "Melhor hub por objetivo",
      [({"server": lst[0], "objective": o}, 1) for o, lst in res["ranking"].items() if lst])
    g("vpnlab_opt_route_rtt_ms", "RTT usuário -> servidor de saída: direto e melhor rota",
      [({"client": r["client"], "exit": r["exit"], "route": "direct", "via": ""}, r["direct_rtt"]) for r in res["routes"]] +
      [({"client": r["client"], "exit": r["exit"], "route": "best", "via": r["best_via"] or "direto"}, r["best_rtt"]) for r in res["routes"]])
    g("vpnlab_opt_route_gain_ms", "Quanto o relay economiza em relação a ir direto (ms)",
      [({"client": r["client"], "exit": r["exit"], "via": r["best_via"]}, r["gain_ms"]) for r in res["routes"] if r["best_via"]])
    g("vpnlab_opt_pair_rtt_ms", "RTT entre pares de usuários: melhor caminho e via hub único",
      [({"a": p["a"], "b": p["b"], "route": "best", "path": p["best_path"]}, p["best_rtt"]) for p in res["pairs"]] +
      [({"a": p["a"], "b": p["b"], "route": "hub", "path": p["hub"] or ""}, p["hub_rtt"]) for p in res["pairs"]])
    g("vpnlab_opt_tunnel_overhead_ms", "Acréscimo médio de RTT do túnel em relação à rede sem VPN",
      [({}, res["tunnel_overhead_ms"])])
    g("vpnlab_opt_samples", "Caminhos com medição na janela", [({}, res["samples"])])
    g("vpnlab_opt_last_run_timestamp_seconds", "Última execução", [({}, res["ts"])])
    return "\n".join(x.replace("{}", "") for x in L) + "\n"


def serve(port=9200, every=15):
    state = {"metrics": "# aguardando primeira execução\n", "json": {}}

    def loop():
        while True:
            try:
                res = run_once()
                state["metrics"] = to_metrics(res)
                state["json"] = res
            except Exception as e:  # noqa: BLE001
                print(f"otimizador: {e}", file=sys.stderr, flush=True)
            time.sleep(every)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/metrics"):
                body, ct = state["metrics"].encode(), "text/plain; version=0.0.4"
            else:
                body, ct = json.dumps(state["json"], default=str).encode(), "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    threading.Thread(target=loop, daemon=True).start()
    print(f"otimizador em :{port} (janela {WINDOW}, a cada {every}s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        print(json.dumps(run_once(), indent=1, default=str))
