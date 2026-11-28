"""
End-to-end check of BL-9's setup wizard: the real driver through the Remote's real core.

Every page the driver sends during a setup has to pass the Remote's core before a user
interface can show it, and the core parses a page more strictly than the published API
description says. On 2026-11-19 the device test of BL-9 failed that way: the wizard's
last page, "Remote API access", carried the wait setting as a number field with
"steps": 0.5, the core (0.83.2) reads steps as a whole number, dropped the whole page
(its log: Err(Error("invalid type: floating point `0.5`, expected i32", ...))) and the
web configurator showed "Setting up..." for ever. tests/bl9_setup_test.py had passed:
its page check followed the API description, and its stand-in for the core accepts any
page. This file is the check that was missing. It runs a tree's real ``driver.py``
against UC's core simulator, which runs the Remote's core on a PC (its version 0.83.2
refuses that page with the very error the Remote logged), along every path of the
wizard, and prints for each page whether the core handed it on.

Real: the core (its parser of setup pages, its checks of an answer, its setup sessions,
its entity subscriptions), ucapi and its websocket server, ucapi-framework with its
pauses, and the tree's ``driver.py`` ``main()``: setup flow, driver, device, entities,
log filter and configuration file. Fake, in the driver's process, are the network edges
only, and every address is a documentation address (192.0.2.0/24), so that no run can
reach a real device:

- python-broadlink: ``discover``, ``xdiscover``, ``hello`` and the device they return
  (``auth``, ``send_data``): one Broadlink, which a path can move to another address;
- unfurled in setup.py: ``discover_remotes`` (one Remote, or none) and ``Remote``, whose
  ``auth.generate_key`` makes a new key for the right PIN and fails like unfurled for a
  wrong one;
- unfurled in ir_emitter.py: ``Remote``, whose ``ir.convert`` converts a code for an API
  key the Remote knows (the saved one, until a new one replaces it) and fails like
  unfurled for any other.

The file refuses to start in a container with a network route (it must run in the
simulator's network namespace, and the simulator with ``--network none``), and it sets
UC_DISABLE_MDNS_PUBLISH. The only connections are on the loopback: the core's REST API
and the driver's websocket.

The wizard is driven through the core's REST API only, with what the web configurator
sends (read from the source of version 2.6.3, the one core 0.83.2 comes with): a setup
starts with ``POST /intg/setup``, a page is answered with ``PUT /intg/setup/<driver>``
and every input field of the page, each value as text: a list's selected item id, a
checkbox as "true" or "false", a text as it is, a PIN as "" unless one is typed; a
label gets no answer. Done and Cancel are ``DELETE /intg/setup/<driver>``.

Verdict per page. The driver's process records every message ucapi hands to the
websocket for the core, as it is on the wire, and every message it takes from the core
(the tap). When the driver has sent a page, the client reads the session's state from
the core:

- SHOWN: the core reports WAIT_USER_ACTION with that page, the whole page equal to what
  the driver sent (not only its title);
- REFUSED: the driver sent the page and the core's state stayed SETUP, with no page,
  for 5 s: what the web configurator shows as "Setting up...";
- ALTERED or LOST: the core shows another page, or the session is in another state.

``--crosscheck`` then compares each verdict with the core's own log, by time: the core
logs ``Ok(DriverSetupChange { ... SettingsPage { title: ... } })`` for a page it
parsed and ``Err(Error(...))`` for one it refused (the simulator needs RUST_LOG=info
for these lines).

The paths (each needs a simulator of its own: a core takes one first setup per driver;
P1 and P5 run the tree's own first setup, the others start from an integration a
stand-in driver set up, with a configuration file written for the path):

- P0: with the integration's entities configured on the core, a button press (an IR
  code through the IR emitter, converted through the saved Remote address and key)
  before and right after a saved Update and a Restore, and one while a Restore is
  applied. Both re-create the device inside the driver, which drops its entities: the
  core must subscribe to them again, or every press fails until the driver restarts;
- P1: the first setup of a new installation, with the PIN and a wait of 5 seconds;
- P2: Update with the PIN left empty and another wait: the key kept, the wait saved,
  and the wizard opened again selects the new value;
- P3: a wrong PIN (the page comes back with its error line and the chosen wait still
  selected), then the right one;
- P4: no Remote found: its address is asked for as text;
- P5: "Setup Manually": the Broadlink's address typed (left empty at first);
- P6: Backup; a Restore the framework can't read (nothing pasted, no JSON, JSON of
  another shape: its error page each time); a Restore with another wait, which the
  running device then uses and the wizard selects; a Restore with a wait between whole
  seconds (2.5): the device uses it as stored, the page selects the nearest whole
  second (3) and saving the page stores that;
- P7: Remove, the wizard with nothing configured (the framework's "---" entry, whose id
  is empty), then Add;
- P8: Reset and its restore prompt, answered once with a backup and once without;
- P9: answers the web configurator can't send but another client could (a wait that is
  no item of the list, no wait at all, a number in place of text, and others): what the
  core answers, and whether the driver ever sees them;
- P10: Update, Cancel on the last page, Update again, in one driver process: the
  Broadlink is listed once on "Discovered Devices" (discover.py used to add to a list
  it never cleared); then the Broadlink at a new address: listed once, and saved there;
- P11 (BL-11): "Configure selected device": the first page opens on it, its settings
  page saves the wait in place with no search and no key request, presses work before
  and right after it, and Update and Configure each show what the other saved.

Usage, from the repository root, with <out> an empty folder outside the repository and
<image> the test image (Python 3.11 with requirements.txt installed). One path, P2:
    docker run -d --rm --name sim --network none -e RUST_LOG=info \
        unfoldedcircle/core-simulator:0.83.2-bt
    docker run --rm --network container:sim -e PYTHONDONTWRITEBYTECODE=1 \
        -v "$PWD":/w:ro -v <out>:/out <image> \
        python -u /w/tests/bl9_setup_e2e.py /w P2 --out /out
    docker logs sim > <out>/core.log 2>&1; docker rm -f sim
    docker run --rm --network none -e PYTHONDONTWRITEBYTECODE=1 \
        -v "$PWD":/w:ro -v <out>:/out:ro <image> \
        python /w/tests/bl9_setup_e2e.py --crosscheck /out
(On Git Bash for Windows, prefix the docker commands with MSYS_NO_PATHCONV=1 and give
the folders as Windows paths: "$(pwd -W)", "$(cygpath -w <out>)".) The first argument,
/w here, is the tree to test: a folder with driver.json and intg-broadlink/; the test
file may come from another folder. No port is published and the simulator has no
network; the client shares its loopback. Run every path, P0 to P11, each with a new
simulator, on the core of the firmware the Remote runs and on the newest one, and
again for each new firmware: the page rules are the core's, and it can change them.

Each run prints one line per page and per expectation and a summary, and exits with 1
if anything is not as expected: on a tree whose "Remote API access" page the core
refuses, every path that reaches the page fails there. With ``--out`` it writes
transcript.txt (every REST call and answer, every message between the driver and the
core, every call of a fake, with times), results.json, driver.log (the driver's own
log, as the Remote would have it) and driver_tap.jsonl.

What it can't see: how a user interface draws a page. A page the core hands on can
still look wrong in the web configurator or on the Remote's screen.

:copyright: (c) 2026 by madalone.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

# pylint: disable=import-outside-toplevel,too-many-lines,too-many-instance-attributes
# pylint: disable=too-many-public-methods,too-few-public-methods,protected-access
# pylint: disable=too-many-arguments,too-many-locals,too-many-statements

import argparse
import base64
import datetime
import errno
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field


def require_offline() -> None:
    """Refuse to run with a network route (no packet is sent: only route lookups).

    As tests/bl9_window_checks.py does, for IPv4 and IPv6, and the kernel's IPv4
    routing table must be empty: with ``--network none`` there is only the loopback.
    """
    refusal = (
        "this process has a network route: run it in the network namespace of a "
        "core simulator that was started with --network none"
    )
    for family, address in (
        (socket.AF_INET, "192.0.2.1"),
        (socket.AF_INET6, "2001:db8::1"),
    ):
        try:
            probe = socket.socket(family, socket.SOCK_DGRAM)
        except OSError:
            continue  # the address family is not available at all
        try:
            probe.connect((address, 9))
            sys.exit(refusal)
        except OSError as err:
            if err.errno not in (errno.ENETUNREACH, errno.EADDRNOTAVAIL):
                sys.exit(f"unexpected routing error {err}")
        finally:
            probe.close()
    try:
        with open("/proc/net/route", encoding="ascii") as file:
            routes = file.read().splitlines()[1:]
    except OSError:
        routes = []
    if routes:
        sys.exit(refusal)


require_offline()
os.environ["UC_DISABLE_MDNS_PUBLISH"] = "true"  # ucapi: announce nothing
sys.dont_write_bytecode = True  # the tree may be mounted read-only

CORE = "http://127.0.0.1:8080/api"  # the simulator's REST API, on the shared loopback
CORE_LOGIN = b"web-configurator:1234"  # the simulator's fixed web-configurator PIN
DRIVER_PORT = 9090
# The environment variable naming the folder the driver's process shares with the client
WORK_ENV = "BL9_E2E_WORK"
TEST_NET = ipaddress.ip_network("192.0.2.0/24")  # documentation addresses (RFC 5737)

MAC = "a043b0112233"
NAME = "Broadlink RM4MINI"
IP = "192.0.2.124"
NEW_IP = "192.0.2.131"  # the same Broadlink after its address changed
REMOTE_NAME = "Remote3-0a1b2c._uc-remote._tcp.local."
REMOTE_HOST = "192.0.2.204"
REMOTE_URL = f"http://{REMOTE_HOST}:80/api/"
OTHER_URL = "http://192.0.2.205:80/api/"  # no Remote of the page's list
PIN = "8642"
WRONG_PIN = "9753"
SAVED_KEY = "saved-api-key"
RESTORED_KEY = "restored-api-key"
CODES = {"TV": {"power": "JgBQAAABKZIUEhQ3FBIU"}}
OTHER_CODES = {"Amplifier": {"mute": "JgBIAAABJ5QSFBI2EhQS"}}
PRONTO = "0000 006D 0002 0000 0157 00AC 0015 0689"
RAW = [9000, 4500, 560, 560, 560, 1690]

LAST_PAGE = "Remote API access"
WAIT_FIELD = "wake_wait_sec"
WAIT_ITEMS = [
    ("0", "0 seconds (no wait)"),
    ("1", "1 second"),
    ("2", "2 seconds"),
    ("3", "3 seconds"),
    ("4", "4 seconds (default)"),
    ("5", "5 seconds"),
    ("6", "6 seconds"),
]
PIN_LABEL = "Remote PIN"
KEEP_LABEL = "Remote PIN (leave empty to keep the current access)"

DRIVER_WAIT = 30.0  # how long the driver may take to answer an action
REFUSAL_WAIT = 5.0  # a page the core has not handed on this long after it was sent
END_WAIT = 5.0  # how long the core may take to report a setup's end
# cross-check: the core logs a message within milliseconds of the driver sending it,
# and two pages of a session are at least 0.5 s apart (ucapi waits that long before
# it hands an answer to the setup flow): seconds before and after a page
LOG_WINDOW = (0.1, 0.4)

for _address in (IP, NEW_IP, REMOTE_HOST, OTHER_URL.split("/")[2].split(":")[0]):
    if ipaddress.ip_address(_address) not in TEST_NET:
        sys.exit(f"{_address} is not a documentation address")


# --- the driver's process ------------------------------------------------------------


def run_driver(tree: str) -> None:
    """Run the tree's driver.py main() with every network edge fake (mode --driver).

    The fakes read the scene file at every call, so the client can change what is on
    the network while the driver runs. Every message ucapi hands to the websocket for
    the core or takes from it, and every call of a fake, goes to the tap file.
    """
    import asyncio

    work = os.environ[WORK_ENV]
    scene_file = os.path.join(work, "scene.json")
    tap_fd = os.open(
        os.path.join(work, "tap.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT
    )
    tap_lock = threading.Lock()
    os.chdir(tree)  # driver.py opens "driver.json" in the working directory
    sys.path.insert(0, os.path.join(tree, "intg-broadlink"))

    import broadlink
    import driver as tree_driver
    import ir_emitter
    import setup as tree_setup
    import ucapi.api as ucapi_api
    import unfurled
    from broadlink.exceptions import NetworkTimeoutError

    def tap(kind: str, **fields) -> None:
        """Append one record to the tap file (one write, also from a worker thread)."""
        try:
            line = json.dumps({"t": time.time(), "kind": kind, **fields}) + "\n"
            with tap_lock:
                os.write(tap_fd, line.encode())
        except (OSError, TypeError, ValueError) as err:
            print(f"TAP FAILED: {err}", file=sys.stderr, flush=True)

    def on_network() -> dict:
        """Return what is on the network now; only documentation addresses may be."""
        with open(scene_file, encoding="utf-8") as file:
            current = json.load(file)
        addresses = [spec["ip"] for spec in current["broadlinks"]]
        addresses += [spec["host"] for spec in current["remotes"]]
        for address in addresses:
            if ipaddress.ip_address(address) not in TEST_NET:
                raise SystemExit(f"{address} is not a documentation address")
        return current

    def no_answer() -> NetworkTimeoutError:
        return NetworkTimeoutError(-4000, "Network timeout", "No response received")

    class FakeBroadlink:
        """A python-broadlink device, as far as the driver and its setup use it."""

        def __init__(self, spec: dict):
            self.mac = bytes.fromhex(spec["mac"])
            self.host = (spec["ip"], 80)
            self.name = spec.get("name", "")
            self.type = spec.get("type", "RM4MINI")
            self.timeout = 10

        def __str__(self) -> str:
            mac = ":".join(f"{byte:02X}" for byte in self.mac)
            return (
                f"{self.name or 'Unknown'} "
                f"(Broadlink RM4 mini 0x5216 / {self.host[0]}:80 / {mac})"
            )

        def _there(self) -> None:
            """Fail like python-broadlink once nothing answers at this address."""
            if not any(
                spec["ip"] == self.host[0] for spec in on_network()["broadlinks"]
            ):
                raise no_answer()

        def auth(self) -> bool:
            self._there()
            tap("edge", edge="broadlink.auth", ip=self.host[0])
            return True

        def hello(self) -> bool:
            self._there()
            return True

        def send_data(self, data) -> None:
            self._there()
            tap("edge", edge="broadlink.send_data", ip=self.host[0], size=len(data))

    def fake_discover(timeout=10, **_kwargs):
        """Return the Broadlinks on the network, after listening a moment."""
        current = on_network()
        time.sleep(min(timeout, current.get("discover_sec", 0.2)))
        found = [FakeBroadlink(spec) for spec in current["broadlinks"]]
        tap("edge", edge="broadlink.discover", found=[d.host[0] for d in found])
        return found

    def fake_xdiscover(timeout=10, discover_ip_address="255.255.255.255", **_kwargs):
        """Yield the Broadlinks answering at the address (broadcast: all of them)."""
        found = [
            FakeBroadlink(spec)
            for spec in on_network()["broadlinks"]
            if discover_ip_address in ("255.255.255.255", spec["ip"])
        ]
        tap(
            "edge",
            edge="broadlink.xdiscover",
            asked=discover_ip_address,
            found=[d.host[0] for d in found],
        )
        yield from found

    def fake_hello(ip_address, port=80, timeout=10):
        """Return the Broadlink at the address, or time out like python-broadlink."""
        for spec in on_network()["broadlinks"]:
            if spec["ip"] == ip_address:
                tap("edge", edge="broadlink.hello", asked=ip_address, found=True)
                return FakeBroadlink(spec)
        tap("edge", edge="broadlink.hello", asked=ip_address, found=False)
        raise no_answer()

    async def fake_discover_remotes(timeout: float = 5.0):
        """Return the Remotes on the network, after listening a moment."""
        current = on_network()
        await asyncio.sleep(min(timeout, current.get("remote_search_sec", 0.3)))
        found = [
            unfurled.DiscoveredDevice(
                name=spec["name"], host=spec["host"], port=spec.get("port", 80)
            )
            for spec in current["remotes"]
        ]
        tap("edge", edge="unfurled.discover_remotes", found=[d.api_url for d in found])
        return found

    key_requests: list[str] = []
    issued: list[str] = []  # the keys made in this process

    def key_works(key) -> bool:
        """Return whether the Remote takes an API key: its newest, else a saved one.

        unfurled replaces the integration's key: once a new one is made, the one saved
        before no longer works.
        """
        return key == issued[-1] if issued else key in on_network().get("keys", [])

    class FakeAuth:
        """unfurled's ``Remote.auth``: only ``generate_key``."""

        def __init__(self, remote):
            self._remote = remote

        async def generate_key(self, name: str = "pyUnfoldedCircle") -> str:
            """Make a new key for the right PIN; fail like unfurled for a wrong one."""
            key_requests.append(self._remote.api_url)
            right = self._remote.pin == on_network()["pin"]
            key = f"e2e-key-{len(key_requests)}" if right else None
            tap(
                "edge",
                edge="unfurled.generate_key",
                api_url=self._remote.api_url,
                name=name,
                pin=self._remote.pin,
                key=key,
            )
            if not right:
                raise unfurled.AuthenticationError(
                    "Authentication failed (check API key / PIN)"
                )
            issued.append(key)
            return key

    class FakeIR:
        """unfurled's IR submodule: only ``convert``."""

        def __init__(self, remote):
            self._remote = remote

        async def convert(self, code, *, format=None, repeat=None):  # noqa: A002
            """Convert a code to raw timings; fail like unfurled for a key not known."""
            accepted = key_works(self._remote.api_key)
            tap(
                "edge",
                edge="unfurled.ir.convert",
                api_url=self._remote.api_url,
                api_key=self._remote.api_key,
                format=format,
                accepted=accepted,
            )
            if not accepted:
                raise unfurled.AuthenticationError(
                    "Authentication failed (check API key / PIN)"
                )
            return {"raw": list(RAW)}

    class FakeRemote:
        """unfurled's ``Remote``, as far as setup.py and ir_emitter.py use it."""

        def __init__(self, api_url, *, pin=None, api_key=None, **_kwargs):
            unfurled.Remote._normalize_url(api_url)  # unfurled's own address check
            self.api_url = api_url
            self.pin = pin
            self.api_key = api_key
            self.auth = FakeAuth(self)
            self.ir = FakeIR(self)

        async def close(self) -> None:
            return None

    send = ucapi_api.IntegrationAPI._enqueue_ws_payload
    receive = ucapi_api.IntegrationAPI._process_ws_message

    async def tapped_send(self, websocket, payload):
        """Tap a message for the core as it will be on the wire, then send it."""
        sent = (
            self._get_ws_context(websocket) is not None and websocket in self._clients
        )
        tap("to_core" if sent else "not_sent", payload=json.loads(json.dumps(payload)))
        return await send(self, websocket, payload)

    async def tapped_receive(self, websocket, data):
        """Tap a message from the core, then let ucapi handle it."""
        tap("from_core", payload=data)
        return await receive(self, websocket, data)

    broadlink.discover = fake_discover
    broadlink.xdiscover = fake_xdiscover
    broadlink.hello = fake_hello
    tree_setup.discover_remotes = fake_discover_remotes
    tree_setup.Remote = FakeRemote
    ir_emitter.Remote = FakeRemote
    ucapi_api.IntegrationAPI._enqueue_ws_payload = tapped_send
    ucapi_api.IntegrationAPI._process_ws_message = tapped_receive
    on_network()
    tap("start", tree=tree, pid=os.getpid())
    asyncio.run(tree_driver.main())


