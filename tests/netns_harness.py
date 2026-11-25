#!/usr/bin/env python3
"""Teste de integração sem Docker: sobe cada nó em um network namespace Linux,
ligados por bridges que fazem o papel das redes `underlay` e `mgmt`.

Uso (root):  python3 tests/netns_harness.py up   tests/mini.toml
             python3 tests/netns_harness.py down
Útil para depurar o agente em qualquer Linux, sem precisar construir imagens.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.environ.get("HARNESS_DIR", "/tmp/vpnlab-harness")


def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"falhou: {cmd}\n{r.stderr}")
    return r.stdout


def up(topo):
    os.makedirs(WORK, exist_ok=True)
    env = {**os.environ, "VPNLAB_BUILD": os.path.join(WORK, "build")}
    subprocess.run([sys.executable, os.path.join(ROOT, "gen", "gen.py"), topo], check=True, env=env)
    t = json.load(open(os.path.join(WORK, "build", "topology.json")))
    for br, ip in (("vl-under", None), ("vl-mgmt", "172.31.0.1/16")):
        sh(f"ip link add {br} type bridge", check=False)
        sh(f"ip link set {br} up")
        if ip:
            sh(f"ip addr add {ip} dev {br}", check=False)
    for i, n in enumerate(t["servers"] + t["clients"]):
        ns = n["name"]
        sh(f"ip netns add {ns}")
        sh(f"ip netns exec {ns} ip link set lo up")
        for k, br, ip in ((0, "vl-under", n["underlay_ip"]), (1, "vl-mgmt", n["mgmt_ip"])):
            a, b = f"v{i}x{k}", f"eth{k}"
            sh(f"ip link add {a} type veth peer name {b} netns {ns}")
            sh(f"ip link set {a} master {br} up")
            sh(f"ip netns exec {ns} ip addr add {ip}/16 dev {b}")
            sh(f"ip netns exec {ns} ip link set {b} up")
        fwd = 1 if n["role"] == "server" else 0
        sh(f"ip netns exec {ns} sh -c 'echo {fwd} > /proc/sys/net/ipv4/ip_forward; "
           f"echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter; echo 0 > /proc/sys/net/ipv4/conf/default/rp_filter'")
        run = os.path.join(WORK, "run", ns)
        os.makedirs(run, exist_ok=True)
        logf = open(os.path.join(WORK, f"{ns}.log"), "w")
        # cada nó ganha seu próprio /run/wireguard (no Docker isso já vem de graça)
        os.makedirs("/run/wireguard", exist_ok=True)
        inner = f"mount -t tmpfs none /run/wireguard && exec {sys.executable} -u {os.path.join(ROOT, 'node', 'agent.py')}"
        subprocess.Popen(["ip", "netns", "exec", ns, "unshare", "-m", "--propagation", "private", "sh", "-c", inner],
                         env={**os.environ, "VPNLAB_CONFIG": os.path.join(WORK, "build", "nodes", f"{ns}.json"),
                              "VPNLAB_RUN": run}, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(3)
    print("nós no ar; logs em", WORK)


def down():
    sh("pkill -f '[n]ode/agent[.]py'", check=False)
    sh("pkill -f 'iperf3 -s'", check=False)
    sh("pkill wireguard-go", check=False)
    for ns in sh("ip netns list", check=False).split():
        if ns.startswith(("srv-", "cli-")):
            sh(f"ip netns del {ns}", check=False)
    for br in ("vl-under", "vl-mgmt"):
        sh(f"ip link del {br}", check=False)
    print("removido")


if __name__ == "__main__":
    if sys.argv[1] == "up":
        up(sys.argv[2])
    else:
        down()
