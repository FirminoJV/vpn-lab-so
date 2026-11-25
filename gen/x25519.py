"""X25519 (RFC 7748) em Python puro, para gerar chaves WireGuard sem depender do binário `wg`.

Uma chave privada WireGuard é um escalar de 32 bytes "clampado"; a pública é X25519(priv, 9).
"""
import base64
import os

P = 2**255 - 19
A24 = 121665


def _clamp(k: bytes) -> bytes:
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return bytes(b)


def x25519(k: bytes, u: bytes) -> bytes:
    k_int = int.from_bytes(_clamp(k), "little")
    ub = bytearray(u)
    ub[31] &= 127
    x1 = int.from_bytes(ub, "little") % P
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        kt = (k_int >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a = (x2 + z2) % P
        aa = a * a % P
        b = (x2 - z2) % P
        bb = b * b % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * b % P
        x3 = (da + cb) ** 2 % P
        z3 = x1 * (da - cb) ** 2 % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


def genkey() -> str:
    return base64.b64encode(_clamp(os.urandom(32))).decode()


def pubkey(priv_b64: str) -> str:
    priv = base64.b64decode(priv_b64)
    return base64.b64encode(x25519(priv, (9).to_bytes(32, "little"))).decode()
