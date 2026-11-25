"""Relatório HTML autocontido (sem dependências) com as medições do laboratório.

Inclui: ranking de hubs, matriz de RTT usuário x servidor, sobrecarga do túnel,
precisão da emulação, ganhos por relay e os últimos testes de rampa, carga e abuso
encontrados na pasta de resultados. Abre em qualquer navegador e pode ser impresso em PDF.
"""
import glob
import html
import json
import math
import os
import time

import optimizer as opt

S1, S2, S3 = "var(--s1)", "var(--s2)", "var(--s3)"
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
       "#1c5cab", "#184f95", "#104281", "#0d366b"]

CSS = """
:root{--bg:#fcfcfb;--card:#ffffff;--t1:#0b0b0b;--t2:#52514e;--t3:#8a8984;--grid:#e6e5e0;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--good:#008300;--bad:#e34948}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#1a1a19;--card:#232322;--t1:#f0efec;--t2:#c3c2b7;--t3:#8f8e86;--grid:#3a3a37;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--good:#2f9e2f;--bad:#e66767}}
:root[data-theme="dark"]{--bg:#1a1a19;--card:#232322;--t1:#f0efec;--t2:#c3c2b7;--t3:#8f8e86;--grid:#3a3a37;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--good:#2f9e2f;--bad:#e66767}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t1);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 4px}
.sub{color:var(--t2);margin:0 0 16px}.card{background:var(--card);border:1px solid var(--grid);border-radius:10px;padding:16px;margin:12px 0;overflow-x:auto}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:12px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.kpi .v{font-size:26px;font-weight:600}.kpi .l{color:var(--t2);font-size:13px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:5px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--t2);font-weight:600}.win{font-weight:700}
svg text{fill:var(--t2);font-size:11px}svg .ax{stroke:var(--grid)}.legend{display:flex;gap:16px;color:var(--t2);font-size:13px;margin:4px 0}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}.note{color:var(--t3);font-size:13px}
@media print{.card{break-inside:avoid}}
"""


def esc(x):
    return html.escape(str(x))


def f(v, fmt="{:.1f}", dash="–"):
    return dash if v is None or (isinstance(v, float) and not math.isfinite(v)) else fmt.format(v)


def heat_color(v, lo, hi):
    if v is None:
        return "transparent"
    k = 0 if hi <= lo else (v - lo) / (hi - lo)
    return SEQ[min(len(SEQ) - 1, int(k * (len(SEQ) - 1)))]


def text_on(v, lo, hi):
    k = 0 if hi <= lo or v is None else (v - lo) / (hi - lo)
    return "#ffffff" if k > 0.55 else "#0b0b0b"


def tick(v, vmax):
    return f"{v:.1f}" if vmax < 10 else f"{v:.0f}"


def hbars(rows, series, width=760, unit="ms"):
    """Barras horizontais agrupadas. rows=[(rótulo, [v1, v2...])]; series=[(nome, cor)]."""
    bh, gap, left, right = 12, 2, 90, 60
    group = len(series) * (bh + gap) + 10
    h = group * len(rows) + 24
    vmax = max([v for _, vs in rows for v in vs if v is not None] + [1])
    sc = (width - left - right) / vmax
    out = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img">']
    for t in range(0, 5):
        x = left + (width - left - right) * t / 4
        out.append(f'<line class="ax" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h - 18}"/>'
                   f'<text x="{x:.1f}" y="{h - 4}" text-anchor="middle">{tick(vmax * t / 4, vmax)}</text>')
    for i, (label, vals) in enumerate(rows):
        y0 = i * group + 4
        out.append(f'<text x="{left - 8}" y="{y0 + group / 2}" text-anchor="end" dominant-baseline="middle" '
                   f'style="fill:var(--t1)">{esc(label)}</text>')
        for j, ((name, color), v) in enumerate(zip(series, vals)):
            if v is None:
                continue
            y = y0 + j * (bh + gap)
            w = max(2, v * sc)
            out.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bh}" rx="3" fill="{color}">'
                       f'<title>{esc(label)} — {esc(name)}: {v:.1f} {unit}</title></rect>')
            out.append(f'<text x="{left + w + 4:.1f}" y="{y + bh / 2}" dominant-baseline="middle">{tick(v, vmax)}</text>')
    out.append("</svg>")
    legend = "".join(f'<span><span class="sw" style="background:{c}"></span>{esc(n)}</span>' for n, c in series)
    return (f'<div class="legend">{legend}</div>' if len(series) > 1 else "") + "".join(out)