def run_stand_in(tree: str) -> None:
    """Run a driver that ends every setup with success at once (mode --stand-in).

    The core creates an integration when a driver's first setup ends with success, and
    only an integration can be configured again. A path that starts with the wizard's
    "Configuration mode" lets this stand-in answer that first setup, under the tree's
    own driver.json and on the same port, so its starting point doesn't depend on the
    tree under test.
    """
    import asyncio

    import ucapi

    async def serve() -> None:
        api = ucapi.IntegrationAPI(asyncio.get_running_loop())

        async def setup_handler(_msg):
            return ucapi.SetupComplete()

        async def connected():
            await api.set_device_state(ucapi.DeviceStates.CONNECTED)

        api.add_listener(ucapi.Events.CONNECT, connected)
        await api.init(os.path.join(tree, "driver.json"), setup_handler)
        await asyncio.Future()

    asyncio.run(serve())


# --- the client: what the web configurator does, through the core's REST API ---------


class Stop(Exception):
    """A path can't go on: a page was not shown, or the core or the driver failed."""


@dataclass
class Outcome:
    """What an action led to: a page, the setup's end, or nothing from the driver."""

    kind: str  # "page", "end" or "silent"
    title: str = ""
    verdict: str = ""  # a page: SHOWN, REFUSED, ALTERED or LOST
    page: dict = field(default_factory=dict)  # as the core shows it (else as sent)
    state: str = ""  # the core's setup state when the wait ended
    driver: str = ""  # an end: the state the driver sent, OK or ERROR
    mark: int = 0  # where the tap stood before the action

    def describe(self) -> str:
        """Return the outcome in words."""
        if self.kind == "page":
            return f'the page "{self.title}" ({self.verdict})'
        if self.kind == "end":
            return (
                f"the driver ended the setup with {self.driver}, "
                f"the core's state: {self.state}"
            )
        return f"nothing from the driver; the core's state: {self.state}"


