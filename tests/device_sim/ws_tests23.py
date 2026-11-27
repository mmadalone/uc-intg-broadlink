"""Device tests 2 and 3, simulated against the packaged driver (madalone.5).

A core-like WebSocket client drives the frozen driver. The Remote's Wi-Fi is simulated
with the Broadlink's address 192.0.2.10 on a loopback alias (up = Wi-Fi up, down =
ENETUNREACH, as when the Remote's Wi-Fi drops). "Unplugging" the Broadlink stops the
fake RM4 process (Wi-Fi up, the Broadlink silent); "plugging it in" starts it again.
Root + NET_ADMIN inside a --network none container. Python 3.11 + websockets.

Test 2 (the owner's: wake, wait 10 s, block the Remote ~15 s, press 3-4 times, unblock,
press): each press during the outage must get an error within 2 s, nothing may
be sent later, and a press afterwards must work.
Test 3 (unplug, press, plug back, press): the press while unplugged must get an error
and never be sent later; after plugging back in, a press must work.
"""

import asyncio
import fcntl
import json
import socket
import struct
import subprocess
import time

import websockets

URL = "ws://127.0.0.1:9090"
PRONTO = "0000 006D 0002 0000 0157 00AC 0015 0689"
ALIAS, ADDR = b"lo:9", "192.0.2.10"
FAKE_LOG = "/tmp/run/fake.txt"
RESULTS = []


def ts():
    """Return a wall-clock stamp for the log."""
    return f"{time.time() % 10000:9.2f}"


def _sockaddr(address: str) -> bytes:
    """Return an ifreq with ``address`` for the loopback alias."""
    return struct.pack("16sH2s4s8s", ALIAS, socket.AF_INET, bytes(2), socket.inet_aton(address), bytes(8))


