#!/usr/bin/env python3
"""
BL-9 scenario test: button presses on a timeline, through the real driver and router.

Fork patch BL-9 (presses must not pile up and fire late while the Remote's Wi-Fi is
down). Each run starts the ``driver.py`` ``main()`` of an intg-broadlink folder in a
fresh process: ``BroadlinkDriver``, the real ucapi-framework 1.9.8 and the real ucapi
0.7.0 WebSocket server, whose ``_ws_router`` handles one request at a time. A fake core
talks to it over a real WebSocket, as the Remote's core does. Nothing in the driver is
replaced; only the network edges are fake:

- python-broadlink 0.19.0 (``hello``, ``xdiscover``, ``discover``, ``ping`` and the
  device's ``auth``, ``send_data``, ``hello``, ``get_fwversion``), timed like the
  library: a request is resent every 1 s until its timeout; while the Remote's network
  is down the first ``sendto`` fails at once with ``[Errno 101] Network is
  unreachable``; a silent Broadlink never answers, and one that comes back is heard at
  the next resend. An IR code counts as sent when the Broadlink receives it. Faked per
  address: MD runs configure two Broadlinks, A and B, and each press names one.
- unfurled's ``Remote`` (the Remote's IR conversion API): 0.1 s while the network is
  up; while it is down, the REAL unfurled and aiohttp fail against an address without
  a route, so the errors are the libraries' own.
- the core (``FakeCore``), as the BL-9 research measured core-simulator 0.83.2: it
  forwards every request at once, without waiting for earlier answers; it answers 408
  10.0 s after a request the integration has not answered, and ignores a later answer.

Press kinds: "conv" (an IR code the Remote converts, not converted yet: BL-8's path),
"mem" (the same, converted once before: remembered), "local" (no Remote API key: the
integration converts it), "learned" (the Remote entity's send_cmd with a learned code).
Setup, with the network up: the core connects, then presses once each code the run
remembers (all of a "mem" run's, PRE in a "conv" run that uses it) and SETUP, a code
no timeline uses, on every Broadlink of the run, so a code has been sent since the
core's connect, as on a Remote in use. The driver's start and the core's connect start
wake windows; both are long past when a timeline starts (SETTLE). That connect is also
the first wake event since the driver's start (see below).

Settings: a run can store the device's BL-9 setting (``BroadlinkConfig``
``wake_wait_sec``, "Wait for Wi-Fi after a wake") in its configuration. Every other run
keeps the default (4 s) and is judged by it. On a tree whose ``BroadlinkConfig`` lacks
the field, the settings runs (WW; the 6 s runs of QW, MX, RZQ, RZN, RZC, FL, CN, MD
and EX; one H1 and one E1) are N/A: not run, not counted.

What every press must do (BL-9's wake window; fix round 4: one window per wake), from
its arrival, the run's schedules, the setting and, for a press that queued, when the
driver handled it (``expectations()``):
- the Broadlink can be reached and stays so: sent within 1 s (later is LATE);
- one window per wake: only the wake's first wake event, exit_standby or the core's
  connect, since the last enter_standby (or since the driver's start) starts a window,
  when the driver handles it, for every Broadlink (``_windows()``). A later one before
  the next enter_standby starts none, in whatever order the two come and however late
  it is handled (CN), unless it is handled WAKE_EVENT_LAPSE (60 s) or more after the
  window's start (LP). The setup's connect took the first turn, so a wake event before
  the timeline's first enter_standby, within 60 s of that connect, starts none (X1,
  RZC; the stale runs of W1, QW and MX: every press in them fails at once);
- a press made within wake_wait of that start is in the wake window and waits in it
  only, whatever its connect, conversion and send find later;
- a press handled while the Remote is in standby (after enter_standby and before the
  wake's first wake event: the press that woke the Remote reached the driver first, PB)
  is outside every window: with the Wi-Fi down an error within 0.5 s, nothing sent;
  with the Wi-Fi up it is sent. Fix round 3's press-started window is gone;
- inside the window: if the Broadlink can be reached again at least 0.3 s before the
  window ends (1.3 s when it was silent, or its first reply is lost: python-broadlink
  resends a hello only every second), sent within 1 s of that; if only after the
  window, an error by the window's end + 0.5 s and nothing sent; in between, either.
  1.5 s instead of 0.5 s when the Broadlink's first reply is lost or it was silent in
  the window: the press's last attempt starts when its window ends, and its hello
  listens 1.4 s for the resend. A press is never sent later than the window's end +
  that slack, + 0.3 s for every press sent before it from its queue: the presses made
  inside the window go out one after another once the wake press connects, each
  ~0.1-0.25 s after the one before, so a burst queued behind a wake press that
  connects late is replayed in full (the documented limit: ucapi gives no request
  time, so they can't be told from fresh presses; EQ, ZB10);
- a press made inside the window but handled after it ended (it queued behind a slow
  press, or behind a second wake event that starts nothing) is handled as a press
  outside it: ucapi doesn't say when a press was made. With the Wi-Fi down when it is
  handled, an error within 0.5 s of that, nothing sent;
- outside the window, the Wi-Fi down: an error within 0.5 s of when the driver handled
  the press, nothing sent. Since fix round 2 nothing waits when the Wi-Fi drops while
  the Remote is awake;
- the Wi-Fi up only for moments (back for less than STEADY, 2 s, between drops): a press
  may or may not reach the Broadlink then (either), with the bounds above;
- the Broadlink silent while the Wi-Fi is up (BL-7): judged by S1 / S2 / B1 only.
Allowances: +0.3 s when the Broadlink's first reply is lost (J3, J5: the press's own
hello meets the Wi-Fi's return), +0.8 s when the driver's hello meets it (H1, up to
0.5 s later); +0.3 s for every press sent before it from the same queue, on the "sent
within 1 s" limit and on the window's end + slack alike.
Every run (hard): no exception out of a command handler, every request answered, no
408 from the core, no press sent twice, nothing sent for a press answered with an
error, nothing sent after the core's 408 (LATE-AFTER-408), no DOUBLE-TOGGLE (two late
sends of one code within 1 s), every send attributed to its press and received by the
Broadlink it was pressed for (MD), exactly one core connection to the run's driver
(each run listens on a port of its own, PORT_BASE + its index). Not a DOUBLE-TOGGLE:
two presses of one code, both made inside one wake window and each sent once (REPEAT
in the report): each was a real press, sent as the documented limit above says.
A run whose Wi-Fi drops a second time at a chosen moment is INVALID when the drop
misses it, because it then tests nothing (re-review): TW and MX (between a connect's
auth and the send after it), RZQ, RZD, RZN, RZ2 and PB's queued runs (after the press
that waited in the window is sent, in PB since fix round 4 the first press queued
behind the wake press, and before the presses queued behind it are handled).
On top: W1 every IR within 0.05 s of HEAD's (madalone.3, f8c33c4), except the stale
connection's wake press, which since fix round 4 has no window (W1_NO_WINDOW: an error
at once); S1 every answer within 0.05 s of HEAD's (BL-7's timings), except U8's wake
press, whose error must come when its wake window ends (BL-9 waits for the driver's
attempt until then; madalone.3 gave up after 3 s), or about 3.5 s after it was handled
when its own attempt came before the driver's (its 3 s hello, then BL-7's hold-off); S2
how long the router is held: awake, the first press answered within 4 s and the second
within 4.5 s of the first's arrival; at a wake, the first by its window's end + 0.2 s,
the second 1 s (BL-7's wait for the driver's attempt) later; B1 the Broadlink comes
back without a press, and the next press is sent within 1 s.

Families: W1 / W2 / P2 the first press after a wake (P2: the 17:42:03 replay); X1 a
stale connection (no enter_standby, so no window since fix round 4); XW a stale
connection inside the window (the send fails with Errno 101 and waits); X7 a press
later in the wake; O1-O5, AX3, AX4 outages while awake; S1 / S2 / B1 BL-7's timings
with a silent Broadlink; V1 / V4 / J3 / J5 lost replies, an activity, slow failures; WW
the setting at 0 / 4 / 6 s; the reviews' cases: QW (presses queued behind a slow wake
press), TW and MX (a second drop between a connect and its send, MX at the 6 s
maximum), H1 (the driver's hello meets the Wi-Fi's return and loses its first reply),
E1 (a lost first reply in the window's last second), RZQ / RZD / RZN / RZ2 (a press
queued behind a wake press sent at the window's end, then a second drop), RZC (the
core connects again with no enter_standby: no window), FL (the Wi-Fi flapping during
and after the window, presses queued); the final review's: EQ (three presses queued
behind a wake press whose first reply is lost), ZB10 (VOL+ ten times while the Wi-Fi
comes back), PB (the wake press reaches the driver before exit_standby: an error at
once; the presses queued behind it wait in the exit_standby's window, then a second
drop; the Wi-Fi never back); fix round 4's: CN (the core's connect and exit_standby
in one wake, in three orders, with a press between them and presses queued behind;
and enter_standby followed by a connect alone), W2W (two wakes in one run, by
exit_standby or the core's connect, and an enter_standby / exit_standby bounce queued
behind a waiting press), MD (two Broadlinks: a press for B queued behind A's wake
press, made after exit_standby or before it), EX (enter_standby inside the previous
wake's window, then the next wake's press before its exit_standby); fix round 5's: LP
(a wake whose enter_standby the driver never saw, after WAKE_EVENT_LAPSE).

Usage, from the repository root (Python 3.11 with requirements.txt installed and NO
network, so that a request to the Remote's address fails as when its Wi-Fi is down; the
run refuses to start with a route). One command, in the bl8-harness image
(python:3.11-slim with the fork's requirements and test requirements):
    docker run --rm --network none -v "$PWD":/w:ro -w /w bl8-harness
        python tests/bl9_scenarios.py intg-broadlink <label> [options]
(Git Bash on Windows: prefix MSYS_NO_PATHCONV=1 and mount "$(pwd -W)".)
Options: --quick (a representative 50 runs), --only IDS (comma list of ids or id
prefixes, so W2 picks W2W too; an item with a space picks the runs whose "ID variant"
starts with it), --jobs N (parallel runs, default 8), --json FILE (results),
--reference FILE (W1 / S1 references from a --json file instead of the built-in HEAD
values), --verbose (the driver's log lines), --list. The catalogue has 203 runs, 47 of
them with the setting (156 run on a tree without it). With 8 jobs the full catalogue
took ~10 min, --quick ~3 min. Exit code 1 if any run is not as expected (FAIL, ERROR
or INVALID).

:copyright: (c) 2026 by madalone.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

# pylint: disable=import-error,import-outside-toplevel,protected-access,too-many-lines
# pylint: disable=global-statement,broad-exception-caught,too-many-locals
# pylint: disable=too-many-branches,too-many-statements,too-many-arguments
# pylint: disable=too-many-return-statements,too-many-instance-attributes

import argparse
import ast
import asyncio
import base64
import concurrent.futures
import contextvars
import dataclasses
import errno
import hashlib
import itertools
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import weakref
import zlib

# =========================================================================== constants

MAC = "a043b0112233"
BL_IP = "192.0.2.124"  # TEST-NET-1 (RFC 5737): never a real device
# BL-9 (fix round 4): MD runs configure a second Broadlink, "B"; every other run has
# only "A". Each press names its Broadlink ("dev", default "A").
MAC_B = "a043b0445566"
BL_IP_B = "192.0.2.125"
BROADLINKS = {"A": (BL_IP, MAC), "B": (BL_IP_B, MAC_B)}  # label -> (address, MAC)
REMOTE_URL = "http://192.0.2.1/api/"  # the Remote's own API, TEST-NET-1 as well
CORE_TIMEOUT = 10.0  # the core answers 408 this long after a request
RETRY_INTVL = 1.0  # python-broadlink resends a request every second
HELLO_LATENCY = 0.015  # hello + auth ~25 ms; a run's "latency" parameter replaces them
AUTH_LATENCY = 0.010
SEND_LATENCY = 0.12  # device log: a remembered code answered 139 ms after the request
CONVERT_LATENCY = 0.10  # device log: conversion + send of a HEX code 243 ms
SETTLE = 6.0  # quiet time before a timeline: the core's connect starts a wake window
RESULT_MARK = "BL9RESULT "

# Settings and pass rules (seconds)
# BL-9: the device's one setting ("Wait for Wi-Fi after a wake"); fix round 2 removed
# "Wait for Wi-Fi when it drops" (drop_wait_sec) and its drop window.
SETTINGS = ("wake_wait_sec",)
WAKE_WAIT = 4.0  # BL-9's default: every run without settings is judged by it
WAKE_WAIT_MAX = 6.0  # rm.WAKE_WAIT_MAX
# BL-9: the core events that start a wake window (broadlink_driver's listeners). Fix
# round 4: only the first of them since the last enter_standby (or since the driver's
# start) does; a later one in the same wake starts none (_windows()).
WAKE_EVENTS = ("exit_standby", "connect")
# BL-9 (fix round 5): broadlink_driver.WAKE_EVENT_LAPSE. A wake event this long after
# the last window's start, with no enter_standby between, starts a window of its own.
# The driver counts the time the Remote slept (CLOCK_BOOTTIME); nothing sleeps here.
WAKE_EVENT_LAPSE = 60.0
LATE_AFTER = 1.0  # an IR sent later than this after its press is LATE
AT_ONCE = 0.5  # an error "at once": within this long of the press
RESCUE_MARGIN = 0.3  # Broadlink back this long before a window ends: must be sent
RESEND_WAIT = 1.0  # more margin when it was silent or a reply lost: the hello resend
WINDOW_SLACK = 0.5  # nothing answered or sent later than this after a window ends...
# BL-9: ...or this, when the Broadlink's first reply is lost or it was silent in the
# window: the press's last attempt starts when its window ends, and its hello listens
# 1.4 s (rm.PRESS_HELLO_TIMEOUT) to hear python-broadlink's resend after 1 s.
LATE_END = 1.5
LOST_REPLY = 0.3  # extra time when the Broadlink's first reply is lost (J3, J5)
# BL-9: H1, the driver's own hello is the first to meet the Wi-Fi's return, up to one
# attempt interval (0.5 s) after it; with its reply lost the press is sent this much
# later than when its own hello meets the return (J5).
DRIVER_HELLO = 0.5
QUEUE_STEP = 0.3  # extra time for every press sent before this one from its queue
# BL-9: the Broadlink must be reachable this long without a break for a press to be
# sure to reach it: a press tries every 0.5 s, a connect takes up to 0.2 s, a lost reply
# 1 s more. Up for less between two drops (the Wi-Fi flapping), a press may or may not
# reach it ("either").
STEADY = 2.0
HEAD_EQUAL = 0.05  # W1, S1: equal to HEAD within this
PROBE_LOCK_WAIT = 1.0  # rm.PROBE_LOCK_WAIT: BL-7's wait for a connect attempt running
S2_SLACK = 0.2  # S2 at a wake: answered within this of the window's end (+ that wait)
# BL-9: a wake press handled in the same instant as exit_standby, before the driver's
# retry has started, makes the first attempt itself: a hello that listens 3 s (the most
# a press's own attempt may, rm.DISCOVERY_TIMEOUT), then, 0.5 s later
# (rm.COMMAND_RECONNECT_INTERVAL), BL-7's hold-off refuses the next one. Against a
# silent Broadlink its error then comes this long after it was handled, before the
# 4 s window ends (re-review; madalone.3 gave up after 6 s there).
OWN_ATTEMPT_ERROR = 3.5

# madalone.3 (f8c33c4, before BL-9), measured with this script (mean of two runs, which
# differed by 2.3 ms at most): W1's IR time and S1's answer delay of each press.
# --reference FILE replaces them with another run's results.
HEAD_IR = {
    "W1 conv up=0.3": {"wake": 0.663},
    "W1 conv up=1.0": {"wake": 1.164},
    "W1 conv up=2.0": {"wake": 2.166},
    "W1 conv up=2.4": {"wake": 2.668},
    "W1 mem up=0.3": {"wake": 0.561},
    "W1 mem up=1.0": {"wake": 1.063},
    "W1 mem up=2.0": {"wake": 2.065},
    "W1 mem up=2.4": {"wake": 2.567},
    "W1 local up=0.3": {"wake": 0.561},
    "W1 local up=1.0": {"wake": 1.063},
    "W1 local up=2.0": {"wake": 2.065},
    "W1 local up=2.4": {"wake": 2.566},
    "W1 conv up=1.0 press with exit_standby": {"wake": 1.136},
    "W1 conv up=1.0 after an outage while awake": {"wake": 1.164},
    "W1 mem up=1.0 after an outage while awake": {"wake": 1.063},
    "W1 conv up=1.0 stale connection (no enter_standby)": {"wake": 1.164},
    "W1 conv up=1.0 and a second press 0.3 s later": {"wake": 1.164, "wake2": 1.387},
    "W1 learned (Remote entity send_cmd) up=1.0": {"wake": 1.062},
}
HEAD_ANSWER = {
    "S1 U1 one press": {"p1": 5.003},
    "S1 U2 five presses 0.3 s apart": {
        "p1": 5.004,
        "p2": 4.704,
        "p3": 4.404,
        "p4": 4.105,
        "p5": 3.805,
    },
    "S1 U5 presses at 0 / 10 / 20.5 s": {"p1": 5.004, "p2": 1.001, "p3": 1.002},
    "S1 U8 Broadlink off at a wake, presses 0.2 / 7 s": {"p1": 3.002, "p2": 0.001},
    "S1 conv one press (code not remembered)": {"p1": 5.105},
}
# BL-9: S1 presses judged by their wake window's end instead of by the reference. With
# the Broadlink off at a wake, madalone.3 (BL-7) gave the wake press its error 3 s after
# the press (3.002 above, kept as the record), when it stopped waiting for the driver's
# connect attempt (~6 s against a Broadlink that doesn't answer). BL-9's fix round (the
# owner's decision, 2026-11-15) makes a press wait for that attempt until its window
# ends, so the error now comes when the wake window ends: 4 s after the wake by default.
# A press handled before the driver's attempt started gets it OWN_ATTEMPT_ERROR after
# it was handled instead (rule_s1).
S1_WINDOW_END = {"S1 U8 Broadlink off at a wake, presses 0.2 / 7 s": ("p1",)}
# BL-9 fix round 4: W1 presses HEAD's IR time no longer applies to (kept above as the
# record; madalone.3 sent this one at 1.164 s). An exit_standby with no enter_standby
# since the setup's connect is not the first wake event of a wake, so it starts no
# window: the press, made with the Wi-Fi down, must fail at once (check_press).
W1_NO_WINDOW = {"W1 conv up=1.0 stale connection (no enter_standby)": ("wake",)}


def _pronto(last: int) -> str:
    return f"0000 006D 0002 0000 0157 00AC 0015 {last:04X}"


CODES = {
    "POWER": _pronto(0x0689),
    "PRE": _pronto(0x0700),
    "INPUT": _pronto(0x0701),
    "HDMI1": _pronto(0x0702),
    "VOLUP": _pronto(0x0703),
    "MUTE": _pronto(0x0704),
    "CHECK": _pronto(0x0705),
    "SETUP": _pronto(0x0706),  # sent once during every run's setup, never in a timeline
}
CYCLE = ["INPUT", "HDMI1", "VOLUP", "MUTE"]
# A code the integration learned: the Remote entity's "send_cmd tv:power", sent through
# rm.send_command(predefined_code=...) without any conversion.
LEARNED_DATA = b"\x26\x00\x10\x00learned-code"


def raw_for(code: str) -> list[int]:
    """Return the raw timings the fake conversion API gives a code (one per code)."""
    return [
        9000,
        4500,
        560,
        560,
        560,
        1690,
        560,
        500 + zlib.crc32(code.encode()) % 2000,
    ]


# =========================================================================== catalogue


def _ev(at, msg):
    return {"at": at, "type": "event", "msg": msg}


def _pr(at, code, tag, dev="A"):
    """Return a press of ``code`` for the Broadlink ``dev`` (MD: "A" or "B")."""
    press = {"at": at, "type": "press", "code": code, "tag": tag}
    if dev != "A":
        press["dev"] = dev
    return press


def _seq(at, codes, gap):
    """Return an activity: its steps, each ``gap`` s after the previous step's answer."""
    return {"at": at, "type": "sequence", "codes": codes, "gap": gap}