def line(xs, ys, xlabel, ylabel, width=520, height=220, color=S1):
    left, bottom, top, right = 48, 34, 10, 14
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if not pts:
        return '<p class="note">sem dados</p>'
    xmax = max(x for x, _ in pts) or 1
    ymax = max(y for _, y in pts) * 1.1 or 1
    X = lambda x: left + (width - left - right) * x / xmax  # noqa: E731
    Y = lambda y: top + (height - top - bottom) * (1 - y / ymax)  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in range(5):
        y = ymax * t / 4
        out.append(f'<line class="ax" x1="{left}" y1="{Y(y):.1f}" x2="{width - right}" y2="{Y(y):.1f}"/>'
                   f'<text x="{left - 6}" y="{Y(y):.1f}" text-anchor="end" dominant-baseline="middle">{tick(y, ymax)}</text>')
    for x, _ in pts:
        out.append(f'<text x="{X(x):.1f}" y="{height - bottom + 14}" text-anchor="middle">{x:g}</text>')
    out.append(f'<text x="{(left + width) / 2}" y="{height - 4}" text-anchor="middle">{esc(xlabel)}</text>')
    d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(pts))
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
    for x, y in pts:
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4" fill="{color}" stroke="var(--card)" stroke-width="2">'
                   f'<title>{x:g} usuários: {y:.2f} {esc(ylabel)}</title></circle>')
    out.append("</svg>")
    return f'<div class="note">{esc(ylabel)}</div>' + "".join(out)


def latest(results, kind):
    files = sorted(glob.glob(os.path.join(results, f"*-{kind}.json")))
    if not files:
        return None, None
    with open(files[-1]) as fh:
        return json.load(fh), os.path.basename(files[-1])


FRENTE_INFO = [("latencia", "Latência (RTT)", "ms", S1), ("vazao", "Vazão por usuário", "Mbit/s", S2),
               ("perda", "Perda de pacotes", "%", S3)]


def latest_medicao(results, frente):
    files = sorted(glob.glob(os.path.join(results, f"medicao-{frente}-*-resumo.csv")))
    if not files:
        return None, None
    import csv
    with open(files[-1]) as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k in ("usuarios", "repeticoes", "base_usuarios"):
            r[k] = int(float(r[k])) if r.get(k) not in (None, "") else None
        for k in ("media", "desvio", "minimo", "maximo", "variacao_pct"):
            r[k] = float(r[k]) if r.get(k) not in (None, "") else None
    return rows, os.path.basename(files[-1])


