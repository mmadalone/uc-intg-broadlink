"""
Broadlink integration driver that keeps reconnecting while the remote is awake.

Fork patch BL-1. The remote sends exit_standby before its Wi-Fi is usable, so the
single connect attempt ucapi_framework makes on wake often fails with
"[Errno 101] Network is unreachable" (28 of 128 wakes on 2026-10-17..20). Nothing
retried, and the Broadlink stayed unavailable until the remote slept and woke again.

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

    async def on_r2_connect_cmd(self) -> None:
        """Handle the Remote connect command: connect every device, with retry."""
        _LOG.debug("Client connect command: connecting device(s) with retry")
        await self.api.set_device_state(ucapi.DeviceStates.CONNECTED)
        self.connect_all_with_retry()

    async def on_r2_exit_standby(self) -> None:
        """Handle the Remote waking up: connect every device, with retry."""
        _LOG.debug("Exit standby event: connecting device(s) with retry")
        self.connect_all_with_retry()

    async def on_r2_enter_standby(self) -> None:
        """Handle the Remote going to sleep: stop retrying, then disconnect."""
        for task in self._connect_tasks.values():
            task.cancel()
        self._connect_tasks.clear()
        await super().on_r2_enter_standby()

    def connect_all_with_retry(self) -> None:
        """Start a retrying connect for every device that is not connected yet."""
        for device_id, device in self._device_instances.items():
            task = self._connect_tasks.get(device_id)
            if task is not None and not task.done():
                continue
            task = self._loop.create_task(
                self._connect_with_retry(device_id, device),
                name=f"broadlink-connect[{device_id}]",
            )
            self._connect_tasks[device_id] = task
            task.add_done_callback(self._connect_task_done)

    async def _connect_with_retry(self, device_id: str, device) -> None:
        """Connect ``device``, retrying until it works or the device goes away."""
        attempt = 0
        while self._device_instances.get(device_id) is device:
            if device.is_connected:
                return
            attempt += 1
            if await device.connect():
                if attempt > 1:
                    _LOG.info("[%s] Connected after %d attempts", device_id, attempt)
                return
            delay = _retry_delay(attempt)
            _LOG.debug(
                "[%s] Connect attempt %d failed, retrying in %.1fs",
                device_id,
                attempt,
                delay,
            )
            await asyncio.sleep(delay)

    def _connect_task_done(self, task: asyncio.Task) -> None:
        """Forget a finished connect task and log anything it raised."""
        for device_id, current in list(self._connect_tasks.items()):
            if current is task:
                del self._connect_tasks[device_id]
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOG.error("Connect task %s failed", task.get_name(), exc_info=error)