def _wake(press_at, code="POWER"):
    """Return standby at -2 s (Broadlink disconnected), wake at 0, a press later."""
    return [
        _ev(-2.0, "enter_standby"),
        _ev(0.0, "exit_standby"),
        _pr(press_at, code, "wake"),
    ]


def _press_first(exit_after, code="POWER"):
    """Return standby at -2 s, the wake press at 0 and exit_standby ``exit_after`` later.

    BL-9 (PB): the press that woke the Remote reaches the driver before exit_standby.
    """
    return [
        _ev(-2.0, "enter_standby"),
        _pr(0.0, code, "wake"),
        _ev(exit_after, "exit_standby"),
    ]


def catalogue() -> list[dict]:
    """Return every run: id, variant, timeline, schedules, settings and parameters."""
    runs: list[dict] = []

    def add(
        sid,
        variant,
        timeline,
        *,
        kind,
        duration,
        down=(),
        silent=(),
        settings=None,
        quick=False,
        **params,
    ):
        codes = set()
        for item in timeline:
            if item["type"] == "press":
                codes.add(item["code"])
            elif item["type"] == "sequence":
                codes.update(item["codes"])
        # "mem": every code was converted once before the scenario (remembered); "conv":
        # none was, except PRE, the code of a press made earlier to find the outage.
        if kind == "mem":
            warm = sorted(codes - {"LEARNED"})
        elif kind == "conv" and "PRE" in codes:
            warm = ["PRE"]
        else:
            warm = []
        # BL-9: and SETUP, so a code has been sent since the core's connect (a wake for
        # BL-9, long past when the timeline starts), as on a Remote in use. Kept from the
        # first BL-9 build, whose drop window needed it, so every run's setup stays as
        # when the W1 / S1 references were last checked against it.
        warm.append("SETUP")
        runs.append(
            {
                "id": sid,
                "variant": variant,
                "kind": kind,
                "api_key": kind != "local",
                "timeline": timeline,
                "down": [list(x) for x in down],
                "silent": [list(x) for x in silent],
                "warm": warm,
                "duration": duration,
                "settings": settings,
                "quick": quick,
                "params": params,
            }
        )

    # ---- W1 the first press after a wake, the Wi-Fi back at 0.3 / 1.0 / 2.0 / 2.4 s;
    # the press arrives 58 ms after exit_standby (BL-8 incidents). As today: sent within
    # 1 s of the Wi-Fi's return, the IR within 0.05 s of HEAD's.
    for kind in ("conv", "mem", "local"):
        for up in (0.3, 1.0, 2.0, 2.4):
            add(
                "W1",
                f"{kind} up={up}",
                _wake(0.058),
                kind=kind,
                down=[(-1.0, up)],
                duration=11.0,
                quick=(kind, up) in (("mem", 1.0), ("conv", 2.4)),
            )
    # in the same instant as exit_standby: ucapi must start the wake window first
    add(
        "W1",
        "conv up=1.0 press with exit_standby",
        _wake(0.0),
        kind="conv",
        down=[(-1.0, 1.0)],
        duration=11.0,
        quick=True,
    )
    # nothing remembered about an outage may outlive a wake
    for kind in ("conv", "mem"):
        add(
            "W1",
            f"{kind} up=1.0 after an outage while awake",
            [
                _pr(-8.0, "PRE", "pre"),
                _pr(-7.0, "PRE", "pre"),
                _ev(-4.0, "enter_standby"),
                _ev(0.0, "exit_standby"),
                _pr(0.058, "POWER", "wake"),
            ],
            kind=kind,
            down=[(-9.0, 1.0)],
            duration=11.0,
        )
    # no enter_standby seen: the Broadlink is still marked connected at the wake. BL-9
    # fix round 4: the setup's connect was this wake's first wake event, so this
    # exit_standby starts no window and the press fails at once (W1_NO_WINDOW)
    add(
        "W1",
        "conv up=1.0 stale connection (no enter_standby)",
        [_ev(0.0, "exit_standby"), _pr(0.058, "POWER", "wake")],
        kind="conv",
        down=[(-1.0, 1.0)],
        duration=11.0,
    )
    # a second press queued behind the wake press
    add(
        "W1",
        "conv up=1.0 and a second press 0.3 s later",
        _wake(0.058) + [_pr(0.358, "VOLUP", "wake2")],
        kind="conv",
        down=[(-1.0, 1.0)],
        duration=11.0,
    )
    add(
        "W1",
        "learned (Remote entity send_cmd) up=1.0",
        _wake(0.058, "LEARNED"),
        kind="learned",
        down=[(-1.0, 1.0)],
        duration=11.0,
    )

    # ---- W2 the 3.1 s wake of 2026-11-14 at the device's latencies: hello 63 ms + auth
    # 23 ms (median of 39 first connects after a wake) and a slow first connect of 0.2 s.
    # Sent the first time (BL-2's last attempt at 2.5 s made it fail).
    for kind in ("mem", "conv"):
        add(
            "W2",
            f"{kind} up=3.1, hello 63 ms + auth 23 ms",
            _wake(0.058),
            kind=kind,
            down=[(-1.0, 3.1)],
            duration=12.0,
            latency=[0.063, 0.023],
        )
        add(
            "W2",
            f"{kind} up=3.1, first connect 0.2 s",
            _wake(0.058),
            kind=kind,
            down=[(-1.0, 3.1)],
            duration=12.0,
            latency=[0.09, 0.11],
            quick=kind == "mem",
        )

    # ---- P2 the wake of 2026-11-14 17:42:03 as logged: the first Errno 101 0.88 s after
    # exit_standby, the Broadlink back 3.08 s after it; a press 58 ms after the wake.
    for kind in ("mem", "conv"):
        add(
            "P2",
            f"{kind} replay of 17:42:03 (first failure 0.88 s, up=3.08)",
            _wake(0.058),
            kind=kind,
            down=[(-1.0, 3.08)],
            duration=12.0,
            slow_fail=[0.88, 1],
            quick=kind == "mem",
        )

    # ---- X1 a wake with the Broadlink still marked connected (no enter_standby seen):
    # the send itself fails with Errno 101. BL-9 fix round 4: with no enter_standby
    # since the setup's connect (the first wake event since the driver's start), this
    # exit_standby starts no window, so the press is outside every window: one quick
    # attempt, then an error at once, nothing sent. Before round 4 it waited for the
    # Wi-Fi in the window; XW keeps that case, with an enter_standby before the wake.
    for kind, code in (("mem", "POWER"), ("learned", "LEARNED"), ("local", "POWER")):
        for up in (0.3, 1.0, 2.0):
            add(
                "X1",
                f"{kind} stale connection up={up}",
                [_ev(0.0, "exit_standby"), _pr(0.058, code, "wake")],
                kind=kind,
                down=[(-1.0, up)],
                duration=11.0,
                quick=(kind, up) == ("mem", 1.0),
            )

    # ---- XW (fix round 4) X1's case inside a wake window: enter_standby, the wake with
    # the Wi-Fi up, so the driver's retry connects (~0.03 s), then down from 0.2 s. The
    # press at 0.3 s finds the Broadlink marked connected, and its send (or, "conv", its
    # conversion) fails with Errno 101 inside the window: it waits for the Wi-Fi and is
    # sent once (a send is resent only after the reconnect worked).
    for kind, code, up in (
        ("mem", "POWER", 1.0),
        ("mem", "POWER", 2.0),
        ("learned", "LEARNED", 1.0),
        ("local", "POWER", 1.0),
        ("conv", "POWER", 1.0),
    ):
        add(
            "XW",
            f"{kind} stale connection inside the window, Wi-Fi down 0.2..{up:g} s",
            [
                _ev(-2.0, "enter_standby"),
                _ev(0.0, "exit_standby"),
                _pr(0.3, code, "wake"),
            ],
            kind=kind,
            down=[(0.2, up)],
            duration=11.0,
            quick=(kind, up) == ("mem", 1.0),
        )

    # ---- X7 the motion sensor woke the Remote; a button is pressed later, while the
    # Wi-Fi is still coming back (the 3.1 s wake).
    for at in (0.5, 1.0, 2.0):
        add(
            "X7",
            f"mem press at {at} s, up=3.1",
            _wake(at),
            kind="mem",
            down=[(-1.0, 3.1)],
            duration=14.0,
            quick=at == 0.5,
        )
    add(
        "X7",
        "conv press at 1.0 s, up=3.1",
        _wake(1.0),
        kind="conv",
        down=[(-1.0, 3.1)],
        duration=14.0,
    )

    # ---- O1 / O2 awake, the Wi-Fi down, five presses of POWER 0.3 s apart; back at 8 /
    # 11.6 s. "stale": it dropped 1 s before (the Broadlink still marked connected);
    # "noticed": it dropped 4 s before and a press of another code at -3 s failed.
    five = [_pr(0.3 * i, "POWER", f"p{i + 1}") for i in range(5)]
    for sid, up, dur in (("O1", 8.0, 14.0), ("O2", 11.6, 16.0)):
        for start in ("stale", "noticed"):
            for kind in ("conv", "mem"):
                quick = (sid, start, kind) in (
                    ("O1", "stale", "conv"),
                    ("O2", "stale", "mem"),
                )
                if start == "stale":
                    add(
                        sid,
                        f"{start} {kind}",
                        five,
                        kind=kind,
                        down=[(-1.0, up)],
                        duration=dur,
                        quick=quick,
                    )
                else:
                    add(
                        sid,
                        f"{start} {kind}",
                        [_pr(-3.0, "PRE", "pre")] + five,
                        kind=kind,
                        down=[(-4.0, up)],
                        duration=dur,
                    )
    add(
        "O1",
        "stale learned (Remote entity send_cmd)",
        [_pr(0.3 * i, "LEARNED", f"p{i + 1}") for i in range(5)],
        kind="learned",
        down=[(-1.0, 8.0)],
        duration=14.0,
    )

    # ---- O3 a long outage: presses at 0 / 10 / 20.5 / 40 s, back at 45 s; presses 0.3 s
    # (another code) and 1 s after it must be sent.
    o3 = [
        _pr(0.0, "POWER", "p0"),
        _pr(10.0, "POWER", "p10"),
        _pr(20.5, "POWER", "p20"),
        _pr(40.0, "POWER", "p40"),
        _pr(45.3, "VOLUP", "p45"),
        _pr(46.0, "POWER", "p46"),
    ]
    for kind in ("conv", "mem"):
        add("O3", kind, o3, kind=kind, down=[(-1.0, 45.0)], duration=60.0)

    # ---- O4 the Wi-Fi drops at 0.5 s while connected, a press at 1 s, back at 1.5 s
    # (awake, outside the wake window: refused at once).
    for kind in ("mem", "local"):
        add(
            "O4",
            kind,
            [_pr(1.0, "POWER", "p")],
            kind=kind,
            down=[(0.5, 1.5)],
            duration=12.0,
            quick=kind == "mem",
        )
    add(
        "O4",
        "learned (Remote entity send_cmd)",
        [_pr(1.0, "LEARNED", "p")],
        kind="learned",
        down=[(0.5, 1.5)],
        duration=12.0,
    )

    # ---- O5 an activity-like burst: four codes 0.2 s apart during an outage (back at
    # 20 s); "sequential": each step 0.2 s after the previous answer.
    burst = [_pr(0.2 * i, code, f"s{i + 1}") for i, code in enumerate(CYCLE)]
    for kind in ("conv", "mem"):
        add(
            "O5",
            f"{kind} pipelined",
            burst,
            kind=kind,
            down=[(-1.0, 20.0)],
            duration=22.0,
            quick=kind == "conv",
        )
    add(
        "O5",
        "mem sequential",
        [_seq(0.0, CYCLE, 0.2)],
        kind="mem",
        down=[(-1.0, 20.0)],
        duration=26.0,
    )

    # ---- AX3 five presses, the Wi-Fi back at 2.0 s; AX4 a press 0.5 s into a drop
    # that ends at 2.0 / 3.2 / 6.0 s (awake, outside the wake window: refused at once).
    for start in ("stale", "noticed"):
        for kind in ("mem", "conv"):
            if start == "stale":
                add(
                    "AX3",
                    f"{start} {kind}, back at 2.0 s",
                    five,
                    kind=kind,
                    down=[(-1.0, 2.0)],
                    duration=12.0,
                )
            else:
                add(
                    "AX3",
                    f"{start} {kind}, back at 2.0 s",
                    [_pr(-3.0, "PRE", "pre")] + five,
                    kind=kind,
                    down=[(-4.0, 2.0)],
                    duration=12.0,
                    quick=kind == "mem",
                )
    for up in (2.0, 3.2, 6.0):
        add(
            "AX4",
            f"mem, back at {up} s",
            [_pr(1.0, "POWER", "p")],
            kind="mem",
            down=[(0.5, up)],
            duration=14.0,
            quick=up == 3.2,
        )

    # ---- S1 the Wi-Fi fine, the Broadlink silent (unplugged while connected): BL-7's
    # timings (madalone.2 results U1 / U2 / U5 / U8), equal to HEAD.
    silent = [(-1.0, None)]
    add(
        "S1",
        "U1 one press",
        [_pr(0.0, "POWER", "p1")],
        kind="mem",
        silent=silent,
        duration=9.0,
    )
    add(
        "S1",
        "U2 five presses 0.3 s apart",
        five,
        kind="mem",
        silent=silent,
        duration=12.0,
        quick=True,
    )
    add(
        "S1",
        "U5 presses at 0 / 10 / 20.5 s",
        [_pr(0.0, "POWER", "p1"), _pr(10.0, "POWER", "p2"), _pr(20.5, "POWER", "p3")],
        kind="mem",
        silent=silent,
        duration=24.0,
    )
    add(
        "S1",
        "U8 Broadlink off at a wake, presses 0.2 / 7 s",
        [
            _ev(-2.0, "enter_standby"),
            _ev(0.0, "exit_standby"),
            _pr(0.2, "POWER", "p1"),
            _pr(7.0, "VOLUP", "p2"),
        ],
        kind="mem",
        silent=silent,
        duration=14.0,
        quick=True,
    )
    add(
        "S1",
        "conv one press (code not remembered)",
        [_pr(0.0, "POWER", "p1")],
        kind="conv",
        silent=silent,
        duration=9.0,
    )

    # ---- S2 the Broadlink silent and the Wi-Fi back at 2.45 s; a second press at 1 s
    # queues behind the first. The router is held ~4 s at most.
    s2 = [_pr(0.058, "POWER", "p1"), _pr(1.0, "VOLUP", "p2")]
    for kind in ("mem", "conv"):
        add(
            "S2",
            f"wake {kind}",
            [_ev(-2.0, "enter_standby"), _ev(0.0, "exit_standby")] + s2,
            kind=kind,
            down=[(-1.0, 2.45)],
            silent=[(-1.0, None)],
            duration=16.0,
            quick=kind == "mem",
        )
    add(
        "S2",
        "awake, outage noticed by a press at -3 s",
        [_pr(-3.0, "PRE", "pre"), _pr(0.0, "POWER", "p1"), _pr(1.0, "VOLUP", "p2")],
        kind="mem",
        down=[(-4.0, 2.45)],
        silent=[(-4.0, None)],
        duration=16.0,
    )

    # ---- B1 a press gives up; the Broadlink must come back without a press.
    add(
        "B1",
        "network back at 4 s",
        [_pr(0.0, "POWER", "p1"), _pr(18.5, "CHECK", "check")],
        kind="mem",
        down=[(-1.0, 4.0)],
        duration=20.0,
        back=4.0,
        bound=15.0,
        quick=True,
    )
    add(
        "B1",
        "Broadlink back at 10 s (BL-7 U7)",
        [_pr(0.0, "POWER", "p1"), _pr(14.5, "CHECK", "check")],
        kind="mem",
        silent=[(-1.0, 10.0)],
        duration=16.0,
        back=10.0,
        bound=2.5,
    )

    # ---- V1 the Wi-Fi back at `up` after a wake, but the Broadlink answers nothing for
    # 1.5 s after it (two packets lost); up just after a wake-retry attempt (1.03, 2.03:
    # the press's own attempt is the first after it) or on one (1.0).
    for kind in ("mem", "conv"):
        for up in (1.03, 2.03, 1.0):
            add(
                "V1",
                f"{kind} wake, Broadlink silent 1.5 s after the network returns, "
                f"up={up}",
                _wake(0.058) + [_pr(0.458, "VOLUP", "wake2")],
                kind=kind,
                down=[(-1.0, up)],
                silent=[(up, round(up + 1.5, 3))],
                duration=12.0,
                quick=(kind, up) == ("mem", 1.03),
            )

    # ---- V4 an activity started by the wake press (four steps, each 0.2 s after the
    # previous answer; the UI never resends a step), the Wi-Fi back at 3.1 / 4.5 / 5.5 s.
    for up in (3.1, 4.5, 5.5):
        add(
            "V4",
            f"mem activity of 4 steps from the wake press, Wi-Fi back at {up} s",
            [
                _ev(-2.0, "enter_standby"),
                _ev(0.0, "exit_standby"),
                _seq(0.058, ["POWER", "INPUT", "HDMI1", "VOLUP"], 0.2),
            ],
            kind="mem",
            down=[(-1.0, up)],
            duration=16.0,
            quick=up in (3.1, 4.5),
        )

    # ---- J3 awake, a short outage whose first reply is lost when it ends (7 of 39 first
    # connects after a wake on the owner's Remote were answered only on the library's
    # resend): the Wi-Fi drops at -1 s, back at 1.0 s; presses at 0.0 and 1.5 s.
    add(
        "J3",
        "mem stale, back at 1.0 s, first hello reply lost, presses 0.0 / 1.5 s",
        [_pr(0.0, "POWER", "p1"), _pr(1.5, "VOLUP", "p2")],
        kind="mem",
        down=[(-1.0, 1.0)],
        duration=10.0,
        lose_first_hello=True,
        quick=True,
    )

    # ---- J5 the wake press with the first reply lost, the Wi-Fi back just after one of
    # the driver's wake-retry attempts (every 0.5 s), so the press's own hello meets it.
    for kind in ("mem", "conv"):
        for up in (1.03, 2.03):
            add(
                "J5",
                f"{kind} wake, first hello reply lost, up={up}",
                _wake(0.058) + [_pr(0.458, "VOLUP", "wake2")],
                kind=kind,
                down=[(-1.0, up)],
                duration=12.0,
                lose_first_hello=True,
                quick=(kind, up) == ("conv", 1.03),
            )

    # ---- WW the setting: "Wait for Wi-Fi after a wake" at 0 / 4 / 6 s on the slow wakes.
    # The press after the Wi-Fi's return must work; at 6 s a queue of presses must still
    # never reach the core's 408.
    for wait in (0.0, 4.0, 6.0):
        sets = {"wake_wait_sec": wait}
        pre = f"wake {wait:g} s:"
        for kind in ("mem", "conv"):
            add(
                "WW",
                f"{pre} {kind} Wi-Fi back at 3.1 s (hello 63 + auth 23 ms), a press "
                "at 4.6 s",
                _wake(0.058) + [_pr(4.6, "VOLUP", "after")],
                kind=kind,
                down=[(-1.0, 3.1)],
                duration=12.0,
                settings=sets,
                latency=[0.063, 0.023],
                quick=kind == "mem",
            )
        if wait > 0:
            # the window's edge: the last attempt starts when the window ends
            add(
                "WW",
                f"{pre} mem Wi-Fi back at {wait - 0.4:g} s (0.4 s before the window "
                "ends)",
                _wake(0.058),
                kind="mem",
                down=[(-1.0, wait - 0.4)],
                duration=12.0,
                settings=sets,
                quick=wait == 4.0,
            )
        for up, after in ((5.0, 6.6), (7.0, 8.0)):
            add(
                "WW",
                f"{pre} mem Wi-Fi back at {up:g} s, a press at {after:g} s",
                _wake(0.058) + [_pr(after, "VOLUP", "after")],
                kind="mem",
                down=[(-1.0, up)],
                duration=12.0,
                settings=sets,
            )
        # A queue: a different button every 0.7 s until 5.7 s. With the Wi-Fi back at
        # 7 s a press that waits too long goes out late; at 11.5 s also after its 408.
        queue = [_ev(-2.0, "enter_standby"), _ev(0.0, "exit_standby")]
        queue += [
            _pr(round(0.058 + 0.7 * i, 3), CYCLE[i % 4], f"k{i}") for i in range(9)
        ]
        for up, after, duration in ((7.0, 8.0, 20.0), (11.5, 12.5, 24.0)):
            add(
                "WW",
                f"{pre} mem a different button every 0.7 s, Wi-Fi back at {up:g} s",
                queue + [_pr(after, "POWER", "after")],
                kind="mem",
                down=[(-1.0, up)],
                duration=duration,
                settings=sets,
                quick=wait == 6.0 and up == 11.5,
            )
        add(
            "WW",
            f"{pre} mem activity of 4 steps from the wake press, Wi-Fi back at 4.8 s",
            [
                _ev(-2.0, "enter_standby"),
                _ev(0.0, "exit_standby"),
                _seq(0.058, ["POWER", "INPUT", "HDMI1", "VOLUP"], 0.2),
            ],
            kind="mem",
            down=[(-1.0, 4.8)],
            duration=16.0,
            settings=sets,
        )

    # ---- QW presses queued behind the wake press during a slow wake, the Wi-Fi back
    # only after the wake window (the default 4 s, back at 5 s; the 6 s maximum, back at
    # 7 s). They were made inside the window but are handled after it ended: ucapi
    # doesn't say when a press was made, so each is handled as a press outside the window
    # and must be refused at once, nothing sent late. Before fix round 1 each got a drop
    # window of its own and was sent late, POWER twice (review: VE1, VE4, VE5, VE6). The
    # press finds the network down through its hello (the Broadlink disconnected by the
    # standby), its conversion or its send (the Broadlink still marked connected). Fix
    # round 4: the "stale" runs have no enter_standby, so their exit_standby starts no
    # window (the setup's connect was this wake's first wake event): every press in
    # them, the wake press too, fails at once.
    stale = [_ev(0.0, "exit_standby"), _pr(0.058, "POWER", "wake")]
    for wait, up in ((WAKE_WAIT, 5.0), (WAKE_WAIT_MAX, 7.0)):
        sets = None if wait == WAKE_WAIT else {"wake_wait_sec": wait}
        pre = f"wake {wait:g} s:"
        add(
            "QW",
            f"{pre} mem wake press + VOLUP at 1.0 s, Wi-Fi back at {up:g} s",
            _wake(0.058) + [_pr(1.0, "VOLUP", "second")],
            kind="mem",
            down=[(-1.0, up)],
            duration=14.0,
            settings=sets,
        )
        add(
            "QW",
            f"{pre} mem POWER wake press + POWER again at 1.5 and 3.0 s, Wi-Fi back "
            f"at {up:g} s",
            _wake(0.058) + [_pr(1.5, "POWER", "again1"), _pr(3.0, "POWER", "again2")],
            kind="mem",
            down=[(-1.0, up)],
            duration=14.0,
            settings=sets,
            quick=wait == WAKE_WAIT_MAX,
        )
        add(
            "QW",
            f"{pre} stale conv wake press + VOLUP at 1.0 s (its conversion), Wi-Fi "
            f"back at {up:g} s",
            stale + [_pr(1.0, "VOLUP", "second")],
            kind="conv",
            down=[(-1.0, up)],
            duration=14.0,
            settings=sets,
        )
        add(
            "QW",
            f"{pre} stale conv wake press + remembered PRE at 1.0 s (its send), Wi-Fi "
            f"back at {up:g} s",
            stale + [_pr(1.0, "PRE", "second")],
            kind="conv",
            down=[(-1.0, up)],
            duration=14.0,
            settings=sets,
            quick=wait == WAKE_WAIT,
        )

    # ---- TW one press, one window (the default 4 s): the Wi-Fi back at 3.97 s, so the
    # press's last attempt starts when the window ends and connects after it, then the
    # Wi-Fi drops again between that connect's auth and the send. The send fails on its
    # first packet outside the window: one quick attempt, an error at once, nothing sent
    # later. Before fix round 1 the press opened a drop window there and was sent at
    # 6.06 s (review: ZA / RVB). With the auth's 10 ms the gap was a few ms wide, and a
    # drop that missed it (the auth failed, or the IR went out) tested nothing
    # (re-review). Here the first connect takes 0.2 s (W2's latencies: hello 90 ms, auth
    # 110 ms): the auth runs from ~4.095 s to the send at ~4.205 s, both later by as much
    # as the core sends exit_standby late (up to ~16 ms seen). The drops sit at least
    # 20 ms inside that gap even 30 ms late; a run whose drop still misses it is INVALID
    # (hit "auth-send").
    for flap in (4.14, 4.16, 4.18):
        add(
            "TW",
            f"mem wake press, Wi-Fi up 3.97..{flap:g} s (first connect 0.2 s), back at "
            "5.9 s",
            _wake(0.058),
            kind="mem",
            down=[(-1.0, 3.97), (flap, 5.9)],
            duration=12.0,
            latency=[0.09, 0.11],
            hit="auth-send",
            quick=flap == 4.16,
        )

    # ---- MX the 6 s maximum, first hello replies lost: a press waits in its window at
    # most, so it never reaches the core's 408.
    # - One press with exit_standby: the Wi-Fi back at 5.97 s, its last attempt (6.0 s)
    #   connects on the resend, the auth from ~7.095 s to the send at ~7.205 s (first
    #   connect 0.2 s, as in TW); a second drop in that gap; back at 8.9 s. Before fix
    #   round 1 it opened a drop window and was sent at 10.06 s, after the core's 408 at
    #   10.0 s (review: RVC / ZB). As in TW, a run whose drop misses the gap is INVALID.
    # - A press queued 0.04 s behind the wake press, the Wi-Fi back at 7.95 s: handled
    #   when the window ends, with the Wi-Fi down, so refused at once (before fix round 1
    #   sent at 9.0-9.1 s, about 9 s late: review VW); and through the conversion: the
    #   Broadlink still marked connected (no enter_standby), so the queued press's
    #   conversion is what finds the network down. Fix round 4: with no enter_standby
    #   that exit_standby starts no window, so both presses fail at once.
    max_sets = {"wake_wait_sec": WAKE_WAIT_MAX}
    for flap in (7.14, 7.16, 7.18):
        add(
            "MX",
            f"mem press with exit_standby, Wi-Fi up 5.97..{flap:g} s (first connect "
            "0.2 s), back at 8.9 s, first hello replies lost",
            _wake(0.0),
            kind="mem",
            down=[(-1.0, 5.97), (flap, 8.9)],
            duration=14.0,
            lose_first_hello=True,
            latency=[0.09, 0.11],
            settings=max_sets,
            hit="auth-send",
            quick=flap == 7.16,
        )
    for kind in ("conv", "mem"):
        add(
            "MX",
            f"{kind} wake press + VOLUP at 0.1 s, Wi-Fi back at 7.95 s, first hello "
            "reply lost",
            _wake(0.058) + [_pr(0.1, "VOLUP", "second")],
            kind=kind,
            down=[(-1.0, 7.95)],
            duration=14.0,
            lose_first_hello=True,
            settings=max_sets,
            quick=kind == "conv",
        )
    add(
        "MX",
        "stale conv wake press + VOLUP at 0.1 s (its conversion), Wi-Fi back at 7.95 s, "
        "first hello reply lost",
        stale + [_pr(0.1, "VOLUP", "second")],
        kind="conv",
        down=[(-1.0, 7.95)],
        duration=14.0,
        lose_first_hello=True,
        settings=max_sets,
    )

    # ---- H1 the driver's own wake-retry hello (~2.52 s) is the first to meet the
    # Wi-Fi's return, and its first reply is lost: it connects on the library's resend,
    # at ~3.53 s. The press waits for that attempt until its window ends, so it must be
    # sent. Before the fix round it gave up 3 s after the press (503 at 3.06 s, as in
    # madalone.3), whatever the wake wait (review: ZD / RVE). Back by 2.45 s at the
    # latest, so the resend comes before the 4 s window ends.
    for kind, up in (
        ("mem", 2.1),
        ("mem", 2.3),
        ("mem", 2.45),
        ("conv", 2.1),
        ("conv", 2.45),
    ):
        add(
            "H1",
            f"{kind} wake, the driver's hello meets the Wi-Fi back at {up} s, first "
            "reply lost",
            _wake(0.058),
            kind=kind,
            down=[(-1.0, up)],
            duration=12.0,
            lose_first_hello=True,
            driver_hello=True,
            quick=(kind, up) == ("mem", 2.1),
        )
    add(
        "H1",
        "mem wake 6 s, the driver's hello meets the Wi-Fi back at 2.3 s, first reply lost",
        _wake(0.058),
        kind="mem",
        down=[(-1.0, 2.3)],
        duration=12.0,
        lose_first_hello=True,
        driver_hello=True,
        settings={"wake_wait_sec": WAKE_WAIT_MAX},
    )

    # ---- E1 the Broadlink's first reply lost in the window's last second: the press's
    # last attempt starts when the window ends and hears the resend 1 s later, so it and
    # a press queued 0.3 s behind it are sent up to LATE_END after the window (review:
    # RVA / ZE); at the 6 s maximum too, still well before the core's 408.
    for kind in ("mem", "conv"):
        for up in (3.6, 3.95):
            add(
                "E1",
                f"{kind} wake, Wi-Fi back at {up} s, first hello reply lost, a second "
                "press 0.3 s later",
                _wake(0.058) + [_pr(0.358, "VOLUP", "wake2")],
                kind=kind,
                down=[(-1.0, up)],
                duration=12.0,
                lose_first_hello=True,
                quick=(kind, up) == ("conv", 3.6),
            )
    add(
        "E1",
        "mem wake 6 s, Wi-Fi back at 5.95 s, first hello reply lost, a second press "
        "0.3 s later",
        _wake(0.058) + [_pr(0.358, "VOLUP", "wake2")],
        kind="mem",
        down=[(-1.0, 5.95)],
        duration=12.0,
        lose_first_hello=True,
        settings={"wake_wait_sec": WAKE_WAIT_MAX},
    )

    # ---- EQ (final review) E1 with three presses queued behind the wake press. The
    # wake press is sent ~1.1 s after its window ends (its first reply lost), and the
    # presses queued behind it, all made inside the window, go out one after another,
    # each a conversion and a send (~0.22 s) or a send (~0.12 s) after the one before:
    # the third converted one ~1.8 s after the window, past the wake press's own 1.5 s.
    # The documented limit: judged with QUEUE_STEP for every press sent before it.
    for kind in ("conv", "mem"):
        for up in (3.6, 3.95):
            add(
                "EQ",
                f"{kind} wake, Wi-Fi back at {up} s, first hello reply lost, three more "
                "presses 0.3 s apart",
                _wake(0.058)
                + [
                    _pr(0.358, "VOLUP", "wake2"),
                    _pr(0.658, "MUTE", "wake3"),
                    _pr(0.958, "INPUT", "wake4"),
                ],
                kind=kind,
                down=[(-1.0, up)],
                duration=12.0,
                lose_first_hello=True,
                quick=(kind, up) == ("conv", 3.95),
            )

    # ---- RZ (re-review) a press queued behind a wake press whose last attempt, at the
    # window's end, connects and sends; then the Wi-Fi drops again before the queued
    # press is handled. Handled after its window with the Wi-Fi down, the queued press
    # must be refused at once: nothing sent late, nothing after the core's 408. Before
    # fix round 2 it got a drop window of its own and was sent at 6.2-11.3 s, after the
    # core's 408 in RZQ and RZ2. A run whose drop misses that moment (the wake press not
    # sent before it, or a queued press handled with the Wi-Fi up) is INVALID (hit
    # "queued"). The wake press is sent, and answered 0.12 s later, as late as the core
    # sends exit_standby late (up to ~16 ms seen); each drop sits at least 20 ms inside
    # that gap even 30 ms late (the reviewer's 7.06 s once missed by 6 ms).
    # - RZQ: the 6 s maximum, first hello replies lost: the wake press is sent on the
    #   resend at ~7.03 s and answered at ~7.15 s; the second drop at 7.09-7.13 s.
    # - RZD: the same at the default 4 s: sent at ~5.03 s, the drop at 5.08-5.13 s.
    # - RZN: no reply lost, at 4 and 6 s: the wake press is sent ~30 ms after the window
    #   ends and answered ~0.15 s after it; the drop 0.1 s after the window's end.
    # - RZ2: two queued presses, and the Wi-Fi back and down once more after them.
    for flap in (7.09, 7.11, 7.13):
        add(
            "RZQ",
            f"wake 6 s: mem wake press + VOLUP at 0.1 s, Wi-Fi up 5.97..{flap:g} s, back "
            "at 8.9 s, first hello replies lost",
            _wake(0.058) + [_pr(0.1, "VOLUP", "second")],
            kind="mem",
            down=[(-1.0, 5.97), (flap, 8.9)],
            duration=16.0,
            lose_first_hello=True,
            settings={"wake_wait_sec": WAKE_WAIT_MAX},
            hit="queued",
            quick=flap == 7.11,
        )
    for flap in (5.08, 5.105, 5.13):
        add(
            "RZD",
            f"wake 4 s: mem wake press + VOLUP at 0.358 s, Wi-Fi up 3.97..{flap:g} s, "
            "back at 6.9 s, first hello replies lost",
            _wake(0.058) + [_pr(0.358, "VOLUP", "second")],
            kind="mem",
            down=[(-1.0, 3.97), (flap, 6.9)],
            duration=14.0,
            lose_first_hello=True,
            hit="queued",
            quick=flap == 5.105,
        )
    for wait, up, flap, back, duration in (
        (WAKE_WAIT, 3.9, 4.1, 6.1, 14.0),
        (WAKE_WAIT_MAX, 5.9, 6.1, 8.1, 16.0),
    ):
        add(
            "RZN",
            f"wake {wait:g} s: mem wake press + VOLUP at 0.358 s, Wi-Fi up "
            f"{up:g}..{flap:g} s, back at {back:g} s",
            _wake(0.058) + [_pr(0.358, "VOLUP", "second")],
            kind="mem",
            down=[(-1.0, up), (flap, back)],
            duration=duration,
            settings=None if wait == WAKE_WAIT else {"wake_wait_sec": wait},
            hit="queued",
            quick=wait == WAKE_WAIT,
        )
    add(
        "RZ2",
        "wake 4 s: mem wake press + VOLUP at 0.358 s + MUTE at 0.6 s, Wi-Fi up "
        "3.97..5.11, 6.9..8.23, back at 9.9 s, first hello replies lost",
        _wake(0.058) + [_pr(0.358, "VOLUP", "second"), _pr(0.6, "MUTE", "third")],
        kind="mem",
        down=[(-1.0, 3.97), (5.11, 6.9), (8.23, 9.9)],
        duration=16.0,
        lose_first_hello=True,
        hit="queued",
        quick=True,
    )

    # ---- RZC (re-review) the core connects again while the Wi-Fi is down, the Broadlink
    # still marked connected, with no enter_standby since the setup's connect; three
    # presses queue. In fix round 1 the core's connect kept the drop window's "network
    # has worked" flag, so with a drop wait of 2 s the queued presses got drop windows
    # and were sent at ~5.0-5.2 s (re-review); in rounds 2 and 3 it started a wake
    # window. Fix round 4: only a wake's first wake event starts a window, and the
    # setup's connect was this one's, so this connect starts none: all three presses
    # fail at once, nothing sent. A connect after an enter_standby: CN, W2W.
    for wait, up in ((WAKE_WAIT, 5.0), (WAKE_WAIT_MAX, 7.0)):
        add(
            "RZC",
            f"wake {wait:g} s: mem core connect at 0 s, Wi-Fi down -1..{up:g} s, presses "
            "at 0.1 / 0.4 / 0.7 s",
            [
                _ev(0.0, "connect"),
                _pr(0.1, "POWER", "p1"),
                _pr(0.4, "VOLUP", "p2"),
                _pr(0.7, "MUTE", "p3"),
            ],
            kind="mem",
            down=[(-1.0, up)],
            duration=14.0,
            settings=None if wait == WAKE_WAIT else {"wake_wait_sec": wait},
            quick=wait == WAKE_WAIT,
        )

    # ---- FL the Wi-Fi flapping during and after the wake window, presses queued: up for
    # 0.1-0.15 s at a time, the window's end inside a drop, back for good at 6.5 s (4 s
    # window) or 8.5 s (6 s). A press made inside the window may catch a moment the
    # Wi-Fi is up or not (either), but is never sent after the window's end + 0.5 s
    # (+ QUEUE_STEP for each press sent before it from its queue); the presses still
    # queued when the window ends are handled after it, with the Wi-Fi down: refused at
    # once. After the window, a press made with the Wi-Fi down fails at once (o1), one
    # made in a moment it is up may be sent at once (o2). No reply lost.
    for wait, inside, flaps, after in (
        (
            WAKE_WAIT,
            (1.6, 2.6),
            [
                (-1.0, 0.9),
                (1.05, 1.9),
                (2.0, 3.0),
                (3.15, 4.4),
                (4.55, 5.2),
                (5.35, 6.5),
            ],
            (4.6, 5.25, 7.5),
        ),
        (
            WAKE_WAIT_MAX,
            (2.6, 4.6),
            [
                (-1.0, 0.9),
                (1.05, 1.9),
                (2.0, 3.0),
                (3.15, 4.4),
                (4.55, 5.2),
                (5.35, 6.4),
                (6.55, 7.2),
                (7.35, 8.5),
            ],
            (6.6, 7.25, 9.5),
        ),
    ):
        timeline = _wake(0.058) + [
            _pr(0.3, "VOLUP", "q1"),
            _pr(0.6, "MUTE", "q2"),
            _pr(inside[0], "INPUT", "q3"),
            _pr(inside[1], "HDMI1", "q4"),
            _pr(after[0], "POWER", "o1"),
            _pr(after[1], "VOLUP", "o2"),
            _pr(after[2], "CHECK", "after"),
        ]
        for kind in ("mem", "conv") if wait == WAKE_WAIT else ("mem",):
            add(
                "FL",
                f"wake {wait:g} s: {kind} the Wi-Fi up 0.1-0.15 s at a time until "
                f"{flaps[-1][1]:g} s, presses queued",
                timeline,
                kind=kind,
                down=flaps,
                duration=after[2] + 4.5,
                settings=None if wait == WAKE_WAIT else {"wake_wait_sec": wait},
                quick=(wait, kind) == (WAKE_WAIT, "mem"),
            )

    # ---- ZB10 (final review) a burst: VOL+ pressed ten times 0.25 s apart, from 0.3 s,
    # while the Wi-Fi comes back after a wake: at 3.1 s (the wake of 2026-11-14), or at
    # 3.95 s with the first reply lost. All ten presses are made inside the wake window
    # and go out one after another once the wake press connects, the tenth up to ~2.4 s
    # after the window: every volume step reaches the TV (the documented limit). Each is
    # a real press sent once, so two of them within 1 s are a REPEAT, not a
    # DOUBLE-TOGGLE.
    burst10 = _wake(0.058) + [
        _pr(round(0.3 + 0.25 * i, 3), "VOLUP", f"v{i + 1}") for i in range(10)
    ]
    for kind in ("mem", "conv"):
        add(
            "ZB10",
            f"{kind} wake press + VOLUP x10 every 0.25 s, Wi-Fi back at 3.1 s",
            burst10,
            kind=kind,
            down=[(-1.0, 3.1)],
            duration=14.0,
            quick=kind == "mem",
        )
        add(
            "ZB10",
            f"{kind} wake press + VOLUP x10 every 0.25 s, Wi-Fi back at 3.95 s, first "
            "hello reply lost",
            burst10,
            kind=kind,
            down=[(-1.0, 3.95)],
            duration=16.0,
            lose_first_hello=True,
        )

    # ---- PB (final review) the press that wakes the Remote reaches the driver BEFORE
    # exit_standby (2 of 159 wakes in a Kodi driver's logs): the core sends
    # enter_standby, then at the wake the press, and exit_standby 10-60 ms after it.
    # ucapi handles them in that order, one at a time, so the press is handled while
    # the Remote is in standby. Fix round 4 (the owner's decision, 2026-11-16): such a
    # press is outside every window, as in fix round 2: one attempt, then an error at
    # once with the Wi-Fi down, never sent later (the Remote's "Retry commands after
    # wakeup", if on, resends it). Round 3 let it start the wake window itself, which
    # the review found unsafe; madalone.3 gave up after ~2.5 s. The exit_standby behind
    # it is the wake's first wake event: it starts the window when it is handled.
    # - The Wi-Fi back at 1.0 / 2.0 / 3.1 s (3.1: the wake of 2026-11-14, at the
    #   device's latencies): the wake press's error at once, nothing sent.
    # - Two presses queued behind it, made 0.3 and 0.6 s after: both after the
    #   exit_standby, so inside its window (the documented limit). The first waits; its
    #   last attempt starts when the window ends (the exit_standby's handling + 4 s),
    #   connects and sends. Then the Wi-Fi drops again before the second is handled,
    #   after the window: it must fail at once. The gap moves with the exit_standby (as
    #   late as the core sends it late): no reply lost (as RZN), the first queued press's
    #   IR at ~4.06 s and the second handled at ~4.18 s (converted, ~4.15 and ~4.27 s);
    #   the first replies lost (as RZD), ~5.08 and ~5.20 s. Each drop sits at least
    #   20 ms inside its gap even 30 ms late; a run whose drop misses it is INVALID (hit
    #   "queued", anchored on the first queued press).
    # - The Wi-Fi never back: the wake press's error at once, nothing sent.
    for kind, exits in (("mem", (0.01, 0.035, 0.06)), ("conv", (0.06, 0.01, 0.035))):
        for up, exit_after in zip((1.0, 2.0, 3.1), exits):
            slow = up == 3.1  # the device's latencies (W2), as on 2026-11-14
            add(
                "PB",
                f"{kind} wake press {exit_after * 1000:.0f} ms before exit_standby, "
                f"Wi-Fi back at {up} s"
                + (", hello 63 ms + auth 23 ms" if slow else ""),
                _press_first(exit_after),
                kind=kind,
                down=[(-1.0, up)],
                duration=12.0,
                quick=(kind, up) == ("mem", 3.1),
                **({"latency": [0.063, 0.023]} if slow else {}),
            )
    queued = [_pr(0.3, "VOLUP", "second"), _pr(0.6, "MUTE", "third")]
    for flap in (4.11, 4.135, 4.16):
        add(
            "PB",
            "mem wake press 30 ms before exit_standby + VOLUP at 0.3 s + MUTE at 0.6 s, "
            f"Wi-Fi up 3.9..{flap:g} s, back at 6.1 s",
            _press_first(0.03) + queued,
            kind="mem",
            down=[(-1.0, 3.9), (flap, 6.1)],
            duration=14.0,
            hit="queued",
            anchor="second",
            quick=flap == 4.135,
        )
    for flap in (5.13, 5.155, 5.18):
        add(
            "PB",
            "mem wake press 50 ms before exit_standby + VOLUP at 0.3 s + MUTE at 0.6 s, "
            f"Wi-Fi up 3.97..{flap:g} s, back at 6.9 s, first hello replies lost",
            _press_first(0.05) + queued,
            kind="mem",
            down=[(-1.0, 3.97), (flap, 6.9)],
            duration=14.0,
            lose_first_hello=True,
            hit="queued",
            anchor="second",
        )
    add(
        "PB",
        "conv wake press 20 ms before exit_standby + VOLUP at 0.3 s + MUTE at 0.6 s, "
        "Wi-Fi up 3.9..4.22 s, back at 6.1 s",
        _press_first(0.02) + queued,
        kind="conv",
        down=[(-1.0, 3.9), (4.22, 6.1)],
        duration=14.0,
        hit="queued",
        anchor="second",
    )
    for kind, exit_after in (("mem", 0.01), ("conv", 0.06)):
        add(
            "PB",
            f"{kind} wake press {exit_after * 1000:.0f} ms before exit_standby, the "
            "Wi-Fi never back",
            _press_first(exit_after),
            kind=kind,
            down=[(-1.0, None)],
            duration=10.0,
        )

    # ---- CN (fix round 4) one wake with both wake events, the core's connect and
    # exit_standby, and a press between them; presses queued behind. Only the wake's
    # first wake event starts the window, whatever the order. A second one queued
    # behind a press that waits in that window starts none, so the presses queued
    # behind it are handled after the window: with the Wi-Fi still down they fail at
    # once. Before fix round 4 the core's connect restarted the window there (round 2
    # also a second exit_standby): those presses waited a second time and could be
    # sent late, after their 408 at the 6 s maximum (round-3 review, windows F3). The
    # orders:
    # - A: exit_standby, the wake press, connect;
    # - B: connect, the wake press, exit_standby (the connect, first, starts it);
    # - C: the wake press (in standby: an error at once with the Wi-Fi down, as PB),
    #   exit_standby, connect: the connect is handled right after the exit_standby,
    #   so it would move the window by only ~30 ms; C2 puts a press between them.
    # With the Wi-Fi back after the window (6.5 s; 11 s at the 6 s maximum): the press
    # waiting in the window gets its error when it ends, every press queued behind the
    # second wake event an error at once, nothing is sent later, and a press after the
    # Wi-Fi's return is sent. Back inside the window (2.0 s): the presses queued behind
    # the waiting press are sent once it connects (the documented limit).
    # - "connect only" (round-3 review, windows F3 / F7): enter_standby, then the core's
    #   connect and no exit_standby. The connect starts the window; an outage 8 s later
    #   finds none, so its presses fail at once.
    def cn_timeline(order: str) -> list[dict]:
        second, third = _pr(0.3, "VOLUP", "second"), _pr(0.6, "MUTE", "third")
        if order == "A":
            return [
                _ev(-2.0, "enter_standby"),
                _ev(0.0, "exit_standby"),
                _pr(0.058, "POWER", "wake"),
                _ev(0.1, "connect"),
                second,
                third,
            ]
        if order == "B":
            return [
                _ev(-2.0, "enter_standby"),
                _ev(0.0, "connect"),
                _pr(0.058, "POWER", "wake"),
                _ev(0.1, "exit_standby"),
                second,
                third,
            ]
        if order == "C":
            return [
                _ev(-2.0, "enter_standby"),
                _pr(0.0, "POWER", "wake"),
                _ev(0.03, "exit_standby"),
                _ev(0.06, "connect"),
                second,
                third,
            ]
        return [  # C2
            _ev(-2.0, "enter_standby"),
            _pr(0.0, "POWER", "wake"),
            _ev(0.03, "exit_standby"),
            second,
            _ev(0.45, "connect"),
            third,
            _pr(0.9, "INPUT", "fourth"),
        ]

    cn_orders = {
        "A": "exit_standby / wake press / connect",
        "B": "connect / wake press / exit_standby",
        "C": "wake press / exit_standby / connect",
        "C2": "wake press / exit_standby / VOLUP / connect",
    }
    for order, back, kind in (
        ("A", 6.5, "mem"),
        ("A", 6.5, "conv"),
        ("A", 11.0, "mem"),
        ("A", 2.0, "mem"),
        ("B", 6.5, "mem"),
        ("B", 11.0, "mem"),
        ("B", 2.0, "mem"),
        ("C", 6.5, "mem"),
        ("C", 2.0, "mem"),
        ("C2", 6.5, "mem"),
        ("C2", 11.0, "mem"),
    ):
        wait = WAKE_WAIT_MAX if back == 11.0 else WAKE_WAIT
        timeline = cn_timeline(order)
        if back > wait:  # back after the window: a press after the return is sent
            timeline.append(_pr(back + 1.0, "CHECK", "after"))
        add(
            "CN",
            f"wake {wait:g} s: {kind} {order} {cn_orders[order]}, presses queued, "
            f"Wi-Fi back at {back:g} s",
            timeline,
            kind=kind,
            down=[(-1.0, back)],
            duration=max(8.0, back + 3.5),
            settings=None if wait == WAKE_WAIT else {"wake_wait_sec": wait},
            quick=(order, back, kind) in (("A", 6.5, "mem"), ("C2", 6.5, "mem")),
        )
    add(
        "CN",
        "mem connect only (enter_standby, connect, no exit_standby), Wi-Fi back at 1.0 s; "
        "an outage 8.0..9.5 s, presses at 8.1 / 8.4 s",
        [
            _ev(-2.0, "enter_standby"),
            _ev(0.0, "connect"),
            _pr(0.058, "POWER", "wake"),
            _pr(8.1, "VOLUP", "o1"),
            _pr(8.4, "MUTE", "o2"),
            _pr(10.5, "CHECK", "after"),
        ],
        kind="mem",
        down=[(-1.0, 1.0), (8.0, 9.5)],
        duration=13.0,
    )

    # ---- W2W (fix round 4) two wakes in one run. The plain ENTER_STANDBY listener
    # clears the driver's flag, so the next wake's first wake event starts a window of
    # its own. Without that the second wake would get none and its press would fail at
    # once (in this harness the first wake too: the setup's connect set the flag).
    # Wake 1: exit_standby at 0, the Wi-Fi back at 1.0 s, the wake press sent then, a
    # press at 2.0 s sent at once; enter_standby at 6 s, the Wi-Fi down from 7 s; wake 2
    # at 8 s, the Wi-Fi back 1-2 s later: its press waits and is sent. Also by the
    # core's connect instead of exit_standby.
    # - A bounce queued behind a waiting press: wake 1's press waits with the Wi-Fi down
    #   until its window ends (4 s); enter_standby (1.0 s) and exit_standby (2.0 s) come
    #   meanwhile and are handled one right after the other when it is answered; then a
    #   press made at 2.05 s. The listener runs inline, so that exit_standby finds the
    #   flag clear (the framework's async standby handler hasn't run yet) and starts
    #   wake 2's window at ~4 s; the press waits in it and is sent when the Wi-Fi is
    #   back at 5.0 s, well before its 408. Back at 5.0 s, not later: a driver that
    #   gave wake 2 no window fails both presses at once, and wake 2's window would
    #   then start at 2.0 s, so a later return would let the test accept that.
    def two_wakes(second_event: str, code2: str) -> list[dict]:
        return [
            _ev(-2.0, "enter_standby"),
            _ev(0.0, "exit_standby"),
            _pr(0.058, "POWER", "wake"),
            _pr(2.0, "VOLUP", "awake"),
            _ev(6.0, "enter_standby"),
            _ev(8.0, second_event),
            _pr(8.058, code2, "wake2"),
        ]

    for kind, second_event, code2, back in (
        ("mem", "exit_standby", "POWER", 9.0),
        ("mem", "exit_standby", "POWER", 10.0),
        ("conv", "exit_standby", "INPUT", 10.0),
        ("mem", "connect", "POWER", 9.5),
    ):
        add(
            "W2W",
            f"{kind} wake 2 by {second_event} at 8 s, Wi-Fi down 7..{back:g} s",
            two_wakes(second_event, code2),
            kind=kind,
            down=[(-1.0, 1.0), (7.0, back)],
            duration=13.0,
            quick=(kind, back) == ("mem", 9.0),
        )
    add(
        "W2W",
        "mem a bounce (enter_standby 1.0 s, exit_standby 2.0 s) queued behind the wake "
        "press waiting with the Wi-Fi down, a press at 2.05 s, Wi-Fi back at 5.0 s",
        [
            _ev(-2.0, "enter_standby"),
            _ev(0.0, "exit_standby"),
            _pr(0.058, "POWER", "wake"),
            _ev(1.0, "enter_standby"),
            _ev(2.0, "exit_standby"),
            _pr(2.05, "VOLUP", "wake2"),
        ],
        kind="mem",
        down=[(-1.0, 5.0)],
        duration=13.0,
        quick=True,
    )

    # ---- MD (fix round 4) two Broadlinks, A and B. The wake's first wake event starts
    # every Broadlink's window at once, so a press for B queued behind A's waiting wake
    # press is handled after the window: with the Wi-Fi still down it fails at once,
    # nothing sent later. In round 3 a wake press for A made before exit_standby started
    # only A's window, and the exit_standby behind it gave B a late window: B's press
    # was sent after its 408 at the 6 s maximum (round-3 review, windows F1).
    # - exit_standby first, A's press at 0.058 s, B's at 0.3 s. The Wi-Fi back after
    #   the window (6.5 s; 11 s at 6 s): A's error when the window ends, B's at once,
    #   and a press for B after the return is sent. Back inside it (2.0 s): both sent.
    # - A's press before exit_standby (as PB; the review's timeline): A's error at once.
    #   B's press comes after the exit_standby, so it waits in the window: with the
    #   Wi-Fi back at 11 s (6 s) an error when it ends, nothing sent; back at 3.0 s,
    #   sent.
    for first, back in (
        ("exit", 6.5),
        ("exit", 11.0),
        ("exit", 2.0),
        ("press", 11.0),
        ("press", 3.0),
    ):
        wait = WAKE_WAIT_MAX if back == 11.0 else WAKE_WAIT
        if first == "exit":
            timeline = [
                _ev(-2.0, "enter_standby"),
                _ev(0.0, "exit_standby"),
                _pr(0.058, "POWER", "wake"),
                _pr(0.3, "VOLUP", "second", "B"),
            ]
            what = "exit_standby, A's wake press at 0.058 s, B's press at 0.3 s"
        else:
            timeline = [
                _ev(-2.0, "enter_standby"),
                _pr(0.0, "POWER", "wake"),
                _ev(0.03, "exit_standby"),
                _pr(0.3, "VOLUP", "second", "B"),
            ]
            what = "A's wake press 30 ms before exit_standby, B's press at 0.3 s"
        if back > wait:
            timeline.append(_pr(back + 1.0, "CHECK", "after", "B"))
        add(
            "MD",
            f"wake {wait:g} s: mem two Broadlinks, {what}, Wi-Fi back at {back:g} s",
            timeline,
            kind="mem",
            down=[(-1.0, back)],
            duration=max(8.0, back + 3.5),
            settings=None if wait == WAKE_WAIT else {"wake_wait_sec": wait},
            broadlinks=["A", "B"],
            quick=(first, back) == ("press", 11.0),
        )

    # ---- EX (fix round 4, from the round-4 test review) enter_standby INSIDE the
    # previous wake's window (a wake forced back to sleep 0.5 s later), then the next
    # wake's press before its exit_standby, and a press queued behind it; the Wi-Fi
    # down throughout. enter_standby ends the window, so the wake press fails at once
    # and the queued press gets the next exit_standby's window, which ends before the
    # Wi-Fi is back: an error, nothing sent (before the fix the wake press waited in
    # the old window and the queued one in a second, and was sent after its 408).
    for wait, back in ((WAKE_WAIT, 7.5), (WAKE_WAIT_MAX, 11.5)):
        add(
            "EX",
            f"wake {wait:g} s: exit_standby 0, enter_standby 0.5, wake press 1.0 (before "
            f"exit_standby 1.03), VOLUP 1.3, Wi-Fi back at {back:g} s",
            [
                _ev(-2.0, "enter_standby"),
                _ev(0.0, "exit_standby"),
                _ev(0.5, "enter_standby"),
                _pr(1.0, "POWER", "wake"),
                _ev(1.03, "exit_standby"),
                _pr(1.3, "VOLUP", "second"),
            ],
            kind="mem",
            down=[(-1.0, back)],
            duration=back + 4.0,
            settings=None if wait == WAKE_WAIT else {"wake_wait_sec": wait},
        )

    # ---- LP (fix round 5) a wake whose enter_standby the driver never saw (a driver
    # started or reconnected while the Remote slept): the setup's connect started this
    # session's window about 68 s before the exit_standby (more than WAKE_EVENT_LAPSE),
    # so the exit_standby starts a window of its own; the wake press, its connection
    # stale and the Wi-Fi down for ~1.5 s, waits and is sent (round 4 answered it 503
    # at once).
    add(
        "LP",
        "mem exit_standby at 62 s, ~68 s after the setup's connect, no enter_standby, "
        "Wi-Fi down 61.5..63 s",
        [
            _pr(0.5, "VOLUP", "early"),  # starts the timeline at 0.2 s
            _ev(62.0, "exit_standby"),
            _pr(62.06, "POWER", "wake"),
        ],
        kind="mem",
        down=[(61.5, 63.0)],
        duration=67.0,
    )
    return runs