def multiline(series, xlabel, ylabel, width=760, height=320):
    """Várias séries no mesmo eixo. series=[(nome, cor, [(x, y)])]. Aceita y negativo (linha de zero destacada)."""
    left, bottom, top, right = 60, 40, 14, 200
    pts = [p for _, _, ps in series for p in ps if p[1] is not None]
    if not pts:
        return '<p class="note">sem dados</p>'
    xs = sorted({x for x, _ in pts})
    lo, hi = min(0, min(y for _, y in pts)), max(0, max(y for _, y in pts))
    raw = (hi - lo) / 5 or 1
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    ymin, ymax = math.floor(lo / step) * step, math.ceil(hi / step) * step
    if ymax == ymin:
        ymax = ymin + step
    xmax = max(xs) or 1
    X = lambda x: left + (width - left - right) * x / xmax  # noqa: E731
    Y = lambda y: top + (height - top - bottom) * (ymax - y) / (ymax - ymin)  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    y = ymin
    while y <= ymax + step / 2:
        out.append(f'<line class="ax" x1="{left}" y1="{Y(y):.1f}" x2="{width - right}" y2="{Y(y):.1f}"/>'
                   f'<text x="{left - 6}" y="{Y(y):.1f}" text-anchor="end" dominant-baseline="middle">{y:+g}%</text>')
        y += step
    out.append(f'<line x1="{left}" y1="{Y(0):.1f}" x2="{width - right}" y2="{Y(0):.1f}" style="stroke:var(--t3)" stroke-width="1.5"/>')
    for x in xs:
        out.append(f'<text x="{X(x):.1f}" y="{height - bottom + 16}" text-anchor="middle">{x:g}</text>')
    out.append(f'<text x="{(left + width - right) / 2}" y="{height - 4}" text-anchor="middle">{esc(xlabel)}</text>')
    labels = []
    for name, color, ps in series:
        ps = [(x, y) for x, y in ps if y is not None]
        if not ps:
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(ps))
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y in ps:
            out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4.5" fill="{color}" stroke="var(--card)" stroke-width="2">'
                       f'<title>{esc(name)} · {x:g} usuários: {y:+.1f}%</title></circle>')
        labels.append([Y(ps[-1][1]), X(ps[-1][0]), name, ps[-1][1]])
    labels.sort()
    for i in range(1, len(labels)):  # afasta rótulos que colidiriam
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 15)
    for y, x, name, v in labels:
        out.append(f'<text x="{x + 9:.1f}" y="{y:.1f}" dominant-baseline="middle" style="fill:var(--t1)">{esc(name)} {v:+.0f}%</text>')
    out.append("</svg>")
    legend = "".join(f'<span><span class="sw" style="background:{c}"></span>{esc(n)}</span>' for n, c, _ in series)
    return f'<div class="legend">{legend}</div><div class="note">{esc(ylabel)}</div>' + "".join(out)


def errline(rows, unit, color, width=520, height=230):
    """Média por condição com barras de ±1 desvio-padrão."""
    left, bottom, top, right = 52, 34, 10, 14
    pts = [(r["usuarios"], r["media"], r["desvio"] or 0) for r in rows if r["media"] is not None]
    if not pts:
        return '<p class="note">sem dados</p>'
    xmax = max(x for x, _, _ in pts) or 1
    ymax = max(m + d for _, m, d in pts) * 1.12 or 1
    X = lambda x: left + (width - left - right) * x / xmax  # noqa: E731
    Y = lambda y: top + (height - top - bottom) * (1 - y / ymax)  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in range(5):
        y = ymax * t / 4
        out.append(f'<line class="ax" x1="{left}" y1="{Y(y):.1f}" x2="{width - right}" y2="{Y(y):.1f}"/>'
                   f'<text x="{left - 6}" y="{Y(y):.1f}" text-anchor="end" dominant-baseline="middle">{tick(y, ymax)}</text>')
    for x, _, _ in pts:
        out.append(f'<text x="{X(x):.1f}" y="{height - bottom + 14}" text-anchor="middle">{x:g}</text>')
    out.append(f'<text x="{(left + width) / 2}" y="{height - 4}" text-anchor="middle">usuários simultâneos</text>')
    d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(m):.1f}" for i, (x, m, _) in enumerate(pts))
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
    for x, m, dv in pts:
        if dv:
            out.append(f'<line x1="{X(x):.1f}" y1="{Y(m - dv):.1f}" x2="{X(x):.1f}" y2="{Y(m + dv):.1f}" style="stroke:var(--t2)" stroke-width="1.5"/>')
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(m):.1f}" r="4.5" fill="{color}" stroke="var(--card)" stroke-width="2">'
                   f'<title>{x:g} usuários: {m:.3f} ± {dv:.3f} {esc(unit)}</title></circle>')
    out.append("</svg>")
    return f'<div class="note">{esc(unit)} (média ± desvio-padrão)</div>' + "".join(out)


