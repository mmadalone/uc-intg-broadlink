"""
Broadlink integration driver that keeps reconnecting while the remote is awake.

Fork patch BL-1. The remote sends exit_standby before its Wi-Fi is usable, so the
single connect attempt ucapi_framework makes on wake often fails with
"[Errno 101] Network is unreachable" (28 of 128 wakes on 2026-10-17..20). Nothing
retried, and the Broadlink stayed unavailable until the remote slept and woke again.

Fork patch BL-7. A Broadlink that a command finds unreachable while the remote is awake
(unplugged, off Wi-Fi) is reconnected in the background, so button presses can fail
fast instead of each one making a slow connect attempt.

:copyright: (c) 2026 by madalone.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging

import ucapi
from ucapi_framework import BaseIntegrationDriver

_LOG = logging.getLogger(__name__)

# Fast retries cover the remote's Wi-Fi coming back right after a wake, the slow ones
# a longer outage. Retries continue for as long as the remote is awake.
FAST_RETRY_INTERVAL = 0.5
FAST_RETRY_ATTEMPTS = 10
SLOW_RETRY_DELAYS = (5.0, 10.0, 30.0, 60.0)


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
        # BL-7: background reconnects only run while the remote is awake.
        self._awake = True

    async def on_r2_connect_cmd(self) -> None:
        """Handle the Remote connect command: connect every device, with retry."""
        _LOG.debug("Client connect command: connecting device(s) with retry")
        self._awake = True
        await self.api.set_device_state(ucapi.DeviceStates.CONNECTED)
        self.connect_all_with_retry()

    async def on_r2_exit_standby(self) -> None:
        """Handle the Remote waking up: connect every device, with retry."""
        _LOG.debug("Exit standby event: connecting device(s) with retry")
        self._awake = True
        self._forget_unanswered()
        self.connect_all_with_retry()

    async def on_r2_enter_standby(self) -> None:
        """Handle the Remote going to sleep: stop retrying, then disconnect."""
        self._awake = False
        for task in self._connect_tasks.values():
            task.cancel()
        self._connect_tasks.clear()
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
        """Start a retrying connect for one device, unless one is already running."""
        task = self._connect_tasks.get(device_id)
        if task is not None and not task.done():
            return
        task = self._loop.create_task(
            self._connect_with_retry(device_id, device, background),
            name=f"broadlink-connect[{device_id}]",
        )
        self._connect_tasks[device_id] = task
        task.add_done_callback(self._connect_task_done)

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

    def _connect_task_done(self, task: asyncio.Task) -> None:
        """Forget a finished connect task and log anything it raised."""
        for device_id, current in list(self._connect_tasks.items()):
            if current is task:
                del self._connect_tasks[device_id]
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOG.error("Connect task %s failed", task.get_name(), exc_info=error)