# =============================================================================== child

PRESS: contextvars.ContextVar = contextvars.ContextVar("bl9_press", default=None)
EXPECTED_NET: contextvars.ContextVar = contextvars.ContextVar("bl9_net", default=False)
REC: list[dict] = []
LOGS: list[dict] = []
ACTIVE = [0]  # requests the integration is handling right now
SCENARIO_RIDS: set[int] = set()  # request ids of the presses the fake core sent
# BL-9 (fix round 4): the run's Broadlinks, address -> label (MD: "A" and "B"). A
# hello to any other address gets no answer.
ADDRESSES: dict[str, str] = {BL_IP: "A"}


def _broadlinks(run: dict) -> list[str]:
    """Return the labels of the run's Broadlinks: ["A"], or ["A", "B"] (MD)."""
    return list((run.get("params") or {}).get("broadlinks") or ["A"])


class Net:
    """Scenario clock, the Remote's network and the Broadlink, as the fakes see them."""

    def __init__(self, spec: dict | None = None):
        """Take the schedules of a run (None: the network always up)."""
        spec = spec or {}
        params = spec.get("params") or {}
        self.t0 = None  # monotonic time of the scenario's t=0; None during setup
        self.down = [tuple(x) for x in spec.get("down", [])]
        self.silent = [tuple(x) for x in spec.get("silent", [])]
        # J3, J5: the first directed hello that reaches the Broadlink after the network
        # returns gets no answer; the library's resend 1 s later does.
        self.lose_first_hello = bool(params.get("lose_first_hello"))
        # P2: the first network-down failure after the wake takes [delay] s, [count] times
        self.slow_fail = list(params["slow_fail"]) if params.get("slow_fail") else None
        self._lost_for = None
        self._lock = threading.Lock()

    def now(self) -> float:
        """Return the scenario time (-1e6 during setup)."""
        return -1e6 if self.t0 is None else time.monotonic() - self.t0

    @staticmethod
    def _inside(intervals, t) -> bool:
        return any(a <= t and (b is None or t < b) for a, b in intervals)

    def up(self) -> bool:
        """Return whether the Remote's network is up."""
        return self.t0 is None or not self._inside(self.down, self.now())

    def answers(self) -> bool:
        """Return whether the Broadlink answers."""
        return self.t0 is None or not self._inside(self.silent, self.now())

    def lose_this_hello(self) -> bool:
        """Return True for the first directed hello after the network came back."""
        if not self.lose_first_hello or self.t0 is None:
            return False
        now = self.now()
        ends = [b for _, b in self.down if b is not None and b <= now]
        if not ends:
            return False
        with self._lock:
            if self._lost_for == max(ends):
                return False
            self._lost_for = max(ends)
            return True

    def fail_delay(self) -> float:
        """Return how long this network-down failure takes (P2: the first is slow)."""
        with self._lock:
            slow = self.slow_fail
            if slow and slow[1] > 0 and self.t0 is not None and self.now() >= -0.5:
                slow[1] -= 1
                return slow[0]
        return 0.0


