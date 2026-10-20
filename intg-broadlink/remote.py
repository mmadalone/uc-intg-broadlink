"""
Remote entity functions.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from rm import Broadlink
from config_manager import BroadlinkConfig
from ucapi import EntityTypes, StatusCodes
from ucapi.media_player import States as MediaStates
from ucapi.remote import Commands, Features
from ucapi.remote import States as RemoteStates
from ucapi import remote
from ucapi_framework import create_entity_id
from ucapi_framework.entities import RemoteEntity

_LOG = logging.getLogger(__name__)

BROADLINK_REMOTE_STATE_MAPPING = {
    MediaStates.UNKNOWN: RemoteStates.UNKNOWN,
    MediaStates.UNAVAILABLE: RemoteStates.UNAVAILABLE,
    MediaStates.OFF: RemoteStates.OFF,
    MediaStates.ON: RemoteStates.ON,
    MediaStates.STANDBY: RemoteStates.OFF,
    MediaStates.PLAYING: RemoteStates.ON,
}


class BroadlinkRemote(RemoteEntity):
    """Representation of a Broadlink Remote entity."""

    def __init__(self, config_device: BroadlinkConfig, device: Broadlink):
        """Initialize the class."""
        self._device = device
        _LOG.debug("Broadlink Remote init")
        features = [Features.SEND_CMD]
        super().__init__(
            create_entity_id(EntityTypes.REMOTE, config_device.identifier),
            f"{config_device.name} Remote",
            features,
            attributes={
                remote.Attributes.STATE: RemoteStates.UNKNOWN,
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
        self.update({remote.Attributes.STATE: mapped})

    def get_int_param(self, param: str, params: dict[str, Any], default: int):
        """Get parameter in integer format."""
        return self._get_int_param(param, params, default)

    async def command_handler(
        self,
        _entity: RemoteEntity,
        cmd_id: str,
        params: dict[str, Any] | None = None,
        _options: Any | None = None,
    ) -> StatusCodes:
        """
        Remote entity command handler.

        Called by the integration-API if a command is sent to a configured remote entity.

        :param entity: remote entity
        :param cmd_id: command
        :param params: optional command parameters
        :param options: optional command options
        :return: status code of the command request
        """
        _LOG.info("Got %s command request: %s %s", self.id, cmd_id, params)

        if self._device is None:
            _LOG.warning("No Broadlink instance for entity: %s", self.id)
            return StatusCodes.SERVICE_UNAVAILABLE

        # BL-4: report the device's result instead of always OK. BL-5: repeat is
        # applied once, in handle_command (it used to be applied here as well).
        return await self.handle_command(cmd_id, params)

    async def handle_command(
        self, cmd_id: str, params: dict[str, Any] | None = None
    ) -> StatusCodes:
        """Handle command."""
        if params is None:
            return StatusCodes.BAD_REQUEST

        repeat = max(1, self._get_int_param("repeat", params, 1))

        if cmd_id == Commands.SEND_CMD:
            command_or_status = self._get_command_or_status_code(
                cmd_id, params.get("command", "")
            )
            if isinstance(command_or_status, StatusCodes):
                return command_or_status

            action = command_or_status.split(":")[0]
            predefined_code = command_or_status
            # BL-4: learning and removal run once and report their own result;
            # sends stop at the first failure and report it.
            match action.upper():
                case "LEARN":
                    mode = command_or_status.split(":")[1]
                    match mode.upper():
                        case "IR":
                            return await self._device.learn_ir_command(
                                command_or_status
                            )
                        case "RF":
                            return await self._device.learn_rf_command(
                                command_or_status
                            )
                    return StatusCodes.BAD_REQUEST
                case "REMOVE" | "DELETE":
                    return await self._device.remove_command(
                        command_or_status.split(":", 1)[1]
                    )
                case "SEND":
                    predefined_code = command_or_status.split(":", 1)[1]
            for _ in range(0, repeat):
                result = await self._device.send_command(
                    predefined_code=predefined_code
                )
                if result != StatusCodes.OK:
                    return result
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
                        res = await self._device.send_command(
                            predefined_code=command_or_status
                        )
                        if res != StatusCodes.OK:
                            success = False
            if success:
                return StatusCodes.OK
            return StatusCodes.BAD_REQUEST

        # send "raw" commands as is to the receiver
        return await self._device.send_command(code=cmd_id)

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
