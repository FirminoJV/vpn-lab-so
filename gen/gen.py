#!/usr/bin/env python3
"""Gerador do VPN Lab.

Lê topology.toml e produz em build/:
  compose.yml            -> todos os contêineres (servidores, clientes, Prometheus, Grafana, otimizador)
  nodes/<nó>.json        -> configuração de cada nó (WireGuard, emulação de rede, sondas)
  prometheus.yml         -> alvos de coleta
  topology.json          -> visão da topologia usada pelo controlador/otimizador
  keys.json              -> chaves WireGuard (reaproveitadas entre execuções)
"""
import json
import math
import os
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from x25519 import genkey, pubkey  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.environ.get("VPNLAB_BUILD", os.path.join(ROOT, "build"))

WG_SERVER_PORT = 51820
WG_MESH_PORT = 51821
ECHO_PORT = 9000
AGENT_PORT = 9100
OPT_PORT = 9200
IPERF_BASE_PORT = 5201

NODE_IMAGE = "vpnlab-node:latest"
CTL_IMAGE = "vpnlab-ctl:latest"
PROM_IMAGE = "prom/prometheus:v2.53.0"
GRAFANA_IMAGE = "grafana/grafana:11.2.0"


def die(msg):
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def pair_lookup(table, a, b, default=None):
    if a == b:
        return default
    for k in (f"{a}-{b}", f"{b}-{a}"):
        if k in table:
            return table[k]
    return default


def combine_loss(*pcts):
    keep = 1.0
    for p in pcts:
        keep *= 1 - p / 100.0
    return 100.0 * (1 - keep)


def one_way_loss(rt_loss_pct):
    """Divide a perda de ida-e-volta entre os dois sentidos."""
    return 100.0 * (1 - math.sqrt(1 - rt_loss_pct / 100.0))


def load_topology(path):
    with open(path, "rb") as f:
        topo = tomllib.load(f)
    lab = topo.get("lab", {})
    regions = topo.get("region", [])
    if not regions:
        die("defina ao menos uma [[region]]")
    codes = [r["code"] for r in regions]
    if len(set(codes)) != len(codes):
        die("códigos de região repetidos")
    for c in codes:
        if not c.isalnum() or len(c) > 8:
            die(f"código de região inválido: {c!r} (alfanumérico, até 8 caracteres)")

    rtt = topo.get("rtt_ms", {})
    for a in codes:
        for b in codes:
            if a < b and pair_lookup(rtt, a, b) is None:
                die(f"falta rtt_ms para o par {a}-{b}")

    servers, clients = [], []
    seen = set()
    for s in topo.get("server", []):
        if s["region"] not in codes:
            die(f"servidor em região desconhecida: {s['region']}")
        name = s.get("name", f"srv-{s['region']}")
        if name in seen:
            die(f"nome repetido: {name} (use `name` para dois servidores na mesma região)")
        seen.add(name)
        servers.append({
            "name": name, "region": s["region"], "role": "server",
            "uplink_mbit": s.get("uplink_mbit", 200),
            "access_rtt_ms": s.get("access_rtt_ms", 1),
            "access_jitter_ms": s.get("access_jitter_ms", 0),
            "access_loss_pct": s.get("access_loss_pct", 0.0),
        })
    counters = {}
    for c in topo.get("client", []):
        if c["region"] not in codes:
            die(f"cliente em região desconhecida: {c['region']}")
        for _ in range(int(c.get("count", 1))):
            counters[c["region"]] = counters.get(c["region"], 0) + 1
            name = f"cli-{c['region']}-{counters[c['region']]}"
            clients.append({
                "name": name, "region": c["region"], "role": "client",
                "profile": c.get("profile", "fibra"),
                "uplink_mbit": c.get("uplink_mbit", 50),
                "downlink_mbit": c.get("downlink_mbit", 4 * c.get("uplink_mbit", 50)),
                "access_rtt_ms": c.get("access_rtt_ms", 5),
                "access_jitter_ms": c.get("access_jitter_ms", 1),
                "access_loss_pct": c.get("access_loss_pct", 0.0),
            })
    if not servers:
        die("defina ao menos um [[server]]")
    if len(servers) > 50:
        die("máximo de 50 servidores")
    if len(clients) > 200:
        die("máximo de 200 clientes")
    return lab, regions, rtt, topo.get("loss_pct", {}), topo.get("jitter_ms", {}), servers, clients