NET = Net()


def rec(kind: str, **kw) -> dict:
    """Record an event of the run, at the scenario time."""
    kw["kind"] = kind
    kw.setdefault("t", round(NET.now(), 4))
    REC.append(kw)
    return kw


def _unreachable() -> OSError:
    return OSError(errno.ENETUNREACH, "Network is unreachable")


def _exchange(
    timeout: float, latency: float, what: str, on_received=None, dev: str | None = "A"
) -> bool:
    """Make one python-broadlink request, resent every 1 s until ``timeout``.

    Return True when answered, False on timeout; raise OSError(101) while the Remote's
    network is down, as the library's ``sendto`` does. ``dev``: the Broadlink asked
    (its label; None: an address no Broadlink of the run has, which never answers).
    """
    start = time.monotonic()
    t_start = round(NET.now(), 4)
    press = PRESS.get()
    while True:
        if not NET.up():
            delay = NET.fail_delay()
            if delay:
                time.sleep(delay)
            rec(
                what,
                start=t_start,
                result="ENETUNREACH",
                press=press,
                active=ACTIVE[0],
                dev=dev,
            )
            raise _unreachable()
        if (
            dev is not None
            and NET.answers()
            and not (what == "hello" and NET.lose_this_hello())
        ):
            if on_received is not None:
                on_received()
            time.sleep(latency)
            rec(
                what, start=t_start, result="ok", press=press, active=ACTIVE[0], dev=dev
            )
            return True
        left = timeout - (time.monotonic() - start)
        if left <= 0:
            break
        time.sleep(min(RETRY_INTVL, left))
        if time.monotonic() - start >= timeout:
            break
    rec(what, start=t_start, result="timeout", press=press, active=ACTIVE[0], dev=dev)
    return False


