"""
Offline test for BL-9's setup part, and what the driver's log shows of its secrets.

The "Remote API access" page, Update, Backup and Restore, whether the Remote's core
takes every page the driver sends, whether the driver's log shows the PIN or the Remote
API key, and the retrying connect of the devices a setup creates.

Drives the real ``BroadlinkSetupFlow`` through ucapi-framework's real setup state machine,
and passes every setup message through ucapi's own message handling: the JSON message the
Remote sends (``setup_driver``, ``set_driver_user_data``, ``abort_driver_setup``) goes to
``IntegrationAPI._process_ws_message``, which ucapi's websocket reader hands each message
to once it has parsed it. There ucapi logs the message (its DEBUG message log), hands it to
the setup handler and answers the Remote; the test checks that answer against what the
handler returned. A page is answered the way the Remote answers it: each value under its
field's id, as text (for a list, the id of the item picked). The configuration is the
real ``BroadlinkConfigManager`` in a temporary folder, read back from disk after each
flow. Only the network edges, and one failure a case asks for, are fake:

- python-broadlink: ``xdiscover``, ``hello`` and ``discover`` find one Broadlink (at its
  address, or at a second one as after a DHCP change), and its ``auth`` succeeds;
- unfurled: ``discover_remotes`` finds the Remotes a case asks for (none: the page asks
  for the Remote's address); ``Remote(...)`` checks the address with unfurled's own code
  (a malformed one raises ValueError), and ``auth.generate_key`` makes a new key for the
  right PIN and fails like unfurled for a wrong one (or, when a case asks, with the
  right one after the old key was deleted);
- the driver's handler for a saved device fails when a case asks (as when the device
  can't be created).

R runs the real ``BroadlinkDriver`` and ``rm.Broadlink`` behind the setup flow instead,
with tests/bl8_harness.py's python-broadlink fakes (the Broadlink unreachable, then
answering); the Remote's other messages R needs (``subscribe_events``,
``unsubscribe_events``, ``enter_standby``, ``exit_standby``) go through ucapi's message
handling too.

Cases A-F and W cover the page and Update as BL-9 built them. G, H and K came with the
fixes after BL-9's review: an Update left unfinished (Cancel, a time-out, or no abort, as
after a lost connection) no longer derails the next Configure (G); after a key request
that may have deleted the saved key, no Remote address keeps that key with an empty PIN,
even the same Remote listed twice (H1-H3); the saved API key never sits in the pending
configuration, which the framework logs on an error (K1-K4). P came with the second fix
round, which removed the drop wait and named the PIN field "password": ucapi's message
log shows the PIN field's value hidden (P1), and no record of the driver's log, at any
level, shows a PIN typed on the page (P2). The third fix round, after the final review:

- the key guard lasts until a key request succeeds: neither a Cancel nor a new setup
  ends it, so the next Update asks for the PIN again (H4, H5);
- Configure -> Backup and Restore: ucapi hides only the values of key names it knows,
  and the configuration, the API key included, travels as one JSON text (the Backup
  page's text field, the Restore answer's restore_data), so the key reached the driver's
  log. driver.py now puts log_redact's filter on the log's handler: no driver-log record
  shows the key (K5-K7), nor any API key of the run (P3), while the Backup still has it;
- log_redact's patterns, its filter and install(), one by one (L);
- a Restore re-creates the running device: the restored wake wait applies at once, and
  the old device can't write its configuration back over the restored one (R1, R2).

The fourth fix round, after the review of the third: a device the framework creates
while the remote is awake keeps retrying until it connects (BL-1's retry; the framework
makes one attempt), and a retry left by another instance of the device no longer keeps
it from getting one. In each case the Broadlink can't be reached when the device is
created; the case checks the driver's retry for it, then lets the Broadlink answer: the
device must be connected, by its retry, within the retry's interval (the delay it logged
after its last failed attempt) plus ``CONNECT_SLACK``:

- Configure -> Restore: the old device's retry cancelled, the new device's own running
  (R1); the IR emitter the Remote subscribes to meanwhile is registered once the retry
  connects (R2);
- the integration's first setup restores a backup, nothing configured before, as after
  a reinstall (R3);
- Configure -> Reset cancels the device's retry, then its restore prompt restores the
  backup (R4); Configure -> Remove cancels the retry too (R5);
- an Update saved while the Broadlink can't be reached; it answered while the Update
  started, as setup.py's query_device needs (R6);
- a device created while the remote is in standby gets no retry until the next
  exit_standby starts one (R7);
- ucapi-framework drops a device on an unsubscribe without remove_device, leaving its
  retry waiting, and creates it again on a subscribe: the left-behind retry is
  cancelled, and the new device gets its own (R8).

The seventh fix round, after the device test of 2026-11-19, where the Remote's core
refused the "Remote API access" page (see "The Remote's core" below): the wait setting
is a list of whole seconds, "0" to "6", in place of a number field, and discover.py
empties its list before each search:

- the wait's list: its items, the one marked as the default, the one selected
  (F1-F9); a saved wait that is no item of the list (a hand edit, or 2.5 from the half
  seconds of the test build before) shows as the nearest item and stays as saved until
  the page is saved (F7-F9);
- every page the driver sent, in any case of the run: none the core's parser refuses
  (S1), none with anything else wrong, the framework's "---" entry aside (S2); the page
  check itself, on pages with a known verdict, the page refused on 2026-11-19 among
  them (S3); driver.json's static first page (S4); the answers sent past the core's
  answer checks on purpose (S5); no number field left on any page (S6);
- two setups with the one discovery object the driver keeps: the second "Discovered
  Devices" page lists the Broadlink once (V1), and after the Broadlink got another
  address, the Update saves that address (V2).

The Remote's core sits between the driver and the two user interfaces (the web
configurator and the Remote's own screen), and the test stands in for it with the core's
own rules, as core-simulator 0.83.2 (the core of firmware 2.12.1) and 0.85.3 apply them:

- a page: the core parses each page with the types of Unfolded Circle's api-model-rs
  (listed at FIELD_KEYS) and refuses the whole page for one value of the wrong type.
  The page then reaches no user interface, and the web configurator shows "Setting
  up..." for ever. On 2026-11-19 the wait setting was a number field with "steps": 0.5:
  the core takes "steps" as a whole number only, although the published API description
  says "number", which the page check of this test followed until then.
  ``page_problems`` now checks a page as ucapi sent it, by the core's types, and S
  checks every page of the run with it. One known difference, on the safe side: the
  core also takes a struct written as a JSON array in field order (a field as
  ``[value, regex]``, an item as ``[id, label]``), which the check refuses;
- an answer: the core refuses an answer that lacks a value for an input field of the
  page it answers (every field but a label), has a value that isn't text, has a list
  value that is none of the list's items, or a checkbox value other than "true" or
  "false"; the driver never sees it (``answer_problems``). ``Flow.send`` does the same.
  The answers that try the driver's own leniency with such input say so (``lenient``:
  B2's waits that no list sends, C2's empty wait, the answers without a wait of F7-F9),
  and S5 counts them, with what the stand-in core finds wrong with each.

The log: one capture keeps every record of the run, at every level, with every logger at
DEBUG. The driver's log is the part the driver's own logging setup makes: the tested
folder's driver.py runs, for real, its main() up to where it creates the driver, with
UC_LOG_LEVEL set to DEBUG (its default, and the most the driver logs). That part is
``logging.basicConfig()``, which leaves the root logger at WARNING and adds the handler
that writes the log; ``log_redact.install()``, which puts BL-9's filter on that handler;
and the levels set on the driver's own modules' loggers. ucapi sets its api module's
logger to DEBUG itself, so its message log is in the driver's log whatever UC_LOG_LEVEL
says. That handler stays quiet: for each record the capture keeps its text as logged
(what the log would show without the filter) and the line the handler writes for it,
after its filters. K1-K4 look for an API key in every record as logged. K5-K7 and P3
look for one in the driver's log, and need it there as logged, so they can fail. P2 looks
for a typed PIN, as a whole word, in the driver's log, and in every record once
filtered: ucapi-framework 1.9.8 logs each page's answer whole at DEBUG ("User data
response: ...", the PIN in clear whatever the field id) on its own logger, which the
driver leaves at WARNING. P2 and P3 fail when driver.py doesn't put log_redact's filter
on its handler.

ucapi-framework's fixed 1 s pauses in its setup module (a web-configurator workaround and
a wait for entity registration) and ucapi's 0.5 s pause before it hands a page's answer to
the setup handler (another web-configurator workaround) are skipped; nothing else in the
flow is changed.

Rounding of the wait setting, as BL-9 decides it: clamped to 0-6 s first, then to the
nearest whole second, a half rounding up (2.5 -> 3.0, 0.5 -> 1.0, 2.3 -> 2.0).

Usage (Python 3.11, the fork's requirements installed, no network):
    docker run --rm --network none -v "$PWD":/w:ro -w /w <image> python tests/bl9_setup_test.py <path> [label]
(<path>: the intg-broadlink folder to test, whose driver.py sets up the driver's log; R
loads bl8_harness.py from this file's folder, and S4 reads driver.json from the folder
above it.)

The code before BL-9: madalone.3's setup.py can't be tested with madalone.3's
config_manager.py, because the cases read the wait setting, which only BL-9's
BroadlinkConfig has (every case then raises AttributeError). So the baseline runs
madalone.3's setup.py in a copy of the current tree. From the repository root, with
<copy> an empty folder outside the repository:
    cp intg-broadlink/*.py <copy>/
    git show v1.3.0-madalone.3:intg-broadlink/setup.py > <copy>/setup.py
    docker run --rm --network none -v "$PWD":/w:ro -v <copy>:/before:ro -w /w <image> \
        python tests/bl9_setup_test.py /before "before BL-9"
(On Git Bash for Windows, prefix the docker command with MSYS_NO_PATHCONV=1 and give
both folders as Windows paths: "$(pwd -W)", "$(cygpath -w <copy>)".) On 2026-11-19,
with fix round 7, that gave 43 of 109 as expected, the current tree 109 of 109. Case W
there shows an Update wiping the learned codes, and P1 and P2 show madalone.3's PIN in
clear in ucapi's message log (its field was "remote_pin"). The 43 that hold: the three A
cases, because the copy has the current config_manager.py; B1's first line, because
madalone.3 refused an empty PIN too; B2 with no wait sent, where madalone.3 leaves the
default; K1, K2 and K4, because madalone.3 never copied the saved key and the copy's
config_manager.py keeps the key out of a repr; K5-K7, L1-L12, the twelve R lines and
P3, which test the copy's driver.py, log_redact.py and broadlink_driver.py (R6's Update
gives the PIN, which madalone.3 required); V1 and V2, which test the copy's discover.py
(V2 gives the PIN too); and S1-S4 and S6, because madalone.3's page has no wait setting
and so no number field: the core takes every page of that run. S5 fails there: 14 of
the test's answers don't fit madalone.3's pages, and the stand-in core refuses them.
With madalone.3's driver.py and broadlink_driver.py in the copy as well (``git show``
them the same way), 28 of 109 hold, the A, B1, B2, K1, K2, K4, L, V, S1-S4 and S6 lines
and R2's IR emitter line (the old device's own retry connects it, and the framework
registers its emitter): K5-K7 and P3 then find the Remote API key in lines of the
driver's log, P2 finds no filter on its handler; R1, R2 and R7 find the Restore kept the
old device (in R2 it then wrote its old configuration back over the restored one: one
learned code, wake 4.0); R3, R4, R6 and R8 find no retry for the new device; and R4 and
R5 find the removed device's retry left to end on its own.

The code before fix round 7, which the Remote refused: with the setup.py and discover.py
of the test build 1.3.0-madalone.4 in the copy (the wait as a number field with "steps":
0.5; no emptying of the list in discover()), 77 of 109 hold. S1 finds every "Remote API
access" page of the run, 90 of them, refused by the core's parser for its "steps", and S6
finds the number field on them; F1-F9, B3, E1's second line and G1-G3 miss the list, and
so do the lines of B1, D2 and D3 that look for the chosen wait on an error page; seven B2
lines and F9's second and third lines find a wait saved in half seconds; V1, V2 and S2
find the Broadlink listed twice after the second search, and the pick going to its old
address; S5 finds no list value among the answers sent past the stand-in core. The page
check this test had until then passed that page.

:copyright: (c) 2026 by madalone.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

# pylint: disable=import-error,wrong-import-position,protected-access,too-few-public-methods

import ast
import asyncio
import contextlib
import dataclasses
import importlib.util
import inspect
import io
import itertools
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import traceback
from collections import Counter
from typing import NamedTuple

sys.dont_write_bytecode = True  # the repository may be mounted read-only
SRC = sys.argv[1] if len(sys.argv) > 1 else "intg-broadlink"
LABEL = sys.argv[2] if len(sys.argv) > 2 else SRC
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)

import broadlink  # noqa: E402
import broadlink_driver  # noqa: E402
import discover  # noqa: E402
import ir_emitter  # noqa: E402
import rm  # noqa: E402
import setup as broadlink_setup  # noqa: E402
import ucapi.api as ucapi_api  # noqa: E402
import ucapi_framework.setup as framework_setup  # noqa: E402
import unfurled  # noqa: E402
from broadlink.exceptions import NetworkTimeoutError  # noqa: E402
from config_manager import BroadlinkConfig, BroadlinkConfigManager  # noqa: E402
from ucapi import (  # noqa: E402
    EntityTypes,
    IntegrationAPI,
    IntegrationSetupError,
    RequestUserInput,
    SetupComplete,
    SetupError,
    StatusCodes,
    UserDataResponse,
)
from ucapi_framework import create_entity_id  # noqa: E402

try:  # BL-9's third fix round: the filter driver.py puts on the log's handler
    LOG_REDACT = importlib.import_module("log_redact")
except ImportError:
    LOG_REDACT = None

MAC = "a043b0112233"
IP = "192.168.2.124"
NEW_IP = "192.168.2.131"  # the same Broadlink after a DHCP change
OTHER_IP, OTHER_MAC = "192.168.2.140", "b4430dddeeff"  # a second Broadlink
# P looks for every PIN typed on the page in the log, as a whole word: none as short as
# "0", and none a request id of this run (a number in ucapi's log) could reach
PIN = "8642"
WRONG_PIN = "9753"
PIN_ID = "password"  # the PIN field's id: ucapi hides a "password" value in its log
REDACTED = ucapi_api._REDACTED_VALUE  # what ucapi logs in place of a hidden value
OLD_KEY = "saved-api-key"
RESTORED_KEY = "restored-api-key"  # the key in a backup a case restores
KEY_NAME = "broadlink_integration"
REMOTE_A = unfurled.DiscoveredDevice(
    name="Remote3-c9087a._uc-remote._tcp.local.", host="192.168.2.204", port=80
)
REMOTE_B = unfurled.DiscoveredDevice(
    name="RemoteTwo-5f3e21._uc-remote._tcp.local.", host="192.168.2.205", port=80
)
ADDRESS_A = REMOTE_A.api_url  # http://192.168.2.204:80/api/
ADDRESS_B = REMOTE_B.api_url
CODES = {
    "TV": {"power": "JgBQAAABKZIUEhQ3FBIU", "volume_up": "JgBYAAABKJMUERQ2FBEU"},
    "Amplifier": {"mute": "JgBIAAABJ5QSFBI2EhQS"},
}
OLD_CODES = {"Projector": {"power": "JgBGAAABJ5QSFBI2EhQT"}}  # R: before the Restore
# a wait the page saved: a whole second, not the default, and none of the items a case
# that starts from it changes it to ("1", "3" or "5")
BL9_SAVED = {"wake_wait_sec": 2.0}
SAVED_WAIT = "2"  # its item in the page's list
WAKE_TITLE = "Wait for Wi-Fi after a wake"
# the wait setting's list on the "Remote API access" page, as fix round 7 decided it:
# whole seconds, the ids as text
WAIT_ITEMS = [
    {"id": "0", "label": {"en": "0 seconds (no wait)"}},
    {"id": "1", "label": {"en": "1 second"}},
    {"id": "2", "label": {"en": "2 seconds"}},
    {"id": "3", "label": {"en": "3 seconds"}},
    {"id": "4", "label": {"en": "4 seconds (default)"}},
    {"id": "5", "label": {"en": "5 seconds"}},
    {"id": "6", "label": {"en": "6 seconds"}},
]
EMITTER = create_entity_id(EntityTypes.IR_EMITTER, MAC)  # the IR emitter's entity id

# The types the Remote's core reads a setup page with: Unfolded Circle's api-model-rs,
# src/model/settings.rs, the same file at commit 336beb1 (uc_api 0.21.0, in core 0.83.2)
# and at b480cd5 (uc_api 0.21.1, in core 0.85.3). "map" is language code to text:
#   page      { title: map, settings: [setting] }
#   setting   { id: text, 1 to 50 characters; label: map; field: exactly one of: }
#   number    { value: i32 or f32; min, max: optional, i32 or f32; steps: optional i32;
#               decimals: optional u8; unit: optional map }
#   text, password { value, regex: optional text }    textarea { value: optional text }
#   checkbox  { value: true or false }                label { value: map }
#   dropdown  { value: optional text; items: [{ id: text, 1 to 50; label: map }] }
# The published API description (core-api's UCR-integration-asyncapi.yaml, and the copy
# each core serves) is wrong about "steps": it says "type: number", and the core takes a
# whole number only. A page with "steps": 0.5, or 1.0, is refused whole: it reaches no
# user interface, and the web configurator shows "Setting up..." for ever (the device
# test of 2026-11-19). page_problems follows the model, not that description. On
# 2026-11-19 its verdicts on 100 pages, S3's among them, were set against
# core-simulator 0.83.2 and 0.85.3: both refused every page it calls hard, and showed
# every other one.
FIELD_KEYS = {
    "number": {"value", "min", "max", "steps", "decimals", "unit"},
    "text": {"value", "regex"},
    "textarea": {"value"},
    "password": {"value", "regex"},
    "checkbox": {"value"},
    "dropdown": {"value", "items"},
    "label": {"value"},
}
I32_MIN, I32_MAX = -(2**31), 2**31 - 1
F32_MAX = 3.4028234663852886e38
ID_LENGTH = range(1, 51)  # the model's length rule for a setting's id and an item's


class FakeBroadlink:
    """A python-broadlink device, as far as the setup flow and discovery use it."""

    def __init__(self, ip_address: str):
        """Create the Broadlink answering at ``ip_address``."""
        self.mac = bytes.fromhex(OTHER_MAC if ip_address == OTHER_IP else MAC)
        self.host = (ip_address, 80)
        self.name = "RM4 mini"
        self.type = "RM4MINI"

    def __str__(self) -> str:
        """Return the device as python-broadlink shows it (setup.py logs it).

        Its default would show a memory address, random digits a PIN could turn up in.
        """
        mac = ":".join(format(byte, "02X") for byte in self.mac)
        return f"{self.name} ({self.type} / {self.host[0]}:{self.host[1]} / {mac})"

    def auth(self):
        """Authenticate."""
        return True

    def hello(self):
        """Answer a hello."""
        return True


# Where the Broadlink is. None: it answers at IP and at NEW_IP, whichever a case looks
# at, and a search of the network finds it at IP. V gives it one address at a time (a
# DHCP change between two searches): it then answers there only, and is found there.
BROADLINK_AT: list[str | None] = [None]


def answers_at(ip_address: str) -> bool:
    """Return whether a Broadlink answers at the address."""
    if ip_address == OTHER_IP:
        return True
    if BROADLINK_AT[0] is None:
        return ip_address in (IP, NEW_IP)
    return ip_address == BROADLINK_AT[0]


def fake_xdiscover(timeout=10, discover_ip_address="255.255.255.255", **_kwargs):
    """Yield the Broadlink if it answers at the address (python-broadlink's signature)."""
    if answers_at(discover_ip_address):
        yield FakeBroadlink(discover_ip_address)


def fake_hello(ip_address, port=80, timeout=10):
    """Return the Broadlink at the address, or time out like python-broadlink."""
    if answers_at(ip_address):
        return FakeBroadlink(ip_address)
    raise NetworkTimeoutError(-4000, "Network timeout", "No response received")


def fake_discover(timeout=10, **_kwargs):
    """Return the Broadlinks a search of the network finds: the one, where it is."""
    return [FakeBroadlink(BROADLINK_AT[0] or IP)]


DISCOVERED_REMOTES: list = []
KEY_REQUESTS: list[tuple[str, str, str]] = []
ISSUED_KEYS: set[str] = set()  # every new key of the run, for P3
OPEN_REMOTES: list = []
# unfurled replaces a key by listing, deleting, then creating it: True makes the next
# key request with the right PIN fail after the delete, as a lost connection would.
FAIL_AFTER_DELETE = [False]
# True makes the driver's handler for a saved device fail, as when the device can't be
# created: the framework then ends the setup with an error and logs the pending config.
FAIL_ADD_DEVICE = [False]


def fake_device_added(device):
    """Create the driver's device for a saved configuration; fail when a case asks."""
    if FAIL_ADD_DEVICE[0]:
        raise RuntimeError(f"Cannot create the device {device.identifier}")


async def fake_discover_remotes(timeout: float = 5.0):
    """Return the Remotes this case puts on the network."""
    return list(DISCOVERED_REMOTES)


class FakeAuth:
    """unfurled's ``Remote.auth``: only ``generate_key``."""

    def __init__(self, remote):
        """Belong to ``remote``."""
        self._remote = remote

    async def generate_key(self, name: str = "pyUnfoldedCircle") -> str:
        """Make a new key for the right PIN; fail like unfurled for a wrong one."""
        KEY_REQUESTS.append((self._remote.api_url, self._remote.pin, name))
        if self._remote.pin != PIN:
            raise unfurled.AuthenticationError(
                "Authentication failed (check API key / PIN)"
            )
        if FAIL_AFTER_DELETE[0]:
            FAIL_AFTER_DELETE[0] = False
            raise unfurled.ConnectionError(
                f"Request to {self._remote.api_url}auth/api_keys failed: "
                "Connection reset by peer"
            )
        key = f"new-key-{len(KEY_REQUESTS)}"
        ISSUED_KEYS.add(key)
        return key


class FakeRemote:
    """unfurled's ``Remote``, as far as the setup flow uses it."""

    def __init__(self, api_url, *, pin=None, api_key=None, **_kwargs):
        """Open a session to the Remote at ``api_url``.

        The address is checked with unfurled's own code, which raises ValueError for a
        malformed one (an unbalanced bracket, say) before sending anything.
        """
        unfurled.Remote._normalize_url(api_url)
        self.api_url = api_url
        self.pin = pin
        self.auth = FakeAuth(self)
        OPEN_REMOTES.append(self)

    async def close(self):
        """Close the session."""
        OPEN_REMOTES.remove(self)


class NoPauseAsyncio:
    """``asyncio`` without its pauses, for ucapi's api and ucapi-framework's setup."""

    def __getattr__(self, name):
        """Return everything else from the real asyncio."""
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(_delay, result=None):
        """Yield to the loop once instead of pausing."""
        return await asyncio.sleep(0, result)


broadlink.xdiscover = fake_xdiscover
broadlink.hello = fake_hello
broadlink.discover = fake_discover
broadlink_setup.discover_remotes = fake_discover_remotes
broadlink_setup.Remote = FakeRemote
framework_setup.asyncio = NoPauseAsyncio()
ucapi_api.asyncio = NoPauseAsyncio()


class RecordedPauses(NoPauseAsyncio):
    """``asyncio`` without its pauses for setup.py, which records each one (BL-11)."""

    pauses: list[float] = []

    async def sleep(self, delay, result=None):  # pylint: disable=invalid-overridden-method
        """Record the pause, then yield to the loop once."""
        self.pauses.append(delay)
        return await asyncio.sleep(0, result)


SETUP_ASYNCIO = RecordedPauses()
broadlink_setup.asyncio = SETUP_ASYNCIO


class FakeDriver:
    """What ucapi-framework's setup flow uses of the integration driver."""

    def __init__(self, config_manager):
        """Hold the configuration manager."""
        self.config_manager = config_manager
        self._device_class = None
        self._pending_setup_task = None

    def get_device_config(self, device_id: str):
        """Return a copy of the saved configuration, as the framework does with no device."""
        return self.config_manager.get(device_id)


class DriverCreated(Exception):
    """Raised where driver.py's main() creates the driver: its logging is set up then."""


def stop_at_driver(*_args, **_kwargs):
    """Stand in for the driver class in driver.py: stop main() where it creates one."""
    raise DriverCreated


async def run_driver_main(folder: str) -> str | None:
    """Run driver.py's main() up to where it creates the driver; return a problem, or None.

    Everything main() does before that is the driver's logging setup, run as it is
    written. UC_LOG_LEVEL is DEBUG meanwhile: its default, and the most the driver logs.
    """
    saved = os.environ.get("UC_LOG_LEVEL")
    os.environ["UC_LOG_LEVEL"] = "DEBUG"
    try:
        spec = importlib.util.spec_from_file_location(
            "bl9_driver_main", os.path.join(folder, "driver.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.BroadlinkDriver = stop_at_driver
        await asyncio.wait_for(module.main(), 10)
    except DriverCreated:
        return None
    except Exception as err:  # pylint: disable=broad-except
        return f"driver.py: {type(err).__name__}: {err}"
    finally:
        if saved is None:
            os.environ.pop("UC_LOG_LEVEL", None)
        else:
            os.environ["UC_LOG_LEVEL"] = saved
    return "driver.py's main() ended without creating the driver"


class Logged(NamedTuple):
    """A record the capture kept: its text as logged, and the driver's line for it."""

    record: logging.LogRecord
    text: str
    driver: str | None  # None: the driver's handler drops the record, or there is none


class LogCapture(logging.Handler):
    """Keep every log record, at every level, with its texts; print none."""

    def __init__(self, folder: str):
        """Model the driver's log with the logging setup of ``folder``'s driver.py."""
        super().__init__(logging.DEBUG)
        self.folder = folder
        self.entries: list[Logged] = []
        self.problem: str | None = "not set up yet"
        self.handlers: list[logging.Handler] = []  # the root handlers driver.py made
        self.handler_levels: list[int] = []  # their own levels, as driver.py left them
        self.root_level = logging.WARNING
        self.levels: dict[str, int] = {}  # the loggers driver.py set a level on
        self.raised: list[str] = []  # records the driver's handler raised on
        self._formatter = logging.Formatter("%(levelname)s %(name)s: %(message)s")
        self._saved: tuple = ()

    async def __aenter__(self):
        """Set up logging as driver.py does; then collect with every logger at DEBUG.

        The root logger starts as in a new process: no handler, WARNING. The handlers
        driver.py's setup adds stay quiet; their filters run on a copy of each record.
        """
        root = logging.getLogger()
        before = loggers()
        self._saved = (root.level, root.handlers[:], before)
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        root.setLevel(logging.WARNING)
        self.problem = await run_driver_main(self.folder)
        self.handlers = root.handlers[:]
        self.handler_levels = [handler.level for handler in self.handlers]
        self.root_level = root.level
        self.levels = {
            name: level
            for name, level in loggers().items()
            if level != before.get(name, logging.NOTSET)
        }
        for handler in self.handlers:
            handler.setLevel(logging.CRITICAL + 1)
        root.setLevel(logging.DEBUG)
        root.addHandler(self)
        return self

    async def __aexit__(self, *_exc):
        """Put back the root logger's handlers and every logger's level."""
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        level, handlers, before = self._saved
        root.setLevel(level)
        for handler in handlers:
            root.addHandler(handler)
        for name, logger in logging.Logger.manager.loggerDict.items():
            if isinstance(logger, logging.Logger):
                logger.setLevel(before.get(name, logging.NOTSET))

    def emit(self, record):
        """Keep the record: its text as logged, and the line the driver writes for it."""
        try:
            text = self._formatter.format(record)
        except Exception:  # pylint: disable=broad-except
            self.handleError(record)
            return
        try:
            line = self.driver_line(record)
        except Exception as err:  # pylint: disable=broad-except
            # in the driver it would raise into the code that logged the record
            line = f"(the driver's handler raised {type(err).__name__}: {err})"
            self.raised.append(text)
        self.entries.append(Logged(record, text, line))

    def driver_line(self, record: logging.LogRecord) -> str | None:
        """Return the line the driver's handler writes for ``record``, after its filters.

        The handler gets a copy: a filter may change the record (log_redact's does).
        None when the handler's own level or a filter drops it, or when driver.py made
        no handler.
        """
        lines = []
        for handler, level in zip(self.handlers, self.handler_levels):
            if record.levelno < level:
                continue
            copy = logging.makeLogRecord(record.__dict__)
            if handler.filter(copy):
                lines.append(handler.format(copy))
        return "\n".join(lines) if lines else None

    def texts(self, start: int = 0) -> list[str]:
        """Return the texts, as logged, of the records kept since ``start``."""
        return [entry.text for entry in self.entries[start:]]

    def in_driver_log(self, record: logging.LogRecord) -> bool:
        """Return whether the driver's logging setup makes ``record``.

        A logger makes a record at or above its effective level: the first level set on
        it or an ancestor (by driver.py, or by the library itself, as ucapi does), else
        the root logger's as driver.py leaves it.
        """
        logger = logging.getLogger(record.name)
        while logger.parent is not None:
            if logger.level:
                return record.levelno >= logger.level
            logger = logger.parent
        return record.levelno >= self.root_level

    def ready(self) -> bool:
        """Return whether driver.py's logging setup ran and made a handler to model."""
        return self.problem is None and bool(self.handlers)

    def redacting(self) -> bool:
        """Return whether every handler driver.py made has log_redact's filter."""
        kind = getattr(LOG_REDACT, "RedactSecrets", None)
        return (
            self.ready()
            and kind is not None
            and all(
                any(isinstance(item, kind) for item in handler.filters)
                for handler in self.handlers
            )
        )

    def describe(self) -> str:
        """Describe the driver's logging setup for a report line."""
        if self.problem is not None:
            return f"not applied ({self.problem})"
        filters = [
            [type(item).__name__ for item in handler.filters]
            for handler in self.handlers
        ]
        named = sorted(name for name, level in self.levels.items() if level)
        return (
            f"root {logging.getLevelName(self.root_level)}, handlers {len(self.handlers)} "
            f"with filters {filters}, levels set on {', '.join(named) or 'none'}"
        )


def loggers() -> dict[str, int]:
    """Return every logger's own level, by name (the root logger's not included)."""
    return {
        name: logger.level
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }


LOGS = LogCapture(SRC)
FOLDERS: list[str] = []
REQUEST_IDS = itertools.count(1)
# every page answer the driver got: (request id, the PIN field's id it used, the PIN)
ANSWERS: list[tuple[int, str | None, str]] = []
TYPED_PINS: set[str] = set()
CASE = ["(no case)"]  # the case function that is running, for the records below


class SentPage(NamedTuple):
    """A page the driver put on the wire: the case it was sent in, the page as sent."""

    case: str
    page: dict  # require_user_action.input, decoded again from ucapi's own JSON


class Unchecked(NamedTuple):
    """An answer a case sent past the core's answer checks, on purpose."""

    check: str  # the check that says so
    problems: list[str]  # what the core refuses it for


PAGES: list[SentPage] = []  # every page of the run, in the order sent, for S
UNCHECKED: list[Unchecked] = []  # for S5
# every answer the stand-in core refused: (the case, why); none is expected
REFUSED: list[tuple[str, list[str]]] = []


class NotSent:
    """Stands for a value a case leaves out of an answer, on purpose."""

    def __repr__(self) -> str:
        """Return what a check's name shows for it."""
        return "(not sent)"


NOT_SENT = NotSent()


class AnswerRefused:
    """What a case gets for an answer the core refuses: the driver never saw it."""

    def __init__(self, problems: list[str]):
        """Keep why the core refused the answer."""
        self.problems = problems


class RemoteConnection:
    """The Remote's websocket connection to the driver, as far as ucapi uses it here."""

    remote_address = ("127.0.0.1", 50000)


def remote_got(answer, sent: list[dict]) -> bool:
    """Return whether ucapi's last setup event to the Remote carries ``answer``."""
    events = [m.get("msg_data") for m in sent if m.get("msg") == "driver_setup_change"]
    last = events[-1] if events else None
    if isinstance(answer, RequestUserInput):
        try:
            page = json.loads(
                json.dumps({"title": answer.title, "settings": answer.settings})
            )
        except (TypeError, ValueError):  # not JSON: ucapi can't have sent it either
            return False
        return last == {
            "event_type": "SETUP",
            "state": "WAIT_USER_ACTION",
            "require_user_action": {"input": page},
        }
    if isinstance(answer, SetupComplete):
        return last == {"event_type": "STOP", "state": "OK"}
    if isinstance(answer, SetupError):
        return last == {
            "event_type": "STOP",
            "state": "ERROR",
            "error": str(answer.error_type),
        }
    return False


def answer_problems(page: dict | None, values: dict) -> list[str]:
    """Return why the Remote's core refuses ``values`` as the answer to ``page``.

    The core checks an answer before the driver sees it. It refuses one (HTTP 400 to
    the user interface, nothing to the driver, and the page stays) that

    - lacks a value for an input field of the page, which is every field but a label
      ("Setup data not provided for field: <id>");
    - has a value that isn't text ("invalid type: integer `5`, expected a string");
    - has, for a list, a value that is none of its items ("Invalid value specified in
      field '<id>': dropdown item '<value>' not found");
    - has, for a checkbox, anything but "true" or "false" ("Error parsing boolean
      value").

    It passes on a value for a label, and one for a field the page doesn't have. All
    of that as core-simulator 0.83.2 and 0.85.3 answered on 2026-11-19. The core also
    holds a number box's text to a number within min and max, and a text to its regex;
    no page of this driver has either (S6 for the number), and neither is checked here.
    ``page`` is the page the Remote shows, as the driver sent it; None when it shows
    none, and then there is nothing to answer.
    """
    if page is None:
        return ["no page is waiting for an answer"]
    problems = [
        f"a value that isn't text: {key!r} is {type(value).__name__}"
        for key, value in values.items()
        if not isinstance(value, str)
    ]
    for setting in page.get("settings", []):
        key, field = setting.get("id"), setting.get("field")
        if not isinstance(field, dict) or "label" in field:
            continue
        if key not in values:
            problems.append(f"a field left out: {key!r}")
        elif not isinstance(values[key], str):
            continue
        elif "dropdown" in field:
            ids = [item.get("id") for item in field["dropdown"].get("items", [])]
            if values[key] not in ids:
                problems.append(
                    f"a list value that is none of its items: {key!r} {values[key]!r}"
                )
        elif "checkbox" in field and values[key] not in ("true", "false"):
            problems.append(
                f"a checkbox value other than true or false: {key!r} {values[key]!r}"
            )
    return problems


def untouched(page: dict | None, key: str) -> str | None:
    """Return what the Remote sends for the field ``key`` of a page left as shown.

    Both user interfaces send every value as text: a list's selected item id, a number
    box's value ("4" for 4.0), a text field's text. ``page`` is the page as the driver
    sent it. None: the page has no such field, or its list selects none of its items
    (the web configurator then sends nothing for it, and the core refuses the answer).
    """
    for setting in (page or {}).get("settings", []):
        field = setting.get("field")
        if setting.get("id") != key or not isinstance(field, dict):
            continue
        if "dropdown" in field:
            value = field["dropdown"].get("value")
            ids = [item.get("id") for item in field["dropdown"].get("items", [])]
            return value if isinstance(value, str) and value in ids else None
        if "number" in field:
            value = field["number"].get("value")
            return f"{value:g}" if numeric(value) else None
        for kind in ("text", "password", "textarea"):
            if kind in field:
                return field[kind].get("value") or ""
    return None


class Flow:
    """A configuration folder and a setup session, ucapi between the Remote and it.

    The test is the Remote: its core, which reads every page the driver sends and
    checks every answer before the driver sees it, and the user behind it.
    """

    def __init__(
        self, saved=None, remotes=(REMOTE_A,), discovery=None, make_driver=None
    ):
        """Write ``saved`` as the driver saved it, then start the setup handler.

        ``make_driver`` makes the integration driver for the configuration folder (R);
        by default a stand-in, whose handler for a saved device fails when a case asks.
        """
        DISCOVERED_REMOTES[:] = remotes
        KEY_REQUESTS.clear()
        OPEN_REMOTES.clear()
        FAIL_AFTER_DELETE[0] = False
        FAIL_ADD_DEVICE[0] = False
        BROADLINK_AT[0] = None
        self.folder = tempfile.mkdtemp(prefix="bl9-setup-")
        FOLDERS.append(self.folder)
        if saved is not None:
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(saved, file)
        if make_driver is None:
            self.manager = BroadlinkConfigManager(
                self.folder, add_handler=fake_device_added, config_class=BroadlinkConfig
            )
            self.driver = FakeDriver(self.manager)
            self.api = IntegrationAPI(asyncio.get_running_loop())
        else:
            self.driver = make_driver(self.folder)
            self.manager = self.driver.config_manager
            self.api = self.driver.api
        self.handler = broadlink_setup.BroadlinkSetupFlow.create_handler(
            self.driver, discovery=discovery
        )
        # ucapi with the setup handler, as the driver starts it, and the Remote connected
        self.api._setup_handler = self._setup_handler
        self.remote = RemoteConnection()
        self.api._clients.add(self.remote)
        self.api._ws_contexts[self.remote] = ucapi_api._WsContext(
            incoming=asyncio.Queue(), outgoing=asyncio.Queue(), pending={}
        )
        self.page = None  # the last page the Remote was sent
        self.waiting = None  # the page the Remote shows now, as the driver sent it
        self._answer = None
        self._raised = None

    @property
    def path(self) -> str:
        """Return the configuration file's path."""
        return os.path.join(self.folder, "config.json")

    def pending(self) -> BroadlinkConfig | None:
        """Return the configuration the setup flow is working on, if any.

        The flow is the one the framework's handler made on its first message.
        """
        flow = inspect.getclosurevars(self.handler).nonlocals.get("setup_flow")
        return None if flow is None else flow._pending_device_config

    async def _setup_handler(self, msg):
        """Run the setup handler on what ucapi hands it; keep its answer or exception."""
        try:
            self._answer = await self.handler(msg)
        except Exception as err:
            self._raised = err
            raise
        return self._answer

    async def _from_remote(self, message: dict):
        """Pass a message from the Remote through ucapi's message handling.

        ucapi's websocket reader parses the Remote's JSON text and hands the message to
        ``_process_ws_message``, which logs it (DEBUG, sensitive values hidden) and
        dispatches it. Return the setup handler's answer, once ucapi has sent it on.
        """
        self._answer = self._raised = None
        if message["msg"] in ("setup_driver", "abort_driver_setup"):
            self.waiting = None  # a new setup, or the setup ended: the page is gone
        await self.api._process_ws_message(self.remote, json.loads(json.dumps(message)))
        if self._raised is not None:
            raise self._raised
        queue = self.api._ws_contexts[self.remote].outgoing
        sent = []
        while not queue.empty():
            sent.append(json.loads(queue.get_nowait()))
        self._core_reads(sent)
        if message["kind"] == "req" and not remote_got(self._answer, sent):
            raise AssertionError(
                f"ucapi sent the Remote {sent[-1:]} for {type(self._answer).__name__}"
            )
        if isinstance(self._answer, RequestUserInput):
            self.page = self._answer
        return self._answer

    def _core_reads(self, sent: list[dict]) -> None:
        """Take in what the driver sent, as the Remote's core does: keep every page.

        ``sent`` holds each message as ucapi put it on the wire, decoded again. A page
        is the ``input`` of a ``driver_setup_change``'s ``require_user_action``; it is
        kept for S as it is, with the case that is running. The Remote shows the last
        one until the driver ends the setup (event type STOP). A page the real core
        would refuse counts as shown here all the same: the cases run on, and S1 names
        every such page of the run.
        """
        for event in sent:
            data = event.get("msg_data")
            if event.get("msg") != "driver_setup_change" or not isinstance(data, dict):
                continue
            action = data.get("require_user_action")
            if isinstance(action, dict) and "input" in action:
                PAGES.append(SentPage(CASE[0], action["input"]))
                self.waiting = action["input"]
            elif data.get("event_type") == "STOP":
                self.waiting = None

    async def abort(self, error=IntegrationSetupError.OTHER):
        """End the setup unfinished, as the Remote does: OTHER on Cancel, TIMEOUT.

        (NONE is what the Remote sent on 2026-11-19 for the web configurator's Cancel;
        V sends that. setup.py treats every AbortDriverSetup alike.)
        """
        return await self._from_remote(
            {
                "kind": "event",
                "msg": "abort_driver_setup",
                "cat": "DEVICE",
                "msg_data": {"error": error.name},
            }
        )

    async def start(self, reconfigure: bool = True):
        """Start a setup: Configure (``reconfigure``), or the integration's first one."""
        return await self._from_remote(
            {
                "kind": "req",
                "id": next(REQUEST_IDS),
                "msg": "setup_driver",
                "msg_data": {"reconfigure": reconfigure, "setup_data": {}},
            }
        )

    async def send(
        self,
        values: dict | None = None,
        pin_id: str | None = None,
        lenient: str | None = None,
    ):
        """Send the Remote's next setup message: a new setup, or a page's values.

        The values answer the page the Remote shows, and its core checks them first
        (:func:`answer_problems`). An answer the core refuses never reaches the driver:
        the case gets an ``AnswerRefused``, and the page stays. ``lenient`` names the
        check that sends such an answer on purpose, to see what the driver itself makes
        of it: that answer is passed on, and S5 lists it.
        """
        if values is None:
            return await self.start()
        problems = answer_problems(self.waiting, values)
        if lenient is not None:
            UNCHECKED.append(Unchecked(lenient, problems))
        elif problems:
            REFUSED.append((CASE[0], problems))
            return AnswerRefused(problems)
        request_id = next(REQUEST_IDS)
        pin = str(values.get(pin_id, "")) if pin_id else ""
        ANSWERS.append((request_id, pin_id, pin))
        if pin:  # typed on the page, and now on its way to the driver
            TYPED_PINS.add(pin)
        return await self._from_remote(
            {
                "kind": "req",
                "id": request_id,
                "msg": "set_driver_user_data",
                "msg_data": {"input_values": values},
            }
        )

    async def answer(self, address, pin: str, lenient: str | None = None, **values):
        """Answer the "Remote API access" page the way the Remote does.

        Each value goes under its field's id, as text: the PIN under the id of the PIN
        field on the last page the Remote was sent. A PIN that isn't empty was typed on
        the page. The wait is the id of the list item a case picks; a case that gives
        none leaves the list as the page shows it (:func:`untouched`), and NOT_SENT
        leaves the value out of the answer. ``lenient``: see :meth:`send`.
        """
        key = pin_field(self.page).get("id", PIN_ID)
        answer = {"remote_address": address, key: pin}
        shown = untouched(self.waiting, "wake_wait_sec")
        if shown is not None:
            answer["wake_wait_sec"] = shown
        answer.update(values)
        return await self.send(
            {name: value for name, value in answer.items() if value is not NOT_SENT},
            pin_id=key,
            lenient=lenient,
        )

    async def open_page(self, action: str, address: str = IP):
        """Open the "Remote API access" page: a first setup, or Add or Update."""
        if action == "first":  # the integration's first setup: no "Configuration mode"
            await self.start(reconfigure=False)
            page = await self.send({"restore_from_backup": "false"})
        else:
            await self.send()  # the "Configuration mode" page
            # Add leaves the device list as shown: the configured Broadlink, or, with
            # nothing configured, the framework's "---" entry, whose id is empty
            shown = untouched(self.waiting, "choice")
            page = await self.send(
                {"choice": MAC if action == "update" else shown, "action": action}
            )
        if page.settings[0]["id"] == "choice":  # the framework discovered the Broadlink
            return await self.send({"choice": MAC})
        return await self.send({"address": address})

    def saved(self) -> BroadlinkConfig | None:
        """Return the Broadlink as saved on disk (a new manager reads config.json)."""
        return BroadlinkConfigManager(self.folder, config_class=BroadlinkConfig).get(
            MAC
        )

    def file_text(self) -> str:
        """Return the configuration file's text."""
        with open(self.path, encoding="utf-8") as file:
            return file.read()


def codes(data) -> int:
    """Return the number of learned codes in a configuration's ``data``."""
    if not isinstance(data, dict):
        return 0
    return sum(len(commands) for commands in data.values())


def is_access_page(result) -> bool:
    """Return whether ``result`` is the "Remote API access" page."""
    return isinstance(result, RequestUserInput) and result.title == {
        "en": "Remote API access"
    }


def fields(page) -> dict:
    """Return the page's settings by id."""
    if not isinstance(page, RequestUserInput):
        return {}
    return {setting.get("id"): setting for setting in page.settings}


def wait_list(page) -> dict | None:
    """Return the wait setting's list on the page (a dropdown field), if it has one."""
    spec = fields(page).get("wake_wait_sec", {}).get("field", {}).get("dropdown")
    return spec if isinstance(spec, dict) else None


def wait_shown(page) -> str | None:
    """Return the id of the item the page's wait list has selected, if any."""
    return (wait_list(page) or {}).get("value")


def number_fields(settings) -> list:
    """Return the ids of the number fields among a page's settings."""
    return [
        setting.get("id")
        for setting in settings
        if isinstance(setting, dict)
        and isinstance(setting.get("field"), dict)
        and "number" in setting["field"]
    ]


def marked(items, mark: str) -> list:
    """Return the ids of the list items whose English text has ``mark``."""
    found = []
    for item in items:
        label = item.get("label") if isinstance(item, dict) else None
        text = label.get("en") if isinstance(label, dict) else None
        if isinstance(text, str) and mark in text:
            found.append(item.get("id"))
    return found


def as_sent(page: RequestUserInput) -> dict:
    """Return a page as ucapi puts it on the wire, decoded again (see remote_got)."""
    return json.loads(json.dumps({"title": page.title, "settings": page.settings}))


def title_of(page) -> str:
    """Return the English title of a page as sent, for a report line."""
    title = page.get("title") if isinstance(page, dict) else None
    return str(title.get("en", "?")) if isinstance(title, dict) else "?"


def address_field(page) -> tuple[str | None, str | None, list]:
    """Return the Remote address field's kind, value and dropdown item ids."""
    field = fields(page).get("remote_address", {}).get("field", {})
    if "dropdown" in field:
        dropdown = field["dropdown"]
        return "dropdown", dropdown.get("value"), [i["id"] for i in dropdown["items"]]
    if "text" in field:
        return "text", field["text"].get("value"), []
    return None, None, []


def messages(page) -> list[str]:
    """Return the texts of the page's read-only fields that mention the PIN."""
    texts = []
    for setting in page.settings if isinstance(page, RequestUserInput) else []:
        label = setting.get("field", {}).get("label")
        text = label.get("value", {}).get("en", "") if label else ""
        if "PIN" in text:
            texts.append(text)
    return texts


def pin_field(page) -> dict:
    """Return the page's PIN field: its password field, which the Remote masks."""
    for setting in page.settings if isinstance(page, RequestUserInput) else []:
        if "password" in setting.get("field", {}):
            return setting
    return {}


def pin_label(page) -> str:
    """Return the PIN field's label."""
    return pin_field(page).get("label", {}).get("en", "")


def page_name(page) -> str:
    """Return a page's title, or what the flow answered instead of a page."""
    if isinstance(page, RequestUserInput):
        return page.title.get("en", "?")
    return type(page).__name__


def is_configuration_mode(page) -> bool:
    """Return whether ``page`` is the framework's "Configuration mode" page."""
    return isinstance(page, RequestUserInput) and {"choice", "action"} <= set(
        fields(page)
    )


def is_broadlink_page(page) -> bool:
    """Return whether ``page`` asks for the Broadlink (its address), as setups start."""
    return (
        isinstance(page, RequestUserInput)
        and "address" in fields(page)
        and not is_access_page(page)
    )


def language_problems(value, at: str, hard: list, strict: list) -> None:
    """Check a language map: an object of language code to text."""
    if not isinstance(value, dict):
        hard.append(f"{at}: {type(value).__name__} {value!r}, not a language map")
        return
    for key, text in value.items():
        if not isinstance(text, str):
            hard.append(f"{at}: the text for {key!r} is {type(text).__name__}")
    if "en" not in value:
        strict.append(f"{at}: no English text")


def surrogate_problems(value, at: str, hard: list) -> None:
    """Note every text under ``value``, keys included, that holds a lone surrogate.

    Python's json writes one as an escape that stands for no character, and the core's
    parser refuses the whole message for it ("lone leading surrogate in hex escape").
    """
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            hard.append(f"{at}: a text with a lone surrogate, which is not JSON")
    elif isinstance(value, dict):
        for key, item in value.items():
            surrogate_problems(key, f"{at}: the key {key!r}", hard)
            surrogate_problems(item, f"{at}[{key!r}]", hard)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            surrogate_problems(item, f"{at}[{index}]", hard)


def unknown_keys(found: dict, known: set, at: str, strict: list) -> None:
    """Note the keys of ``found`` the core's type doesn't have: the core drops them."""
    extra = sorted(set(found) - known, key=str)
    if extra:
        strict.append(f"{at}: unknown keys {extra} (the core drops them)")


def text_problems(spec: dict, key: str, at: str, hard: list) -> None:
    """Check an optional text: missing, null or text."""
    if spec.get(key) is not None and not isinstance(spec[key], str):
        hard.append(f"{at}.{key}: {type(spec[key]).__name__} {spec[key]!r}, not text")


def whole(value) -> bool:
    """Return whether ``value`` is a JSON integer (true and false are not)."""
    return isinstance(value, int) and not isinstance(value, bool)


def numeric(value) -> bool:
    """Return whether ``value`` is a JSON number: an integer or a float, not a bool."""
    return whole(value) or isinstance(value, float)


def value_problems(value, at: str, hard: list, strict: list) -> None:
    """Check an "i32 or f32": a JSON integer or float; not true or false, not text.

    The parser takes an integer beyond 32 bits and a float beyond the largest f32 too:
    the core passes the first on as a float and the second as null. An integer no
    float can hold (10**400) it refuses, with the whole message ("number out of
    range").
    """
    if not numeric(value):
        hard.append(f"{at}: {type(value).__name__} {value!r}, not a number")
    elif whole(value):
        if I32_MIN <= value <= I32_MAX:
            return
        try:
            float(value)
        except OverflowError:
            hard.append(
                f"{at}: a whole number of {value.bit_length()} bits is beyond a float; "
                "the core's parser refuses the message"
            )
        else:
            strict.append(f"{at}: {value} is beyond 32 bits (the core: a float)")
    elif not math.isfinite(value):
        hard.append(f"{at}: {value!r} is not JSON")
    elif abs(value) > F32_MAX:
        strict.append(f"{at}: {value!r} is beyond a 32-bit float (the core: null)")


def number_problems(spec: dict, at: str, hard: list, strict: list) -> None:
    """Check a number field's properties."""
    value, steps, decimals = (spec.get(key) for key in ("value", "steps", "decimals"))
    if value is None:
        hard.append(f"{at}: no value")
    else:
        value_problems(value, f"{at}.value", hard, strict)
    for key in ("min", "max"):
        if spec.get(key) is not None:
            value_problems(spec[key], f"{at}.{key}", hard, strict)
    if steps is not None:
        if not whole(steps) or not I32_MIN <= steps <= I32_MAX:
            hard.append(
                f"{at}.steps: {type(steps).__name__} {steps!r}; the core takes a "
                "whole number only (i32), and refuses 1.0 like 0.5"
            )
        elif steps < 1:
            strict.append(f"{at}.steps: {steps} is not a step")
    if decimals is not None and not (whole(decimals) and 0 <= decimals <= 255):
        hard.append(
            f"{at}.decimals: {type(decimals).__name__} {decimals!r}; the core takes "
            "a whole number from 0 to 255 only (u8)"
        )
    if spec.get("unit") is not None:
        language_problems(spec["unit"], f"{at}.unit", hard, strict)
    low, high = spec.get("min"), spec.get("max")
    if numeric(value) and (
        (numeric(low) and value < low) or (numeric(high) and value > high)
    ):
        strict.append(f"{at}: the value {value!r} is outside {low!r}..{high!r}")


def dropdown_problems(spec: dict, at: str, hard: list, strict: list) -> None:
    """Check a list's (a dropdown field's) value and items."""
    text_problems(spec, "value", at, hard)
    items = spec.get("items")
    if not isinstance(items, list):
        hard.append(f"{at}.items: {type(items).__name__}, not a list")
        return
    ids = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            hard.append(f"{at}: an item without a text id: {item!r}")
            continue
        name = item["id"]
        ids.append(name)
        if "label" not in item:
            hard.append(f"{at}: item {name!r} has no label")
        else:
            language_problems(item["label"], f"{at} item {name!r}", hard, strict)
        if len(name) not in ID_LENGTH:
            strict.append(f"{at}: item id of {len(name)} characters (model: 1 to 50)")
        unknown_keys(item, {"id", "label"}, f"{at} item {name!r}", strict)
    if not items:
        strict.append(f"{at}: no items")
    twice = sorted({name for name in ids if ids.count(name) > 1})
    if twice:
        strict.append(f"{at}: item ids used twice: {twice}")
    if spec.get("value") is None:
        strict.append(f"{at}: no value, so no item is selected")
    elif isinstance(spec["value"], str) and spec["value"] not in ids:
        strict.append(
            f"{at}: the value {spec['value']!r} is none of its items (the web "
            "configurator then sends no value for the field, and the core refuses the "
            "answer)"
        )


def field_problems(kind: str, spec: dict, at: str, hard: list, strict: list) -> None:
    """Check one field's properties against the core's type for its kind."""
    unknown_keys(spec, FIELD_KEYS[kind], at, strict)
    if kind == "number":
        number_problems(spec, at, hard, strict)
    elif kind == "dropdown":
        dropdown_problems(spec, at, hard, strict)
    elif kind == "label":
        if "value" not in spec:
            hard.append(f"{at}: no value")
        else:
            language_problems(spec["value"], f"{at}.value", hard, strict)
    elif kind == "checkbox":
        if not isinstance(spec.get("value"), bool):
            hard.append(f"{at}.value: {spec.get('value')!r}, not true or false")
    else:  # text, password, textarea
        for key in sorted(FIELD_KEYS[kind]):
            text_problems(spec, key, at, hard)


def page_problems(page) -> tuple[list[str], list[str]]:
    """Return what is wrong with a setup page as sent: (hard, strict).

    ``page`` is the object the core parses: a ``driver_setup_change``'s
    ``require_user_action.input`` (or driver.json's ``setup_data_schema``), decoded
    from the JSON text ucapi sent. The rules are the core's own types (see FIELD_KEYS),
    not the published API description.

    - hard: the core's parser refuses the message, so the page reaches no user
      interface, and the web configurator waits for it for ever. That includes what is
      not JSON the core reads at all: a text with a lone surrogate, a whole number no
      float can hold;
    - strict: the parser takes the page, but it is wrong or risky: a key the model
      doesn't have, a list whose value is none of its items or missing, an id used
      twice, an id outside the model's 1 to 50 characters, a number outside its range
      or beyond 32 bits, a text with no English.
    """
    hard: list[str] = []
    strict: list[str] = []
    if not isinstance(page, dict):
        return [f"the page is {type(page).__name__}, not an object"], strict
    surrogate_problems(page, "the page", hard)
    if "title" not in page:
        hard.append("the page has no title")
    else:
        language_problems(page["title"], "title", hard, strict)
    settings = page.get("settings")
    if not isinstance(settings, list):
        hard.append(f"the page's settings are {type(settings).__name__}, not a list")
        return hard, strict
    unknown_keys(page, {"title", "settings"}, "the page", strict)
    seen: set[str] = set()
    for index, setting in enumerate(settings):
        if not isinstance(setting, dict):
            hard.append(f"setting {index}: {type(setting).__name__}, not an object")
            continue
        name, field = setting.get("id"), setting.get("field")
        at = f"setting {name!r}"
        if not isinstance(name, str):
            hard.append(f"setting {index}: the id is {type(name).__name__}, not text")
        else:
            if len(name) not in ID_LENGTH:
                strict.append(f"{at}: id of {len(name)} characters (model: 1 to 50)")
            if name in seen:
                strict.append(f"{at}: id used twice on the page")
            seen.add(name)
        if "label" not in setting:
            hard.append(f"{at}: no label")
        else:
            language_problems(setting["label"], f"{at} label", hard, strict)
        unknown_keys(setting, {"id", "label", "field"}, at, strict)
        if (
            not isinstance(field, dict)
            or len(field) != 1
            or set(field) - set(FIELD_KEYS)
        ):
            hard.append(f"{at}: the field is not exactly one known type: {field!r}")
            continue
        kind, spec = next(iter(field.items()))
        if not isinstance(spec, dict):
            hard.append(f"{at} {kind}: {type(spec).__name__} {spec!r}, not an object")
            continue
        field_problems(kind, spec, f"{at} {kind}", hard, strict)
    return hard, strict


def saved_entry(**changes) -> dict:
    """Return the Broadlink as a driver saved it; without BL-9's field unless given."""
    entry = {
        "identifier": MAC,
        "name": "RM4 mini",
        "address": IP,
        "data": json.loads(json.dumps(CODES)),
        "remote_address": ADDRESS_A,
        "remote_api_key": OLD_KEY,
    }
    entry.update(changes)
    return entry


def describe(device: BroadlinkConfig | None) -> str:
    """Describe a saved Broadlink for a report line."""
    if device is None:
        return "nothing saved"
    key = {OLD_KEY: "kept", RESTORED_KEY: "restored", None: "none"}.get(
        device.remote_api_key, "new"
    )
    return (
        f"API key {key}, Remote {device.remote_address}, learned codes "
        f"{codes(device.data)}, wake {device.wake_wait_sec!r}, Broadlink at "
        f"{device.address}"
    )


def short(value) -> str:
    """Return a value's repr, shortened for a report line."""
    text = repr(value)
    return text if len(text) <= 20 else f"{text[:6]}... ({len(text)} characters)"


def is_seconds(value, expected: float) -> bool:
    """Return whether a saved wait setting is the float ``expected``."""
    return isinstance(value, float) and value == expected


RESULTS: list[tuple[str, str, bool]] = []


def report(name: str, got: str, ok: bool):
    """Print and record a result."""
    RESULTS.append((name, got, ok))
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got}")


async def case_a():
    """Check (a): configurations saved before BL-9, and by test builds, load."""
    flow = Flow(saved=[saved_entry()])
    device = flow.manager.get(MAC)
    report(
        "A  config saved before BL-9 (no wait field) loads with the default",
        describe(device),
        device is not None
        and device.wake_wait_sec == 4.0
        and device.data == CODES
        and device.remote_api_key == OLD_KEY
        and device.remote_address == ADDRESS_A,
    )
    flow.manager.store()
    stored = json.loads(flow.file_text())[0]
    report(
        "A  ... and is saved back with the wait field",
        str({key: stored.get(key) for key in BL9_SAVED}),
        stored.get("wake_wait_sec") == 4.0 and stored.get("data") == CODES,
    )

    # a test build of BL-9 before fix round 2 also saved the drop wait, since removed
    flow = Flow(saved=[saved_entry(wake_wait_sec=2.5, drop_wait_sec=1.0)])
    device = flow.manager.get(MAC)
    flow.manager.store()
    stored = json.loads(flow.file_text())[0]
    report(
        "A  config saved with the removed drop wait loads, and is saved back without it",
        f"{describe(device)}; saved back: {sorted(stored)}",
        device is not None
        and is_seconds(device.wake_wait_sec, 2.5)
        and device.data == CODES
        and device.remote_api_key == OLD_KEY
        and stored.get("wake_wait_sec") == 2.5
        and "drop_wait_sec" not in stored,
    )


async def case_w():
    """Check W: Update keeps the learned codes (before BL-9 it saved data={})."""
    flow = Flow(saved=[saved_entry()])
    await flow.open_page("update")
    # the page's values as the code before BL-9 asks for them: an Update needs a PIN
    # (the wait, where the page has one, goes as the page shows it)
    result = await flow.answer(ADDRESS_A, PIN)
    device = flow.saved()
    report(
        "W  Update (with a PIN, the only Update the code before BL-9 allows) keeps the "
        "learned codes",
        f"{type(result).__name__}; {describe(device)} ({codes(CODES)} before the "
        f"Update); data saved: {json.dumps(device.data) if device else None}",
        isinstance(result, SetupComplete)
        and device is not None
        and device.data == CODES,
    )


async def case_b():
    """Check (b): Add needs the PIN; the wait is read, clamped and rounded."""
    flow = Flow(saved=None)
    await flow.open_page("add")
    result = await flow.answer(ADDRESS_A, "", wake_wait_sec="5")
    again = is_access_page(result)
    report(
        "B1 Add with an empty PIN is refused: page again, nothing saved, no key asked",
        f"page again {again}, saved {flow.saved()}, key requests {len(KEY_REQUESTS)}",
        again and flow.saved() is None and not KEY_REQUESTS,
    )
    report(
        "B1 ... and the page says the PIN is needed and keeps the wait that was picked",
        f"messages {messages(result)}, wake {wait_shown(result)!r} selected",
        again and bool(messages(result)) and wait_shown(result) == "5",
    )

    # What the driver makes of a wait as it arrives: the wait in the answer -> the wait
    # saved. First what the page's list sends, the id of the item picked (here its
    # first and its last): these go through the core's answer checks.
    picked = [("0", 0.0), ("6", 6.0)]
    # Then what no list sends and the Remote's core refuses before the driver sees it
    # (a list value that is none of the items, a value that isn't text, the field left
    # out): the driver reads it leniently all the same, as from another client. These
    # go past the stand-in core on purpose, and say so (``lenient``; S5 counts them).
    unlisted = [
        ("7", 6.0),  # clamped to 0..6 s
        ("-1", 0.0),
        ("2.3", 2.0),  # to the nearest whole second
        ("2.7", 3.0),
        ("0.2", 0.0),
        ("2.5", 3.0),  # a half rounds up (round() gives 2)
        ("0.5", 1.0),  # (round() gives 0)
        ("1.5", 2.0),
        ("5.75", 6.0),
        (" 5 ", 5.0),  # spaces
        ("1,5", 2.0),  # a decimal comma
        ("abc", 4.0),  # not a number: the default (Add)
        ("", 4.0),
        ("nan", 4.0),  # not finite: the default
        ("inf", 4.0),
        ("1e9", 6.0),
        ("-0.4", 0.0),
        (3, 3.0),  # a number, not text
        (2.5, 3.0),
        (10**400, 4.0),  # too big for a float: the default
        (True, 4.0),  # a bool: the default
        (NOT_SENT, 4.0),  # not sent at all: the default
    ]
    entered = [(wake, saved, None) for wake, saved in picked]
    entered += [(wake, saved, "B2") for wake, saved in unlisted]
    for wake, wake_saved, past_core in entered:
        flow = Flow(saved=None)
        await flow.open_page("add")
        result = await flow.answer(
            ADDRESS_A, PIN, lenient=past_core, wake_wait_sec=wake
        )
        device = flow.saved()
        report(
            f"B2 Add, wake {short(wake)} -> {wake_saved}",
            f"{type(result).__name__}; {describe(device)}",
            isinstance(result, SetupComplete)
            and device is not None
            and is_seconds(device.wake_wait_sec, wake_saved)
            and device.remote_api_key == "new-key-1"
            and device.remote_address == ADDRESS_A
            and KEY_REQUESTS == [(ADDRESS_A, PIN, KEY_NAME)]
            and not OPEN_REMOTES
            and PIN not in flow.file_text(),
        )

    # the integration's first setup (not "Add"): the framework clears the configuration
    # first, so there is nothing to keep, as on Add
    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    page = await flow.open_page("first")
    label = pin_label(page)
    wake = wait_shown(page)
    refused = await flow.answer(ADDRESS_A, "", wake_wait_sec="1")
    saved_meanwhile = flow.saved()
    accepted = await flow.answer(ADDRESS_A, PIN, wake_wait_sec="1")
    device = flow.saved()
    report(
        "B3 first setup: the default, the PIN required, nothing kept",
        f"PIN label {label!r}, wake {wake!r} selected; empty PIN: page "
        f"again {is_access_page(refused)}, saved meanwhile {saved_meanwhile}; then "
        f"{type(accepted).__name__}; {describe(device)}",
        label == "Remote PIN"
        and wake == "4"
        and is_access_page(refused)
        and bool(messages(refused))
        and saved_meanwhile is None
        and isinstance(accepted, SetupComplete)
        and device is not None
        and device.remote_api_key == "new-key-1"
        and is_seconds(device.wake_wait_sec, 1.0),
    )


async def case_c():
    """Check (c): Update with an empty PIN keeps the access and the codes."""
    flow = Flow(saved=[saved_entry(**BL9_SAVED)], remotes=(REMOTE_A,))
    await flow.open_page("update", address=NEW_IP)
    result = await flow.answer(ADDRESS_A, "", wake_wait_sec="5")
    device = flow.saved()
    report(
        "C1 Update (Broadlink at a new address), empty PIN: keeps the API key, the "
        "Remote address and the learned codes; saves the new wait",
        f"{type(result).__name__}; {describe(device)}; key requests {len(KEY_REQUESTS)}",
        isinstance(result, SetupComplete)
        and device is not None
        and device.remote_api_key == OLD_KEY
        and device.remote_address == ADDRESS_A
        and device.data == CODES
        and is_seconds(device.wake_wait_sec, 5.0)
        and device.address == NEW_IP
        and not KEY_REQUESTS,
    )

    # the Broadlink found by discovery; no Remote discovered, so the address is typed
    flow = Flow(
        saved=[saved_entry(**BL9_SAVED)],
        remotes=(),
        discovery=discover.BroadlinkDiscovery(timeout=1),
    )
    page = await flow.open_page("update")
    kind, value, _ = address_field(page)
    # an empty wait: no list sends that, and the Remote's core would refuse it; the
    # driver takes it as no wait given
    result = await flow.answer(value, "", lenient="C2", wake_wait_sec="")
    device = flow.saved()
    report(
        "C2 Update through the discovered Broadlink, Remote address typed (prefilled), "
        "empty PIN: keeps everything; a wait left empty keeps the saved one",
        f"address field {kind} {value!r}; {type(result).__name__}; {describe(device)}",
        kind == "text"
        and value == ADDRESS_A
        and isinstance(result, SetupComplete)
        and device is not None
        and device.remote_api_key == OLD_KEY
        and device.remote_address == ADDRESS_A
        and device.data == CODES
        and is_seconds(device.wake_wait_sec, 2.0)
        and not KEY_REQUESTS,
    )

    flow = Flow(saved=[saved_entry(remote_address=None, remote_api_key=None)])
    page = await flow.open_page("update")
    label = pin_label(page)
    result = await flow.answer(ADDRESS_A, "", wake_wait_sec="4")
    device = flow.saved()
    report(
        "C3 Update of a Broadlink saved without an API key: the PIN is required",
        f"PIN label {label!r}; page again {is_access_page(result)}, messages "
        f"{messages(result)}; {describe(device)}",
        label == "Remote PIN"
        and is_access_page(result)
        and bool(messages(result))
        and device is not None
        and device.remote_api_key is None
        and device.data == CODES
        and not KEY_REQUESTS,
    )


async def case_d():
    """Check (d): Update with a PIN makes a new key and keeps the codes."""
    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    await flow.open_page("update")
    result = await flow.answer(ADDRESS_A, PIN, wake_wait_sec="3")
    device = flow.saved()
    report(
        "D1 Update with a PIN: a new API key; codes kept, the wait saved",
        f"{type(result).__name__}; {describe(device)}; key requests {KEY_REQUESTS}",
        isinstance(result, SetupComplete)
        and device is not None
        and device.remote_api_key == "new-key-1"
        and KEY_REQUESTS == [(ADDRESS_A, PIN, KEY_NAME)]
        and device.data == CODES
        and is_seconds(device.wake_wait_sec, 3.0)
        and not OPEN_REMOTES
        and PIN not in flow.file_text(),
    )

    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    await flow.open_page("update")
    wrong = await flow.answer(ADDRESS_A, WRONG_PIN, wake_wait_sec="3")
    wake = wait_shown(wrong)
    kept = await flow.answer(ADDRESS_A, "")  # the wait's list left as the page shows it
    device = flow.saved()
    report(
        "D2 Update with a wrong PIN: the authentication error, and the page keeps the "
        "wait that was picked; then an empty PIN keeps the access",
        f"first: page again {is_access_page(wrong)}, messages {messages(wrong)}, wake "
        f"{wake!r} selected; then {type(kept).__name__}; {describe(device)}",
        is_access_page(wrong)
        and bool(messages(wrong))
        and wake == "3"
        and isinstance(kept, SetupComplete)
        and device is not None
        and device.remote_api_key == OLD_KEY
        and device.data == CODES
        and is_seconds(device.wake_wait_sec, 3.0)
        and not OPEN_REMOTES,
    )

    # the right PIN, but the request fails after unfurled deleted the saved key
    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    await flow.open_page("update")
    FAIL_AFTER_DELETE[0] = True
    failed = await flow.answer(ADDRESS_A, PIN, wake_wait_sec="3")
    label = pin_label(failed)
    wake = wait_shown(failed)
    refused = await flow.answer(ADDRESS_A, "", wake_wait_sec="3")
    on_disk = flow.saved()
    accepted = await flow.answer(ADDRESS_A, PIN, wake_wait_sec="3")
    device = flow.saved()
    report(
        "D3 Update whose key request fails after the old key may be deleted: the page "
        "keeps the wait that was picked; an empty PIN can't keep that key any more; "
        "the PIN again makes a new one",
        f"after the failure: PIN label {label!r}, wake {wake!r} selected; empty PIN: "
        f"page again {is_access_page(refused)}, messages {messages(refused)}, on disk "
        f"{describe(on_disk)}; then {type(accepted).__name__}; {describe(device)}",
        is_access_page(failed)
        and label == "Remote PIN"
        and wake == "3"
        and is_access_page(refused)
        and any("removed" in text for text in messages(refused))
        and on_disk is not None
        and on_disk.remote_api_key == OLD_KEY  # unchanged until the Update is saved
        and isinstance(accepted, SetupComplete)
        and device is not None
        and device.remote_api_key == "new-key-2"
        and device.data == CODES
        and len(KEY_REQUESTS) == 2
        and not OPEN_REMOTES,
    )


async def case_e():
    """Check (e): Update to another Remote address with an empty PIN is refused."""
    flow = Flow(saved=[saved_entry(**BL9_SAVED)], remotes=(REMOTE_A, REMOTE_B))
    await flow.open_page("update")
    refused = await flow.answer(ADDRESS_B, "", wake_wait_sec="5")
    device = flow.saved()
    report(
        "E1 Update to another Remote (selected), empty PIN: refused with a clear error",
        f"page again {is_access_page(refused)}, messages {messages(refused)}; on disk: "
        f"{describe(device)}; key requests {len(KEY_REQUESTS)}",
        is_access_page(refused)
        and any("address" in text.lower() for text in messages(refused))
        and device is not None
        and device.remote_api_key == OLD_KEY
        and device.remote_address == ADDRESS_A
        and device.wake_wait_sec == 2.0
        and not KEY_REQUESTS,
    )
    _, selected, _ = address_field(refused)
    wake = wait_shown(refused)
    report(
        "E1 ... the page keeps what was entered",
        f"Remote {selected}, wake {wake!r} selected",
        selected == ADDRESS_B and wake == "5",
    )
    accepted = await flow.answer(ADDRESS_B, PIN, wake_wait_sec="5")
    device = flow.saved()
    report(
        "E2 ... then with the PIN: a new key for the other Remote; codes kept",
        f"{type(accepted).__name__}; {describe(device)}; key requests {KEY_REQUESTS}",
        isinstance(accepted, SetupComplete)
        and device is not None
        and device.remote_address == ADDRESS_B
        and device.remote_api_key == "new-key-1"
        and KEY_REQUESTS == [(ADDRESS_B, PIN, KEY_NAME)]
        and device.data == CODES
        and is_seconds(device.wake_wait_sec, 5.0),
    )

    flow = Flow(saved=[saved_entry(**BL9_SAVED)], remotes=())
    await flow.open_page("update")
    refused = await flow.answer("192.168.2.205", "", wake_wait_sec="4")
    device = flow.saved()
    report(
        "E3 Update to another Remote (typed), empty PIN: refused with a clear error",
        f"page again {is_access_page(refused)}, messages {messages(refused)}, address "
        f"field {address_field(refused)[:2]}; on disk: {describe(device)}",
        is_access_page(refused)
        and any("address" in text.lower() for text in messages(refused))
        and address_field(refused)[:2] == ("text", "192.168.2.205")
        and device is not None
        and device.remote_api_key == OLD_KEY
        and device.remote_address == ADDRESS_A
        and not KEY_REQUESTS,
    )


def check_page(name, page, wake, kind, address, pin_optional):
    """Check the "Remote API access" page as first shown.

    Its fields, in order: the Remote, the PIN (a password field with the id "password",
    empty), the wait and the wait's help line; nothing else. The wait is a list of whole
    seconds: the ids "0" to "6" in order, as text; "4" marked as the default and no
    other; "0" marked as no wait; the item ``wake`` selected, by its id. No field of
    the page is a number field, and the page as sent has no problem (page_problems).
    """
    settings = page.settings if isinstance(page, RequestUserInput) else []
    ids = [setting.get("id") for setting in settings]
    by_id = fields(page)
    got_kind, got_address, items = address_field(page)
    pin = pin_field(page)
    label = pin_label(page)
    wait = by_id.get("wake_wait_sec", {})
    help_field = by_id.get("wake_wait_sec_help", {}).get("field", {})
    help_text = help_field.get("label", {}).get("value", {}).get("en")
    waits = wait_list(page) or {}
    wait_items = waits.get("items") if isinstance(waits.get("items"), list) else []
    wait_ids = [
        item.get("id") if isinstance(item, dict) else item for item in wait_items
    ]
    selected = waits.get("value")
    numbers = number_fields(settings)
    hard, strict = ["?"], []
    if isinstance(page, RequestUserInput):
        hard, strict = page_problems(as_sent(page))
    report(
        name,
        f"ids {ids}; PIN field {pin.get('id')!r} {pin.get('field')}; the wait: a "
        f"{'/'.join(sorted(wait.get('field', {}))) or 'missing'} field, item ids "
        f"{wait_ids}, {selected!r} selected, '(default)' on "
        f"{marked(wait_items, '(default)')}, '(no wait)' on "
        f"{marked(wait_items, '(no wait)')}, the items as decided "
        f"{wait_items == WAIT_ITEMS}; number fields {numbers}; Remote {got_kind} "
        f"{got_address!r}; PIN label {label!r}; problems of the page as sent: hard "
        f"{hard}, strict {strict}",
        is_access_page(page)
        and ids == ["remote_address", PIN_ID, "wake_wait_sec", "wake_wait_sec_help"]
        and pin.get("id") == PIN_ID
        and pin.get("field") == {"password": {"value": ""}}
        and wait.get("label") == {"en": WAKE_TITLE}
        and bool(help_text)
        and "PIN" not in help_text
        and wait.get("field") == {"dropdown": {"value": wake, "items": WAIT_ITEMS}}
        and wait_ids == ["0", "1", "2", "3", "4", "5", "6"]
        and marked(wait_items, "(default)") == ["4"]
        and marked(wait_items, "(no wait)") == ["0"]
        and isinstance(selected, str)
        and selected == wake
        and selected in wait_ids
        and not numbers
        and got_kind == kind
        and got_address == address
        and (kind != "dropdown" or address in items)
        and ("leave empty" in label) == pin_optional
        and label.startswith("Remote PIN")
        and not messages(page)
        and not hard
        and not strict,
    )


async def case_f():
    """Check (f): the page's fields; prefilled on Update, the default on Add."""
    flow = Flow(saved=None, remotes=(REMOTE_A, REMOTE_B))
    check_page(
        "F1 Add, Remotes discovered: the default; the first Remote selected",
        await flow.open_page("add"),
        "4",
        "dropdown",
        ADDRESS_A,
        False,
    )
    flow = Flow(saved=None, remotes=())
    check_page(
        "F2 Add, no Remote discovered: the default; the address empty",
        await flow.open_page("add"),
        "4",
        "text",
        "",
        False,
    )
    flow = Flow(saved=[saved_entry(**BL9_SAVED)], remotes=(REMOTE_B, REMOTE_A))
    check_page(
        "F3 Update, Remotes discovered: the saved value; the saved Remote selected (not "
        "the first)",
        await flow.open_page("update"),
        SAVED_WAIT,
        "dropdown",
        ADDRESS_A,
        True,
    )
    flow = Flow(saved=[saved_entry(**BL9_SAVED)], remotes=())
    check_page(
        "F4 Update, no Remote discovered: the saved value; the saved address",
        await flow.open_page("update"),
        SAVED_WAIT,
        "text",
        ADDRESS_A,
        True,
    )
    flow = Flow(saved=[saved_entry()], remotes=(REMOTE_A,))
    check_page(
        "F5 Update of a configuration saved before BL-9: the default",
        await flow.open_page("update"),
        "4",
        "dropdown",
        ADDRESS_A,
        True,
    )

    # saved from the typed-address page: discovery reports the Remote under another URL
    typed = "192.168.2.204"
    flow = Flow(saved=[saved_entry(remote_address=typed, **BL9_SAVED)])
    page = await flow.open_page("update")
    check_page(
        "F6 Update, the saved address isn't what discovery reports: it is offered and "
        "selected",
        page,
        SAVED_WAIT,
        "dropdown",
        typed,
        True,
    )
    result = await flow.answer(typed, "")  # the wait's list left as shown
    device = flow.saved()
    report(
        "F6 ... and kept with an empty PIN",
        f"{type(result).__name__}; {describe(device)}",
        isinstance(result, SetupComplete)
        and device is not None
        and device.remote_address == typed
        and device.remote_api_key == OLD_KEY
        and device.data == CODES
        and not KEY_REQUESTS,
    )

    # A saved wait that is no item of the list: a hand-edited configuration, or one a
    # test build saved or a backup restored (2.5: the number field of the build before
    # fix round 7 went in half seconds). The page selects a usable item; the saved
    # value stays as it is until the page is saved, and is then stored as shown.
    for case, edited, shown, what in (
        ("F7", "abc", "4", "the default"),
        ("F8", 99, "6", "the maximum"),
        ("F9", 2.5, "3", "the nearest whole second, a half up"),
    ):
        flow = Flow(saved=[saved_entry(wake_wait_sec=edited)])
        page = await flow.open_page("update")
        meanwhile = flow.saved()
        check_page(
            f"{case} Update of a hand-edited configuration (wake {edited!r}): {what}",
            page,
            shown,
            "dropdown",
            ADDRESS_A,
            True,
        )
        result = await flow.answer(ADDRESS_A, "")  # the list left as shown
        device = flow.saved()
        on_disk = getattr(meanwhile, "wake_wait_sec", None)
        report(
            f"{case} ... on disk it stays {edited!r} while the page is shown; saved "
            "with the list left as shown, it is stored as shown",
            f"on disk meanwhile: wake {on_disk!r}; {type(result).__name__}; "
            f"{describe(device)}",
            type(on_disk) is type(edited)
            and on_disk == edited
            and isinstance(result, SetupComplete)
            and device is not None
            and is_seconds(device.wake_wait_sec, float(shown))
            and device.data == CODES,
        )
        # An answer without the wait keeps the saved one, and that saved value is read
        # as leniently as the page reads it. No user interface sends such an answer (the
        # Remote's core refuses it), so it goes past the stand-in core on purpose.
        flow = Flow(saved=[saved_entry(wake_wait_sec=edited)])
        await flow.open_page("update")
        result = await flow.answer(ADDRESS_A, "", lenient=case, wake_wait_sec=NOT_SENT)
        device = flow.saved()
        report(
            f"{case} ... and an answer without the wait stores the same usable value",
            f"{type(result).__name__}; {describe(device)}",
            isinstance(result, SetupComplete)
            and device is not None
            and is_seconds(device.wake_wait_sec, float(shown))
            and device.data == CODES,
        )


async def case_g():
    """Check G: an Update left unfinished doesn't derail the next Configure."""
    endings = (
        ("G1", "Cancel (AbortDriverSetup OTHER)", IntegrationSetupError.OTHER),
        ("G2", "a time-out (AbortDriverSetup TIMEOUT)", IntegrationSetupError.TIMEOUT),
        ("G3", "no abort (a lost connection): the next setup just starts", None),
    )
    for case, ending, error in endings:
        flow = Flow(saved=[saved_entry(**BL9_SAVED)], remotes=(REMOTE_A, REMOTE_B))
        await flow.open_page("update")
        # the user changes the page, gets an error, then leaves
        await flow.answer(ADDRESS_B, "", wake_wait_sec="5")
        ended = None
        if error is None:
            first = await flow.send()  # Configure again: "Configuration mode"
            left = flow.pending()
        else:
            ended = await flow.abort(error)
            left = flow.pending()
            first = await flow.send()
        choice = await flow.send({"choice": MAC, "action": "update"})
        page = await flow.send({"address": IP})
        _, selected, _ = address_field(page)
        wake = wait_shown(page)
        result = await flow.answer(selected, "")  # the wait's list left as shown
        device = flow.saved()
        report(
            f"{case} Update left on the access page by {ending}, then Configure -> "
            "Update: the Broadlink first, then the page with what is saved; an empty "
            "PIN keeps the access",
            f"pending config after the {'new setup' if error is None else 'abort'}: "
            f"{'none' if left is None else 'still there'}; answer to Update: "
            f"{page_name(choice)!r}; then {page_name(page)!r}: Remote {selected!r}, "
            f"wake {wake!r} selected, PIN label {pin_label(page)!r}, messages "
            f"{messages(page)}; then {type(result).__name__}; {describe(device)}; key "
            f"requests {len(KEY_REQUESTS)}",
            left is None
            and (error is None or isinstance(ended, SetupError))
            and is_configuration_mode(first)
            and is_broadlink_page(choice)
            and is_access_page(page)
            and selected == ADDRESS_A
            and wake == SAVED_WAIT
            and "leave empty" in pin_label(page)
            and not messages(page)
            and isinstance(result, SetupComplete)
            and device is not None
            and device.remote_api_key == OLD_KEY
            and device.remote_address == ADDRESS_A
            and device.data == CODES
            and is_seconds(device.wake_wait_sec, 2.0)
            and not KEY_REQUESTS,
        )


async def case_g4():
    """Check G4: an Update ended without an abort, then Add of another Broadlink."""
    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    await flow.open_page("update")
    await flow.send()  # no abort: the next setup just starts
    # Add leaves the device list as shown, with the configured Broadlink selected
    await flow.send({"choice": MAC, "action": "add"})
    page = await flow.send({"address": OTHER_IP})
    result = await flow.answer(ADDRESS_A, PIN)
    saved = BroadlinkConfigManager(flow.folder, config_class=BroadlinkConfig)
    ids = sorted(device.identifier for device in saved.all())
    report(
        "G4 Update left with no abort, then Configure -> Add of another Broadlink: "
        "both are saved",
        f"page {page_name(page)!r}; {type(result).__name__}; saved {ids}",
        isinstance(result, SetupComplete) and ids == sorted([MAC, OTHER_MAC]),
    )


async def case_h():
    """Check H: after a key request that may have deleted the key, no address keeps it.

    The same Remote is listed twice when the saved address isn't what discovery reports
    (F6): "Saved (192.168.2.204)" and discovery's http://192.168.2.204:80/api/.
    """
    typed = "192.168.2.204"
    values = {"wake_wait_sec": SAVED_WAIT}
    for case, failed_on, empty_on in (
        ("H1", ADDRESS_A, typed),
        ("H2", typed, ADDRESS_A),
    ):
        flow = Flow(saved=[saved_entry(remote_address=typed, **BL9_SAVED)])
        _, _, items = address_field(await flow.open_page("update"))
        FAIL_AFTER_DELETE[0] = True
        failed = await flow.answer(failed_on, PIN, **values)
        refused = await flow.answer(empty_on, "", **values)
        on_disk = flow.saved()
        accepted = await flow.answer(empty_on, PIN, **values)
        device = flow.saved()
        report(
            f"{case} the same Remote listed twice; the key request through {failed_on} "
            f"fails after the old key may be deleted; then {empty_on} with an empty PIN "
            "is refused too, with the message; the PIN makes a new key",
            f"Remotes {items}; after the failure: PIN label {pin_label(failed)!r}; "
            f"empty PIN: page again {is_access_page(refused)}, messages "
            f"{messages(refused)}, on disk {describe(on_disk)}; then "
            f"{type(accepted).__name__}; {describe(device)}; key requests "
            f"{KEY_REQUESTS}",
            items == [typed, ADDRESS_A]
            and is_access_page(failed)
            and pin_label(failed) == "Remote PIN"
            and is_access_page(refused)
            and any("removed" in text for text in messages(refused))
            and not any("keep the saved address" in text for text in messages(refused))
            and on_disk is not None
            and on_disk.remote_api_key == OLD_KEY  # unchanged until the Update is saved
            and isinstance(accepted, SetupComplete)
            and device is not None
            and device.remote_api_key == "new-key-2"
            and device.remote_address == empty_on
            and device.data == CODES
            and KEY_REQUESTS == [(failed_on, PIN, KEY_NAME), (empty_on, PIN, KEY_NAME)]
            and not OPEN_REMOTES,
        )

    # a wrong PIN fails before unfurled deletes anything: the access can still be kept
    flow = Flow(saved=[saved_entry(remote_address=typed, **BL9_SAVED)])
    await flow.open_page("update")
    wrong = await flow.answer(ADDRESS_A, WRONG_PIN, **values)
    kept = await flow.answer(typed, "", **values)
    device = flow.saved()
    report(
        f"H3 a wrong PIN through {ADDRESS_A}: the PIN stays optional, and {typed} with "
        "an empty PIN keeps the access",
        f"after the wrong PIN: messages {messages(wrong)}, PIN label "
        f"{pin_label(wrong)!r}; then {type(kept).__name__}; {describe(device)}",
        is_access_page(wrong)
        and bool(messages(wrong))
        and "leave empty" in pin_label(wrong)
        and isinstance(kept, SetupComplete)
        and device is not None
        and device.remote_api_key == OLD_KEY
        and device.remote_address == typed
        and device.data == CODES
        and KEY_REQUESTS == [(ADDRESS_A, WRONG_PIN, KEY_NAME)]
        and not OPEN_REMOTES,
    )

    await case_h_later_setups(values)


def removed_message(page) -> bool:
    """Return whether ``page`` is the access page saying the access may be removed."""
    return is_access_page(page) and any("removed" in text for text in messages(page))


async def case_h_later_setups(values: dict):
    """Check H4, H5: the guard lasts until a key request succeeds, across setups.

    The framework keeps one setup flow for as long as the driver runs, and a cancelled
    setup saves nothing: the saved key may still be the one a failed key request
    removed from the Remote, so a later setup must not keep it with an empty PIN.
    """
    # the setup lens's case: a failure on the saved Remote, then Cancel
    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    await flow.open_page("update")
    FAIL_AFTER_DELETE[0] = True
    failed = await flow.answer(ADDRESS_A, PIN, **values)
    ended = await flow.abort()
    page = await flow.open_page("update")
    refused = await flow.answer(ADDRESS_A, "", **values)
    on_disk = flow.saved()
    accepted = await flow.answer(ADDRESS_A, PIN, **values)
    device = flow.saved()
    report(
        "H4 the key request to the saved Remote fails after the old key may be deleted, "
        "then Cancel: the next setup asks for the PIN, an empty PIN is refused with the "
        "message; the PIN makes a new key",
        f"after the failure: PIN label {pin_label(failed)!r}; Cancel: "
        f"{type(ended).__name__}; next setup: PIN label {pin_label(page)!r}; empty PIN: "
        f"page again {is_access_page(refused)}, messages {messages(refused)}, on disk "
        f"{describe(on_disk)}; then {type(accepted).__name__}; {describe(device)}",
        pin_label(failed) == "Remote PIN"
        and isinstance(ended, SetupError)
        and is_access_page(page)
        and pin_label(page) == "Remote PIN"
        and removed_message(refused)
        and on_disk is not None
        and on_disk.remote_api_key == OLD_KEY  # Cancel saved nothing
        and isinstance(accepted, SetupComplete)
        and device is not None
        and device.remote_api_key == "new-key-2"
        and device.data == CODES
        and KEY_REQUESTS == [(ADDRESS_A, PIN, KEY_NAME)] * 2,
    )
    # the key request has succeeded: a later setup keeps that key with an empty PIN
    page = await flow.open_page("update")
    kept = await flow.answer(ADDRESS_A, "", **values)
    device = flow.saved()
    report(
        "H4 ... once a key request has succeeded, the next setup keeps that key with an "
        "empty PIN again",
        f"PIN label {pin_label(page)!r}; then {type(kept).__name__}; {describe(device)}; "
        f"key requests {len(KEY_REQUESTS)}",
        "leave empty" in pin_label(page)
        and isinstance(kept, SetupComplete)
        and device is not None
        and device.remote_api_key == "new-key-2"
        and device.remote_address == ADDRESS_A
        and device.data == CODES
        and len(KEY_REQUESTS) == 2
        and not OPEN_REMOTES,
    )

    # a failure on another Remote counts too, and a new setup without an abort (after a
    # lost connection) doesn't end it either
    flow = Flow(saved=[saved_entry(**BL9_SAVED)], remotes=(REMOTE_A, REMOTE_B))
    await flow.open_page("update")
    FAIL_AFTER_DELETE[0] = True
    failed = await flow.answer(ADDRESS_B, PIN, **values)
    refused = await flow.answer(ADDRESS_A, "", **values)
    page = await flow.open_page("update")  # no abort: the next setup just starts
    again = await flow.answer(ADDRESS_A, "", **values)
    device = flow.saved()
    report(
        "H5 the key request to another Remote fails the same way: no address keeps the "
        "access, in that setup or in the next one, started without an abort",
        f"after the failure: PIN label {pin_label(failed)!r}; the saved Remote, empty "
        f"PIN: messages {messages(refused)}; next setup: PIN label {pin_label(page)!r}; "
        f"empty PIN: page again {is_access_page(again)}, messages {messages(again)}; "
        f"on disk {describe(device)}; key requests {KEY_REQUESTS}",
        pin_label(failed) == "Remote PIN"
        and removed_message(refused)
        and is_access_page(page)
        and pin_label(page) == "Remote PIN"
        and removed_message(again)
        and device is not None
        and device.remote_api_key == OLD_KEY
        and device.remote_address == ADDRESS_A
        and device.data == CODES
        and KEY_REQUESTS == [(ADDRESS_B, PIN, KEY_NAME)]
        and not OPEN_REMOTES,
    )


async def case_k():
    """Check K1-K4: the saved API key never sits in the pending config.

    ucapi-framework logs the pending config's repr (its first 200 characters) at ERROR
    when anything raises in the "Remote API access" step. K2-K4 save no learned codes,
    as the owner's config, so a key in that repr would fall inside the 200 characters.
    K2-K4 look at every record logged meanwhile, as logged (before any filter), with
    every logger at DEBUG.
    """
    # no Remote discovered: the address is a text field, which the user can empty (a
    # list always sends one of its items)
    flow = Flow(saved=[saved_entry(**BL9_SAVED)], remotes=())
    await flow.open_page("update")
    on_page = flow.pending()
    shown = repr(on_page)
    refused = await flow.answer("", "")
    after = flow.pending()
    report(
        "K1 Update: while the access page is shown, the pending config has no API key "
        "(only the empty-PIN answer takes the saved one)",
        f"on the page: key {short(getattr(on_page, 'remote_api_key', '?'))}, repr "
        f"with the saved key {OLD_KEY in shown}; after a refused answer: key "
        f"{short(getattr(after, 'remote_api_key', '?'))}",
        on_page is not None
        and on_page.remote_api_key is None
        and OLD_KEY not in shown
        and is_access_page(refused)
        and after is not None
        and after.remote_api_key is None,
    )

    attempts = (
        (
            "K2 Update, a typed Remote address unfurled can't parse, with the PIN (the "
            "review's case): the setup ends with an error before any key request",
            (),
            "http://[192.168.2.204/api/",
            PIN,
            False,
            0,
        ),
        (
            "K3 Update, empty PIN: the saved key is kept, then the driver can't create "
            "the device",
            (REMOTE_A,),
            ADDRESS_A,
            "",
            True,
            0,
        ),
        (
            "K4 Update with the PIN: a new key is made, then the driver can't create "
            "the device",
            (REMOTE_A,),
            ADDRESS_A,
            PIN,
            True,
            1,
        ),
    )
    for name, remotes, address, pin, fail_add, requests in attempts:
        flow = Flow(saved=[saved_entry(data={}, **BL9_SAVED)], remotes=remotes)
        await flow.open_page("update")
        FAIL_ADD_DEVICE[0] = fail_add
        start = len(LOGS.entries)
        result = await flow.answer(address, pin, wake_wait_sec=SAVED_WAIT)
        texts = LOGS.texts(start)
        keys = [OLD_KEY] + [f"new-key-{n}" for n in range(1, len(KEY_REQUESTS) + 1)]
        reprs = [text for text in texts if "repr: BroadlinkConfig(" in text]
        leaks = [text for text in texts if any(key in text for key in keys)]
        report(
            f"{name}; the framework's log of the pending config has no key",
            f"{type(result).__name__}; repr lines {len(reprs)}; key requests "
            f"{len(KEY_REQUESTS)}; log lines with a key {leaks}",
            isinstance(result, SetupError)
            and len(reprs) == 1
            and not leaks
            and len(KEY_REQUESTS) == requests
            and not OPEN_REMOTES,
        )


def secret_records(start: int, keys) -> tuple[list, list, list]:
    """Return the driver-log records since ``start`` that show a secret.

    Three lists: the records showing one of ``keys`` as logged (the driver's log
    without its filter), those whose line, as the driver's handler writes it, shows
    one, and those whose line shows a PIN typed on a page.
    """
    in_log = [e for e in LOGS.entries[start:] if LOGS.in_driver_log(e.record)]
    logged = [e for e in in_log if any(key in e.text for key in keys)]
    written = [e for e in in_log if e.driver and any(key in e.driver for key in keys)]
    pins = [e for e in in_log if e.driver and pin_in(e.driver)]
    return logged, written, pins


def where(entries) -> dict:
    """Count records by logger and level, for a report line."""
    return dict(Counter(f"{e.record.name} {e.record.levelname}" for e in entries))


def around(text: str | None, secrets) -> str:
    """Return the part of a text around the first of ``secrets`` it shows."""
    text = text or ""
    found = [text.find(secret) for secret in secrets if secret in text]
    start = max(min(found) - 60, 0) if found else 0
    return f"{'...' if start else ''}{text[start:start + 100]}..."


def textarea(page, key: str):
    """Return the value of the page's text field ``key`` (a textarea), if any."""
    return fields(page).get(key, {}).get("field", {}).get("textarea", {}).get("value")


def secrets_line(logged, written, pins, keys) -> str:
    """Describe what ``secret_records`` found, for a report line."""
    return (
        f"driver-log records with the key: as logged {where(logged)}, as the driver "
        f"writes them {len(written)} {[around(e.driver, keys) for e in written[:2]]}; "
        f"with a typed PIN {len(pins)}"
    )


async def case_k_backup():
    """Check K5-K7: Configure -> Backup and Restore; no driver-log record shows a key.

    ucapi logs every message at DEBUG and hides only the values of key names it knows.
    The configuration, the Remote API key included, travels as one JSON text: the
    Backup page's text field, the Restore answer's ``restore_data``, and the Restore
    page shown again after a restore that failed. ucapi-framework also logs, at
    WARNING, a backup entry it can't read, as a dict. Each check needs the key in the
    driver's log as logged (what the log shows without driver.py's filter) and in none
    of the lines the driver's handler writes.
    """
    keys = [OLD_KEY, RESTORED_KEY]
    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    start = len(LOGS.entries)
    await flow.send()  # "Configuration mode"
    page = await flow.send({"choice": MAC, "action": "backup"})
    backup = textarea(page, "backup_data")
    try:
        entries = json.loads(backup)
    except (TypeError, ValueError):
        entries = None
    # closing the page: its text field's value sent back (one more message with the
    # key in it; the framework ignores the values here)
    done = await flow.send({"backup_data": backup})
    logged, written, pins = secret_records(start, keys)
    report(
        "K5 Configure -> Backup: the page has the configuration with the saved key, and "
        "no line of the driver's log shows the key",
        f"page {page_name(page)!r}; the backup is the saved configuration "
        f"{entries == [saved_entry(**BL9_SAVED)]}; then {type(done).__name__}; "
        + secrets_line(logged, written, pins, keys),
        page_name(page) == "Configuration Backup"
        and entries == [saved_entry(**BL9_SAVED)]
        and isinstance(done, SetupComplete)
        and LOGS.ready()
        and bool(logged)
        and not written
        and not pins,
    )

    # pasted as the Backup page shows it, with another wake wait and key
    restored = saved_entry(wake_wait_sec=1.0, remote_api_key=RESTORED_KEY)
    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    start = len(LOGS.entries)
    await flow.send()
    page = await flow.send({"choice": MAC, "action": "restore"})
    pasted = json.dumps([restored], ensure_ascii=False, indent=2)
    result = await flow.send({"restore_data": pasted})
    device = flow.saved()
    logged, written, pins = secret_records(start, keys)
    report(
        "K6 Configure -> Restore: the backup is restored, and no line of the driver's "
        "log shows its key",
        f"page {page_name(page)!r}; {type(result).__name__}; {describe(device)}; "
        + secrets_line(logged, written, pins, keys),
        page_name(page) == "Restore Configuration"
        and isinstance(result, SetupComplete)
        and device is not None
        and device.remote_api_key == RESTORED_KEY
        and is_seconds(device.wake_wait_sec, 1.0)
        and device.data == CODES
        and LOGS.ready()
        and bool(logged)
        and not written
        and not pins,
    )

    # no Broadlink address and no learned codes: the framework can't read the entry
    flow = Flow(saved=[saved_entry(**BL9_SAVED)])
    start = len(LOGS.entries)
    await flow.send()
    await flow.send({"choice": MAC, "action": "restore"})
    broken = {k: v for k, v in restored.items() if k not in ("address", "data")}
    pasted = json.dumps([broken], ensure_ascii=False, indent=2)
    again = await flow.send({"restore_data": pasted})
    device = flow.saved()
    warnings = [
        e
        for e in LOGS.entries[start:]
        if e.record.levelno == logging.WARNING
        and "Failed to deserialize device" in e.record.getMessage()
    ]
    warning = warnings[0] if len(warnings) == 1 else None
    logged, written, pins = secret_records(start, keys)
    report(
        "K7 a Restore the framework can't read: the page again with the pasted text, "
        "nothing restored; neither the framework's warning (the entry as a dict) nor "
        "the page shows the key in a line of the driver's log",
        f"answer {page_name(again)!r}, the pasted text shown again "
        f"{textarea(again, 'restore_data') == pasted}, an error {'error' in fields(again)}"
        f"; on disk {describe(device)}; the framework's warnings {len(warnings)}"
        + (
            f" (in the driver's log {LOGS.in_driver_log(warning.record)}, the key as "
            f"logged {RESTORED_KEY in warning.text}, as written "
            f"{RESTORED_KEY in (warning.driver or '')})"
            if warning
            else ""
        )
        + "; "
        + secrets_line(logged, written, pins, keys),
        page_name(again) == "Restore Configuration"
        and textarea(again, "restore_data") == pasted
        and "error" in fields(again)
        and device is not None
        and device.remote_api_key == OLD_KEY
        and is_seconds(device.wake_wait_sec, 2.0)
        and warning is not None
        and LOGS.in_driver_log(warning.record)
        and RESTORED_KEY in warning.text
        and warning.driver is not None
        and RESTORED_KEY not in warning.driver
        and LOGS.ready()
        and bool(logged)
        and not written
        and not pins,
    )


@dataclasses.dataclass
class Secrets:
    """A dataclass with fields named as the secrets are, for its repr (L5)."""

    name: str
    remote_api_key: str
    password: str


def log_record(name: str, level: int, msg: str, args: tuple) -> logging.LogRecord:
    """Return the record ``logging.getLogger(name).log(level, msg, *args)`` makes."""
    return logging.LogRecord(name, level, __file__, 1, msg, args, None)


def loads(text, times: int = 1):
    """Return ``text`` parsed as JSON ``times`` times over, or None if it isn't."""
    try:
        for _ in range(times):
            text = json.loads(text)
    except (TypeError, ValueError):
        return None
    return text


def run_filter(record: logging.LogRecord):
    """Return what log_redact's filter returns for ``record``, or what it raised."""
    try:
        return LOG_REDACT.RedactSecrets().filter(record)
    except Exception as err:  # pylint: disable=broad-except
        return err


async def case_l():
    """Check L: log_redact's patterns, its filter and install(), one by one."""
    if LOG_REDACT is None:
        report("L  log_redact.py", "not in the tested folder", False)
        return
    redact = LOG_REDACT.redact
    key, pin = "uc_L-k3y.9", "5317"  # used nowhere else
    secret = {"name": "TV", "remote_api_key": key, "password": pin}
    hidden = {"name": "TV", "remote_api_key": REDACTED, "password": REDACTED}

    def clean(text) -> bool:
        return isinstance(text, str) and key not in text and pin not in text

    got = redact(json.dumps(secret))
    report(
        "L1 plain JSON: both values hidden, nothing else changed",
        got,
        loads(got) == hidden,
    )

    # the Backup page as ucapi logs it: its text field holds JSON, escaped once
    page = {
        "kind": "event",
        "msg": "driver_setup_change",
        "msg_data": {
            "event_type": "SETUP",
            "state": "WAIT_USER_ACTION",
            "require_user_action": {
                "input": {
                    "title": {"en": "Configuration Backup"},
                    "settings": [
                        {
                            "id": "backup_data",
                            "label": {"en": "Configuration Data (copy this)"},
                            "field": {
                                "textarea": {"value": json.dumps([secret], indent=2)}
                            },
                        }
                    ],
                }
            },
        },
    }
    prefix = "[('127.0.0.1', 50000)] <-: "
    got = redact(prefix + json.dumps(ucapi_api.sanitize_json_message(page)))
    head, _, data = got.partition(" <-: ")
    message = loads(data) if f"{head} <-: " == prefix else None
    try:
        value = message["msg_data"]["require_user_action"]["input"]["settings"][0]
        value = value["field"]["textarea"]["value"]
    except (KeyError, IndexError, TypeError):
        value = None
    report(
        "L2 JSON escaped once in a JSON string: the Backup page's text field in ucapi's "
        "message log",
        around(got, [REDACTED, key, pin]),
        clean(got) and loads(value) == [hidden],
    )

    got = redact(json.dumps(json.dumps(json.dumps(secret))))
    report("L3 JSON escaped twice", got, loads(got, 3) == hidden)

    got = redact(repr(secret))
    try:
        parsed = ast.literal_eval(got)
    except (SyntaxError, ValueError):
        parsed = None
    report("L4 a dict's repr", got, parsed == hidden)

    got = redact(repr(Secrets("TV", key, pin)))
    # a value with a ' in it is shown in double quotes
    quoted = redact(repr(Secrets("TV's", key, f"it's {pin}")))
    report(
        "L5 a dataclass's repr, in single quotes or double",
        f"{got}; {quoted}",
        got == repr(Secrets("TV", REDACTED, REDACTED))
        and clean(quoted)
        and quoted.count(REDACTED) == 2
        and quoted.startswith('Secrets(name="TV\'s", '),
    )

    # what ucapi already hid: the text stays as it is, and so does the record
    text = json.dumps(
        {
            "kind": "req",
            "msg": "set_driver_user_data",
            "msg_data": {
                "input_values": {"remote_address": ADDRESS_A, PIN_ID: REDACTED}
            },
        }
    )
    args = (("127.0.0.1", 50000), text)
    record = log_record("ucapi.api", logging.DEBUG, "[%s] ->: %s", args)
    kept = run_filter(record)
    report(
        f"L6 ucapi's own {REDACTED} value: the text unchanged; the record keeps its "
        "message and args",
        f"text unchanged {redact(text) == text}; filter {kept!r}, args kept "
        f"{record.args is args}",
        redact(text) == text
        and kept is True
        and record.msg == "[%s] ->: %s"
        and record.args is args,
    )

    # the page's PIN field: "password" names a field, with no value to hide
    field = {
        "id": PIN_ID,
        "label": {"en": "Remote PIN"},
        "field": {"password": {"value": ""}},
    }
    texts = [
        json.dumps(field),
        repr(field),
        json.dumps(json.dumps(field)),
        json.dumps(ucapi_api.sanitize_json_message({"settings": [field]})),
    ]
    changed = [text for text in texts if redact(text) != text]
    report(
        'L7 the PIN field\'s definition ("password": {"value": ""}) is left as it is: '
        "as JSON, a repr, escaped, and as ucapi logs it",
        f"changed {changed}",
        not changed,
    )

    # ucapi's message log: a format string and its args, the secret in an arg
    text = json.dumps(
        {
            "kind": "req",
            "msg": "set_driver_user_data",
            "msg_data": {
                "input_values": {
                    "restore_data": json.dumps([secret], indent=2),
                    "note": "100% done",
                }
            },
        }
    )
    record = log_record(
        "ucapi.api", logging.DEBUG, "[%s] ->: %s", (("127.0.0.1", 50000), text)
    )
    formatted = record.getMessage()
    kept = run_filter(record)
    try:
        line = logging.Formatter("%(levelname)s %(message)s").format(record)
    except (TypeError, ValueError) as err:
        line = f"(cannot format: {err})"
    report(
        "L8 a record with %-args: the message as formatted, masked; the args dropped",
        f"filter {kept!r}, args {short(record.args)}, line {around(line, [REDACTED])}",
        kept is True
        and record.args is None
        and record.getMessage() == redact(formatted)
        and line == f"DEBUG {redact(formatted)}"
        and clean(line)
        and "100% done" in line,
    )

    args = (IP,)
    record = log_record("rm", logging.INFO, "Connecting to %s", args)
    kept = run_filter(record)
    report(
        "L9 a record without secrets: kept as it is, its args too",
        f"filter {kept!r}, message {record.msg!r}, args kept {record.args is args}",
        kept is True and record.msg == "Connecting to %s" and record.args is args,
    )

    args = ("remote_api_key", MAC)
    record = log_record("setup", logging.WARNING, "No %s saved for %s", args)
    kept = run_filter(record)
    report(
        "L10 a record that names a key but shows no value: kept as it is, its args too",
        f"filter {kept!r}, message {record.msg!r}, args kept {record.args is args}",
        kept is True and record.msg == "No %s saved for %s" and record.args is args,
    )

    args = ("many",)
    record = log_record("rm", logging.WARNING, "%d devices", args)
    kept = run_filter(record)
    report(
        "L11 a record that can't be formatted: the filter keeps it as it is, without "
        "raising (the handler reports it, as before)",
        f"filter {kept!r}, args kept {record.args is args}",
        kept is True and record.args is args,
    )

    # install() as driver.py calls it, on root handlers of the test's own
    root = logging.getLogger()
    saved = root.handlers[:]
    first = logging.StreamHandler(io.StringIO())
    second = logging.StreamHandler(io.StringIO())
    root.handlers = [first, second]
    try:
        LOG_REDACT.install()
        LOG_REDACT.install()
        counts = [
            sum(isinstance(item, LOG_REDACT.RedactSecrets) for item in handler.filters)
            for handler in (first, second)
        ]
    finally:
        root.handlers = saved
    report(
        "L12 install() twice: one filter on each root handler",
        f"filters per handler {counts}",
        counts == [1, 1],
    )


BL8: list = []  # tests/bl8_harness.py, once loaded


def bl8_harness():
    """Return tests/bl8_harness.py, loaded once.

    Loading it puts its python-broadlink fakes in place; the setup cases keep their
    own, so those are put back. :func:`bl8_fakes` switches to its fakes for R.
    """
    if not BL8:
        saved = broadlink.hello, broadlink.xdiscover
        spec = importlib.util.spec_from_file_location(
            "bl8_harness", os.path.join(TESTS, "bl8_harness.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        broadlink.hello, broadlink.xdiscover = saved
        BL8.append(module)
    return BL8[0]


@contextlib.contextmanager
def bl8_fakes(harness):
    """Use tests/bl8_harness.py's python-broadlink fakes meanwhile."""
    saved = broadlink.hello, broadlink.xdiscover
    broadlink.hello, broadlink.xdiscover = harness.fake_hello, harness.fake_xdiscover
    try:
        yield
    finally:
        broadlink.hello, broadlink.xdiscover = saved


def real_driver(folder: str):
    """Return the integration driver as driver.py makes it, configured from ``folder``."""
    driver = broadlink_driver.BroadlinkDriver(
        device_class=rm.Broadlink,
        entity_classes=[ir_emitter.BroadlinkIREmitter],
        driver_id="broadlink_driver",
        require_connection_before_registry=True,
        loop=asyncio.get_running_loop(),
    )
    driver.config_manager = BroadlinkConfigManager(
        folder,
        driver.on_device_added,
        driver.on_device_removed,
        config_class=BroadlinkConfig,
    )
    return driver


def task_state(task) -> str:
    """Describe an asyncio task for a report line."""
    if task is None:
        return "none"
    if task.cancelled():
        return "cancelled"
    return "finished" if task.done() else "running"


async def stop_driver(driver, before):
    """Stop every task started since ``before``: retries, adds, handlers.

    The driver's retrying connects and the device being added, and anything else the case
    left running (ucapi-framework's subscribe handlers, say).
    """
    tasks = {task for task in driver._connect_tasks.values() if not task.done()}
    if driver._pending_setup_task is not None:
        tasks.add(driver._pending_setup_task)
    tasks |= asyncio.all_tasks() - set(before) - {asyncio.current_task()}
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# R: a device must be connected this long after its retry's next attempt was due: the
# attempt itself (python-broadlink's calls run in worker threads) and this test's polling
CONNECT_SLACK = 1.0
# broadlink_driver's log of BL-1's retry (DEBUG and INFO)
RETRY_FAILED = re.compile(
    r"\[(\w+)\] Connect attempt \d+ failed, retrying in ([\d.]+)s"
)
RETRY_CONNECTED = re.compile(r"\[(\w+)\] (?:Connected|Reconnected) after \d+")


def retry_delays(start: int) -> list[float]:
    """Return the delays the Broadlink's retrying connect logged since ``start``.

    broadlink_driver logs "[<id>] Connect attempt N failed, retrying in X.Xs" after each
    failed attempt of its retry, and only there.
    """
    delays = []
    for entry in LOGS.entries[start:]:
        found = RETRY_FAILED.search(entry.text)
        if entry.record.name == broadlink_driver.__name__ and found and found[1] == MAC:
            delays.append(float(found[2]))
    return delays


def retry_connected(start: int) -> bool:
    """Return whether the retrying connect logged, since ``start``, that it connected."""
    for entry in LOGS.entries[start:]:
        found = RETRY_CONNECTED.search(entry.text)
        if entry.record.name == broadlink_driver.__name__ and found and found[1] == MAC:
            return True
    return False


def retry_of(driver) -> tuple:
    """Return the Broadlink's retrying connect and the device instance it is for.

    Fix round 4 records the instance (``_connect_task_device``); older code doesn't.
    """
    owners = getattr(driver, "_connect_task_device", None) or {}
    return driver._connect_tasks.get(MAC), owners.get(MAC)


def own_retry(driver, device) -> bool:
    """Return whether ``device`` has a retrying connect of its own, running."""
    task, owner = retry_of(driver)
    return (
        device is not None and task is not None and not task.done() and owner is device
    )


def describe_retry(driver, device) -> str:
    """Describe the Broadlink's retrying connect, and whose it is, for a report line."""
    task, owner = retry_of(driver)
    if task is None:
        return "no retry"
    if owner is None:
        whose = "for no recorded device"
    else:
        whose = "its own" if owner is device else "another instance's"
    return f"a retry {task_state(task)}, {whose}"


async def until(condition, limit: float) -> bool:
    """Wait until ``condition()`` holds, at most ``limit`` seconds; return whether it did."""
    loop = asyncio.get_running_loop()
    end = loop.time() + limit
    while not condition():
        if loop.time() >= end:
            return False
        await asyncio.sleep(0.01)
    return True


async def first_attempt(start: int) -> bool:
    """Wait (at most 2 s) for a failed attempt of the retry since ``start``."""
    return await until(lambda: bool(retry_delays(start)), 2.0)


async def back_and_connected(harness, driver, device, start: int) -> tuple[bool, str]:
    """Let the Broadlink answer again; wait for the retry to connect ``device``.

    ``start``: where the log stood before ``device``'s retry began. The retry's next
    attempt is due within the delay it logged after its last failed one (its interval),
    so the device must be connected by then, plus CONNECT_SLACK, and the retry must log
    that it connected it. Return whether all that held, and a description.
    """
    delays = retry_delays(start)
    interval = delays[-1] if delays else None
    mark = len(LOGS.entries)
    loop = asyncio.get_running_loop()
    harness.NET = harness.Net(up_at=0.0)
    back = loop.time()
    limit = (interval or 0.0) + CONNECT_SLACK
    in_time = await until(
        lambda: device is not None
        and driver._device_instances.get(MAC) is device
        and device.is_connected,
        limit,
    )
    took = loop.time() - back
    by_retry = retry_connected(mark)
    return interval is not None and in_time and by_retry, (
        f"the retry's interval {interval} s (after {len(delays)} failed attempts); "
        + (f"connected after {took:.2f} s" if in_time else f"not within {limit:.1f} s")
        + f", logged by the retry {by_retry}"
    )


async def start_driver(driver) -> None:
    """Start the driver as driver.py does: register the devices, then retry."""
    await driver.register_all_device_instances()
    driver.connect_all_with_retry()


async def added(driver) -> bool | None:
    """Wait (at most 10 s) for the framework's add of a device; return its connect's result.

    ucapi-framework adds a device in a task: it creates it, makes one connect attempt,
    and returns whether that worked (None: no such task, or it didn't end).
    """
    task = driver._pending_setup_task
    if task is None:
        return None
    await asyncio.wait({task}, timeout=10)
    if not task.done() or task.cancelled() or task.exception() is not None:
        return None
    return task.result()


async def send_backup(flow, entry: dict):
    """Paste ``entry`` on the restore page as the Backup page shows it; return the answer."""
    return await flow.send(
        {"restore_data": json.dumps([entry], ensure_ascii=False, indent=2)}
    )


async def remote_request(flow, msg: str, entity_ids: list[str]) -> None:
    """Send the Remote's subscribe_events or unsubscribe_events through ucapi.

    ucapi records it and emits its event; ucapi-framework's handler then runs as a task,
    which this waits for (at most 10 s), its connect attempts included.
    """
    before = asyncio.all_tasks()
    message = {
        "kind": "req",
        "id": next(REQUEST_IDS),
        "msg": msg,
        "msg_data": {"entity_ids": entity_ids},
    }
    await flow.api._process_ws_message(flow.remote, message)
    queue = flow.api._ws_contexts[flow.remote].outgoing
    sent = []
    while not queue.empty():
        sent.append(json.loads(queue.get_nowait()))
    flow._core_reads(sent)  # no page is expected here; one would be kept all the same
    handlers = [
        task
        for task in asyncio.all_tasks() - before
        if getattr(task.get_coro(), "__name__", "") in ("subscribe", "unsubscribe")
    ]
    if handlers:
        await asyncio.wait(handlers, timeout=10)


async def remote_event(flow, msg: str) -> None:
    """Send the Remote's event ``msg`` (enter_standby, exit_standby) through ucapi."""
    await flow._from_remote({"kind": "event", "msg": msg, "cat": "DEVICE"})


async def case_r():
    """Check R1, R2: a Restore re-creates the running device; the new one keeps retrying.

    The real driver, configuration manager and device behind the setup flow, with
    tests/bl8_harness.py's python-broadlink fakes; the Restore goes through ucapi, as
    Configure -> Restore does. The Broadlink can't be reached, so the driver keeps
    retrying the running device, and the remote stays awake. The new device's one
    connect by the framework fails too. Then the Remote subscribes to the IR emitter (as
    ucapi-framework expects the core to after a setup), and the Broadlink answers, at
    another address than the saved one (a DHCP change), which the connected device writes
    to the configuration.

    Before fix round 3 the framework kept the running device with its old configuration
    object: the restored wake wait applied only after a driver restart, and that
    device's address update wrote its old configuration, learned codes included, over
    the restored one. In fix round 3 the new device got no retry: it stayed disconnected
    until the next wake.
    """
    harness = bl8_harness()
    before = asyncio.all_tasks()
    with bl8_fakes(harness):
        harness.NET = harness.Net(up_at=None)  # the Broadlink can't be reached
        flow = Flow(
            saved=[saved_entry(address=NEW_IP, data=OLD_CODES, wake_wait_sec=4.0)],
            make_driver=real_driver,
        )
        driver = flow.driver
        try:
            await start_driver(driver)
            old = driver._device_instances.get(MAC)
            old_task = driver._connect_tasks.get(MAC)
            start = len(LOGS.entries)
            await flow.send()
            await flow.send({"choice": MAC, "action": "restore"})
            restored = saved_entry(
                address=NEW_IP, wake_wait_sec=1.0, remote_api_key=RESTORED_KEY
            )
            result = await send_backup(flow, restored)
            # the new device exists by now, and the old device's retry was stopped
            # before it: only the new device's retry logs attempts from here
            restored_at = len(LOGS.entries)
            framework = await added(driver)
            new = driver._device_instances.get(MAC)
            wait = new._wake_wait() if new is not None else None
            if old_task is not None:
                await asyncio.wait({old_task}, timeout=2)
            attempted = await first_attempt(restored_at)
            created = [
                e.driver or ""
                for e in LOGS.entries[start:]
                if e.record.name == rm.__name__
                and "Wait for the remote's Wi-Fi" in e.text
                and LOGS.in_driver_log(e.record)
            ]
            report(
                "R1 Configure -> Restore with another wake wait while the device runs, the "
                "Broadlink unreachable, the remote awake: a new device, with the restored "
                "wake wait at once; the old device's retry cancelled; the framework's one "
                "connect fails, and the new device keeps retrying, with a retry of its own",
                f"{type(result).__name__}; a new device {new is not None and new is not old}"
                f", its wake wait {wait}; creation lines in the driver's log "
                f"{[around(line, ['up to']) for line in created]}; the old device's retry "
                f"{task_state(old_task)}; the framework's connect "
                f"{'failed' if framework is False else framework}; now "
                f"{describe_retry(driver, new)}, a failed attempt logged {attempted}",
                isinstance(result, SetupComplete)
                and old is not None
                and new is not None
                and new is not old
                and wait == 1.0
                and bool(created)
                and "up to 1.0s after a wake" in created[-1]
                and old_task is not None
                and old_task.cancelled()
                and framework is False
                and own_retry(driver, new)
                and attempted,
            )

            # the Remote subscribes to the IR emitter, as after a setup: the framework's
            # own three connect attempts for it fail meanwhile
            await remote_request(flow, "subscribe_events", [EMITTER])
            # the Broadlink answers again, at another address than the saved one
            connected, text = await back_and_connected(
                harness, driver, new, restored_at
            )
            report(
                "R1 ... the Remote subscribes to the IR emitter, then the Broadlink answers "
                "again: the retry connects the new device within its interval",
                f"a new device {new is not None and new is not old}; {text}",
                new is not None and new is not old and connected,
            )

            current = driver._device_instances.get(MAC)
            device = flow.saved()
            report(
                "R2 ... the Broadlink answered at another address: the new device saves "
                "it, and the restored configuration stays, learned codes included",
                f"the device connected {current is not None and current.is_connected}; "
                f"on disk {describe(device)}",
                current is not None
                and current is new
                and current.is_connected
                and device is not None
                and device.address == IP
                and device.data == CODES
                and is_seconds(device.wake_wait_sec, 1.0)
                and device.remote_api_key == RESTORED_KEY
                and device.remote_address == ADDRESS_A,
            )
            # ucapi-framework registers a device's entities when it connects, for the
            # subscriptions the Remote made meanwhile (its on_device_connected)
            configured = await until(
                lambda: driver.api.configured_entities.contains(EMITTER), 2.0
            )
            available = driver.api.available_entities.contains(EMITTER)
            entity = driver.api.configured_entities.get(EMITTER)
            bound = entity is not None and getattr(entity, "_device", None) is new
            report(
                "R2 ... and the IR emitter the Remote subscribed to after the Restore is "
                "registered (available and configured) once the retry has connected the "
                "new device, and is the new device's",
                f"available {available}, configured {configured}, the new device's "
                f"{bound}",
                available and configured and bound,
            )
        finally:
            await stop_driver(driver, before)


async def case_r_first_setup():
    """Check R3: the integration's first setup restores a backup; nothing ran before.

    As after a reinstall: no device configured, so no device and no retry before the
    setup. The framework's first setup clears the configuration, then offers to restore
    a backup. The Broadlink can't be reached; the remote is awake.
    """
    harness = bl8_harness()
    before = asyncio.all_tasks()
    with bl8_fakes(harness):
        harness.NET = harness.Net(up_at=None)
        flow = Flow(saved=None, make_driver=real_driver)
        driver = flow.driver
        try:
            await start_driver(driver)
            devices = len(driver._device_instances)
            start = len(LOGS.entries)
            prompt = await flow.start(reconfigure=False)
            page = await flow.send({"restore_from_backup": "true"})
            result = await send_backup(flow, saved_entry(**BL9_SAVED))
            framework = await added(driver)
            new = driver._device_instances.get(MAC)
            attempted = await first_attempt(start)
            state, own = describe_retry(driver, new), own_retry(driver, new)
            connected, text = await back_and_connected(harness, driver, new, start)
            report(
                "R3 the integration's first setup, nothing configured before (as after a "
                "reinstall), restores a backup while the Broadlink can't be reached: the "
                "device keeps retrying, a retry of its own, and connects once the "
                "Broadlink answers, within the retry's interval",
                f"devices before {devices}; restore prompt "
                f"{'restore_from_backup' in fields(prompt)}, then {page_name(page)!r}, "
                f"then {type(result).__name__}; the framework's connect "
                f"{'failed' if framework is False else framework}; {state}, a failed "
                f"attempt logged {attempted}; {text}",
                devices == 0
                and "restore_from_backup" in fields(prompt)
                and page_name(page) == "Restore Configuration"
                and isinstance(result, SetupComplete)
                and new is not None
                and framework is False
                and own
                and attempted
                and connected,
            )
        finally:
            await stop_driver(driver, before)


async def case_r_reset():
    """Check R4: Configure -> Reset, then its restore prompt restores a backup.

    The Broadlink can't be reached and the remote is awake, so the device is retrying
    when the Reset removes it (the framework's clear_devices).
    """
    harness = bl8_harness()
    before = asyncio.all_tasks()
    with bl8_fakes(harness):
        harness.NET = harness.Net(up_at=None)
        flow = Flow(saved=[saved_entry(**BL9_SAVED)], make_driver=real_driver)
        driver = flow.driver
        try:
            await start_driver(driver)
            old = driver._device_instances.get(MAC)
            old_task = driver._connect_tasks.get(MAC)
            await flow.send()
            prompt = await flow.send({"choice": MAC, "action": "reset"})
            left = driver._connect_tasks.get(MAC)  # right after the Reset, no waiting
            left_text = "no retry" if left is None else f"a retry {task_state(left)}"
            removed = MAC not in driver._device_instances
            if old_task is not None:
                await asyncio.wait({old_task}, timeout=2)
            report(
                "R4 Configure -> Reset while the device is retrying (the Broadlink "
                "unreachable, the remote awake): the device removed, its retry cancelled "
                "with it, none left behind",
                f"answer: the restore prompt {'restore_from_backup' in fields(prompt)}; "
                f"the device removed {removed}; right after the Reset {left_text} for "
                f"the Broadlink; its retry then {task_state(old_task)}",
                "restore_from_backup" in fields(prompt)
                and old is not None
                and removed
                and old_task is not None
                and old_task.cancelled()
                and left is None,
            )

            start = len(LOGS.entries)
            page = await flow.send({"restore_from_backup": "true"})
            result = await send_backup(flow, saved_entry(**BL9_SAVED))
            framework = await added(driver)
            new = driver._device_instances.get(MAC)
            attempted = await first_attempt(start)
            state, own = describe_retry(driver, new), own_retry(driver, new)
            connected, text = await back_and_connected(harness, driver, new, start)
            report(
                "R4 ... then the restore prompt restores the backup: a new device that "
                "keeps retrying, a retry of its own, and connects once the Broadlink "
                "answers, within the retry's interval",
                f"{page_name(page)!r}, then {type(result).__name__}; a new device "
                f"{new is not None and new is not old}; the framework's connect "
                f"{'failed' if framework is False else framework}; {state}, a failed "
                f"attempt logged {attempted}; {text}",
                page_name(page) == "Restore Configuration"
                and isinstance(result, SetupComplete)
                and new is not None
                and new is not old
                and framework is False
                and own
                and attempted
                and connected,
            )
        finally:
            await stop_driver(driver, before)


async def case_r_remove():
    """Check R5: Configure -> Remove while the device is retrying.

    The Broadlink can't be reached and the remote is awake. Nothing creates a device
    afterwards, so only BL-9's remove_device stops the removed device's retry before
    its current delay ends.
    """
    harness = bl8_harness()
    before = asyncio.all_tasks()
    with bl8_fakes(harness):
        harness.NET = harness.Net(up_at=None)
        flow = Flow(saved=[saved_entry(**BL9_SAVED)], make_driver=real_driver)
        driver = flow.driver
        try:
            await start_driver(driver)
            old = driver._device_instances.get(MAC)
            old_task = driver._connect_tasks.get(MAC)
            await flow.send()
            result = await flow.send({"choice": MAC, "action": "remove"})
            left = driver._connect_tasks.get(MAC)  # right after the Remove, no waiting
            left_text = "no retry" if left is None else f"a retry {task_state(left)}"
            removed = MAC not in driver._device_instances
            if old_task is not None:
                await asyncio.wait({old_task}, timeout=2)
            device = flow.saved()
            report(
                "R5 Configure -> Remove while the device is retrying (the Broadlink "
                "unreachable, the remote awake): the device removed, its retry cancelled "
                "with it, none left behind",
                f"{type(result).__name__}; the device removed {removed}, on disk "
                f"{describe(device)}; right after the Remove {left_text} for the "
                f"Broadlink; its retry then {task_state(old_task)}",
                isinstance(result, SetupComplete)
                and old is not None
                and removed
                and device is None
                and old_task is not None
                and old_task.cancelled()
                and left is None,
            )
        finally:
            await stop_driver(driver, before)


async def case_r_update():
    """Check R6: an Update saved while the Broadlink can't be reached.

    setup.py's query_device has to reach the Broadlink, so it answers while the Update
    starts (this test's own python-broadlink fakes, as the setup cases use them); it can't
    be reached when the Update is saved, which re-creates the device (tests/bl8_harness's
    fakes from then on). The remote is awake.
    """
    harness = bl8_harness()
    before = asyncio.all_tasks()
    flow = Flow(saved=[saved_entry(**BL9_SAVED)], make_driver=real_driver)
    driver = flow.driver
    try:
        await start_driver(driver)
        old = driver._device_instances.get(MAC)
        await until(lambda: old is not None and old.is_connected, 2.0)
        was_connected = old is not None and old.is_connected
        await flow.open_page("update")
        with bl8_fakes(harness):
            harness.NET = harness.Net(up_at=None)
            start = len(LOGS.entries)
            result = await flow.answer(ADDRESS_A, PIN, wake_wait_sec="3")
            new = driver._device_instances.get(MAC)
            wait = new._wake_wait() if new is not None else None
            attempted = await first_attempt(start)
            state, own = describe_retry(driver, new), own_retry(driver, new)
            connected, text = await back_and_connected(harness, driver, new, start)
        report(
            "R6 Configure -> Update, the Broadlink found by the setup but unreachable when "
            "the Update is saved (the remote awake): a new device with the new wake wait "
            "keeps retrying, a retry of its own, and connects once the Broadlink answers, "
            "within the retry's interval",
            f"the device connected before the Update {was_connected}; "
            f"{type(result).__name__}; a new device {new is not None and new is not old}"
            f", its wake wait {wait}; {state}, a failed attempt logged {attempted}; "
            f"{text}",
            was_connected
            and isinstance(result, SetupComplete)
            and new is not None
            and new is not old
            and wait == 3.0
            and own
            and attempted
            and connected,
        )
    finally:
        await stop_driver(driver, before)


async def case_r_standby():
    """Check R7: a device created while the remote is in standby retries after the wake.

    The remote goes to standby (enter_standby through ucapi, as the core sends it; the
    framework's handler runs), then Configure -> Restore re-creates the device: the web
    configurator works while the remote sleeps. The Broadlink can't be reached.
    """
    harness = bl8_harness()
    before = asyncio.all_tasks()
    with bl8_fakes(harness):
        harness.NET = harness.Net(up_at=None)
        flow = Flow(saved=[saved_entry(**BL9_SAVED)], make_driver=real_driver)
        driver = flow.driver
        try:
            await start_driver(driver)
            old = driver._device_instances.get(MAC)
            old_task = driver._connect_tasks.get(MAC)
            await remote_event(flow, "enter_standby")
            asleep = await until(
                lambda: not driver._awake and (old_task is None or old_task.done()),
                2.0,
            )
            await flow.send()
            await flow.send({"choice": MAC, "action": "restore"})
            result = await send_backup(flow, saved_entry(**BL9_SAVED))
            framework = await added(driver)
            new = driver._device_instances.get(MAC)
            task, _ = retry_of(driver)
            report(
                "R7 Configure -> Restore while the remote is in standby, the Broadlink "
                "unreachable: a new device, and no retry for it while the remote sleeps",
                f"in standby {asleep} (the old retry {task_state(old_task)}); "
                f"{type(result).__name__}; a new device "
                f"{new is not None and new is not old}; the framework's connect "
                f"{'failed' if framework is False else framework}; "
                f"{describe_retry(driver, new)}",
                asleep
                and isinstance(result, SetupComplete)
                and new is not None
                and new is not old
                and framework is False
                and task is None,
            )

            start = len(LOGS.entries)
            await remote_event(flow, "exit_standby")
            started = await until(lambda: own_retry(driver, new), 2.0)
            awake, state = driver._awake, describe_retry(driver, new)
            attempted = await first_attempt(start)
            connected, text = await back_and_connected(harness, driver, new, start)
            report(
                "R7 ... the next exit_standby starts its retry, which connects it once the "
                "Broadlink answers, within the retry's interval",
                f"awake {awake}; {state}, a failed attempt logged {attempted}; {text}",
                awake and started and attempted and connected,
            )
        finally:
            await stop_driver(driver, before)


async def case_r_resubscribe():
    """Check R8: the device the framework re-creates on a subscribe gets its own retry.

    ucapi-framework drops a device whose entities the Remote all unsubscribed from
    without calling remove_device (it pops it itself), so the dropped device's retry is
    left behind, waiting; a later subscribe creates the device again. Here the Broadlink
    stops answering while the remote is awake, and a press finds it (wake wait 0 s: no
    wake window), so BL-7's background retry waits 5 s before its first attempt; then
    the Remote unsubscribes from the IR emitter and subscribes again. Fix round 3 started
    no retry for a new device, and its _start_connect_task left a running retry alone,
    whatever instance it was for: that one then ends at its next check, without
    connecting the new device.
    """
    harness = bl8_harness()
    before = asyncio.all_tasks()
    with bl8_fakes(harness):
        harness.NET = harness.Net(up_at=0.0)  # the Broadlink answers at first
        flow = Flow(saved=[saved_entry(wake_wait_sec=0.0)], make_driver=real_driver)
        driver = flow.driver
        try:
            await start_driver(driver)
            first = driver._device_instances.get(MAC)
            await remote_request(flow, "subscribe_events", [EMITTER])
            subscribed = driver.api.configured_entities.contains(EMITTER)
            harness.NET = harness.Net(up_at=None)  # the Broadlink stops answering
            status = None
            if first is not None:
                status = await first.send_command(code=b"\x26\x00\x04\x00\x01\x02")
            stale, owner = retry_of(driver)
            if stale is not None and owner is first:
                press_retry = "a background retry for that device"
            else:
                press_retry = describe_retry(driver, first)
            await remote_request(flow, "unsubscribe_events", [EMITTER])
            dropped = MAC not in driver._device_instances
            left = task_state(stale)
            start = len(LOGS.entries)
            await remote_request(flow, "subscribe_events", [EMITTER])
            new = driver._device_instances.get(MAC)
            if stale is not None:
                await asyncio.wait({stale}, timeout=2)
            stale_state = task_state(stale)  # now, not when the line is reported
            state, own = describe_retry(driver, new), own_retry(driver, new)
            attempted = await first_attempt(start)
            connected, text = await back_and_connected(harness, driver, new, start)
            report(
                "R8 the Broadlink unreachable, a press starts BL-7's background retry; the "
                "Remote unsubscribes from the IR emitter (the framework drops the device "
                "itself, its retry left waiting), then subscribes again (the framework "
                "creates the device again): the left-behind retry is cancelled, the new "
                "device gets a retry of its own and connects once the Broadlink answers, "
                "within the retry's interval",
                f"subscribed {subscribed}; the press "
                f"{getattr(status, 'name', status)}, then {press_retry}; "
                f"unsubscribed: the device dropped {dropped}, its retry {left}; "
                f"subscribed again: a new device {new is not None and new is not first}, "
                f"the left-behind retry {stale_state}, now {state}, a failed attempt "
                f"logged {attempted}; {text}",
                subscribed
                and status == StatusCodes.SERVICE_UNAVAILABLE
                and stale is not None
                and owner is first
                and dropped
                and left == "running"
                and new is not None
                and new is not first
                and stale_state == "cancelled"
                and own
                and attempted
                and connected,
            )
        finally:
            await stop_driver(driver, before)


def setup_flow_of(flow):
    """Return the setup flow object the framework's handler made (on its first message)."""
    return inspect.getclosurevars(flow.handler).nonlocals.get("setup_flow")


def actions(page) -> tuple[list[str], str | None]:
    """Return the "Configuration mode" page's action ids and the one selected."""
    for setting in getattr(page, "settings", None) or []:
        if setting.get("id") == "action":
            dropdown = setting["field"]["dropdown"]
            return [item["id"] for item in dropdown["items"]], dropdown.get("value")
    return [], None


def is_configure_page(page) -> bool:
    """Return whether ``page`` is BL-11's settings page: its title, the wait list, its help."""
    return (
        isinstance(page, RequestUserInput)
        and page_name(page) == "Configure RM4 mini"
        and [s["id"] for s in page.settings] == ["wake_wait_sec", "wake_wait_sec_help"]
        and wait_list(page) is not None
    )


def unchanged_but_wait(saved: BroadlinkConfig | None, address: str = IP) -> bool:
    """Return whether everything but the wait is still as ``saved_entry`` saved it."""
    return (
        saved is not None
        and saved.address == address
        and saved.data == CODES
        and saved.remote_address == ADDRESS_A
        and saved.remote_api_key == OLD_KEY
        and saved.name == "RM4 mini"
    )


async def configure(flow, wait: str | None):
    """Open "Configure selected device" for the Broadlink; answer ``wait`` unless None.

    Return (the first page, the settings page, the answer to it or None).
    """
    first = await flow.send()
    page = await flow.send({"choice": MAC, "action": "configure"})
    if wait is None:
        return first, page, None
    return first, page, await flow.send({"wake_wait_sec": wait})


async def case_x():
    """Check X: BL-11's "Configure selected device" on the first page.

    The action opens the chosen Broadlink's settings page (the wait list only) and
    saves it in place: no device search, no Remote search, no key request, nothing
    else changed, and the running device uses the new value at once, also when the
    framework re-created it from a copy of its configuration (after the Remote
    unsubscribed from and subscribed to its entities again).
    """
    # X1 the action list, with a Broadlink configured and with none
    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    with_device = actions(await flow.send())
    empty = Flow(saved=[])
    without = actions(await empty.send())
    report(
        "X1 the first page with a Broadlink configured lists Configure selected device "
        "second, selected; with none configured the list is the framework's own, on Add",
        f"configured: {with_device}; none: {without}",
        with_device
        == (
            ["add", "configure", "update", "remove", "reset", "backup", "restore"],
            "configure",
        )
        and without == (["add", "restore"], "add"),
    )

    # X2 Configure -> 5 s: saved in place, nothing else, no searches, both pauses
    calls = Counter()
    real_remotes, real_xdiscover = broadlink_setup.discover_remotes, broadlink.xdiscover
    real_discover = broadlink.discover

    async def counted_remotes(*args, **kwargs):
        calls["remotes"] += 1
        return await real_remotes(*args, **kwargs)

    def counted_xdiscover(*args, **kwargs):
        calls["broadlink at an address"] += 1
        return real_xdiscover(*args, **kwargs)

    def counted_discover(*args, **kwargs):
        calls["broadlink search"] += 1
        return real_discover(*args, **kwargs)

    broadlink_setup.discover_remotes = counted_remotes
    broadlink.xdiscover = counted_xdiscover
    broadlink.discover = counted_discover
    try:
        flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
        SETUP_ASYNCIO.pauses.clear()
        start = len(LOGS.entries)
        _, page, done = await configure(flow, "5")
        pauses = list(SETUP_ASYNCIO.pauses)
        saved = flow.saved()
        lines = [t for t in LOGS.texts(start) if "Saved for" in t]
    finally:
        broadlink_setup.discover_remotes = real_remotes
        broadlink.xdiscover = real_xdiscover
        broadlink.discover = real_discover
    report(
        "X2 Configure, 5 s: the settings page shows the saved 4 s; the answer ends the "
        "setup and saves 5 s in place, everything else as it was; no device search, no "
        "Remote search, no key request; a 1 s pause before the page and before the end, "
        "as the framework makes; one log line",
        f"page {page_name(page)!r}, shown {wait_shown(page)!r}; answer "
        f"{type(done).__name__}; saved: {describe(saved)}; searches {dict(calls)}, key "
        f"requests {len(KEY_REQUESTS)}; pauses {pauses}; log {lines}",
        is_configure_page(page)
        and wait_shown(page) == "4"
        and isinstance(done, SetupComplete)
        and saved is not None
        and is_seconds(saved.wake_wait_sec, 5.0)
        and unchanged_but_wait(saved)
        and not calls
        and not KEY_REQUESTS
        and pauses == [1.0, 1.0]
        and lines == ["INFO setup: Saved for RM4 mini: wait for Wi-Fi after a wake 5 s"],
    )

    # X3 a value saved between whole seconds is shown rounded; Next untouched saves that
    flow = Flow(saved=[saved_entry(wake_wait_sec=2.5)])
    _, page, _ = await configure(flow, None)
    shown = untouched(flow.waiting, "wake_wait_sec")
    done = await flow.send({"wake_wait_sec": shown})
    saved = flow.saved()
    report(
        "X3 a saved 2.5 s (a hand-edited or restored file): the page shows 3 s, and "
        "Next without touching the list saves 3 s",
        f"shown {shown!r}; answer {type(done).__name__}; saved {describe(saved)}",
        shown == "3"
        and isinstance(done, SetupComplete)
        and saved is not None
        and is_seconds(saved.wake_wait_sec, 3.0),
    )

    # X4 the lowest item, and an answer that only another client could send (the
    # Remote's core refuses an answer that is no item of the list or leaves it out; S
    # checks the list's items against the core's model)
    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    await configure(flow, None)
    done = await flow.send({"wake_wait_sec": "0"})
    first_saved = flow.saved()
    flow = Flow(saved=[saved_entry(wake_wait_sec=2.0)])  # not the default (4 s)
    await configure(flow, None)
    direct = await setup_flow_of(flow).handle_driver_setup(UserDataResponse({}))
    kept = flow.saved()
    report(
        "X4 0 s (no wait) is saved as 0 s; an answer without the wait (no core sends it) "
        "keeps the saved 2 s, not the default 4 s",
        f"0: {type(done).__name__}, saved {describe(first_saved)}; no wait: "
        f"{type(direct).__name__}, saved {describe(kept)}",
        isinstance(done, SetupComplete)
        and is_seconds(first_saved.wake_wait_sec, 0.0)
        and isinstance(direct, SetupComplete)
        and is_seconds(kept.wake_wait_sec, 2.0),
    )

    # X5 an unknown Broadlink, one removed between the two pages, a write that fails
    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    await flow.send()
    unknown = await setup_flow_of(flow).handle_driver_setup(
        UserDataResponse({"choice": "ffffffffffff", "action": "configure"})
    )
    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    await configure(flow, None)
    flow.manager.remove(MAC)
    gone = await flow.send({"wake_wait_sec": "5"})
    removed = flow.saved()
    cleared = setup_flow_of(flow)._configure_id is None
    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    await configure(flow, None)
    before_text = flow.file_text()
    real_path = flow.manager._cfg_file_path
    flow.manager._cfg_file_path = flow.folder  # a folder: the write fails (OSError)
    start = len(LOGS.entries)
    try:
        failed = await flow.send({"wake_wait_sec": "5"})
    finally:
        flow.manager._cfg_file_path = real_path
    failed_lines = [t for t in LOGS.texts(start) if "Saved for" in t]
    in_memory = next(iter(flow.manager.all())).wake_wait_sec
    cleared = cleared and setup_flow_of(flow)._configure_id is None
    report(
        "X5 an unknown Broadlink: an error; the Broadlink removed before the answer: an "
        "error, nothing written back; the configuration file can't be written: an error, "
        "no Saved line, the file as it was and the old value back in memory (the device "
        "must not use, and a later write must not save, a value the user saw fail); the "
        "settings page's flag cleared after each error",
        f"unknown {type(unknown).__name__}; removed {type(gone).__name__}, then "
        f"{describe(removed)}; write fails {type(failed).__name__}, Saved lines "
        f"{failed_lines}, file unchanged {flow.file_text() == before_text}, in memory "
        f"{in_memory!r}; flag cleared {cleared}",
        isinstance(unknown, SetupError)
        and isinstance(gone, SetupError)
        and removed is None
        and isinstance(failed, SetupError)
        and not failed_lines
        and flow.file_text() == before_text
        and in_memory == 4.0
        and cleared,
    )

    # X6 a setup that ends on the settings page leaves nothing behind
    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    await configure(flow, None)
    await flow.abort()
    stray = await setup_flow_of(flow).handle_driver_setup(
        UserDataResponse({"wake_wait_sec": "5"})
    )
    stray_saved = flow.saved()
    after_cancel = await flow.send()
    update_page = await flow.send({"choice": MAC, "action": "update"})
    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    await configure(flow, None)
    restarted = await flow.send()  # no abort: the next setup just starts
    update_again = await flow.send({"choice": MAC, "action": "update"})
    report(
        "X6 the settings page left with Cancel: an answer after it is not saved; the next "
        "setup's first page is Configuration mode and its Update goes on to the device "
        "search (not to the settings page's handler); the same when the page is left "
        "open and a new setup starts",
        f"after Cancel: a stray answer {type(stray).__name__}, saved "
        f"{describe(stray_saved)}; then {page_name(after_cancel)!r}, "
        f"{page_name(update_page)!r}; after a new setup: {page_name(restarted)!r}, then "
        f"{page_name(update_again)!r}",
        not isinstance(stray, SetupComplete)
        and is_seconds(stray_saved.wake_wait_sec, 4.0)
        and is_configuration_mode(after_cancel)
        and is_broadlink_page(update_page)
        and is_configuration_mode(restarted)
        and is_broadlink_page(update_again),
    )

    # X7 an exception inside the new handlers ends that setup with an error only
    real_fields = broadlink_setup._wait_fields

    def broken_fields(_device):
        raise RuntimeError("X7 test")

    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    broadlink_setup._wait_fields = broken_fields
    try:
        await flow.send()
        raised_page = await flow.send({"choice": MAC, "action": "configure"})
    finally:
        broadlink_setup._wait_fields = real_fields
    real_seconds = broadlink_setup._wait_seconds
    _, _, _ = await configure(flow, None)
    broadlink_setup._wait_seconds = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("X7 test")
    )
    try:
        raised_save = await flow.send({"wake_wait_sec": "5"})
    finally:
        broadlink_setup._wait_seconds = real_seconds
    _, page, done = await configure(flow, "6")
    report(
        "X7 an exception while making the settings page, or while saving it: that setup "
        "ends with an error; the next Configure works",
        f"page {type(raised_page).__name__}; save {type(raised_save).__name__}; then "
        f"{page_name(page)!r}, {type(done).__name__}, saved {describe(flow.saved())}",
        isinstance(raised_page, SetupError)
        and isinstance(raised_save, SetupError)
        and is_configure_page(page)
        and isinstance(done, SetupComplete)
        and is_seconds(flow.saved().wake_wait_sec, 6.0),
    )

    # X8 the framework's page as BL-11 expects it, and a page of another shape
    flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)])
    await flow.send()
    own = await framework_setup.BaseSetupFlow._build_configuration_mode_screen(
        setup_flow_of(flow)
    )
    real_build = framework_setup.BaseSetupFlow._build_configuration_mode_screen

    async def other_shape(_self):
        return RequestUserInput({"en": "Configuration mode"}, [])

    framework_setup.BaseSetupFlow._build_configuration_mode_screen = other_shape
    start = len(LOGS.entries)
    try:
        other = await setup_flow_of(flow)._build_configuration_mode_screen()
    finally:
        framework_setup.BaseSetupFlow._build_configuration_mode_screen = real_build
    warned = [t for t in LOGS.texts(start) if "no action list" in t]
    report(
        "X8 ucapi-framework as BL-11 relies on it: a CONFIGURATION_MODE step and an "
        "action list on its first page without Configure; a first page of another shape "
        "is sent unchanged, with a warning (else: ucapi-framework changed, re-check BL-11)",
        f"step {getattr(framework_setup.SetupSteps, 'CONFIGURATION_MODE', None)}; the "
        f"framework's actions {actions(own)}; another shape: {other.settings}, "
        f"warnings {len(warned)}",
        hasattr(framework_setup.SetupSteps, "CONFIGURATION_MODE")
        and actions(own) == (
            ["add", "update", "remove", "reset", "backup", "restore"],
            "add",
        )
        and other.settings == []
        and len(warned) == 1,
    )

    # X9-X10 the running device uses the new value at once, also when it holds a copy
    harness = bl8_harness()
    before = asyncio.all_tasks()
    with bl8_fakes(harness):
        harness.NET = harness.Net(up_at=0.0)
        flow = Flow(saved=[saved_entry(wake_wait_sec=4.0)], make_driver=real_driver)
        driver = flow.driver
        try:
            await start_driver(driver)
            device = driver._device_instances.get(MAC)
            shared = device is not None and device.device_config is next(
                iter(driver.config_manager.all())
            )
            _, page, done = await configure(flow, "5")
            same = driver._device_instances.get(MAC) is device
            live = device._wake_wait() if device is not None else None
            report(
                "X9 the driver running, its device sharing the saved configuration: "
                "Configure 5 s, and the same device (not re-created) uses 5 s at once",
                f"shared {shared}; answer {type(done).__name__}; same device {same}; "
                f"its wait {live}",
                shared and isinstance(done, SetupComplete) and same and live == 5.0,
            )

            await remote_request(flow, "subscribe_events", [EMITTER])
            await remote_request(flow, "unsubscribe_events", [EMITTER])
            await remote_request(flow, "subscribe_events", [EMITTER])
            copy = driver._device_instances.get(MAC)
            holds_copy = copy is not None and copy is not device and (
                copy.device_config is not next(iter(driver.config_manager.all()))
            )
            _, page, done = await configure(flow, "2")
            live = copy._wake_wait() if copy is not None else None
            if copy is not None:
                copy.update_config(address=NEW_IP)  # what the device does on an IP change
            saved = flow.saved()
            report(
                "X10 the Remote unsubscribes and subscribes again (the framework "
                "re-creates the device from a copy of its configuration): Configure 2 s, "
                "and that device uses 2 s at once; its own update after an IP change "
                "then keeps 2 s in the file, with the new address and the learned codes",
                f"a new device holding a copy {holds_copy}; answer "
                f"{type(done).__name__}; its wait {live}; after its update: "
                f"{describe(saved)}",
                holds_copy
                and isinstance(done, SetupComplete)
                and live == 2.0
                and saved is not None
                and is_seconds(saved.wake_wait_sec, 2.0)
                and unchanged_but_wait(saved, address=NEW_IP),
            )
        finally:
            await stop_driver(driver, before)


