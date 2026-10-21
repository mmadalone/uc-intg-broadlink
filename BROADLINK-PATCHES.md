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

Upstream's `test_pronto_conversion.py` doesn't run on either tree: it is a development script that fails with `IndentationError` at line 68 on pristine v1.3.0. The fork doesn't touch IR conversion. The isort/flake8 findings are identical to pristine upstream, and black reports no changes.

## Deploying

- **Back up first.** Learned IR codes live in this integration's config. `GET /api/system/backup/export` (administrator account; integrations restart for a few seconds) or the web configurator's backup page. The archive contains secrets, so keep it outside any repo.
- Upload the tarball over the existing Broadlink integration in the web configurator. The same `driver_id` keeps its config (verified with the Kodi fork on 2026-10-20). Check afterwards that the learned-code list (media player source list) is unchanged.
- **Never press Update on the Broadlink card in uc-intg-manager.** It resolves updates against upstream and would install stock v1.3.0 over the fork (the same mechanism as the Kodi fork; see that project's CLAUDE.md).
