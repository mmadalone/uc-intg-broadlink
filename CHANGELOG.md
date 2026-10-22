# Yamaha Integration for Unfolded Circle Remote — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Unreleased

---

## v1.3.0-madalone.2 - 2026-10-22

First release of the madalone fork of upstream v1.3.0. It includes madalone.1, which was tested on the Remote but not released on its own. Details and evidence are in [BROADLINK-PATCHES.md](BROADLINK-PATCHES.md).

### Fixed
- **Reconnects after the Remote wakes (BL-1).** Upstream made one connect attempt on wake, which often fails because the Remote's Wi-Fi isn't up yet, and then never retried. The Broadlink stayed unavailable until the Remote slept and woke again (22% of wakes in one household, 31% of IR presses dropped). The driver now retries quickly, then backs off, for as long as the Remote is awake. The same retry runs at driver start and on the Remote's connect command.
- **A button press reconnects instead of being dropped (BL-2).** When the device is disconnected, a command keeps trying to reconnect for up to 3 seconds before giving up. The press that wakes the Remote is no longer lost.
- **The driver no longer freezes on an unresponsive Broadlink (BL-2).** Blocking Broadlink calls run off the event loop, one at a time per device, with shorter timeouts.
- **Lost entity subscriptions after a driver restart (BL-3).** ucapi-framework 1.9.6 → 1.9.8.
- **Failed IR sends are reported as failures instead of "OK" (BL-4).**
- **`repeat` given as an integer is honoured, and repeat is applied once (BL-5).**
- **Errors come back in seconds when the Broadlink can't be reached (BL-7).** With the Broadlink unplugged, the first press used to report its error after 10 seconds and every later press after 6. Repeated presses also waited behind each other: the fifth was answered after 34 seconds. If the Broadlink came back while presses were waiting, they were all sent at once, late.
  - Now the first error comes after about 5 seconds and later ones within a second.
  - Presses that queued behind a failure are rejected instead of being sent late.
  - While the Remote is awake, the driver keeps reconnecting in the background, so the Broadlink is usable again without a button press.
  - Sending to a Broadlink that answers is unchanged.
- **The press that wakes the Remote waits at most its 3-second reconnect window (BL-7).** Before, it could wait ~6 seconds for a connect attempt against a Broadlink that wasn't answering.

### Changed
- CI: no Docker image is published; the release tag must match `driver.json`; ARM emulation is set up with Docker's own action instead of being installed from Ubuntu's package mirrors, which hung the first release build (BL-6).

### Tested on a Remote 3 before release
- **31 wakes in a day, all reconnected** (worst 1.26 s). On 5 of them the first attempt failed because the Remote's Wi-Fi was still coming up. Upstream never recovers from that within the wake; here each one reconnected within about half a second.
- **Two wakes triggered by a Broadlink button:** both presses were sent, in 0.02 s and 0.08 s.
- **Broadlink unplugged:**
  - The first error came after 5 s and later ones within a second.
  - Nothing was sent late.
  - After it was plugged back in, it reconnected by itself.

---

## v1.2.2 - 2026-03-10

Under the hood changes to simplify future development

---


## v0.1.0 - 2025-01-22

### Added
- First release. Control broadlink devices on your local network from your Unfolded Circle Remote.
