#!/usr/bin/env python3
"""labctl — controlador do VPN Lab (roda dentro da rede de gerência).

Comandos (use ./lab <comando> --help no seu computador):
  status       estado de cada nó: túneis, handshakes, emulação, sondas
  matrix       matriz de RTT/perda usuário x servidor (túnel ou sem VPN)
  optimize     melhor servidor intermediário para todos + relays + pares
  load         carga simultânea de vários usuários num servidor
  ramp         rampa de usuários até saturar o túnel (curva de capacidade)
  abuse        um usuário abusivo x usuários normais (justiça / impacto)
  impair       injeta falhas: atraso, perda, jitter, queda de enlace, banda
  reset        remove todas as falhas injetadas
  medir        protocolo do grupo: latencia | vazao | perda (condições 0/2/8/18, 3 repetições)
  report       relatório HTML com todas as medições (pasta results/)
"""
import argparse
import base64
import csv
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimizer as opt  # noqa: E402

TOPO_PATH = os.environ.get("VPNLAB_TOPOLOGY", "/etc/vpnlab/topology.json")
RESULTS = os.environ.get("VPNLAB_RESULTS", "/results")
GRAFANA = os.environ.get("GRAFANA_URL", "http://grafana:3000")


# ----------------------------------------------------------------------------
# utilitários
# ----------------------------------------------------------------------------
def topo():
    with open(TOPO_PATH) as f:
        return json.load(f)


def nodes(t):
    return {n["name"]: n for n in t["servers"] + t["clients"]}


def http(method, url, body=None, timeout=10, auth=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", str(e))
        except Exception:  # noqa: BLE001
            msg = str(e)
        raise RuntimeError(msg) from None


def agent(t, name, method, path, body=None, timeout=10):
    n = nodes(t)[name]
    return http(method, f"http://{n['mgmt_ip']}:{t['agent_port']}{path}", body, timeout)


def annotate(text, tags, t0=None, t1=None):
    """Marca o teste no Grafana (aparece como faixa nos gráficos)."""
    body = {"text": text, "tags": ["vpnlab"] + tags, "time": int((t0 or time.time()) * 1000)}
    if t1:
        body["timeEnd"] = int(t1 * 1000)
    try:
        http("POST", f"{GRAFANA}/api/annotations", body, 5, auth="admin:admin")
    except Exception:  # noqa: BLE001
        pass


def table(rows, headers, fmt=None):
    fmt = fmt or {}
    cells = [[(fmt.get(h, "{}").format(r.get(h)) if r.get(h) is not None else "-") for h in headers] for r in rows]
    w = [max(len(h), *(len(c[i]) for c in cells)) if cells else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w[i] for i in range(len(headers))))
    for c in cells:
        print("  ".join(c[i].rjust(w[i]) if c[i][:1].isdigit() or c[i][:1] == "-" else c[i].ljust(w[i])
                        for i in range(len(headers))))


def save(kind, data):
    os.makedirs(RESULTS, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RESULTS, f"{stamp}-{kind}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, default=str)
    return path


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, default=str)


def select_clients(t, spec):
    """'all', 'region:sao', 'cli-a,cli-b' ou um número N (N clientes alternando regiões)."""
    clients = [c["name"] for c in t["clients"]]
    if spec in (None, "all"):
        return clients
    if spec.startswith("region:"):
        regs = spec.split(":", 1)[1].split(",")
        return [c["name"] for c in t["clients"] if c["region"] in regs]
    if spec.isdigit():
        return interleave(t)[: int(spec)]
    out = spec.split(",")
    for c in out:
        if c not in clients:
            raise SystemExit(f"cliente desconhecido: {c}")
    return out


def interleave(t):
    """Ordem de entrada dos usuários na rampa: alterna regiões (mais realista)."""
    by_reg = {}
    for c in t["clients"]:
        by_reg.setdefault(c["region"], []).append(c["name"])
    out = []
    while any(by_reg.values()):
        for r in list(by_reg):
            if by_reg[r]:
                out.append(by_reg[r].pop(0))
    return out


def jain(xs):
    xs = [x for x in xs if x is not None]
    if not xs or sum(x * x for x in xs) == 0:
        return None
    return sum(xs) ** 2 / (len(xs) * sum(x * x for x in xs))