def is_discovered_page(page) -> bool:
    """Return whether ``page`` is the framework's "Discovered Devices" page."""
    return isinstance(page, RequestUserInput) and page.title == {
        "en": "Discovered Devices"
    }


def listed(page) -> list[tuple]:
    """Return the entries of a page's device list: (id, text) of each item."""
    spec = fields(page).get("choice", {}).get("field", {}).get("dropdown", {})
    return [
        (item.get("id"), item.get("label", {}).get("en"))
        for item in spec.get("items", [])
    ]


def once_at(page, address: str) -> bool:
    """Return whether "Discovered Devices" lists the Broadlink once, at ``address``.

    Its list: the Broadlink, its address in the entry's text, then "Setup Manually".
    """
    entries = listed(page)
    return (
        is_discovered_page(page)
        and [name for name, _ in entries] == [MAC, "manual"]
        and address in str(entries[0][1])
    )


async def case_v():
    """Check V: two setups with one discovery object, as the driver keeps one.

    driver.py makes one BroadlinkDiscovery for as long as it runs, and ucapi-framework
    looks the picked device up in the list that object keeps. Upstream's discover()
    added each search's finds to that list without emptying it: on 2026-11-19 the
    second try's "Discovered Devices" page listed the Broadlink twice, and a pick takes
    the oldest entry, with the address the Broadlink had then. Here the first setup is
    ended by the web configurator's Cancel (the Remote sends AbortDriverSetup NONE, as
    on that day) on its "Discovered Devices" page, the search done; then the Broadlink
    gets another address (a DHCP change) and answers at the old one no more. V2 gives
    the PIN, so that it holds for any setup.py an Update works with.
    """
    flow = Flow(
        saved=[saved_entry(**BL9_SAVED)],
        discovery=discover.BroadlinkDiscovery(timeout=1),
    )
    await flow.send()  # Configure: "Configuration mode"
    first = await flow.send({"choice": MAC, "action": "update"})
    await flow.abort(IntegrationSetupError.NONE)
    BROADLINK_AT[0] = NEW_IP
    await flow.send()
    second = await flow.send({"choice": MAC, "action": "update"})
    report(
        "V1 one discovery object: Update, Cancel, the Broadlink gets another address, "
        'Update again: the second "Discovered Devices" page lists the Broadlink once, '
        "at its new address",
        f"the first search: {page_name(first)!r} {listed(first)}; the second: "
        f"{page_name(second)!r} {listed(second)}",
        once_at(first, IP) and once_at(second, NEW_IP),
    )
    page = result = None
    if is_discovered_page(second):
        page = await flow.send({"choice": MAC})
    if is_access_page(page):
        result = await flow.answer(ADDRESS_A, PIN)
    device = flow.saved()
    report(
        "V2 ... picked there, the Update reaches the Broadlink at its new address and "
        "saves that address",
        f"the pick's answer: {'none' if page is None else repr(page_name(page))}; "
        f"then {type(result).__name__}; {describe(device)}",
        is_access_page(page)
        and isinstance(result, SetupComplete)
        and device is not None
        and device.address == NEW_IP
        and not OPEN_REMOTES,
    )


