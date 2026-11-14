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
- **Reconnect on demand.** `send_command` on a disconnected device calls `_reconnect_for_command()`. It first waits for an attempt that is already running, then keeps trying every 0.5 s for up to 3 s (`COMMAND_RECONNECT_WINDOW`). The button press that wakes the Remote arrives before Wi-Fi is back, so a single attempt would fail instantly.
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
- **BL-2's reconnect window is now a real bound.** The patient reconnect, for the press that wakes the Remote, waits for an attempt already in progress only within its own 3 s window. Before, it waited for the whole attempt: ~6 s against a silent device. Once an attempt has found the device silent, the press switches to the quick probe.
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
- **Retry a conversion only while the network is down.** Retried 0.5 s apart (`CONVERT_RETRY_INTERVAL`) while the press is inside its 3 s window (`COMMAND_RECONNECT_WINDOW`), for:
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
  - A conversion still gets at least 1 s after a late connection (W19: 3.51 s).
  - Worst case: a Broadlink that doesn't answer and a network that comes back during the window holds the press about 8.5 s.
  - Every command has worked this way since BL-2; converted codes, which used to fail at once, now share it.
- **A long Wi-Fi outage queues presses.** ucapi handles one request at a time, and each converted press now waits up to ~2.5 s for the network, as raw and locally converted codes already did (BL-2). When the network returns, the presses still queued are sent together, several seconds late. One that waited past the core's 10 s answers 408 there and is still sent afterwards. Measured end to end through core-simulator 0.83.2: five presses 0.3 s apart with the network back at 8 s gave three 503s and two sends at 8.04 s. madalone.2 gave five 400s and no send. A fix belongs in `rm.py` for every command, not here.
- **A remembered code doesn't wait for the network.** W3 (a Broadlink still marked connected) covers codes not yet remembered. A remembered code goes straight to `send_command`. There the send fails with `Errno 101` and BL-7's quick probe fails too, so the press is answered 503 at once, exactly as a raw code is. With the default "Retry commands after wakeup" the UI's resend 0.5 s later succeeds.

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
