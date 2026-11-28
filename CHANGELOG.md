# Yamaha Integration for Unfolded Circle Remote — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## v1.3.0-madalone.6 - 2026-11-28

A shortcut to the integration's settings (BL-11). Details and evidence are in [BROADLINK-PATCHES.md](BROADLINK-PATCHES.md).

### Added
- **"Configure selected device"** on the setup wizard's first page ("Configuration mode"; web configurator: Integrations → Broadlink → Start setup → Start integration setup). It opens one page with the Broadlink's settings, today "Wait for Wi-Fi after a wake", and Next saves it at once: no search for the Broadlink or the Remote, no PIN. The Broadlink uses the new value straight away, without a restart. UC's own Denon, Android TV and Apple TV integrations have the same action.

### Changed
- With a Broadlink configured, the first page now opens on "Configure selected device" instead of "Add a new device", as in UC's integrations. Its settings page saves nothing until you press its Next, and Cancel saves nothing.
- "Configure selected device" changes settings only. "Update information for selected device" is still the way to find the Broadlink again (a new IP address) or to give the Remote's PIN again; the wait setting is on its last page too.

### Known limitations
- A saved wait between whole seconds (from a hand-edited or restored configuration, e.g. 2.5) is shown as the nearest whole second, and Next saves that, as on Update's last page since BL-9.

### Tested before release
- **Test build madalone.6 (firmware 2.13.1 beta), 2026-11-27/28:** the first page opened on "Configure selected device"; Next saved 3 seconds and closed the setup 1.5 seconds later, with no search and no PIN; opened again, the page showed 3 seconds, and Cancel saved nothing. After the next wake, the first press (0.1 s after the wake) reconnected and was sent, and the presses after it too: 5 presses, all answered OK. No warning in the integration's log.
- **Every setup path** (Configure, Add, Update, Remove, Reset, Backup and Restore) was accepted by the Remote's core of firmware 2.12.1 and of the 2.13.0 and 2.13.1 betas (UC's core simulator), with button presses working before and right after Configure.

---

## v1.3.0-madalone.5 - 2026-11-27

Button presses made while the Remote's Wi-Fi is down are no longer sent late, and a new setting controls how long the press that wakes the Remote may wait for its Wi-Fi (BL-9). madalone.4 was a test build and was never released. Details and evidence are in [BROADLINK-PATCHES.md](BROADLINK-PATCHES.md). "Configure" below is the integration's setup wizard: in the web configurator, Integrations → Broadlink → Start setup → Start integration setup, then the action (Update, Backup, Restore).

### Fixed
- **Presses made while the Remote's Wi-Fi is down are no longer sent late (BL-9).** The integration handles one press at a time. During a Wi-Fi outage each press waited for the network in turn, and the ones still waiting when it came back were all sent at once, seconds late; a late burst of power toggles can leave a TV off. Now a press waits for the Wi-Fi only in a short window right after the Remote wakes. At any other time it gets its error at once and is never sent later.
- **A slow wake no longer loses the press that woke the Remote (BL-9).** That press now waits up to 4 seconds from the wake for the Remote's Wi-Fi; before, the last try was at about 2.5 seconds. On 2026-11-14 the Wi-Fi took 3.1 seconds to come back.
- **Updating the Broadlink in the integration's settings no longer erases the IR codes learned in the integration (BL-9).** Configure → Update saved the device with an empty code list.
- **Configure → Update works after a cancelled setup (BL-9).** After a setup was cancelled or left to expire, the next Configure went back to the unfinished "Remote API access" page, whichever action was chosen. An Add after an Update that ended without being cancelled (a lost connection, say) could also delete the Broadlink being updated.
- **A Broadlink that can't be reached when it is added, updated or restored keeps being retried while the Remote is awake (BL-9),** instead of only at the next wake: at first every half second, later up to once a minute.
- **The Broadlink is listed once on "Discovered Devices" (BL-9).** Every search for devices added it to the list again until the integration restarted, and the setup used the oldest entry, so after the Broadlink's IP address changed an Update could save the old address.
- **Configure → Restore applies at once (BL-9).** The restored settings reached the running Broadlink only after the integration restarted, and if the Broadlink's IP address changed before that, the old settings, learned codes included, were written back over the restored ones.
- **The Remote PIN and the Remote API key no longer appear in the integration's log (BL-9).** The PIN was written there in plain text whenever it was typed on the setup page, and the API key, which gives admin access to the Remote, whenever Configure → Backup or Restore was used.
- **A command that fails and can't reconnect is answered "unavailable" again (BL-9).** In one rare case the integration resent it through a connection that never finished setting up and answered with a server error.

