"""Protocolo de medição do grupo (bloco M2).

Variável: número de usuários usando o mesmo servidor VPN ao mesmo tempo.
Condições padrão: 0 (ociosa), 2 (baixa), 8 (média), 18 (alta). 3 repetições por condição.

Cada frente mede a sua métrica, sempre do mesmo jeito em todas as condições:
  latencia  RTT pelo túnel (ms) de TODOS os usuários até o servidor, enquanto N usuários baixam por TCP
  vazao     vazão média por usuário (Mbit/s) dos N usuários baixando por TCP
  perda     perda de pacotes (%) pelo túnel de TODOS os usuários, enquanto N usuários recebem UDP a taxa fixa

Saídas em results/:
  medicao-<frente>-<data>.csv          uma linha por (condição, repetição)
  medicao-<frente>-<data>-resumo.csv   média, desvio, mín, máx e variação % por condição
  medicao-<frente>-<data>.json         tudo, mais a descrição da máquina
"""
import concurrent.futures as cf
import csv
import math
import os
import platform
import statistics
import time

FRENTES = {
    "latencia": {"server": "srv-fra", "proto": "tcp", "rate": "0", "metric": "rtt_ms", "unit": "ms",
                 "titulo": "Latência (RTT) pelo túnel", "melhor": "menor"},
    "vazao": {"server": "srv-fra", "proto": "tcp", "rate": "0", "metric": "mbps_por_usuario", "unit": "Mbit/s",
              "titulo": "Vazão por usuário", "melhor": "maior"},
    "perda": {"server": "srv-jnb", "proto": "udp", "rate": "8M", "metric": "perda_pct", "unit": "%",
              "titulo": "Perda de pacotes pelo túnel", "melhor": "menor"},
}
NOMES = {0: "ociosa", 2: "baixa", 8: "media", 18: "alta"}
CAMPOS = ["frente", "condicao", "usuarios", "repeticao", "inicio", "servidor", "rtt_ms", "jitter_ms", "perda_pct",
          "pacotes_sonda", "mbps_por_usuario", "mbps_total", "justica_jain", "perda_udp_pct", "retransmissoes", "rodadas"]


def machine_info():
    info = {"host": os.environ.get("HOST_NAME") or platform.node(), "kernel": platform.release(),
            "nucleos": os.cpu_count()}
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
        with open("/proc/meminfo") as f:
            info["ram_gb"] = round(int(f.readline().split()[1]) / 1048576, 1)
    except OSError:
        pass
    return info


def condition_name(n):
    return NOMES.get(n, f"{n}_usuarios")


def run_condition(lab, t, f, n, rep, duration, warmup, order, frente):
    """Roda uma condição e devolve uma linha de resultado."""
    clients = [c["name"] for c in t["clients"]]
    server = f["server"]
    loaders = order[:n]
    t0 = time.time()
    prefix = f"m{frente[:3]}{int(t0)}"

    class A:
        pass
    A.duration, A.parallel, A.proto, A.direction, A.rate = duration, 1, f["proto"], "down", f["rate"]
    started = lab.start_loads(t, loaders, server, A, prefix) if n else []

    seen, rounds = set(), []

    def sample(c):
        try:
            s = lab.agent(t, c, "GET", "/state", timeout=5)
        except Exception:  # noqa: BLE001
            return c, None
        for p in s["probes"]:
            if p["dst"] == server and p["path"] == "tunnel":
                return c, p["last"]
        return c, None

    t_end = t0 + duration
    with cf.ThreadPoolExecutor(max_workers=min(24, len(clients))) as ex:
        while time.time() < t_end:
            tick = time.time()
            for c, last in ex.map(sample, clients):
                if not last or "sent" not in last:
                    continue
                key = (c, last["ts"])
                if key in seen or last["ts"] < t0 + warmup or last["ts"] > t_end:
                    continue
                seen.add(key)
                rounds.append(last)
            time.sleep(max(0.2, 2 - (time.time() - tick)))

    per = []
    if started:
        # espera as cargas terminarem e lê o resultado de cada usuário
        deadline = time.time() + 15
        while time.time() < deadline:
            per = []
            for c in started:
                lst = lab.agent(t, c, "GET", "/load")
                mine = [l for l in lst if l["id"] == f"{prefix}-{c}"]
                if mine:
                    per.append(mine[-1])
            if per and not any(l["active"] for l in per):
                break
            time.sleep(1)

    sent = sum(r["sent"] for r in rounds)
    recv = sum(r["received"] for r in rounds)
    rtts = [r["avg"] for r in rounds if "avg" in r]
    jit = [r["jitter"] for r in rounds if "jitter" in r]
    mb = [l["bps_avg"] / 1e6 for l in per if l.get("bps_avg")]
    udp = [l["udp"] for l in per if l.get("udp", {}).get("total")]
    row = {
        "frente": frente, "condicao": condition_name(n), "usuarios": n, "repeticao": rep,
        "inicio": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "servidor": server,
        "rtt_ms": round(statistics.mean(rtts), 3) if rtts else None,
        "jitter_ms": round(statistics.mean(jit), 3) if jit else None,
        "perda_pct": round(100 * (1 - recv / sent), 4) if sent else None,
        "pacotes_sonda": sent,
        "mbps_por_usuario": round(statistics.mean(mb), 3) if mb else None,
        "mbps_total": round(sum(mb), 3) if mb else None,
        "justica_jain": round(lab.jain(mb), 4) if mb else None,
        "perda_udp_pct": round(100 * sum(u["lost"] for u in udp) / sum(u["total"] for u in udp), 4) if udp else None,
        "retransmissoes": sum(l.get("retransmits") or 0 for l in per) if per else None,
        "rodadas": len(rounds),
    }
    erros = [l["error"] for l in per if l.get("error")]
    if erros:
        print(f"    ! iperf3: {erros[0][:120]}")
    if n and len(started) < n:
        print(f"    ! só {len(started)} de {n} usuários iniciaram a carga")
    lab.annotate(f"medir {frente}: {n} usuários (rep {rep})", ["medir", frente, server], t0, time.time())
    return row


