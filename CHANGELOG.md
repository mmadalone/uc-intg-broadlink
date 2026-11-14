# Yamaha Integration for Unfolded Circle Remote — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Unreleased

---

## v1.3.0-madalone.3 - 2026-11-14

Fixes the button press that wakes the Remote when the Remote converts its IR code (BL-8). Details and evidence are in [BROADLINK-PATCHES.md](BROADLINK-PATCHES.md).

### Fixed
- **The button press that wakes the Remote works with codes the Remote converts (BL-8).** With a Remote API key set, PRONTO and HEX codes are converted by the Remote. The press that woke it asked for that conversion before the Remote's Wi-Fi was back, got "Network is unreachable", and answered `400`, which the Remote never resends: the TV didn't toggle and the Remote showed "Error sending the command".
  - Now the press waits for the connection to the Broadlink (it keeps trying for up to 3 seconds, as every command has since BL-2) and retries the conversion while the network still isn't up. Commands from an activity sequence, which the Remote never resends, are saved the same way.
  - A conversion request that gets no answer now has a time limit: what is left of those 3 seconds, at least 1 second. Before, it had none, and every later button press waited behind it.
  - Conversion failures are answered with what went wrong: `503` when the network or the Remote isn't ready, `500` for a server error, `401` for a rejected API key, `400` only for a code or an address that can't be used.
  - Converted codes are remembered (up to 64, until the driver restarts), so a repeated press needs no conversion.
- **The IR emitter's messages reach the Remote's log (BL-8).** Its log level was never set, so only its warnings appeared.

### Changed
- The code conversion is told never to wake the Remote with Wake-on-LAN (BL-8). With the current conversion library it never did; this keeps it so.

### Known limitations
- During a long Wi-Fi outage, presses wait for the network one after another, and the ones still waiting when it comes back are sent together, late. Raw and learned codes have behaved this way since BL-2; codes the Remote converts now do too, where they used to fail at once.
- If the Remote's Wi-Fi takes longer than about 2.5 seconds to come back after a wake (seen once, 3.1 s), the press that woke it is answered 503, and the Remote's default 2-second resend window has already closed. Setting "Retry commands after wakeup" on the Remote to 5 seconds lets it resend that press.

### Tested on a Remote 3 before release
- **239 IR sends over three days of normal use, all answered OK**, including wake presses arriving within 0.4 s of the wake.
- **After a driver restart, POWER on a wake from standby** had to be converted by the Remote again: converted and sent in 0.16 s, TV on, no error. A HEX code (the Netflix button) was converted for the first time as well.
- Not seen on the Remote yet: a press that itself wakes the Remote while its Wi-Fi is still down, the case of the two original failures. The offline harness covers it.

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