def mbps(bps):
    return None if bps is None else bps / 1e6


# ----------------------------------------------------------------------------
# status / matrix / optimize
# ----------------------------------------------------------------------------
def cmd_status(a):
    t = topo()
    rows = []
    for name, n in nodes(t).items():
        try:
            s = agent(t, name, "GET", "/state", timeout=5)
            peers = s["wg"]["peers"]
            hs = sum(1 for p in peers if p["handshake"] and time.time() - p["handshake"] < 180)
            ok = sum(1 for p in s["probes"] if p["last"] and p["last"].get("received"))
            rows.append({"nó": name, "papel": n["role"], "região": n["region"],
                         "emulação": "ativa" if s["emulation"]["active"] else "INATIVA",
                         "wg": s["wg"]["mode"], "túneis ok": f"{sum(s['wg']['up'].values())}/{len(s['wg']['up'])}",
                         "handshakes": f"{hs}/{len(peers)}", "sondas ok": f"{ok}/{len(s['probes'])}",
                         "falhas": ",".join(s["emulation"]["override"]) or ""})
        except Exception as e:  # noqa: BLE001
            rows.append({"nó": name, "papel": n["role"], "região": n["region"], "emulação": f"OFFLINE ({e})"})
    table(rows, ["nó", "papel", "região", "emulação", "wg", "túneis ok", "handshakes", "sondas ok", "falhas"])


def cmd_matrix(a):
    t = topo()
    meas = opt.fetch_measurements(a.window)
    servers = [s["name"] for s in t["servers"]]
    field = {"rtt": "rtt", "loss": "loss", "jitter": "jitter", "p95": "p95"}[a.metric]
    print(f"\n{a.metric.upper()} usuário -> servidor, caminho '{a.path}', média dos últimos {a.window}\n")
    rows = []
    for c in t["clients"]:
        r = {"usuário": c["name"]}
        for s in servers:
            v = meas.get((c["name"], s, a.path, ""), {}).get(field)
            r[s] = None if v is None else (v * 100 if field == "loss" else v)
        rows.append(r)
    unit = "{:.1f}" if field != "loss" else "{:.2f}%"
    table(rows, ["usuário"] + servers, {s: unit for s in servers})
    exp = t["expected"]
    over, err = [], []
    for c in t["clients"]:
        for s in servers:
            v = meas.get((c["name"], s, a.path, ""), {}).get("rtt")
            u = meas.get((c["name"], s, "underlay", ""), {}).get("rtt")
            e = exp.get(f"{c['name']}|{s}", {}).get("rtt_ms")
            if a.path == "tunnel" and v is not None and u is not None:
                over.append(v - u)
            if u is not None and e:
                err.append(abs(u - e) / e)
    if over:
        print(f"\nSobrecarga do túnel (túnel - sem VPN): {sum(over) / len(over):+.2f} ms em média")
    if err:
        print(f"Emulação: RTT sem VPN ficou {sum(err) / len(err) * 100:.1f}% distante do configurado em topology.toml"
              + ("  <- emulação inativa? rode ./lab status" if sum(err) / len(err) > 0.5 else ""))