@dataclass
class Press:
    """A button press through the core: what the core and the driver made of it."""

    note: str
    at: float
    status: int
    body: object
    took: float
    command: bool  # the driver got the entity command
    code: int | None  # what the driver answered
    sends: int  # IR sends by the fake Broadlink
    keys: list = field(default_factory=list)  # the API key of each conversion

    @property
    def works(self) -> bool:
        """Return whether the core answered 200 and the code went out."""
        return self.status == 200 and self.sends >= 1

    def describe(self) -> str:
        """Return the press in words."""
        driver = (
            f"the driver got the command and answered {self.code}"
            if self.command
            else "the driver got no command"
        )
        text = (
            f"the core answered HTTP {self.status} after {self.took:.2f} s; {driver}; "
            f"IR sends: {self.sends}"
        )
        if self.status != 200:
            text += f"; the core said: {json.dumps(self.body)[:200]}"
        return text


def stamp(epoch: float) -> str:
    """Return a time of day in UTC, to the millisecond (the core's log is in UTC)."""
    moment = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return moment.strftime("%H:%M:%S.%f")[:-3]


def port_open() -> bool:
    """Return whether something listens on the driver's port."""
    try:
        with socket.create_connection(("127.0.0.1", DRIVER_PORT), timeout=1):
            return True
    except OSError:
        return False


def wait_port(up: bool, seconds: float = 20.0) -> bool:
    """Wait until the driver's port is open (or closed); return whether it is."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if port_open() == up:
            return True
        time.sleep(0.1)
    return False


def entry(**changes) -> dict:
    """Return a saved Broadlink, as the driver writes one to config.json."""
    saved = {
        "identifier": MAC,
        "name": NAME,
        "address": IP,
        "data": CODES,
        "remote_address": REMOTE_URL,
        "remote_api_key": SAVED_KEY,
        "wake_wait_sec": 4.0,
    }
    saved.update(changes)
    return saved


def scene(**changes) -> dict:
    """Return what the fakes put on the network: one Broadlink, one Remote, its PIN."""
    network = {
        "broadlinks": [{"mac": MAC, "ip": IP, "name": "", "type": "RM4MINI"}],
        "remotes": [{"name": REMOTE_NAME, "host": REMOTE_HOST, "port": 80}],
        "pin": PIN,
        "keys": [SAVED_KEY],  # the API keys the Remote knows, until it makes a new one
    }
    network.update(changes)
    return network


def field_of(page: dict, field_id: str) -> tuple[str, dict]:
    """Return a setting's field type and content ("" and {}: the page has none)."""
    for setting in page.get("settings", []):
        if setting.get("id") == field_id:
            for kind, spec in setting.get("field", {}).items():
                return kind, spec if isinstance(spec, dict) else {}
    return "", {}


def label_of(page: dict, field_id: str) -> str:
    """Return a setting's English label ("" if the page has no such setting)."""
    for setting in page.get("settings", []):
        if setting.get("id") == field_id:
            return (setting.get("label") or {}).get("en", "")
    return ""


def items_of(page: dict, field_id: str) -> list[tuple[str, str]]:
    """Return a list field's items as (id, English label)."""
    _kind, spec = field_of(page, field_id)
    return [
        (item.get("id"), (item.get("label") or {}).get("en"))
        for item in spec.get("items", [])
    ]


def text_of(page: dict, field_id: str) -> str:
    """Return a label field's English text, or a text field's value."""
    _kind, spec = field_of(page, field_id)
    value = spec.get("value")
    return value.get("en", "") if isinstance(value, dict) else str(value or "")


def as_javascript(number) -> str:
    """Return a number the way JavaScript's String() writes it: 4.0 as "4"."""
    if isinstance(number, float) and number.is_integer():
        return str(int(number))
    return str(number)


def fill(page: dict, **changes) -> dict[str, str]:
    """Return the answer the web configurator sends for a page.

    Every input field is answered, each value as text: a list by its selected item's
    id (not at all if its value is none of its items), a checkbox by "true" or "false",
    a PIN by "" (its field always starts empty), a text as it is, a number as
    JavaScript writes it. A label gets no answer. ``changes`` are what the user picks
    or types; in a list only one of its items can be picked.
    """
    values: dict[str, str] = {}
    inputs = set()
    title = (page.get("title") or {}).get("en")
    for setting in page.get("settings", []):
        field_id = setting.get("id")
        kind, spec = field_of(page, field_id)
        if kind != "label":
            inputs.add(field_id)
        if kind == "dropdown":
            ids = [item.get("id") for item in spec.get("items", [])]
            if field_id in changes and changes[field_id] not in ids:
                raise Stop(
                    f'the page "{title}" has no choice {changes[field_id]!r} '
                    f"in its list {field_id}: {ids}"
                )
            if spec.get("value") in ids:
                values[field_id] = spec["value"]
        elif kind == "checkbox":
            values[field_id] = "true" if spec.get("value") else "false"
        elif kind == "password":
            values[field_id] = ""
        elif kind == "number":
            values[field_id] = as_javascript(spec.get("value") or 0)
        elif kind in ("text", "textarea"):
            values[field_id] = str(spec.get("value") or "")
    unknown = sorted(set(changes) - inputs)
    if unknown:
        raise Stop(f'the page "{title}" has no input field {", ".join(unknown)}')
    values.update(changes)
    return values


def differences(sent: dict, shown: dict) -> str:
    """Return where the page the core shows differs from the page the driver sent."""
    if sent.get("title") != shown.get("title"):
        return f"the title: sent {sent.get('title')}, shown {shown.get('title')}"
    ours, theirs = sent.get("settings", []), shown.get("settings", [])
    if len(ours) != len(theirs):
        return f"{len(ours)} settings sent, {len(theirs)} shown"
    for index, (one, other) in enumerate(zip(ours, theirs)):
        if one != other:
            return (
                f"setting {index + 1} ({one.get('id')}): sent {json.dumps(one)}, "
                f"shown {json.dumps(other)}"
            )
    return "nothing a comparison of the settings shows"


