# Yamaha Integration for Unfolded Circle Remote — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Unreleased

---

## v1.3.0-madalone.1 - 2026-10-21

madalone fork of upstream v1.3.0. Details and evidence in [BROADLINK-PATCHES.md](BROADLINK-PATCHES.md).

### Fixed
- **Reconnects after the Remote wakes (BL-1).** Upstream made one connect attempt on wake, which often fails because the Remote's Wi-Fi isn't up yet, and then never retried. The Broadlink stayed unavailable until the Remote slept and woke again (22% of wakes in one household, 31% of IR presses dropped). The driver now retries quickly, then backs off, for as long as the Remote is awake. The same retry runs at driver start and on the Remote's connect command.
- **A button press reconnects instead of being dropped (BL-2).** When the device is disconnected, a command keeps trying to reconnect for up to 3 seconds before giving up. The press that wakes the Remote is no longer lost.
- **The driver no longer freezes on an unresponsive Broadlink (BL-2).** Blocking Broadlink calls run off the event loop, one at a time per device, with shorter timeouts.
- **Lost entity subscriptions after a driver restart (BL-3).** ucapi-framework 1.9.6 → 1.9.8.
- **Failed IR sends are reported as failures instead of "OK" (BL-4).**
- **`repeat` given as an integer is honoured, and repeat is applied once (BL-5).**

### Changed
- CI: no Docker image is published; the release tag must match `driver.json` (BL-6).

---

## v1.2.2 - 2026-03-10

Under the hood changes to simplify future development

---


## v0.1.0 - 2025-01-22

### Added
- First release. Control broadlink devices on your local network from your Unfolded Circle Remote.
