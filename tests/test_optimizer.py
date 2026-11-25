#!/usr/bin/env python3
"""Testes do otimizador com medições sintéticas derivadas da topologia padrão.
Rode: python3 tests/test_optimizer.py   (depois de ./lab gen)"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ctl"))
sys.path.insert(0, os.path.join(ROOT, "gen"))
import optimizer as opt  # noqa: E402
from x25519 import x25519  # noqa: E402


def test_x25519_rfc7748():
    k = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    assert x25519(k, (9).to_bytes(32, "little")).hex() == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"


def synthetic():
    t = json.load(open(os.path.join(ROOT, "build", "topology.json")))
    exp = t["expected"]
    servers = [s["name"] for s in t["servers"]]
    clients = [c["name"] for c in t["clients"]]
    meas = {}
    for c in clients:
        for s in servers:
            e = exp[f"{c}|{s}"]
            meas[(c, s, "underlay", "")] = {"rtt": e["rtt_ms"], "loss": e["loss_pct"] / 100, "jitter": 1}
            meas[(c, s, "tunnel", "")] = {"rtt": e["rtt_ms"] + 0.4, "loss": e["loss_pct"] / 100, "jitter": 1}
    for a in servers:
        for b in servers:
            if a != b:
                meas[(a, b, "mesh", "")] = {"rtt": exp[f"{a}|{b}"]["rtt_ms"] + 0.4, "loss": 0, "jitter": 1}
    return t, servers, clients, meas


def test_relay_beats_bad_peering():
    t, servers, clients, meas = synthetic()
    res = opt.analyze(meas, servers, clients)
    r = next(x for x in res["routes"] if x["client"] == "cli-tok-1" and x["exit"] == "srv-jnb")
    # tok->jnb direto (365) é pior que tok->sin->jnb (70+250): o relay deve ser sin
    assert r["best_via"] == "srv-sin", r
    assert 30 < r["gain_ms"] < 60, r


def test_hub_ranking_and_overhead():
    t, servers, clients, meas = synthetic()
    res = opt.analyze(meas, servers, clients)
    assert set(res["ranking"]["media"]) == set(servers)
    # pior caso: o hub vencedor tem o menor RTT máximo
    hubs = {h["server"]: h for h in res["hubs"]}
    w = res["ranking"]["pior_caso"][0]
    assert all(hubs[w]["rtt_max"] <= h["rtt_max"] for h in hubs.values())
    assert abs(res["tunnel_overhead_ms"] - 0.4) < 1e-6
    assert res["pairs"] and all(p["best_rtt"] <= (p["hub_rtt"] or 1e9) + 1e-6 for p in res["pairs"])
    print("melhor hub:", {o: l[0] for o, l in res["ranking"].items()})


def test_unreachable_server_penalized():
    t, servers, clients, meas = synthetic()
    for c in clients[:5]:
        meas[(c, "srv-mia", "tunnel", "")] = {"loss": 1.0}
    res = opt.analyze(meas, servers, clients)
    assert res["ranking"]["media"][-1] == "srv-mia" or res["ranking"]["media"][0] != "srv-mia"
    assert next(h for h in res["hubs"] if h["server"] == "srv-mia")["coverage"] < 1


def test_mos_bounds():
    assert opt.mos(20, 1, 0) > 4.3
    assert opt.mos(400, 30, 0.05) < 3.6 < opt.mos(150, 5, 0.0)
    m = opt.to_metrics(opt.analyze(synthetic()[3], *synthetic()[1:3]))
    assert "vpnlab_opt_best_hub" in m and "{}" not in m


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