class Run:
    """One path against one fresh core: the processes, the REST calls, the record."""

    def __init__(self, tree: str, path: str, out: str | None):
        """Read the tree's driver.json and make the folder shared with the driver."""
        self.tree = os.path.abspath(tree)
        self.path = path
        self.out = out
        self.work = tempfile.mkdtemp(prefix="bl9e2e-")
        self.config_dir = os.path.join(self.work, "config")
        os.makedirs(self.config_dir)
        self.config_file = os.path.join(self.config_dir, "config.json")
        self.tap_file = os.path.join(self.work, "tap.jsonl")
        with open(os.path.join(self.tree, "driver.json"), encoding="utf-8") as file:
            self.meta = json.load(file)
        self.driver_id = self.meta["driver_id"]
        self.setup_url = f"/intg/setup/{self.driver_id}"
        self.journal: list[tuple[float, str, str]] = []
        self.checks: list[dict] = []
        self.pages: list[dict] = []
        self.ends: list[dict] = []
        self.facts: dict = {}
        self.tap: list[dict] = []
        self._tap_offset = 0
        self.log: list[tuple[float, str, str]] = []
        self.process: subprocess.Popen | None = None
        self.core: dict = {}
        self.intg = ""  # the core's integration for this driver
        self.stopped = ""
        self.session_open = False
        self.answered_at = 0.0  # when the last answer was sent
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.set_scene(scene())

    # -- the record

    def note(self, when: float, kind: str, text: str) -> None:
        """Keep a line for the transcript."""
        self.journal.append((when, kind, text))

    def say(self, text: str) -> None:
        """Print a line and keep it."""
        print(text, flush=True)
        self.note(time.time(), "STEP", text)

    def check(self, name: str, good: bool, got: str = "") -> bool:
        """Print and record an expectation's result."""
        line = f"{'ok  ' if good else 'FAIL'} {self.path} {name}"
        if got:
            line += f": {got}"
        print(line, flush=True)
        self.note(time.time(), "CHECK", line)
        self.checks.append({"name": name, "ok": bool(good), "got": got})
        return bool(good)

    # -- the driver's process and what it shares with the client

    def set_scene(self, network: dict) -> None:
        """Put the Broadlinks and Remotes of ``network`` on the fake network."""
        target = os.path.join(self.work, "scene.json")
        with open(target + ".new", "w", encoding="utf-8") as file:
            json.dump(network, file)
        os.replace(target + ".new", target)
        self.note(time.time(), "SCENE", json.dumps(network))

    def pump(self) -> None:
        """Read what the driver wrote to the tap file since the last time."""
        try:
            with open(self.tap_file, "rb") as file:
                file.seek(self._tap_offset)
                data = file.read()
        except FileNotFoundError:
            return
        end = data.rfind(b"\n") + 1
        self._tap_offset += end
        kinds = {
            "to_core": "DRV>",
            "from_core": "DRV<",
            "not_sent": "DRV!",
            "edge": "FAKE",
        }
        for line in data[:end].splitlines():
            record = json.loads(line)
            self.tap.append(record)
            if "payload" in record:
                text = json.dumps(record["payload"], ensure_ascii=False)
            else:
                rest = {k: v for k, v in record.items() if k not in ("t", "kind")}
                text = json.dumps(rest, ensure_ascii=False)
            self.note(record["t"], kinds.get(record["kind"], "PROC"), text)

    def mark(self) -> int:
        """Return where the tap stands now."""
        self.pump()
        return len(self.tap)

    def wait_tap(self, test, since: int, timeout: float) -> dict | None:
        """Return the first tap record from ``since`` on that passes ``test``."""
        deadline = time.monotonic() + timeout
        index = since
        while True:
            self.pump()
            while index < len(self.tap):
                if test(self.tap[index]):
                    return self.tap[index]
                index += 1
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)

    def from_core(self, message: str, since: int = 0) -> list[dict]:
        """Return the messages of one kind the core sent the driver, from a mark on."""
        self.pump()
        return [
            record
            for record in self.tap[since:]
            if record["kind"] == "from_core" and record["payload"].get("msg") == message
        ]

    def edges(self, edge: str, since: int = 0) -> list[dict]:
        """Return the calls of one fake network edge, from a mark on."""
        self.pump()
        return [
            record
            for record in self.tap[since:]
            if record["kind"] == "edge" and record.get("edge") == edge
        ]

    def logged(self, text: str, since: float = 0.0) -> list[float]:
        """Return when the driver logged lines containing ``text``, from a time on."""
        return [
            when
            for when, who, line in list(self.log)
            if who == "driver" and when >= since and text in line
        ]

    def _read_log(self, process: subprocess.Popen, who: str) -> None:
        """Keep a process's output, each line with its time (runs in a thread)."""
        for line in process.stdout:
            self.log.append((time.time(), who, line.rstrip("\n")))

    def _spawn(self, mode: str, who: str) -> subprocess.Popen:
        """Start this file in a driver mode and wait for its port."""
        env = dict(os.environ)
        env.update(
            UC_CONFIG_HOME=self.config_dir,
            UC_INTEGRATION_INTERFACE="127.0.0.1",
            UC_INTEGRATION_HTTP_PORT=str(DRIVER_PORT),
            PYTHONDONTWRITEBYTECODE="1",
        )
        env[WORK_ENV] = self.work
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, "-u", os.path.abspath(__file__), mode, self.tree],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        threading.Thread(
            target=self._read_log, args=(process, who), daemon=True
        ).start()
        if not wait_port(True):
            process.kill()
            raise Stop(f"the {who} did not open port {DRIVER_PORT}")
        self.note(time.time(), "PROC", f"the {who} is listening (pid {process.pid})")
        return process

    @staticmethod
    def _halt(process: subprocess.Popen) -> None:
        """Stop a driver process and wait until its port is closed."""
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(10)
        wait_port(False)

    def start_driver(self) -> None:
        """Start the tree's real driver."""
        self.process = self._spawn("--driver", "driver")

    def stop_driver(self) -> None:
        """Stop the tree's driver, if it runs."""
        if self.process is not None:
            self._halt(self.process)
            self.process = None
            self.note(time.time(), "PROC", "the driver is stopped")
        self.pump()

    def saved(self):
        """Return the configuration file's content (None: there is no file)."""
        try:
            with open(self.config_file, encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            return None

    def saved_entry(self) -> dict:
        """Return the one saved Broadlink ({} unless exactly one is saved)."""
        content = self.saved()
        if isinstance(content, list) and len(content) == 1:
            return content[0]
        return {}

    # -- the core's REST API

    def rest(self, method: str, path: str, body=None, quiet=False, timeout=30.0):
        """Call the core's REST API; return the status and the answer (JSON or text)."""
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(CORE + path, data=data, method=method)
        request.add_header(
            "Authorization", "Basic " + base64.b64encode(CORE_LOGIN).decode()
        )
        if data is not None:
            request.add_header("Content-Type", "application/json")
        started = time.time()
        try:
            with self._opener.open(request, timeout=timeout) as response:
                status, text = response.status, response.read().decode()
        except urllib.error.HTTPError as err:
            status, text = err.code, err.read().decode()
        except (urllib.error.URLError, OSError) as err:
            status, text = 0, f"{type(err).__name__}: {err}"
        try:
            answer = json.loads(text) if text else None
        except ValueError:
            answer = text
        if not quiet:
            sent = "" if body is None else " " + json.dumps(body, ensure_ascii=False)
            self.note(started, "REST>", f"{method} {path}{sent}")
            self.note(
                time.time(),
                "REST<",
                f"HTTP {status} {json.dumps(answer, ensure_ascii=False)}",
            )
        return status, answer

    def core_up(self) -> None:
        """Wait for the core; refuse one that already knows the driver."""
        deadline = time.monotonic() + 90
        while True:
            status, answer = self.rest("GET", "/pub/version", quiet=True)
            if status == 200:
                break
            if time.monotonic() > deadline:
                raise Stop(f"the core's REST API does not answer: {answer}")
            time.sleep(0.5)
        self.core = answer
        self.note(time.time(), "CORE", json.dumps(answer))
        status, _answer = self.rest("GET", f"/intg/drivers/{self.driver_id}")
        if status != 404:
            raise Stop(
                f"this core already knows {self.driver_id} (HTTP {status}): "
                "every path needs a simulator of its own"
            )

    def register(self) -> None:
        """Register the driver with the core, under the tree's own driver.json."""
        body = dict(
            self.meta,
            driver_type="EXTERNAL",
            driver_url=f"ws://127.0.0.1:{DRIVER_PORT}",
            enabled=True,
            device_discovery=False,
        )
        status, answer = self.rest("POST", "/intg/drivers", body)
        if status not in (200, 201):
            raise Stop(f"the core refused the driver: HTTP {status} {answer}")

    def setup_state(self, quiet: bool = True) -> tuple[str, dict]:
        """Return the core's state of the setup session, and its whole answer."""
        status, info = self.rest("GET", self.setup_url, quiet=quiet)
        if status != 200 or not isinstance(info, dict):
            return f"HTTP {status}", {}
        return info.get("state", ""), info

    def instance(self) -> dict:
        """Return the core's integration for this driver ({}: none)."""
        status, instances = self.rest("GET", "/intg/instances", quiet=True)
        if status == 200:
            for item in instances:
                if item.get("driver_id") == self.driver_id:
                    return item
        return {}

    def wait_connected(self, seconds: float = 30.0) -> None:
        """Wait until the core is connected to the driver."""
        deadline = time.monotonic() + seconds
        state = ""
        while time.monotonic() < deadline:
            state = self.instance().get("device_state", "")
            if state == "CONNECTED":
                return
            time.sleep(0.2)
        raise Stop(f"the core did not connect to the driver (its state: {state})")

    def seed(self, entries: list[dict]) -> None:
        """Start from an integration the core knows and a configuration on disk.

        The stand-in driver answers the first setup, so the core creates the
        integration; then the tree's real driver starts with ``entries`` as its
        configuration file, and the core connects to it.
        """
        stand_in = self._spawn("--stand-in", "stand-in")
        try:
            self.register()
            status, answer = self.rest(
                "POST",
                "/intg/setup",
                {"driver_id": self.driver_id, "reconfigure": False, "setup_data": {}},
            )
            if status not in (200, 201):
                raise Stop(
                    f"the stand-in's setup did not start: HTTP {status} {answer}"
                )
            deadline = time.monotonic() + 15
            while self.setup_state()[0] != "OK" or not self.instance():
                if time.monotonic() > deadline:
                    raise Stop("the stand-in's setup did not end with success")
                time.sleep(0.2)
            self.rest("DELETE", self.setup_url)
        finally:
            self._halt(stand_in)
        self.intg = self.instance()["integration_id"]
        with open(self.config_file, "w", encoding="utf-8") as file:
            json.dump(entries, file)
        self.start_driver()
        self.wait_connected()
        waits = ", ".join(f"{item.get(WAIT_FIELD)} s" for item in entries)
        self.say(
            f"{self.path} start: the core has the integration {self.intg} (set up by "
            f"the stand-in driver); the tree's driver runs with {len(entries)} saved "
            f"Broadlink (wait {waits}) and the core is connected to it"
        )

    def first(self) -> None:
        """Start as a new installation: the tree's driver, nothing saved."""
        self.start_driver()
        self.register()
        self.say(
            f"{self.path} start: the tree's driver runs with nothing saved and is "
            "registered with the core"
        )

    # -- a setup session, as the web configurator runs it

    def _settle(self, mark: int, action: str) -> Outcome:
        """Wait for the driver's answer to an action, and what the core makes of it."""

        def answered(record: dict) -> bool:
            payload = record.get("payload") or {}
            return (
                record["kind"] == "to_core"
                and payload.get("msg") == "driver_setup_change"
                and (payload.get("msg_data") or {}).get("state") != "SETUP"
            )

        record = self.wait_tap(answered, mark, DRIVER_WAIT)
        if record is None:
            state, _info = self.setup_state(quiet=False)
            self.say(
                f"{self.path} {action}: the driver sent nothing for "
                f"{DRIVER_WAIT:.0f} s; the core's state: {state}"
            )
            return Outcome("silent", state=state, mark=mark)
        data = record["payload"]["msg_data"]
        if data.get("state") != "WAIT_USER_ACTION":
            return self._ended(record, action, mark)
        sent = (data.get("require_user_action") or {}).get("input") or {}
        title = (sent.get("title") or {}).get("en", "")
        deadline = record["t"] + REFUSAL_WAIT
        seen: list[str] = []
        while True:
            state, info = self.setup_state()
            now = time.time()
            if not seen or seen[-1] != state:
                seen.append(state)
                self.note(now, "CORE", f"GET {self.setup_url}: state {state}")
            shown = (info.get("require_user_action") or {}).get("input") or {}
            if (state == "WAIT_USER_ACTION" and shown == sent) or now >= deadline:
                break
            time.sleep(0.1)
        after = now - record["t"]
        if state == "WAIT_USER_ACTION" and shown == sent:
            verdict = "SHOWN"
            detail = (
                "the core handed it on, the whole page as the driver sent it "
                f"({after:.2f} s after)"
            )
        elif state == "SETUP":
            verdict = "REFUSED"
            detail = (
                "the driver sent it; the core's setup state stayed SETUP for "
                f"{after:.1f} s and it shows no page"
            )
        elif state == "WAIT_USER_ACTION":
            verdict = "ALTERED"
            detail = f"the core shows another page: {differences(sent, shown)}"
        else:
            verdict = "LOST"
            detail = f"the driver sent it; the core's setup state is {state}"
        number = len(self.pages) + 1
        self.pages.append(
            {
                "n": number,
                "title": title,
                "action": action,
                "t_sent": record["t"],
                "verdict": verdict,
                "core_states": seen,
                "after_s": round(after, 3),
                "sent": sent,
                "shown": shown,
            }
        )
        self.check(
            f'page {number} "{title}" ({action}): {verdict}', verdict == "SHOWN", detail
        )
        self.session_open = True
        page = shown if verdict == "SHOWN" else sent
        return Outcome("page", title, verdict, page, state, mark=mark)

    def _ended(self, record: dict, action: str, mark: int) -> Outcome:
        """Wait for the core to report the end the driver sent."""
        data = record["payload"]["msg_data"]
        driver = data.get("state", "")
        deadline = record["t"] + END_WAIT
        while True:
            state, info = self.setup_state()
            if state == driver or time.time() >= deadline:
                break
            time.sleep(0.1)
        self.note(time.time(), "CORE", f"GET {self.setup_url}: {json.dumps(info)}")
        self.ends.append(
            {
                "action": action,
                "t_sent": record["t"],
                "driver": driver,
                "error": data.get("error"),
                "core_state": state,
            }
        )
        self.session_open = True
        return Outcome("end", state=state, driver=driver, mark=mark)

    def begin(self, reconfigure: bool, action: str) -> Outcome:
        """Start a setup session, as Next on the web configurator's first page does."""
        mark = self.mark()
        status, answer = self.rest(
            "POST",
            "/intg/setup",
            {"driver_id": self.driver_id, "reconfigure": reconfigure, "setup_data": {}},
        )
        if status not in (200, 201):
            raise Stop(f"{action}: the core started no setup: HTTP {status} {answer}")
        self.session_open = True
        return self._settle(mark, action)

    def answer(self, values: dict, action: str, meanwhile=None) -> Outcome:
        """Answer the page the session waits on, as Next in the web configurator does.

        ``meanwhile`` is called once the core has taken the answer, before the wait for
        what the driver makes of it.
        """
        mark = self.mark()
        self.answered_at = time.time()
        status, answer = self.rest("PUT", self.setup_url, {"input_values": values})
        if status != 200:
            raise Stop(f"{action}: the core refused the answer: HTTP {status} {answer}")
        if meanwhile is not None:
            meanwhile()
        return self._settle(mark, action)

    def end(self, button: str) -> None:
        """End the setup session, as the web configurator's Done or Cancel does."""
        status, answer = self.rest("DELETE", self.setup_url)
        self.session_open = False
        self.note(time.time(), "STEP", f"{self.path} {button}: HTTP {status} {answer}")
        time.sleep(0.3)
        self.pump()

    def need_page(self, outcome: Outcome, title: str) -> dict:
        """Return the page an action led to; stop the path unless it is shown."""
        if outcome.kind != "page" or outcome.title != title:
            self.check(f'the next page is "{title}"', False, outcome.describe())
            raise Stop(f'no page "{title}": {outcome.describe()}')
        if outcome.verdict != "SHOWN":
            raise Stop(f'the page "{title}" was not shown: {outcome.verdict}')
        return outcome.page

    def need_end(self, outcome: Outcome, action: str) -> None:
        """Check that an action ended the setup with success; stop the path if not."""
        good = (
            outcome.kind == "end" and outcome.driver == "OK" and outcome.state == "OK"
        )
        self.check(f"{action}: the setup ends with success", good, outcome.describe())
        if not good:
            raise Stop(f"{action}: {outcome.describe()}")

    def to_devices(self, action: str = "update") -> dict:
        """Open the wizard and pick an action: return "Discovered Devices"."""
        page = self.need_page(
            self.begin(True, "the wizard opened"), "Configuration mode"
        )
        return self.need_page(
            self.answer(fill(page, action=action), f"action {action}"),
            "Discovered Devices",
        )

    def to_last_page(self, action: str = "update") -> dict:
        """Open the wizard, pick an action and the Broadlink: return the last page."""
        page = self.to_devices(action)
        return self.need_page(
            self.answer(fill(page, choice=MAC), "the Broadlink picked"), LAST_PAGE
        )

    # -- what a path expects of a page, an answer and the saved configuration

    def check_listed_once(self, page: dict, address: str = IP) -> None:
        """Check "Discovered Devices": the Broadlink once, then "Setup Manually"."""
        items = items_of(page, "choice")
        expected = [(MAC, f"{NAME} ({address})"), ("manual", "Setup Manually")]
        self.check(
            f"page {len(self.pages)}: the Broadlink is listed once, at {address}",
            items == expected,
            f"{[label for _id, label in items]}",
        )

    def check_last_page(
        self,
        page: dict,
        wait: str,
        pin_label: str,
        remote: str = "list",
        error: str = "",
    ) -> None:
        """Check "Remote API access": the Remote, the PIN field, the wait's list."""
        where = f"page {len(self.pages)}"
        ids = [setting.get("id") for setting in page.get("settings", [])]
        expected = ["remote_address", "password", WAIT_FIELD, f"{WAIT_FIELD}_help"]
        if error:
            expected.insert(0, "authentication_error")
        self.check(f"{where}: its fields", ids == expected, f"{ids}")
        if error:
            self.check(
                f'{where}: its error line is "{error}"',
                label_of(page, "authentication_error") == error,
                f"{label_of(page, 'authentication_error')!r}: "
                f"{text_of(page, 'authentication_error')!r}",
            )
        kind, spec = field_of(page, "remote_address")
        if remote == "list":
            good = kind == "dropdown" and spec.get("value") == REMOTE_URL
            good = good and items_of(page, "remote_address") == [
                (REMOTE_URL, f"Remote3 ({REMOTE_HOST})")
            ]
            self.check(
                f"{where}: the Remote is a list, the Remote selected", good, f"{spec}"
            )
        else:
            good = kind == "text" and spec == {"value": REMOTE_URL}
            self.check(
                f"{where}: the Remote's address is a text field", good, f"{kind} {spec}"
            )
        kind, spec = field_of(page, "password")
        self.check(
            f'{where}: the PIN field is empty and labelled "{pin_label}"',
            kind == "password"
            and spec == {"value": ""}
            and label_of(page, "password") == pin_label,
            f"{kind} {spec}, {label_of(page, 'password')!r}",
        )
        kind, spec = field_of(page, WAIT_FIELD)
        labels = [label for _id, label in items_of(page, WAIT_FIELD)]
        self.check(
            f"{where}: the wait is a list of whole seconds, 0 to 6, 4 the default",
            kind == "dropdown" and items_of(page, WAIT_FIELD) == WAIT_ITEMS,
            f"{kind}: {labels or spec}",
        )
        self.check(
            f'{where}: the list selects "{dict(WAIT_ITEMS).get(wait)}"',
            kind == "dropdown" and spec.get("value") == wait,
            f"value {spec.get('value')!r}",
        )

    def check_answer(self, since: int, **values) -> None:
        """Check what the core handed the driver as the page's answer."""
        answers = self.from_core("set_driver_user_data", since)
        got = None
        if answers:
            got = answers[-1]["payload"].get("msg_data", {}).get("input_values")
        self.check(
            "the core hands the answer on as sent: "
            + ", ".join(f"{key} {value!r}" for key, value in values.items()),
            got == values,
            f"the driver got {got}",
        )

    def check_saved(self, note: str = "", **fields) -> None:
        """Check the one saved Broadlink's fields; the wait must be a float."""
        saved = self.saved_entry()
        wrong = {
            key: saved.get(key, "<missing>")
            for key, value in fields.items()
            if saved.get(key, "<missing>") != value
            or (key == WAIT_FIELD and not isinstance(saved.get(key), float))
        }
        text = ", ".join(f"{key} {value!r}" for key, value in fields.items())
        self.check(
            f"saved{' (' + note + ')' if note else ''}: {text}",
            bool(saved) and not wrong,
            (
                (f"differs: {wrong}" if wrong else "")
                if saved
                else f"the file: {self.saved()}"
            ),
        )

    # -- button presses (P0)

    def configure_entities(self) -> str:
        """Configure the driver's entities on the core; return the IR emitter's id."""
        url = f"/intg/instances/{self.intg}/entities"
        mark = self.mark()
        status, entities = self.rest("GET", f"{url}?reload=true&filter=ALL")
        if status != 200:
            raise Stop(f"the core lists no entities: HTTP {status} {entities}")
        ids = [item["entity_id"] for item in entities]
        status, done = self.rest("POST", url, ids)
        if status != 201:
            raise Stop(f"the core did not configure the entities: HTTP {status} {done}")
        subscribed = self.wait_tap(
            lambda r: r["kind"] == "from_core"
            and r["payload"].get("msg") == "subscribe_events",
            mark,
            10,
        )
        emitter = next((item for item in ids if item.startswith("ir_emitter.")), "")
        self.check(
            "the core configures the driver's entities and subscribes to them",
            subscribed is not None and bool(emitter),
            f"{ids}",
        )
        if not emitter:
            raise Stop("the driver has no IR emitter entity")
        time.sleep(1.0)
        return f"{self.intg}.{emitter}"

    def press(self, emitter: str, note: str) -> Press:
        """Send an IR code through the core, as a button of the Remote does."""
        mark = self.mark()
        started = time.time()
        status, body = self.rest(
            "PUT",
            f"/ir/emitters/{emitter}/send",
            {"code": PRONTO, "format": "PRONTO", "port_id": "main"},
            timeout=40,
        )
        took = time.time() - started
        time.sleep(0.15)
        commands = self.from_core("entity_command", mark)
        code = None
        if commands:
            wanted = commands[-1]["payload"].get("id")
            for record in self.tap[mark:]:
                payload = record.get("payload") or {}
                if record["kind"] == "to_core" and payload.get("req_id") == wanted:
                    code = payload.get("code")
        press = Press(
            note,
            started,
            status,
            body,
            took,
            bool(commands),
            code,
            len(self.edges("broadlink.send_data", mark)),
            [r.get("api_key") for r in self.edges("unfurled.ir.convert", mark)],
        )
        self.say(f"{self.path} press, {note}: {press.describe()}")
        return press

    # -- the end of a run

    def finish(self) -> int:
        """Stop the driver, write the record, print the summary; return the code."""
        try:
            if self.session_open:
                self.rest("DELETE", self.setup_url)
            self.stop_driver()
        except Exception as err:  # pylint: disable=broad-except
            self.note(time.time(), "STEP", f"cleaning up failed: {err}")
        shown = sum(1 for page in self.pages if page["verdict"] == "SHOWN")
        refused = [page["title"] for page in self.pages if page["verdict"] == "REFUSED"]
        other = [
            f'{page["title"]} ({page["verdict"]})'
            for page in self.pages
            if page["verdict"] not in ("SHOWN", "REFUSED")
        ]
        good = sum(1 for item in self.checks if item["ok"])
        summary = (
            f"== {self.path} on core {self.core.get('core', '?')}, driver "
            f"{self.meta.get('version')}: {good} of {len(self.checks)} as expected; "
            f"pages: {shown} SHOWN, {len(refused)} REFUSED"
        )
        if refused:
            summary += f" ({', '.join(sorted(set(refused)))})"
        if other:
            summary += f", others: {', '.join(other)}"
        if self.stopped:
            summary += f"; the path stopped: {self.stopped}"
        self.say(summary)
        if self.out:
            self._write()
        shutil.rmtree(self.work, ignore_errors=True)
        return 0 if self.checks and good == len(self.checks) and not self.stopped else 1

    def _write(self) -> None:
        """Write the transcript, the results, the driver's log and the tap."""
        os.makedirs(self.out, exist_ok=True)
        lines = [
            f"{stamp(when)} {kind:5} {text}"
            for when, kind, text in sorted(self.journal, key=lambda item: item[0])
        ]
        with open(
            os.path.join(self.out, "transcript.txt"), "w", encoding="utf-8"
        ) as file:
            file.write("\n".join(lines) + "\n")
        with open(os.path.join(self.out, "driver.log"), "w", encoding="utf-8") as file:
            for when, who, text in sorted(self.log, key=lambda item: item[0]):
                file.write(f"{stamp(when)} [{who}] {text}\n")
        results = {
            "path": self.path,
            "tree": self.tree,
            "driver": self.meta.get("version"),
            "core": self.core,
            "stopped": self.stopped,
            "checks": self.checks,
            "pages": self.pages,
            "ends": self.ends,
            "facts": self.facts,
            "saved": self.saved(),
        }
        with open(
            os.path.join(self.out, "results.json"), "w", encoding="utf-8"
        ) as file:
            json.dump(results, file, indent=1, ensure_ascii=False)
        if os.path.exists(self.tap_file):
            shutil.copy(self.tap_file, os.path.join(self.out, "driver_tap.jsonl"))


# --- the paths -----------------------------------------------------------------------


PRESS_FIRST = "the press first"
UI_FIRST = "the web configurator's reads and Done first"


def web_configurator_reads(run: Run) -> None:
    """Read what the web configurator reads when a setup has ended with success."""
    run.rest("GET", f"/intg/instances/{run.intg}")
    run.rest("GET", f"/intg/drivers/{run.driver_id}")
    run.rest("GET", f"/intg/instances/{run.intg}/entities?reload=true&filter=NEW")


def after_setup(run: Run, emitter: str, what: str, style: str, mark: int) -> dict:
    """Press right after a setup that re-created the device; find when presses work.

    ``style`` PRESS_FIRST: the press comes first, with nothing else done; UI_FIRST:
    first what the web configurator does when a setup ends (its reads, then Done). A
    press that fails is repeated for 10 s; then the remaining steps of the web
    configurator, a reconnect of the core, and a driver restart are tried, to find
    what makes presses work again.
    """
    ended = run.ends[-1]["t_sent"]
    record = {"what": what, "style": style, "presses": [], "works": ""}
    run.facts.setdefault("after_setup", []).append(record)

    def attempt(note: str) -> bool:
        press = run.press(emitter, f"{what} ({style}), {note}")
        record["presses"].append(
            {
                "note": note,
                "after_end_s": round(press.at - ended, 3),
                "status": press.status,
                "driver": press.code,
                "sends": press.sends,
                "keys": press.keys,
            }
        )
        if press.works and not record["works"]:
            record["works"] = f"{note} ({press.at - ended:.3f} s after the setup's end)"
        return press.works

    done = style == UI_FIRST
    if done:
        web_configurator_reads(run)
        run.end("Done")
    first = works = attempt("at once")
    deadline = time.monotonic() + 10
    while not works and time.monotonic() < deadline:
        time.sleep(0.5)
        works = attempt("nothing else done")
    if not works and not done:
        web_configurator_reads(run)
        works = attempt("after the web configurator's reads")
    if not done:
        run.end("Done")
        if not works:
            works = attempt("after Done")
    if not works:
        run.rest("PUT", f"/intg/instances/{run.intg}?cmd=DISCONNECT")
        time.sleep(1.0)
        run.rest("PUT", f"/intg/instances/{run.intg}?cmd=CONNECT")
        run.wait_connected()
        time.sleep(1.0)
        works = attempt(
            "after the core disconnected from the driver and connected again"
        )
    restarted = not works
    if restarted:
        run.stop_driver()
        run.start_driver()
        run.wait_connected()
        time.sleep(1.0)
        works = attempt("after a driver restart")
    time.sleep(0.3)  # let the reader of the driver's log catch up
    gone = run.logged("[configured] entity deleted: ir_emitter.", run.answered_at)
    back = run.logged("[configured] entity added: 'ir_emitter.", run.answered_at)
    record["emitter_gone_at"] = gone[0] if gone else None
    record["emitter_back_at"] = back[0] if back else None
    messages = [
        f'{r["payload"].get("msg")} {r["t"] - ended:+.3f} s'
        for r in run.tap[mark:]
        if r["kind"] == "from_core"
        and r["t"] >= ended
        and r["payload"].get("msg") != "entity_command"
    ]
    record["from_core"] = messages
    subscribed = [text for text in messages if text.startswith("subscribe_events")]
    run.say(
        f"{run.path} {what} ({style}): after the driver's OK the core sent the driver: "
        f"{', '.join(messages) or 'nothing'}"
    )
    if gone and back:
        run.say(
            f"{run.path} {what} ({style}): the driver's log has the IR emitter not a "
            f"configured entity for {back[0] - gone[0]:.3f} s, from {stamp(gone[0])} "
            f"({ended - gone[0]:.3f} s before its OK) to {stamp(back[0])}"
        )
    run.check(
        f"{what} ({style}): the core subscribes to the entities again",
        bool(subscribed),
        ", ".join(subscribed) or "no subscribe_events after the setup's end",
    )
    run.check(
        f"{what} ({style}): a press right after is answered 200 and sent",
        first,
        f"presses work {record['works'] or 'never'}",
    )
    if not first:
        run.check(
            f"{what} ({style}): presses work again without a driver restart",
            works and not restarted,
            f"presses work {record['works'] or 'never'}",
        )
    return record


def p0(run: Run) -> None:
    """P0: a button press before and right after a saved Update and a Restore."""
    run.seed([entry(wake_wait_sec=5.0)])
    emitter = run.configure_entities()
    before = run.press(emitter, "before any setup")
    run.check("a press before any setup is answered 200 and sent", before.works)
    waits = iter(("3", "2"))
    for style in (PRESS_FIRST, UI_FIRST):
        page = run.to_last_page()
        outcome = run.answer(
            fill(page, wake_wait_sec=next(waits)), "the PIN left empty"
        )
        run.need_end(outcome, f"Update ({style})")
        after_setup(run, emitter, "Update", style, outcome.mark)
    run.check_saved("the two Updates", remote_api_key=SAVED_KEY, wake_wait_sec=2.0)

    def restore_page() -> dict:
        page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
        return run.need_page(
            run.answer(fill(page, action="restore"), "action restore"),
            "Restore Configuration",
        )

    waits = iter((1.0, 6.0))
    for style in (PRESS_FIRST, UI_FIRST):
        page = restore_page()
        backup = json.dumps([entry(wake_wait_sec=next(waits), data=OTHER_CODES)])
        outcome = run.answer(fill(page, restore_data=backup), "a backup pasted")
        run.need_end(outcome, f"Restore ({style})")
        after_setup(run, emitter, "Restore", style, outcome.mark)
    run.check_saved("the two Restores", data=OTHER_CODES, wake_wait_sec=6.0)

    # For the record: a press while a Restore is applied. The framework waits 1 s
    # between re-creating the device and ending the setup, and the IR emitter is not a
    # configured entity until the core subscribes again.
    during: list[Press] = []

    def press_meanwhile() -> None:
        time.sleep(1.0)
        during.append(
            run.press(emitter, "1.0 s after Next, while a Restore is applied")
        )

    page = restore_page()
    backup = json.dumps([entry(wake_wait_sec=3.0)])
    outcome = run.answer(
        fill(page, restore_data=backup), "a backup pasted", press_meanwhile
    )
    run.need_end(outcome, "Restore (a press meanwhile)")
    record = after_setup(
        run, emitter, "Restore", "after a press meanwhile", outcome.mark
    )
    gone, back = record["emitter_gone_at"], record["emitter_back_at"]
    inside = bool(gone and back and gone <= during[0].at <= back)
    run.facts["press_while_restoring"] = {
        "at": during[0].at,
        "inside_the_gap": inside,
        "status": during[0].status,
        "body": during[0].body,
        "driver": during[0].code,
        "sends": during[0].sends,
    }
    run.say(
        f"{run.path} for the record: the press while the Restore was applied was sent "
        f"at {stamp(during[0].at)}, {'inside' if inside else 'NOT inside'} the time "
        f"the IR emitter was not configured: {during[0].describe()}"
    )


def p1(run: Run) -> None:
    """P1: the first setup of a new installation, with the PIN and a wait of 5 s."""
    run.first()
    page = run.need_page(run.begin(False, "the first setup"), "Restore Configuration?")
    page = run.need_page(run.answer(fill(page), "no restore"), "Discovered Devices")
    run.check_listed_once(page)
    page = run.need_page(
        run.answer(fill(page, choice=MAC), "the Broadlink picked"), LAST_PAGE
    )
    run.check_last_page(page, "4", PIN_LABEL)
    answer = fill(page, password=PIN, wake_wait_sec="5")
    outcome = run.answer(answer, "the PIN and 5 seconds")
    run.need_end(outcome, "the PIN and 5 seconds")
    run.check_answer(outcome.mark, **answer)
    run.check_saved(
        identifier=MAC,
        address=IP,
        data={},
        remote_address=REMOTE_URL,
        remote_api_key="e2e-key-1",
        wake_wait_sec=5.0,
    )
    intg = run.instance().get("integration_id", "")
    run.check("the core has created the integration", bool(intg), intg or "none")
    run.end("Done")


def p2(run: Run) -> None:
    """P2: Update with the PIN left empty and another wait, then the wizard again."""
    run.seed([entry(wake_wait_sec=5.0)])
    page = run.to_last_page()
    run.check_last_page(page, "5", KEEP_LABEL)
    answer = fill(page, wake_wait_sec="3")
    outcome = run.answer(answer, "the PIN left empty, 3 seconds")
    run.need_end(outcome, "the PIN left empty, 3 seconds")
    run.check_answer(outcome.mark, **answer)
    requests = run.edges("unfurled.generate_key")
    run.check("no new API key was asked for", not requests, f"{len(requests)} requests")
    run.check_saved(
        remote_api_key=SAVED_KEY,
        remote_address=REMOTE_URL,
        data=CODES,
        wake_wait_sec=3.0,
    )
    run.end("Done")
    page = run.to_last_page()
    run.check_last_page(page, "3", KEEP_LABEL)
    run.end("Cancel")
    run.check_saved("after the Cancel", remote_api_key=SAVED_KEY, wake_wait_sec=3.0)


def p3(run: Run) -> None:
    """P3: a wrong PIN, then the right one."""
    run.seed([entry()])
    page = run.to_last_page()
    page = run.need_page(
        run.answer(
            fill(page, password=WRONG_PIN, wake_wait_sec="2"), "a wrong PIN, 2 seconds"
        ),
        LAST_PAGE,
    )
    run.check_last_page(page, "2", KEEP_LABEL, error="Authentication failed")
    run.check_saved("after the wrong PIN", remote_api_key=SAVED_KEY, wake_wait_sec=4.0)
    answer = fill(page, password=PIN)
    outcome = run.answer(answer, "the right PIN")
    run.need_end(outcome, "the right PIN")
    run.check_answer(outcome.mark, **answer)
    run.check_saved(
        remote_api_key="e2e-key-2",
        remote_address=REMOTE_URL,
        data=CODES,
        wake_wait_sec=2.0,
    )
    run.end("Done")


def p4(run: Run) -> None:
    """P4: no Remote found, so its address is asked for as text."""
    run.set_scene(scene(remotes=[]))
    run.seed([entry()])
    page = run.to_last_page()
    run.check_last_page(page, "4", KEEP_LABEL, remote="text")
    answer = fill(page, password=PIN, wake_wait_sec="1")
    outcome = run.answer(answer, "the PIN and 1 second")
    run.need_end(outcome, "the PIN and 1 second")
    run.check_answer(outcome.mark, **answer)
    run.check_saved(
        remote_api_key="e2e-key-1",
        remote_address=REMOTE_URL,
        data=CODES,
        wake_wait_sec=1.0,
    )
    run.end("Done")


def p5(run: Run) -> None:
    """P5: "Setup Manually": the Broadlink's address typed, left empty at first."""
    run.first()
    page = run.need_page(run.begin(False, "the first setup"), "Restore Configuration?")
    page = run.need_page(run.answer(fill(page), "no restore"), "Discovered Devices")
    page = run.need_page(
        run.answer(fill(page, choice="manual"), "Setup Manually"), "Broadlink Setup"
    )
    kind, spec = field_of(page, "address")
    run.check(
        f"page {len(run.pages)}: the address is an empty text field",
        kind == "text" and spec == {"value": ""},
        f"{kind} {spec}",
    )
    outcome = run.answer(fill(page), "the address left empty")
    page = run.need_page(outcome, "Broadlink Setup")
    run.check_answer(outcome.mark, address="")
    page = run.need_page(
        run.answer(fill(page, address=IP), "the address typed"), LAST_PAGE
    )
    run.check_last_page(page, "4", PIN_LABEL)
    answer = fill(page, password=PIN, wake_wait_sec="0")
    outcome = run.answer(answer, "the PIN and 0 seconds")
    run.need_end(outcome, "the PIN and 0 seconds")
    run.check_answer(outcome.mark, **answer)
    run.check_saved(
        identifier=MAC, address=IP, remote_api_key="e2e-key-1", wake_wait_sec=0.0
    )
    run.end("Done")


def p6(run: Run) -> None:
    """P6: Backup, a Restore the framework can't read, a Restore with another wait."""
    run.seed([entry()])
    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    page = run.need_page(
        run.answer(fill(page, action="backup"), "action backup"), "Configuration Backup"
    )
    try:
        backup = json.loads(text_of(page, "backup_data"))
    except ValueError:
        backup = None
    run.check(
        f"page {len(run.pages)}: the backup is the saved configuration",
        backup == run.saved() == [entry()],
        f"{len(text_of(page, 'backup_data'))} characters",
    )
    run.need_end(run.answer(fill(page), "Next on the backup"), "Backup")
    run.end("Done")
    run.check_saved("after the Backup", remote_api_key=SAVED_KEY, wake_wait_sec=4.0)

    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    page = run.need_page(
        run.answer(fill(page, action="restore"), "action restore"),
        "Restore Configuration",
    )
    unreadable = [
        ("nothing pasted", "", "Please paste the configuration backup data."),
        ("no JSON pasted", "this is not a backup", "Invalid JSON format"),
        ("JSON of another shape pasted", '{"a": 1}', "Invalid configuration format"),
    ]
    for action, pasted, error in unreadable:
        outcome = run.answer(fill(page, restore_data=pasted), action)
        page = run.need_page(outcome, "Restore Configuration")
        run.check_answer(outcome.mark, restore_data=pasted)
        run.check(
            f'page {len(run.pages)}: its error line has "{error}", the text is kept',
            error in text_of(page, "error") and text_of(page, "restore_data") == pasted,
            f"{text_of(page, 'error')!r}",
        )
    run.check_saved("after the unreadable ones", data=CODES, wake_wait_sec=4.0)
    restored = entry(wake_wait_sec=2.0, remote_api_key=RESTORED_KEY, data=OTHER_CODES)
    outcome = run.answer(
        fill(page, restore_data=json.dumps([restored], indent=2)),
        "a backup with 2 seconds pasted",
    )
    run.need_end(outcome, "Restore")
    run.check_saved(remote_api_key=RESTORED_KEY, data=OTHER_CODES, wake_wait_sec=2.0)
    time.sleep(0.3)  # let the reader of the driver's log catch up
    applied = run.logged("Wait for the remote's Wi-Fi: up to 2.0s", run.answered_at)
    run.check(
        "the running device uses the restored wait",
        bool(applied),
        (
            "the driver logged: Wait for the remote's Wi-Fi: up to 2.0s after a wake"
            if applied
            else "no such log line"
        ),
    )
    run.end("Done")
    page = run.to_last_page()
    run.check_last_page(page, "2", KEEP_LABEL)
    run.end("Cancel")

    # A wait between whole seconds, as a configuration edited by hand can hold: the
    # device uses it as stored, the page selects the nearest whole second (a half
    # rounds up), and saving the page stores that.
    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    page = run.need_page(
        run.answer(fill(page, action="restore"), "action restore"),
        "Restore Configuration",
    )
    between = entry(wake_wait_sec=2.5, remote_api_key=RESTORED_KEY, data=OTHER_CODES)
    outcome = run.answer(
        fill(page, restore_data=json.dumps([between])),
        "a backup with 2.5 seconds pasted",
    )
    run.need_end(outcome, "Restore of 2.5 seconds")
    run.check_saved("2.5 seconds restored as they are", wake_wait_sec=2.5)
    time.sleep(0.3)  # let the reader of the driver's log catch up
    applied = run.logged("Wait for the remote's Wi-Fi: up to 2.5s", run.answered_at)
    run.check(
        "the running device uses the 2.5 seconds",
        bool(applied),
        (
            "the driver logged: Wait for the remote's Wi-Fi: up to 2.5s after a wake"
            if applied
            else "no such log line"
        ),
    )
    run.end("Done")
    page = run.to_last_page()
    run.check_last_page(page, "3", KEEP_LABEL)
    outcome = run.answer(fill(page), "the page as it is, the PIN left empty")
    run.need_end(outcome, "Update with the page as it is")
    run.check_saved(
        "the whole second the page showed",
        remote_api_key=RESTORED_KEY,
        wake_wait_sec=3.0,
    )
    run.end("Done")


def p7(run: Run) -> None:
    """P7: Remove, the wizard with nothing configured (the "---" entry), then Add."""
    run.seed([entry()])
    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    run.need_end(run.answer(fill(page, action="remove"), "action remove"), "Remove")
    run.check(
        "nothing is saved any more", run.saved() == [], f"the file: {run.saved()}"
    )
    run.end("Done")
    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    kind, spec = field_of(page, "choice")
    run.check(
        f'page {len(run.pages)}: the device list is the "---" entry, its id empty',
        kind == "dropdown"
        and spec == {"value": "", "items": [{"id": "", "label": {"en": "---"}}]},
        f"{spec}",
    )
    actions = [item_id for item_id, _label in items_of(page, "action")]
    run.check(
        f"page {len(run.pages)}: the actions are Add and Restore",
        actions == ["add", "restore"],
        f"{actions}",
    )
    answer = fill(page)
    outcome = run.answer(answer, 'action add, the "---" entry as it is')
    page = run.need_page(outcome, "Discovered Devices")
    run.check_answer(outcome.mark, **answer)
    run.check_listed_once(page)
    page = run.need_page(
        run.answer(fill(page, choice=MAC), "the Broadlink picked"), LAST_PAGE
    )
    run.check_last_page(page, "4", PIN_LABEL)
    answer = fill(page, password=PIN, wake_wait_sec="6")
    outcome = run.answer(answer, "the PIN and 6 seconds")
    run.need_end(outcome, "Add")
    run.check_answer(outcome.mark, **answer)
    run.check_saved(
        identifier=MAC,
        address=IP,
        data={},
        remote_api_key="e2e-key-1",
        wake_wait_sec=6.0,
    )
    run.end("Done")


def p8(run: Run) -> None:
    """P8: Reset and its restore prompt, answered with a backup and without one."""
    run.seed([entry(wake_wait_sec=5.0)])
    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    page = run.need_page(
        run.answer(fill(page, action="reset"), "action reset"), "Restore Configuration?"
    )
    run.check(
        "the configuration is gone", run.saved() is None, f"the file: {run.saved()}"
    )
    answer = fill(page, restore_from_backup="true")
    outcome = run.answer(answer, "restore from a backup")
    page = run.need_page(outcome, "Restore Configuration")
    run.check_answer(outcome.mark, **answer)
    backup = json.dumps([entry(wake_wait_sec=3.0)])
    run.need_end(
        run.answer(fill(page, restore_data=backup), "a backup with 3 seconds pasted"),
        "Reset, then a backup",
    )
    run.check_saved(remote_api_key=SAVED_KEY, data=CODES, wake_wait_sec=3.0)
    run.end("Done")

    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    page = run.need_page(
        run.answer(fill(page, action="reset"), "action reset"), "Restore Configuration?"
    )
    answer = fill(page)
    outcome = run.answer(answer, "no restore")
    page = run.need_page(outcome, "Discovered Devices")
    run.check_answer(outcome.mark, **answer)
    run.check_listed_once(page)
    page = run.need_page(
        run.answer(fill(page, choice=MAC), "the Broadlink picked"), LAST_PAGE
    )
    run.check_last_page(page, "4", PIN_LABEL)
    run.need_end(
        run.answer(
            fill(page, password=PIN, wake_wait_sec="2"), "the PIN and 2 seconds"
        ),
        "Reset, then a new setup",
    )
    run.check_saved(data={}, remote_api_key="e2e-key-1", wake_wait_sec=2.0)
    run.end("Done")


def bad_answer(run: Run, page: dict, name: str, values: dict, refused: bool):
    """Send an answer no browser sends; return the page if the session still shows it.

    ``refused``: the core is expected to answer HTTP 400 and to keep the answer from
    the driver. The other answers are sent for the record.
    """
    mark = run.mark()
    before = run.saved()
    status, answer = run.rest("PUT", run.setup_url, {"input_values": values})
    time.sleep(1.2)  # ucapi hands an answer to the setup flow after 0.5 s
    reached = run.from_core("set_driver_user_data", mark)
    record = {
        "name": name,
        "sent": values,
        "status": status,
        "answer": answer,
        "reached_driver": bool(reached),
    }
    run.facts.setdefault("bad_answers", []).append(record)
    if status == 200:
        outcome = run._settle(mark, f"bad answer: {name}")
        got = reached[-1]["payload"].get("msg_data") if reached else None
        text = f"the core took it (HTTP 200) and handed the driver {got}; then: "
        text += outcome.describe()
        if (
            outcome.kind != "page"
            or outcome.title != LAST_PAGE
            or outcome.verdict != "SHOWN"
        ):
            run.end("Done" if outcome.kind == "end" else "Cancel")
            page = None
        else:
            page = outcome.page
    else:
        state, info = run.setup_state()
        still = (info.get("require_user_action") or {}).get("input") == page
        text = (
            f"the core answered HTTP {status} {json.dumps(answer)}; the driver "
            f"{'got it all the same' if reached else 'never saw it'}; the session "
            f"{'still shows the page' if still else 'is in state ' + state}"
        )
        if not still:
            run.end("Cancel")
            page = None
    record["saved_after"] = run.saved()
    if run.saved() != before:
        text += f"; saved now: {run.saved()}"
    run.say(f"{run.path} bad answer, {name}: {text}")
    if refused:
        run.check(
            f"bad answer, {name}: the core answers HTTP 400, the driver never sees it",
            status == 400 and not reached and run.saved() == before,
            f"HTTP {status} {json.dumps(answer)}",
        )
    return page


def p9(run: Run) -> None:
    """P9: answers the web configurator can't send, but another client could."""
    run.seed([entry()])
    page = run.to_last_page()
    good = fill(page)
    cases = [
        ("a wait that is no item of the list", dict(good, wake_wait_sec="9"), True),
        ("no wait at all", {k: v for k, v in good.items() if k != WAIT_FIELD}, True),
        ("the wait as a number, not as text", dict(good, wake_wait_sec=4), True),
        ("no PIN at all", {k: v for k, v in good.items() if k != "password"}, False),
        (
            "a Remote that is no item of its list",
            dict(good, remote_address=OTHER_URL),
            False,
        ),
        (
            "a value for a field the page doesn't have",
            dict(good, drop_wait_sec="1"),
            False,
        ),
    ]
    for name, values, refused in cases:
        if page is None:
            page = run.to_last_page()
        page = bad_answer(run, page, name, values, refused)
    if page is None:
        page = run.to_last_page()
    outcome = run.answer(fill(page), "the page as it is, the PIN left empty")
    run.need_end(outcome, "a good answer after the bad ones")
    run.check_saved(remote_api_key=SAVED_KEY, data=CODES)
    run.end("Done")


def p10(run: Run) -> None:
    """P10: Update, Cancel on the last page, Update again: the Broadlink listed once."""
    run.seed([entry()])
    for which in ("first", "second"):
        page = run.to_devices()
        run.check_listed_once(page)
        mark = run.mark()
        run.answer(fill(page, choice=MAC), "the Broadlink picked")
        run.end("Cancel")
        aborted = run.from_core("abort_driver_setup", mark)
        run.check(
            f"the {which} Update: Cancel on the last page reaches the driver",
            len(aborted) == 1,
            f"{[record['payload'].get('msg_data') for record in aborted]}",
        )
    run.check_saved("after the two Cancels", address=IP, wake_wait_sec=4.0)
    moved = scene()
    moved["broadlinks"][0]["ip"] = NEW_IP
    run.set_scene(moved)
    run.say(f"{run.path} the Broadlink now answers at {NEW_IP}, no longer at {IP}")
    page = run.to_devices()
    run.check_listed_once(page, NEW_IP)
    page = run.need_page(
        run.answer(fill(page, choice=MAC), "the Broadlink picked"), LAST_PAGE
    )
    run.check_last_page(page, "4", KEEP_LABEL)
    run.need_end(run.answer(fill(page), "the PIN left empty"), "the third Update")
    run.check_saved(
        address=NEW_IP, remote_api_key=SAVED_KEY, data=CODES, wake_wait_sec=4.0
    )
    run.end("Done")


def p11(run: Run) -> None:
    """P11: BL-11's "Configure selected device": a press, Configure, a press, Update.

    The first page opens on Configure; Next as it is opens the settings page; 5 s saves
    in place with no device search, Remote search or key request; a press right after
    works; Update still shows and saves the setting; Configure shows what Update saved,
    and its Cancel saves nothing.
    """
    run.seed([entry()])
    emitter = run.configure_entities()
    before = run.press(emitter, "before Configure")
    run.check("a press before Configure is answered 200 and sent", before.works)
    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    actions = [item_id for item_id, _label in items_of(page, "action")]
    _kind, spec = field_of(page, "action")
    run.check(
        f"page {len(run.pages)}: the actions, Configure second and selected",
        actions
        == ["add", "configure", "update", "remove", "reset", "backup", "restore"]
        and spec.get("value") == "configure",
        f"{actions}, selected {spec.get('value')!r}",
    )
    mark = run.mark()
    answer = fill(page, choice=MAC)
    outcome = run.answer(answer, "Next with the action as shown")
    run.check_answer(outcome.mark, **answer)
    page = run.need_page(outcome, f"Configure {NAME}")
    items = [item_id for item_id, _label in items_of(page, WAIT_FIELD)]
    _kind, spec = field_of(page, WAIT_FIELD)
    run.check(
        f"page {len(run.pages)}: the wait list 0-6 s, the saved 4 s selected",
        items == [str(n) for n in range(7)] and spec.get("value") == "4",
        f"{items}, selected {spec.get('value')!r}",
    )
    answer = fill(page, wake_wait_sec="5")
    outcome = run.answer(answer, "5 seconds")
    run.need_end(outcome, "Configure, 5 seconds")
    run.check_answer(outcome.mark, **answer)
    searches = [
        record["edge"]
        for edge in (
            "broadlink.discover",
            "broadlink.hello",
            "unfurled.discover_remotes",
            "unfurled.generate_key",
        )
        for record in run.edges(edge, mark)
    ]
    run.check(
        "no device search, Remote search or key request", not searches, f"{searches}"
    )
    run.check_saved(
        address=IP,
        remote_api_key=SAVED_KEY,
        remote_address=REMOTE_URL,
        data=CODES,
        wake_wait_sec=5.0,
    )
    saved_line = run.logged(f"Saved for {NAME}: wait for Wi-Fi after a wake 5 s")
    run.check("the driver logged the save once", len(saved_line) == 1, f"{saved_line}")
    run.end("Done")
    after = run.press(emitter, "right after Configure")
    run.check("a press right after Configure is answered 200 and sent", after.works)
    page = run.to_last_page()
    run.check_last_page(page, "5", KEEP_LABEL)
    run.need_end(
        run.answer(fill(page, wake_wait_sec="3"), "Update, the PIN left empty, 3 s"),
        "Update after Configure",
    )
    run.check_saved(
        "Update after Configure", remote_api_key=SAVED_KEY, wake_wait_sec=3.0
    )
    run.end("Done")
    page = run.need_page(run.begin(True, "the wizard opened"), "Configuration mode")
    page = run.need_page(
        run.answer(fill(page, choice=MAC), "Next with the action as shown"),
        f"Configure {NAME}",
    )
    _kind, spec = field_of(page, WAIT_FIELD)
    run.check(
        f"page {len(run.pages)}: Configure shows what Update saved, 3 s",
        spec.get("value") == "3",
        f"selected {spec.get('value')!r}",
    )
    run.end("Cancel")
    run.check_saved("after Configure's Cancel", data=CODES, wake_wait_sec=3.0)


PATHS = {
    "P0": p0,
    "P1": p1,
    "P2": p2,
    "P3": p3,
    "P4": p4,
    "P5": p5,
    "P6": p6,
    "P7": p7,
    "P8": p8,
    "P9": p9,
    "P10": p10,
    "P11": p11,
}


def run_path(tree: str, path: str, out: str | None) -> int:
    """Run one path against the core this process shares the loopback with."""
    run = Run(tree, path, out)
    try:
        run.core_up()
        title = (PATHS[path].__doc__ or path).rstrip(".")
        run.say(
            f"== BL-9 setup wizard, end to end: {title}; tree {run.tree}, driver "
            f"{run.meta.get('version')}, core {run.core.get('core')} "
            f"(API {run.core.get('api')})"
        )
        PATHS[path](run)
    except Stop as stop:
        run.stopped = str(stop)
    except Exception as err:  # pylint: disable=broad-except
        run.stopped = f"{type(err).__name__}: {err}"
        run.note(time.time(), "STEP", traceback.format_exc())
        traceback.print_exc()
    return run.finish()


# --- the cross-check with the core's own log -----------------------------------------

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z)\s+(\w+)\s+(\S+): (.*)$")


