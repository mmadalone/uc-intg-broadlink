"""
Direct checks of BL-9's wake window on the real ``rm.Broadlink``, IR emitter and driver.

Uses ``tests/bl8_harness.py``'s fakes (python-broadlink, unfurled's ``Remote``), so the
network-down failures come from the real unfurled and aiohttp, as there. Each check
builds a device and presses one button or sends one raw code while the Remote's network
is down; ``tests/bl9_scenarios.py`` covers the same rules through the real ucapi router,
this file the paths that are hard to reach there, deterministically:

- C1: outside the wake window, the Wi-Fi down: one attempt, 503 at once, and nothing
  is sent once the Wi-Fi is back (a stale or a lost connection; a converted or a raw
  code);
- C2: inside the wake window a converted code waits for the connection, then converts;
- C3: the wake wait setting is clamped or defaulted (a value that isn't a finite
  number: infinity, or too large for a float), and a change applies at once;
- C4: a wake wait of 0: right after a wake a press gets one attempt;
- C5: a send that waited for the device lock still counts as failing on its first
  packet (its start is noted in the worker thread), so it waits in the wake window;
- C6: a silent Broadlink in the wake window: BL-7's quick probe ends the press, by the
  window's end;
- C7: the driver's plain ucapi listeners act synchronously, as ucapi handles the event,
  before the next request: the first wake event (connect, exit_standby) starts the
  wake window, enter_standby lets the next wake start it again, and exit_standby
  also forgets BL-7's record;
- C8: a send whose first packet went out (its reply lost) and whose resend failed with
  Errno 101 after 1 s is not resent: one IR;
- C9, C10: one window per press: a press that waited in the wake window and connected at
  its end gets no second wait when its send or its conversion then finds the network
  down;
- C11: a press handled after the wake window, behind a press that reconnected at its
  end, fails at once when the Wi-Fi drops again, and is not sent later;
- C12: a press that reaches the driver before exit_standby is outside every window,
  even within the wake wait of the last wake: one attempt, 503 at once, never sent;
  the exit_standby behind it starts the window;
- C13: one window per wake: a wake event later in the same wake (a connect behind a
  waiting press, a second exit_standby) starts none, so a queued press facing a new
  drop fails at once; when the connect comes first it starts the window. With no
  enter_standby, a wake event 59 s after the window's start starts none, one 61 s
  after starts its own and moves the stamp on, and an hour asleep counts
  (WAKE_EVENT_LAPSE, CLOCK_BOOTTIME);
- C14: a device created while the remote is awake gets a retrying connect, one
  re-created for the same id replaces the old instance's task; one created in
  standby gets none, and its window is ended;
- C15: a failed send whose reconnect fails in the wake window, after an attempt whose
  hello was answered but whose auth failed, is not resent through that attempt's client:
  503 by the window's end, one background reconnect, nothing sent.

Usage (Python 3.11, the fork's requirements installed, NO network):
    docker run --rm --network none -v "$PWD":/w -w /w <image> python tests/bl9_window_checks.py <path>
(<path>: the intg-broadlink folder to test.) Exit code 1 if any check fails.

:copyright: (c) 2026 by madalone.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

# pylint: disable=protected-access,import-error,wrong-import-position

import asyncio
import errno
import importlib.util
import logging
import socket
import sys
import time
import types
from pathlib import Path


def require_offline() -> None:
    """Refuse to run with a network route (no packet is sent: only a route lookup)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        sys.exit("this process has a network route: run it with --network none")
    except OSError as err:
        if err.errno != errno.ENETUNREACH:
            sys.exit(f"unexpected routing error {err}")
    finally:
        probe.close()


require_offline()