def _timeout_error(timeout):
    from broadlink.exceptions import NetworkTimeoutError

    return NetworkTimeoutError(
        -4000, "Network timeout", f"No response received within {timeout}s"
    )


class FakeBroadlinkDevice:
    """A python-broadlink RM4 device: blocking calls, timed like the library."""

    TYPE = "RM4MINI"

    def __init__(self, address: str = BL_IP):
        """Create the device the fake hello and discovery return for ``address``."""
        self.label = ADDRESSES.get(address)  # BL-9: "A" or "B" (MD)
        self.mac = bytes.fromhex(BROADLINKS.get(self.label, (address, MAC))[1])
        self.host = (address, 80)
        self.devtype = 0x6539
        self.timeout = 10
        self.name = "RM4 mini"
        self.model = "RM4 mini"
        self.manufacturer = "Broadlink"
        self.is_locked = False
        self.lock = threading.Lock()

    def auth(self):
        """Authenticate."""
        if not _exchange(self.timeout, AUTH_LATENCY, "auth", dev=self.label):
            raise _timeout_error(self.timeout)
        return True

    def send_data(self, data):
        """Send an IR code; it counts as sent when the Broadlink receives it."""

        def received():
            rec(
                "ir",
                rid=PRESS.get(),
                sha=hashlib.sha1(bytes(data)).hexdigest()[:12],
                dev=self.label,
            )

        if not _exchange(
            self.timeout, SEND_LATENCY, "send", on_received=received, dev=self.label
        ):
            raise _timeout_error(self.timeout)

    def hello(self, local_ip_address=None):  # noqa: ARG002
        """Say hello to the device."""
        if not _exchange(self.timeout, HELLO_LATENCY, "dev_hello", dev=self.label):
            raise _timeout_error(self.timeout)
        return True

    def get_fwversion(self):
        """Return the firmware version."""
        if not _exchange(self.timeout, AUTH_LATENCY, "fwversion", dev=self.label):
            raise _timeout_error(self.timeout)
        return 52

    def ping(self):
        """Ping the device (fails while the network is down)."""
        if not NET.up():
            raise _unreachable()


def fake_hello(ip_address, port=80, timeout=10):  # noqa: ARG001
    """Replace ``broadlink.hello``: a directed hello to one address."""
    label = ADDRESSES.get(str(ip_address))
    if not _exchange(timeout, HELLO_LATENCY, "hello", dev=label):
        raise _timeout_error(timeout)
    return FakeBroadlinkDevice(str(ip_address))


def fake_xdiscover(
    timeout=10,
    local_ip_address=None,  # noqa: ARG001
    discover_ip_address="255.255.255.255",
    discover_ip_port=80,
):
    """Replace ``broadlink.xdiscover``: a broadcast discovery (every Broadlink)."""
    if _exchange(timeout, HELLO_LATENCY, "discover"):
        for address in ADDRESSES:
            yield FakeBroadlinkDevice(address)


def fake_discover(
    timeout=10,
    local_ip_address=None,
    discover_ip_address="255.255.255.255",
    discover_ip_port=80,
):
    """Replace ``broadlink.discover``."""
    return list(
        fake_xdiscover(timeout, local_ip_address, discover_ip_address, discover_ip_port)
    )


def fake_ping(ip_address, port=80):  # noqa: ARG001
    """Replace ``broadlink.ping``."""
    if not NET.up():
        raise _unreachable()


REAL_REMOTE = None  # unfurled's real Remote class


async def real_convert(url, code, ir_format):
    """Convert with the real unfurled against ``url``: its real errors and timeouts."""
    remote = REAL_REMOTE(url, api_key="key", wake_if_asleep=False)
    try:
        return await remote.ir.convert(code, format=ir_format)
    finally:
        await remote.close()


class FakeIR:
    """unfurled's IR submodule, ``convert`` only."""

    def __init__(self, url):
        """Remember the Remote's API address."""
        self._url = url

    async def convert(self, code, *, format=None, repeat=None):  # noqa: A002,ARG002
        """Convert a code: 0.1 s while the network is up, else the real failure."""
        t_start = round(NET.now(), 4)
        press = PRESS.get()
        if NET.up():
            await asyncio.sleep(CONVERT_LATENCY)
        if not NET.up():
            token = EXPECTED_NET.set(True)
            try:
                await real_convert(self._url, code, format)
            except BaseException as err:
                rec("convert", start=t_start, result=type(err).__name__, press=press)
                raise
            finally:
                EXPECTED_NET.reset(token)
            raise RuntimeError("a request to the Remote's address worked: not offline")
        rec("convert", start=t_start, result="ok", press=press)
        return {"raw": raw_for(code)}


class FakeRemote:
    """unfurled.Remote as far as a conversion uses it; refuses what the real one does."""

    def __init__(
        self,
        api_url,
        *,
        pin=None,
        api_key=None,
        session=None,
        wake_if_asleep=True,
        **kwargs,
    ):
        """Check the arguments with the real class, then fake the IR submodule."""
        REAL_REMOTE(api_url, pin=pin, api_key=api_key, wake_if_asleep=wake_if_asleep)
        rec("remote_created", wake_if_asleep=wake_if_asleep, extra=sorted(kwargs))
        self.ir = FakeIR(api_url)

    async def close(self):
        """Close nothing."""
        return None


def _loopback(address) -> bool:
    try:
        host = str(address[0])
    except Exception:
        return True
    return host.startswith("127.") or host in ("::1", "localhost")


def install_fakes():
    """Patch python-broadlink, unfurled and the socket layer before the driver loads."""
    global REAL_REMOTE
    import broadlink
    import unfurled
    import unfurled.remote

    broadlink.hello = fake_hello
    broadlink.xdiscover = fake_xdiscover
    broadlink.discover = fake_discover
    broadlink.ping = fake_ping
    REAL_REMOTE = unfurled.Remote
    unfurled.Remote = FakeRemote
    unfurled.remote.Remote = FakeRemote

    # A UDP connect() sends nothing, it only looks up a route: modelled (it fails with
    # ENETUNREACH while the Remote's network is down). Any other network access is not
    # modelled: recorded and failed, as on a Remote without a route (the run INVALID).
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_sendto = socket.socket.sendto
    orig_send = socket.socket.send
    route_checked = weakref.WeakSet()

    def unmodelled(sock, address) -> bool:
        return (
            sock.family in (socket.AF_INET, socket.AF_INET6)
            and not _loopback(address)
            and not EXPECTED_NET.get()
        )

    def connect(self, address):
        if unmodelled(self, address):
            if self.type == socket.SOCK_DGRAM:
                rec("route_check", addr=str(address), up=NET.up())
                if not NET.up():
                    raise _unreachable()
                route_checked.add(self)
                return None
            rec("unmodelled_net", op="connect", addr=str(address))
            raise _unreachable()
        return orig_connect(self, address)

    def connect_ex(self, address):
        if unmodelled(self, address):
            if self.type == socket.SOCK_DGRAM:
                rec("route_check", addr=str(address), up=NET.up())
                if not NET.up():
                    return errno.ENETUNREACH
                route_checked.add(self)
                return 0
            rec("unmodelled_net", op="connect_ex", addr=str(address))
            return errno.ENETUNREACH
        return orig_connect_ex(self, address)

    def sendto(self, data, *args):
        if args and unmodelled(self, args[-1]):
            rec("unmodelled_net", op="sendto", addr=str(args[-1]))
            raise _unreachable()
        return orig_sendto(self, data, *args)

    def send(self, data, *args):
        if self in route_checked:
            rec("unmodelled_net", op="send after a route check")
            raise _unreachable()
        return orig_send(self, data, *args)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.socket.send = send


def install_observers():
    """Attribute every IR send to its press; record handler starts, ends and answers."""
    import ucapi.api as uapi

    orig_entity_command = uapi.IntegrationAPI._entity_command
    orig_ack = uapi.IntegrationAPI.acknowledge_command

    async def entity_command(self, websocket, req_id, msg_data):
        rid = req_id if req_id in SCENARIO_RIDS else None
        token = PRESS.set(rid)
        ACTIVE[0] += 1
        rec("handler_start", rid=rid, req=req_id)
        try:
            return await orig_entity_command(self, websocket, req_id, msg_data)
        except BaseException as err:
            rec("raised", rid=rid, error=f"{type(err).__name__}: {err}")
            raise
        finally:
            ACTIVE[0] -= 1
            rec("handler_end", rid=rid, req=req_id)
            PRESS.reset(token)

    async def acknowledge_command(self, websocket, req_id, status_code=200):
        rec("ack", rid=PRESS.get(), req=req_id, code=int(status_code))
        return await orig_ack(self, websocket, req_id, status_code)

    uapi.IntegrationAPI._entity_command = entity_command
    uapi.IntegrationAPI.acknowledge_command = acknowledge_command


class LogCapture(logging.Handler):
    """Keep the driver's log lines, with the scenario time."""

    def emit(self, record):
        """Store one log line."""
        if len(LOGS) > 6000:
            return
        try:
            LOGS.append(
                {
                    "t": round(NET.now(), 4),
                    "name": record.name,
                    "level": record.levelname,
                    "msg": record.getMessage()[:400],
                }
            )
        except Exception:
            pass


async def _sleep_until(t: float):
    delay = t - NET.now()
    if delay > 0:
        await asyncio.sleep(delay)


def _timeline_start(spec: dict) -> float:
    """Return the scenario time the timeline starts at: 0.3 s before anything in it."""
    times = [item["at"] for item in spec["timeline"]]
    times += [x[0] for x in spec["down"]] + [x[0] for x in spec["silent"]]
    return min(times) - 0.3