def cmd_optimize(a):
    t = topo()
    res = opt.run_once(a.window, topo_path=TOPO_PATH)
    if not res["hubs"]:
        raise SystemExit("ainda não há medições suficientes (aguarde ~1 minuto após ./lab up)")
    print(f"\n=== 1. Melhor servidor intermediário (hub) para todos — janela {a.window} ===\n")
    hubs = sorted(res["hubs"], key=lambda h: res["ranking"][a.objective].index(h["server"]))
    table([{**h, "cobertura": f"{h['coverage'] * 100:.0f}%", "perda": h["loss_mean"] * 100} for h in hubs],
          ["server", "cobertura", "rtt_mean", "rtt_p95", "rtt_max", "perda", "mos_mean", "mos_min", "pair_rtt_mean", "pair_rtt_max"],
          {"rtt_mean": "{:.1f}", "rtt_p95": "{:.1f}", "rtt_max": "{:.1f}", "perda": "{:.2f}%", "mos_mean": "{:.2f}",
           "mos_min": "{:.2f}", "pair_rtt_mean": "{:.1f}", "pair_rtt_max": "{:.1f}"})
    print("\nVencedor por objetivo:")
    for o, desc in opt.OBJECTIVES.items():
        if res["ranking"][o]:
            print(f"  {o:10} -> {res['ranking'][o][0]:10}  ({desc})")

    print("\n=== 2. Rotas em que um relay é melhor que ir direto ===\n")
    gains = sorted([r for r in res["routes"] if r["best_via"] and r["gain_ms"] >= a.min_gain], key=lambda r: -r["gain_ms"])
    if gains:
        table(gains[: a.top], ["client", "exit", "direct_rtt", "best_via", "best_rtt", "gain_ms", "best_measured"],
              {"direct_rtt": "{:.1f}", "best_rtt": "{:.1f}", "gain_ms": "{:.1f}", "best_measured": "{:.1f}"})
    else:
        print(f"  nenhuma rota com ganho >= {a.min_gain} ms")

    print("\n=== 3. Pares de usuários: melhor caminho x hub único ===\n")
    pairs = sorted(res["pairs"], key=lambda p: -(p["hub_rtt"] or 0) + p["best_rtt"])
    table(pairs[: a.top], ["a", "b", "best_path", "best_rtt", "best_mos", "hub", "hub_rtt"],
          {"best_rtt": "{:.1f}", "best_mos": "{:.2f}", "hub_rtt": "{:.1f}"})
    if res["tunnel_overhead_ms"] is not None:
        print(f"\nSobrecarga média do túnel (túnel - sem VPN): {res['tunnel_overhead_ms']:+.2f} ms")
    print("\nsalvo em", save("optimize", res))


# ----------------------------------------------------------------------------
# carga
# ----------------------------------------------------------------------------
def start_loads(t, clients, server, a, prefix):
    started = []
    for c in clients:
        body = {"id": f"{prefix}-{c}", "server": server, "duration": a.duration, "parallel": a.parallel,
                "proto": a.proto, "direction": a.direction, "rate": a.rate}
        try:
            agent(t, c, "POST", "/load", body)
            started.append(c)
        except RuntimeError as e:
            print(f"  ! {c}: {e}")
    return started


def probe_rtt(t, client, server):
    try:
        s = agent(t, client, "GET", "/state", timeout=5)
    except Exception:  # noqa: BLE001
        return None, None
    for p in s["probes"]:
        if p["dst"] == server and p["path"] == "tunnel" and p["last"]:
            return p["last"].get("avg"), p["last"].get("loss")
    return None, None


def collect(t, clients, server, load_id_prefix, duration, sample_every=3, observers=()):
    """Espera a carga terminar, amostrando RTT pelo túnel de quem carrega e de quem só observa."""
    rtt_load, rtt_obs = [], []
    t_end = time.time() + duration + 2
    while time.time() < t_end:
        time.sleep(sample_every)
        for c in clients:
            r, _ = probe_rtt(t, c, server)
            if r:
                rtt_load.append(r)
        for c in observers:
            r, _ = probe_rtt(t, c, server)
            if r:
                rtt_obs.append(r)
    time.sleep(2)
    per = []
    for c in clients:
        lst = agent(t, c, "GET", "/load")
        mine = [l for l in lst if l["id"] == f"{load_id_prefix}-{c}"]
        if mine:
            l = mine[-1]
            per.append({"client": c, "mbps": mbps(l["bps_avg"]), "retr": l["retransmits"],
                        "udp_loss": (l["udp"]["lost"] / l["udp"]["total"]) if l["udp"].get("total") else None,
                        "udp_jitter": l["udp"].get("jitter_ms"), "error": l["error"][:80]})
    return per, rtt_load, rtt_obs


def summarize(per, rtt_load, rtt_obs):
    vals = [p["mbps"] for p in per if p["mbps"] is not None]
    return {"users": len(per), "total_mbps": sum(vals), "per_user_mbps": (sum(vals) / len(vals)) if vals else 0,
            "min_user_mbps": min(vals, default=0), "max_user_mbps": max(vals, default=0), "fairness": jain(vals),
            "rtt_under_load_ms": (sum(rtt_load) / len(rtt_load)) if rtt_load else None,
            "rtt_observers_ms": (sum(rtt_obs) / len(rtt_obs)) if rtt_obs else None,
            "retransmits": sum(p["retr"] or 0 for p in per),
            "udp_loss": (sum(p["udp_loss"] for p in per if p["udp_loss"] is not None) / max(1, sum(1 for p in per if p["udp_loss"] is not None)))
            if any(p["udp_loss"] is not None for p in per) else None}