def net(up: bool):
    """Bring the simulated Wi-Fi (the Broadlink's address) up or down."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sin = _sockaddr
    try:
        if up:
            fcntl.ioctl(s, 0x8916, sin(ADDR))  # SIOCSIFADDR
            fcntl.ioctl(s, 0x891C, sin("255.255.255.255"))  # SIOCSIFNETMASK
        else:
            fcntl.ioctl(s, 0x8914, struct.pack("16sH22s", ALIAS, 0, bytes(22)))  # SIOCSIFFLAGS: down
    finally:
        s.close()
    print(ts(), "CLIENT Remote Wi-Fi", "UP" if up else "DOWN (blocked)", flush=True)


def ir_sends() -> int:
    """Return how many IR commands the fake Broadlink has received."""
    with open(FAKE_LOG, encoding="utf-8", errors="replace") as file:
        return sum(1 for line in file if "FAKE command" in line)


def unplug():
    """Stop the fake Broadlink, as unplugging it would."""
    subprocess.run(["pkill", "-f", "fake_rm4.py"], check=False)
    time.sleep(0.3)
    print(ts(), "CLIENT Broadlink UNPLUGGED", flush=True)


def plug():
    """Start the fake Broadlink again."""
    subprocess.Popen(
        ["python3", "-I", "-u", "/w/fake_rm4.py"],
        stdout=open(FAKE_LOG, "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    print(ts(), "CLIENT Broadlink PLUGGED IN", flush=True)


def check(name, ok, got):
    """Record and print one result."""
    RESULTS.append((name, ok))
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got}", flush=True)


async def main():
    """Connect like the core, then run tests 2 and 3."""
    async with websockets.connect(URL, max_size=None) as ws:
        pending, counter = {}, [0]

        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                if m.get("kind") == "resp" and m.get("req_id") in pending:
                    pending.pop(m["req_id"]).set_result(m)
                elif m.get("kind") == "req" and m.get("msg") == "get_supported_entity_types":
                    await ws.send(json.dumps({"kind": "resp", "req_id": m["id"], "code": 200,
                                              "msg": "supported_entity_types",
                                              "msg_data": ["ir_emitter", "media_player", "remote"]}))

        rt = asyncio.create_task(reader())

        async def req(msg, data=None, timeout=60):
            counter[0] += 1
            i = counter[0]
            fut = asyncio.get_running_loop().create_future()
            pending[i] = fut
            payload = {"kind": "req", "id": i, "msg": msg}
            if data is not None:
                payload["msg_data"] = data
            start = time.monotonic()
            await ws.send(json.dumps(payload))
            r = await asyncio.wait_for(fut, timeout)
            return r, time.monotonic() - start

        await req("get_driver_version")
        await ws.send(json.dumps({"kind": "event", "msg": "connect"}))
        r, _ = await req("get_available_entities")
        ents = r.get("msg_data", {}).get("available_entities", [])
        await req("subscribe_events", {"entity_ids": [e["entity_id"] for e in ents]})
        print(ts(), "CLIENT entities", [(e["entity_id"], e["entity_type"]) for e in ents], flush=True)
        ir = [e["entity_id"] for e in ents if e["entity_type"] == "ir_emitter"][0]

        async def press(label):
            before = ir_sends()
            r, dt = await req("entity_command", {"entity_type": "ir_emitter", "entity_id": ir,
                                                 "cmd_id": "send_ir",
                                                 "params": {"code": PRONTO, "format": "PRONTO",
                                                            "port_id": "main"}})
            sent = ir_sends() - before
            print(ts(), f"CLIENT press {label}: answered {r.get('code')} after {dt:.2f} s; IR sends {sent}", flush=True)
            return r.get("code"), dt, sent

        print(ts(), "CLIENT the Remote is awake; waiting 10 s (past the wake wait)", flush=True)
        await asyncio.sleep(10)
        code, dt, sent = await press("P0 awake, Wi-Fi up")
        check("P0 a press while all is well is sent", code == 200 and sent == 1, f"{code}, {dt:.2f} s, {sent} sent")

        # ---------------- test 2: the Remote's Wi-Fi blocked for 15 s ----------------
        print(ts(), "CLIENT ===== TEST 2: Wi-Fi outage while awake =====", flush=True)
        base = ir_sends()
        t0 = time.monotonic()
        net(False)
        for n in range(4):
            await asyncio.sleep(max(0.0, n * 3.0 - (time.monotonic() - t0)))
            code, dt, sent = await press(f"T2.{n + 1} during the outage")
            check(f"T2.{n + 1} an error, within 2 s, nothing sent", code in (503, 500, 404) and code != 200
                  and dt <= 2.0 and sent == 0, f"{code} after {dt:.2f} s, {sent} sent")
        await asyncio.sleep(max(0.0, 15.0 - (time.monotonic() - t0)))
        net(True)
        await asyncio.sleep(8)
        late = ir_sends() - base
        check("T2.5 nothing sent late after the Wi-Fi came back", late == 0, f"{late} IR sends in the 8 s after")
        code, dt, sent = await press("T2.6 after the Wi-Fi is back")
        check("T2.6 a press afterwards works", code == 200 and sent == 1, f"{code} after {dt:.2f} s, {sent} sent")

        # ---------------- test 3: the Broadlink unplugged, then plugged back in ----------------
        print(ts(), "CLIENT ===== TEST 3: Broadlink unplugged =====", flush=True)
        await asyncio.sleep(3)
        unplug()
        base = ir_sends()
        code, dt, sent = await press("T3.1 Broadlink unplugged")
        check("T3.1 an error, nothing sent", code != 200 and sent == 0, f"{code} after {dt:.2f} s, {sent} sent")
        await asyncio.sleep(3)
        code, dt, sent = await press("T3.2 still unplugged")
        check("T3.2 an error, nothing sent", code != 200 and sent == 0, f"{code} after {dt:.2f} s, {sent} sent")
        await asyncio.sleep(5)
        plug()
        await asyncio.sleep(15)
        late = ir_sends() - base
        check("T3.3 nothing sent late after plugging it back in", late == 0, f"{late} IR sends in the 15 s after")
        code, dt, sent = await press("T3.4 after plugging it back in")
        check("T3.4 a press afterwards works", code == 200 and sent == 1, f"{code} after {dt:.2f} s, {sent} sent")
        code, dt, sent = await press("T3.5 one more")
        check("T3.5 and the next one too", code == 200 and sent == 1, f"{code} after {dt:.2f} s, {sent} sent")
        rt.cancel()
    bad = [n for n, ok in RESULTS if not ok]
    summary = f"== {len(RESULTS) - len(bad)}/{len(RESULTS)} as expected"
    print(summary + (f"; not as expected: {bad}" if bad else ""), flush=True)


asyncio.run(main())