# The "Remote API access" page the Remote's core refused on 2026-11-19, as the driver
# sent it (firmware 2.12.1, core 0.83.2; the test build 1.3.0-madalone.4). The core
# logged Err(Error("invalid type: floating point `0.5`, expected i32", ...)) for it,
# at both tries, and the web configurator stayed on "Setting up...". The driver's log
# masks the PIN field; on the wire it was as here.
REFUSED_PAGE = {
    "title": {"en": "Remote API access"},
    "settings": [
        {
            "id": "remote_address",
            "label": {"en": "Remote"},
            "field": {
                "dropdown": {
                    "value": "http://192.168.2.204:80/api/",
                    "items": [
                        {
                            "id": "http://192.168.2.204:80/api/",
                            "label": {"en": "Remote3 (192.168.2.204)"},
                        }
                    ],
                }
            },
        },
        {
            "id": "password",
            "label": {"en": "Remote PIN (leave empty to keep the current access)"},
            "field": {"password": {"value": ""}},
        },
        {
            "id": "wake_wait_sec",
            "label": {"en": "Wait for Wi-Fi after a wake"},
            "field": {
                "number": {
                    "value": 4.0,
                    "min": 0.0,
                    "max": 6.0,
                    "steps": 0.5,
                    "decimals": 1,
                    "unit": {"en": "s"},
                }
            },
        },
        {
            "id": "wake_wait_sec_help",
            "label": {"en": ""},
            "field": {
                "label": {
                    "value": {
                        "en": "A press right after the Remote wakes waits up to this "
                        "long for its Wi-Fi."
                    }
                }
            },
        },
    ],
}
# Two fields of Unfolded Circle's own demo setup page ("Homey demo configuration", in
# the test data of core-simulator 0.83.2 and 0.85.3): a whole number with "steps": 1,
# a decimal number without "steps".
UC_DEMO_FIELDS = [
    {
        "id": "integer",
        "label": {"en": "Enter percentage between 0 and 100"},
        "field": {
            "number": {
                "value": 0,
                "min": 0,
                "max": 100,
                "steps": 1,
                "unit": {"en": "%"},
            }
        },
    },
    {
        "id": "number",
        "label": {"en": "Enter a number between 0.00 and 1.00"},
        "field": {"number": {"value": 0.5, "min": 0.0, "max": 1.0, "decimals": 2}},
    },
]
# The one strict problem S2 lets pass: ucapi-framework's "Configuration mode" page lists
# the configured devices, and with none configured it lists one entry "---" whose id is
# empty, where the model asks for 1 to 50 characters.
DASH_ENTRY = "setting 'choice' dropdown: item id of 0 characters (model: 1 to 50)"
DASH_LIST = {"value": "", "items": [{"id": "", "label": {"en": "---"}}]}
# The answers that go past the stand-in core's checks on purpose, by the check that
# sends them (the ``lenient`` of Flow.send): B2's waits that no list sends (17 texts
# that are no item, 4 values that aren't text, 1 left out), C2's empty wait and the
# answers without a wait of F7, F8 and F9. S5 fails on any other, so a new one shows.
EXPECTED_UNCHECKED = {"B2": 22, "C2": 1, "F7": 1, "F8": 1, "F9": 1}
# What the stand-in core must find wrong with those answers. This is the self-test of
# its answer check: with the check off, or blind to one kind, S5 fails.
EXPECTED_UNCHECKED_PROBLEMS = {
    "a list value that is none of its items": 18,
    "a value that isn't text": 4,
    "a field left out": 4,
}