def crosscheck(folder: str) -> int:
    """Compare each page's verdict with the core's own log lines, by time.

    ``folder`` holds results.json, as a path wrote it, and core.log, the simulator's
    log (``docker logs``, the simulator started with RUST_LOG=info). The core logs
    every setup message of the driver it handles: ``Ok(DriverSetupChange { ... })``
    with the page it parsed, or ``Err(Error(...))`` for one it can't parse.
    """
    with open(os.path.join(folder, "results.json"), encoding="utf-8") as file:
        results = json.load(file)
    lines = []
    with open(
        os.path.join(folder, "core.log"), encoding="utf-8", errors="replace"
    ) as file:
        for raw in file:
            match = LOG_LINE.match(ANSI.sub("", raw.rstrip("\n")))
            if match:
                when = datetime.datetime.fromisoformat(match.group(1)).timestamp()
                lines.append((when, match.group(2), match.group(3), match.group(4)))
    handled = [
        line
        for line in lines
        if line[2].endswith("driver_event_msg")
        and (line[3].startswith("Ok(DriverSetupChange") or line[3].startswith("Err("))
    ]
    print(
        f"== {results['path']} on core {results['core'].get('core')}: the client's "
        f"verdicts against the core's log ({len(lines)} lines, {len(handled)} setup "
        "messages from the driver)"
    )
    agree = 0
    used = set()
    for page in results["pages"]:
        sent = page["t_sent"]
        near = [
            line
            for line in handled
            if sent - LOG_WINDOW[0] <= line[0] <= sent + LOG_WINDOW[1]
            and "require_user_action: None" not in line[3]
        ]
        wanted = 'title: {"en": ' + json.dumps(page["title"]) + "}"
        accepted = [
            line for line in near if line[3].startswith("Ok(") and wanted in line[3]
        ]
        refused = [line for line in near if line[3].startswith("Err(")]
        if refused:
            core, line = "REFUSED", refused[0]
        elif accepted:
            core, line = "SHOWN", accepted[0]
        else:
            core, line = "NO LINE", None
        same = core == page["verdict"]
        agree += same
        text = "nothing in the window"
        if line is not None:
            used.add(line)
            text = f"{stamp(line[0])} ({line[0] - sent:+.3f} s) {line[3][:150]}"
        print(
            f'{"agree " if same else "DIFFER"} page {page["n"]} "{page["title"]}" '
            f'{page["verdict"]} (sent {stamp(sent)}) | core: {core}: {text}'
        )
    for end in results["ends"]:
        sent = end["t_sent"]
        wanted = f"event_type: Stop, state: {end['driver'].capitalize()}"
        near = [
            line
            for line in handled
            if sent - LOG_WINDOW[0] <= line[0] <= sent + LOG_WINDOW[1]
            and wanted in line[3]
        ]
        same = bool(near) and end["core_state"] == end["driver"]
        agree += same
        used.update(near[:1])
        text = f"{stamp(near[0][0])} {near[0][3][:110]}" if near else "nothing"
        print(
            f'{"agree " if same else "DIFFER"} end {end["driver"]} after '
            f'"{end["action"]}" (sent {stamp(sent)}), the core\'s state '
            f'{end["core_state"]} | core: {text}'
        )
    stray = [
        line for line in handled if line[3].startswith("Err(") and line not in used
    ]
    for line in stray:
        print(f"UNMATCHED refusal in the core's log: {stamp(line[0])} {line[3][:200]}")
    errors = [
        line
        for line in lines
        if line[1] == "ERROR" and line[2] == "remote_core::api::errors"
    ]
    total = len(results["pages"]) + len(results["ends"])
    print(
        f"== {agree} of {total} verdicts agree with the core's log; refusals in the "
        f"core's log that match no page of the run: {len(stray)}; ERROR lines of "
        f"remote_core::api::errors: {len(errors)}"
    )
    return 0 if agree == total and not stray else 1


def main() -> int:
    """Run the mode the arguments ask for."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument(
        "tree", nargs="?", help="folder with driver.json and intg-broadlink/"
    )
    parser.add_argument("path", nargs="?", help=f"one of {', '.join(PATHS)}")
    parser.add_argument("--out", help="folder for the transcript and the results")
    parser.add_argument("--crosscheck", metavar="FOLDER", help="compare with core.log")
    parser.add_argument("--driver", metavar="TREE", help=argparse.SUPPRESS)
    parser.add_argument("--stand-in", metavar="TREE", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.driver:
        run_driver(os.path.abspath(args.driver))
        return 0
    if args.stand_in:
        run_stand_in(os.path.abspath(args.stand_in))
        return 0
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    if args.crosscheck:
        return crosscheck(args.crosscheck)
    if not args.tree or args.path not in PATHS:
        parser.error(f"give a tree and one path of: {', '.join(PATHS)}")
    return run_path(args.tree, args.path, args.out)


if __name__ == "__main__":
    sys.exit(main())
