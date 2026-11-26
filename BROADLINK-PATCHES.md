# Broadlink Integration Patches — Implementation Notes (madalone fork)

Fork of [JackJPowell/uc-intg-broadlink](https://github.com/JackJPowell/uc-intg-broadlink), based on tag `v1.3.0` (`06d8e6d`), branch `v1.3.0-patched`. This file is the source of truth for every divergence from upstream.

**Why the fork exists:** on 2026-10-17..20 the Remote's Broadlink connection failed after **28 of 128 wakes (22%)**, and **22 of 70 IR presses (31%)** were dropped. Every dropped press was answered "OK", so the remote showed no error. A second failure mode (lost entity subscription after a driver restart) was reported on the UC Discord with a log from 2026-09-28.

---

## Root causes (all confirmed in code and logs)

1. **One connect attempt per wake, no retry.** ucapi-framework 1.9.6 `on_r2_exit_standby` starts a single background `device.connect()`. The Remote sends `exit_standby` before its Wi-Fi is usable, so that attempt fails with `[Errno 101] Network is unreachable`. The only retry path in the framework is the watchdog. `rm.py` disables it (`enable_watchdog=False`), and even when enabled it only starts after a *successful* connect.
2. **No reconnect when a command arrives.** `send_command` deliberately refused to connect (`# don't force connection here`).
3. **Failures reported as success.** The IR emitter's and remote's `command_handler`, and the media player's source select, returned `OK` whatever happened.
4. **Blocking calls on the event loop.** python-broadlink is synchronous. `hello`, `xdiscover`, `auth` and `send_data` ran directly in async code. The default device timeout is 10 s, resent every 1 s, so an unresponsive device froze the whole driver.
5. **The library's device lock never engages.** python-broadlink 0.19.0 `send_packet` uses `with self.lock and socket.socket(...)`. That expression evaluates to the socket, so the lock is never acquired.
6. **No lock around connect.** ucapi-framework's `ExternalClientDevice.connect()` is check-then-act around awaits (1.9.6 and 1.9.8), so concurrent callers could run `create_client` / `connect_client` together.
7. **Lost subscription (Discord reporter).** The driver started before Wi-Fi was up. With `require_connection_before_registry=True` no entities existed when the Remote subscribed. `ucapi` logged `cannot subscribe entity … entity is not available`, answered 200, and never replayed the request. Every IR command then returned 404 `no configured entity found` until the Remote was restarted (16 h in that log). Fixed upstream in **ucapi-framework 1.9.8** (`cabdb7e`, "Improve subscription behavior"). That fix only completes once the device eventually connects, which root cause 1 prevented.

---

## BL-1: Reconnect loop after wake, connect command and startup

**Files:** new `intg-broadlink/broadlink_driver.py` (`BroadlinkDriver`), `intg-broadlink/driver.py`.

`BroadlinkDriver(BaseIntegrationDriver)` overrides the framework's documented hooks:
- `on_r2_exit_standby`
- `on_r2_connect_cmd` (still sets the integration state to CONNECTED)
- `on_r2_enter_standby` (cancels retries first, then `super()`)

`connect_all_with_retry()` runs one task per device: 10 attempts 0.5 s apart, then 5 → 10 → 30 → 60 s, for as long as the Remote is awake. It stops on success, on standby, or when the device is removed. `driver.py` instantiates it and calls `connect_all_with_retry()` after `register_all_device_instances()`, so a device that failed its first connect at driver start keeps trying. A successful connect makes the device emit CONNECTED, and framework 1.9.8's `on_device_connected` then registers the entities and completes any pending subscription.

**Why driver-level:** the framework's subscribe path (`_ensure_device_connected`) calls `device.disconnect()` between its own attempts. A retry loop living inside the device would be cancelled by that.

## BL-2: Connection robustness in `rm.py`

- **`connect()` override** with an `asyncio.Lock`. Callers wait for the attempt in progress instead of running a second one alongside it. This is the same race class as the Kodi fork's patch 48.
- **Reconnect on demand.** `send_command` on a disconnected device calls `_reconnect_for_command()`. It first waits for an attempt that is already running, then keeps trying every 0.5 s for up to 3 s (`COMMAND_RECONNECT_WINDOW`; BL-9: replaced by the wake window, and outside it one attempt). The button press that wakes the Remote arrives before Wi-Fi is back, so a single attempt would fail instantly.
- **Off-loop I/O.** Every python-broadlink call goes through `_run_io()`: `asyncio.to_thread` under a per-device lock (one call at a time, because the library's own lock is ineffective). Covered calls: `hello`, broadcast discovery (consumed inside the thread), `auth`, `send_data`, the learning calls and the RF sweep calls.
- **Timeouts.** `device.timeout = 4` (was the library default of 10). `DISCOVERY_TIMEOUT = 3` for the directed hello and the broadcast (was 5 + 5). An unreachable device now costs ~6 s per connect attempt instead of ~10 s, and a LAN device answers in milliseconds.

## BL-3: ucapi-framework 1.9.6 → 1.9.8

`requirements.txt` and `pyproject.toml`. 1.9.8 records subscription requests and completes them when the entity appears (root cause 7). Its other changes since 1.9.6 touch the WebSocket device class, which this integration doesn't use. Dependencies are unchanged (`pyee>=9`, `ucapi>=0.7.0`, `aiohttp>=3.9`).

## BL-4: Report failures honestly

- `ir_emitter.py` and `remote.py`: `command_handler` returns the device's result instead of always `OK`.
- Remote `SEND_CMD` returns the first failing `send_command` status. `LEARN:*` and `REMOVE`/`DELETE` run once and return their own status.
- `media_player.py`: source select returns the send result.
- `rm.py`: when the reconnect after a failed send also fails, the result is `SERVICE_UNAVAILABLE`. It used to fall through to `BAD_REQUEST`.

**Behaviour change:** a genuine failure now reaches the Remote as an error instead of a silent no-op. Observed on the device on 2026-10-21: an activity started while the Broadlink was unplugged still ran its other steps. A failed IR step doesn't abort the activity.

## BL-5: `repeat` parameter

`get_int_param` / `_get_int_param` used to parse only strings, so an integer `repeat` was silently ignored. They now accept integers too. Repeat was also applied twice: `command_handler` looped `repeat` times around `handle_command`, which loops again for sequences and remote `SEND_CMD`. Now it is applied exactly once per command path. `send_ir` and raw commands loop in `handle_command` via `_send_repeated()`, stopping at the first failure. Not exercised by this household's setup, which never sends `repeat`.

## BL-6: CI

`.github/workflows/build.yml`:
- The Docker/GHCR job is removed; it would publish a multi-arch image under the repository owner.
- The release job needs only `build`.
- The tag must match `driver.json` `version`, or the build fails. Without this check a stale `driver.json` would publish an asset named after the wrong version. Upstream's version line strips a `v` from `GITHUB_REF`, so a manual (dispatch) run fails the check instead of publishing a mislabelled release.
- Upstream's builder image `r2-pyinstaller:3.11.12-0.3.0` is kept.
- ARM emulation is set up with `docker/setup-qemu-action@v3` (arm64) instead of `apt-get install qemu-system-arm binfmt-support qemu-user-static` plus the unmaintained `multiarch/qemu-user-static` image (last updated 2023). The first release build on 2026-10-22 hung for 12 minutes in `apt-get update` on an unreachable Ubuntu mirror (`azure.archive.ubuntu.com`) before anything was built, and was cancelled. The action needs no package mirror.

## BL-7: Fail fast when the Broadlink stops answering

**Files:** `intg-broadlink/rm.py`, `intg-broadlink/broadlink_driver.py`.

**Problem.** Measured on the device with madalone.1 on 2026-10-21, with the Broadlink unplugged while the Remote was awake:
- The first press got its error after **10.07 s**: the 4 s send timeout, then a full reconnect (3 s directed hello plus 3 s broadcast discovery). Every later press took 6.0 s.
- **ucapi 0.7.0 handles a connection's requests one at a time.** `_ws_router` awaits each message before reading the next. So quick repeated presses queued up: in the harness, five presses 0.3 s apart were answered at 10, 16, 22, 28 and 34 s.
- If the Broadlink came back while presses were queued, they were all sent at once, up to ~9 s late. A late burst of power toggles can leave a TV off.
- Nothing reconnected in the background. A Broadlink lost while the Remote was awake stayed disconnected until the next press or wake.

**Fix.** The normal send path is unchanged from madalone.1, including the 4 s send timeout. BL-7 changes only what happens after a failure.
- **"Didn't answer" record** (`_unanswered_at`). The device records when it last failed to answer while the network was usable: the hello and the broadcast went out and nothing came back, or auth timed out.
  - `Errno 101` never counts. It means the Remote's own Wi-Fi is down, as right after a wake.
  - A successful connect clears the record, and so do standby and wake.
- **Quick reconnect after an unanswered send** (`_reconnect_after_failed_send`): one 1 s directed hello (`QUICK_PROBE_TIMEOUT`), no discovery. A healthy device answers within milliseconds and the command is resent. A silent one fails within a second.
- **Presses while the device isn't answering** (`_probe_for_command`): a press on a disconnected device makes at most one quick probe.
  - No probe at all within 2 s of the last unanswered attempt (`PROBE_HOLDOFF`). Those are the presses that queued behind it, and they fail at once instead of being sent late.
  - It waits at most 1 s for an attempt already running (`PROBE_LOCK_WAIT`).
- **BL-2's reconnect window is now a real bound.** The patient reconnect, for the press that wakes the Remote, waits for an attempt already in progress only within its own 3 s window (BL-9: until the wake window ends). Before, it waited for the whole attempt: ~6 s against a silent device. Once an attempt has found the device silent, the press switches to the quick probe.
- **Background reconnect** (`BroadlinkDriver.reconnect_in_background`). When a command gives up on an unreachable device while the Remote is awake, and no retry is running, a background retry starts: 5 → 10 → 30 → 60 s, cancelled on standby.
  - It does full connects, with discovery, so a Broadlink that came back on a new IP address is still found.
  - The fast 0.5 s retries stay reserved for wakes, the Remote's connect command and driver start.
  - The device asks for the retry itself. It is deliberately not hooked to the framework's connection-error event: with `require_connection_before_registry=True`, the first connect at driver start runs inside `register_all_device_instances()`, before `connect_all_with_retry()`. Its error would start the slow retry, and the fast one would then be skipped. The review check R1 measured exactly that: Wi-Fi back at 1 s, connected 4.03 s later instead of 0.05 s.

**Not changed: the send timeout.** A draft lowered it to 2 s. It went back to 4 s because an unanswered send is resent after a reconnect. If a Broadlink only answers once a long code (an RF code with repeats, say) has gone out, a timeout shorter than that code would send it twice. Nothing showed when the device answers, and Home Assistant uses 5 s.

New log lines:
- `Device did not answer recently, probing once before sending` (the quick probe)
- `Reconnected after N background attempts`

**Trade-off.** A press within 2 s of a failed attempt fails without trying. If the Broadlink came back in exactly that window, the next press works. During an outage the error comes back without a retry that would have sent the code late.

## BL-8: The press that wakes the Remote, with a code the Remote converts

**Files:** `intg-broadlink/ir_emitter.py`, `intg-broadlink/rm.py`, `intg-broadlink/driver.py`; harness `tests/bl8_harness.py`.

**Problem.** Seen on the owner's Remote (fw 2.12.1, madalone.2) on 2026-11-05 and 2026-11-06: the POWER press that woke the Remote showed a red "Error sending the command … Error code: 400" pop-up, and the TV didn't toggle.
- The IR emitter's `send_ir` asks the Remote's own REST API (`GET /api/ir/convert/PRONTO|HEX`, through unfurled) to convert PRONTO and HEX codes, when the integration has a Remote API key. Upstream v1.3.0 added this path. It converted first, before any connection check.
- Right after a wake the Remote's Wi-Fi isn't back yet (root cause 1), so the request failed at once with `[Errno 101] Network is unreachable`, wrapped in unfurled's `ConnectionError`. The driver log of both incidents shows the command arriving ~58 ms after `exit_standby`, and the Broadlink authenticated within ~20 ms of the failure.
- `command_handler` turned every exception into `400 BAD_REQUEST`, and the press was lost. The Remote's UI resends a failed command after a wake, 0.5 s after each failure while the press is within "Retry commands after wakeup" (2 s by default). It never resends 400, 401, 403, 422 or 501 (`commandRetryPolicy.cpp`, UI 0.84.0). Stock UI 0.84.0 resends only after a wake from SUSPEND; the owner's custom UI (v1.4.19 and later) also after LOW_POWER, the Remote 3's usual standby. Commands from an activity sequence are not resent at all. BL-2's reconnect never ran, because the failure came before `send_command`.
- Every other conversion failure was a 400 as well (a server error, a rejected API key). A conversion request that got no answer had no time limit at all (see below).

**Fix.**
- **Wait for the connection before converting.** On a conversion by the Remote that isn't remembered (below), the press first calls `Broadlink.ensure_connected_for_command()`. That is the start of `send_command`, moved into its own method with no change: BL-2's reconnect, or BL-7's quick probe. A connected Broadlink shows that the Remote's network is back. This is what saves the wake press, and the only thing that can save a command from an activity sequence.
- **Retry a conversion only while the network is down.** Retried 0.5 s apart (`CONVERT_RETRY_INTERVAL`) while the press is inside its 3 s window (`COMMAND_RECONNECT_WINDOW`; BL-9: inside the wake window), for:
  - `ENETUNREACH`, `ENETDOWN`, `EHOSTUNREACH` or `EADDRNOTAVAIL` anywhere in the exception chain unfurled raises from aiohttp (with an IP address, the case seen on the Remote);
  - a failed name lookup with `EAI_AGAIN`, for a host-name address (`Remote3-….local`). That is what the glibc resolver in the test container gives without a network. The Remote's own resolver, mDNS for `.local`, was not tested;
  - the Remote's API answering 503.

  That covers a Broadlink still marked connected from before (no standby seen) while the network is down. A refused connection, a name that doesn't exist, a server error or a time-out is answered at once.
- **A time limit on every conversion attempt.** The conversion request (`CoreAPI.request` in unfurled 0.4.1) is sent with `timeout=None`, which aiohttp 3.14 takes as "no limit": an explicit `None` becomes `ClientTimeout(total=None)`. So the session's 5 s default never applies to it. A conversion that hung (a slow core, a stale address that drops packets) held the press, and so every later press behind it, until the server or the kernel gave up; madalone.2 was still waiting after 15 s in the harness. Each attempt now gets what is left of the press's window, at least 1 s (`CONVERT_ATTEMPT_MIN_TIMEOUT`).
- **Honest status codes** (`_conversion_status`):

  | Conversion failure | Answer before | Answer now |
  |---|---|---|
  | network unreachable or down, name lookup failed, connection refused (`unfurled.ConnectionError`), a time-out | 400 | 503 |
  | the Remote answered 503 | 400 | 503 |
  | the Remote answered another 5xx | 400 | 500 |
  | API key rejected (`unfurled.AuthenticationError`) | 400 | 401 |
  | the Remote answered 4xx, invalid timings, an invalid code | 400 | 400 |
  | a malformed Remote address, a TLS certificate failure (a `ValueError` in the chain) | 400 | 400 |

  The exception chain follows `raise … from …` and implicit contexts, but not a context suppressed with `from None`. Nothing raises out of `command_handler`. A failed conversion is logged as `Cannot convert the IR code, answering <status>: <exception>`, and nothing is sent.
- **Remembered conversions.** The converted Broadlink data is kept per (format, code) in memory, at most 64 codes (`CONVERTED_CODE_CACHE_SIZE`), least recently used dropped first, and cleared when the driver restarts. A repeated press needs no conversion request, so it works even if the Remote's API is unreachable.
- **No Wake-on-LAN for a conversion.** `Remote(..., wake_if_asleep=False)`, one `Remote` (and aiohttp session) per press, closed afterwards. unfurled 0.4.1 wakes only once a `Remote` has loaded its settings, which the emitter never does, so this changes nothing today. It keeps it that way if a later unfurled wakes without them.
- **The IR emitter's log lines reach the Remote's log.** `driver.py` raises only named loggers to `UC_LOG_LEVEL`, and the root logger stays at WARNING. `ir_emitter` wasn't one of them, so its INFO lines never appeared, including upstream's `Got … command request`. It is now raised like the others.

**Log lines:**
- `IR code conversion failed (…), retrying` (INFO): a conversion retried while the network came back.
- `Cannot convert the IR code, answering <status>: …` (WARNING): a conversion that failed.
- The most common failure doesn't come from the emitter: when the Broadlink can't be reached within the window, `rm`'s `Cannot send command: device not connected` is logged, and the press is answered 503 without a conversion.

**Not changed:**
- codes converted locally (no Remote API key);
- `send_cmd_sequence`;
- raw commands;
- `send_command`'s reconnect.

unfurled stays at 0.4.1. The 0.4.1 and 0.6.3 wheels from PyPI (compared 2026-11-11) have identical `IR.convert`, `CoreAPI.request`, `_raise_on_error`, `Remote._ensure_awake` and exception classes; only other features changed. Upstream pins 0.4.1 too.

**What the 503 buys, and the limits.**
- The wait for the connection and the conversion retry make the wake press work. The 503 is a better answer, not a rescue. The UI resends only within its window (above), and a 503 after BL-2's 2.5 s usually comes too late, so the Remote shows its "not responding" pop-up (or the error banner, with "Show error pop-ups" off). Only a fast failure, a 500 for example, can still be resent; the API's own 503 is retried until the window ends.
- **The window bounds when an attempt starts, not when it ends.**
  - BL-2's last reconnect attempt starts 2.5 s into the press and runs to its end: about 6 s against a Broadlink that doesn't answer (3 s hello + 3 s discovery).
  - A conversion still gets at least 1 s after a late connection (W19: 3.51 s; BL-9: 4.0 s, the window's end).
  - Worst case: a Broadlink that doesn't answer and a network that comes back during the window holds the press about 8.5 s.
  - Every command has worked this way since BL-2; converted codes, which used to fail at once, now share it. (BL-9: a press's own attempts are hellos without discovery, and the last one starts when its window ends.)
- **A long Wi-Fi outage queues presses.** ucapi handles one request at a time, and each converted press now waits up to ~2.5 s for the network, as raw and locally converted codes already did (BL-2). When the network returns, the presses still queued are sent together, several seconds late. One that waited past the core's 10 s answers 408 there and is still sent afterwards. Measured end to end through core-simulator 0.83.2: five presses 0.3 s apart with the network back at 8 s gave three 503s and two sends at 8.04 s. madalone.2 gave five 400s and no send. A fix belongs in `rm.py` for every command, not here. (Fixed by BL-9.)
- **A remembered code doesn't wait for the network.** (BL-9: inside the wake window it does, like any press; outside it, it fails at once.) W3 (a Broadlink still marked connected) covers codes not yet remembered. A remembered code goes straight to `send_command`. There the send fails with `Errno 101` and BL-7's quick probe fails too, so the press is answered 503 at once, exactly as a raw code is. With the default "Retry commands after wakeup" the UI's resend 0.5 s later succeeds.

### Verification (`tests/bl8_harness.py`, in the repo)

Runs the real `BroadlinkIREmitter.command_handler` and the real `rm.Broadlink`.
- **python-broadlink is faked.** While the network is down every call fails at once with `Errno 101`; a silent Broadlink blocks until the timeout.
- **unfurled's `Remote` is faked; successful conversions are fake.** Some failures come from the real unfurled 0.4.1 and aiohttp over real sockets:
  - the Remote's address in a container without a network gives `ConnectionError <- ClientConnectorError <- OSError [Errno 101]`, checked at the start of every run;
  - a host name without a network gives `EAI_AGAIN` from the container's resolver;
  - a closed loopback port (refused);
  - a port out of range (aiohttp's `InvalidUrlClientError`);
  - a loopback server that never answers.
- **Raised directly:** the Remote's HTTP answers (500 / 503 / 400 / 401) as unfurled's own exception classes, and a malformed address from unfurled's real constructor. "Invalid timings" is fake data.
- **W20 runs the real `Remote`** (session, retries, cancellation, `close()`) against loopback HTTP servers.
- **Presses are awaited one after the other,** as ucapi handles them. A press still busy after 15 s is reported as "no answer".

```bash
docker run --rm --network none -v "$(pwd -W)":/w -w /w <python:3.11 image with requirements.txt> python tests/bl8_harness.py intg-broadlink BL-8
```

The "before" column is the same script on `git archive v1.3.0-madalone.2 intg-broadlink`.

| Scenario | madalone.2 | BL-8 |
|---|---|---|
| W1 the press that wakes the Remote, network back at 0.05 / 0.3 / 0.6 / 1 / 2 / 2.4 s | **400 after 0.01 s, nothing sent** (all 6) | 200 after 0.51 / 0.51 / 1.01 / 1.01 / 2.01 / 2.51 s, sent once, one conversion |
| W2 the same, network back at 2.8 / 3.5 s / never | 400 after 0.01 s | 503 after 2.51 s, nothing sent, no conversion, one background reconnect request (BL-7) |
| W3 Broadlink still marked connected, a code not yet remembered, network back at 0.3 / 1 / 2 s; 5 s | 400 after 0.01 s | 200 after 0.51 / 1.02 / 2.03 s; 503 after 2.54 s |
| W12 the press 50 ms after `exit_standby`, the driver's wake retry running, network back at 0.3 / 1 / 2 s | 400 after 0.01 s | 200 after 0.51 / 1.01 / 2.01 s, sent once, one conversion |
| W16 host-name address, network back at 1 s / never | 400 after 0.01 s | 200 after 1.01 s / 503 after 2.51 s, no conversion |
| W17 host-name address, Broadlink still marked connected, network back at 1 s | 400 after 0.01 s | 200 after 1.02 s (3 conversion attempts) |
| W18 the network-down error is one the retry doesn't recognise, network back at 1 s | 400 after 0.01 s | 200 after 1.01 s, one conversion (only the wait for the connection saves it) |
| W19 the Broadlink connects at 2.5 s, the conversion API never answers | 400 after 0.01 s | 503 after 3.51 s |
| W14 two presses of the same code in a row on a wake, network back at 1 s | 400, 400 | 200 after 1.01 s, then 200 at once; one conversion |
| W4 network fine, Broadlink silent | 503 after 6.01 s, one conversion | 503 after 6.01 s, no conversion |
| W5 the Remote answers 500 / 503 / 400; API key rejected; invalid timings; invalid port; connection refused; server never answers | 400 for all, and **no answer after 15 s** for the last | 500 / 503 (after 2.53 s, retried) / 400 / 401 / 400 / 400 / 503 at once / 503 after 3.00 s; nothing sent, one attempt except the API's 503 |
| W13 the Remote's API answers 503 once, then converts | 400 | 200 after 0.51 s |
| W15 malformed Remote address in the configuration | 400 | 400 |
| W20 real unfurled `Remote`: the API answers 503, 503, then converts / never answers | 400 / **no answer after 15 s** | 200 after 1.01 s, 3 requests / 503 after 3.00 s; no unclosed session |
| W6 second press of the same code, conversion API unreachable | 400 | 200, no conversion |
| W7 70 different codes, code 0 used again after the first 64 | nothing remembered | 64 remembered, code 0 kept, code 1 dropped |
| W8 `repeat` 3 | 3 sent, one conversion | the same |
| W9 no Remote API key (local conversion) | 200 | the same |
| W10 `wake_if_asleep` passed | `True` (unfurled's default) | `False` |
| W11 `send_command` reconnect, network back at 0.6 s / never | 200 after 1.00 s / 503 after 2.51 s | the same |

madalone.2: 8 of 42 as expected; BL-8: 42 of 42.

**Mutation check.** Eight broken copies of BL-8 were run through the harness. It catches seven:
- skip the wait for the connection: 16 scenarios fail;
- no time limit;
- retry every connection error;
- drop the `EAI_AGAIN` rule;
- never close the `Remote`;
- answer network errors with 400;
- cache under the wrong key.

The eighth survives: walking a context suppressed with `from None`. It only matters with aiohttp's pure-Python HTTP parser; the release bundle ships the compiled one.

**Reviews.** Two independent reviews, the second a multi-agent review with adversarial checks against the real libraries and core-simulator 0.83.2. They found:
- the missing time limit;
- the retry of every connection error;
- the overstated value of the 503;
- the invisible log lines;
- the suppressed-context walk;
- gaps in this harness, including a "time-out" fake the real library never applies and no check that the conversion waits for the connection.

All fixed above, or listed as limits.

**Not modelled:**
- a name that doesn't exist while the network is up (without a network every lookup in the container fails with `EAI_AGAIN`);
- the Remote's own `.local` resolver.

The S1–S9, U1–U11 and R1–R2 scripts of BL-1..BL-7 lived outside the repo and are gone. BL-8 changes no driver logic: `driver.py` only gets a log level. `rm.py` only moves the start of `send_command` into `ensure_connected_for_command`. W11, W12 and W14 cover its BL-2 branch and W2 its background-reconnect request; BL-7's quick-probe branch (a press right after an unanswered attempt) isn't re-run.

### On the device (madalone.3)

**Installs.** The first build went on 2026-11-11 at 20:22 UTC, the rebuild with the second review's fixes on 2026-11-12 at 19:13 UTC; both were the owner's manual uploads. After an upload, `GET /api/intg/drivers` (the list) kept showing the old version, while `GET /api/intg/drivers/broadlink_driver` and the driver's `get_driver_version` showed the new one.

**Use, 2026-11-11 to 2026-11-14:**
- 239 `send_ir` requests, all answered 200. There was no `Cannot convert`, retry or `Cannot send command` line.
- Four presses arrived within 0.36 s of `exit_standby`, but the Remote's Wi-Fi was already back for each.
- POWER alternates two PRONTO codes (a toggle bit), so each one is converted once.

**Test after a driver restart** (the remembered conversions cleared by a Remote restart, 2026-11-12):
- `enter_standby` at 20:22:45 UTC, `exit_standby` at 20:30:58; the Broadlink was connected 0.26 s later.
- POWER arrived 10.6 s after the wake (the motion sensor had woken the Remote first). It was converted by the Remote and sent: 200 after 0.16 s.
- The second POWER code was handled the same way, 0.09 s.
- The next press used the remembered conversion.

**HEX codes.** On 2026-11-14 the Netflix button's HEX codes were converted for the first time: 200 after 0.25 and 0.22 s.

**Not seen yet: a press that itself wakes the Remote before its Wi-Fi is back**, the case of the two original failures.
- With the motion sensor at its highest sensitivity, picking the Remote up wakes it before a button is pressed.
- Two wakes on 2026-11-14 found Wi-Fi still down.
- At 17:42:03 UTC the Broadlink connected only 3.08 s after `exit_standby`, after four `Errno 101`. That is longer than BL-2's last attempt at 2.5 s. A press that woke the Remote then would be answered 503 at 2.5 s, after the UI's default 2 s resend window. Raising "Retry commands after wakeup" (0–10 s, default 2) to 5 s lets the UI resend it.

**Clearing the remembered conversions.** `PUT /api/intg/drivers/broadlink_driver?cmd=STOP` and `?cmd=START` only restart the Remote's connection to the driver, not the driver process (no new `Broadlink IR Emitter init`), so the remembered conversions survive. A Remote restart, or a new upload, clears them.

## BL-9: Presses wait for the Wi-Fi only right after a wake, and are never sent late

**Files:** `intg-broadlink/rm.py`, `intg-broadlink/broadlink_driver.py`, `intg-broadlink/ir_emitter.py`, `intg-broadlink/config_manager.py`, `intg-broadlink/log_redact.py` (new), `intg-broadlink/setup.py`, `intg-broadlink/driver.py` and `intg-broadlink/discover.py` (upstream files); tests `tests/bl9_scenarios.py`, `tests/bl9_window_checks.py`, `tests/bl9_setup_test.py` and `tests/bl9_setup_e2e.py` (new), `tests/bl8_harness.py` (expectations).

**Problem.**
- **Presses queued during an outage were sent late.** ucapi 0.7.0 handles one request at a time. When the Remote's own Wi-Fi was down, every press that found the Broadlink disconnected waited its own ~2.5 s (BL-2), and the presses behind it queued up. When the Wi-Fi came back, the ones still queued were sent together, late. Through core-simulator 0.83.2 (BL-8's review): five POWER presses 0.3 s apart with the network back at 8 s gave three errors and two toggles about 7 s late. With the network back at 11.6 s, a press got the core's 408 at 10 s and was still sent afterwards. Raw and learned codes had behaved this way since BL-2; BL-8 extended it to converted codes.
- **A press could hold the driver about 8.5 s** (a Broadlink that doesn't answer, and the network back late in the window; see BL-8's limits).
- **A slow wake failed.** On 2026-11-14 at 17:42:03 UTC the Remote's Wi-Fi took 3.08 s to come back after a wake, past BL-2's last attempt at 2.5 s.
- **UC's guidance:** the IR emitter spec says "Queuing IR send requests should be avoided to prevent 'ghost actions'… New IR commands should be executed within 1 second" (core-api `entity_ir_emitter.md`). UC's Denon integration (PR #249, 2026-11-11) waits for the connection only in a window after a wake, then fails fast. UC's Android TV integration and Home Assistant's Broadlink integration fail fast.

**Fix.**
- **The wake window** (`Broadlink.command_deadline()`). A press may wait for the Remote's Wi-Fi only inside the wake window.
  - It starts at the first wake event of a wake (`exit_standby` or the core's connect) and when a device is created (driver start, Add, Update, Restore), ends at `enter_standby`, and is counted from that moment, never from when a press is handled.
  - Every press handled inside it shares its end, so a press that queued doesn't get a fresh wait.
  - Its length is the "Wait for Wi-Fi after a wake" setting: 4 s by default, 0–6 s.
- **Outside the wake window a press makes one attempt** and then fails with 503 at once.
  - The attempt is a directed hello of 1.4 s (`PRESS_HELLO_TIMEOUT`), long enough to hear python-broadlink's resend after 1 s: 7 of 39 first hellos after a wake on 2026-11-11..14 were answered after 0.94–1.05 s.
  - A press made while the Remote is awake and its Wi-Fi is down gets its error at once and is never sent later, as UC's IR emitter spec advises.
  - ucapi doesn't say when a request was made. So a press that queued during a slow wake and is handled after the window also fails at once.
- **The wake window starts as soon as ucapi handles the event.**
  - The driver adds plain listeners for `ENTER_STANDBY`, `EXIT_STANDBY` and `CONNECT` with ucapi's public `add_listener`. ucapi runs those inline, before it reads the next request; the async `on_r2_*` handlers run later as tasks, and the press that woke the Remote is often queued right behind `exit_standby`.
  - BL-7's "didn't answer" record is cleared in that listener now, instead of in the async handler.
  - Because the listeners run only between requests, the window can't move while a press is handled.
- **One window per wake** (`broadlink_driver.py` `_first_wake_event`).
  - The first wake event after `enter_standby` (or after the driver starts) starts every device's window: `exit_standby`, which also forgets BL-7's record, or the core's connect.
  - A later one before the next `enter_standby` changes nothing (INFO `exit_standby: the wake window has already started for this wake`). An event can be queued behind a press that is waiting in the window, and started again it would give the presses queued behind that press a second wait.
  - The `ENTER_STANDBY` listener clears the flag inline, so an `exit_standby` right behind `enter_standby` always starts the window. The core sends `enter_standby` before every wake (enter and exit strictly alternated in a week of the Kodi driver's logs).
  - A wake event more than 60 s after the window's start (`WAKE_EVENT_LAPSE`) counts as a new wake even without an `enter_standby`: a driver started or reconnected while the Remote slept never saw that `enter_standby`. 60 s is longer than any press can hold the router (about 25 s at the 6 s setting with every timeout hit), so an event queued behind a press is still caught.
  - The 60 s include the time the Remote slept: they are counted on Linux's `CLOCK_BOOTTIME`, which keeps running while the Remote is suspended in standby; the event loop's clock stops then (fix round 6). Where that clock is missing, as on a development PC, the monotonic clock.
  - So the core's connect while the Remote is awake starts a window only more than 60 s after the last one; until round 4 every connect did.
- **Inside the window** a press reconnects until the window ends (`_reconnect_for_command(deadline)`):
  - **its own attempts** are directed hellos without discovery (the driver's retry has discovery). Each listens for what is left of the window, at least 1.4 s, so a lost reply doesn't make the Broadlink look silent, and at most 3 s. Without discovery, no attempt holds the driver the way the old 3 s hello plus 3 s of discovery did (the 8.5 s above);
  - **an attempt already running** (the driver's wake retry) is waited for until the window ends. Right after a wake the driver's retry runs in step with the press and often connects just after the press would otherwise have given up: the 3.1 s wake failed 6 times out of 6 in the scenario model with the device's latencies when the press stopped waiting earlier.
    - This moves one of BL-7's timings on purpose. With the Broadlink unplugged at a wake, the wake press now gets its error by the wake window's end, instead of after 3.06 s.
    - That is about 3.5 s when the press is handled in the same instant as `exit_standby` (its own 3 s hello goes unanswered, and half a second later BL-7's hold-off ends the press), and 4 s by default when the driver's attempt is already running;
  - **the last attempt** starts when the window ends (`RETRY_MIN_GAP`), so the window is as long as its setting. Its hello listens up to 1.4 s, so a press can still connect and be sent up to about 1.5 s after the window ends (when the Broadlink's first reply is lost), or get its error then;
  - **BL-7's handling** of a Broadlink that doesn't answer is otherwise unchanged.
- **A send that fails because the network is down on its first packet** waits like a press in the window.
  - python-broadlink resends only after a 1 s receive timeout, so a failure within that second means nothing went out, and the resend can't send the code twice.
  - The send's start is noted in the worker thread, so a wait for the device lock doesn't count.
  - Outside the window, or after that first second, a failed send gets BL-7's quick probe, as before, and is never resent late.
  - It is resent only if the reconnect worked. An attempt whose hello is answered but whose auth fails leaves a client that never authenticated (ucapi-framework keeps it), and the press used to be resent through it: 500 instead of BL-4's 503, no background reconnect, and with the network up a packet that wasn't authenticated. That defect is older than BL-9; the wait in the window made it easy to reach. Now such a press gets BL-4's 503 and BL-7's background reconnect.
- **The IR emitter's conversion by the Remote (BL-8)** retries within the same window, with its last attempt at the window's end. Outside the window it gets one attempt.
- **The setting** is on the integration's setup page "Remote API access": "Wait for Wi-Fi after a wake", a list of whole seconds from "0 seconds (no wait)" to "6 seconds", with "4 seconds (default)", stored as `wake_wait_sec`.
  - **Where it is.** Web configurator: Integrations → Broadlink → Start setup → Start integration setup → Action "Update information for selected device" → the Broadlink. The integration's page has no Configure button; this document and the CHANGELOG call that wizard "Configure", and its actions Update, Backup, Restore, Remove and Reset.
  - **Why a list (fix round 7).** The first test build offered a number field with `"steps": 0.5`. The Remote's core reads `steps` as a whole number only (`Option<i32>` in UC's `api-model-rs`; the published API description says "number") and refused the whole page: ``Err(Error("invalid type: floating point `0.5`, expected i32", …))`` in the core's log. The web configurator then showed "Setting up…" until it was closed, and a first-time setup would have failed the same way (device test of 2026-11-19).
  - A list is also what both user interfaces show well: the web configurator draws a number field as a bare box without unit or range, and the Remote's own screen as free text.
  - The page's selected item is the saved value read through `_wait_seconds` (clamped to 0–6, rounded to whole seconds, a half up), so it is always one of the items. A saved value between whole seconds (a hand-edited or restored configuration) stays in use as saved until the page is saved, which stores the item shown.
  - The Remote's core refuses an answer whose wait is no item of the list, that lacks a field or whose value isn't text (HTTP 400), so the driver's lenient reading of other input is reached only by its tests.
  - A configuration saved before BL-9 loads with the default, and so does a value that isn't a finite number: text, infinity, or too large for a float (a hand-edited `config.json`). An older driver ignores the field, because ucapi-framework reads only its dataclass's fields, so going back to madalone.3 works.
  - An Update re-creates the device, and so does a Restore now (below), so a change applies at once, and the driver logs the value.
  - At most 6 s: the core answers 408 after 10 s without telling the driver, and a press still needs time to connect (up to about 1.5 s after the window), convert and send after the wait.
- **The Broadlink is listed once on "Discovered Devices"** (`discover.py`, an upstream bug). `discover()` added to a list it never cleared, and the driver keeps one discovery object for as long as it runs, so each search listed the Broadlink once more. The setup picked the oldest entry, with the address the Broadlink had then. `discover()` now empties the list first.
- **Update keeps what is saved** (`setup.py`, a bug since upstream v1.3.0).
  - `query_device` built the configuration with `data={}`, and the framework saves it in place of the saved one, so every Update wiped the IR codes learned in the integration.
  - It now copies the learned codes, the Remote address and the setting from the saved configuration of the same Broadlink (same MAC). The API key is taken from it only when an empty PIN keeps it.
  - An Update that picks another Broadlink replaces the updated one, as before; that new one keeps its own saved values if it was already configured.
- **The Remote PIN is optional on Update** when an API key is saved: an empty PIN keeps it, unless another Remote address is chosen. On Add it is still required.
  - unfurled replaces a key by deleting the old one first, and only a wrong PIN fails before that. After any other failure the saved key may be gone, so the PIN is required for every address until a key request succeeds, in that setup or a later one: a cancelled setup saves nothing, so the saved key may still be the one the Remote no longer has. The flag lives as long as the driver (ucapi-framework keeps one setup flow object); a driver restart forgets it. The same Remote can be listed twice, as "Saved (address)" and as found by the search.
  - After a setup was cancelled or left to expire, the next Configure went back to the unfinished "Remote API access" page, whichever action was chosen. ucapi-framework 1.9.8 kept its pending configuration after an abort (an upstream bug); the setup flow now clears it when a setup starts or is aborted.
  - The flow also clears the Broadlink chosen for an Update when a new setup starts. The framework kept it, and an Add after an Update that ended without an abort deleted that Broadlink.
  - The API key never shows in a printed configuration (`repr=False`). ucapi-framework logs the pending configuration at ERROR when a setup step fails.
- **The PIN and the Remote API key no longer appear in the driver's log.** ucapi sets its own logger to DEBUG, logs every message to and from the Remote, and redacts only known keys.
  - The PIN field was called `remote_pin` (since upstream v1.3.0), so the PIN went into the log in clear. The field's id is now `password`, which ucapi redacts; the page looks the same.
  - Configure → Backup sends the whole configuration, the admin API key included, as one JSON string in a text field, and Restore sends it back in `restore_data`, so there was no key name to redact (pre-existing). A failed Restore shows the pasted text again, and ucapi-framework logs a device it can't read at WARNING.
  - `log_redact.py` (new) adds a filter to the root logger's handlers (`driver.py` installs it right after `logging.basicConfig()`), so it sees every record from every logger. It masks the values of `remote_api_key` and `password` as JSON, plain or escaped inside a JSON string, and as Python repr. The rest of each line, and the message log itself, stay. It filters the message text, not a traceback attached to a record, and a value with a quote or a backslash in it would be masked only up to that character.
  - This also covers the framework's DEBUG "User data response" line, which shows the PIN; that logger stays at WARNING anyway.
- **A Restore re-creates the device** (`broadlink_driver.py` `on_device_added`).
  - The framework's Restore replaces the saved configuration and calls only the add handler, which kept the running device and its old configuration object until the driver restarted. The restored wake wait didn't apply.
  - Also pre-existing: the old device's IP-change update would write its old configuration, learned codes included, over the restored one.
  - Only a Restore adds a configuration whose device is running (`add_or_update` updates an existing one in place; Add refuses a configured Broadlink). So the running device is removed first and created again from the restored configuration, as an Update's remove + add does.
  - Like an Update, this removes the device's entities and adds them again: until the core subscribes to them again, a press for the IR emitter gets ucapi's "no configured entity" answer (404).
  - Measured through core-simulator 0.83.2 and 0.85.3 (`tests/bl9_setup_e2e.py`, P0): the core sends `get_entity_states` and `subscribe_events` within 2 ms of a setup's end, so a press right after a saved Update or Restore is answered 200 and sent. The IR emitter is unconfigured for 0.01–0.04 s in an Update and for 1.0 s while a Restore is applied (the framework's pause), both before the setup ends; a press made in that second gets 404 and is not sent.
  - `remove_device` now also cancels the device's BL-7 retrying connect, for an Update too. The retry stopped on its own only after its current delay, and until then it kept the new device from getting its own; cancelling it also stops a connect in progress before it can update a removed device's configuration.
- **A new device keeps connecting** (`broadlink_driver.py` `setup_device_event_handlers`).
  - The framework creates a device (at driver start, on Add, Update or Restore) and makes one connect attempt. A Broadlink that couldn't be reached at that moment stayed disconnected until the next wake.
  - While the Remote is awake the new device now also gets BL-1/BL-7's retrying connect: every 0.5 s at first, then up to every 60 s.
  - Its entities become available once it connects and the core has subscribed to them. After an Update or a Restore the core is expected to subscribe again (see above). After an Add saved while the Broadlink couldn't be reached, no subscription is pending, so the emitter may stay unavailable until the driver restarts.
  - A running retry that belongs to another instance of the same Broadlink is replaced instead of left alone, and a Reset (`clear_devices`) cancels every retry.
- **Dropped during review: "Wait for Wi-Fi when it drops."** The first build also let a press wait for the Wi-Fi when it dropped while the Remote was awake (0–2 s, default 0).
  - Both multi-agent reviews found that this can't be made safe. ucapi doesn't say when a request was made, so a press that queued behind others got its window when it was handled.
  - With a Wi-Fi that came back and dropped again, queued presses went out seconds late, one of them after the core's 408.
  - The owner decided to remove it: outside the wake window a press fails at once.

**Log lines:**
- `Wait for the remote's Wi-Fi: up to 4.0s after a wake` (INFO, when the device is created: at driver start, and on Add, Update and Restore)
- `Device not connected, reconnecting before sending (1.2s after the wake)` (INFO, inside the window; BL-2's line with the time added)
- `Device not connected, trying once before sending (35.0s after the wake)` (INFO, outside it)
- `Gave up waiting for a connect attempt (4.0s after the wake)` (INFO)
- `Cannot send command: device not connected (4.0s after the wake)` (WARNING; BL-2's line with the time added)
- `No Remote PIN entered: keeping the saved Remote API key for <address>` (INFO, setup)
- `exit_standby: the wake window has already started for this wake` (INFO; `connect: …` the same)
- `Re-creating the device from its new configuration` (INFO, Restore)

**Not changed:** the 4 s send timeout; BL-7's quick probe, hold-off and background reconnect (one BL-7 timing moves, above: the wake press against an unplugged Broadlink); BL-8's status codes and remembered conversions.

**Known limits:**
- **Presses made right after the wake press, inside the wake window,** go out one after another once the wake press connects.
  - The wake press goes out up to the wake wait (4 s by default) plus about 1.5 s after the wake. Each press queued behind it follows about 0.12 s (a learned or remembered code) to 0.22 s (a code still to convert) after the one before, in the scenario model's device latencies, so a burst is replayed in full: ten VOL+ presses made 0.25 s apart, with the Wi-Fi back at 3.95 s and the first reply lost, went out until 6.44 s in the final review's runs.
  - Through core-simulator 0.83.2, with the Wi-Fi back at 3.1 s, presses made 0.3 and 0.6 s after the wake press were sent at 3.52–3.53 s with it.
  - A POWER pressed twice there toggles twice.
  - madalone.3 sent such presses late too, each with its own window.
  - Refusing them would need to know which presses were queued, and ucapi gives no request time.
- **A wake slower than the window** (the Wi-Fi back after 4 s with the default): the wake press and the presses queued behind it fail with an error, where some used to be sent late. The setting moves this edge.
- **A send whose first network-down failure takes 1 s or more** can't be told from a packet that went out, so it is not resent (no double toggle) and the press fails (`tests/bl9_window_checks.py` C8).
- **A press that reaches the driver before `exit_standby`** (about 1 wake in 80: 2 of 159 in the Kodi driver's logs, Oct 2026) is outside every window. With the Wi-Fi still down it gets its error at once and is never sent late; the Remote's "Retry commands after wakeup", if on, resends it once the window has started. madalone.3 gave it about 2.5 s of its own.
  - Fix round 3 let such a press start the window, and fix round 4 dropped that after review. Per device, it sent queued presses late with two Broadlinks; its time bound could be outrun by a failing wake press; and the driver can't tell a wake press from a command sent through the Remote's API while the Remote stays asleep.
- **A command sent while the Remote is in standby** (through its API, by Home Assistant say) is outside every window too: one attempt.
- **Two older, rare late-send paths remain** (madalone.3 had them too):
  - a wake press whose last attempt connects late in a 5–6 s window, and whose send then goes unanswered for 4 s, is resent after BL-7's 1 s probe, after its 408. The presses queued behind it are then sent right after it, also after their own 408s, even with one Broadlink: POWER pressed twice toggles twice;
  - with two Broadlinks, a press for one, queued behind the other's failing wake press, is handled after its 408 and gets its one attempt.
  - Closing them needs to know when a request was made, which ucapi doesn't say, or a time budget per press (not in BL-9).
- **A standby bounce while the wake press is still waiting** (`enter_standby` and `exit_standby` within seconds of a wake, queued behind that press) starts a new window when the pair is finally handled, so a press queued behind both can wait twice and, at 5–6 s, be sent after its 408.
  - On this Remote the core sends `enter_standby` only with the system standby, at least 90 s after a wake: all 189 wakes in a week of the Kodi driver's log (2026-11-07..14) lasted 91.7 s or more.
  - So it needs a standby time under ~7 s, set through the API, or an explicit standby command right after a wake.
- **A driver started or reconnected while the Remote sleeps** never sees that standby's `enter_standby`. The core's connect at the session's start takes the wake's turn, and a wake within 60 s of it, the time asleep included, gets no window; later wakes start their own (`WAKE_EVENT_LAPSE`). On this Remote, new sessions began while it was awake.
- **One API key name for every Broadlink on a Remote** (upstream): a key request for one Broadlink deletes the keys saved for the others, so with more than one, an Update needs the PIN for each.
- **A Broadlink that changed IP address during standby, with the Wi-Fi down at the wake:** the wake press fails, and the driver's background full connect finds the Broadlink later.
- **The key guard is forgotten when the driver restarts:** after a key request that may have deleted the key, a driver restart lets the next Update keep the saved key with an empty PIN. If converted codes then fail, an Update with the PIN makes a new key.

### Verification (BL-9)

- **`tests/bl9_scenarios.py`** (new, in the repo): the router-level scenario test.
  - **How it runs:** each run starts the tree's own `driver.py` with the real ucapi 0.7.0 router and ucapi-framework 1.9.8. python-broadlink and unfurled are faked with the libraries' timing, and a fake core answers 408 after 10 s. Every run gets its own port and must see exactly one core connection; two parallel runs once shared a driver and produced false double sends.
  - **The catalogue:** 203 runs, 50 of them in `--quick`; 47 need the BL-9 setting.
    - the wake cases (W1, W2, P2: the 17:42:03 replay) and stale connections (X1, X7);
    - outages while awake (O1–O5, AX3, AX4) and BL-7's timings (S1, S2, B1);
    - lost replies and slow failures (V1, V4, J3, J5);
    - the setting (WW: wake wait 0 / 4 / 6 s, including the window's last half second);
    - the reviews' cases:
      - QW: presses queued behind a slow wake press;
      - TW, MX: a second drop between a press's connect and its send, at the default and the maximum wake wait;
      - H1: the driver's hello meets the Wi-Fi's return and loses its first reply;
      - E1: a lost first reply in the window's last second;
      - RZQ, RZD, RZN, RZ2: a press queued behind a wake press that reconnects at the window's end, then a re-drop;
      - RZC: the core's connect with no `enter_standby` before it (since fix round 4 it starts no window);
      - FL: a Wi-Fi that flaps during and after the window;
      - EQ: three converted presses queued behind a wake press whose first reply is lost; ZB10: ten VOL+ presses 0.25 s apart, with the Wi-Fi back at 3.1 s, or at 3.95 s with the first reply lost;
      - PB: the wake press reaches the driver before `exit_standby` (it gets an error at once), with the Wi-Fi back at 1.0, 2.0 or 3.1 s, presses queued behind it meeting a re-drop, or a Wi-Fi that never comes back;
      - fix round 4's: XW (a stale connection's first-packet failure inside the window, after an `enter_standby`); CN (the core's connect and `exit_standby` in one wake, in every order, with presses between and behind them); W2W (two wakes in one run, and an `enter_standby` / `exit_standby` bounce behind a waiting press); MD (two Broadlinks, a press for B queued behind A's wake press); EX (`enter_standby` inside the previous wake's window, then the next wake's press before its `exit_standby`); and fix round 5's LP (a wake about 68 s after the setup's connect, in a session that saw no `enter_standby`: the lapse gives it a window).
    - Runs whose second drop must land in a short gap (TW, MX, RZ…, PB's re-drops) are judged INVALID when it missed, not PASS. In three repeats every drop landed, at least 21 ms inside its gap.
  - **What a press must do:**
    - A press made inside the wake window must be sent if the Broadlink can be reached at least 0.3 s before the window ends. That becomes 1.3 s when the Broadlink was silent or its first reply was lost, because python-broadlink resends a hello only every second.
    - If the Broadlink can be reached only after the window, it must get an error by the window's end + 0.5 s (1.5 s when the Broadlink was silent or its first reply was lost), and nothing may be sent. In between, either is allowed, but never a send after that bound plus the queue allowance below.
    - A press handled after the window with the Wi-Fi down, whenever it was made, and a press made outside the window with the Wi-Fi down, must get an error within 0.5 s, and nothing may be sent.
    - A Wi-Fi that was back for less than 2 s between two drops may count either way.
    - Only the first wake event (`exit_standby` or the core's connect) after an `enter_standby`, or after the driver starts, starts a window, and one handled 60 s or more after the window's start (`WAKE_EVENT_LAPSE`, LP); `enter_standby` ends it. A press handled in standby, before the next wake event, is outside every window.
    - Presses sent one after another from a queue get 0.3 s each (`QUEUE_STEP`) on top of both limits, for every press sent before them.
  - **On every run:** no 408, nothing sent twice, nothing sent after an error answer or after the core's 408, and no DOUBLE-TOGGLE (two late sends of one code within 1 s). Two presses of one code, both made inside one wake window and each sent once, are a REPEAT instead: the documented limit.
  - **Results:**
    - BL-9: 203 of 203 as expected (8 at a time, about 10 minutes; `--quick` 50 of 50).
    - madalone.3: 71 of the 156 runs without the setting as expected (the setting's 47 runs don't apply); 77 FAIL, 8 INVALID.
      - Fail, in whole or in part: the outages while awake (O1–O3, O5, AX3; O2 sends after the core's 408 in 3 of its 4 runs), the slow wakes (W2, P2), W1's stale-connection run (an `exit_standby` with no `enter_standby` before it: madalone.3 converts the code and sends the wake press at 1.16 s, BL-9 fails it at once, the known limit of a driver that never saw the `enter_standby`), X7, V4, S2, H1, QW (it toggles twice), FL (a press sent 2.3 s late), ZB10, S1 U8 (its rule now follows the wake window), and of fix rounds 4 and 5's families PB (the wake press waits ~2.5 s), XW, CN, MD, RZC, EX and LP (madalone.3's stale send gets one quick probe).
      - INVALID: madalone.3's wake press gives up at 2.57 s, so the moment those runs aim at never comes (TW, RZD, RZN, RZ2).
      - X1, W2W and EQ pass there: madalone.3 also fails a stale press at once when the code needs no conversion (a remembered, local or learned code; one still to convert is retried, as in W1), and its queued presses land inside the allowance.
- **`tests/bl9_window_checks.py`** (new, in the repo): 18 direct checks on the real device, IR emitter and driver, with `tests/bl8_harness.py`'s fakes; it refuses to run with a network route. They cover what the router-level runs reach only by timing:
  - outside the wake window, one attempt and 503 at once, nothing sent once the Wi-Fi is back (C1);
  - the first-packet rule (C8);
  - one window per press through the send and through the conversion (C9, C10);
  - a press handled after the window behind a press that reconnected at its end, then a re-drop (C11);
  - the listeners running as ucapi emits `enter_standby`, `exit_standby` and connect (C7);
  - a press before `exit_standby` getting no window (C12); one window per wake, whatever the order of `exit_standby`, the core's connect and the presses, and the lapse: with no `enter_standby`, a wake event 59 s after the window's start starts none, one 61 s after starts its own and moves the stamp on, and an hour asleep counts (C13); a new device's retry, and its replacement for a re-created one (C14);
  - a failed send whose reconnect fails after an attempt that left an unauthenticated client: 503, no resend (C15);
  - a send that waited for the device lock, a silent Broadlink, wake wait 0, clamping (a value too large for a float too).

  BL-9: 18 of 18.
- **`tests/bl9_setup_test.py`** (new, in the repo): drives the real `BroadlinkSetupFlow` through ucapi-framework 1.9.8's setup state machine and every setup message through ucapi's own message handler, 109 checks.
  - BL-9: 109 of 109, three times.
  - **Its page check follows the core's own model** (`api-model-rs` `src/model/settings.rs` at the commits that core 0.83.2 and 0.85.3 embed), not the API description: `steps` and `decimals` whole numbers, a list's ids and value text. S1 checks every page of the run (248), S2 the stricter rules (a list value that is one of its items, no id twice, no unknown key; one named exception, the framework's "---" entry), S3 that the check can fail, with the page the Remote refused among its cases, S4 `driver.json`'s static page, S6 that no page has a number field.
  - **Its stand-in core checks answers as the real one does** (every input field, every value text, a list's value one of its items). The 26 answers that try the driver's own leniency go past it on purpose; S5 counts them, with what the stand-in finds wrong with each.
  - On the code of the first test build it gives 77 of 109: the page checks, the list's checks and V1/V2 fail. The check before fix round 7 followed the API description and passed that page.
  - A reviewer ran about 40 more page shapes through both cores. The check had passed two kinds the cores refuse (a text with a lone surrogate, a whole number no float can hold): both are in the check now. It refuses one kind the cores take (a struct written as a JSON array), which is left as it is.
  - F7–F9: a saved wait that is no item (text, 99, 2.5). V1, V2: one discovery object across two setups lists the Broadlink once, and after an address change the Update saves the new address.
  - Its log checks run `driver.py`'s real `main()` up to where it creates the driver, so the logging is the driver's own, filter included. The PIN appears in no written line (P1, P2), and no API key, saved, restored or new, in any line of the driver's log (P3). P2 fails if the handler lacks the filter.
  - K5–K7: Configure → Backup, Restore, and a Restore the framework can't read (the page shown again with the pasted text, and its "Failed to deserialize device" warning). The key is in the records as logged and in no written line. L1–L12: the filter's patterns, a record with %-args, a record without secrets, install() run twice.
  - H4, H5: the key guard survives a Cancel and a new setup, and a successful key request clears it.
  - R1–R8, the new devices, through the real `BroadlinkDriver`, config manager and `rm.Broadlink`:
    - a Restore while the Broadlink can't be reached gives a new device with the restored wake wait at once, cancels the old retry, and the new device's own retry connects it once it is back;
    - an IP-change update then keeps the restored codes, wake wait and key;
    - likewise a Restore onto an empty configuration, a Reset followed by a Restore, a Remove, and an Update while the Broadlink can't be reached;
    - a device created in standby gets no retry until `exit_standby`;
    - an unsubscribe and subscribe of the entities replaces a stale retry.
  - madalone.3's `setup.py` inside the BL-9 tree: 43 of 109 (madalone.3's own tree can't run it: its `BroadlinkConfig` has no BL-9 field). There an Update ends with "learned codes 0", and the PIN is in the log in clear; the log and R checks pass because they test the tree's own `driver.py`, `log_redact.py` and `broadlink_driver.py`.
  - With madalone.3's `driver.py` and `broadlink_driver.py` as well: 28 of 109. The key is in the written log after a Backup or Restore, P2 finds no filter, the old device is kept after a Restore, its configuration is written back over the restored one, and no retry starts for a new device.
- **`tests/bl9_setup_e2e.py`** (new, in the repo; fix round 7): the tree's real `driver.py` through the real core (core-simulator, `--network none`), along every path of the wizard, driven through the core's REST API with the answers the web configurator sends. Every network edge is faked, every address is in 192.0.2.0/24, and it refuses to start with a network route.
  - A page is SHOWN when the core hands on the whole page as sent, REFUSED when the core's setup state stays SETUP; `--crosscheck` compares each verdict with the core's own log by time.
  - Paths: P0 a press right after a saved Update and a Restore; P1 first setup; P2 Update with the PIN empty; P3 a wrong PIN; P4 no Remote found; P5 manual entry; P6 Backup and Restore; P7 Remove, nothing configured, Add; P8 Reset; P9 answers no user interface sends; P10 Update, Cancel, Update.
  - BL-9, on 0.83.2 and on 0.85.3: every page of every path SHOWN, every expectation met, the core's log agreeing.
  - The first test build's code: "Remote API access" REFUSED on every path, on both versions. Two broken copies (the old number field; the list's value left as a number) are REFUSED too, and a driver that ignores the second `subscribe_events` fails P0.
  - P9: the core answers 400 to a wait that is no item, to a missing field and to a value that isn't text, and hands on an empty PIN.
  - It can't see how a page is drawn. That was checked once by hand in the simulator's web configurator (v2.6.3, the Remote's version, 2026-11-19): the page, the list, 5 seconds with the PIN empty, the setup's end, and the saved value on reopening. The framework's "---" entry (P7) was not tried in a browser.
- **`tests/bl8_harness.py`:** 42 of 42 on both BL-9 and madalone.3. Its expectations follow the code under test. With BL-9, W2's slow wakes (2.8 and 3.5 s) now succeed, and the checks that expect an error allow the 4 s wake window plus 0.3 s instead of 3.6 s.
- **Mutation checks, run one copy at a time (memory).**
  - **Fix round 7's setup change:** 27 broken copies of the list and of `discover()` (the number field back, `steps` 1.0, the value as a number or as "4.0", an item id as a number, a value that is no item, a short list, a wrong mark, half-second or `round()` rounding, the answer not parsed, a page that rewrites the saved value, an error page that forgets the chosen wait, no clear), all caught by `tests/bl9_setup_test.py`.
    - Two of them got past the test's first version: an error page that selects the saved or the default wait instead of the chosen one (after a wrong PIN; on Add). B1, D2 and D3 check it now.
    - A reviewer's 6 further copies are caught too. One of them, an unusable saved wait read raw, got past the first version, until F7–F9 got a third line.
    - 10 broken copies of the test itself (a page check that never objects or takes any `steps`, a recorder that keeps no page, a stand-in core that checks no answer, takes any list value or misses a field left out, the "---" exception widened, the two new JSON rules off) each fail a check of S1–S6. Three of them (the stand-in checking no answer or taking any list value, the exception widened) got past the first version, which is why S5 pins what the stand-in finds wrong and S3 pins the exception.
    - The 9 earlier setup copies are still caught.
  - **The wake-window code (round 6, unchanged since):** 41 broken copies, all caught by the repo's tests, each counted at the first test that caught it:
    - `tests/bl9_window_checks.py`, 29: round 2's window mutants (a window per press, no `exit_standby` listener, a failed send that never waits, the last attempt half a second early, the setting ignored, no clamping, the send's start noted before the device lock, a conversion outside the window that keeps BL-8's own 3 s, BL-7's silent-device check dropped inside the window, the first-packet rule removed, a press outside the window that waits anyway), round 3's (a resend after a failed reconnect, `OverflowError` escaping `wait_setting`), round 4's and round 5's: every wake event starting the window (a lapse of 0 s), no lapse, `enter_standby` not clearing the flag, `enter_standby` not ending the windows, `note_standby` ending nothing, a device created in standby keeping its window, no retry for a new device, a stale retry kept, infinity read as 6 s; round 6's: a lapse of infinity, an hour, 65 s or 55 s, a lapse that doesn't move the stamp on, the lapse counted on the event loop's clock, `CLOCK_BOOTTIME` never used;
    - `tests/bl9_setup_test.py`, 9: the PIN field's id back to `remote_pin`, the Update selection kept into the next setup, the key guard reset per session or kept after a success, the filter not installed or missing the escaped JSON form, no Restore re-create, `remove_device` keeping the old task, `clear_devices` keeping the retries;
    - `tests/bl9_scenarios.py --quick`, 2: the press's own hello listening only 1.4 s, and the 3 s limit on waiting for the driver's attempt;
    - `tests/bl8_harness.py`, 1: a conversion that never retries inside the window.
  - **Round 5's code:** 34 broken copies, all caught.
  - **Round 4's code:** 33 broken copies, all caught.
  - **Round 3's code:** 29 broken copies, 28 caught; the survivor moved `command_deadline()` after the connected check, which mattered only to the standby rule that round 4 dropped.
  - **Round 2's code:** 16 broken copies, all caught by the repo's tests:
    - `tests/bl9_window_checks.py`, 11: a window per press, a wake that doesn't start the window (no `exit_standby` listener), a failed send that never waits, the last attempt half a second early, the setting ignored, no clamping, the send's start noted before the device lock, a conversion outside the window that keeps BL-8's own 3 s, BL-7's silent-device check dropped inside the window, the first-packet rule removed, and a press outside the window that waits anyway;
    - `tests/bl9_setup_test.py`, 2: the PIN field's id back to `remote_pin` (the PIN in the log), and the Update selection kept into the next setup;
    - `tests/bl9_scenarios.py --quick`, 2: the press's own hello listening only 1.4 s, and the 3 s limit on waiting for the driver's attempt;
    - `tests/bl8_harness.py`, 1: a conversion that never retries inside the window.
  - **The setup flow, the logging and the new devices (round 4, on the number field of that time; not re-run on the list):** 74 broken copies of `setup.py`, `config_manager.py`, `driver.py`'s logging, `log_redact.py` and the driver's Restore and retry paths, 70 caught by `tests/bl9_setup_test.py`.
    - Among the caught: the PIN field's id back to `remote_pin`, no reset of the Update selection, the key guard reset per session or kept after a success, the filter not installed, installed before `basicConfig()`, missing the escaped JSON or a repr form, the Restore re-create or the task cancel removed, no retry for a new device, a retry in standby, a stale retry kept, `clear_devices` keeping retries.
    - Of the 4 survivors, two can't change behaviour in the framework's flow. One only changes the wording of the error an empty PIN gets after a failed key request when no saved access is kept, a path no check reaches. The last raises ucapi-framework's logger in `driver.py`, which the filter now makes harmless.
  - **Earlier rounds, both on code with the drop window:** before fix round 1, 18 broken copies, all caught; after it, 23, of which 22 were caught.
- **Lint:** isort and flake8 report the same findings as before BL-9. black would reformat only `discover.py`, an upstream file, as it already did before; BL-9 adds one statement to it by hand.
- **Presses end to end through core-simulator 0.83.2** (the owner's core version): the real core's REST API sends `send_ir` with a converted code to the real IR emitter and device, with the Broadlink and the Remote's network faked as in `tests/bl8_harness.py`; 0.3 s between presses. The script is outside the repo.

  | Case | madalone.3 | BL-9 |
  |---|---|---|
  | awake, five presses, Wi-Fi back at 8 s | 503 at 2.52 / 5.03 / 7.54 s, then two toggles sent at 8.05 s | 503 within 0.01 s each, nothing sent |
  | awake, five presses, Wi-Fi back at 11.6 s | 503 at 2.52 / 5.03 / 7.54 / 10.04 s; the fifth answered 408 by the core at 11.21 s and **sent at 12.06 s** | 503 within 0.01 s each, nothing sent |
  | awake, Broadlink still marked connected, Wi-Fi back at 8 s | 503 at 2.55 / 5.09 / 7.63 s, two sent at 8.14 s | 503 within 0.01 s each, nothing sent |
  | a wake, one press, Wi-Fi back at 3.1 s | | 200, sent at 3.52 s |
  | a wake, three presses, Wi-Fi back at 3.1 s | | 200, all three sent at 3.52–3.53 s over the rounds (the limit above) |
  | a wake, Broadlink still marked connected, Wi-Fi back at 1.0 s | | 200, sent at 1.02 s |
  | a slow wake, three presses, Wi-Fi back at 5 s | | 503 at 4.00 s each, nothing sent |

  The first build, with its drop wait at 2 s, answered the slow wake's first press 503 at 4.00 s and sent the other two at 5.01 s.
- **Reviews.**
  - **First review:** a multi-agent review in four lenses (the windows, the IR emitter, the setup, the tests and docs), each finding verified by an adversarial agent. It confirmed 18 findings, 2 critical, and fix round 1 addressed them all:
    - one press could wait in two windows, and with 6 s and 2 s set a POWER was sent after the core's 408;
    - presses queued during a slow wake got a fresh drop window and went out late in a burst.
  - **Fix round 1** added the review's fixes and one more found while building them: an Add after an Update that ended without an abort deleted the Update's Broadlink.
  - **Re-review** of fix round 1 (2 lenses, 2 verifiers): 10 findings confirmed. The critical one, found by both lenses, was that the drop window still couldn't stop a queued press from waiting late when the Wi-Fi came back and dropped again, because ucapi gives no request time. The owner decided to remove the drop wait.
    - Major: `remote.py`'s repeats and sequences opened a new window per send (with one wake window, every send shares it), and a change of mine beyond the plan, the core's connect keeping "the network has worked", reopened the queued-press case (gone with the drop window).
    - Minor: the setup PIN logged in clear (fixed: the field is now `password`); "Update keeps what is saved" holds for the same Broadlink only (now said); BL-7's timing note (corrected above); TW and MX tested nothing when their re-drop missed (now INVALID); the drop checks' offline guard sent a real request to the Remote's address (`tests/bl9_window_checks.py` checks the route only); stale counts in docstrings.
  - **Final review** of fix round 2 (2 lenses, 2 verifiers): 13 findings confirmed. Reviewers found the listeners, the first-packet rule, the bounded waits and one window per press sound.
    - Major: a failed send was resent through a client that never authenticated (500 instead of 503; older than BL-9, made reachable by the wait); the Remote API key was in clear in the log after a Backup or Restore (pre-existing).
    - Minor: the queued-press bound was understated; the key guard lasted one session; a Restore didn't reach the running device; `OverflowError` at start; stale notes and comments.
    - It also raised the wake press that reaches the driver before `exit_standby`, which the stored device notes confirm happens.
    - The owner chose to fix them all in BL-9 (fix round 3).
  - **Focused review** of fix round 3 (2 lenses, 2 verifiers), and a design check of its fix (Plan agent): the standby rule was unsafe.
    - Per device, a queued press for a second Broadlink waited twice and was sent after its 408.
    - Its 5 s same-wake bound could be outrun by a failing wake press.
    - Without a bound, a later wake got no window.
    - The driver can't tell a wake press from a command sent through the Remote's API while it sleeps.
    - Also: the core's connect restarted a running window when queued behind a waiting press (since round 2), and a Restore while the Broadlink was unreachable left it disconnected until the next wake.
    - The owner decided to drop the standby rule (fix round 4).
  - **Fix round 4** added one window per wake, the retries for new devices and the infinity fix. Its test update found that `enter_standby` didn't end a running window, so a wake forced back to sleep at once lent its window to the next wake's early press (EX); fixed with `note_standby`.
  - **Focused review** of fix round 4 (2 lenses, 2 verifiers) found two rare paths:
    - a standby bounce queued behind a waiting wake press;
    - a driver started or reconnected while the Remote sleeps.

    One verifier checked both against a week of device logs: the core sends `enter_standby` only with the system standby, at least 90 s after a wake, and new sessions began while the Remote was awake. The review also found doc errors: path 1 with queued presses, the retry and Restore claims, the model's docstring.
  - **Fix round 5:** the owner chose to document both rare paths, correct the docs and add `WAKE_EVENT_LAPSE`.
  - **Light review** of fix round 5 (1 reviewer, 1 verifier): 7 findings, all confirmed, none critical or major:
    - the lapse counted only the time the Remote was awake (the event loop's clock stops in standby);
    - C13 tested the lapse through the driver's own constant, so a lapse of infinity, an hour or 65 s, or no re-stamp, passed it;
    - doc errors: madalone.3's W1, the PIN count, the Add marker, the scenario docstrings, the bounce missing from the CLAUDE.md row and the CHANGELOG.
  - **Fix round 6:** the owner chose to count the time asleep (`CLOCK_BOOTTIME`) and to go to the device test without another review. C13 now pins the lapse on both sides of 60 s, its re-stamp and its clock.
  - **Device test of 2026-11-19** (below): the core refused the setup page.
  - **Fix round 7:** research (the core's model, the page through the simulator, both user interfaces' source) and a plan review, then the list, the `discover()` fix, the page check from the core's model and `tests/bl9_setup_e2e.py`. The owner chose a list in whole seconds, and a shortcut on the wizard's first page ("Configure selected device", as UC's own integrations have it) as a later patch of its own. Two reviewers then ran broken copies and looked for holes in the two tests; what they found is in the mutation notes above.

### On the device (madalone.4, a test build, installed 2026-11-16 18:18 UTC)

- **Normal use, 2026-11-16 to 2026-11-19:** 54 wakes; 1,052 commands (576 `send_ir`, 476 `stop_ir`), all answered 200. 9 presses found the Broadlink disconnected 0.0–0.2 s after a wake, reconnected in the wake window and were sent. No `trying once`, `Cannot send command`, `Gave up waiting` or `already started for this wake` line. No wake had the Wi-Fi down for seconds with a press waiting, so the outage path is untested on the device.
- **Update, 2026-11-19: failed.** The core refused "Remote API access" twice (see "Why a list"). Nothing was saved. The first session was cancelled; the second stayed open until the core's setup time limit ended it about 2.5 hours later (`abort_driver_setup` TIMEOUT), and the driver carried on.
- The second attempt's "Discovered Devices" listed the Broadlink twice.

### On the device (madalone.5, a test build, installed 2026-11-26 18:42 UTC)

- **Start-up:** `Wait for the remote's Wi-Fi: up to 4.0s after a wake` (the saved configuration had no wait yet), the Broadlink authenticated in 0.1 s, the core subscribed to the three entities. No warning.
- **Update, 2026-11-26: passed, twice.**
  - "Discovered Devices" listed the Broadlink once.
  - "Remote API access" came 5.0–5.6 s after the pick (the search for the Remote). The core accepted every page (`Ok(DriverSetupChange …)` in its log, no `Err`). The list showed "4 seconds (default)" the first time and "5 seconds" the second.
  - The answer, 5 seconds with the PIN left empty (logged as `***REDACTED***`), kept the saved key (`No Remote PIN entered: keeping the saved Remote API key`). The device came back with `up to 5.0s after a wake`, and the core subscribed to the entities again within 20 ms of each setup's end.
- **Presses after the Update:** after a standby and a wake, the Broadlink connected 0.95 s after `exit_standby`; two presses were answered 200 and sent.
- No PIN in clear in the driver's or the core's log.

Not run on the device yet (the owner committed without them, 2026-11-26): the outage (the Remote blocked on the router for ~15 s while awake, more than the wake wait after a wake); the Broadlink unplugged (B5); normal use with Broadlink-button wakes, and the log read for every wake (a `trying once` line right after a wake: a press before `exit_standby`; any `already started for this wake` line). Optional, because this build passed them in core-simulator 0.83.2 and 0.85.3 (the independent package check): Configure → Backup (the log shows `***REDACTED***`, no key); Restore with another wake wait (the creation line shows the new value at once), also with the Broadlink unplugged, then plugged back in (it connects without a wake).

---

## Verification (offline harness, outside the repo)

The harness runs the **real** driver (`BroadlinkDriver` here, `BaseIntegrationDriver` for upstream), the real `rm.Broadlink` device and the real entity command handlers. It talks to a fake python-broadlink whose network can be down (instant `Errno 101`, as on a wake) or up with the device not answering (blocking until timeout, like the real library). Upstream v1.3.0 runs with ucapi-framework 1.9.6, the fork with 1.9.8.

| Scenario | upstream v1.3.0 (framework 1.9.6) | fork |
|---|---|---|
| S1 wake, Wi-Fi back after 0.3 / 0.6 / 1 / 2 / 3 s | **never reconnects** (all 5) | reconnects 0.05–0.44 s after Wi-Fi returns |
| S2 the press that wakes the Remote (50 ms after `exit_standby`, Wi-Fi back at 0.6 s) | "OK", **nothing sent** | sent; answered after 1.04 s |
| S3 press 2 s after a wake whose first connect failed | "OK", **nothing sent** | sent |
| S4 network down for the whole wake, press at 2 s | "OK", nothing sent | `SERVICE_UNAVAILABLE` after 2.6 s |
| S5 network fine, Broadlink not answering | "OK" instantly, nothing sent; **event loop frozen 10.0 s** | `SERVICE_UNAVAILABLE` after 5.8 s (3.0 s with BL-7); worst loop stall 7 ms |
| S6 driver starts offline, Remote subscribes before Wi-Fi is back (Discord reporter) | IR emitter never configured; presses → 404 `no configured entity` | configured once Wi-Fi returns; press OK |
| S7 Remote sleeps again during the wake retry | no connect after sleep | no connect after sleep |
| S8 `repeat`: IR emitter int 3 / str "3"; remote int 3 / str "3" | 1 / 3 / 1 / **9** sends | 3 / 3 / 3 / 3 sends |
| S9 replay of the 28 dead wakes of 2026-10-17..19: one driver across all wakes, the real press timings (0.69 s to 30.5 s after waking) and the real IR codes from the log; Wi-Fi back after 0.3 / 0.6 / 1 / 2 / 3 s | 28/28 wakes never connect; 22 presses answered "OK", **0 sent**, at every Wi-Fi time (matches the log exactly) | 0/28; connected 0.04–0.46 s after Wi-Fi returned; 22/22 sent; slowest answer 2.6 s (a press 0.69 s into a wake whose Wi-Fi took 3 s) |

S9: the log doesn't record how long Wi-Fi took to come back, so the replay sweeps the range seen for the Kodi integration on the same Remote. Upstream reproducing the log exactly is the check that the replay is faithful. A press waits at most the 3 s reconnect window. If Wi-Fi takes longer, the press fails with `SERVICE_UNAVAILABLE` (S4), and the wake retry still connects once Wi-Fi is back. Script: `bl_replay.py` next to the harness.

### BL-7: the Broadlink stops answering while the Remote is awake

Same harness, script `bl_unplug.py`. The fake Broadlink models python-broadlink's resend loop: a packet every second until the timeout, so a device that comes back mid-attempt is heard. Silent discovery returns nothing rather than raising, as in the real library. Presses are handled one at a time, like ucapi's router.

| Scenario | madalone.1 | madalone.2 (BL-7) |
|---|---|---|
| U1 one press | error after 10.0 s | error after **5.0 s** |
| U2 five presses 0.3 s apart | answered at 10 / 16 / 22 / 28 / 34 s | all answered by **5.0 s** |
| U3 the same, Broadlink back at 8 s | **all five sent together at 9.0 s**, up to 9 s late | none sent late; the driver reconnects by itself at 10.1 s |
| U4 back at 15 s, press at 25 s | sent | sent (background reconnect at 16.1 s) |
| U5 long outage, presses at 0 / 10 / 20.5 s | errors after 10 / 6 / 6 s | errors after 5 / 1 / 1 s |
| U6 back at 12 s, press at 13 s | sent | sent |
| U7 back at 10 s, no further press | never reconnects | reconnects by itself at 10.1 s |
| U8 Broadlink already off at the wake; presses 0.2 s and 7 s after it | errors after 5.8 / 5.5 s | errors after 3.0 / 0.0 s |
| U9 the packets of one send are lost, Broadlink fine | sent once, after 4.0 s | the same |
| U10 Remote sleeps during the background retry, Broadlink back meanwhile | no attempt while asleep; connected 0.03 s after the wake | the same |
| U11 remote `send_cmd_sequence` of 3 commands while unplugged | 22.0 s | 5.0 s |

Adversarial review checks (`bl_review.py`):

| Check | madalone.1 | BL-7 draft | madalone.2 |
|---|---|---|---|
| R1 driver start with Wi-Fi down, back at 1 s, with a yield between registration and `connect_all_with_retry()` (a second device's connect causes one) | connected 0.05 s after Wi-Fi | **4.03 s**: the background retry, hooked to the connection-error event, replaced the fast one | 0.05 s |
| R2 Broadlink off during sleep; press 5.9 s after the wake, as the wake retry's first ~6 s attempt fails | error after 6.1 s | 3.0 s | 0.6 s |

Wake behaviour is unchanged. S1–S8 give the same results as madalone.1, except S5 (5.8 → 3.0 s). The S9 replay still gives 0/28 dead wakes and 22/22 presses sent at every Wi-Fi timing, with the slowest answer 2.54 s. All of it was also run on Python 3.11.12 inside the build image `r2-pyinstaller:3.11.12-0.3.0`, the Remote's interpreter, with the same results.

### On the device (madalone.1, 2026-10-21)

- **B3:** the three entity records were byte-identical before and after the upload. This household's integration holds no learned codes; its IR codes live in the Remote.
- **B4, Broadlink unplugged:** the first press was answered `503` after 10.07 s, as the harness predicted (10.0 s). Four later presses took 6.03–6.04 s each. The Remote showed an error and waited the full time; it has no shorter timeout of its own. An activity started meanwhile still ran its other steps. After replugging, the first press reconnected and was sent in 0.31 s.
- **Wake:** a press that woke the Remote reached Kodi after 1.15 s, and the Broadlink reconnected 1.2 s after the same wake.
- **B1, a Broadlink button wakes the Remote:** at 12:48:10 UTC, `exit_standby` and the IR command arrived in the same millisecond. The wake retry connected in 34 ms, and the press was answered `200` 125 ms after the wake.

### On the device (madalone.2, installed 2026-10-21 12:51 UTC)

- **B5, Broadlink unplugged:**
  - The first press was answered `503` after **5.05 s**: `Custom command failed … within 4s`, then the 1 s quick probe. The harness predicted 5.0 s.
  - Eleven later presses got their error in about 1 s (a quick probe, or `A connect attempt is still running`) or in 15–18 ms (`Device did not answer …s ago, not retrying yet`).
  - Background retries ran at +5, +10, +30 and +60 s. The 4th one, `Reconnected after 4 background attempts`, connected within about a second of the Broadlink answering again.
  - The next six presses were sent in 33–137 ms. None of the failed presses was sent late.
- No standby wake happened on madalone.2 that day: the Remote never slept after the install. Its wake path is unchanged from madalone.1 and proven offline (S1–S9).
- **B2, a day of use (reviewed 2026-10-22 13:16 UTC):** 31 wakes, counted only where the log shows `exit_standby`.
  - All 31 connected: median 0.13 s after the wake, worst 1.26 s.
  - On 5 wakes the first attempt failed with `Network is unreachable`, the original bug. They recovered 0.45–0.51 s after the wake.
  - 2 wakes were triggered by a Broadlink `send_ir` (B1 on madalone.2). Both were sent, in 0.02 s and 0.08 s.
  - Every non-200 answer fell inside the deliberate unplug test (12:52:21–12:54:23 on 10-21); there were none in normal use.
- Released as `v1.3.0-madalone.2` on 2026-10-22.

Upstream's `test_pronto_conversion.py` doesn't run on either tree: it is a development script that fails with `IndentationError` at line 68 on pristine v1.3.0. madalone.2 doesn't touch IR conversion (BL-8 does, see above). The isort/flake8 findings are identical to pristine upstream, and black reports no changes.

## Deploying

- **Back up first.** Learned IR codes live in this integration's config. `GET /api/system/backup/export` (administrator account; integrations restart for a few seconds) or the web configurator's backup page. The archive contains secrets, so keep it outside any repo.
- Upload the tarball over the existing Broadlink integration in the web configurator. The same `driver_id` keeps its config (verified with the Kodi fork on 2026-10-20). Check afterwards that the learned-code list (media player source list) is unchanged.
- **Never press Update on the Broadlink card in uc-intg-manager.** It resolves updates against upstream and would install stock v1.3.0 over the fork (the same mechanism as the Kodi fork; see that project's CLAUDE.md).