def main():
    topo_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "topology.toml")
    lab, regions, rtt, loss_tbl, jit_tbl, servers, clients = load_topology(topo_path)
    intra = lab.get("intra_region_rtt_ms", 2)

    # ---------------- endereçamento ----------------
    for i, s in enumerate(servers):
        s["idx"] = i
        s["underlay_ip"] = f"172.30.1.{i + 10}"
        s["mgmt_ip"] = f"172.31.1.{i + 10}"
        s["tunnel_ip"] = f"10.{100 + i}.0.1"
        s["mesh_ip"] = f"10.99.0.{i + 1}"
        s["client_subnet"] = f"10.{100 + i}.0.0/16"
    for j, c in enumerate(clients):
        c["idx"] = j
        c["underlay_ip"] = f"172.30.2.{j + 10}"
        c["mgmt_ip"] = f"172.31.2.{j + 10}"
        c["tunnel_ips"] = {s["name"]: f"10.{100 + s['idx']}.0.{j + 2}" for s in servers}
    nodes = servers + clients

    # ---------------- chaves (persistentes) ----------------
    os.makedirs(os.path.join(BUILD, "nodes"), exist_ok=True)
    keys_path = os.path.join(BUILD, "keys.json")
    keys = {}
    if os.path.exists(keys_path):
        with open(keys_path) as f:
            keys = json.load(f)
    for n in nodes:
        if n["name"] not in keys:
            keys[n["name"]] = genkey()
        n["private_key"] = keys[n["name"]]
        n["public_key"] = pubkey(n["private_key"])
    with open(keys_path, "w") as f:
        json.dump(keys, f, indent=1)
    os.chmod(keys_path, 0o600)

    # ---------------- emulação de rede (por destino) ----------------
    def region_rtt(a, b):
        return intra if a == b else pair_lookup(rtt, a, b)

    def link_params(a, b):
        base = region_rtt(a["region"], b["region"])
        total_rtt = base + a["access_rtt_ms"] + b["access_rtt_ms"]
        rt_loss = combine_loss(pair_lookup(loss_tbl, a["region"], b["region"], 0.0),
                               a["access_loss_pct"], b["access_loss_pct"])
        jitter = pair_lookup(jit_tbl, a["region"], b["region"], round(base * 0.02, 2))
        jitter = (jitter + a["access_jitter_ms"] + b["access_jitter_ms"]) / 2
        return {
            "delay_ms": round(total_rtt / 2, 2),
            "jitter_ms": round(jitter, 2),
            "loss_pct": round(one_way_loss(rt_loss), 4),
            "expected_rtt_ms": round(total_rtt, 2),
            "expected_loss_pct": round(rt_loss, 4),
        }

    expected = {}
    probe_defaults = {
        "interval_s": lab.get("probe_interval_s", 10),
        "count": lab.get("probe_count", 10),
        "spacing_ms": lab.get("probe_spacing_ms", 100),
        "size": lab.get("probe_size_bytes", 64),
        "timeout_ms": 2000,
        "echo_port": ECHO_PORT,
    }

    for n in nodes:
        links = []
        for m in nodes:
            if m is n:
                continue
            lp = link_params(n, m)
            links.append({"dst": m["name"], "dst_ip": m["underlay_ip"], **{k: lp[k] for k in ("delay_ms", "jitter_ms", "loss_pct")}})
            expected[f"{n['name']}|{m['name']}"] = {"rtt_ms": lp["expected_rtt_ms"], "loss_pct": lp["expected_loss_pct"]}

        cfg = {
            "name": n["name"], "role": n["role"], "region": n["region"],
            "underlay_ip": n["underlay_ip"], "mgmt_ip": n["mgmt_ip"],
            "uplink_mbit": n["uplink_mbit"], "downlink_mbit": n.get("downlink_mbit", 0), "links": links,
            "private_key": n["private_key"], "wg": [], "probes": [],
            "probe": probe_defaults, "agent_port": AGENT_PORT, "queue_ms": lab.get("queue_ms", 100),
        }

        if n["role"] == "server":
            cfg["wg"].append({
                "iface": "wg0", "listen_port": WG_SERVER_PORT,
                "address": f"{n['tunnel_ip']}/16", "routes": [], "table": None, "source": None,
                "peers": [{"name": c["name"], "public_key": c["public_key"],
                           "allowed_ips": [f"{c['tunnel_ips'][n['name']]}/32"]} for c in clients],
            })
            others = [s for s in servers if s is not n]
            if others:
                cfg["wg"].append({
                    "iface": "wg-mesh", "listen_port": WG_MESH_PORT,
                    "address": f"{n['mesh_ip']}/24", "table": None, "source": None,
                    "routes": [s["client_subnet"] for s in others],
                    "peers": [{"name": s["name"], "public_key": s["public_key"],
                               "endpoint": f"{s['underlay_ip']}:{WG_MESH_PORT}", "keepalive": 25,
                               "allowed_ips": [f"{s['mesh_ip']}/32", s["client_subnet"]]} for s in others],
                })
            cfg["iperf"] = {"bind": n["tunnel_ip"], "ports": [IPERF_BASE_PORT + c["idx"] for c in clients]}
            for s in others:
                cfg["probes"].append({"dst": s["name"], "dst_region": s["region"], "path": "underlay", "via": "",
                                      "src_ip": n["underlay_ip"], "dst_ip": s["underlay_ip"]})
                cfg["probes"].append({"dst": s["name"], "dst_region": s["region"], "path": "mesh", "via": "",
                                      "src_ip": n["mesh_ip"], "dst_ip": s["mesh_ip"]})
        else:
            for s in servers:
                table = 100 + s["idx"]
                src = n["tunnel_ips"][s["name"]]
                cfg["wg"].append({
                    "iface": f"wg-{s['name'].removeprefix('srv-')}"[:15], "listen_port": None,
                    "address": f"{src}/16", "table": table, "source": src, "routes": [],
                    "server": s["name"],
                    "peers": [{"name": s["name"], "public_key": s["public_key"],
                               "endpoint": f"{s['underlay_ip']}:{WG_SERVER_PORT}", "keepalive": 15,
                               "allowed_ips": [s["client_subnet"], "10.99.0.0/24"]}],
                })
                cfg["probes"].append({"dst": s["name"], "dst_region": s["region"], "path": "underlay", "via": "",
                                      "src_ip": n["underlay_ip"], "dst_ip": s["underlay_ip"]})
                cfg["probes"].append({"dst": s["name"], "dst_region": s["region"], "path": "tunnel", "via": "",
                                      "src_ip": src, "dst_ip": s["tunnel_ip"],
                                      "count": lab.get("tunnel_probe_count", 50),
                                      "spacing_ms": lab.get("tunnel_probe_spacing_ms", 20),
                                      "iperf_port": IPERF_BASE_PORT + n["idx"]})
            if lab.get("relay_probes", True):
                for a in servers:
                    for b in servers:
                        if a is b:
                            continue
                        cfg["probes"].append({"dst": b["name"], "dst_region": b["region"], "path": "relay",
                                              "via": a["name"], "src_ip": n["tunnel_ips"][a["name"]],
                                              "dst_ip": b["mesh_ip"], "interval_mult": 3})
        with open(os.path.join(BUILD, "nodes", f"{n['name']}.json"), "w") as f:
            json.dump(cfg, f, indent=1)
        os.chmod(os.path.join(BUILD, "nodes", f"{n['name']}.json"), 0o644)

    # ---------------- topology.json (controlador / otimizador) ----------------
    public = lambda n: {k: v for k, v in n.items() if k not in ("private_key",)}  # noqa: E731
    topo_json = {
        "regions": regions,
        "servers": [public(s) for s in servers],
        "clients": [public(c) for c in clients],
        "expected": expected,
        "agent_port": AGENT_PORT,
        "grafana_url": "http://grafana:3000",
        "prometheus_url": "http://prometheus:9090",
    }
    with open(os.path.join(BUILD, "topology.json"), "w") as f:
        json.dump(topo_json, f, indent=1, ensure_ascii=False)

    # ---------------- prometheus.yml ----------------
    prom = ["global:", "  scrape_interval: 5s", "  evaluation_interval: 5s", "scrape_configs:",
            "  - job_name: vpnlab-nodes", "    static_configs:"]
    for n in nodes:
        prom += [f"      - targets: ['{n['mgmt_ip']}:{AGENT_PORT}']",
                 f"        labels: {{node: '{n['name']}', role: '{n['role']}', region: '{n['region']}'}}"]
    prom += ["  - job_name: vpnlab-optimizer", "    scrape_interval: 15s", "    static_configs:",
             f"      - targets: ['optimizer:{OPT_PORT}']"]
    with open(os.path.join(BUILD, "prometheus.yml"), "w") as f:
        f.write("\n".join(prom) + "\n")

    # ---------------- compose.yml ----------------
    bind = lab.get("bind_address", "127.0.0.1")
    y = [
        "# Gerado por gen/gen.py — não edite à mão; edite topology.toml e rode ./lab gen",
        "name: vpnlab",
        "networks:",
        "  underlay:   # a 'internet' simulada — isolada do mundo real",
        "    driver: bridge",
        "    internal: true",
        "    ipam: {config: [{subnet: 172.30.0.0/16}]}",
        "  mgmt:       # rede de observabilidade, fora da emulação de latência",
        "    driver: bridge",
        "    internal: true",
        "    ipam: {config: [{subnet: 172.31.0.0/16}]}",
        "  front:      # só para publicar Grafana/Prometheus no seu computador",
        "    driver: bridge",
        "volumes:",
        "  prom-data: {}",
        "  grafana-data: {}",
        "services:",
    ]
    for n in nodes:
        y += [
            f"  {n['name']}:",
            f"    image: {NODE_IMAGE}",
            "    pull_policy: never",
            f"    container_name: vpnlab-{n['name']}",
            f"    hostname: {n['name']}",
            "    cap_add: [NET_ADMIN]",
            "    devices: [/dev/net/tun]",
            "    sysctls:",
            f"      net.ipv4.ip_forward: {1 if n['role'] == 'server' else 0}",
            "      net.ipv4.conf.all.rp_filter: 0",
            "      net.ipv4.conf.default.rp_filter: 0",
            "    volumes:",
            f"      - ./nodes/{n['name']}.json:/etc/vpnlab/node.json:ro",
            "    networks:",
            f"      underlay: {{ipv4_address: {n['underlay_ip']}}}",
            f"      mgmt: {{ipv4_address: {n['mgmt_ip']}}}",
            f"    mem_limit: {'256m' if n['role'] == 'server' else '160m'}",
            f"    cpus: {lab.get('cpus_server', 1.0) if n['role'] == 'server' else lab.get('cpus_client', 0.5)}",
            "    restart: unless-stopped",
        ]
    y += [
        "  prometheus:",
        f"    image: {PROM_IMAGE}",
        "    container_name: vpnlab-prometheus",
        "    command: ['--config.file=/etc/prometheus/prometheus.yml', '--storage.tsdb.retention.time=30d', '--web.enable-admin-api']",
        "    volumes:",
        "      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro",
        "      - prom-data:/prometheus",
        "    networks:",
        "      mgmt: {ipv4_address: 172.31.0.2}",
        "      front: {}",
        f"    ports: ['{bind}:{lab.get('prometheus_port', 9090)}:9090']",
        "    restart: unless-stopped",
        "  grafana:",
        f"    image: {GRAFANA_IMAGE}",
        "    container_name: vpnlab-grafana",
        "    depends_on: [prometheus]",
        "    environment:",
        "      GF_AUTH_ANONYMOUS_ENABLED: 'true'",
        "      GF_AUTH_ANONYMOUS_ORG_ROLE: Admin",
        "      GF_SECURITY_ADMIN_PASSWORD: admin",
        "      GF_USERS_DEFAULT_LANGUAGE: pt-BR",
        "      GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: /var/lib/grafana/dashboards/01-visao-geral.json",
        "      GF_ANALYTICS_REPORTING_ENABLED: 'false'",
        "      GF_ANALYTICS_CHECK_FOR_UPDATES: 'false'",
        "      GF_PLUGINS_PREINSTALL_DISABLED: 'true'",
        "    volumes:",
        "      - ../grafana/provisioning:/etc/grafana/provisioning:ro",
        "      - ../grafana/dashboards:/var/lib/grafana/dashboards:ro",
        "      - grafana-data:/var/lib/grafana",
        "    networks:",
        "      mgmt: {ipv4_address: 172.31.0.3}",
        "      front: {}",
        f"    ports: ['{bind}:{lab.get('grafana_port', 3000)}:3000']",
        "    restart: unless-stopped",
        "  optimizer:",
        f"    image: {CTL_IMAGE}",
        "    pull_policy: never",
        "    container_name: vpnlab-optimizer",
        "    depends_on: [prometheus]",
        "    command: ['python', '/app/optimizer.py', '--serve']",
        "    environment: {PROM_URL: 'http://prometheus:9090'}",
        "    volumes: ['./topology.json:/etc/vpnlab/topology.json:ro']",
        "    networks:",
        "      mgmt: {ipv4_address: 172.31.0.4}",
        "    restart: unless-stopped",
        "  ctl:",
        f"    image: {CTL_IMAGE}",
        "    pull_policy: never",
        "    profiles: [tools]",
        "    entrypoint: ['python', '/app/labctl.py']",
        "    environment: {PROM_URL: 'http://prometheus:9090', GRAFANA_URL: 'http://grafana:3000'}",
        "    volumes:",
        "      - ./topology.json:/etc/vpnlab/topology.json:ro",
        "      - ../results:/results",
        "      - /etc/localtime:/etc/localtime:ro",
        "    networks: [mgmt]",
    ]
    with open(os.path.join(BUILD, "compose.yml"), "w") as f:
        f.write("\n".join(y) + "\n")

    n_probes = sum(len(json.load(open(os.path.join(BUILD, "nodes", f"{n['name']}.json")))["probes"]) for n in nodes)
    print(f"OK: {len(servers)} servidores, {len(clients)} clientes, {n_probes} sondas -> {BUILD}")


if __name__ == "__main__":
    main()