SRC = sys.argv[1] if len(sys.argv) > 1 else "intg-broadlink"
sys.argv = [sys.argv[0], SRC, "BL-9 window checks"]
_spec = importlib.util.spec_from_file_location(
    "bl8_harness", Path(__file__).resolve().parent / "bl8_harness.py"
)
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # patches python-broadlink and ir_emitter.Remote
logging.getLogger().setLevel(logging.WARNING)
for _name in ("rm", "ir_emitter"):
    logging.getLogger(_name).setLevel(logging.INFO)

import ucapi  # noqa: E402
from ucapi import StatusCodes  # noqa: E402

RESULTS: list[bool] = []


def report(name: str, got: str, ok: bool) -> None:
    """Print and record a check's result."""
    RESULTS.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got}", flush=True)


async def device_after_window(connected: bool = True) -> tuple:
    """Return a device the remote has been awake with for a while (no wake window)."""
    h.NET = h.Net(up_at=0.0)
    device, emitter = h.make()
    if connected:
        assert await device.connect()
    device._woke_at -= 100.0
    return device, emitter


async def raw_send(device) -> tuple:
    """Send a raw code; return the status and the time taken."""
    start = h.NET.now()
    status = await device.send_command(code=b"\x26\x00")
    return status, h.NET.now() - start


async def settle(seconds: float) -> None:
    """Wait (the network comes back meanwhile: nothing may be sent late)."""
    await asyncio.sleep(seconds)


class SendScript:
    """Replace the fake Broadlink's send_data for one check; the first call is scripted."""

    def __init__(self, first):
        """Run ``first(device, data)`` on the first send, the normal fake after it."""
        self.first = first
        self.calls = 0
        self.orig = h.FakeBroadlinkDevice.send_data

    def __enter__(self):
        """Install the script."""
        script = self

        def send_data(device, data):
            script.calls += 1
            if script.calls == 1:
                return script.first(device, data)
            return script.orig(device, data)

        h.FakeBroadlinkDevice.send_data = send_data
        return self

    def __exit__(self, *_exc):
        """Put the normal fake back."""
        h.FakeBroadlinkDevice.send_data = self.orig


def first_packet_then_down(_device, data):
    """Deliver the first packet, lose its reply, and fail the resend with Errno 101."""
    h.NET.sent.append(data)  # the IR went out
    time.sleep(1.0)  # python-broadlink waits 1 s for the reply, then resends...
    h.NET.up_at = h.NET.now() + 0.6  # ...and the Wi-Fi has dropped meanwhile
    raise OSError(errno.ENETUNREACH, "Network is unreachable")


def drop_before_first_packet(_device, _data):
    """Drop the Wi-Fi just before the send: its first packet fails at once."""
    h.NET.up_at = h.NET.now() + 0.5
    raise OSError(errno.ENETUNREACH, "Network is unreachable")


