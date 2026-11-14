"""
Offline harness for BL-8: an IR code converted by the Remote, sent through a Broadlink.

Runs the real ``BroadlinkIREmitter`` command handler and the real ``rm.Broadlink`` device.
Only the network edges are fake:

- python-broadlink (``broadlink.hello`` / ``xdiscover`` / the device): while the Remote's
  network is down every call fails at once with ``[Errno 101] Network is unreachable``,
  as after a wake; a silent Broadlink blocks until the timeout, like the real library.
- unfurled's ``Remote`` (the Remote's IR conversion API). A successful conversion is
  fake. Failures come from the REAL unfurled and aiohttp, over real sockets, so the
  exception classes, their wrapping and the (missing) time limits are the libraries' own:
  - network down: a real request to the Remote's address in a container without a
    network fails at once with ``[Errno 101] Network is unreachable``, as after a wake;
  - refused: a real request to a closed loopback port;
  - hanging: a real request to a loopback server that accepts and never answers.
  HTTP error answers are unfurled's own exception classes, raised directly.

Usage (Python 3.11, the fork's requirements installed, NO network, so that a request to
the Remote's address fails like a Remote whose Wi-Fi is down):
    docker run --rm --network none -v "$PWD":/w -w /w <image> python tests/bl8_harness.py <path> [label]
(<path>: the intg-broadlink folder to test.)
The path lets the same scenarios run against the code before BL-8 (``git archive``).

:copyright: (c) 2026 by madalone.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

# pylint: disable=import-error,wrong-import-position,protected-access,too-few-public-methods

import asyncio
import errno
import gc
import inspect
import json
import logging
import sys
import time

SRC = sys.argv[1] if len(sys.argv) > 1 else "intg-broadlink"
LABEL = sys.argv[2] if len(sys.argv) > 2 else SRC
sys.path.insert(0, SRC)

import broadlink  # noqa: E402
import ir_emitter  # noqa: E402
import rm  # noqa: E402
import unfurled  # noqa: E402
from broadlink.exceptions import NetworkTimeoutError  # noqa: E402
from config_manager import BroadlinkConfig  # noqa: E402
from ucapi import StatusCodes  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")

MAC = "a043b0112233"
IP = "192.168.2.124"
PRONTO = "0000 006D 0002 0000 0157 00AC 0015 0689"
RAW = [9000, 4500, 560, 560, 560, 1690]
REMOTE_URL = "http://192.168.2.204/api/"
REFUSED_URL = "http://127.0.0.1:9/api/"
BAD_URL = "http://[::1/api/"  # unfurled refuses it: ValueError
HOSTNAME_URL = "http://remote3-c9087a.local/api/"
# yarl accepts the URL, aiohttp refuses it when sending: InvalidUrlClientError (a
# ValueError) wrapped in unfurled's ConnectionError, which BL-8 answers 400.
INVALID_PORT_URL = "http://127.0.0.1:99999/api/"
HANG_URL = ""  # set by main(): a loopback server that never answers


class Net:
    """The Remote's network and the Broadlink, as seen by the fakes."""

    def __init__(self, up_at: float = 0.0, silent: bool = False):
        """Network back ``up_at`` s from now (None: never); ``silent``: Broadlink mute."""
        self.t0 = time.monotonic()
        self.up_at = up_at  # seconds after t0; None: never
        self.silent = silent
        self.sent: list[bytes] = []
        self.converts = 0
        self.convert_mode = "ok"
        self.remote_url = REMOTE_URL  # what the Remote's API is reached at
        self.wake_flags: list[bool] = []

    def now(self) -> float:
        """Return the seconds since this scenario started."""
        return time.monotonic() - self.t0

    def up(self) -> bool:
        """Return whether the Remote's network is usable now."""
        return self.up_at is not None and self.now() >= self.up_at


NET = Net()


def _unreachable() -> OSError:
    return OSError(101, "Network is unreachable")