### Added
- **A setting** on the integration's "Remote API access" setup page (web configurator: Integrations → Broadlink → Configure → Update): "Wait for Wi-Fi after a wake", picked from a list: 0 to 6 seconds, default 4.

### Changed
- On Update, the Remote PIN can be left empty to keep the current access to the Remote's API, unless you pick another Remote. Adding a Broadlink still needs the PIN. If a request for new access failed partway, the PIN is asked for again until new access is made.
- Configure → Restore reconnects the Broadlink, as Update does.

### Known limitations
- Presses made right after the press that woke the Remote, while its Wi-Fi is still coming back, are sent one after another once it connects, as before: the first up to the wake wait (4 seconds by default) plus about 1.5 seconds after the wake, each one after it a tenth to a quarter of a second later. A burst of presses is replayed in full, and POWER pressed twice there toggles twice.
- About one wake in 80, the press that wakes the Remote reaches the integration just before the Remote's wake event. With the Wi-Fi still down, it gets an error at once and is never sent late; the Remote's "Retry commands after wakeup", if on, resends it.
- With several Broadlinks on one Remote, setting up access for one removes the others' access (one key name, as upstream): enter the PIN on each Update.
- With "Wait for Wi-Fi after a wake" at 5 or 6 seconds, a wake press that connects late and whose send then gets no answer can be resent after the Remote has given up on it, and the presses made behind it too.
- If the Remote goes back to standby within seconds of a wake, while the press that woke it is still waiting for the Wi-Fi, and then wakes again, the presses made behind that press can wait twice and, at 5 or 6 seconds, be sent after the Remote has given up on them.
- After adding a Broadlink that couldn't be reached at that moment, its IR emitter may stay unavailable until the integration restarts.
- A button pressed during the second in which a Restore is being applied gets an error and is not sent.
- If the Wi-Fi takes longer than the wake wait (4 seconds by default), the press that woke the Remote, and the presses made behind it, fail with an error. A longer "Wait for Wi-Fi after a wake" moves this edge, and so does the Remote's own "Retry commands after wakeup" set to 5 seconds or more, which resends the press after its error.

### Tested before release
- **Three days of normal use on the previous test build** (firmware 2.12.1), whose wake handling is the same: 54 wakes and 1,052 commands, all answered OK. 9 presses found the Broadlink still disconnected just after a wake; each reconnected within the wake wait and was sent.
- **A day of normal use on this build** (2026-11-26/27, firmware 2.12.1): 26 wakes and 77 commands, all answered OK. Two presses found the Broadlink disconnected just after a wake; each reconnected at once and was sent. After the Remote updated itself to the 2.13.1 beta, the integration started and connected normally.
- **The setup page on this build:** two Updates with the new list. The page appeared, 5 seconds was saved with the Remote PIN left empty, the Broadlink was listed once, and presses afterwards were sent.
- **Not run on the Remote: a Wi-Fi outage while the Remote is awake, and an unplugged Broadlink.** Both were run with this release's code (the test build installed on the Remote) against a simulated network and Broadlink. In the simulation, during a 15-second outage every press got an error at once and none was sent late. With the Broadlink unplugged, the first press got its error within about 5 seconds and the next after about 1 second, and none was sent after it was plugged back in. Presses afterwards worked in both.
- **The setup pages** were accepted by the Remote's core of firmware 2.12.1 and of the 2.13.0 and 2.13.1 betas (UC's core simulator), along every setup path: Add, Update, Remove, Reset, Backup and Restore.

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
