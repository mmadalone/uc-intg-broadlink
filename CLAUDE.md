# CLAUDE.md -- Broadlink Integration for UC Remote 3 (madalone fork)

DON'T BE SYCOPHANTIC

## Project identity

Fork of [JackJPowell/uc-intg-broadlink](https://github.com/JackJPowell/uc-intg-broadlink) for the **Unfolded Circle Remote 3**. Python async integration driver on `ucapi` 0.7.0 + `ucapi-framework` + `python-broadlink` 0.19.0.

- **Fork repo:** `mmadalone/uc-intg-broadlink` (public GitHub fork). Local clone: `C:\Users\mique\_Claude Projects\uc-integration-broadlink`. Remotes: `origin` = fork, `upstream` = JackJPowell.
- **Base:** upstream tag `v1.3.0` (`06d8e6d`, 2026-08-26). Branch `v1.3.0-patched`. Fork version `1.3.0-madalone.1` in `driver.json`; `pyproject.toml` uses the PEP 440 form `1.3.0+madalone.1`.
- **Devices:** UC Remote 3 at `192.168.2.204`. **The web-configurator PIN is deliberately not recorded here: this file is tracked and public.** Broadlink RM4 mini at `192.168.2.124`.
- **Sibling fork with the same history and conventions:** `C:\Users\mique\_Claude Projects\integration-kodi-patch` (Kodi). Its CLAUDE.md documents uc-intg-manager, log retrieval and the deploy flow in depth.

## Patches

`BROADLINK-PATCHES.md` is the source of truth: root causes, BL-1…BL-6, verification, deploy notes. Summary:

| ID | What |
|----|------|
| BL-1 | `BroadlinkDriver` (`intg-broadlink/broadlink_driver.py`) keeps reconnecting after wake / connect command / driver start: 10 × 0.5 s, then 5 → 10 → 30 → 60 s while awake |
| BL-2 | `rm.py`: locked `connect()`; reconnect on demand for up to 3 s; every python-broadlink call off the event loop under a per-device lock; device timeout 4 s; discovery timeout 3 s |
| BL-3 | ucapi-framework 1.9.6 → 1.9.8 (lost-subscription fix) |
| BL-4 | Command handlers return the real result instead of always OK |
| BL-5 | `repeat` accepts int and str, applied once |
| BL-6 | CI: no GHCR docker job; tag must match `driver.json` |

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
  - At most 10,000 lines per request.
- Markers:
  - `Exit standby event: connecting device(s) with retry` (BL-1 active)
  - `Connect attempt N failed, retrying in …`
  - `Connected after N attempts`
  - `Device not connected, reconnecting before sending` (BL-2)
  - `Cannot send command: device not connected` (gave up after the reconnect window)
- Upstream-era baseline (2026-10-17..20): 28 of 128 wakes failed with `[Errno 101] Network is unreachable` and never recovered within the wake.

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
For a new upstream release: create `vX.Y.Z-patched` from the upstream tag and re-apply BL-1…BL-6. Never rebase a published branch in place. Re-check whether upstream has fixed any of the root causes before re-applying.

## Validation pending (on device)

- B1: wake the remote **with a button press** on an activity that uses the Broadlink. The press should act, and the log should show either `Connected after N attempts` or a reconnect before sending.
- B2: a dozen ordinary wakes. No `Cannot send command`.
- B3: the learned-code list is identical after installing.
- B4: with the Broadlink unplugged, a press returns an error within ~6 s. Note whether an activity's start sequence stops or continues.