class FakeBroadlinkDevice:
    """python-broadlink device: blocking calls, run in a thread by rm._run_io."""

    def __init__(self):
        """Create a device with the configured MAC and IP."""
        self.mac = bytes.fromhex(MAC)
        self.host = (IP, 80)
        self.timeout = 10

    def _wait_silent(self):
        """Block until the timeout, then fail like python-broadlink."""
        time.sleep(self.timeout)
        raise NetworkTimeoutError(-4000, "Network timeout", "No response received")

    def auth(self):
        """Authenticate, or fail like python-broadlink."""
        if not NET.up():
            raise _unreachable()
        if NET.silent:
            self._wait_silent()
        return True

    def send_data(self, data):
        """Send IR data, or fail like python-broadlink."""
        if not NET.up():
            raise _unreachable()
        if NET.silent:
            self._wait_silent()
        NET.sent.append(data)


def fake_hello(ip_address, timeout=10, **_kwargs):
    """Answer a directed hello like python-broadlink."""
    if not NET.up():
        raise _unreachable()
    if NET.silent:
        time.sleep(timeout)
        raise NetworkTimeoutError(-4000, "Network timeout", "No response received")
    return FakeBroadlinkDevice()


def fake_xdiscover(timeout=10, **_kwargs):
    """Discover like python-broadlink: yield the device if it answers."""
    if not NET.up():
        raise _unreachable()
    time.sleep(timeout if NET.silent else 0.01)
    if not NET.silent:
        yield FakeBroadlinkDevice()


async def real_convert(url, code, ir_format):
    """Convert with the real unfurled against ``url``: its real errors, its timeouts."""
    remote = unfurled.Remote(url, api_key="key", wake_if_asleep=False)
    try:
        return await remote.ir.convert(code, format=ir_format)
    finally:
        await remote.close()


class FakeIR:
    """unfurled's IR submodule: only ``convert``."""

    async def convert(self, code, *, format=None, repeat=None):  # noqa: A002
        """Convert a code, or fail like unfurled (really: through unfurled)."""
        NET.converts += 1
        await asyncio.sleep(0.005)
        if not NET.up():
            if NET.convert_mode == "down_unrecognised":
                # a network-down failure the conversion retry doesn't recognise: only the
                # wait for the Broadlink connection before converting can save the press
                raise unfurled.ConnectionError("Cannot connect") from OSError(
                    errno.ECONNREFUSED, "Connection refused"
                )
            return await real_convert(NET.remote_url, code, format)
        mode = NET.convert_mode
        if mode == "refused":
            return await real_convert(REFUSED_URL, code, format)
        if mode == "badurl":
            return await real_convert(INVALID_PORT_URL, code, format)
        if mode == "hang":
            return await real_convert(HANG_URL, code, format)
        if mode == "http503_once" and NET.converts == 1:
            raise unfurled.HTTPError(503, "SERVICE_UNAVAILABLE - starting")
        if mode == "http500":
            raise unfurled.HTTPError(500, "INTERNAL_ERROR - boom")
        if mode == "http503":
            raise unfurled.HTTPError(503, "SERVICE_UNAVAILABLE - busy")
        if mode == "http400":
            raise unfurled.HTTPError(400, "BAD_REQUEST - invalid code")
        if mode == "auth":
            raise unfurled.AuthenticationError(
                "Authentication failed (check API key / PIN)"
            )
        if mode == "badraw":
            return {"raw": "nope"}
        return {"raw": list(RAW)}


class FakeRemote:
    """unfurled.Remote, as far as the IR emitter uses it."""

    def __init__(self, api_url, *, api_key=None, pin=None, wake_if_asleep=True, **_kw):
        """Record the wake_if_asleep argument; refuse what the real Remote refuses."""
        unfurled.Remote(api_url, api_key=api_key, wake_if_asleep=wake_if_asleep)
        NET.wake_flags.append(wake_if_asleep)
        self.ir = FakeIR()

    async def close(self):
        """Close nothing."""
        return None