def page_of(*settings, title: str = "A page") -> dict:
    """Return a page, as sent, with the given settings."""
    return {"title": {"en": title}, "settings": list(settings)}


def one_field(field: dict, name: str = "field") -> dict:
    """Return a page whose one setting has the field ``field``."""
    return page_of({"id": name, "label": {"en": "A field"}, "field": field})


def with_wait(field: dict) -> dict:
    """Return the page the core refused, with ``field`` as its wait setting's field."""
    page = json.loads(json.dumps(REFUSED_PAGE))
    page["settings"][2]["field"] = field
    return page


def wait_number(**changes) -> dict:
    """Return the refused page's number field with properties changed, or left out."""
    spec = dict(REFUSED_PAGE["settings"][2]["field"]["number"], **changes)
    return {"number": {k: v for k, v in spec.items() if v is not NOT_SENT}}


def wait_dropdown(**changes) -> dict:
    """Return the wait's list as fix round 7 sends it, with properties changed."""
    spec = dict({"value": "4", "items": json.loads(json.dumps(WAIT_ITEMS))}, **changes)
    return {"dropdown": {k: v for k, v in spec.items() if v is not NOT_SENT}}


def known_pages() -> list[tuple[str, dict, str, str]]:
    """Return S3's pages: (what it is, the page as sent, its verdict, a word it names).

    The verdicts: "hard", the core's parser refuses the page; "strict", the parser
    takes it and page_problems objects all the same; "pass", nothing to object to. The
    word must be in a problem of that kind (none for "pass"). Core-simulator 0.83.2 and
    0.85.3 refused each "hard" page here and showed each other one (2026-11-19).
    """
    number_items = json.loads(json.dumps(WAIT_ITEMS))
    number_items[4]["id"] = 4
    doubled = {"id": MAC, "label": {"en": f"RM4 mini ({IP})"}}
    manual = {"id": "manual", "label": {"en": "Setup Manually"}}
    twin = {"id": "name", "label": {"en": "Name"}, "field": {"text": {"value": ""}}}
    return [
        (
            "the page the Remote's core refused on 2026-11-19, as sent",
            REFUSED_PAGE,
            "hard",
            "steps",
        ),
        ("... with steps 1.0", with_wait(wait_number(steps=1.0)), "hard", "steps"),
        ("... with steps 1", with_wait(wait_number(steps=1)), "pass", ""),
        ("... without steps", with_wait(wait_number(steps=NOT_SENT)), "pass", ""),
        (
            "... with decimals 1.0",
            with_wait(wait_number(steps=1, decimals=1.0)),
            "hard",
            "decimals",
        ),
        (
            "... a number without a value",
            with_wait(wait_number(steps=1, value=NOT_SENT)),
            "hard",
            "no value",
        ),
        ("... the wait as fix round 7's list", with_wait(wait_dropdown()), "pass", ""),
        (
            "... the list's value given as a number",
            with_wait(wait_dropdown(value=4)),
            "hard",
            "not text",
        ),
        (
            '... the list\'s value "4.0", none of its items',
            with_wait(wait_dropdown(value="4.0")),
            "strict",
            "none of its items",
        ),
        (
            "... an item's id given as a number",
            with_wait(wait_dropdown(items=number_items)),
            "hard",
            "without a text id",
        ),
        (
            "... the list without a value",
            with_wait(wait_dropdown(value=NOT_SENT)),
            "strict",
            "no value",
        ),
        (
            "... the list with a key the model doesn't have",
            with_wait(wait_dropdown(steps=1)),
            "strict",
            "unknown keys",
        ),
        (
            "a label whose value is plain text",
            one_field({"label": {"value": "Plain text"}}),
            "hard",
            "not a language map",
        ),
        (
            "a field with two types",
            one_field({"text": {"value": ""}, "password": {"value": ""}}),
            "hard",
            "exactly one known type",
        ),
        (
            "a checkbox whose value is text",
            one_field({"checkbox": {"value": "false"}}),
            "hard",
            "not true or false",
        ),
        (
            'the same item id twice (as "Discovered Devices" at the second try of '
            "2026-11-19)",
            page_of(
                {
                    "field": {
                        "dropdown": {"value": MAC, "items": [doubled, doubled, manual]}
                    },
                    "id": "choice",
                    "label": {"en": "Discovered Devices"},
                },
                title="Discovered Devices",
            ),
            "strict",
            "used twice",
        ),
        (
            'the framework\'s "---" entry, an item id of no characters',
            one_field({"dropdown": DASH_LIST}, name="choice"),
            "strict",
            "0 characters",
        ),
        (
            "a setting id of 51 characters",
            one_field({"text": {"value": ""}}, name="a" * 51),
            "strict",
            "51 characters",
        ),
        ("the same setting id twice", page_of(twin, twin), "strict", "used twice"),
        (
            "a number outside its range",
            one_field({"number": {"value": 7, "min": 0, "max": 6}}),
            "strict",
            "outside",
        ),
        (
            "a text with no English",
            one_field({"label": {"value": {"de": "Nur Deutsch"}}}),
            "strict",
            "no English text",
        ),
        (
            "a whole number beyond 32 bits that a float can hold",
            one_field({"number": {"value": 10**30}}),
            "strict",
            "beyond 32 bits",
        ),
        (
            "a whole number no float can hold: not JSON the core reads",
            one_field({"number": {"value": 10**400}}),
            "hard",
            "beyond a float",
        ),
        (
            "a text with a lone surrogate: not JSON the core reads",
            one_field({"label": {"value": {"en": f"bad {chr(0xD800)} text"}}}),
            "hard",
            "lone surrogate",
        ),
        (
            "UC's own demo fields: a whole number with steps 1, a decimal number "
            "without steps",
            page_of(*UC_DEMO_FIELDS, title="Homey demo configuration"),
            "pass",
            "",
        ),
    ]