class FakeCore:
    """The Remote's core, as the BL-9 research measured core-simulator 0.83.2."""

    def __init__(self, port: int, spec: dict):
        """Prepare the core of one run; ``run()`` connects it to the driver."""
        self.port = port
        self.spec = spec
        self.users: list[dict] = []
        self.reqs: dict[int, dict] = {}
        self.warm_rids: list[int] = []  # presses made during setup
        self.error = None
        self.setup_ok = False
        self.loop = None
        self.next_id = 1
        self.pending: dict[int, asyncio.Future] = {}
        self.ws = None
        self.emitter = None
        self.emitters: dict[str, str] = {}  # BL-9: label ("A", "B") -> IR emitter id
        self.remote = None

    def run(self):
        """Run the core in this thread until the timeline ends."""
        try:
            asyncio.run(self.main())
        except BaseException as err:
            self.error = (
                f"{type(err).__name__}: {err}\n{traceback.format_exc()[-1500:]}"
            )

    def result(self) -> dict:
        """Return what the core saw: presses, requests, setup state, errors."""
        users = [
            {k: v for k, v in u.items() if not k.startswith("_")} for u in self.users
        ]
        return {
            "users": users,
            "requests": list(self.reqs.values()),
            "error": self.error,
            "setup_ok": self.setup_ok,
            "warm_rids": self.warm_rids,
        }

    async def send(self, obj: dict):
        """Send one message to the integration."""
        await self.ws.send(json.dumps(obj))

    async def request(self, msg: str, data=None, timeout: float = 15.0) -> dict:
        """Send a request of the core's own and wait for its answer."""
        rid = self.next_id
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        obj = {"kind": "req", "id": rid, "msg": msg}
        if data is not None:
            obj["msg_data"] = data
        await self.send(obj)
        return await asyncio.wait_for(fut, timeout)

    async def send_event(self, msg: str):
        """Send a core event (exit_standby, enter_standby)."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())
        await self.send(
            {"kind": "event", "msg": msg, "cat": "REMOTE", "ts": ts, "msg_data": None}
        )

    def command(self, code: str, dev: str = "A") -> dict:
        """Return the entity command of a press of ``code`` for the Broadlink ``dev``."""
        if code == "LEARNED":  # the Remote entity, a code the integration learned
            return {
                "cmd_id": "send_cmd",
                "entity_id": self.remote,
                "entity_type": "remote",
                "params": {"command": "tv:power"},
            }
        return {
            "cmd_id": "send_ir",
            "entity_id": self.emitters[dev],
            "entity_type": "ir_emitter",
            "params": {"code": CODES[code], "port": "main"},
        }

    async def press(self, code: str, tag: str, dev: str = "A") -> dict:
        """Send the request of a user's press at once; return the press's record."""
        user = {
            "user": len(self.users),
            "tag": tag,
            "code": code,
            "dev": dev,  # BL-9: the Broadlink pressed (MD)
            "at": round(NET.now(), 4),
            "requests": [],
            "final": None,
            "_done": asyncio.Event(),
        }
        self.users.append(user)
        rid = self.next_id
        self.next_id += 1
        self.reqs[rid] = {
            "rid": rid,
            "user": user["user"],
            "code": code,
            "dev": dev,
            "at": user["at"],
            "answer": None,
            "core408": None,
            "late_answer": None,
        }
        user["requests"].append(rid)
        SCENARIO_RIDS.add(rid)
        await self.send(
            {
                "kind": "req",
                "id": rid,
                "msg": "entity_command",
                "msg_data": self.command(code, dev),
            }
        )
        self.loop.create_task(self.core_timeout(rid))
        return user

    def answered(self, req: dict, code: int, t: float):
        """Record the core's answer to a press: the integration's, or its own 408."""
        user = self.users[req["user"]]
        if user["final"] is None:
            user["final"] = {"t": round(t, 4), "code": code}
            user["_done"].set()

    async def core_timeout(self, rid: int):
        """Answer 408 when the integration has not answered within 10 s."""
        req = self.reqs[rid]
        await _sleep_until(req["at"] + CORE_TIMEOUT)
        if req["answer"] is None:
            req["core408"] = round(NET.now(), 4)
            self.answered(req, 408, NET.now())

    async def poll_entity_states(self):
        """Ask for the entity states, as the core does after a CONNECTED device state."""
        await asyncio.sleep(0.01)
        try:
            await self.request("get_entity_states", timeout=60.0)
        except Exception:
            pass

    async def reader(self):
        """Handle everything the integration sends."""
        async for raw in self.ws:
            msg = json.loads(raw)
            t = NET.now()
            kind = msg.get("kind")
            if kind == "resp":
                rid = msg.get("req_id")
                if rid in self.reqs:
                    req = self.reqs[rid]
                    answer = {"t": round(t, 4), "code": msg.get("code")}
                    if req["core408"] is not None:
                        req["late_answer"] = answer  # the core has answered 408 already
                    elif req["answer"] is None:
                        req["answer"] = answer
                        self.answered(req, msg.get("code"), t)
                elif rid in self.pending:
                    fut = self.pending.pop(rid)
                    if not fut.done():
                        fut.set_result(msg)
            elif kind == "req":  # the integration asks the core
                if msg.get("msg") == "get_supported_entity_types":
                    data = [
                        "button",
                        "switch",
                        "climate",
                        "cover",
                        "light",
                        "media_player",
                        "sensor",
                        "activity",
                        "macro",
                        "remote",
                        "ir_emitter",
                        "select",
                        "number",
                        "voice_assistant",
                    ]
                    await self.send(
                        {
                            "kind": "resp",
                            "req_id": msg.get("id"),
                            "msg": "supported_entity_types",
                            "code": 200,
                            "msg_data": data,
                        }
                    )
                else:
                    await self.send(
                        {
                            "kind": "resp",
                            "req_id": msg.get("id"),
                            "msg": "result",
                            "code": 200,
                            "msg_data": {},
                        }
                    )
            elif kind == "event":
                data = msg.get("msg_data") or {}
                if msg.get("msg") == "device_state":
                    rec("device_state", state=data.get("state"))
                    if data.get("state") == "CONNECTED":
                        self.loop.create_task(self.poll_entity_states())
                elif msg.get("msg") == "entity_change":
                    state = (data.get("attributes") or {}).get("state")
                    if data.get("entity_id") == self.emitter and state is not None:
                        rec("emitter_state", state=state)

    async def run_timeline(self):
        """Play the timeline: core events, presses and activities at their times."""
        items = sorted(self.spec["timeline"], key=lambda item: item["at"])
        start = _timeline_start(self.spec)
        NET.t0 = time.monotonic() - start  # now() == start
        rec("timeline_start", start=start)
        tasks = []
        for item in items:
            await _sleep_until(item["at"])
            if item["type"] == "event":
                await self.send_event(item["msg"])
                rec("event_sent", msg=item["msg"])
            elif item["type"] == "press":
                await self.press(item["code"], item["tag"], item.get("dev", "A"))
            else:
                tasks.append(asyncio.create_task(self.sequence(item)))
        await _sleep_until(self.spec["duration"])
        for task in tasks:
            task.cancel()

    async def sequence(self, item: dict):
        """Press an activity's steps, each ``gap`` s after the previous step's answer."""
        for i, code in enumerate(item["codes"]):
            user = await self.press(code, f"s{i + 1}")
            await user["_done"].wait()
            await asyncio.sleep(item["gap"])

    async def main(self):
        """Connect as core-simulator 0.83.2 does, warm the codes up, play the timeline."""
        import websockets

        self.loop = asyncio.get_running_loop()
        async with websockets.connect(
            f"ws://127.0.0.1:{self.port}",
            max_size=None,
            ping_interval=None,
            open_timeout=20,
        ) as ws:
            self.ws = ws
            auth = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if auth.get("msg") != "authentication" or auth.get("code") != 200:
                raise RuntimeError(f"unexpected first message {auth}")
            reader = asyncio.create_task(self.reader())
            await self.request("get_driver_version")
            await self.send({"id": self.next_id, "kind": "event", "msg": "connect"})
            self.next_id += 1
            await self.request("get_entity_states")
            avail = await self.request("get_available_entities", {})
            entities = avail.get("msg_data", {}).get("available_entities", [])
            ids = [e["entity_id"] for e in entities]
            emitters = [
                e["entity_id"] for e in entities if e.get("entity_type") == "ir_emitter"
            ]
            # BL-9: each Broadlink's IR emitter, by the MAC its entity id ends with
            for label in _broadlinks(self.spec):
                mac = BROADLINKS[label][1]
                mine = [e for e in emitters if e.endswith(mac)]
                if not mine:
                    raise RuntimeError(
                        f"no IR emitter for Broadlink {label} among the available "
                        f"entities: {ids}"
                    )
                self.emitters[label] = mine[0]
            self.emitter = self.emitters["A"]
            remotes = [
                e["entity_id"]
                for e in entities
                if e.get("entity_type") == "remote" and e["entity_id"].endswith(MAC)
            ]
            self.remote = remotes[0] if remotes else None
            await self.request("subscribe_events", {"entity_ids": ids})
            await asyncio.sleep(0.5)
            # converted once, so remembered from now on (BL-9: on every Broadlink)
            for label in _broadlinks(self.spec):
                for code in self.spec["warm"]:
                    rid = self.next_id
                    SCENARIO_RIDS.add(rid)
                    self.warm_rids.append(rid)
                    try:
                        answer = await self.request(
                            "entity_command", self.command(code, label), timeout=20
                        )
                    except asyncio.TimeoutError:
                        raise RuntimeError(
                            f"setup press of {code} ({label}): no answer in 20 s"
                        ) from None
                    if answer.get("code") != 200:
                        raise RuntimeError(
                            f"setup press of {code} ({label}) answered "
                            f"{answer.get('code')}"
                        )
            await asyncio.sleep(0.3 + float(self.spec.get("settle") or SETTLE))
            self.setup_ok = True
            await self.run_timeline()
            reader.cancel()


async def _lag_monitor(box):
    while True:
        before = time.monotonic()
        await asyncio.sleep(0.05)
        lag = time.monotonic() - before - 0.05
        if NET.t0 is not None and lag > box[0]:
            box[0] = lag
            box[1] = round(NET.now(), 3)


# Each run's driver listens on PORT_BASE + its index (the parent hands it over), below
# Linux's ephemeral port range, so neither another run nor a client socket takes it.
PORT_BASE = 20000


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _code_names(api_key: bool) -> dict:
    """Return the sha1 prefix of each code's Broadlink data -> its name."""
    names = {hashlib.sha1(LEARNED_DATA).hexdigest()[:12]: "LEARNED"}
    try:
        import ir_converter

        for name, code in CODES.items():
            if api_key:
                data = ir_converter.pulses_to_broadlink_data(raw_for(code))
            else:
                data = ir_converter.convert_to_broadlink(code)
            names[hashlib.sha1(bytes(data)).hexdigest()[:12]] = name
    except Exception as err:
        names["error"] = f"{type(err).__name__}: {err}"
    return names