def baseline_rtt(t, clients, server):
    vals = [r for r, _ in (probe_rtt(t, c, server) for c in clients) if r]
    return (sum(vals) / len(vals)) if vals else None


def cmd_load(a):
    t = topo()
    clients = select_clients(t, a.clients)
    observers = [c["name"] for c in t["clients"] if c["name"] not in clients][:3]
    print(f"carga: {len(clients)} usuários -> {a.server} ({a.proto}, {a.direction}, {a.duration}s, rate={a.rate})")
    base = baseline_rtt(t, clients, a.server)
    t0 = time.time()
    prefix = f"load{int(t0)}"
    started = start_loads(t, clients, a.server, a, prefix)
    per, rl, ro = collect(t, started, a.server, prefix, a.duration, observers=observers)
    annotate(f"carga {len(started)} usuários -> {a.server}", ["load", a.server], t0, time.time())
    s = summarize(per, rl, ro)
    s["rtt_idle_ms"] = base
    table(per, ["client", "mbps", "retr", "udp_loss", "udp_jitter", "error"],
          {"mbps": "{:.2f}", "udp_loss": "{:.4f}", "udp_jitter": "{:.2f}"})
    print(f"\nTotal {s['total_mbps']:.1f} Mbit/s | por usuário {s['per_user_mbps']:.2f} | justiça (Jain) "
          f"{s['fairness'] or 0:.3f} | RTT ocioso {base or 0:.1f} ms -> sob carga {s['rtt_under_load_ms'] or 0:.1f} ms")
    print("salvo em", save("load", {"args": vars(a), "server": a.server, "summary": s, "per_client": per, "t0": t0, "t1": time.time()}))


def cmd_ramp(a):
    t = topo()
    order = [c for c in interleave(t)]
    steps = [int(x) for x in a.steps.split(",")]
    if max(steps) > len(order):
        raise SystemExit(f"só existem {len(order)} clientes")
    print(f"rampa em {a.server}: degraus {steps}, {a.duration}s cada ({a.proto}, {a.direction})")
    t_start = time.time()
    base = baseline_rtt(t, order, a.server)
    results = []
    for k in steps:
        users = order[:k]
        observers = order[k:k + 3]
        t0 = time.time()
        prefix = f"ramp{int(t0)}"
        started = start_loads(t, users, a.server, a, prefix)
        per, rl, ro = collect(t, started, a.server, prefix, a.duration, observers=observers)
        s = summarize(per, rl, ro)
        s.update(step_users=k, t0=t0, t1=time.time(), per_client=per)
        results.append(s)
        annotate(f"rampa {a.server}: {k} usuários", ["ramp", a.server], t0, s["t1"])
        print(f"  {k:3d} usuários: total {s['total_mbps']:7.1f} Mbit/s | por usuário {s['per_user_mbps']:6.2f} | "
              f"pior {s['min_user_mbps']:6.2f} | Jain {s['fairness'] or 0:.3f} | RTT {s['rtt_under_load_ms'] or 0:6.1f} ms"
              f" | retrans {s['retransmits']}")
        time.sleep(a.pause)
    # joelho da curva: primeiro degrau em que adicionar usuários rende < 10% de vazão a mais
    knee = None
    for prev, cur in zip(results, results[1:]):
        if prev["total_mbps"] > 0 and cur["total_mbps"] < prev["total_mbps"] * 1.10:
            knee = prev["step_users"]
            break
    print(f"\nRTT ocioso: {base or 0:.1f} ms. Saturação (joelho) por volta de: {knee or 'não atingida'} usuários")
    path = save("ramp", {"args": vars(a), "server": a.server, "baseline_rtt_ms": base, "knee_users": knee,
                         "steps": results, "t0": t_start, "t1": time.time()})
    with open(path.replace(".json", ".csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["usuarios", "vazao_total_mbps", "por_usuario_mbps", "pior_usuario_mbps", "justica_jain",
                    "rtt_sob_carga_ms", "rtt_observadores_ms", "retransmissoes", "perda_udp"])
        for s in results:
            w.writerow([s["step_users"], round(s["total_mbps"], 3), round(s["per_user_mbps"], 3), round(s["min_user_mbps"], 3),
                        round(s["fairness"] or 0, 4), s["rtt_under_load_ms"], s["rtt_observers_ms"], s["retransmits"], s["udp_loss"]])
    print("salvo em", path, "(+ .csv)")