class FakeDriver:
    """The two driver hooks rm.Broadlink uses."""

    def __init__(self):
        """Count background reconnect requests."""
        self.background_requests = 0

    def reconnect_in_background(self, _device_id):
        """Record a background reconnect request (BL-7)."""
        self.background_requests += 1


broadlink.hello = fake_hello
broadlink.xdiscover = fake_xdiscover
ir_emitter.Remote = FakeRemote


def make(api_key: str | None = "key", remote_address: str = REMOTE_URL) -> tuple:
    """Create the device and the IR emitter under test."""
    config = BroadlinkConfig(
        identifier=MAC,
        name="RM4",
        address=IP,
        data={},
        remote_address=remote_address if api_key else None,
        remote_api_key=api_key,
    )
    device = rm.Broadlink(config, asyncio.get_running_loop(), driver=FakeDriver())
    emitter = ir_emitter.BroadlinkIREmitter(config, device)
    emitter._api = (
        FakeApi()
    )  # the framework sets it once the driver registers the entity
    return device, emitter


class _ConfiguredEntities:
    """The integration API's configured-entity store: the entity isn't configured."""

    def contains(self, _entity_id):
        """Report the entity as not configured."""
        return False

    def get(self, _entity_id):
        """Return no configured entity."""
        return None

    def update_attributes(self, *_args, **_kwargs):
        """Accept an attribute update."""
        return True


class FakeApi:
    """The part of ucapi's IntegrationAPI that entity updates touch."""

    configured_entities = _ConfiguredEntities()


async def press(emitter, code=PRONTO, repeat=1, cap=15.0):
    """Press a button with an IR code; return the status and the time taken.

    A handler still busy after ``cap`` seconds is reported as "NO ANSWER" (ucapi would
    hold every later press behind it).
    """
    start = NET.now()
    try:
        status = await asyncio.wait_for(
            emitter.command_handler(
                emitter, "send_ir", {"code": code, "repeat": repeat}
            ),
            cap,
        )
    except TimeoutError:
        status = "NO ANSWER"
    return status, NET.now() - start


RESULTS: list[tuple[str, str, bool]] = []


def report(name: str, got: str, ok: bool):
    """Print and record a scenario result."""
    RESULTS.append((name, got, ok))
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got}")


def causes(err):
    """Yield ``err`` and the exceptions it was raised from."""
    while err is not None:
        yield err
        err = err.__cause__ or err.__context__


def status_name(status) -> str:
    """Return a status code's name."""
    return getattr(status, "name", str(status))


async def wake_press(up_at, connected_before=False):
    """Press at t=0 of a wake; the network comes back at ``up_at`` (None: never)."""
    global NET  # pylint: disable=global-statement
    NET = Net(up_at=0.0)
    device, emitter = make()
    if connected_before:
        assert await device.connect()
    NET = Net(up_at=up_at)
    if not connected_before:
        device.forget_unanswered()
    status, took = await press(emitter)
    return status, took, len(NET.sent), NET.converts, device


