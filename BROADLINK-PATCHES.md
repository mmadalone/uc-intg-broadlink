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

**Behaviour change:** a genuine failure now reaches the Remote as an error instead of a silent no-op. Not yet observed on device: whether an activity's start sequence stops or carries on after a failed step.

## BL-5: `repeat` parameter

`get_int_param` / `_get_int_param` used to parse only strings, so an integer `repeat` was silently ignored. They now accept integers too. Repeat was also applied twice: `command_handler` looped `repeat` times around `handle_command`, which loops again for sequences and remote `SEND_CMD`. Now it is applied exactly once per command path. `send_ir` and raw commands loop in `handle_command` via `_send_repeated()`, stopping at the first failure. Not exercised by this household's setup, which never sends `repeat`.

## BL-6: CI

`.github/workflows/build.yml`:
- The Docker/GHCR job is removed; it would publish a multi-arch image under the repository owner.
- The release job needs only `build`.
- The tag must match `driver.json` `version`, or the build fails. Without this check a stale `driver.json` would publish an asset named after the wrong version. Upstream's version line strips a `v` from `GITHUB_REF`, so a manual (dispatch) run fails the check instead of publishing a mislabelled release.
- Upstream's builder image `r2-pyinstaller:3.11.12-0.3.0` is kept.

---

## Verification (offline harness, outside the repo)

The harness runs the **real** driver (`BroadlinkDriver` here, `BaseIntegrationDriver` for upstream), the real `rm.Broadlink` device and the real entity command handlers. It talks to a fake python-broadlink whose network can be down (instant `Errno 101`, as on a wake) or up with the device not answering (blocking until timeout, like the real library). Upstream v1.3.0 runs with ucapi-framework 1.9.6, the fork with 1.9.8.

| Scenario | upstream v1.3.0 (framework 1.9.6) | fork |
|---|---|---|
| S1 wake, Wi-Fi back after 0.3 / 0.6 / 1 / 2 / 3 s | **never reconnects** (all 5) | reconnects 0.05–0.44 s after Wi-Fi returns |
| S2 the press that wakes the Remote (50 ms after `exit_standby`, Wi-Fi back at 0.6 s) | "OK", **nothing sent** | sent; answered after 1.04 s |
| S3 press 2 s after a wake whose first connect failed | "OK", **nothing sent** | sent |
| S4 network down for the whole wake, press at 2 s | "OK", nothing sent | `SERVICE_UNAVAILABLE` after 2.6 s |
| S5 network fine, Broadlink not answering | "OK" instantly, nothing sent; **event loop frozen 10.0 s** | `SERVICE_UNAVAILABLE` after 5.8 s; worst loop stall 7 ms |
| S6 driver starts offline, Remote subscribes before Wi-Fi is back (Discord reporter) | IR emitter never configured; presses → 404 `no configured entity` | configured once Wi-Fi returns; press OK |
| S7 Remote sleeps again during the wake retry | no connect after sleep | no connect after sleep |
| S8 `repeat`: IR emitter int 3 / str "3"; remote int 3 / str "3" | 1 / 3 / 1 / **9** sends | 3 / 3 / 3 / 3 sends |

Upstream's `test_pronto_conversion.py` doesn't run on either tree: it is a development script that fails with `IndentationError` at line 68 on pristine v1.3.0. The fork doesn't touch IR conversion. The isort/flake8 findings are identical to pristine upstream, and black reports no changes.

## Deploying

- **Back up first.** Learned IR codes live in this integration's config. `GET /api/system/backup/export` (administrator account; integrations restart for a few seconds) or the web configurator's backup page. The archive contains secrets, so keep it outside any repo.
- Upload the tarball over the existing Broadlink integration in the web configurator. The same `driver_id` keeps its config (verified with the Kodi fork on 2026-10-20). Check afterwards that the learned-code list (media player source list) is unchanged.
- **Never press Update on the Broadlink card in uc-intg-manager.** It resolves updates against upstream and would install stock v1.3.0 over the fork (the same mechanism as the Kodi fork; see that project's CLAUDE.md).