def cmd_abuse(a):
    t = topo()
    normal = [c for c in interleave(t) if c != a.heavy][: a.normal]
    print(f"fase 1: {len(normal)} usuários normais ({a.normal_rate}) -> {a.server}")

    class N:
        duration, parallel, proto, direction, rate = a.duration, 1, a.proto, "down", a.normal_rate

    t0 = time.time()
    p = f"abA{int(t0)}"
    st = start_loads(t, normal, a.server, N, p)
    per1, rl1, _ = collect(t, st, a.server, p, a.duration)
    s1 = summarize(per1, rl1, [])
    annotate("abuso fase 1: só normais", ["abuse", a.server], t0, time.time())
    time.sleep(3)

    print(f"fase 2: + {a.heavy} abusivo ({a.heavy_parallel} conexões, sem limite)")

    class Hv:
        duration, parallel, proto, direction, rate = a.duration, a.heavy_parallel, "tcp", "down", "0"

    t1 = time.time()
    p2 = f"abB{int(t1)}"
    start_loads(t, [a.heavy], a.server, Hv, p2)
    st = start_loads(t, normal, a.server, N, p2)
    per2, rl2, _ = collect(t, st + [a.heavy], a.server, p2, a.duration)
    annotate(f"abuso fase 2: {a.heavy} abusivo", ["abuse", a.server], t1, time.time())
    heavy = [x for x in per2 if x["client"] == a.heavy]
    s2 = summarize([x for x in per2 if x["client"] != a.heavy], rl2, [])
    print(f"\n{'':28}{'sem abusivo':>14}{'com abusivo':>14}")
    for label, k, f in (("vazão média normal (Mbit/s)", "per_user_mbps", "{:.2f}"), ("pior usuário normal (Mbit/s)", "min_user_mbps", "{:.2f}"),
                        ("RTT sob carga (ms)", "rtt_under_load_ms", "{:.1f}"), ("justiça entre normais", "fairness", "{:.3f}")):
        print(f"{label:28}{f.format(s1[k] or 0):>14}{f.format(s2[k] or 0):>14}")
    if heavy:
        print(f"\nO usuário abusivo sozinho puxou {heavy[0]['mbps'] or 0:.1f} Mbit/s")
    print("salvo em", save("abuse", {"args": vars(a), "server": a.server, "phase1": s1, "phase2": s2, "heavy": heavy,
                                     "per1": per1, "per2": per2}))


# ----------------------------------------------------------------------------
# falhas
# ----------------------------------------------------------------------------
def cmd_impair(a):
    t = topo()
    targets = list(nodes(t)) if a.node == "all" else a.node.split(",")
    body = {"dst": a.dst}
    for k in ("add_delay_ms", "jitter_ms", "loss_pct", "uplink_mbit", "downlink_mbit"):
        v = getattr(a, k)
        if v is not None:
            body[k] = v
    if a.down:
        body["down"] = True
    for n in targets:
        try:
            agent(t, n, "POST", "/impair", body)
            print(f"  {n}: {body}")
        except RuntimeError as e:
            print(f"  ! {n}: {e}")
    if a.both and a.dst != "*":
        rev = {**body, "dst": a.node}
        for d in a.dst.split(","):
            try:
                agent(t, d, "POST", "/impair", rev)
                print(f"  {d}: {rev}")
            except RuntimeError as e:
                print(f"  ! {d}: {e}")
    annotate(f"falha: {a.node} -> {a.dst} {body}", ["impair"])


def cmd_reset(a):
    t = topo()
    for n in nodes(t):
        try:
            agent(t, n, "POST", "/impair/reset", {})
        except Exception as e:  # noqa: BLE001
            print(f"  ! {n}: {e}")
    annotate("falhas removidas", ["impair"])
    print("todas as falhas removidas")


def cmd_medir(a):
    import medir
    medir.cmd_medir(sys.modules[__name__], a)


def cmd_report(a):
    import report
    path = report.build(topo(), a.window, RESULTS)
    print("relatório: results/" + os.path.basename(path))


