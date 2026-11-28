# CLAUDE.md -- Broadlink Integration for UC Remote 3 (madalone fork)

DON'T BE SYCOPHANTIC

## Project identity

Fork of [JackJPowell/uc-intg-broadlink](https://github.com/JackJPowell/uc-intg-broadlink) for the **Unfolded Circle Remote 3**. Python async integration driver on `ucapi` 0.7.0 + `ucapi-framework` + `python-broadlink` 0.19.0.

- **Fork repo:** `mmadalone/uc-intg-broadlink` (public GitHub fork). Local clone: `C:\Users\mique\_Claude Projects\uc-integration-broadlink`. Remotes: `origin` = fork, `upstream` = JackJPowell.
- **Base:** upstream tag `v1.3.0` (`06d8e6d`, 2026-08-26). Branch `v1.3.0-patched`. Fork version `1.3.0-madalone.6` in `driver.json` (BL-11, released `v1.3.0-madalone.6` on 2026-11-28; before it `v1.3.0-madalone.5`, BL-9, 2026-11-27; before it `v1.3.0-madalone.3`, BL-8, 2026-11-14; madalone.4 was a test build only); `pyproject.toml` uses the PEP 440 form `1.3.0+madalone.6`. Installed on the Remote: the BL-11 test build `1.3.0-madalone.6` since 2026-11-27 23:03 UTC (Configure tested OK); before it the BL-9 test build `1.3.0-madalone.5` since 2026-11-26 18:42 UTC (its Update test passed; the outage and unplug tests were simulated in Docker, `tests/device_sim/`, not run on the Remote); before it the test build `1.3.0-madalone.4` since 2026-11-16 18:18 UTC, whose setup page the core refused; before that a `1.3.0-madalone.3` test build since 2026-11-11 20:22 UTC (owner's manual upload; `1.3.0-madalone.2` before, since 2026-10-21 12:51 UTC). `GET /api/intg/drivers` (the list) kept showing the old version after the upload; `GET /api/intg/drivers/broadlink_driver` and the driver's `get_driver_version` showed the new one. First fork release: `v1.3.0-madalone.2`, 2026-10-22.
- **Devices:** UC Remote 3 at `192.168.2.204`. **The web-configurator PIN is deliberately not recorded here: this file is tracked and public.** Broadlink RM4 mini at `192.168.2.124`.
- **Sibling fork with the same history and conventions:** `C:\Users\mique\_Claude Projects\integration-kodi-patch` (Kodi). Its CLAUDE.md documents uc-intg-manager, log retrieval and the deploy flow in depth.

## Patches

`BROADLINK-PATCHES.md` is the source of truth: root causes, BL-1…BL-9, verification, deploy notes. Summary:

| ID | What |
|----|------|
| BL-1 | `BroadlinkDriver` (`intg-broadlink/broadlink_driver.py`) keeps reconnecting after wake / connect command / driver start: 10 × 0.5 s, then 5 → 10 → 30 → 60 s while awake |
| BL-2 | `rm.py`: locked `connect()`; reconnect on demand for up to 3 s (BL-9: only inside the wake window); every python-broadlink call off the event loop under a per-device lock; device timeout 4 s; discovery timeout 3 s |
| BL-3 | ucapi-framework 1.9.6 → 1.9.8 (lost-subscription fix) |
| BL-4 | Command handlers return the real result instead of always OK |
| BL-5 | `repeat` accepts int and str, applied once |
| BL-6 | CI: no GHCR docker job; tag must match `driver.json`; ARM emulation via `docker/setup-qemu-action` (no apt, no `multiarch/qemu-user-static`) |
| BL-7 | Fail fast when the Broadlink stops answering. A quick 1 s probe instead of a ~6 s reconnect; no retry within 2 s of a failed attempt, so queued presses can't fire late; background reconnect while awake (5 → 10 → 30 → 60 s), requested by the device when a command gives up; BL-2's 3 s window is a real bound (BL-9: now the wake window). The normal send path and its 4 s timeout are unchanged |
| BL-8 | `send_ir` with a code the Remote converts (PRONTO/HEX, Remote API key set): wait for the Broadlink connection (`ensure_connected_for_command`, BL-2/BL-7) before converting, retry a conversion only while the network is down (ENETUNREACH & co., EAI_AGAIN for a host-name address) or the API answers 503, within the 3 s window (BL-9: the wake window); a time limit on every attempt (unfurled 0.4.1's conversion request has none); answer failures honestly (503 network/not ready, 500 server error, 401 API key, 400 bad code; was always 400, which the Remote never resends), remember up to 64 converted codes, `Remote(..., wake_if_asleep=False)`. Harness `tests/bl8_harness.py` (in the repo) |
| BL-9 | Presses wait for the Remote's Wi-Fi only inside the wake window (started by the first wake event after `enter_standby`, `exit_standby` or the core's connect, and when a device is created; `enter_standby` ends it; a later wake event in the same wake starts none, unless 60 s after the window's start, the time asleep included (`WAKE_EVENT_LAPSE` on `CLOCK_BOOTTIME`, for a driver that never saw the `enter_standby`); counted from that moment, never per press, so a queued press gets no second wait, except behind a standby bounce (Known limits); setting "Wait for Wi-Fi after a wake", `wake_wait_sec`, default 4 s, a list of whole seconds 0–6 on the setup page, never a number field: the core takes a number field's `steps` as a whole number only and refuses the page otherwise). Outside it: one 1.4 s hello, then 503 at once, never sent later. Plain ucapi listeners start the window before the next queued request; a press waits for the driver's running attempt until the window ends; its own attempts are hellos without discovery, the last one at the window's end (the wake press can be sent up to ~1.5 s after it, and the presses queued behind it follow, ~0.12–0.22 s apart); a send whose first packet failed with network down within 1 s waits in the window, and is resent only if the reconnect worked (else BL-4's 503). The drop wait of the first build was removed after two reviews (ucapi gives no request time, so queued presses could go out late). Setup: the setting, PIN optional on Update (after a key request that may have deleted the key, required until one succeeds; a driver restart forgets it), Update keeps the learned codes (it wiped them), an unfinished setup no longer misroutes the next one, and an Add after an Update that ended without an abort no longer deletes the Update's Broadlink; the API key is `repr=False`, the PIN field is `password`. `log_redact.py` (installed by `driver.py`) masks `remote_api_key` and `password` values in every log line (ucapi logs Backup/Restore in full at DEBUG). A Restore re-creates the device (`on_device_added`), `remove_device` cancels the device's retry task, and a device created while awake gets a retry (`setup_device_event_handlers`). A press before `exit_standby` gets no window (round 3's standby rule was dropped after review). Tests `tests/bl9_scenarios.py` (router level, 203 runs, `--quick` 50, one port per run), `tests/bl9_window_checks.py` (18) and `tests/bl9_setup_test.py` (109; its page check follows the core's model) and `tests/bl9_setup_e2e.py` (the real driver through core-simulator; run it for every new or changed setup page and for each new firmware). `discover()` empties its list, so the Broadlink is listed once |
| BL-11 | `setup.py`: "Configure selected device" on the wizard's first page (selected when a Broadlink is configured): one page with the wait setting, saved in place on the saved configuration and on the running device's own object (a copy after a re-subscribe), no search, no PIN. Overrides the framework's private `_build_configuration_mode_screen` and routes in `handle_driver_setup` (no hook exists); X8 fails if the framework's first page changes; `tests/bl9_setup_test.py` has 119 checks with case X. BL-10 (the router crash on `send_cmd` without `:`) is still to do |

## Build (Windows Git Bash)

```bash
cd "C:/Users/mique/_Claude Projects/uc-integration-broadlink"
rm -rf build dist artifacts
MSYS_NO_PATHCONV=1 docker run --rm --platform=linux/arm64 -v "$(pwd)":/workspace \
  docker.io/unfoldedcircle/r2-pyinstaller:3.11.12-0.3.0 \
  bash -c "cd /workspace && python -m pip install -r requirements.txt && pyinstaller --clean --onedir --name intg-broadlink_driver --collect-all zeroconf intg-broadlink/driver.py"
mkdir -p artifacts/bin && mv dist/intg-broadlink_driver/* artifacts/bin/ && mv artifacts/bin/intg-broadlink_driver artifacts/bin/driver && cp driver.json artifacts/
cd artifacts && tar czf ../uc-intg-broadlink_driver-<VERSION>-aarch64.tar.gz . && cd ..
```

These mirror `.github/workflows/build.yml`.
- `MSYS_NO_PATHCONV=1` is required on Git Bash.
- **`--platform=linux/arm64` is required.** The `3.11.12-0.3.0` tag is a manifest list whose only image is arm64. On this amd64 host Docker refuses to choose one (`no matching manifest for linux/amd64`) unless told to; Docker Desktop then emulates arm64. The Kodi fork's `3.11.13-0.4.0` tag is a plain arm64 image, so Docker runs it under emulation without the flag.
- Run `tar` from inside the folder with relative paths: a `C:/…` path makes tar treat the colon as `host:path`.
- Tarball layout: `./driver.json`, `./bin/driver`, `./bin/_internal/`.

**Release:** bump `driver.json` (and `pyproject.toml`), commit, `git tag -a v<VERSION>`, push branch + tag (only on the user's go-ahead). CI builds the GitHub Release and fails if the tag and `driver.json` disagree.

## Deploy

1. **Backup first:** the learned IR codes live in this integration's config. `GET http://192.168.2.204/api/system/backup/export` (admin; integrations restart for a few seconds), or the web configurator's backup page. Keep the archive outside any repo; it contains secrets.
2. Upload the tarball over the existing integration in the web configurator. The same `driver_id` keeps the config. Then check that the learned-code list is unchanged.
3. **Never press Update on the Broadlink card in uc-intg-manager.** It would install stock upstream over the fork. Keep the manager's `auto_update` off.

## Logs and diagnosis

- Driver log: `GET http://192.168.2.204/api/system/logs?s=custom-intg-broadlink_driver&p=7&limit=20000` (basic auth `web-configurator:<PIN>`).
  - **`p=7` is required**; without it the body is empty.
  - Never pass `?q=`; it hangs the server.
- Core log, for a setup page that never appears ("Setting up…" in the web configurator): `GET …/api/system/logs?s=core&p=7&from=<ISO time>&to=<ISO time>`. An accepted page is `Ok(DriverSetupChange { … })`, a refused one `Err(Error("invalid type: …"))`. Both logs come newest first, and the driver's lines are stamped in batches: reverse, then sort by time.
- `tests/bl9_setup_e2e.py` needs the images `unfoldedcircle/core-simulator:<tag>-bt` and a Python 3.11 image with `requirements.txt` and `test-requirements.txt` installed (`FROM python:3.11-slim`, `COPY` both, `pip install -r` both); its docstring has the commands.
  - At most 10,000 lines per request.
- Markers:
  - `Exit standby event: connecting device(s) with retry` (BL-1 active)
  - `Connect attempt N failed, retrying in …`
  - `Connected after N attempts`
  - `Device not connected, reconnecting before sending` (BL-2)
  - `Device did not answer recently, probing once before sending` (BL-7 quick probe)
  - `Reconnected after N background attempts` (BL-7 background retry)
  - `Cannot send command: device not connected` (gave up)
  - `IR code conversion failed (…), retrying` (INFO) / `Cannot convert the IR code, answering <status>: …` (WARNING) (BL-8; the `ir_emitter` logger is raised to `UC_LOG_LEVEL` in `driver.py` since BL-8, before that none of its INFO lines reached the log)
  - A BL-8 wake press that fails usually fails before any conversion: `Cannot send command: device not connected` from `rm`, answered 503
  - BL-9: `Wait for the remote's Wi-Fi: up to Xs after a wake` (device created: driver start, Add, Update, Restore); `Device not connected, reconnecting before sending (x.xs after the wake)` (inside the wake window) / `… trying once before sending (…)` (outside); `Gave up waiting for a connect attempt (…)`; `Cannot send command: device not connected (x.xs after the wake)`; `No Remote PIN entered: keeping the saved Remote API key for <address>` (setup); `<event>: the wake window has already started for this wake`; `Re-creating the device from its new configuration` (Restore)
- Upstream-era baseline (2026-10-17..20): 28 of 128 wakes failed with `[Errno 101] Network is unreachable` and never recovered within the wake.
- **ucapi 0.7.0 handles each connection's requests one at a time.** `_ws_router` awaits each message before reading the next. A command handler that waits on the device delays every later press, and the standby events too. Keep handler waits short. Harness presses must be awaited in order to model this.

## Code conventions

- Formatting matches upstream: **black** (default 88 columns), **isort** (`profile=black`), **flake8** (`max-line-length = 120`, `setup.cfg`). Tools are in `test-requirements.txt`.
- Pre-existing upstream findings: isort flags `driver.py`, `rm.py` and `remote.py` import order, and flake8 flags `rm.py` D200. Don't "fix" them; it only adds merge noise. Check that a change introduces nothing new by running the same tools on `git archive v1.3.0`.
- Mark fork changes in code comments with their patch ID (`# BL-2: …`). Keep upstream headers intact. New fork files carry `:copyright: (c) 2026 by madalone.`
- Every python-broadlink call must go through `Broadlink._run_io()`. The library is blocking, and its own device lock never engages.

## Upstream sync

```bash
git fetch upstream --tags
git log --oneline v1.3.0..upstream/main   # what upstream has added
```
For a new upstream release: create `vX.Y.Z-patched` from the upstream tag and re-apply BL-1…BL-9. Never rebase a published branch in place. Re-check whether upstream has fixed any of the root causes before re-applying.

## Validation

Done on the device with madalone.1 (2026-10-21; details in `BROADLINK-PATCHES.md`):
- B3: the entity records were byte-identical after the upload. The integration holds no learned codes here; the IR codes live in the Remote.
- B4: with the Broadlink unplugged, the first press got an error after 10.07 s and later presses after 6.0 s. An activity started meanwhile still ran its other steps. After replugging, the next press worked in 0.31 s. This measurement led to BL-7.

- B1 (madalone.1): a Broadlink button woke the Remote; the press was sent 125 ms after the wake.
- B5 (madalone.2): unplugged, the first error came after 5.05 s and later ones within ~1 s or at once. After replugging, the Broadlink came back without a press (`Reconnected after 4 background attempts`), and nothing failed was sent late.

- B2 (madalone.2, a day of use): 31 wakes all connected (worst 1.26 s). 5 hit the original Wi-Fi-not-up-yet failure and recovered in ~0.5 s. 2 Broadlink-button wakes were sent OK. No errors outside the unplug test.

- madalone.3 (BL-8, 2026-11-11..14; details in `BROADLINK-PATCHES.md`):
  - B2-like: 239 IR sends, all 200.
  - After a driver restart, POWER on a wake from standby was converted by the Remote and sent in 0.16 s.
  - A HEX code was converted for the first time, also OK.
  - Not re-run: B5, which BL-8 doesn't touch (harness W4), and a press that itself wakes the Remote before Wi-Fi is back. The motion sensor (sensitivity 3) wakes the Remote when it is picked up, so that case comes only from natural use: watch the log.

- madalone.5 (BL-9, 2026-11-26..27; details in `BROADLINK-PATCHES.md`): two Updates with the new list passed, presses afterwards 200; a day of use, 26 wakes and 77 commands, all 200; started cleanly on firmware 2.13.1 (beta), installed 2026-11-27. B5 (unplug) and the outage were simulated with the package (`tests/device_sim/`, 12/12), not run on the Remote.

For the next release, re-run B1, B2 and B5. Score a wake only where the log shows `enter_standby`/`exit_standby`: a Remote left untouched doesn't always sleep, for example when docked.