def verdict_of(page) -> tuple[str, list[str]]:
    """Return page_problems' verdict on a page, and the problems it rests on."""
    hard, strict = page_problems(page)
    if hard:
        return "hard", hard
    return ("strict", strict) if strict else ("pass", [])


def is_dash_page(page) -> bool:
    """Return whether a page as sent is "Configuration mode" with the "---" entry."""
    if not isinstance(page, dict) or page.get("title") != {"en": "Configuration mode"}:
        return False
    settings = page.get("settings")
    return isinstance(settings, list) and any(
        isinstance(setting, dict)
        and setting.get("id") == "choice"
        and setting.get("field") == {"dropdown": DASH_LIST}
        for setting in settings
    )


def is_excused(page, strict: list[str]) -> bool:
    """Return whether a page's strict problems are the one exception S2 lets pass."""
    return strict == [DASH_ENTRY] and is_dash_page(page)


def exception_cases() -> list[tuple[str, dict, bool]]:
    """Return S3's pages for S2's exception: (what it is, the page, excused or not)."""
    entry = {
        "id": "choice",
        "label": {"en": "Configured Devices"},
        "field": {"dropdown": DASH_LIST},
    }
    twin = {"id": "name", "label": {"en": "Name"}, "field": {"text": {"value": ""}}}
    return [
        (
            'the "---" entry on "Configuration mode"',
            page_of(entry, title="Configuration mode"),
            True,
        ),
        ("the same entry on another page", page_of(entry), False),
        (
            '"Configuration mode" with the entry and a second problem',
            page_of(entry, twin, twin, title="Configuration mode"),
            False,
        ),
    ]