def summarize(rows, metric):
    out = []
    by_n = {}
    for r in rows:
        if r[metric] is not None:
            by_n.setdefault(r["usuarios"], []).append(r[metric])
    base_n = min(by_n) if by_n else None
    base = statistics.mean(by_n[base_n]) if base_n is not None else None
    for n in sorted(by_n):
        v = by_n[n]
        m = statistics.mean(v)
        out.append({"usuarios": n, "condicao": condition_name(n), "repeticoes": len(v), "media": round(m, 4),
                    "desvio": round(statistics.stdev(v), 4) if len(v) > 1 else 0.0,
                    "minimo": round(min(v), 4), "maximo": round(max(v), 4),
                    "variacao_pct": round(100 * (m - base) / base, 2) if base else None,
                    "base_usuarios": base_n})
    return out


def cmd_medir(lab, a):
    f = dict(FRENTES[a.frente])
    if a.server:
        f["server"] = a.server
    if a.rate:
        f["rate"] = a.rate
    t = lab.topo()
    if f["server"] not in [s["name"] for s in t["servers"]]:
        raise SystemExit(f"servidor desconhecido: {f['server']}")
    users = [int(x) for x in a.users.split(",")]
    reps, duration, settle = a.reps, a.duration, a.settle
    if a.rapido:
        users, reps, duration, settle = [0, 2], 1, 20, 3
        if a.frente == "vazao":
            users = [2, 4]
    inativos = []
    for n in [f["server"]] + [c["name"] for c in t["clients"]]:
        try:
            if not lab.agent(t, n, "GET", "/state", timeout=5)["emulation"]["active"]:
                inativos.append(n)
        except Exception:  # noqa: BLE001
            inativos.append(n + " (offline)")
    if inativos and not a.forcar:
        raise SystemExit("Emulação de rede inativa ou nó fora do ar em: " + ", ".join(inativos[:5]) +
                         ("..." if len(inativos) > 5 else "") +
                         "\nOs números não representariam a 'internet' simulada. Veja 'Problemas comuns' no GUIA.md"
                         "\n(para medir assim mesmo, acrescente --forcar)")
    order = lab.interleave(t)
    if max(users) > len(order):
        raise SystemExit(f"a topologia só tem {len(order)} usuários")
    if a.frente == "vazao" and 0 in users:
        users = [u for u in users if u] or [2]
        print("(vazão não existe com 0 usuários: a base de comparação é a menor condição com carga)")

    plan = []
    for rep in range(1, reps + 1):
        seq = users if rep % 2 else list(reversed(users))  # alterna a ordem para não enviesar por deriva
        plan += [(rep, n) for n in seq]
    total = len(plan) * (duration + settle + 6)
    print(f"Frente: {f['titulo']} ({f['unit']}) · servidor {f['server']} · carga {f['proto'].upper()}"
          f"{' a ' + f['rate'] + 'bit/s por usuário' if f['rate'] != '0' else ' sem limite'}")
    print(f"Condições (usuários): {users} · {reps} repetições · {duration}s cada · ~{math.ceil(total / 60)} min no total\n")

    for c in [c["name"] for c in t["clients"]]:
        try:
            lab.agent(t, c, "POST", "/load/stop", {})
        except Exception:  # noqa: BLE001
            pass
    time.sleep(settle)

    rows = []
    t_start = time.time()
    for i, (rep, n) in enumerate(plan, 1):
        print(f"  [{i:2d}/{len(plan)}] rep {rep} · {n:2d} usuários ({condition_name(n)}) ... ", end="", flush=True)
        row = run_condition(lab, t, f, n, rep, duration, 3, order, a.frente)
        rows.append(row)
        v = row[f["metric"]]
        extra = f" · {row['rodadas']} rodadas de sonda" if f["metric"] != "mbps_por_usuario" else ""
        print(f"{f['titulo'].lower()}: {('%.3f' % v) if v is not None else 'sem dado'} {f['unit']}{extra}")
        time.sleep(settle)

    resumo = summarize(rows, f["metric"])
    print(f"\nResumo — {f['titulo']} ({f['unit']}), {f['melhor']} é melhor:\n")
    lab.table(resumo, ["usuarios", "condicao", "repeticoes", "media", "desvio", "minimo", "maximo", "variacao_pct"],
              {"media": "{:.3f}", "desvio": "{:.3f}", "minimo": "{:.3f}", "maximo": "{:.3f}", "variacao_pct": "{:+.1f}%"})

    os.makedirs(lab.RESULTS, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(lab.RESULTS, f"medicao-{a.frente}-{stamp}")
    with open(base + ".csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(rows)
    with open(base + "-resumo.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(resumo[0].keys()) if resumo else ["usuarios"])
        w.writeheader()
        w.writerows(resumo)
    lab.save_json(base + ".json", {"frente": a.frente, "config": f, "usuarios": users, "repeticoes": reps,
                                   "duracao_s": duration, "maquina": machine_info(), "inicio": t_start,
                                   "fim": time.time(), "linhas": rows, "resumo": resumo})
    shown = os.path.join("results", os.path.basename(base))
    print(f"\nsalvo em {shown}.csv (+ -resumo.csv e .json)")
    print("Envie esses arquivos para o grupo. ./lab report junta as três frentes num gráfico.")
