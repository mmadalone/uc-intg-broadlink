"""Fake Broadlink RM4 mini for the device simulation.

Enough of python-broadlink 0.19.0's UDP protocol (hello,
auth 0x65, RM4 command 0x6A) for the driver to connect and send IR. Binds 0.0.0.0:80
(root) inside a --network none container; reachable at whatever local address the test
adds. Logs every packet with a wall-clock stamp.

usage: python3 -I -u fake_rm4.py [mac hex]
"""

import socket
import struct
import sys
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

INIT_KEY = bytes.fromhex("097628343fe99e23765c1513accf8b02")
IV = bytes.fromhex("562e17996d093d28ddb3ba695a2e6f58")
SESSION_KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
DEV_ID = 0x0000ABCD
DEVTYPE = 0x51DA  # RM4 mini
MAC = bytes.fromhex(sys.argv[1] if len(sys.argv) > 1 else "a0b1c2d3e4f5")


def ts():
    """Return a wall-clock stamp for the log."""
    return f"{time.time() % 10000:9.2f}"


def aes(key, data, encrypt):
    """Encrypt or decrypt with AES-CBC, as the Broadlink protocol does."""
    c = Cipher(algorithms.AES(key), modes.CBC(IV))
    x = c.encryptor() if encrypt else c.decryptor()
    return x.update(data) + x.finalize()


def reply(req, payload, key):
    """Build a reply packet to ``req`` carrying ``payload``."""
    p = bytearray(0x38)
    p[0x00:0x08] = bytes.fromhex("5aa5aa555aa5aa55")
    p[0x24:0x26] = DEVTYPE.to_bytes(2, "little")
    p[0x26:0x28] = (int.from_bytes(req[0x26:0x28], "little") + 0x3E8).to_bytes(2, "little")
    p[0x28:0x2A] = req[0x28:0x2A]
    p[0x2A:0x30] = MAC[::-1]
    p[0x30:0x34] = DEV_ID.to_bytes(4, "little")
    p[0x34:0x36] = (sum(payload, 0xBEAF) & 0xFFFF).to_bytes(2, "little")
    pad = (16 - len(payload)) % 16
    p.extend(aes(key, payload + bytes(pad), True))
    p[0x20:0x22] = (sum(p, 0xBEAF) & 0xFFFF).to_bytes(2, "little")
    return bytes(p)


def main():
    """Answer hellos, auth and commands until killed."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 80))
    print(ts(), "FAKE listening on udp/80, mac", MAC.hex(), flush=True)
    ir_sends = 0
    while True:
        data, addr = s.recvfrom(4096)
        if len(data) == 0x30 and data[0x26] == 6:
            r = bytearray(0x80)
            r[0x34:0x36] = DEVTYPE.to_bytes(2, "little")
            r[0x3A:0x40] = MAC[::-1]
            name = b"Fake RM4 mini"
            r[0x40:0x40 + len(name)] = name
            s.sendto(bytes(r), addr)
            print(ts(), "FAKE hello from", addr, flush=True)
            continue
        ptype = int.from_bytes(data[0x26:0x28], "little")
        if ptype == 0x65:
            s.sendto(reply(data, DEV_ID.to_bytes(4, "little") + SESSION_KEY, INIT_KEY), addr)
            print(ts(), "FAKE auth from", addr, flush=True)
        elif ptype == 0x6A:
            body = aes(SESSION_KEY, data[0x38:], False)
            length, cmd = struct.unpack("<HI", body[:6])
            extra = bytes([22, 5, 40, 3]) if cmd == 0x24 else b""
            s.sendto(reply(data, struct.pack("<HI", 4 + len(extra), cmd) + extra, SESSION_KEY), addr)
            if cmd == 0x2:
                ir_sends += 1
            print(ts(), f"FAKE command 0x{cmd:x} ({length - 4} data bytes) from {addr}; IR sends so far {ir_sends}",
                  flush=True)
        else:
            print(ts(), "FAKE unknown packet type", hex(ptype), len(data), flush=True)


main()