def grupo_section(results):
    found = [(k, n, u, c, *latest_medicao(results, k)) for k, n, u, c in FRENTE_INFO]
    found = [f for f in found if f[4]]
    if not found:
        return []
    H = ["<h2>Medições do grupo: custo de mais usuários no mesmo túnel</h2>",
         "<p class='sub'>Eixo X: usuários simultâneos no mesmo servidor VPN. Eixo Y: variação em relação à menor condição "
         "(ociosa; para a vazão, a menor condição com carga). Uma série por frente.</p><div class='card'>"]
    H.append(multiline([(n, c, [(r["usuarios"], r["variacao_pct"]) for r in rows]) for _, n, _, c, rows, _ in found],
                       "usuários simultâneos", "variação em relação à condição base (%)"))
    sem_base = [n for _, n, _, _, rows, _ in found if all(r["variacao_pct"] is None for r in rows[1:])]
    if sem_base:
        H.append(f"<p class='note'>Sem variação % para: {esc(', '.join(sem_base))} (o valor na condição base foi 0). "
                 "Use a variação absoluta na tabela abaixo.</p>")
    H.append("</div><div class='grid2'>")
    for k, n, u, c, rows, fname in found:
        H.append(f"<div class='card'><b>{esc(n)}</b> <span class='note'>· {esc(fname)}</span>{errline(rows, u, c)}"
                 "<table><tr><th>usuários</th><th>média</th><th>desvio</th><th>mín</th><th>máx</th><th>variação</th><th>reps</th></tr>")
        for r in rows:
            H.append(f"<tr><td>{r['usuarios']}</td><td>{f(r['media'], '{:.3f}')}</td><td>{f(r['desvio'], '{:.3f}')}</td>"
                     f"<td>{f(r['minimo'], '{:.3f}')}</td><td>{f(r['maximo'], '{:.3f}')}</td>"
                     f"<td>{f(r['variacao_pct'], '{:+.1f}%')}</td><td>{r['repeticoes']}</td></tr>")
        H.append("</table></div>")
    H.append("</div>")
    return H