def require_offline():
    """Refuse to run with a network route: a request must fail as on a Remote offline."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(
            ("192.0.2.1", 9)
        )  # no packet is sent: only the route is looked up
        raise SystemExit("this process has a network route: run it with --network none")
    except OSError as err:
        if err.errno != errno.ENETUNREACH:
            raise SystemExit(f"unexpected routing error {err}") from err
    finally:
        probe.close()


def child(src: str, spec: dict) -> dict:
    """Run one scenario in this process and return its records."""
    global NET, HELLO_LATENCY, AUTH_LATENCY, ADDRESSES
    require_offline()
    params = spec.get("params") or {}
    if params.get("latency"):
        HELLO_LATENCY, AUTH_LATENCY = params["latency"]
    NET = Net(spec)
    labels = _broadlinks(spec)
    ADDRESSES = {BROADLINKS[label][0]: label for label in labels}  # BL-9: MD
    src = os.path.abspath(src)
    tmp = tempfile.mkdtemp(prefix="bl9_")
    # a port of its own from the parent: two runs that each picked a "free" port could
    # pick the same one, and one run's core would then talk to the other's driver
    port = int(spec.get("port") or 0) or _free_port()
    os.environ.update(
        {
            "UC_CONFIG_HOME": tmp,
            "UC_INTEGRATION_HTTP_PORT": str(port),
            "UC_INTEGRATION_INTERFACE": "127.0.0.1",
            "UC_DISABLE_MDNS_PUBLISH": "true",
            "UC_LOG_LEVEL": "DEBUG",
        }
    )
    devices = []
    for label in labels:  # BL-9: one entry per Broadlink of the run (MD: two)
        address, mac = BROADLINKS[label]
        device = {
            "identifier": mac,
            "name": "RM4" if label == "A" else f"RM4 {label}",
            "address": address,
            "data": {"tv": {"power": base64.b64encode(LEARNED_DATA).decode()}},
            "remote_address": REMOTE_URL if spec["api_key"] else None,
            "remote_api_key": "key" if spec["api_key"] else None,
        }
        # BL-9: the run's settings, stored in the device configuration as the setup
        # does
        device.update(spec.get("settings") or {})
        devices.append(device)
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
        json.dump(devices, f)
    with open(os.path.join(tmp, "driver.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "driver_id": "broadlink_driver",
                "version": "0.0.0-bl9",
                "min_core_api": "0.20.0",
                "name": {"en": "Broadlink"},
                "icon": "uc:wave-pulse",
                "developer": {"name": "bl9 scenarios"},
                "port": port,
            },
            f,
        )
    os.chdir(tmp)
    sys.path.insert(0, src)

    logging.getLogger().addHandler(
        LogCapture(level=logging.DEBUG)
    )  # basicConfig: no-op
    install_fakes()
    install_observers()
    import config_manager  # the tree's own

    fields = {
        field.name for field in dataclasses.fields(config_manager.BroadlinkConfig)
    }
    if spec.get("settings") and not set(SETTINGS) <= fields:
        return {
            "error": "the tree's BroadlinkConfig has no BL-9 setting",
            "setup_ok": False,
        }
    import driver as driver_module  # the tree's own driver.py

    names = _code_names(spec["api_key"])

    async def run() -> dict:
        loop = asyncio.get_running_loop()
        main_task = loop.create_task(driver_module.main(), name="driver-main")
        deadline = time.monotonic() + 40
        while True:
            if main_task.done():
                raise RuntimeError(
                    f"driver main() ended early: {main_task.exception()!r}"
                )
            try:
                _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        "the driver's WebSocket server did not start"
                    ) from None
                await asyncio.sleep(0.05)
        core = FakeCore(port, spec)
        lag = [0.0, None]
        monitor = loop.create_task(_lag_monitor(lag))
        thread = threading.Thread(target=core.run, daemon=True, name="core")
        thread.start()
        while thread.is_alive():
            await asyncio.sleep(0.1)
        monitor.cancel()
        out = core.result()
        if main_task.done() and not main_task.cancelled() and main_task.exception():
            out["driver_error"] = repr(main_task.exception())
        clients = {
            r["msg"]
            for r in LOGS
            if r["name"] == "ucapi.api" and r["msg"].startswith("WS: Client added")
        }
        if len(clients) != 1:
            out["error"] = (
                f"{len(clients)} core connections reached this run's driver, not 1 "
                "(a port shared with another run?): the run is not valid"
            )
        out["lag_max"] = round(lag[0], 4)
        out["lag_at"] = lag[1]
        return out

    # No asyncio.run(): its cleanup would cancel the driver and wait for fake requests
    # still sleeping in threads. The process exits right after printing the result.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        out = loop.run_until_complete(run())
    except BaseException as err:
        out = {
            "error": f"{type(err).__name__}: {err}",
            "setup_ok": False,
            "users": [],
            "requests": [],
        }
    # setup records are dropped, except an exception that left a command handler
    out["records"] = [
        r for r in REC if r.get("t", -1e6) > -1e5 or r["kind"] == "raised"
    ]
    out["code_names"] = names
    out["logs"] = [r for r in LOGS if r["t"] > -1e5]
    out["python"] = sys.version.split()[0]
    return out


# ============================================================================== parent


def tree_has_settings(src: str) -> bool:
    """Return whether the tree's BroadlinkConfig has the BL-9 setting (wake_wait_sec)."""
    try:
        with open(os.path.join(src, "config_manager.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "BroadlinkConfig":
            names = {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
            return set(SETTINGS) <= names
    return False


def _settings(run: dict) -> float:
    """Return the wake wait a run is judged by: its setting, or the default."""
    return (run.get("settings") or {}).get("wake_wait_sec", WAKE_WAIT)


def analyse(res: dict) -> dict:
    """Link IR sends to presses; flag LATE and LATE-AFTER-408.

    Also collect the pairs that may be a DOUBLE-TOGGLE (two late sends of one code
    within 1 s); ``double_toggles()`` decides which are, with the run's wake windows.
    """
    reqs = {r["rid"]: r for r in res.get("requests", [])}
    users = res.get("users", [])
    names = res.get("code_names", {})
    warm_rids = set(res.get("warm_rids", []))
    records = res.get("records", [])
    acks, starts, ends = {}, {}, {}
    for r in records:
        if r.get("rid") is None:
            continue
        if r["kind"] == "ack":
            acks.setdefault(r["rid"], r)
        elif r["kind"] == "handler_start":
            starts.setdefault(r["rid"], r["t"])
        elif r["kind"] == "handler_end":
            ends.setdefault(r["rid"], r["t"])
    for rid, req in reqs.items():
        req["started"] = starts.get(rid)
        # BL-9: when its handler ended; a core event sent behind it is handled after
        req["ended"] = ends.get(rid)
        ack = acks.get(rid)
        if (
            req.get("core408") is not None
            and req.get("late_answer") is None
            and ack is not None
            and ack["t"] >= req["core408"]
        ):
            req["late_answer"] = {"t": ack["t"], "code": ack["code"]}
        # what the integration answered: seen by the ack hook, or by the core
        req["integration_answer"] = (
            {"t": ack["t"], "code": ack["code"]}
            if ack is not None
            else req.get("late_answer") or req.get("answer")
        )
    for user in users:
        user["irs"] = []
    irs = []
    for r in records:
        if r["kind"] != "ir":
            continue
        ir = {
            "t": r["t"],
            "rid": r.get("rid"),
            "code": names.get(r.get("sha"), "?"),
            "dev": r.get("dev", "A"),  # BL-9: the Broadlink that received it (MD)
        }
        if ir["rid"] in warm_rids:  # a press made during setup, sent now
            ir.update(user=None, late=True, after408=None, warm=True)
            irs.append(ir)
            continue
        req = reqs.get(ir["rid"])
        if req is None or (ir["code"] != "?" and req["code"] != ir["code"]):
            # Sent from a task no press of this code started: attribute it to the press
            # of the same code (and Broadlink) whose handler was running then, else to
            # the earliest one made before it that has no IR yet.
            sent = {x["rid"] for x in irs}
            same = [
                q
                for q in sorted(reqs.values(), key=lambda q: q["at"])
                if q["code"] == ir["code"]
                and q.get("dev", "A") == ir["dev"]
                and q["at"] <= ir["t"]
            ]
            running = [
                q
                for q in same
                if q.get("started") is not None
                and q["started"] <= ir["t"] <= ends.get(q["rid"], float("inf"))
            ]
            waiting = [q for q in same if q["rid"] not in sent]
            req = (running or waiting or [None])[0]
            ir["rid"] = None if req is None else req["rid"]
            ir["by_code"] = True
        if req is not None:
            ir["user"] = req["user"]
            ir["late"] = ir["t"] - req["at"] > LATE_AFTER
            ir["after408"] = (
                req.get("core408") is not None and ir["t"] >= req["core408"]
            )
            users[req["user"]]["irs"].append(ir)
        else:
            ir.update(user=None, late=None, after408=None)
        irs.append(ir)
    toggles = [  # BL-9: maybe a DOUBLE-TOGGLE (double_toggles() decides)
        (i, j)
        for (i, first), (j, second) in itertools.combinations(enumerate(irs), 2)
        if first["code"] == second["code"]
        and abs(first["t"] - second["t"]) <= 1.0
        and (first["late"] or second["late"])
    ]
    return {
        "users": users,
        "reqs": reqs,
        "irs": irs,
        "toggles": toggles,
        "raised": [r for r in records if r["kind"] == "raised"],
        "unmodelled": [r for r in records if r["kind"] == "unmodelled_net"],
        "auth_ok": [
            r for r in records if r["kind"] == "auth" and r.get("result") == "ok"
        ],
        "connects": [
            r
            for r in records
            if r["kind"] in ("hello", "discover", "auth", "dev_hello", "fwversion")
        ],
        "sends": [r for r in records if r["kind"] == "send"],  # BL-9: the hit check
        # BL-9: when the core sent its events, in that order (the driver starts a wake
        # window as it handles one, ~0.5 ms later)
        "events": [(r["t"], r["msg"]) for r in records if r["kind"] == "event_sent"],
        "emitter_states": [
            (r["t"], r["state"]) for r in records if r["kind"] == "emitter_state"
        ],
        "unanswered": [
            rid for rid, req in reqs.items() if req["integration_answer"] is None
        ],
    }


def _inside(intervals, t) -> bool:
    return any(a <= t and (b is None or t < b) for a, b in intervals)


def _reach(run: dict, t: float, steady: float = 0.0):
    """Return when the Broadlink can be reached at or after ``t`` (None: never).

    With ``steady``, the first moment from which it stays reachable that long: a
    shorter stretch between two drops (the Wi-Fi flapping) is skipped.
    """
    blocked = [tuple(x) for x in run["down"]] + [tuple(x) for x in run["silent"]]
    while True:
        ends = [b for a, b in blocked if a <= t and (b is None or t < b)]
        if ends:
            if None in ends:
                return None
            t = max(ends)
            continue
        starts = [a for a, _ in blocked if a > t]
        if not starts or min(starts) - t >= steady:
            return t
        t = min(starts)


def _events(run: dict, a: dict | None = None) -> list[tuple[float, str]]:
    """Return the run's core events (time, msg) in order.

    The times the core sent them, when the run recorded them all, as for the presses:
    the core's thread can send one ~15 ms late, and the driver's window moves with it.
    Otherwise the timeline's.
    """
    planned = sorted(
        (
            (item["at"], item["msg"])
            for item in run["timeline"]
            if item["type"] == "event"
        ),
        key=lambda event: event[0],
    )
    sent = [tuple(event) for event in (a or {}).get("events") or []]
    if [msg for _, msg in sent] == [msg for _, msg in planned]:
        return sent
    return planned


def _handled(a: dict, user: dict) -> float | None:
    """Return when the driver started handling a press (None: never seen)."""
    if not user["requests"]:
        return None
    return a["reqs"][user["requests"][0]].get("started")


def _event_handled(a: dict, t: float) -> float:
    """Return when the driver handled a core event the core sent at ``t``.

    ucapi handles a connection's messages one at a time, in order (BL-9), so an event
    waits for the handlers of the presses sent before it; otherwise it is handled at
    once (~0.5 ms, not modelled).
    """
    ends = [r.get("ended") for r in a["reqs"].values() if r["at"] < t]
    return max([t] + [end for end in ends if end is not None])


def _windows(run: dict, a: dict) -> dict:
    """Return the start of the wake window each press is judged by: user -> time.

    None: no window. BL-9 fix round 4, one window per wake, as broadlink_driver keeps it
    (``_first_wake_event()``), replaying the core's events and presses in the order the
    core sent them (an event sent at or before a press is handled first) at the times
    the run observed:
    - Only a wake's first wake event, exit_standby or the core's connect, starts a
      window, when the driver handles it, for every Broadlink at once. A later one
      before the next enter_standby starts none, whatever the order and however late
      it is handled (queued behind a press waiting in the window: CN), unless it is
      handled WAKE_EVENT_LAPSE or more after the window's start (below).
    - enter_standby lets the next wake event start one. A press made after it and
      before that event (the press that woke the Remote reached the driver first: PB,
      CN, MD) is outside every window: enter_standby ends every window (rm.py
      note_standby), even one still within its wake wait (EX).
    - A wake event WAKE_EVENT_LAPSE after the last window's start, with no
      enter_standby between, starts its own (a driver that never saw the wake's
      enter_standby: LP).
    - The setup's connect, the first wake event since the driver's start, started a
      window at least 0.3 s + settle before the timeline's start and took that wake's
      turn: a wake event before the timeline's first enter_standby, within
      WAKE_EVENT_LAPSE of it, starts none (X1, RZC, the stale runs of W1, QW and MX).
    """
    events = _events(run, a)
    out: dict = {}
    start, started, done = None, True, 0
    # The setup's connect came at least 0.3 s + settle before the timeline's start (the
    # setup's warm presses before that): the latest it can have started a window.
    started_at = _timeline_start(run) - 0.3 - float(run.get("settle") or SETTLE)

    def event(t: float, msg: str):
        nonlocal start, started, started_at
        if msg == "enter_standby":
            start, started = None, False
        elif msg in WAKE_EVENTS:
            handled = _event_handled(a, t)
            if not started or handled - started_at >= WAKE_EVENT_LAPSE:
                start, started, started_at = handled, True, handled

    for user in sorted(a["users"], key=lambda u: u["at"]):
        while done < len(events) and events[done][0] <= user["at"]:
            event(*events[done])
            done += 1
        out[user["user"]] = start
    return out


def _wake_start(run: dict, a: dict, user: dict) -> float | None:
    """Return when the last wake window before a press started (None: none)."""
    return _windows(run, a).get(user["user"])


def _in_window(run: dict, user: dict, start: float | None) -> bool:
    """Return whether a press was made inside the wake window starting at ``start``."""
    wait = _settings(run)
    return start is not None and wait > 0 and user["at"] < start + wait


def expectations(run: dict, a: dict, wake_wait: float) -> dict:
    """Return what BL-9's wake window promises each press, by user index.

    "sent": answered 200 and sent once, at most ``allow`` s after ``send_from`` (plus
    QUEUE_STEP for every press sent before it from the same queue); "refused": an error
    by ``answer_by``, nothing sent; "either": one of the two, an error by ``answer_by``;
    "at once": an error within AT_ONCE of ``start`` (when the press was made, or when
    the driver handled it), nothing sent; None: not judged here (the Broadlink silent,
    BL-7's rules).

    One window per wake (BL-9 fix round 4), as broadlink_driver and rm.Broadlink keep it
    (``_windows()``, ``command_deadline()``):
    - A press made within the wake wait of the wake's first wake event (exit_standby or
      the core's connect, when the driver handled it) is in that wake window. It waits
      in it only, whatever its connect, conversion and send find later. It is sent at
      most the window's end + its slack, + QUEUE_STEP for every press sent before it
      from its queue (``check_press()``): the presses made inside the window are sent
      one after another once the wake press connects, each ~0.1-0.25 s after the one
      before (the documented limit).
    - A press made inside the window but handled after it ended (it queued behind a slow
      press, or behind a second wake event that starts nothing) is handled as a press
      outside it: ucapi doesn't say when a press was made. With the Wi-Fi down then, it
      must fail at once and is never sent. With the Broadlink reachable then, it may be
      sent then, within that bound.
    - Outside a window, a press with the Wi-Fi down fails at once (from when the driver
      handled it); nothing waits. So does a press handled in standby, before the wake's
      first wake event (PB, CN, MD).
    - A Broadlink reachable only for moments between drops (less than STEADY) may or
      may not be reached: "either".
    The judgement is by when the press was made, as the user sees it; when the driver
    handled it (observed) matters only for a press that queued past its window.
    """
    params = run["params"]
    lost = bool(params.get("lose_first_hello"))
    extra = 0.0  # the "sent" allowance's extra time for a lost first reply
    if lost:
        extra = LOST_REPLY + (DRIVER_HELLO if params.get("driver_hello") else 0.0)
    down = [tuple(x) for x in run["down"]]
    windows = _windows(run, a)
    out = {}
    for user in a["users"]:
        at = user["at"]
        first = _reach(run, at)  # the first moment the Broadlink can be reached
        steady = _reach(run, at, STEADY)  # ... and from which it stays reachable
        handled = _handled(a, user)
        woke = windows.get(user["user"])
        exp = {"reach": first, "window": None, "end": None, "expect": None}
        if steady == at:
            exp["expect"] = "sent"
        elif woke is not None and wake_wait > 0 and at < woke + wake_wait:
            end = woke + wake_wait
            exp.update(window="wake", end=end)
            silent = any(s < end and (b is None or b > at) for s, b in run["silent"])
            # the press's last attempt starts at the window's end; its hello listens
            # 1.4 s, so a lost first reply or a silent Broadlink makes it end later
            exp["slack"] = LATE_END if silent or lost else WINDOW_SLACK
            exp["answer_by"] = end + exp["slack"]
            # a silent Broadlink is heard only at the next hello resend (every 1 s)
            silent_end = any(b == steady for _, b in run["silent"])
            margin = RESCUE_MARGIN + (RESEND_WAIT if silent_end or lost else 0.0)
            late = handled is not None and handled >= end  # queued past its window
            if late and _inside(down, handled):
                exp.update(expect="at once", start=handled)
            elif steady is not None and steady <= end - margin:
                exp["expect"] = "sent"
            elif first is None or (
                first > end and not (late and _reach(run, handled) == handled)
            ):
                exp["expect"] = "refused"
            else:
                exp["expect"] = "either"
        elif first == at:
            # reachable when made, but only for a moment (the Wi-Fi flapping): one
            # attempt, which may or may not get through
            exp.update(expect="either", answer_by=at + LATE_END)
        elif _inside(down, at):
            # BL-9: from when the driver handled it (it may have queued behind a press
            # waiting in a window); the press itself when not seen
            exp.update(
                expect="at once", start=at if handled is None else max(at, handled)
            )
        if exp["expect"] == "sent":
            exp["send_from"] = max(at, steady)
            exp["allow"] = LATE_AFTER + extra
        out[user["user"]] = exp
    return out


def _outcome(user: dict) -> str:
    final = user["final"]
    txt = (
        "no answer"
        if final is None
        else f"answered {final['code']} at {final['t']:.2f}"
    )
    irs = ", ".join(f"{ir['t']:.2f}" for ir in user["irs"])
    return f"{txt}, IR at {irs}" if irs else f"{txt}, nothing sent"


def check_press(user: dict, exp: dict, a: dict) -> list[str]:
    """Return how a press broke what BL-9 promises it (empty: kept)."""
    kind = exp["expect"]
    if kind is None:
        return []
    tag, final, irs = user["tag"], user["final"], user["irs"]
    got = _outcome(user)
    error = final is not None and final["code"] != 200
    end = exp["end"]
    if kind == "at once":
        start = exp["start"]
        if error and final["t"] - start <= AT_ONCE and not irs:
            return []
        if end is None:
            why = "the Wi-Fi was down, outside every wake window"
        else:
            why = (
                f"made in its wake window (ends {end:.2f}) but handled at {start:.2f}, "
                "after it, with the Wi-Fi down"
            )
        return [
            f"{tag}: {got}; {why}: must be an error within {AT_ONCE:g} s of "
            f"{start:.2f}, nothing sent"
        ]
    where = "" if end is None else f" ({exp['window']} window ends {end:.2f})"
    reach = "never" if exp["reach"] is None else f"at {exp['reach']:.2f}"
    if kind == "refused":
        if error and final["t"] <= exp["answer_by"] and not irs:
            return []
        return [
            f"{tag}: {got}; the Broadlink could be reached {reach}{where}: must be an "
            f"error by {exp['answer_by']:.2f}, nothing sent"
        ]
    reasons = []
    if kind == "sent":
        if final is None or final["code"] != 200 or len(irs) != 1:
            return [
                f"{tag}: {got}; the Broadlink could be reached {reach}{where}: must be "
                "answered 200 and sent once"
            ]
        sent = irs[0]["t"]
        queued = _sent_before(user, a, exp["send_from"], sent)
        limit = exp["send_from"] + exp["allow"] + QUEUE_STEP * len(queued)
        if sent > limit:
            reasons.append(
                f"{tag}: IR at {sent:.2f}, after {limit:.2f} (the Broadlink could "
                f"be reached {reach}{where})"
            )
    elif final is None:  # "either"
        reasons.append(f"{tag}: no answer")
    elif final["code"] == 200 and len(irs) != 1:
        reasons.append(f"{tag}: {got}; answered 200 must mean sent once")
    elif final["code"] != 200 and (final["t"] > exp["answer_by"] or irs):
        reasons.append(
            f"{tag}: {got}; an error must come by {exp['answer_by']:.2f}, "
            "nothing sent"
        )
    if end is not None and irs:
        # BL-9: the window's end + its slack, and QUEUE_STEP for every press sent
        # before it from its queue, as the "sent" limit above: the presses made inside
        # the window go out one after another once the wake press connects (final
        # review: EQ, ZB10)
        sent = irs[0]["t"]
        slack = exp.get("slack", WINDOW_SLACK)
        queued = _sent_before(user, a, exp.get("send_from", exp["reach"]), sent)
        limit = end + slack + QUEUE_STEP * len(queued)
        if sent > limit:
            also = (
                f", + {QUEUE_STEP:g} s for each press sent before it from its queue "
                f"({len(queued)})"
                if queued
                else ""
            )
            reasons.append(
                f"{tag}: IR at {sent:.2f}, after {limit:.2f}: more than {slack:g} s "
                f"after its {exp['window']} window ended at {end:.2f}{also}"
            )
    return reasons


def _sent_before(user: dict, a: dict, since: float | None, sent: float) -> list[dict]:
    """Return the other presses' IRs sent from ``since`` until ``sent``: its queue.

    ucapi handles one press at a time, so a press is sent after those queued before
    it, each with its own conversion and send (QUEUE_STEP covers one of them).
    """
    since = -1e9 if since is None else since
    return [
        x for x in a["irs"] if x.get("user") != user["user"] and since <= x["t"] < sent
    ]


def hit_missed(run: dict, a: dict) -> list[str]:
    """Return why a run's second drop missed the moment it is placed for (empty: hit).

    A run whose Wi-Fi drops a second time tests something only if that drop comes where
    it is meant to; the re-review found TW / MX runs whose drop missed by a few ms and
    tested nothing. Such a run is INVALID, not PASS (``params["hit"]``):
    - "auth-send" (TW, MX): between a connect's auth and the send after it, so the
      send's first packet finds the network down. With an earlier drop the auth fails;
      with a later one the IR goes out.
    - "queued" (RZQ, RZD, RZN, RZ2, PB): after the press that waited in the window is
      sent (the wake press; PB's ``anchor``: the first press queued behind its wake
      press, which since fix round 4 fails at once in standby) and before each press
      queued behind it, made inside its window, is handled.
    """
    hit = run["params"].get("hit")
    if not hit:
        return []
    down = sorted(tuple(x) for x in run["down"])
    back, (flap, again) = down[0][1], down[1]
    if hit == "auth-send":
        auths = [
            r
            for r in a["connects"]
            if r["kind"] == "auth"
            and r.get("result") == "ok"
            and back <= r["start"] < flap
        ]
        if not auths:
            return [
                f"the second drop at {flap:g} s came before any auth after the Wi-Fi's "
                f"return at {back:g} s: the run tests nothing"
            ]
        auth = max(auths, key=lambda r: r["start"])
        sends = sorted(
            (r for r in a["sends"] if r["start"] >= auth["start"]),
            key=lambda r: r["start"],
        )
        if sends and sends[0]["result"] == "ENETUNREACH":
            return []
        what = (
            "no send followed it"
            if not sends
            else f"the send at {sends[0]['start']:.3f} s got {sends[0]['result']}"
        )
        return [
            f"the second drop at {flap:g} s missed the gap between the auth at "
            f"{auth['start']:.3f} s and the send ({what}): the run tests nothing"
        ]
    tag = run["params"].get("anchor", "wake")
    anchor = _tag(a, tag)
    sent = [] if anchor is None else [ir["t"] for ir in anchor["irs"]]
    if not sent or sent[0] >= flap:
        return [
            f"{tag} was not sent before the second drop at {flap:g} s: the run tests "
            "nothing"
        ]
    windows = _windows(run, a)
    woke = windows.get(anchor["user"])
    for user in a["users"]:
        # BL-9: the presses queued behind it, made inside its window
        if (
            user is anchor
            or user["at"] < anchor["at"]
            or windows.get(user["user"]) != woke
            or not _in_window(run, user, woke)
        ):
            continue
        handled = _handled(a, user)
        if handled is None or not flap <= handled < again:
            return [
                f"{user['tag']} was handled at {_fmt(handled)} s, not during the second "
                f"drop ({flap:g}..{again:g} s): the run tests nothing"
            ]
    return []


def hard_rules(a: dict) -> list[str]:
    """Return the breaches of the rules every run keeps."""
    reasons = []
    if a["raised"]:
        reasons.append(
            f"an exception left a command handler: {a['raised'][0]['error']}"
        )
    if a["unanswered"]:
        reasons.append(f"requests the integration never answered: {a['unanswered']}")
    warm = [ir["t"] for ir in a["irs"] if ir.get("warm")]
    if warm:
        reasons.append(f"a setup press (answered long before) was sent at {warm}")
    if any(ir["user"] is None and not ir.get("warm") for ir in a["irs"]):
        reasons.append("an IR send could not be attributed to a press")
    for user in a["users"]:
        tag = user["tag"]
        if len(user["irs"]) > 1:
            reasons.append(f"{tag}: sent {len(user['irs'])} times")
        wrong = [ir["dev"] for ir in user["irs"] if ir["dev"] != user.get("dev", "A")]
        if wrong:  # BL-9: MD, two Broadlinks
            reasons.append(
                f"{tag}: pressed for Broadlink {user.get('dev', 'A')}, sent to {wrong}"
            )
        for rid in user["requests"]:
            req = a["reqs"][rid]
            answer = req["integration_answer"]
            if req["core408"] is not None:
                reasons.append(
                    f"{tag}: the core answered 408 at {req['core408']:.2f} (no "
                    "answer within 10 s)"
                )
            if user["irs"] and answer is not None and answer["code"] != 200:
                reasons.append(f"{tag}: sent although answered {answer['code']}")
    after408 = [f"{ir['t']:.2f}" for ir in a["irs"] if ir.get("after408")]
    if after408:
        reasons.append(f"LATE-AFTER-408: IR sent after the core's 408 at {after408}")
    if any(ir.get("burst") for ir in a["irs"]):
        reasons.append("DOUBLE-TOGGLE: two late sends of one code within 1 s")
    return reasons


def double_toggles(run: dict, a: dict) -> None:
    """Flag the DOUBLE-TOGGLEs among ``analyse()``'s pairs (ir["burst"]).

    Two late sends of one code within 1 s, except (BL-9, final review) two presses of
    that code both made inside the same wake window and each sent once (ir["repeat"],
    REPEAT in the report): each was a real press, and the presses made inside the
    window are sent one after another once the wake press connects (the documented
    limit; ucapi gives no request time, so they can't be told from fresh presses). One
    press sent twice, or a press made outside the window, stays a DOUBLE-TOGGLE.
    """
    windows = _windows(run, a)
    users = a["users"]

    def real_press(index) -> bool:
        if index is None or len(users[index]["irs"]) != 1:
            return False
        return _in_window(run, users[index], windows.get(index))

    for i, j in a["toggles"]:
        first, second = a["irs"][i], a["irs"][j]
        one, two = first.get("user"), second.get("user")
        repeat = (
            one != two
            and real_press(one)
            and real_press(two)
            and windows.get(one) == windows.get(two)
        )
        flag = "repeat" if repeat else "burst"
        first[flag] = second[flag] = True


def _tag(a: dict, tag: str):
    for user in a["users"]:
        if user["tag"] == tag:
            return user
    return None


def _answer_delay(user):
    if user is None or user["final"] is None:
        return None
    return user["final"]["t"] - user["at"]


def _fmt(t) -> str:
    return "-" if t is None else f"{t:.2f}"


def rule_w1(run: dict, a: dict, ref: dict) -> list[str]:
    """W1: every IR within HEAD_EQUAL of HEAD's (except W1_NO_WINDOW's presses)."""
    key = f"{run['id']} {run['variant']}"
    expected = ref["ir"].get(key)
    no_window = W1_NO_WINDOW.get(key, ())
    if not expected and not no_window:
        return [f"no HEAD reference for {key!r}"]
    reasons = []
    for tag, t_ref in (expected or {}).items():
        if tag in no_window:
            continue  # BL-9 fix round 4: no window, judged by check_press (at once)
        user = _tag(a, tag)
        irs = [] if user is None else user["irs"]
        if len(irs) != 1 or abs(irs[0]["t"] - t_ref) > HEAD_EQUAL:
            got = ", ".join(f"{ir['t']:.3f}" for ir in irs) or "none"
            reasons.append(
                f"{tag}: IR at {got}, HEAD's at {t_ref:.3f} (equal within "
                f"{HEAD_EQUAL:g} s)"
            )
    return reasons


def _wake_end(run: dict, a: dict, user: dict) -> float | None:
    """Return when the last wake window before a press ends (None: no wake)."""
    woke = _wake_start(run, a, user)
    return None if woke is None else woke + _settings(run)


def _own_attempt_first(run: dict, a: dict, user: dict) -> bool:
    """Return whether a press's own hello was the first after its wake (BL-9).

    So it was handled before the driver's wake retry made its first attempt.
    """
    woke = _wake_start(run, a, user)
    if woke is None or not user["requests"]:
        return False
    hellos = [
        r
        for r in a["connects"]
        if r["kind"] == "hello" and r.get("start", -1e9) >= woke
    ]
    first = min(hellos, key=lambda r: r["start"], default=None)
    return first is not None and first.get("press") == user["requests"][0]


def rule_s1(run: dict, a: dict, ref: dict) -> list[str]:
    """S1: BL-7's timings, every answer within HEAD_EQUAL of HEAD's; nothing sent.

    BL-9: the presses in S1_WINDOW_END get their error when their wake window ends, or
    OWN_ATTEMPT_ERROR after they were handled when their own attempt came before the
    driver's: handled in the same instant as exit_standby, a press makes the first
    attempt itself, a hello that listens 3 s against the silent Broadlink, and BL-7's
    hold-off refuses its next attempt 0.5 s later (re-review: about 3.5 s, not 4 s).
    """
    key = f"{run['id']} {run['variant']}"
    expected = ref["answer"].get(key)
    if not expected:
        return [f"no HEAD reference for {key!r}"]
    at_end = S1_WINDOW_END.get(key, ())
    reasons = []
    for tag, d_ref in expected.items():
        if tag in at_end:
            continue
        user = _tag(a, tag)
        delay = _answer_delay(user)
        if (
            delay is None
            or abs(delay - d_ref) > HEAD_EQUAL
            or user["final"]["code"] == 200
        ):
            final = None if user is None else user["final"]
            reasons.append(
                f"{tag}: {final} after {_fmt(delay)} s, HEAD's error after "
                f"{d_ref:.2f} s (equal within {HEAD_EQUAL:g} s)"
            )
    for tag in at_end:
        user = _tag(a, tag)
        final = None if user is None else user["final"]
        end = None if user is None else _wake_end(run, a, user)
        when = [] if end is None else [end]
        own = None
        if user is not None and _own_attempt_first(run, a, user):
            handled = _handled(a, user)
            own = None if handled is None else handled + OWN_ATTEMPT_ERROR
            when += [] if own is None else [own]
        if (
            final is None
            or final["code"] == 200
            or not any(abs(final["t"] - t) <= HEAD_EQUAL for t in when)
        ):
            also = "" if own is None else f" or, its own attempt first, at {own:.2f}"
            reasons.append(
                f"{tag}: {final}; must be an error when its wake window ends at "
                f"{_fmt(end)}{also} (within {HEAD_EQUAL:g} s)"
            )
    if a["irs"]:
        reasons.append("an IR send to a silent Broadlink")
    return reasons


def rule_s2(run: dict, a: dict, ref: dict) -> list[str]:  # noqa: ARG001
    """S2: how long a silent Broadlink holds the router; nothing sent to it.

    Awake: p1 answered within 4 s, p2 within 4.5 s of p1's arrival. BL-9: at a wake, p1
    waits for the driver's connect attempt (~6 s against a silent Broadlink) until its
    wake window ends, so it is answered by then; p2, handled after the window, waits
    for that attempt PROBE_LOCK_WAIT more (BL-7's 1 s). The BL-9 build before the fix
    round gave up 3 s after p1 and answered p2 when the window ended.
    """
    first, second = _tag(a, "p1"), _tag(a, "p2")
    end = _wake_end(run, a, first)
    if end is None:
        by1, by2 = first["at"] + 4.0, first["at"] + 4.5
    else:
        by1, by2 = end + S2_SLACK, end + PROBE_LOCK_WAIT + S2_SLACK
    reasons = []
    t1 = None if first["final"] is None else first["final"]["t"]
    t2 = None if second["final"] is None else second["final"]["t"]
    if t1 is None or t1 > by1:
        reasons.append(f"p1 answered at {_fmt(t1)} (by {by1:.2f})")
    if t2 is None or t2 > by2:
        reasons.append(f"p2 answered at {_fmt(t2)} (by {by2:.2f})")
    if a["irs"]:
        reasons.append("an IR send to a silent Broadlink")
    return reasons


def rule_b1(run: dict, a: dict, ref: dict) -> list[str]:  # noqa: ARG001
    """B1: the press fails; the Broadlink comes back without a press; then a press works."""
    first, check = _tag(a, "p1"), _tag(a, "check")
    params = run["params"]
    reasons = []
    if first["final"] is None or first["final"]["code"] == 200 or first["irs"]:
        reasons.append(f"the press during the outage: {_outcome(first)}")
    # Disconnected (the IR emitter's state not ON) after the press: it must come back
    # without a press, i.e. before the check press: the emitter's state ON again, or a
    # device exchange made while no press was handled.
    states = [(t, s) for t, s in a["emitter_states"] if first["at"] <= t < check["at"]]
    went_off = [t for t, s in states if s != "ON"]
    if went_off:
        back_on = [t for t, s in states if s == "ON" and t > went_off[0]]
        alone = [
            r["t"]
            for r in a["connects"]
            if r.get("result") == "ok"
            and r.get("active") == 0
            and params["back"] <= r["t"] < check["at"]
        ]
        recovered = (back_on or alone or [None])[0]
        if recovered is None or recovered - params["back"] > params["bound"]:
            reasons.append(
                f"no reconnect without a press within {params['bound']} s of "
                f"{params['back']} s (first: {_fmt(recovered)})"
            )
    ok = (
        check["final"]
        and check["final"]["code"] == 200
        and len(check["irs"]) == 1
        and check["irs"][0]["t"] - check["at"] <= LATE_AFTER
    )
    if not ok:
        reasons.append(f"the press after the recovery: {_outcome(check)}")
    return reasons


FAMILY_RULES = {"W1": rule_w1, "S1": rule_s1, "S2": rule_s2, "B1": rule_b1}


def judge(run: dict, res: dict, a: dict, ref: dict) -> tuple[str, list[str]]:
    """Return the run's verdict (PASS, FAIL, ERROR, INVALID) and the reasons."""
    double_toggles(run, a)  # BL-9: needs the run's wake windows
    if res.get("error") or not res.get("setup_ok"):
        err = (res.get("error") or "setup did not finish").strip().splitlines()[0]
        reasons = [f"run error: {err}"]
        if a["raised"]:
            reasons.append(
                f"an exception left a command handler: {a['raised'][0]['error']}"
            )
        return "ERROR", reasons
    if a["unmodelled"]:
        return "INVALID", [
            f"network access the harness does not model: {a['unmodelled'][:3]}"
        ]
    reasons = hard_rules(a)
    exp = expectations(run, a, _settings(run))
    for user in a["users"]:
        if user["tag"] != "pre":  # presses made only to set up an outage are not judged
            reasons += check_press(user, exp[user["user"]], a)
    rule = FAMILY_RULES.get(run["id"])
    if rule is not None:
        reasons += rule(run, a, ref)
    if reasons:
        return "FAIL", reasons
    # BL-9: a run whose second drop missed its moment kept every rule but tested nothing
    missed = hit_missed(run, a)
    return ("INVALID" if missed else "PASS"), missed


def _press_text(user: dict, a: dict) -> str:
    dev = user.get("dev", "A")
    label = "" if dev == "A" else f"[{dev}]"  # BL-9: MD, the second Broadlink
    parts = [f"{user['tag']}{label}@{user['at']:.2f}"]
    for rid in user["requests"]:
        req = a["reqs"][rid]
        if req["core408"] is not None:
            late = req.get("late_answer")
            parts.append(
                f"408@{req['core408']:.2f}"
                + (f" (integration {late['code']}@{late['t']:.2f})" if late else "")
            )
        elif req["answer"] is not None:
            parts.append(f"{req['answer']['code']}@{req['answer']['t']:.2f}")
        else:
            parts.append("no answer")
    for ir in user["irs"]:
        flags = [
            name
            for name, on in (
                ("LATE", ir.get("late")),
                ("LATE-AFTER-408", ir.get("after408")),
                ("DOUBLE-TOGGLE", ir.get("burst")),
                ("REPEAT", ir.get("repeat")),  # BL-9: not a DOUBLE-TOGGLE (judge)
            )
            if on
        ]
        flag = f" {' '.join(flags)}" if flags else ""
        parts.append(f"IR {ir['t']:.2f} (+{ir['t'] - user['at']:.2f}{flag})")
    if not user["irs"]:
        parts.append("no IR")
    return " ".join(parts)


def run_line(run: dict, verdict: str, a: dict | None, res: dict | None) -> str:
    """Return the run's one-line report: settings, every press, its IR sends, flags."""
    sets = "set" if run["settings"] else "default"
    head = (
        f"{verdict:<7} {run['id']:<3} {run['variant']} [{sets}: wake "
        f"{_settings(run):g} s]"
    )
    if a is None:
        return f"{head}  not run: this tree's BroadlinkConfig has no BL-9 setting"
    line = f"{head}  " + " | ".join(_press_text(u, a) for u in a["users"])
    if (res or {}).get("lag_max", 0) > 0.05:
        line += (
            f"  [event loop stalled {res['lag_max'] * 1000:.0f} ms at {res['lag_at']}]"
        )
    return line


def load_reference(path: str) -> dict:
    """Return W1 IR times and S1 answer delays from a --json results file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ref: dict = {"ir": {}, "answer": {}}
    for entry in data["runs"]:
        run = entry["run"]
        key = f"{run['id']} {run['variant']}"
        users = (entry.get("result") or {}).get("users") or []
        if run["id"] == "W1":
            ref["ir"][key] = {
                u["tag"]: u["irs"][0]["t"]
                for u in users
                if u["tag"] != "pre" and len(u.get("irs") or []) == 1
            }
        elif run["id"] == "S1":
            ref["answer"][key] = {
                u["tag"]: round(u["final"]["t"] - u["at"], 3)
                for u in users
                if u["final"]
            }
    return ref


def run_child(script: str, src: str, run: dict, timeout: float) -> dict:
    """Run one scenario in a fresh Python process and return its result."""
    cmd = [sys.executable, script, src, "--child", json.dumps(run)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return {
            "error": f"the run did not end within {timeout:.0f} s",
            "setup_ok": False,
        }
    for line in proc.stdout.splitlines():
        if line.startswith(RESULT_MARK):
            return json.loads(line.removeprefix(RESULT_MARK))
    return {
        "error": f"no result (exit {proc.returncode}): {proc.stderr[-1500:]}",
        "setup_ok": False,
    }


def select(args) -> list[dict]:
    """Return the runs the command line asks for, in catalogue order."""
    runs = catalogue()
    if args.quick:
        runs = [run for run in runs if run["quick"]]
    if args.only:
        # ids or id prefixes ("O"), or the start of "ID variant" (an item with a space).
        # Some variants hold commas: the whole argument is tried as one item first.

        def chosen(run: dict, wanted: list[str]) -> bool:
            name = f"{run['id']} {run['variant']}"
            return any(
                name.startswith(w) if " " in w else run["id"].startswith(w)
                for w in wanted
            )

        picked = [run for run in runs if chosen(run, [args.only.strip()])]
        if not picked:
            wanted = [item.strip() for item in args.only.split(",") if item.strip()]
            picked = [run for run in runs if chosen(run, wanted)]
        runs = picked
    return runs


def parent(args) -> int:
    """Run the selected runs in child processes; print, judge and summarize them."""
    require_offline()
    runs = select(args)
    if not runs:
        raise SystemExit("no run selected")
    with_settings = tree_has_settings(args.src)
    ref = (
        load_reference(args.reference)
        if args.reference
        else {"ir": HEAD_IR, "answer": HEAD_ANSWER}
    )
    for run in runs:
        run["settle"] = max(SETTLE, _settings(run) + 2.0)
    todo = [i for i, run in enumerate(runs) if with_settings or not run["settings"]]
    for i, run in enumerate(runs):
        run["port"] = PORT_BASE + i  # one port per run, below the ephemeral range
    started = time.monotonic()
    print(
        f"== BL-9 scenarios on {args.label} ({args.src}): {len(runs)} runs, {len(todo)} "
        f"to run, {args.jobs} at a time; the BL-9 setting in the tree: "
        f"{'yes' if with_settings else 'no (settings runs N/A)'}",
        flush=True,
    )
    script = os.path.abspath(__file__)
    results: dict[int, dict] = {}
    longest_first = sorted(
        todo, key=lambda i: -(runs[i]["duration"] + runs[i]["settle"])
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {}
        for i in longest_first:
            run = runs[i]
            timeout = (
                run["duration"] - min(-6.0, _timeline_start(run)) + run["settle"] + 90
            )
            futures[pool.submit(run_child, script, args.src, run, timeout)] = i
        for fut in concurrent.futures.as_completed(futures):
            results[futures[fut]] = fut.result()

    counts: dict[str, dict[str, int]] = {}
    out = []
    python = None
    for i, run in enumerate(runs):
        if i not in results:
            verdict = "N/A"
            print(run_line(run, verdict, None, None))
            reason = "the tree's BroadlinkConfig has no BL-9 setting"
            out.append({"run": run, "verdict": verdict, "reasons": [reason]})
        else:
            res = results[i]
            python = python or res.get("python")
            a = analyse(res)
            verdict, reasons = judge(run, res, a, ref)
            print(run_line(run, verdict, a, res))
            for reason in reasons:
                print(f"        - {reason}")
            if args.verbose:
                for log in res.get("logs", []):
                    ours = log["name"] in ("rm", "broadlink_driver", "ir_emitter")
                    if ours or log["level"] in ("WARNING", "ERROR"):
                        print(
                            f"          {log['t']:7.3f} {log['level'][:4]} {log['name']}: "
                            f"{log['msg']}"
                        )
            out.append(
                {"run": run, "verdict": verdict, "reasons": reasons, "result": res}
            )
        family = counts.setdefault(run["id"], {})
        family[verdict] = family.get(verdict, 0) + 1
    judged = [e for e in out if e["verdict"] != "N/A"]
    bad = [e for e in judged if e["verdict"] != "PASS"]
    print(f"== Python {python}; wall time {time.monotonic() - started:.0f} s")
    for sid, verdicts in counts.items():
        print(
            f"== {sid}: " + ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items()))
        )
    not_run = len(runs) - len(todo)
    print(
        f"== {args.label}: {len(judged) - len(bad)}/{len(judged)} as expected"
        + (f"; {not_run} N/A (no BL-9 setting in this tree)" if not_run else "")
    )
    for entry in bad:
        print(
            f"==   not as expected: {entry['verdict']} {entry['run']['id']} "
            f"{entry['run']['variant']}"
        )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "label": args.label,
                    "src": args.src,
                    "python": python,
                    "settings_in_tree": with_settings,
                    "runs": out,
                },
                f,
                indent=1,
            )
        print(f"== results written to {args.json}")
    return 1 if bad else 0


def main() -> int:
    """Parse the command line; run the whole test, or one run as a child process."""
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "src",
        nargs="?",
        default="intg-broadlink",
        help="the intg-broadlink folder to test",
    )
    parser.add_argument("label", nargs="?", default=None, help="its name in the report")
    parser.add_argument("--quick", action="store_true", help="a representative subset")
    parser.add_argument(
        "--only",
        default=None,
        help='comma list of ids or id prefixes; an item with a space: "ID variant" start',
    )
    parser.add_argument("--jobs", type=int, default=8, help="parallel runs (default 8)")
    parser.add_argument("--json", default=None, help="write the results to this file")
    parser.add_argument(
        "--reference",
        default=None,
        help="W1 / S1 references from a --json file (default: HEAD's)",
    )
    parser.add_argument("--verbose", action="store_true", help="the driver's log lines")
    parser.add_argument("--list", action="store_true", help="list the runs and exit")
    parser.add_argument("--child", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        out = child(args.src, json.loads(args.child))
        sys.stdout.write(RESULT_MARK + json.dumps(out) + "\n")
        sys.stdout.flush()
        os._exit(0)
    args.label = args.label or args.src
    if args.list:
        for run in select(args):
            print(
                f"{run['id']:<3} {run['variant']} [kind {run['kind']}, wake "
                f"{_settings(run):g} s{', set' if run['settings'] else ''}"
                f"{', quick' if run['quick'] else ''}] down={run['down']} "
                f"silent={run['silent']}"
            )
        return 0
    return parent(args)


if __name__ == "__main__":
    sys.exit(main())