# ----------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(prog="lab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="estado dos nós")

    m = sub.add_parser("matrix", help="matriz RTT/perda usuário x servidor")
    m.add_argument("--path", default="tunnel", choices=["tunnel", "underlay"])
    m.add_argument("--metric", default="rtt", choices=["rtt", "p95", "jitter", "loss"])
    m.add_argument("--window", default="2m")

    o = sub.add_parser("optimize", help="melhor hub, relays e rotas entre pares")
    o.add_argument("--objective", default="media", choices=list(opt.OBJECTIVES))
    o.add_argument("--window", default="5m")
    o.add_argument("--min-gain", type=float, default=5.0)
    o.add_argument("--top", type=int, default=15)

    def load_args(x, duration=30):
        x.add_argument("--server", required=True)
        x.add_argument("--duration", type=int, default=duration)
        x.add_argument("--proto", default="tcp", choices=["tcp", "udp"])
        x.add_argument("--direction", default="down", choices=["down", "up"])

    lo = sub.add_parser("load", help="carga simultânea")
    load_args(lo)
    lo.add_argument("--clients", default="all", help="all | region:sao,fra | cli-a,cli-b | N")
    lo.add_argument("--parallel", type=int, default=1)
    lo.add_argument("--rate", default="0", help="por usuário, ex.: 20M (0 = sem limite; UDP exige)")

    r = sub.add_parser("ramp", help="rampa de usuários até saturar")
    load_args(r, 25)
    r.add_argument("--steps", default="1,2,4,6,8,12,18")
    r.add_argument("--parallel", type=int, default=1)
    r.add_argument("--rate", default="0")
    r.add_argument("--pause", type=int, default=5)

    ab = sub.add_parser("abuse", help="usuário abusivo x normais")
    ab.add_argument("--server", required=True)
    ab.add_argument("--heavy", required=True, help="cliente abusivo")
    ab.add_argument("--heavy-parallel", type=int, default=8)
    ab.add_argument("--normal", type=int, default=6)
    ab.add_argument("--normal-rate", default="5M")
    ab.add_argument("--proto", default="tcp", choices=["tcp", "udp"])
    ab.add_argument("--duration", type=int, default=30)

    i = sub.add_parser("impair", help="injeta falhas")
    i.add_argument("--node", required=True, help="nó de origem (ou all, ou lista)")
    i.add_argument("--dst", default="*", help="destino (nó) ou * para todos")
    i.add_argument("--add-delay-ms", type=float)
    i.add_argument("--jitter-ms", type=float)
    i.add_argument("--loss-pct", type=float)
    i.add_argument("--uplink-mbit", type=float)
    i.add_argument("--downlink-mbit", type=float)
    i.add_argument("--down", action="store_true", help="derruba o enlace (100%% de perda)")
    i.add_argument("--both", action="store_true", help="aplica também no sentido contrário")
    sub.add_parser("reset", help="remove falhas")

    me = sub.add_parser("medir", help="protocolo de medição do grupo (uma frente por integrante)")
    me.add_argument("frente", choices=["latencia", "vazao", "perda"])
    me.add_argument("--users", default="0,2,8,18", help="condições: número de usuários simultâneos")
    me.add_argument("--reps", type=int, default=3, help="repetições por condição")
    me.add_argument("--duration", type=int, default=40, help="segundos por condição")
    me.add_argument("--settle", type=int, default=10, help="pausa entre condições (s)")
    me.add_argument("--server", help="troca o servidor padrão da frente")
    me.add_argument("--rate", help="taxa por usuário (UDP), ex.: 8M")
    me.add_argument("--rapido", action="store_true", help="teste de fumaça: 0 e 2 usuários, 1 repetição, 20 s")
    me.add_argument("--forcar", action="store_true", help="mede mesmo com a emulação de rede inativa")

    rp = sub.add_parser("report", help="gera relatório HTML")
    rp.add_argument("--window", default="5m")

    a = p.parse_args()
    {"status": cmd_status, "matrix": cmd_matrix, "optimize": cmd_optimize, "load": cmd_load, "ramp": cmd_ramp,
     "abuse": cmd_abuse, "impair": cmd_impair, "reset": cmd_reset, "report": cmd_report,
     "medir": cmd_medir}[a.cmd](a)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrompido")
