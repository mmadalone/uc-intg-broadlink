"""
Broadlink integration driver that keeps reconnecting while the remote is awake.

Fork patch BL-1. The remote sends exit_standby before its Wi-Fi is usable, so the
single connect attempt ucapi_framework makes on wake often fails with
"[Errno 101] Network is unreachable" (28 of 128 wakes on 2026-10-17..20). Nothing
retried, and the Broadlink stayed unavailable until the remote slept and woke again.

Fork patch BL-7. A Broadlink that a command finds unreachable while the remote is awake
(unplugged, off Wi-Fi) is reconnected in the background, so button presses can fail
fast instead of each one making a slow connect attempt.

Fork patch BL-9. Waking up (or connecting) starts each device's wake window, the few
seconds in which a press may wait for the remote's Wi-Fi, as soon as ucapi handles the
event; only the first such event of a wake does. A Restore re-creates the device, as an
Update does, and a device created while the remote is awake keeps retrying until it
connects.

:copyright: (c) 2026 by madalone.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import time

import ucapi
from ucapi_framework import BaseIntegrationDriver

_LOG = logging.getLogger(__name__)

# Fast retries cover the remote's Wi-Fi coming back right after a wake, the slow ones
# a longer outage. Retries continue for as long as the remote is awake.
FAST_RETRY_INTERVAL = 0.5
FAST_RETRY_ATTEMPTS = 10
SLOW_RETRY_DELAYS = (5.0, 10.0, 30.0, 60.0)
# BL-9: a wake event within this long of the wake window's start, with no enter_standby
# between, belongs to the same wake and starts no second window. Longer than any press
# can hold ucapi's router (about 25 s at the 6 s setting with every timeout hit: the
# last hello, the auth, a conversion, the send, a probe and the resend), so an event
# queued behind such a press still counts. A later one starts its own window, so a wake
# whose enter_standby the driver never saw (a driver started or reconnected while the
# remote slept) still gets one. The time the remote slept counts (_boot_time()).
WAKE_EVENT_LAPSE = 60.0

# BL-9: the clock WAKE_EVENT_LAPSE is counted on, chosen once: Linux's CLOCK_BOOTTIME,
# which keeps running while the remote is suspended in standby. The event loop's clock
# (CLOCK_MONOTONIC) stops then, so a wake hours later could count as the same wake.
# Where CLOCK_BOOTTIME doesn't exist or can't be read (development machines), the
# monotonic clock.
try:
    _BOOTTIME: int | None = time.CLOCK_BOOTTIME
    time.clock_gettime(_BOOTTIME)
except (AttributeError, OSError):
    _BOOTTIME = None


def _boot_time() -> float:
    """Return seconds on the clock WAKE_EVENT_LAPSE is counted on (BL-9)."""
    if _BOOTTIME is None:
        return time.monotonic()
    return time.clock_gettime(_BOOTTIME)


def _retry_delay(attempt: int) -> float:
    """Return the delay after the given (1-based) failed attempt."""
    if attempt < FAST_RETRY_ATTEMPTS:
        return FAST_RETRY_INTERVAL
    slow = min(attempt - FAST_RETRY_ATTEMPTS, len(SLOW_RETRY_DELAYS) - 1)
    return SLOW_RETRY_DELAYS[slow]


class BroadlinkDriver(BaseIntegrationDriver):
    """BaseIntegrationDriver that retries device connections until they succeed."""

    def __init__(self, *args, **kwargs) -> None:
        """Create the driver; arguments are passed to BaseIntegrationDriver."""
        super().__init__(*args, **kwargs)
        self._connect_tasks: dict[str, asyncio.Task] = {}
        # BL-9: the device instance each running connect task is for, so a device
        # re-created with the same id doesn't wait for the old instance's task.
        self._connect_task_device: dict[str, object] = {}
        # BL-7: background reconnects only run while the remote is awake.
        self._awake = True
        # BL-9: _boot_time() at which a wake event (exit_standby or the core's connect)
        # started the wake window, since the last enter_standby or the driver's start
        # (None: none).
        # Only the first one of a wake does: one queued behind a waiting press would give
        # the presses queued behind it a second wait. See WAKE_EVENT_LAPSE.
        self._wake_window_started_at: float | None = None
        # BL-9: enter_standby seen and no wake event since: a device created now starts
        # with its window ended, like the others.
        self._in_standby = False
        # BL-9: ucapi calls a plain function as it handles the event, before the next
        # request. The async on_r2_* handlers run later, as tasks, and the press that
        # woke the remote is often queued right behind exit_standby.
        self.api.add_listener(ucapi.Events.ENTER_STANDBY, self._went_to_standby)
        self.api.add_listener(ucapi.Events.EXIT_STANDBY, self._woke_up)
        self.api.add_listener(ucapi.Events.CONNECT, self._start_wake_window)

    async def on_r2_connect_cmd(self) -> None:
        """Handle the Remote connect command: connect every device, with retry."""
        _LOG.debug("Client connect command: connecting device(s) with retry")
        self._awake = True
        await self.api.set_device_state(ucapi.DeviceStates.CONNECTED)
        self.connect_all_with_retry()

    async def on_r2_exit_standby(self) -> None:
        """Handle the Remote waking up: connect every device, with retry."""
        _LOG.debug("Exit standby event: connecting device(s) with retry")
        self._awake = True  # BL-9: unanswered attempts are forgotten in _woke_up()
        self.connect_all_with_retry()

    async def on_r2_enter_standby(self) -> None:
        """Handle the Remote going to sleep: stop retrying, then disconnect."""
        self._awake = False
        self._cancel_connect_tasks()
        self._forget_unanswered()
        await super().on_r2_enter_standby()

    def reconnect_in_background(self, device_id: str) -> None:
        """Keep reconnecting a device that a command found unreachable (BL-7).

        The device calls this when a command fails to reach it. Without it, a Broadlink
        lost while the remote is awake stayed disconnected until the next button press
        or wake. A retry that is already running (after a wake, say) is left alone.

        Deliberately not hooked to the framework's connection-error event: that event
        also fires for the first connect at driver start, before connect_all_with_retry()
        runs, and a slow background retry would then take the place of the fast one.
        """
        if not self._awake:
            return
        device = self._device_instances.get(device_id)
        if device is not None:
            self._start_connect_task(device_id, device, background=True)

    def connect_all_with_retry(self) -> None:
        """Start a retrying connect for every device that is not connected yet."""
        for device_id, device in self._device_instances.items():
            self._start_connect_task(device_id, device)

    def _start_connect_task(
        self, device_id: str, device, background: bool = False
    ) -> None:
        """Start a retrying connect for one device, unless one is already running.

        BL-9: a running task for another instance of the device (one that was removed
        or re-created) is cancelled and replaced: it would stop only after its current
        delay, up to a minute, and keep this instance from getting its own retry.
        """
        task = self._connect_tasks.get(device_id)
        if task is not None and not task.done():
            if self._connect_task_device.get(device_id) is device:
                return
            task.cancel()
        task = self._loop.create_task(
            self._connect_with_retry(device_id, device, background),
            name=f"broadlink-connect[{device_id}]",
        )
        self._connect_tasks[device_id] = task
        self._connect_task_device[device_id] = device
        task.add_done_callback(self._connect_task_done)

    def _cancel_connect_tasks(self) -> None:
        """Cancel every retrying connect (BL-7, BL-9)."""
        for task in self._connect_tasks.values():
            task.cancel()
        self._connect_tasks.clear()
        self._connect_task_device.clear()

    async def _connect_with_retry(
        self, device_id: str, device, background: bool = False
    ) -> None:
        """Connect ``device``, retrying until it works or the device goes away.

        ``background`` (BL-7): the device was lost while the remote was awake. The fast
        retries are for the remote's own Wi-Fi coming back after a wake, so skip them,
        and wait before the first attempt: one has just failed.
        """
        skipped = FAST_RETRY_ATTEMPTS if background else 0
        if background:
            await asyncio.sleep(_retry_delay(skipped))
        attempt = 0
        while self._device_instances.get(device_id) is device:
            if device.is_connected:
                return
            attempt += 1
            if await device.connect():
                if background:
                    _LOG.info(
                        "[%s] Reconnected after %d background attempts",
                        device_id,
                        attempt,
                    )
                elif attempt > 1:
                    _LOG.info("[%s] Connected after %d attempts", device_id, attempt)
                return
            delay = _retry_delay(skipped + attempt)
            _LOG.debug(
                "[%s] Connect attempt %d failed, retrying in %.1fs",
                device_id,
                attempt,
                delay,
            )
            await asyncio.sleep(delay)

    def _forget_unanswered(self) -> None:
        """Clear every device's record of not answering (BL-7)."""
        for device in self._device_instances.values():
            device.forget_unanswered()

    def _went_to_standby(self) -> None:
        """End every device's wake window, and let the next wake event start one (BL-9).

        A plain ucapi listener, called inline by ucapi's router as it handles
        enter_standby, so an exit_standby right behind it always finds the flag clear;
        the async handler runs later. A press handled in standby, before the next wake
        event, is then outside every window. Nothing may raise out of a listener, or
        the router (and the connection to the remote) would end.
        """
        self._wake_window_started_at = None
        self._in_standby = True
        self._for_each_device(
            "end the wake window", lambda device: device.note_standby()
        )

    def _woke_up(self) -> None:
        """Forget unanswered attempts (BL-7) and start the wake window (BL-9).

        A plain ucapi listener, like _went_to_standby. Only the first wake event of a
        wake starts the window (see _first_wake_event).
        """

        def wake(device) -> None:
            device.forget_unanswered()
            device.note_wake()

        self._first_wake_event("exit_standby", wake)

    def _start_wake_window(self) -> None:
        """Let presses wait for the remote's Wi-Fi for the next few seconds (BL-9).

        A plain ucapi listener for the core's connect, like _went_to_standby. Only the
        first wake event of a wake starts the window.
        """
        self._first_wake_event("connect", lambda device: device.note_wake())

    def _first_wake_event(self, event: str, start) -> None:
        """Start every device's wake window, unless this wake already has one (BL-9).

        ucapi handles one request at a time, so an event can be queued behind a press
        that is waiting in the window: started again then, the window would make the
        presses queued behind that press wait a second time, and send them late. The
        core sends enter_standby before every wake, which lets the next one start; a
        wake event WAKE_EVENT_LAPSE after the window's start, the time asleep included,
        starts one anyway.
        """
        self._in_standby = False
        now = _boot_time()
        started = self._wake_window_started_at
        if started is not None and now - started < WAKE_EVENT_LAPSE:
            _LOG.info("%s: the wake window has already started for this wake", event)
            return
        self._wake_window_started_at = now
        self._for_each_device("start the wake window", start)

    def setup_device_event_handlers(self, device) -> None:
        """Attach the device's event handlers, and keep it connecting (BL-9).

        The framework calls this for every device it creates: at driver start, and on
        Add, Update and Restore. It then makes one connect attempt; while the remote is
        awake the device also gets a retrying connect, so a Broadlink that can't be
        reached at that moment is connected once it is back, instead of at the next
        wake. A device created in standby starts with its wake window ended, like the
        others. A failure here is logged and doesn't stop the device being created.
        """
        super().setup_device_event_handlers(device)
        try:
            if self._in_standby:
                device.note_standby()
            if self._awake:
                self._start_connect_task(device.identifier, device)
        except Exception:  # pylint: disable=broad-exception-caught
            _LOG.exception("Cannot start the retrying connect for a new device")

    def clear_devices(self) -> None:
        """Remove every device, and stop their retrying connects (BL-9)."""
        self._cancel_connect_tasks()
        super().clear_devices()

    def _for_each_device(self, what: str, action) -> None:
        """Run ``action`` on every device; log, never raise, what fails (BL-9)."""
        for device_id, device in list(self._device_instances.items()):
            try:
                action(device)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOG.exception("[%s] Cannot %s", device_id, what)

    def on_device_added(self, device_config) -> None:
        """Create the device for an added configuration (BL-9: Restore re-creates it).

        Only a Restore adds a configuration whose device is already running: the
        framework's add_or_update() updates an existing one in place, and Add refuses
        a Broadlink that is already configured. The framework would keep the running
        device, with its old configuration object, until the driver restarts: the
        restored settings wouldn't apply, and the old device's IP-change update could
        write its old configuration, learned codes included, over the restored one.
        So the running device is removed first, as an Update's remove + add does.
        """
        if device_config is not None:
            device_id = self.get_device_id(device_config)
            if device_id in self._device_instances:
                _LOG.info(
                    "[%s] Re-creating the device from its new configuration", device_id
                )
                self.remove_device(device_id)
        super().on_device_added(device_config)

    def remove_device(self, device_id: str) -> None:
        """Remove a device, and stop its retrying connect (BL-7, BL-9).

        The retry stops on its own once the device is gone, but only after its current
        delay, and until then it kept a new device with the same id from getting its
        own retry. Cancelling it also stops a connect in progress before it can update
        the configuration of a device that was just removed.
        """
        task = self._connect_tasks.pop(device_id, None)
        self._connect_task_device.pop(device_id, None)
        if task is not None:
            task.cancel()
        super().remove_device(device_id)

    def _connect_task_done(self, task: asyncio.Task) -> None:
        """Forget a finished connect task and log anything it raised."""
        for device_id, current in list(self._connect_tasks.items()):
            if current is task:
                del self._connect_tasks[device_id]
                self._connect_task_device.pop(device_id, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOG.error("Connect task %s failed", task.get_name(), exc_info=error)