def problem_summary(found: list, titles: Counter) -> str:
    """Summarise the pages that have problems, for a report line.

    ``found``: (the page as kept, its problems) of each; ``titles``: every page of the
    run by title, for "n of m".
    """
    if not found:
        return "none"
    by_title = Counter(title_of(sent.page) for sent, _ in found)
    cases = Counter(sent.case for sent, _ in found)
    problems = Counter(problem for _, texts in found for problem in texts)
    return (
        f"{len(found)} pages ("
        + ", ".join(f"{name!r} {n} of {titles[name]}" for name, n in by_title.items())
        + f"), in {dict(cases)}: "
        + "; ".join(f"{n} x {problem}" for problem, n in problems.most_common(3))
        + (f"; and {len(problems) - 3} more kinds" if len(problems) > 3 else "")
    )


def check_known_pages():
    """Check S3: page_problems gives pages with a known verdict that verdict."""
    known = known_pages()
    wrong = []
    for what, page, verdict, word in known:
        got, problems = verdict_of(page)
        named = not word or any(word in problem for problem in problems)
        if got != verdict or not named:
            wrong.append(f"{what}: {got} {problems}, expected {verdict} ({word!r})")
    counts = Counter(verdict for _, _, verdict, _ in known)
    widened = [
        f"{what}: excused {not excused}, expected {excused}"
        for what, page, excused in exception_cases()
        if is_excused(page, page_problems(page)[1]) is not excused
    ]
    report(
        "S3 the page check can fail: pages with a known verdict, the page the Remote's "
        "core refused on 2026-11-19 among them, get that verdict; and S2's exception "
        "covers that one page only",
        f"{len(known)} pages: {dict(counts)}; not as expected: {wrong}; the refused "
        f"page: {verdict_of(REFUSED_PAGE)}; the exception: "
        f"{widened or 'as narrow as S2 says'}",
        not wrong
        and not widened
        and counts["hard"] > 0
        and counts["strict"] > 0
        and counts["pass"] > 0,
    )