async def main() -> None:  # noqa: C901
    """Run every check."""
    print(f"== BL-9 window checks on {SRC} (Python {sys.version.split()[0]})")
    if not hasattr(h.rm.Broadlink, "command_deadline"):
        sys.exit("no BL-9 code in this tree")

    # C1: outside the wake window the Wi-Fi is down until 0.6 s.
    for connected in (True, False):
        for kind in ("conv", "raw"):
            device, emitter = await device_after_window(connected)
            if not connected:
                await device.connect()  # the network worked once,
                await device.disconnect()  # then the Broadlink was lost
            h.NET = h.Net(up_at=0.6)
            if kind == "conv":
                status, took = await h.press(emitter)
            else:
                status, took = await raw_send(device)
            await settle(1.0)  # the Wi-Fi is back: nothing may go out now
            label = "stale" if connected else "disconnected"
            report(
                f"C1 outside the window, {label}, {kind}: at once, never sent",
                f"{h.status_name(status)} after {took:.2f} s, sent {len(h.NET.sent)}",
                status == StatusCodes.SERVICE_UNAVAILABLE
                and took < 0.2
                and not h.NET.sent,
            )

    # C2: inside the wake window a converted code waits for the connection.
    h.NET = h.Net(up_at=0.0)
    device, emitter = h.make()
    device.forget_unanswered()
    device.note_wake()
    h.NET = h.Net(up_at=1.0)
    status, took = await h.press(emitter)
    report(
        "C2 in the wake window, network back at 1.0: waits, converts, sends once",
        f"{h.status_name(status)} after {took:.2f} s, sent {len(h.NET.sent)}",
        status == StatusCodes.OK and len(h.NET.sent) == 1 and 0.95 <= took <= 1.6,
    )

    # C3: the setting, clamped or defaulted; a change applies at once.
    device, _ = await device_after_window()
    checks = []
    for value, want in (
        (9, 6.0),
        (-1, 0.0),
        ("x", 4.0),
        ("2.5", 2.5),
        (None, 4.0),
        (float("nan"), 4.0),
        (float("inf"), 4.0),
        (float("-inf"), 4.0),
        (10**400, 4.0),  # too large for a float: a hand-edited config.json
    ):
        device._device_config.wake_wait_sec = value
        label = "10**400" if value == 10**400 else value
        checks.append((device._wake_wait() == want, (label, device._wake_wait())))
    device._device_config.wake_wait_sec = 4.0
    device.note_wake()
    before = device.command_deadline()
    device._device_config.wake_wait_sec = 2.0
    after = device.command_deadline()
    applies = before is not None and after is not None and before - after > 1.9
    report(
        "C3 the wake wait is clamped or defaulted, and a change applies at once",
        f"{[c[1] for c in checks]}, change applies {applies}",
        all(c[0] for c in checks) and applies,
    )

    # C4: wake wait 0.
    h.NET = h.Net(up_at=0.0)
    device, emitter = h.make()
    device._device_config.wake_wait_sec = 0.0
    device.forget_unanswered()
    device.note_wake()
    h.NET = h.Net(up_at=1.0)
    status, took = await h.press(emitter)
    report(
        "C4 wake wait 0, network back at 1.0: one attempt",
        f"{h.status_name(status)} after {took:.2f} s, sent {len(h.NET.sent)}",
        status == StatusCodes.SERVICE_UNAVAILABLE and took < 0.2 and not h.NET.sent,
    )

    # C5: in the wake window the send waits 1.2 s for the device lock (another
    # python-broadlink call), then fails with Errno 101 at once: nothing went out.
    h.NET = h.Net(up_at=0.0)
    device, _ = h.make()
    assert await device.connect()
    h.NET = h.Net(up_at=1.6)
    holder = asyncio.create_task(device._run_io(time.sleep, 1.2))
    await asyncio.sleep(0.01)
    status, took = await raw_send(device)
    await holder
    report(
        "C5 a send that waited for the device lock, network back at 1.6",
        f"{h.status_name(status)} after {took:.2f} s, sent {len(h.NET.sent)}",
        status == StatusCodes.OK and len(h.NET.sent) == 1 and took <= 2.3,
    )

    # C6: a silent Broadlink in the wake window (BL-7): the press ends by the window's
    # end, after one unanswered hello (no driver retry runs here).
    h.NET = h.Net(up_at=0.0, silent=True)
    device, emitter = h.make()
    status, took = await h.press(emitter)
    report(
        "C6 Broadlink silent in the wake window",
        f"{h.status_name(status)} after {took:.2f} s, sent {len(h.NET.sent)}",
        status == StatusCodes.SERVICE_UNAVAILABLE and not h.NET.sent and took < 3.8,
    )

    # C8: the first packet went out and the resend failed after 1 s: no resend.
    h.NET = h.Net(up_at=0.0)
    device, _ = h.make()
    assert await device.connect()
    with SendScript(first_packet_then_down):
        status, took = await raw_send(device)
        await settle(1.2)  # the network is back: nothing may follow
    report(
        "C8 first packet out, resend failed after 1 s, in the wake window",
        f"{h.status_name(status)} after {took:.2f} s, IR sent {len(h.NET.sent)}",
        status == StatusCodes.SERVICE_UNAVAILABLE and len(h.NET.sent) == 1,
    )

    # C9: a press waits in a 1 s wake window, connects at its end, then its send's
    # first packet fails: no second wait.
    h.NET = h.Net(up_at=0.0)
    device, _ = h.make()
    device._device_config.wake_wait_sec = 1.0
    assert await device.connect()
    await device.disconnect()
    device.forget_unanswered()  # standby
    device.note_wake()  # the wake
    h.NET = h.Net(up_at=0.8)
    with SendScript(drop_before_first_packet):
        status, took = await raw_send(device)
        await settle(1.2)  # the network is back: nothing may follow
    report(
        "C9 one window per press: a failed send after the wake window",
        f"{h.status_name(status)} after {took:.2f} s, sent {len(h.NET.sent)}",
        status == StatusCodes.SERVICE_UNAVAILABLE and not h.NET.sent and took < 1.5,
    )

    # C10: the same through the conversion: the Wi-Fi drops right after the press
    # connected at the window's end, and the conversion finds the network down.
    h.NET = h.Net(up_at=0.0)
    device, emitter = h.make()
    device._device_config.wake_wait_sec = 1.0
    assert await device.connect()
    await device.disconnect()
    device.forget_unanswered()
    device.note_wake()
    h.NET = h.Net(up_at=0.6)
    orig_auth = h.FakeBroadlinkDevice.auth
    dropped = []

    def auth_then_drop(fake):
        orig_auth(fake)
        if not dropped:
            dropped.append(True)
            h.NET.up_at = h.NET.now() + 0.8  # the Wi-Fi drops right after the connect

    h.FakeBroadlinkDevice.auth = auth_then_drop
    try:
        status, took = await h.press(emitter)
        await settle(1.2)  # the network is back: nothing may follow
    finally:
        h.FakeBroadlinkDevice.auth = orig_auth
    report(
        "C10 one window per press: a conversion after the wake window",
        f"{h.status_name(status)} after {took:.2f} s, sent {len(h.NET.sent)}",
        status == StatusCodes.SERVICE_UNAVAILABLE and not h.NET.sent and took < 1.5,
    )

    # C11: press 1 waits in a 1 s wake window and is sent at its end; press 2, handled
    # right after it (it queued behind press 1), meets a new drop: at once, never sent.
    h.NET = h.Net(up_at=0.0)
    device, _ = h.make()
    device._device_config.wake_wait_sec = 1.0
    assert await device.connect()
    await device.disconnect()
    device.forget_unanswered()
    device.note_wake()
    h.NET = h.Net(up_at=0.8)
    s1, t1 = await raw_send(device)
    h.NET.up_at = h.NET.now() + 0.8  # the Wi-Fi drops again
    s2, t2 = await raw_send(device)
    await settle(1.2)  # the network is back: nothing more may go out
    report(
        "C11 a press handled after the window, a re-drop: at once, never sent",
        f"press 1 {h.status_name(s1)} after {t1:.2f} s; press 2 {h.status_name(s2)} "
        f"after {t2:.2f} s; sent {len(h.NET.sent)}",
        s1 == StatusCodes.OK
        and s2 == StatusCodes.SERVICE_UNAVAILABLE
        and t2 < 0.2
        and len(h.NET.sent) == 1,
    )

    # C15: in the wake window a send's first packet fails (the Wi-Fi drops); the Wi-Fi
    # is back for a moment at 0.5 s, long enough for a hello, and drops again before
    # the auth, until 6 s. That attempt leaves a client that never authenticated: the
    # press must not be resent through it, and gets BL-4's 503 by the window's end.
    h.NET = h.Net(up_at=0.0)
    device, _ = h.make()
    assert await device.connect()
    device.note_wake()
    t0 = h.NET.now()
    calls = {"sends": 0, "auths": 0}
    orig_send = h.FakeBroadlinkDevice.send_data
    orig_auth = h.FakeBroadlinkDevice.auth

    def send_then_drop(fake, data):
        calls["sends"] += 1
        if calls["sends"] == 1:
            h.NET.up_at = t0 + 0.5
            raise OSError(errno.ENETUNREACH, "Network is unreachable")
        return orig_send(fake, data)

    def auth_fails_once(fake):
        calls["auths"] += 1
        if calls["auths"] == 1:
            h.NET.up_at = t0 + 6.0  # the hello got through; the auth meets the drop
            raise OSError(errno.ENETUNREACH, "Network is unreachable")
        orig_auth(fake)

    h.FakeBroadlinkDevice.send_data = send_then_drop
    h.FakeBroadlinkDevice.auth = auth_fails_once
    background = device.driver.background_requests
    try:
        status = await device.send_command(code=b"\x26\x00")
        took = h.NET.now() - t0
        await settle(2.5)  # the Wi-Fi is back at 6 s: nothing may follow
    finally:
        h.FakeBroadlinkDevice.send_data = orig_send
        h.FakeBroadlinkDevice.auth = orig_auth
    retries = device.driver.background_requests - background
    report(
        "C15 a failed reconnect after an unauthenticated attempt: 503, no resend",
        f"{h.status_name(status)} after {took:.2f} s, send calls {calls['sends']}, "
        f"auth attempts {calls['auths']}, background reconnects {retries}, sent "
        f"{len(h.NET.sent)}",
        status == StatusCodes.SERVICE_UNAVAILABLE
        and calls["sends"] == 1
        and calls["auths"] >= 1
        and retries == 1
        and not h.NET.sent
        and took <= 4.6,
    )

    # The driver checks come last: they start the real driver's retries.
    import broadlink_driver  # pylint: disable=import-outside-toplevel

    def real_driver(device):
        """Return a real BroadlinkDriver holding ``device``."""
        driver = broadlink_driver.BroadlinkDriver(
            device_class=h.rm.Broadlink,
            entity_classes=[h.ir_emitter.BroadlinkIREmitter],
            driver_id="broadlink_driver",
            require_connection_before_registry=True,
            loop=asyncio.get_running_loop(),
        )
        driver._device_instances[device.identifier] = device
        return driver

    def emit(driver, event) -> None:
        """Emit a core event as ucapi's router does (plain listeners run now)."""
        driver.api._events.emit(event, websocket=None)

    async def stop(driver) -> None:
        """Let the framework's async handlers run, then cancel every retry."""
        await asyncio.sleep(0.05)
        driver._cancel_connect_tasks()

    # C7: the plain listeners act as ucapi emits the event, before anything else runs.
    # The driver start's connect is the first wake event; enter_standby lets the next
    # wake start the window again; exit_standby starts it and forgets BL-7's record.
    h.NET = h.Net(up_at=0.0)
    device, _ = h.make()
    driver = real_driver(device)
    device._woke_at -= 50.0
    before = device._woke_at
    emit(driver, ucapi.Events.CONNECT)
    connect_ok = device._woke_at > before and driver._wake_window_started_at is not None
    emit(driver, ucapi.Events.ENTER_STANDBY)
    enter_ok = driver._wake_window_started_at is None
    device._woke_at -= 50.0
    device._unanswered_at = 1.0
    before = device._woke_at
    emit(driver, ucapi.Events.EXIT_STANDBY)
    exit_ok = (
        device._woke_at > before
        and device._unanswered_at is None
        and driver._wake_window_started_at is not None
    )
    await stop(driver)
    report(
        "C7 connect, enter_standby and exit_standby act as ucapi emits them",
        f"connect {connect_ok}, enter_standby {enter_ok}, exit_standby {exit_ok}",
        connect_ok and enter_ok and exit_ok,
    )

    # C12: the press that wakes the remote can reach the driver before exit_standby. It
    # is outside every wake window, even within the wake wait of the last wake (here a
    # wake forced back to sleep at once): one attempt, 503 at once, nothing sent
    # later. The exit_standby queued behind it then starts the window.
    h.NET = h.Net(up_at=0.0)
    device, _ = h.make()
    assert await device.connect()
    driver = real_driver(device)
    emit(driver, ucapi.Events.CONNECT)  # a wake: its window starts...
    emit(driver, ucapi.Events.ENTER_STANDBY)  # ...and it goes back to sleep at once
    await asyncio.sleep(0.05)  # the framework's async handler disconnects the device
    h.NET = h.Net(up_at=1.0)
    status, took = await raw_send(device)  # the wake press, before exit_standby
    emit(driver, ucapi.Events.EXIT_STANDBY)  # queued behind it
    window = device.command_deadline() is not None
    await settle(1.5)  # the Wi-Fi is back: nothing may go out
    await stop(driver)
    report(
        "C12 a press before exit_standby: 503 at once, never sent; the window follows",
        f"{h.status_name(status)} after {took:.2f} s, sent {len(h.NET.sent)}, the "
        f"exit_standby behind it starts the window {window}",
        status == StatusCodes.SERVICE_UNAVAILABLE
        and took < 0.2
        and not h.NET.sent
        and window,
    )

    # C13: only the first wake event of a wake starts the window. exit_standby starts a
    # 1 s window; press 1 waits and is sent at its end; the core's connect, queued
    # behind press 1, changes nothing, so press 2, facing a new drop, fails at once. A
    # second exit_standby changes nothing either. At the next wake the connect comes
    # first: it starts the window (keeping BL-7's record), and the exit_standby behind
    # it doesn't. Then the lapse, for a driver that never saw the enter_standby, pinned
    # on both sides of 60 s: each step moves the stamp back from a window started just
    # now, so it holds on any clock. Last, 20 s awake and then an hour asleep that the
    # event loop's clock never saw: CLOCK_BOOTTIME counts it, so the wake gets a window.
    h.NET = h.Net(up_at=0.0)
    device, _ = h.make()
    device._device_config.wake_wait_sec = 1.0
    assert await device.connect()
    driver = real_driver(device)
    emit(driver, ucapi.Events.ENTER_STANDBY)
    await asyncio.sleep(0.05)  # the framework's async handler disconnects the device
    h.NET = h.Net(up_at=0.8)
    emit(driver, ucapi.Events.EXIT_STANDBY)
    s1, t1 = await raw_send(device)
    emit(driver, ucapi.Events.CONNECT)  # queued behind press 1
    h.NET.up_at = h.NET.now() + 0.8  # the Wi-Fi drops again
    s2, t2 = await raw_send(device)
    await settle(1.2)  # the network is back: nothing more may go out
    sent = len(h.NET.sent)
    device._woke_at -= 50.0
    device._unanswered_at = 1.0
    before = device._woke_at
    emit(driver, ucapi.Events.EXIT_STANDBY)  # a second exit_standby, same wake
    dup_exit = device._woke_at == before and device._unanswered_at == 1.0
    emit(driver, ucapi.Events.ENTER_STANDBY)
    emit(driver, ucapi.Events.CONNECT)  # the next wake: the connect first
    first_connect = device._woke_at > before and device._unanswered_at == 1.0
    before = device._woke_at
    emit(driver, ucapi.Events.EXIT_STANDBY)  # behind it: the same wake
    dup_after = device._woke_at == before and device._unanswered_at == 1.0

    def starts(event) -> bool:
        """Emit ``event``; return whether it started the device's wake window."""
        device._woke_at -= 50.0
        before = device._woke_at
        emit(driver, event)
        return device._woke_at > before

    def back(seconds: float) -> bool:
        """Move the window's start back by ``seconds``; False if no window started."""
        if driver._wake_window_started_at is None:
            return False
        driver._wake_window_started_at -= seconds
        return True

    emit(driver, ucapi.Events.ENTER_STANDBY)
    emit(driver, ucapi.Events.EXIT_STANDBY)  # a window started now
    at_59 = back(59.0) and not starts(ucapi.Events.EXIT_STANDBY)  # the same wake
    device._unanswered_at = 1.0
    at_61 = (
        back(2.0)
        and starts(ucapi.Events.EXIT_STANDBY)
        and device._unanswered_at is None
    )
    moved_on = at_61 and not starts(ucapi.Events.CONNECT)  # right behind: same wake
    boottime = getattr(time, "CLOCK_BOOTTIME", None)
    slept = "n/a (no CLOCK_BOOTTIME here)"
    if boottime is not None:
        emit(driver, ucapi.Events.ENTER_STANDBY)
        emit(driver, ucapi.Events.EXIT_STANDBY)
        slept = back(20.0)  # 20 s awake, then an hour asleep
        real_gettime = time.clock_gettime
        broadlink_driver.time = types.SimpleNamespace(
            monotonic=time.monotonic,
            clock_gettime=lambda clock: real_gettime(clock)
            + (3600.0 if clock == boottime else 0.0),
        )
        try:
            slept = slept and starts(ucapi.Events.EXIT_STANDBY)
        finally:
            broadlink_driver.time = time
    await stop(driver)
    report(
        "C13 one window per wake: a later wake event starts none, until the lapse",
        f"press 1 {h.status_name(s1)} after {t1:.2f} s; press 2 behind a connect "
        f"{h.status_name(s2)} after {t2:.2f} s; sent {sent}; a second exit_standby "
        f"changes nothing {dup_exit}; a connect first starts it {first_connect}; the "
        f"exit_standby behind it changes nothing {dup_after}; the lapse: 59 s starts "
        f"none {at_59}, 61 s starts one {at_61}, the connect behind it none "
        f"{moved_on}, an hour asleep starts one {slept}",
        s1 == StatusCodes.OK
        and s2 == StatusCodes.SERVICE_UNAVAILABLE
        and t2 < 0.2
        and sent == 1
        and dup_exit
        and first_connect
        and dup_after
        and at_59
        and at_61
        and moved_on
        and (slept is True or boottime is None),
    )

    # C14: a device the framework creates while the remote is awake gets a retrying
    # connect (the framework itself tries once); one created again for the same id
    # replaces the old instance's task. One created in standby gets none, and its
    # wake window is ended like the others'.
    h.NET = h.Net(up_at=100.0)  # the Broadlink can't be reached
    device, _ = h.make()
    driver = real_driver(device)
    driver.setup_device_event_handlers(device)
    first = driver._connect_tasks.get(device.identifier)
    started = first is not None and (
        driver._connect_task_device.get(device.identifier) is device
    )
    device2, _ = h.make()  # the same Broadlink, re-created
    driver._device_instances[device2.identifier] = device2
    driver.setup_device_event_handlers(device2)
    await asyncio.sleep(0.05)
    second = driver._connect_tasks.get(device2.identifier)
    replaced = (
        second is not None
        and second is not first
        and first.done()
        and driver._connect_task_device.get(device2.identifier) is device2
    )
    emit(driver, ucapi.Events.ENTER_STANDBY)
    await asyncio.sleep(0.05)  # the async handler: not awake, retries cancelled
    device3, _ = h.make()
    driver._device_instances[device3.identifier] = device3
    driver.setup_device_event_handlers(device3)
    none_in_standby = (
        device3.identifier not in driver._connect_tasks
        and device3.command_deadline() is None
    )
    await stop(driver)
    report(
        "C14 a new device keeps connecting while awake; a re-created one replaces it",
        f"a retry for a new device {started}; replaced for the re-created one "
        f"{replaced}; none in standby {none_in_standby}",
        started and replaced and none_in_standby,
    )

    failed = RESULTS.count(False)
    print(f"== {len(RESULTS) - failed}/{len(RESULTS)} as expected")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
