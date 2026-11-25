#!/usr/bin/env python3
"""vpnlab-agent — roda dentro de cada máquina virtual (servidor VPN ou cliente).

Responsabilidades:
  1. Emular a "internet": atraso, jitter, perda e banda por destino (tc htb + netem).
  2. Subir os túneis WireGuard (kernel; cai para wireguard-go se o kernel não tiver).
  3. Medir continuamente RTT, jitter e perda (sonda UDP própria, com eco em todos os nós)
     pela rede pura (underlay), pelo túnel, pela malha entre servidores e por relays.
  4. Gerar carga com iperf3 sob comando do controlador (testes de muitos usuários).
  5. Expor tudo em /metrics (formato Prometheus) e uma API HTTP de controle.

Só usa a biblioteca padrão do Python.
"""
import asyncio
import json
import logging
import math
import os
import random
import re
import shutil
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG_PATH = os.environ.get("VPNLAB_CONFIG", "/etc/vpnlab/node.json")
RUN_DIR = os.environ.get("VPNLAB_RUN", "/run/vpnlab")
MAGIC = b"VPNL"
HDR = struct.Struct("!4sIIQ")  # magic, round, seq, send_ns

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agent")

LOCK = threading.RLock()


def sh(cmd, check=True, quiet=False):
    """Executa um comando (lista) e devolve stdout."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        msg = f"{' '.join(cmd)} -> {r.returncode}: {r.stderr.strip()}"
        if check:
            raise RuntimeError(msg)
        if not quiet:
            log.warning(msg)
    return r.stdout


def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def fmt_labels(d):
    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in d.items()) + "}" if d else ""


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


# =============================================================================
# 1. Emulação de rede
# =============================================================================
class Emulator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dev = None
        self.active = False
        self.error = ""
        self.base = {l["dst"]: dict(l) for l in cfg["links"]}
        self.override = {}
        self.classes = {}
        self.uplink = cfg["uplink_mbit"]
        self.downlink = cfg.get("downlink_mbit", 0)
        self.queue_ms = cfg.get("queue_ms", 100)

    def find_dev(self):
        data = json.loads(sh(["ip", "-j", "addr"]))
        for itf in data:
            for a in itf.get("addr_info", []):
                if a.get("local") == self.cfg["underlay_ip"]:
                    return itf["ifname"]
        return None

    def current(self, dst):
        p = dict(self.base[dst])
        o = self.override.get(dst) or self.override.get("*") or {}
        if o.get("down"):
            p["loss_pct"] = 100.0
        p["delay_ms"] = max(0.0, p["delay_ms"] + o.get("add_delay_ms", 0))
        if "jitter_ms" in o:
            p["jitter_ms"] = o["jitter_ms"]
        if "loss_pct" in o and not o.get("down"):
            p["loss_pct"] = o["loss_pct"]
        return p

    def child_rate(self):
        """Garantia de cada destino = teto / nº de destinos. Se cada filho garantisse o teto inteiro,
        o HTB deixaria a soma passar do limite do nó (era o bug: 18 x 8 Mbit/s passavam por um uplink de 100)."""
        n = len(self.base) + 1
        return f"{max(0.1, self.uplink / n):.3f}mbit"

    def queue_limit(self, p):
        """Tamanho da fila em pacotes: o que está 'em voo' no atraso emulado + um buffer de roteador
        de queue_ms na banda de saída. Fila finita = quando satura, há espera E descarte, como num roteador real."""
        pkts = self.uplink * 1e6 * (p["delay_ms"] + self.queue_ms) / 1000 / (8 * 1000)
        return max(100, int(pkts))

    def netem_args(self, p):
        if os.environ.get("VPNLAB_TEST_NO_NETEM"):  # só para testes em kernels sem netem: fila simples, sem atraso
            return ["pfifo", "limit", str(self.queue_limit(p))]
        args = ["netem", "delay", f"{p['delay_ms']}ms"]
        if p["jitter_ms"] > 0:
            args += [f"{p['jitter_ms']}ms", "25%"]
        if p["loss_pct"] > 0:
            args += ["loss", f"{p['loss_pct']}%"]
        return args + ["limit", str(self.queue_limit(p))]

    def setup(self):
        self.dev = self.find_dev()
        if not self.dev:
            self.error = "interface underlay não encontrada"
            log.error(self.error)
            return
        d = self.dev
        try:
            sh(["tc", "qdisc", "del", "dev", d, "root"], check=False, quiet=True)
            up = f"{self.uplink}mbit"
            sh(["tc", "qdisc", "add", "dev", d, "root", "handle", "1:", "htb", "default", "ffff"])
            sh(["tc", "class", "add", "dev", d, "parent", "1:", "classid", "1:1", "htb", "rate", up, "ceil", up])
            share = self.child_rate()
            sh(["tc", "class", "add", "dev", d, "parent", "1:1", "classid", "1:ffff", "htb", "rate", share, "ceil", up])
            for i, dst in enumerate(sorted(self.base)):
                minor = f"{i + 16:x}"
                self.classes[dst] = minor
                sh(["tc", "class", "add", "dev", d, "parent", "1:1", "classid", f"1:{minor}", "htb", "rate", share, "ceil", up])
                sh(["tc", "qdisc", "add", "dev", d, "parent", f"1:{minor}", "handle", f"{minor}:"] + self.netem_args(self.current(dst)))
                sh(["tc", "filter", "add", "dev", d, "parent", "1:", "protocol", "ip", "prio", "1", "u32",
                    "match", "ip", "dst", f"{self.base[dst]['dst_ip']}/32", "flowid", f"1:{minor}"])
            self.active = True
            log.info("emulação ativa em %s: %d destinos, uplink %s", d, len(self.base), up)
        except RuntimeError as e:
            self.error = str(e)
            self.active = False
            sh(["tc", "qdisc", "del", "dev", d, "root"], check=False, quiet=True)
            log.error("emulação de rede indisponível (o kernel tem sch_netem/sch_htb?): %s", e)
            return
        self.set_downlink(self.downlink)

    def set_downlink(self, mbit):
        """Limite de download do nó (policiamento na entrada). 0 = sem limite."""
        d = self.dev
        self.downlink = mbit
        sh(["tc", "qdisc", "del", "dev", d, "ingress"], check=False, quiet=True)
        if not mbit:
            return
        burst = max(64, int(mbit * 1000 / 8 * 0.02))  # ~20 ms de rajada, em KB
        try:
            sh(["tc", "qdisc", "add", "dev", d, "handle", "ffff:", "ingress"])
            sh(["tc", "filter", "add", "dev", d, "parent", "ffff:", "protocol", "ip", "prio", "1", "u32",
                "match", "u32", "0", "0", "police", "rate", f"{mbit}mbit", "burst", f"{burst}k", "drop", "flowid", ":1"])
            log.info("download limitado a %s Mbit/s", mbit)
        except RuntimeError as e:
            log.warning("limite de download indisponível: %s", e)

    def apply(self, dst):
        if not self.active:
            raise RuntimeError("emulação inativa: " + self.error)
        minor = self.classes[dst]
        sh(["tc", "qdisc", "change", "dev", self.dev, "parent", f"1:{minor}", "handle", f"{minor}:"] + self.netem_args(self.current(dst)))

    def impair(self, req):
        with LOCK:
            if "uplink_mbit" in req:
                self.set_uplink(float(req["uplink_mbit"]))
            if "downlink_mbit" in req:
                self.set_downlink(float(req["downlink_mbit"]))
            if not any(k in req for k in ("add_delay_ms", "jitter_ms", "loss_pct", "down")):
                return
            dst = req.get("dst", "*")
            if dst != "*" and dst not in self.base:
                raise ValueError(f"destino desconhecido: {dst}")
            o = {k: req[k] for k in ("add_delay_ms", "jitter_ms", "loss_pct", "down") if k in req}
            if dst == "*":
                self.override = {"*": o}
                targets = list(self.base)
            else:
                self.override[dst] = o
                targets = [dst]
            for t in targets:
                self.apply(t)

    def set_uplink(self, mbit):
        up = f"{mbit}mbit"
        self.uplink = mbit
        if not self.active:
            raise RuntimeError("emulação inativa")
        sh(["tc", "class", "change", "dev", self.dev, "parent", "1:", "classid", "1:1", "htb", "rate", up, "ceil", up])
        for minor in list(self.classes.values()) + ["ffff"]:
            sh(["tc", "class", "change", "dev", self.dev, "parent", "1:1", "classid", f"1:{minor}", "htb", "rate", self.child_rate(), "ceil", up])
        for dst in self.base:
            self.apply(dst)

    def reset(self):
        with LOCK:
            self.override = {}
            if self.active:
                if self.uplink != self.cfg["uplink_mbit"]:
                    self.set_uplink(self.cfg["uplink_mbit"])
                if self.downlink != self.cfg.get("downlink_mbit", 0):
                    self.set_downlink(self.cfg.get("downlink_mbit", 0))
                for t in self.base:
                    self.apply(t)

    def qdisc_stats(self):
        """Fila e descartes na saída da 'placa' (sinal de saturação)."""
        if not self.active:
            return {}
        try:
            data = json.loads(sh(["tc", "-s", "-j", "qdisc", "show", "dev", self.dev], check=False, quiet=True) or "[]")
        except json.JSONDecodeError:
            return {}
        out = {"backlog_bytes": 0, "drops": 0, "overlimits": 0, "bytes": 0}
        for q in data:
            if q.get("kind") == "htb" and q.get("root"):
                out["drops"] += q.get("drops", 0)
                out["overlimits"] += q.get("overlimits", 0)
                out["bytes"] += q.get("bytes", 0)
            out["backlog_bytes"] += q.get("backlog", 0)
        return out


# =============================================================================
# 2. WireGuard
# =============================================================================
class WireGuard:
    def __init__(self, cfg):
        self.cfg = cfg
        self.mode = "kernel"
        self.peer_names = {}
        self.stats = []
        self.ok = {}

    def create_iface(self, name):
        sh(["ip", "link", "del", name], check=False, quiet=True)
        try:
            sh(["ip", "link", "add", name, "type", "wireguard"])
            return
        except RuntimeError:
            pass
        if not shutil.which("wireguard-go"):
            raise RuntimeError("kernel sem WireGuard e wireguard-go ausente")
        self.mode = "userspace"
        subprocess.run(["wireguard-go", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env={**os.environ, "LOG_LEVEL": "error"})
        for _ in range(50):
            if os.path.exists(f"/sys/class/net/{name}"):
                return
            time.sleep(0.1)
        raise RuntimeError(f"wireguard-go não criou {name}")

    def setup(self):
        os.makedirs(RUN_DIR, exist_ok=True)
        for w in self.cfg["wg"]:
            name = w["iface"]
            try:
                self.create_iface(name)
                lines = ["[Interface]", f"PrivateKey = {self.cfg['private_key']}"]
                if w.get("listen_port"):
                    lines.append(f"ListenPort = {w['listen_port']}")
                for p in w["peers"]:
                    self.peer_names[p["public_key"]] = p["name"]
                    lines += ["", "[Peer]", f"PublicKey = {p['public_key']}", f"AllowedIPs = {', '.join(p['allowed_ips'])}"]
                    if p.get("endpoint"):
                        lines.append(f"Endpoint = {p['endpoint']}")
                    if p.get("keepalive"):
                        lines.append(f"PersistentKeepalive = {p['keepalive']}")
                path = os.path.join(RUN_DIR, f"{name}.conf")
                with open(path, "w") as f:
                    f.write("\n".join(lines) + "\n")
                os.chmod(path, 0o600)
                sh(["wg", "setconf", name, path])
                sh(["ip", "addr", "add", w["address"], "dev", name])
                sh(["ip", "link", "set", name, "mtu", "1420", "up"])
                for r in w.get("routes", []):
                    sh(["ip", "route", "replace", r, "dev", name])
                if w.get("table"):
                    t = str(w["table"])
                    sh(["ip", "rule", "del", "from", w["source"], "table", t], check=False, quiet=True)
                    sh(["ip", "rule", "add", "from", w["source"], "table", t, "prio", str(1000 + w["table"])])
                    sh(["ip", "route", "replace", "default", "dev", name, "table", t])
                self.ok[name] = True
                log.info("túnel %s ativo (%s, %d peers)", name, self.mode, len(w["peers"]))
            except RuntimeError as e:
                self.ok[name] = False
                log.error("falha ao subir %s: %s", name, e)

    def collect(self):
        out = sh(["wg", "show", "all", "dump"], check=False, quiet=True)
        peers = []
        for line in out.splitlines():
            f = line.split("\t")
            if len(f) == 9:
                iface, pub, _psk, endpoint, _allowed, hs, rx, tx, _ka = f
                peers.append({"iface": iface, "peer": self.peer_names.get(pub, pub[:8]), "endpoint": endpoint,
                              "handshake": int(hs), "rx": int(rx), "tx": int(tx)})
        self.stats = peers


# =============================================================================
# 3. Sondas de latência/perda (UDP próprio)
# =============================================================================
class EchoServer(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.t = transport

    def datagram_received(self, data, addr):
        if data[:4] == MAGIC:
            self.t.sendto(data, addr)


class ProbeProto(asyncio.DatagramProtocol):
    def __init__(self, owner):
        self.owner = owner

    def datagram_received(self, data, addr):
        now = time.monotonic_ns()
        if len(data) < HDR.size:
            return
        magic, rnd, seq, sent = HDR.unpack_from(data)
        if magic == MAGIC:
            self.owner.on_reply(rnd, seq, (now - sent) / 1e6)

    def error_received(self, exc):
        pass


class Probe:
    def __init__(self, spec, defaults, node):
        self.s = spec
        self.d = defaults
        self.node = node
        self.round = 0
        self.replies = {}
        self.last = None
        self.sent_total = 0
        self.recv_total = 0
        self.reorder_total = 0
        self.transport = None
        self.interval = defaults["interval_s"] * spec.get("interval_mult", 1)
        # o caminho pelo túnel pode usar mais pacotes por rodada (resolução melhor para perda)
        self.d = {**defaults, **{k: spec[k] for k in ("count", "spacing_ms") if k in spec}}
        self.labels = {"src": node["name"], "src_region": node["region"], "dst": spec["dst"],
                       "dst_region": spec["dst_region"], "path": spec["path"], "via": spec.get("via", "")}

    def on_reply(self, rnd, seq, rtt):
        if rnd == self.round and seq not in self.replies:
            self.replies[seq] = (rtt, len(self.replies))

    async def ensure_socket(self, loop):
        if self.transport is None or self.transport.is_closing():
            self.transport, _ = await loop.create_datagram_endpoint(
                lambda: ProbeProto(self), local_addr=(self.s["src_ip"], 0))

    async def run(self):
        loop = asyncio.get_running_loop()
        await asyncio.sleep(random.uniform(0, self.interval))
        pad = b"\0" * max(0, self.d["size"] - HDR.size)
        while True:
            t0 = time.monotonic()
            try:
                await self.ensure_socket(loop)
                self.round = (self.round + 1) & 0xFFFFFFFF
                self.replies = {}
                n = self.d["count"]
                for seq in range(n):
                    pkt = HDR.pack(MAGIC, self.round, seq, time.monotonic_ns()) + pad
                    self.transport.sendto(pkt, (self.s["dst_ip"], self.d["echo_port"]))
                    if seq < n - 1:
                        await asyncio.sleep(self.d["spacing_ms"] / 1000)
                await asyncio.sleep(self.d["timeout_ms"] / 1000)
                self.finish(n)
            except OSError as e:
                # endereço de origem ainda não existe (túnel subindo) ou rota ausente
                self.last = {"error": str(e), "ts": time.time()}
                if self.transport:
                    self.transport.close()
                    self.transport = None
            await asyncio.sleep(max(0.5, self.interval - (time.monotonic() - t0)))

    def finish(self, n):
        rep = dict(self.replies)
        by_seq = [rep[s][0] for s in sorted(rep)]
        arrival = [rep[s][1] for s in sorted(rep)]
        reorder = sum(1 for a, b in zip(arrival, arrival[1:]) if b < a)
        rtts = sorted(by_seq)
        jitter = (sum(abs(b - a) for a, b in zip(by_seq, by_seq[1:])) / (len(by_seq) - 1)) if len(by_seq) > 1 else 0.0
        res = {"ts": time.time(), "sent": n, "received": len(rtts), "loss": 1 - len(rtts) / n, "reorder": reorder}
        if rtts:
            res.update(min=rtts[0], avg=sum(rtts) / len(rtts), max=rtts[-1], p50=percentile(rtts, .5),
                       p95=percentile(rtts, .95), jitter=jitter)
        with LOCK:
            self.last = res
            self.sent_total += n
            self.recv_total += len(rtts)
            self.reorder_total += reorder


# =============================================================================
# 4. Carga (iperf3)
# =============================================================================
UNITS = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
LINE_RE = re.compile(r"^\[\s*(SUM|\d+)\]\s+([\d.]+)-\s*([\d.]+)\s+sec\s+([\d.]+)\s+([KMGT]?)Bytes\s+([\d.]+)\s+([KMGT]?)bits/sec(.*)$")
UDP_RE = re.compile(r"([\d.]+)\s+ms\s+(\d+)/(\d+)")


def parse_iperf_line(line):
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    sid, t0, t1, nbytes, bu, rate, ru, rest = m.groups()
    rest = rest.strip()
    r = {"stream": sid, "t0": float(t0), "t1": float(t1), "bytes": float(nbytes) * UNITS[bu],
         "bps": float(rate) * UNITS[ru], "summary": rest.endswith("sender") or rest.endswith("receiver"),
         "role": "sender" if rest.endswith("sender") else ("receiver" if rest.endswith("receiver") else "")}
    u = UDP_RE.search(rest)
    if u:
        r["jitter_ms"], r["lost"], r["total"] = float(u.group(1)), int(u.group(2)), int(u.group(3))
    else:
        tok = rest.split()
        if tok and tok[0].isdigit() and (r["summary"] or len(tok) >= 3):
            r["retr"] = int(tok[0])
    return r


class Load:
    def __init__(self, req, probe):
        self.id = req.get("id") or f"load-{int(time.time() * 1000)}"
        self.server = req["server"]
        self.duration = int(req.get("duration", 30))
        self.parallel = int(req.get("parallel", 1))
        self.proto = req.get("proto", "tcp")
        self.direction = req.get("direction", "down")
        self.rate = str(req.get("rate", "0" if self.proto == "tcp" else "10M"))
        self.target = probe["dst_ip"]
        self.port = probe["iperf_port"]
        self.src = probe["src_ip"]
        self.started = time.time()
        self.intervals = []
        self.bps = 0.0
        self.bytes_total = 0.0
        self.retr = 0
        self.udp = {}
        self.final = {}
        self.error = ""
        self.active = True
        cmd = ["iperf3", "-c", self.target, "-p", str(self.port), "-B", self.src, "-t", str(self.duration),
               "-i", "1", "-f", "k", "--forceflush", "--connect-timeout", "3000", "-P", str(self.parallel)]
        if self.direction == "down":
            cmd.append("-R")
        if self.proto == "udp":
            cmd += ["-u", "-l", "1200"]
        if self.rate != "0" or self.proto == "udp":
            cmd += ["-b", self.rate]
        self.cmd = cmd
        self.attempts = 0
        threading.Thread(target=self.runner, daemon=True).start()

    def runner(self):
        while True:
            self.attempts += 1
            t0 = time.time()
            self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            self.reader()
            quick_fail = self.proc.returncode != 0 and time.time() - t0 < 8 and not self.intervals
            if not quick_fail or self.attempts >= 4:
                break
            log.info("iperf3 %s: servidor ocupado, tentando de novo (%d)", self.id, self.attempts)
            time.sleep(1.5)
        with LOCK:
            self.active = False
            self.bps = 0.0
            rcv = self.final.get("receiver")
            if rcv and "lost" in rcv:
                self.udp = {"jitter_ms": rcv["jitter_ms"], "lost": rcv["lost"], "total": rcv["total"]}
            if self.proc.returncode != 0:
                self.error = " | ".join(self.tail)

    def reader(self):
        want = "SUM" if self.parallel > 1 else None
        tail = []
        self.tail = tail
        for line in self.proc.stdout:
            tail = (tail + [line.strip()])[-5:]
            self.tail = tail
            r = parse_iperf_line(line)
            if not r or (want and r["stream"] != want) or (not want and r["stream"] == "SUM"):
                continue
            with LOCK:
                if r["summary"]:
                    self.final[r["role"]] = r
                    if "retr" in r:
                        self.retr = r["retr"]
                else:
                    self.intervals.append((r["t1"], r["bps"]))
                    self.bps = r["bps"]
                    self.bytes_total += r["bytes"]
                    if "retr" in r:
                        self.retr += r["retr"]
                    if "lost" in r:
                        self.udp = {"jitter_ms": r["jitter_ms"], "lost": r["lost"], "total": r["total"]}
        self.proc.wait()

    def stop(self):
        self.attempts = 99
        if self.active and getattr(self, "proc", None):
            self.proc.terminate()

    def summary(self, warmup=2):
        vals = [b for t, b in self.intervals if t > warmup] or [b for _, b in self.intervals]
        rcv = self.final.get("receiver", {})
        return {"id": self.id, "server": self.server, "active": self.active, "direction": self.direction,
                "proto": self.proto, "parallel": self.parallel, "rate": self.rate, "started": self.started,
                "duration": self.duration, "bps_now": self.bps,
                "bps_avg": (sum(vals) / len(vals)) if vals else 0.0,
                "bps_final": rcv.get("bps"), "retransmits": self.retr, "udp": self.udp,
                "intervals": self.intervals[-120:], "error": self.error}


class LoadManager:
    def __init__(self, cfg):
        self.tunnels = {p["dst"]: p for p in cfg["probes"] if p["path"] == "tunnel"}
        self.loads = {}
        self.totals = {}  # (server, direction, proto) -> bytes acumulados de cargas finalizadas

    def start(self, req):
        srv = req.get("server")
        if srv not in self.tunnels:
            raise ValueError(f"servidor desconhecido para este cliente: {srv}")
        with LOCK:
            for l in self.loads.values():
                if l.active and l.server == srv:
                    raise ValueError(f"já existe carga ativa para {srv} ({l.id})")
            ld = Load(req, self.tunnels[srv])
            self.loads[ld.id] = ld
            self.gc()
            return ld.summary()

    def gc(self):
        done = [k for k, l in self.loads.items() if not l.active]
        for k in done[:-50]:
            l = self.loads.pop(k)
            key = (l.server, l.direction, l.proto)
            self.totals[key] = self.totals.get(key, 0) + l.bytes_total

    def stop(self, load_id=None):
        with LOCK:
            for l in self.loads.values():
                if load_id in (None, l.id):
                    l.stop()


# =============================================================================
# 5. Servidores iperf3 (lado servidor)
# =============================================================================
def iperf_supervisor(bind, port):
    while True:
        try:
            p = subprocess.Popen(["iperf3", "-s", "-p", str(port), "-B", bind], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            p.wait()
        except OSError as e:
            log.warning("iperf3 :%d: %s", port, e)
        time.sleep(1)


# =============================================================================
# 6. Métricas e API
# =============================================================================
def cgroup_stats():
    out = {}
    try:
        with open("/sys/fs/cgroup/cpu.stat") as f:
            for line in f:
                k, v = line.split()
                if k == "usage_usec":
                    out["cpu_seconds"] = int(v) / 1e6
    except OSError:
        try:  # cgroup v1
            with open("/sys/fs/cgroup/cpuacct/cpuacct.usage") as f:
                out["cpu_seconds"] = int(f.read()) / 1e9
        except OSError:
            pass
    for path in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            with open(path) as f:
                out["memory_bytes"] = int(f.read())
                break
        except OSError:
            pass
    return out


def iface_stats():
    out = {}
    for name in os.listdir("/sys/class/net"):
        if name == "lo":
            continue
        st = {}
        for k in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_dropped", "tx_dropped"):
            try:
                with open(f"/sys/class/net/{name}/statistics/{k}") as f:
                    st[k] = int(f.read())
            except OSError:
                pass
        out[name] = st
    return out


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.started = time.time()
        self.emu = Emulator(cfg)
        self.wg = WireGuard(cfg)
        self.probes = [Probe(p, cfg["probe"], cfg) for p in cfg["probes"]]
        self.loads = LoadManager(cfg) if cfg["role"] == "client" else None

    def metrics(self):
        c = self.cfg
        L = []

        def m(name, val, labels=None, help_=None, typ="gauge"):
            if help_:
                L.append(f"# HELP {name} {help_}")
                L.append(f"# TYPE {name} {typ}")
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return
            L.append(f"{name}{fmt_labels(labels or {})} {val}")

        m("vpnlab_node_info", 1, {"node": c["name"], "role": c["role"], "region": c["region"],
                                    "wg_mode": self.wg.mode, "underlay_dev": self.emu.dev or ""},
          "Informações do nó")
        m("vpnlab_uptime_seconds", round(time.time() - self.started, 1), None, "Tempo desde o início do agente")
        m("vpnlab_emulation_active", int(self.emu.active), None, "1 se tc/netem está emulando a internet")
        m("vpnlab_uplink_mbit", self.emu.uplink, None, "Banda de saída configurada (Mbit/s)")
        m("vpnlab_downlink_mbit", self.emu.downlink, None, "Banda de entrada configurada (Mbit/s, 0 = livre)")

        with LOCK:
            # ---- configuração de enlace (o que foi pedido) ----
            for name, unit in (("delay_ms", "Atraso de ida configurado"), ("jitter_ms", "Jitter configurado"),
                               ("loss_pct", "Perda de ida configurada (%)")):
                first = True
                for dst in sorted(self.emu.base):
                    m(f"vpnlab_link_{name}", self.emu.current(dst)[name], {"dst": dst}, unit if first else None)
                    first = False
            m("vpnlab_link_impaired", None, None, "1 se o enlace tem falha injetada")
            for dst, o in self.emu.override.items():
                m("vpnlab_link_impaired", 1, {"dst": dst})

            # ---- sondas ----
            fields = [("rtt_min_ms", "min", "RTT mínimo da última rodada (ms)"),
                      ("rtt_avg_ms", "avg", "RTT médio da última rodada (ms)"),
                      ("rtt_p50_ms", "p50", "RTT mediano (ms)"),
                      ("rtt_p95_ms", "p95", "RTT percentil 95 (ms)"),
                      ("rtt_max_ms", "max", "RTT máximo (ms)"),
                      ("jitter_ms", "jitter", "Jitter: média das variações entre pacotes consecutivos (ms)"),
                      ("loss_ratio", "loss", "Fração de pacotes perdidos na última rodada (0-1)")]
            for metric, key, help_ in fields:
                L.append(f"# HELP vpnlab_probe_{metric} {help_}")
                L.append(f"# TYPE vpnlab_probe_{metric} gauge")
                for p in self.probes:
                    if p.last and key in p.last:
                        v = p.last[key]
                        L.append(f"vpnlab_probe_{metric}{fmt_labels(p.labels)} {round(v, 4)}")
            for metric, attr, help_ in (("sent_total", "sent_total", "Pacotes de sonda enviados"),
                                        ("received_total", "recv_total", "Pacotes de sonda recebidos"),
                                        ("reordered_total", "reorder_total", "Pacotes fora de ordem")):
                L.append(f"# HELP vpnlab_probe_{metric} {help_}")
                L.append(f"# TYPE vpnlab_probe_{metric} counter")
                for p in self.probes:
                    L.append(f"vpnlab_probe_{metric}{fmt_labels(p.labels)} {getattr(p, attr)}")
            L.append("# HELP vpnlab_probe_up 1 se o destino respondeu na última rodada")
            L.append("# TYPE vpnlab_probe_up gauge")
            for p in self.probes:
                up = 1 if (p.last and p.last.get("received", 0) > 0) else 0
                L.append(f"vpnlab_probe_up{fmt_labels(p.labels)} {up}")

            # ---- WireGuard ----
            m("vpnlab_wg_up", None, None, "1 se a interface WireGuard subiu")
            for itf, ok in self.wg.ok.items():
                m("vpnlab_wg_up", int(ok), {"iface": itf})
            now = time.time()
            for metric, help_, typ in (("vpnlab_wg_peer_rx_bytes_total", "Bytes recebidos do peer", "counter"),
                                       ("vpnlab_wg_peer_tx_bytes_total", "Bytes enviados ao peer", "counter"),
                                       ("vpnlab_wg_peer_handshake_age_seconds", "Segundos desde o último handshake", "gauge")):
                L.append(f"# HELP {metric} {help_}")
                L.append(f"# TYPE {metric} {typ}")
                for p in self.wg.stats:
                    lb = {"iface": p["iface"], "peer": p["peer"]}
                    if metric.endswith("rx_bytes_total"):
                        L.append(f"{metric}{fmt_labels(lb)} {p['rx']}")
                    elif metric.endswith("tx_bytes_total"):
                        L.append(f"{metric}{fmt_labels(lb)} {p['tx']}")
                    elif p["handshake"] > 0:
                        L.append(f"{metric}{fmt_labels(lb)} {round(now - p['handshake'], 1)}")
            active_peers = sum(1 for p in self.wg.stats if p["handshake"] and now - p["handshake"] < 180)
            m("vpnlab_wg_active_peers", active_peers, None, "Peers com handshake nos últimos 3 min")

            # ---- carga ----
            if self.loads:
                L += ["# HELP vpnlab_load_active 1 se há carga iperf3 rodando para o servidor",
                      "# TYPE vpnlab_load_active gauge",
                      "# HELP vpnlab_load_throughput_bps Vazão do último segundo (bit/s)",
                      "# TYPE vpnlab_load_throughput_bps gauge",
                      "# HELP vpnlab_load_bytes_total Bytes transferidos por testes de carga",
                      "# TYPE vpnlab_load_bytes_total counter",
                      "# HELP vpnlab_load_retransmits_total Retransmissões TCP (lado emissor)",
                      "# TYPE vpnlab_load_retransmits_total counter",
                      "# HELP vpnlab_load_udp_loss_ratio Perda em teste UDP (0-1)",
                      "# TYPE vpnlab_load_udp_loss_ratio gauge"]
                agg = {}
                for (srv, d, pr), b in self.loads.totals.items():
                    agg.setdefault((srv, d, pr), {"bytes": 0.0, "bps": 0.0, "active": 0, "retr": 0})["bytes"] += b
                for l in self.loads.loads.values():
                    a = agg.setdefault((l.server, l.direction, l.proto), {"bytes": 0.0, "bps": 0.0, "active": 0, "retr": 0})
                    a["bytes"] += l.bytes_total
                    a["retr"] += l.retr
                    if l.active:
                        a["active"] = 1
                        a["bps"] += l.bps
                        if l.udp.get("total"):
                            L.append(f"vpnlab_load_udp_loss_ratio{fmt_labels({'server': l.server, 'direction': l.direction})} "
                                     f"{round(l.udp['lost'] / l.udp['total'], 5)}")
                for (srv, d, pr), a in agg.items():
                    lb = {"server": srv, "direction": d, "proto": pr}
                    L.append(f"vpnlab_load_active{fmt_labels(lb)} {a['active']}")
                    L.append(f"vpnlab_load_throughput_bps{fmt_labels(lb)} {round(a['bps'])}")
                    L.append(f"vpnlab_load_bytes_total{fmt_labels(lb)} {round(a['bytes'])}")
                    L.append(f"vpnlab_load_retransmits_total{fmt_labels(lb)} {a['retr']}")

        # ---- interfaces, fila, recursos ----
        L += ["# HELP vpnlab_iface_bytes_total Bytes por interface", "# TYPE vpnlab_iface_bytes_total counter",
              "# HELP vpnlab_iface_dropped_total Pacotes descartados por interface",
              "# TYPE vpnlab_iface_dropped_total counter"]
        for name, st in iface_stats().items():
            nm = "underlay" if name == self.emu.dev else name
            for d in ("rx", "tx"):
                if f"{d}_bytes" in st:
                    L.append(f"vpnlab_iface_bytes_total{fmt_labels({'iface': nm, 'dir': d})} {st[d + '_bytes']}")
                    L.append(f"vpnlab_iface_dropped_total{fmt_labels({'iface': nm, 'dir': d})} {st.get(d + '_dropped', 0)}")
        q = self.emu.qdisc_stats()
        if q:
            m("vpnlab_queue_backlog_bytes", q["backlog_bytes"], None, "Bytes enfileirados na saída (bufferbloat)")
            m("vpnlab_queue_drops_total", q["drops"], None, "Descartes na fila de saída (inclui perda emulada)", "counter")
            m("vpnlab_queue_overlimits_total", q["overlimits"], None, "Vezes que o limite de banda segurou pacotes", "counter")
        cg = cgroup_stats()
        if "cpu_seconds" in cg:
            m("vpnlab_cpu_seconds_total", round(cg["cpu_seconds"], 3), None, "CPU usada pelo contêiner", "counter")
        if "memory_bytes" in cg:
            m("vpnlab_memory_bytes", cg["memory_bytes"], None, "Memória usada pelo contêiner")
        return "\n".join(L) + "\n"

    def state(self):
        with LOCK:
            return {
                "name": self.cfg["name"], "role": self.cfg["role"], "region": self.cfg["region"],
                "emulation": {"active": self.emu.active, "dev": self.emu.dev, "error": self.emu.error,
                              "uplink_mbit": self.emu.uplink, "downlink_mbit": self.emu.downlink, "override": self.emu.override},
                "wg": {"mode": self.wg.mode, "up": self.wg.ok, "peers": self.wg.stats},
                "probes": [{**p.labels, "last": p.last} for p in self.probes],
                "loads": [l.summary() for l in self.loads.loads.values()] if self.loads else [],
            }


def make_handler(agent):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/metrics":
                self.send(200, agent.metrics(), "text/plain; version=0.0.4")
            elif path == "/state":
                self.send(200, agent.state())
            elif path == "/load":
                self.send(200, agent.state()["loads"])
            elif path == "/health":
                self.send(200, {"ok": True})
            else:
                self.send(404, {"error": "rota desconhecida"})

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n) or b"{}")
                if path == "/load":
                    if not agent.loads:
                        raise ValueError("carga só pode ser iniciada em clientes")
                    self.send(200, agent.loads.start(req))
                elif path == "/load/stop":
                    if agent.loads:
                        agent.loads.stop(req.get("id"))
                    self.send(200, {"ok": True})
                elif path == "/impair":
                    agent.emu.impair(req)
                    self.send(200, {"ok": True, "override": agent.emu.override, "uplink_mbit": agent.emu.uplink})
                elif path == "/impair/reset":
                    agent.emu.reset()
                    self.send(200, {"ok": True})
                else:
                    self.send(404, {"error": "rota desconhecida"})
            except (ValueError, KeyError, RuntimeError) as e:
                self.send(400, {"error": str(e)})
    return H


def main():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    agent = Agent(cfg)
    log.info("nó %s (%s, região %s)", cfg["name"], cfg["role"], cfg["region"])
    agent.emu.setup()
    agent.wg.setup()

    if cfg["role"] == "server" and shutil.which("iperf3"):
        for port in cfg["iperf"]["ports"]:
            threading.Thread(target=iperf_supervisor, args=(cfg["iperf"]["bind"], port), daemon=True).start()

    def wg_loop():
        while True:
            try:
                agent.wg.collect()
            except Exception as e:  # noqa: BLE001
                log.warning("coleta wg: %s", e)
            time.sleep(5)
    threading.Thread(target=wg_loop, daemon=True).start()

    srv = ThreadingHTTPServer(("0.0.0.0", cfg.get("agent_port", 9100)), make_handler(agent))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("métricas em :%d/metrics — %d sondas", cfg.get("agent_port", 9100), len(agent.probes))

    async def run():
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(EchoServer, local_addr=("0.0.0.0", cfg["probe"]["echo_port"]))
        await asyncio.gather(*(p.run() for p in agent.probes), return_exceptions=False)
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main()