def check_static_page():
    """Check S4: the static first page in the repository's driver.json."""
    path = os.path.join(os.path.dirname(TESTS), "driver.json")
    try:
        with open(path, encoding="utf-8") as file:
            schema = json.load(file).get("setup_data_schema")
        problem = None if schema is not None else "no setup_data_schema"
    except (OSError, ValueError) as err:
        schema, problem = None, f"{type(err).__name__}: {err}"
    hard, strict = page_problems(schema)
    report(
        "S4 the static first page in driver.json (setup_data_schema), which the core "
        "reads when the driver registers, passes the same check",
        f"{path}: "
        + (
            problem
            or f"title {title_of(schema)!r}, "
            f"{len(schema.get('settings', [])) if isinstance(schema, dict) else '?'} "
            f"settings; hard {hard}, strict {strict}"
        ),
        problem is None and not hard and not strict,
    )


def check_answers():
    """Check S5: what the stand-in core did with the answers of the run."""
    used = Counter(entry.check for entry in UNCHECKED)
    broken = Counter(
        problem.partition(":")[0] for entry in UNCHECKED for problem in entry.problems
    )
    report(
        "S5 the stand-in core checked each answer as the Remote's core does (a value "
        "for every input field of the page, every value text, a list's value one of "
        "its items, a checkbox's true or false) and refused none; only the answers "
        "that say so went past it, for the driver's own leniency",
        f"answers passed to the driver {len(ANSWERS)}; refused {len(REFUSED)} "
        f"{REFUSED[:2]}; past the checks on purpose {sum(used.values())}, by check "
        f"{dict(used)} (expected {EXPECTED_UNCHECKED}), which the core would refuse "
        f"for: {dict(broken)} (expected {EXPECTED_UNCHECKED_PROBLEMS})",
        bool(ANSWERS)
        and not REFUSED
        and dict(used) == EXPECTED_UNCHECKED
        and dict(broken) == EXPECTED_UNCHECKED_PROBLEMS
        and all(entry.problems for entry in UNCHECKED),
    )