async def main():
    """Run every scenario."""
    global NET  # pylint: disable=global-statement
    print(f"== BL-8 harness on {LABEL} (Python {sys.version.split()[0]})")
    bl8 = hasattr(rm.Broadlink, "ensure_connected_for_command")

    # The real unfurled must fail exactly as on a waking Remote, or the run is invalid.
    try:
        await real_convert(REMOTE_URL, PRONTO, "PRONTO")
        sys.exit("a request to the Remote's address worked: run with --network none")
    except unfurled.ConnectionError as err:
        chain = [type(c).__name__ for c in causes(err)]
        errnos = [getattr(c, "errno", None) for c in causes(err)]
        print(f"== real unfurled, network down: {' <- '.join(chain)}, errno {errnos}")
        if errno.ENETUNREACH not in errnos:
            sys.exit("no ENETUNREACH in the error chain: run with --network none")

    async def never_answer(_reader, _writer):
        await asyncio.sleep(3600)

    global HANG_URL  # pylint: disable=global-statement
    server = await asyncio.start_server(never_answer, "127.0.0.1", 0)
    HANG_URL = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/api/"

    # W1: the press that wakes the Remote; Broadlink disconnected by the standby.
    for up_at in (0.05, 0.3, 0.6, 1.0, 2.0, 2.4):
        status, took, sent, converts, _ = await wake_press(up_at)
        report(
            f"W1 wake press, network back at {up_at} s",
            f"{status_name(status)} after {took:.2f} s, sent {sent}, conversions {converts}",
            # one conversion: BL-8 converts only once the Broadlink (and so the network)
            # is back, instead of retrying the conversion until it is
            status == StatusCodes.OK and sent == 1 and converts == 1,
        )

    # W2: the network takes longer than the press's window (BL-2: the last reconnect
    # attempt starts 2.5 s into the press).
    for up_at in (2.8, 3.5, None):
        status, took, sent, converts, device = await wake_press(up_at)
        background = device.driver.background_requests
        report(
            f"W2 wake press, network back at {up_at} s",
            f"{status_name(status)} after {took:.2f} s, sent {sent}, conversions "
            f"{converts}, background reconnect requests {background}",
            status == StatusCodes.SERVICE_UNAVAILABLE
            and sent == 0
            and took < 3.6
            and converts == 0  # no conversion is attempted without the Broadlink
            and background == 1,  # BL-7: the driver keeps trying in the background
        )

    # W3: Broadlink still marked connected (no standby seen), network down for a while.
    for up_at in (0.3, 1.0, 2.0):
        status, took, sent, converts, _ = await wake_press(up_at, connected_before=True)
        report(
            f"W3 stale connection, network back at {up_at} s",
            f"{status_name(status)} after {took:.2f} s, sent {sent}, conversions {converts}",
            status == StatusCodes.OK and sent == 1,
        )
    status, took, sent, converts, _ = await wake_press(5.0, connected_before=True)
    report(
        "W3 stale connection, network back at 5 s",
        f"{status_name(status)} after {took:.2f} s, sent {sent}, conversions {converts}",
        status == StatusCodes.SERVICE_UNAVAILABLE and sent == 0 and took < 3.6,
    )

    # W12: the driver's wake retry (BL-1, an attempt every 0.5 s) runs alongside the press.
    for up_at in (0.3, 1.0, 2.0):
        NET = Net(up_at=up_at)
        device, emitter = make()
        device.forget_unanswered()

        async def wake_retry(dev=device):
            while not dev.is_connected:
                if not await dev.connect():
                    await asyncio.sleep(0.5)

        retry = asyncio.create_task(wake_retry())
        await asyncio.sleep(0.05)
        status, took = await press(emitter)
        retry.cancel()
        report(
            f"W12 wake press 50 ms after exit_standby, wake retry running, network back "
            f"at {up_at} s",
            f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
            f"conversions {NET.converts}",
            status == StatusCodes.OK and len(NET.sent) == 1 and NET.converts == 1,
        )

    # W4: network fine, Broadlink not answering: no conversion needed to know it fails.
    NET = Net(up_at=0.0, silent=True)
    device, emitter = make()
    status, took = await press(emitter)
    report(
        "W4 Broadlink silent",
        f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
        f"conversions {NET.converts}",
        status == StatusCodes.SERVICE_UNAVAILABLE
        and not NET.sent
        and NET.converts == 0,
    )

    # W5: conversion errors are answered with what went wrong; nothing is sent.
    expected = {
        "http500": StatusCodes.SERVER_ERROR,
        "http503": StatusCodes.SERVICE_UNAVAILABLE,
        "http400": StatusCodes.BAD_REQUEST,
        "auth": StatusCodes.UNAUTHORIZED,
        "badraw": StatusCodes.BAD_REQUEST,
        "badurl": StatusCodes.BAD_REQUEST,
        "refused": StatusCodes.SERVICE_UNAVAILABLE,
        "hang": StatusCodes.SERVICE_UNAVAILABLE,
    }
    for mode, want in expected.items():
        NET = Net(up_at=0.0)
        NET.convert_mode = mode
        device, emitter = make()
        status, took = await press(emitter)
        report(
            f"W5 conversion error {mode}",
            f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
            f"conversions {NET.converts}",
            status == want and not NET.sent and took < 3.6
            # only a network that is down, or the API's own 503, is worth retrying
            and (NET.converts == 1 or mode == "http503"),
        )

    # W13: the Remote's API answers 503 once (starting up), then converts.
    NET = Net(up_at=0.0)
    NET.convert_mode = "http503_once"
    device, emitter = make()
    status, took = await press(emitter)
    report(
        "W13 the Remote's API answers 503 once, then converts",
        f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
        f"conversions {NET.converts}",
        status == StatusCodes.OK and len(NET.sent) == 1,
    )

    # W15: a malformed Remote address in the configuration.
    NET = Net(up_at=0.0)
    device, emitter = make(remote_address=BAD_URL)
    status, took = await press(emitter)
    report(
        "W15 malformed Remote address in the configuration",
        f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}",
        status == StatusCodes.BAD_REQUEST and not NET.sent,
    )

    # W16: the Remote's address is a host name: without a network the real name lookup
    # fails (EAI_AGAIN) before any connection, so that must count as "network down".
    for up_at in (1.0, None):
        NET = Net(up_at=up_at)
        NET.remote_url = HOSTNAME_URL
        device, emitter = make(remote_address=HOSTNAME_URL)
        device.forget_unanswered()
        status, took = await press(emitter)
        want = StatusCodes.OK if up_at is not None else StatusCodes.SERVICE_UNAVAILABLE
        report(
            f"W16 host-name address, network back at {up_at} s",
            f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
            f"conversions {NET.converts}",
            status == want
            and len(NET.sent) == (1 if up_at is not None else 0)
            and NET.converts == (1 if up_at is not None else 0),
        )

    # W17: the same, with a Broadlink still marked connected, so only the conversion
    # retry can wait for the network.
    NET = Net(up_at=0.0)
    device, emitter = make(remote_address=HOSTNAME_URL)
    assert await device.connect()
    NET = Net(up_at=1.0)
    NET.remote_url = HOSTNAME_URL
    status, took = await press(emitter)
    report(
        "W17 host-name address, stale connection, network back at 1.0 s",
        f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
        f"conversions {NET.converts}",
        status == StatusCodes.OK and len(NET.sent) == 1,
    )

    # W18: the network-down failure is one the conversion retry does NOT recognise; the
    # wait for the Broadlink connection is what saves the wake press.
    NET = Net(up_at=1.0)
    NET.convert_mode = "down_unrecognised"
    device, emitter = make()
    device.forget_unanswered()
    status, took = await press(emitter)
    report(
        "W18 wake press, unrecognised network-down error, network back at 1.0 s",
        f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
        f"conversions {NET.converts}",
        status == StatusCodes.OK and len(NET.sent) == 1 and NET.converts == 1,
    )

    # W19: the Broadlink connects late (network back at 2.4 s) and the conversion API
    # never answers: the conversion gets what is left of the window, at least 1 s.
    NET = Net(up_at=2.4)
    NET.convert_mode = "hang"
    device, emitter = make()
    device.forget_unanswered()
    status, took = await press(emitter)
    report(
        "W19 late connection (network back at 2.4 s), conversion API never answers",
        f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
        f"conversions {NET.converts}",
        status == StatusCodes.SERVICE_UNAVAILABLE and not NET.sent and took < 4.1,
    )

    # W20: end to end through the REAL unfurled Remote (not the fake): one session, a
    # loopback server that answers 503 twice and then converts; and a server that never
    # answers, so the request is cancelled and the session must still be closed.
    for name, plan, want in (
        ("503, 503, then converts", [503, 503, 200], StatusCodes.OK),
        ("never answers", ["hang"], StatusCodes.SERVICE_UNAVAILABLE),
    ):
        requests = []

        async def serve(reader, writer, plan=plan, requests=requests):
            await reader.readuntil(b"\r\n\r\n")
            step = plan[min(len(requests), len(plan) - 1)]
            requests.append(step)
            if step == "hang":
                await asyncio.sleep(3600)
                return
            body = (
                json.dumps({"raw": RAW})
                if step == 200
                else json.dumps({"code": "SERVICE_UNAVAILABLE", "message": "starting"})
            ).encode()
            reason = "OK" if step == 200 else "Service Unavailable"
            writer.write(
                f"HTTP/1.1 {step} {reason}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            writer.close()

        api = await asyncio.start_server(serve, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{api.sockets[0].getsockname()[1]}/api/"
        loop = asyncio.get_running_loop()
        leaks = []
        loop.set_exception_handler(lambda _loop, ctx, leaks=leaks: leaks.append(ctx))
        ir_emitter.Remote = unfurled.Remote  # the real one, for this scenario only
        try:
            NET = Net(up_at=0.0)
            device, emitter = make(remote_address=url)
            status, took = await press(emitter)
            del emitter
            gc.collect()
            await asyncio.sleep(0.1)
        finally:
            ir_emitter.Remote = FakeRemote
            loop.set_exception_handler(None)
            api.close()
        unclosed = [
            c.get("message", "") for c in leaks if "nclosed" in c.get("message", "")
        ]
        report(
            f"W20 real unfurled Remote, the API {name}",
            f"{status_name(status)} after {took:.2f} s, sent {len(NET.sent)}, "
            f"requests {len(requests)}, unclosed sessions {len(unclosed)}",
            status == want
            and len(NET.sent) == (1 if want == StatusCodes.OK else 0)
            and not unclosed
            and took < 3.6,
        )

    # W14: two presses of the same code in a row on a wake (ucapi handles them one at a
    # time): the second waits for the first, then needs no conversion.
    NET = Net(up_at=1.0)
    device, emitter = make()
    device.forget_unanswered()
    first, took1 = await press(emitter)
    second, took2 = await press(emitter)
    report(
        "W14 two presses in a row on a wake, network back at 1.0 s",
        f"{status_name(first)} after {took1:.2f} s, then {status_name(second)} after "
        f"{took2:.2f} s, sent {len(NET.sent)}, conversions {NET.converts}",
        first == second == StatusCodes.OK and len(NET.sent) == 2 and took2 < 0.2,
    )

    # W6: a converted code is remembered: the second press needs no conversion, so it
    # works even while the conversion API is unreachable.
    NET = Net(up_at=0.0)
    device, emitter = make()
    first, _ = await press(emitter)
    NET.up_at = None  # conversion API unreachable from now on ...
    NET.silent = False
    sent_before = len(NET.sent)
    # ... but the Broadlink itself still answers: model that by letting sends through.
    real_up = NET.up
    NET.up = lambda: True  # type: ignore[method-assign]
    converts_before = NET.converts
    original_convert = FakeIR.convert

    async def unreachable_convert(self, code, **kw):
        NET.converts += 1
        raise unfurled.ConnectionError("Network is unreachable")

    FakeIR.convert = unreachable_convert
    second, took = await press(emitter)
    FakeIR.convert = original_convert
    NET.up = real_up  # type: ignore[method-assign]
    report(
        "W6 second press of the same code, conversion API unreachable",
        f"first {status_name(first)}, second {status_name(second)} after {took:.2f} s, "
        f"sent {len(NET.sent) - sent_before}, conversions {NET.converts - converts_before}",
        second == StatusCodes.OK
        and len(NET.sent) - sent_before == 1
        and NET.converts == converts_before,
    )

    # W7: the cache is bounded and drops the least recently used code.
    NET = Net(up_at=0.0)
    device, emitter = make()

    def code_n(i):
        return f"0000 006D 0002 0000 0157 00AC 0015 {i:04X}"

    for i in range(64):
        await press(emitter, code=code_n(i))
    await press(emitter, code=code_n(0))  # used again: now the most recent
    for i in range(64, 70):
        await press(emitter, code=code_n(i))
    size = len(getattr(emitter, "_converted", {}))
    before = NET.converts
    await press(emitter, code=code_n(0))
    kept_0 = NET.converts == before
    await press(emitter, code=code_n(1))
    dropped_1 = NET.converts == before + 1
    report(
        "W7 70 different codes, code 0 used again after the first 64",
        f"cache size {size}, sent {len(NET.sent)}, code 0 kept {kept_0}, "
        f"code 1 dropped {dropped_1}",
        size == getattr(ir_emitter, "CONVERTED_CODE_CACHE_SIZE", -1)
        and len(NET.sent) == 73
        and kept_0
        and dropped_1,
    )

    # W8: repeat 3 converts once and sends three times.
    NET = Net(up_at=0.0)
    device, emitter = make()
    status, _ = await press(emitter, repeat=3)
    report(
        "W8 repeat 3",
        f"{status_name(status)}, sent {len(NET.sent)}, conversions {NET.converts}",
        status == StatusCodes.OK and len(NET.sent) == 3 and NET.converts == 1,
    )

    # W9: no API key: the code is converted locally, as before.
    NET = Net(up_at=0.0)
    device, emitter = make(api_key=None)
    status, _ = await press(emitter)
    report(
        "W9 local conversion (no API key)",
        f"{status_name(status)}, sent {len(NET.sent)}, conversions {NET.converts}",
        status == StatusCodes.OK and len(NET.sent) == 1 and NET.converts == 0,
    )

    # W10: the flag only (unfurled 0.4.1 never wakes a Remote whose settings it hasn't
    # loaded, and the emitter never loads them).
    report(
        "W10 Remote(..., wake_if_asleep=False) passed",
        f"wake_if_asleep values used: {sorted(set(NET.wake_flags) | set(FLAGS_SEEN))}",
        all(flag is False for flag in FLAGS_SEEN),
    )

    # W11: send_command's own reconnect is unchanged (a raw code, not converted).
    for up_at in (0.6, None):
        NET = Net(up_at=up_at)
        device, emitter = make()
        start = NET.now()
        status = await device.send_command(code=b"\x26\x00")
        report(
            f"W11 send_command, network back at {up_at} s",
            f"{status_name(status)} after {NET.now() - start:.2f} s, sent {len(NET.sent)}",
            (status == StatusCodes.OK) == (up_at is not None),
        )

    failed = [name for name, _, ok in RESULTS if not ok]
    print(
        f"== {LABEL}: {len(RESULTS) - len(failed)}/{len(RESULTS)} as expected"
        + (f"; not as expected: {', '.join(failed)}" if failed else "")
    )
    print(f"== BL-8 code present: {bl8}")
    server.close()


FLAGS_SEEN: list[bool] = []
_init = FakeRemote.__init__


def _recording_init(self, *args, **kwargs):
    FLAGS_SEEN.append(kwargs.get("wake_if_asleep", True))
    _init(self, *args, **kwargs)


FakeRemote.__init__ = _recording_init  # type: ignore[method-assign]

if __name__ == "__main__":
    assert inspect.iscoroutinefunction(ir_emitter.BroadlinkIREmitter.command_handler)
    asyncio.run(main())