def build(topo, window, results):
    servers = [s["name"] for s in topo["servers"]]
    clients = [c["name"] for c in topo["clients"]]
    meas = opt.fetch_measurements(window)
    res = opt.analyze(meas, servers, clients)
    rank = res["ranking"]
    best = {o: (lst[0] if lst else "–") for o, lst in rank.items()}
    H = [f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>Relatório VPN Lab</title><style>{CSS}</style></head><body><main>",
         "<h1>Relatório do VPN Lab</h1>",
         f"<p class='sub'>Gerado em {time.strftime('%d/%m/%Y %H:%M')} · {len(servers)} servidores · {len(clients)} usuários · "
         f"médias dos últimos {esc(window)}</p>"]

    H += grupo_section(results)

    # ---- KPIs ----
    tun = [v["rtt"] for (s, d, p, _), v in meas.items() if p == "tunnel" and "rtt" in v]
    loss = [v.get("loss", 0) for (s, d, p, _), v in meas.items() if p == "tunnel"]
    gains = [r for r in res["routes"] if r["gain_ms"] >= 5]
    H.append("<div class='kpis'>")
    for v, l in ((best["media"], "melhor hub (RTT médio)"), (best["pior_caso"], "melhor hub (pior caso)"),
                 (best["mos"], "melhor hub (qualidade de voz)"),
                 (f(res["tunnel_overhead_ms"], "{:+.2f} ms"), "sobrecarga média do túnel"),
                 (f(sum(loss) / len(loss) * 100 if loss else None, "{:.2f}%"), "perda média nos túneis"),
                 (str(len(gains)), "rotas que melhoram com relay (≥5 ms)")):
        H.append(f"<div class='card kpi'><div class='v'>{esc(v)}</div><div class='l'>{esc(l)}</div></div>")
    H.append("</div>")

    # ---- 1. hubs ----
    H.append("<h2>1. Qual servidor intermediário é melhor para todos?</h2><p class='sub'>RTT de cada usuário até o servidor, "
             "pelo túnel. Menor é melhor; o pior caso mostra quem fica mais prejudicado.</p>")
    hubs = sorted(res["hubs"], key=lambda h: h["rtt_mean"])
    H.append("<div class='card'>" + hbars([(h["server"], [h["rtt_mean"], h["rtt_max"]]) for h in hubs],
                                          [("RTT médio (ms)", S1), ("pior usuário (ms)", S2)]) + "</div>")
    H.append("<div class='card'><table><tr><th>servidor</th><th>cobertura</th><th>RTT médio</th><th>p95</th><th>máx</th>"
             "<th>perda</th><th>MOS médio</th><th>MOS mín</th><th>usuário↔usuário médio</th><th>usuário↔usuário máx</th></tr>")
    for h in hubs:
        cls = " class='win'" if h["server"] == best["media"] else ""
        H.append(f"<tr{cls}><td>{esc(h['server'])}</td><td>{h['coverage'] * 100:.0f}%</td><td>{h['rtt_mean']:.1f}</td>"
                 f"<td>{h['rtt_p95']:.1f}</td><td>{h['rtt_max']:.1f}</td><td>{h['loss_mean'] * 100:.2f}%</td>"
                 f"<td>{h['mos_mean']:.2f}</td><td>{h['mos_min']:.2f}</td><td>{h['pair_rtt_mean']:.1f}</td><td>{h['pair_rtt_max']:.1f}</td></tr>")
    H.append("</table><p class='note'>MOS: nota de 1 a 4,5 de qualidade de chamada de voz estimada a partir de RTT, jitter e perda "
             "(modelo E simplificado, ITU-T G.107). Acima de 4,0 é bom; abaixo de 3,6 os usuários percebem.</p></div>")

    # ---- 2. matriz ----
    H.append("<h2>2. Matriz de RTT usuário × servidor (pelo túnel, ms)</h2><p class='sub'>Cor mais escura = mais distante.</p>")
    vals = [meas.get((c, s, "tunnel", ""), {}).get("rtt") for c in clients for s in servers]
    vals = [v for v in vals if v is not None]
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    H.append("<div class='card'><table><tr><th>usuário</th>" + "".join(f"<th>{esc(s)}</th>" for s in servers) + "</tr>")
    for c in clients:
        H.append(f"<tr><td>{esc(c)}</td>")
        for s in servers:
            m = meas.get((c, s, "tunnel", ""), {})
            v = m.get("rtt")
            tip = f"perda {m.get('loss', 0) * 100:.2f}% · jitter {m.get('jitter', 0):.1f} ms" if m else "sem medição"
            H.append(f"<td title='{esc(tip)}' style='background:{heat_color(v, lo, hi)};color:{text_on(v, lo, hi)}'>{f(v)}</td>")
        H.append("</tr>")
    H.append("</table></div>")

    # ---- 3. emulação x medido ----
    errs = []
    for c in clients:
        for s in servers:
            v = meas.get((c, s, "underlay", ""), {}).get("rtt")
            e = topo["expected"].get(f"{c}|{s}", {}).get("rtt_ms")
            if v is not None and e:
                errs.append(abs(v - e) / e)
    if errs:
        H.append(f"<h2>3. Fidelidade da emulação</h2><div class='card'>O RTT medido sem VPN ficou em média "
                 f"<b>{sum(errs) / len(errs) * 100:.1f}%</b> distante do configurado em topology.toml "
                 f"({len(errs)} caminhos). Valores próximos de 0% indicam que a 'internet' simulada se comporta como planejado.</div>")

    # ---- 4. relays ----
    H.append("<h2>4. Onde um servidor de relay encurta o caminho</h2><p class='sub'>Usuário → servidor A → malha → servidor de saída B, "
             "comparado a conectar direto em B.</p><div class='card'>")
    gains = sorted(gains, key=lambda r: -r["gain_ms"])[:20]
    if gains:
        H.append("<table><tr><th>usuário</th><th>saída</th><th>direto (ms)</th><th>via</th><th>com relay (ms)</th><th>ganho (ms)</th><th>medido ponta a ponta</th></tr>")
        for r in gains:
            H.append(f"<tr><td>{esc(r['client'])}</td><td>{esc(r['exit'])}</td><td>{r['direct_rtt']:.1f}</td><td>{esc(r['best_via'])}</td>"
                     f"<td>{r['best_rtt']:.1f}</td><td>{r['gain_ms']:.1f}</td><td>{f(r.get('best_measured'))}</td></tr>")
        H.append("</table>")
    else:
        H.append("<p>Nenhum relay melhora as rotas em pelo menos 5 ms.</p>")
    H.append("</div>")

    # ---- 5. rampa ----
    ramp, fname = latest(results, "ramp")
    if ramp:
        st = ramp["steps"]
        xs = [s["step_users"] for s in st]
        H.append(f"<h2>5. Capacidade do túnel: rampa de usuários em {esc(ramp['server'])}</h2>"
                 f"<p class='sub'>Arquivo {esc(fname)} · {esc(ramp['args'].get('proto'))}/{esc(ramp['args'].get('direction'))} · "
                 f"saturação por volta de <b>{esc(ramp.get('knee_users') or 'não atingida')}</b> usuários · RTT ocioso "
                 f"{f(ramp.get('baseline_rtt_ms'))} ms</p>")
        H.append("<div class='grid2'>")
        for title, key, unit in (("Vazão total do servidor", "total_mbps", "Mbit/s"), ("Vazão por usuário", "per_user_mbps", "Mbit/s"),
                                 ("RTT pelo túnel sob carga", "rtt_under_load_ms", "ms"), ("Justiça entre usuários (Jain)", "fairness", "0–1")):
            H.append(f"<div class='card'><b>{esc(title)}</b>{line(xs, [s.get(key) for s in st], 'usuários simultâneos', unit)}</div>")
        H.append("</div>")

    # ---- 6. abuso ----
    ab, fname = latest(results, "abuse")
    if ab:
        p1, p2 = ab["phase1"], ab["phase2"]
        H.append(f"<h2>6. Impacto de um usuário abusivo em {esc(ab['server'])}</h2><p class='sub'>{esc(fname)} · abusivo: "
                 f"{esc(ab['args']['heavy'])} com {esc(ab['args']['heavy_parallel'])} conexões</p><div class='card'>")
        H.append(hbars([("vazão média", [p1["per_user_mbps"], p2["per_user_mbps"]]),
                        ("pior usuário", [p1["min_user_mbps"], p2["min_user_mbps"]])],
                       [("sem abusivo", S1), ("com abusivo", S2)], unit="Mbit/s"))
        H.append(f"<p>RTT sob carga: {f(p1['rtt_under_load_ms'])} ms → {f(p2['rtt_under_load_ms'])} ms. "
                 f"Abusivo puxou {f(ab['heavy'][0]['mbps'] if ab['heavy'] else None)} Mbit/s.</p></div>")

    # ---- 7. cargas ----
    loads = sorted(glob.glob(os.path.join(results, "*-load.json")))[-10:]
    if loads:
        H.append("<h2>7. Testes de carga recentes</h2><div class='card'><table><tr><th>arquivo</th><th>servidor</th><th>usuários</th>"
                 "<th>total (Mbit/s)</th><th>por usuário</th><th>Jain</th><th>RTT ocioso</th><th>RTT sob carga</th><th>retrans.</th></tr>")
        for p in reversed(loads):
            with open(p) as fh:
                d = json.load(fh)
            s = d["summary"]
            H.append(f"<tr><td>{esc(os.path.basename(p))}</td><td>{esc(d['server'])}</td><td>{s['users']}</td><td>{s['total_mbps']:.1f}</td>"
                     f"<td>{s['per_user_mbps']:.2f}</td><td>{f(s['fairness'], '{:.3f}')}</td><td>{f(s.get('rtt_idle_ms'))}</td>"
                     f"<td>{f(s['rtt_under_load_ms'])}</td><td>{s['retransmits']}</td></tr>")
        H.append("</table></div>")

    H.append("<p class='note'>Dados brutos: pasta results/ (JSON e CSV) e Prometheus. Gráficos ao vivo: Grafana.</p></main></body></html>")
    os.makedirs(results, exist_ok=True)
    path = os.path.join(results, f"{time.strftime('%Y%m%d-%H%M%S')}-relatorio.html")
    with open(path, "w") as fh:
        fh.write("\n".join(H))
    with open(os.path.join(results, "relatorio-mais-recente.html"), "w") as fh:
        fh.write("\n".join(H))
    return path
