"""
Remote entity functions.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import errno
import logging
import re
import socket
from collections import OrderedDict
from typing import Any

import unfurled
from config_manager import BroadlinkConfig
from ir_converter import convert_to_broadlink, pulses_to_broadlink_data
from rm import COMMAND_RECONNECT_WINDOW, Broadlink
from ucapi import StatusCodes, ir_emitter
from ucapi.entity import EntityTypes
from ucapi.media_player import States as MediaStates
from ucapi.remote import Commands
from ucapi.remote import States as RemoteStates
from ucapi_framework import create_entity_id
from ucapi_framework.entities import IREmitterEntity
from unfurled import Remote

_LOG = logging.getLogger(__name__)

# BL-8: codes the Remote has converted, so a repeated press needs no network request.
CONVERTED_CODE_CACHE_SIZE = 64
# BL-8: a conversion that fails because the Remote's network is not usable yet (right
# after a wake) is retried at this interval, within the press's COMMAND_RECONNECT_WINDOW.
CONVERT_RETRY_INTERVAL = 0.5
# BL-8: unfurled 0.4.1 sends every request without a time limit (it passes timeout=None,
# which aiohttp takes as "no limit"), so each conversion attempt gets its own: what is
# left of the press's window, and at least this long. ucapi handles one request at a
# time, so a hanging conversion would hold every later press.
CONVERT_ATTEMPT_MIN_TIMEOUT = 1.0
# BL-8: errors that mean the Remote's own network is not usable yet, as right after a
# wake. Only these are retried; a refused or misdirected request is not.
_NETWORK_DOWN_ERRNOS = frozenset(
    {errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL}
)


def _causes(err: BaseException):
    """Yield ``err`` and the exceptions it was raised from (unfurled wraps aiohttp's).

    Follows ``raise … from …`` and the implicit context, but not a context the raiser
    suppressed with ``from None``: that exception is not part of the failure.
    """
    seen = 0
    while err is not None and seen < 8:
        yield err
        if err.__cause__ is not None:
            err = err.__cause__
        elif err.__suppress_context__:
            err = None
        else:
            err = err.__context__
        seen += 1


def _network_down(err: Exception) -> bool:
    """Return whether a conversion failed because the network is not usable (BL-8).

    With an IP address the request fails with ``Network is unreachable``; with a host
    name (``Remote3-….local``) the name lookup fails first, with EAI_AGAIN.
    """
    for cause in _causes(err):
        if isinstance(cause, socket.gaierror):
            if cause.errno == socket.EAI_AGAIN:
                return True
        elif isinstance(cause, OSError) and cause.errno in _NETWORK_DOWN_ERRNOS:
            return True
    return False


def _retry_conversion(err: Exception) -> bool:
    """Return whether a failed conversion may work if tried again shortly (BL-8)."""
    if isinstance(err, unfurled.HTTPError):
        return err.status_code == 503
    return isinstance(err, unfurled.ConnectionError) and _network_down(err)


def _conversion_status(err: Exception) -> StatusCodes:
    """Return the status for a failed code conversion (BL-8).

    503 and 500 tell the Remote the command may work later (it resends them only within
    its short retry window after a wake); 400 and 401 say the request itself is wrong.
    Before BL-8 every failure was answered 400.
    """
    if isinstance(err, unfurled.AuthenticationError):
        return StatusCodes.UNAUTHORIZED
    if isinstance(err, unfurled.ConnectionError):
        if any(isinstance(cause, ValueError) for cause in _causes(err)):
            # aiohttp's InvalidURL (a bad Remote address) or a TLS certificate failure:
            # both are permanent, so the Remote shouldn't resend.
            return StatusCodes.BAD_REQUEST
        return StatusCodes.SERVICE_UNAVAILABLE
    if isinstance(err, TimeoutError):
        return StatusCodes.SERVICE_UNAVAILABLE
    if isinstance(err, unfurled.HTTPError):
        if err.status_code == 503:
            return StatusCodes.SERVICE_UNAVAILABLE
        if err.status_code >= 500:
            return StatusCodes.SERVER_ERROR
    return StatusCodes.BAD_REQUEST


BROADLINK_REMOTE_STATE_MAPPING = {
    MediaStates.UNKNOWN: RemoteStates.UNKNOWN,
    MediaStates.UNAVAILABLE: RemoteStates.UNAVAILABLE,
    MediaStates.OFF: RemoteStates.OFF,
    MediaStates.ON: RemoteStates.ON,
    MediaStates.STANDBY: RemoteStates.OFF,
    MediaStates.PLAYING: RemoteStates.ON,
}


class BroadlinkIREmitter(IREmitterEntity):
    """Representation of a Broadlink IR Emitter entity."""

    def __init__(self, config_device: BroadlinkConfig, device: Broadlink):
        """Initialize the class."""
        self._device = device
        self._remote_address = config_device.remote_address
        self._remote_api_key = config_device.remote_api_key
        # BL-8: (format, code) -> Broadlink data, least recently used first.
        self._converted: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        _LOG.debug("Broadlink IR Emitter init")
        super().__init__(
            create_entity_id(EntityTypes.IR_EMITTER, config_device.identifier),
            f"{config_device.name} IR Emitter",
            [ir_emitter.Features.SEND_IR],
            attributes={
                ir_emitter.Attributes.STATE: ir_emitter.States.UNKNOWN,
            },
            options={
                ir_emitter.Options.IR_FORMATS: ["PRONTO", "HEX"],
                ir_emitter.Options.PORTS: [{"id": "main", "name": "Main"}],
            },
            cmd_handler=self.command_handler,
        )
        if device is not None:
            self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync entity state from device to Remote."""
        if self._device is None:
            return
        dev_state = self._device.get_state()
        mapped = BROADLINK_REMOTE_STATE_MAPPING.get(
            dev_state.state, RemoteStates.UNKNOWN
        )
        self.update({ir_emitter.Attributes.STATE: mapped})

    def get_int_param(self, param: str, params: dict[str, Any], default: int):
        """Get parameter in integer format."""
        return self._get_int_param(param, params, default)

    async def command_handler(
        self,
        _entity: IREmitterEntity,
        cmd_id: str,
        params: dict[str, Any] | None = None,
        _options: Any | None = None,
    ) -> StatusCodes:
        """
        Remote entity command handler.

        Called by the integration-API if a command is sent to a configured remote entity.

        :param entity: IR emitter entity
        :param cmd_id: command
        :param params: optional command parameters
        :param options: optional command options
        :return: status code of the command request
        """
        _LOG.info("Got %s command request: %s %s", self.id, cmd_id, params)

        if self._device is None:
            _LOG.warning("No Broadlink instance for entity: %s", self.id)
            return StatusCodes.SERVICE_UNAVAILABLE

        try:
            # BL-4: report the device's result instead of always OK. BL-5: repeat is
            # applied once, in handle_command (it used to be applied here as well).
            return await self.handle_command(cmd_id, params)
        except Exception as ex:  # pylint: disable=broad-except
            _LOG.error("Error executing command %s: %s", cmd_id, ex)
            return StatusCodes.BAD_REQUEST

    async def handle_command(
        self, cmd_id: str, params: dict[str, Any] | None = None
    ) -> StatusCodes:
        """Handle command."""
        if params is None:
            return StatusCodes.BAD_REQUEST

        repeat = max(1, self._get_int_param("repeat", params, 1))

        if cmd_id == "send_ir":
            code_param = params.get("code") if params else None
            if code_param:
                code = await self._get_ir_code(code_param)
                if isinstance(code, StatusCodes):
                    return code  # BL-8: the conversion failed; nothing was sent
                return await self._send_repeated(code, repeat)
            return StatusCodes.BAD_REQUEST

        if cmd_id == "stop_ir":
            # Ignore stop command as Broadlink does not support it
            return StatusCodes.OK

        if cmd_id == Commands.SEND_CMD_SEQUENCE:
            success = True
            for command in params.get("sequence", []):
                for _ in range(0, repeat):
                    command_or_status = self._get_command_or_status_code(
                        cmd_id, command
                    )
                    if isinstance(command_or_status, StatusCodes):
                        success = False
                    else:
                        res = await self._device.send_command(code=command_or_status)
                        if res != StatusCodes.OK:
                            success = False
            if success:
                return StatusCodes.OK
            return StatusCodes.BAD_REQUEST

        # send "raw" commands as is to the receiver
        return await self._send_repeated(cmd_id, repeat)

    async def _send_repeated(self, code: Any, repeat: int) -> StatusCodes:
        """Send ``code`` ``repeat`` times, stopping at the first failure (BL-4/BL-5)."""
        for _ in range(repeat):
            result = await self._device.send_command(code=code)
            if result != StatusCodes.OK:
                return result
        return StatusCodes.OK

    async def _get_ir_code(self, code: Any) -> bytes | StatusCodes:
        """Return the Broadlink data for ``code``, or the status to answer (BL-8).

        PRONTO and HEX codes are converted by the Remote when it has an API key. The
        press that wakes the Remote arrives before the Remote's Wi-Fi is back, and the
        conversion request then failed with "Network is unreachable" and was answered
        400, which the Remote never resends: the press was lost. Now the press first
        waits for the Broadlink connection (which needs the network, BL-2), a conversion
        that still finds the network down is retried within the same window, every
        attempt has a time limit, a failure is answered with a status that says what
        went wrong, and converted codes are remembered.
        """
        remote_format = self._get_remote_ir_format(code)
        if not (self._remote_api_key and self._remote_address and remote_format):
            return convert_to_broadlink(code)

        key = (remote_format, code)
        cached = self._converted.get(key)
        if cached is not None:
            self._converted.move_to_end(key)
            _LOG.debug("Using the remembered conversion of the IR code")
            return cached

        loop = asyncio.get_running_loop()
        deadline = loop.time() + COMMAND_RECONNECT_WINDOW
        if not await self._device.ensure_connected_for_command():
            return StatusCodes.SERVICE_UNAVAILABLE
        # Never try Wake-on-LAN (unfurled's default): the command came from the Remote,
        # so it is awake. unfurled 0.4.1 only wakes once its settings are loaded, which
        # this Remote object never does; the flag keeps it that way in later versions.
        try:
            remote = Remote(
                self._remote_address,
                api_key=self._remote_api_key,
                wake_if_asleep=False,
            )
        except ValueError as err:  # a malformed Remote address in the configuration
            return self._conversion_failed(err)
        try:
            while True:
                limit = max(deadline - loop.time(), CONVERT_ATTEMPT_MIN_TIMEOUT)
                try:
                    data = await asyncio.wait_for(
                        self._convert_ir_code(remote, code, remote_format), limit
                    )
                except (unfurled.UnfurledError, TimeoutError, ValueError) as err:
                    if (
                        _retry_conversion(err)
                        and loop.time() + CONVERT_RETRY_INTERVAL < deadline
                    ):
                        _LOG.info("IR code conversion failed (%s), retrying", err)
                        await asyncio.sleep(CONVERT_RETRY_INTERVAL)
                        continue
                    return self._conversion_failed(err)
                self._converted[key] = data
                if len(self._converted) > CONVERTED_CODE_CACHE_SIZE:
                    self._converted.popitem(last=False)
                return data
        finally:
            await remote.close()

    @staticmethod
    def _conversion_failed(err: Exception) -> StatusCodes:
        """Log a failed conversion and return the status to answer (BL-8)."""
        status = _conversion_status(err)
        _LOG.warning(
            "Cannot convert the IR code, answering %d: %s: %s",
            status.value,
            type(err).__name__,
            err,
        )
        return status

    @staticmethod
    async def _convert_ir_code(remote: Remote, code: str, remote_format: str) -> bytes:
        """Ask the Remote to convert a PRONTO or HEX code to Broadlink data."""
        converted = await remote.ir.convert(code, format=remote_format)
        raw = converted.get("raw") if isinstance(converted, dict) else None
        if not isinstance(raw, list) or not all(
            isinstance(timing, int) and not isinstance(timing, bool) for timing in raw
        ):
            raise ValueError("Remote returned invalid raw IR timings")
        return pulses_to_broadlink_data(raw)

    @staticmethod
    def _get_remote_ir_format(code: Any) -> str | None:
        """Return the Remote conversion format for supported IR input."""
        if not isinstance(code, str):
            return None

        normalized = code.strip()
        if normalized.startswith("0000"):
            return "PRONTO"
        if re.fullmatch(r"\d+;0x[0-9A-Fa-f]+;\d+;\d+", normalized):
            return "HEX"
        return None

    @staticmethod
    def _get_command_or_status_code(cmd_id: str, command: str) -> str | StatusCodes:
        if not command:
            _LOG.error("Command parameter is missing for cmd_id %s", cmd_id)
            return StatusCodes.BAD_REQUEST
        if command.startswith("remote."):
            _LOG.error("Command %s is not allowed for cmd_id %s.", command, cmd_id)
            return StatusCodes.BAD_REQUEST
        return command

    @staticmethod
    def _get_int_param(param: str, params: dict[str, Any], default: int) -> int:
        try:
            value = params.get(param, default)
        except AttributeError:
            return default

        # BL-5: accept integers as well as strings (only strings used to be parsed,
        # so an integer repeat value was silently ignored).
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, str) and len(value) > 0:
            try:
                return int(float(value))
            except ValueError:
                return default
        return default