async def case_s():
    """Check S: every page the driver sent is one the Remote's core takes.

    It runs after every case that sends a page and looks at each page of the run, as
    ucapi put it on the wire (Flow._core_reads kept them), with the core's own types.
    The repository's check before fix round 7 looked at the first "Remote API access"
    page only, with the published API description's types, and missed the page the
    Remote refused.
    """
    checked = [(sent, *page_problems(sent.page)) for sent in PAGES]
    titles = Counter(title_of(sent.page) for sent in PAGES)
    refused = [(sent, hard) for sent, hard, _ in checked if hard]
    report(
        "S1 no page the driver sent, in any case, has a problem the core's parser "
        "refuses a page for",
        f"{len(PAGES)} pages checked: {dict(titles)}; refused: "
        f"{problem_summary(refused, titles)}",
        bool(PAGES) and not refused,
    )

    excused = [sent for sent, _, strict in checked if is_excused(sent.page, strict)]
    others = [
        (sent, strict)
        for sent, _, strict in checked
        if strict and not is_excused(sent.page, strict)
    ]
    report(
        "S2 no page has anything else wrong or risky, which the parser lets through; "
        'one exception: the framework\'s "---" entry, an item with an empty id, on '
        '"Configuration mode" when nothing is configured',
        f"the exception: {len(excused)} pages, in "
        f"{dict(Counter(sent.case for sent in excused))} ({DASH_ENTRY}); other "
        f"problems: {problem_summary(others, titles)}",
        bool(PAGES) and not others,
    )

    check_known_pages()
    check_static_page()
    check_answers()

    numbers = []
    for sent in PAGES:
        settings = sent.page.get("settings") if isinstance(sent.page, dict) else None
        found = number_fields(settings if isinstance(settings, list) else [])
        if found:
            numbers.append((sent, [f"a number field {name!r}" for name in found]))
    report(
        "S6 no page of the run has a number field: the wait is a list",
        f"pages with a number field: {problem_summary(numbers, titles)}",
        bool(PAGES) and not numbers,
    )


def logged_answers() -> dict[int, dict]:
    """Return the page answers in ucapi's message log (DEBUG), by request id."""
    logged = {}
    for entry in LOGS.entries:
        record = entry.record
        if record.name != ucapi_api.__name__ or record.levelno != logging.DEBUG:
            continue
        _, found, data = record.getMessage().partition(" ->: ")
        if not found:
            continue
        message = json.loads(data)
        if message.get("msg") == "set_driver_user_data":
            logged[message.get("id")] = message.get("msg_data", {}).get("input_values")
    return logged


def pin_in(text: str, pins=None) -> re.Match | None:
    """Return where ``text`` shows one of ``pins`` (default: every PIN typed on a page).

    Only a whole word counts: "8642" in '"password": "8642"' or "PIN 8642", not inside a
    longer word such as a temporary folder's name.
    """
    for pin in TYPED_PINS if pins is None else pins:
        found = re.search(rf"(?<!\w){re.escape(pin)}(?!\w)", text)
        if found:
            return found
    return None


def around_pin(text: str) -> str:
    """Return the part of a log text around the first typed PIN it shows."""
    found = pin_in(text)
    start = max(found.start() - 100, 0) if found else 0
    end = start + 130
    return f"{'...' if start else ''}{text[start:end]}..."


async def case_p():
    """Check P: no line of the driver's log shows a PIN typed on a page, or an API key.

    It runs last and looks at what every case before it logged.
    """
    logged = logged_answers()
    typed = [(request, key, pin) for request, key, pin in ANSWERS if pin]
    unlogged = [request for request, _, _ in ANSWERS if request not in logged]
    shown = [
        (key, (logged.get(request) or {}).get(key))
        for request, key, pin in typed
        if (logged.get(request) or {}).get(key) != REDACTED
        or pin_in(json.dumps(logged.get(request)), [pin])
    ]
    report(
        f"P1 ucapi's message log (DEBUG) has every page answer, the PIN as {REDACTED}",
        f"answers {len(ANSWERS)}, logged {len(ANSWERS) - len(unlogged)}; with a typed "
        f"PIN {len(typed)}, under the field ids {sorted({key for _, key, _ in typed})}, "
        f"the PIN shown in {len(shown)} {shown[:2]}",
        bool(typed) and not unlogged and not shown,
    )

    in_log = [e for e in LOGS.entries if LOGS.in_driver_log(e.record)]
    outside = [e for e in LOGS.entries if not LOGS.in_driver_log(e.record)]
    levels = Counter(e.record.levelname for e in in_log)
    leaks = [e.driver for e in in_log if e.driver and pin_in(e.driver)]
    shown = Counter(e.record.name for e in outside if pin_in(e.text))
    filtered = [e.driver for e in outside if e.driver and pin_in(e.driver)]
    report(
        "P2 the driver's log, at every level, through every case: no PIN typed on the "
        "page; driver.py puts log_redact's filter on its handler, so no record shows "
        "one once filtered, even outside the driver's log",
        f"driver.py's logging: {LOGS.describe()}; log_redact's filter on every "
        f"handler {LOGS.redacting()}; the driver's log: {len(in_log)} records "
        f"{dict(levels)}, {len(leaks)} lines with a typed PIN "
        f"{[around_pin(text) for text in leaks[:2]]}; outside it (made only with "
        f"every logger at DEBUG), records with a typed PIN: as logged {dict(shown)}, "
        f"once filtered {len(filtered)} {[around_pin(text) for text in filtered[:2]]}; "
        f"records the driver's handler raised on {len(LOGS.raised)}",
        LOGS.redacting()
        and bool(TYPED_PINS)
        and levels["DEBUG"] > 0
        and not leaks
        and not filtered
        and not LOGS.raised,
    )

    keys = sorted({OLD_KEY, RESTORED_KEY, *ISSUED_KEYS})

    def shows(text) -> bool:
        return bool(text) and any(key in text for key in keys)

    logged = [e for e in in_log if shows(e.text)]
    written = [e.driver for e in in_log if shows(e.driver)]
    filtered = [e.driver for e in outside if shows(e.driver)]
    report(
        "P3 the driver's log, through every case: no line shows a Remote API key "
        "(saved, restored or new); once filtered, no record outside it either",
        f"keys {len(keys)}; driver-log records with a key: as logged {where(logged)}, "
        f"as the driver writes them {len(written)} "
        f"{[around(text, keys) for text in written[:2]]}; outside it, once filtered "
        f"{len(filtered)} {[around(text, keys) for text in filtered[:2]]}",
        LOGS.redacting() and bool(logged) and not written and not filtered,
    )


async def main():
    """Run every case, logging captured throughout."""
    print(f"== BL-9 setup test on {LABEL} (Python {sys.version.split()[0]})")
    async with LOGS:
        print(f"== the driver's logging: {LOGS.describe()}")
        for run in (
            case_a,
            case_w,
            case_b,
            case_c,
            case_d,
            case_e,
            case_f,
            case_g,
            case_g4,
            case_h,
            case_k,
            case_k_backup,
            case_l,
            case_r,
            case_r_first_setup,
            case_r_reset,
            case_r_remove,
            case_r_update,
            case_r_standby,
            case_r_resubscribe,
            case_v,
            case_x,
            case_s,  # after every case that sends a page: it checks them all
            case_p,  # last: it checks what the others logged
        ):
            CASE[0] = run.__name__
            try:
                await run()
            except Exception as err:  # pylint: disable=broad-except
                report(run.__name__, f"raised {type(err).__name__}: {err}", False)
                traceback.print_exc()
    for folder in FOLDERS:
        shutil.rmtree(folder, ignore_errors=True)
    failed = [name for name, _, ok in RESULTS if not ok]
    print(
        f"== {LABEL}: {len(RESULTS) - len(failed)}/{len(RESULTS)} as expected"
        + (
            f"; not as expected: {', '.join(n.split()[0] for n in failed)}"
            if failed
            else ""
        )
    )
    errors = [e.text for e in LOGS.entries if e.record.levelno >= logging.ERROR]
    if failed and errors:  # K2-K4 and R log errors on purpose: shown when a check fails
        print("== the run's ERROR log records:", *errors, sep="\n", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
