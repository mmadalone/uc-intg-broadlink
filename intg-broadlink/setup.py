"""
Setup flow for Broadlink Remote integration.

:copyright: (c) 2025 Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import math
from typing import Any

import broadlink
from broadlink.exceptions import NetworkTimeoutError
from config_manager import BroadlinkConfig
from ucapi import (
    AbortDriverSetup,
    DriverSetupRequest,
    IntegrationSetupError,
    RequestUserInput,
    SetupAction,
    SetupComplete,
    SetupDriver,
    SetupError,
    UserDataResponse,
)
from ucapi_framework import BaseSetupFlow
from ucapi_framework.setup import SetupSteps
from unfurled import AuthenticationError, Remote, discover_remotes

_LOG = logging.getLogger(__name__)

# BL-9: the wait setting on the "Remote API access" page, by configuration field:
# (maximum in seconds, title, help line). The page offers it as a list of whole seconds
# from 0 to the maximum. The default is BroadlinkConfig's (a dataclass field's default
# is also its class attribute).
WAIT_SETTINGS: dict[str, tuple[float, str, str]] = {
    "wake_wait_sec": (
        6.0,
        "Wait for Wi-Fi after a wake",
        "A press right after the Remote wakes waits up to this long for its Wi-Fi.",
    ),
}
# BL-9: the spacing of the list's choices and the grid _wait_seconds rounds to, in
# seconds. It is never sent: the setting was a number field with "steps": 0.5 at first,
# and the Remote's core takes "steps" as a whole number only (the API description says
# "number"), so it refused the whole page and the setup stayed on "Setting up…" (device
# test of 2026-11-19). A list has nothing for the core to refuse.
WAIT_STEP_SEC = 1.0
# BL-9: the PIN field's id; ucapi redacts "password" when it logs a message.
PIN_FIELD = "password"
# BL-11: the "Configuration mode" action that opens the settings page of the chosen
# Broadlink and saves it in place, as in UC's own Denon, Android TV and Apple TV
# integrations (same id and label).
CONFIGURE_ACTION = {"id": "configure", "label": {"en": "Configure selected device"}}
# BL-11: the framework pauses 1 s before answering an action (a workaround for the web
# configurator missing a quick first answer) and before SetupComplete; so does this.
CONFIGURE_PAUSE_SEC = 1.0


def _wait_seconds(value: Any, fallback: float, maximum: float) -> float:
    """Return a wait setting in seconds: 0..maximum, in whole seconds (BL-9).

    The page's list sends the chosen item's id as text ("4"). Any other input (a saved
    or restored value, another client) is read leniently: a number, or text in which a
    decimal comma counts as a point. A value that is missing or not a finite number
    gives ``fallback``. The value is clamped first, then rounded to the nearest step, a
    half rounding up: 2.5 gives 3.
    """
    if isinstance(value, str):
        value = value.strip()
        if "," in value and "." not in value:
            value = value.replace(",", ".")
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    try:
        seconds = float(value)
    except (ValueError, OverflowError):
        return fallback
    if not math.isfinite(seconds):
        return fallback
    seconds = min(max(seconds, 0.0), maximum)
    return math.floor(seconds / WAIT_STEP_SEC + 0.5) * WAIT_STEP_SEC


def _wait_id(seconds: float) -> str:
    """Return a wait's list item id: the seconds as short text, "4" (BL-9)."""
    return f"{seconds:g}"


def _wait_label(seconds: float, default: float) -> str:
    """Return a wait's list item text: "4 seconds (default)" (BL-9)."""
    text = f"{seconds:g} second" if seconds == 1 else f"{seconds:g} seconds"
    if seconds == 0:
        text += " (no wait)"
    if seconds == default:
        text += " (default)"
    return text


def _wait_fields(device_config: BroadlinkConfig) -> list[dict[str, Any]]:
    """Return each wait setting's list, with a help line under it (BL-9).

    The choices are every value _wait_seconds can return, so the selected one, the saved
    value read through it, is always one of them: the web configurator sends nothing for
    a list whose value is none of its items, and the core then refuses the answer.
    """
    fields: list[dict[str, Any]] = []
    for key, (maximum, title, help_text) in WAIT_SETTINGS.items():
        default = getattr(BroadlinkConfig, key)
        value = _wait_seconds(getattr(device_config, key, default), default, maximum)
        choices = [
            index * WAIT_STEP_SEC
            for index in range(int(round(maximum / WAIT_STEP_SEC)) + 1)
        ]
        fields.append(
            {
                "id": key,
                "label": {"en": title},
                "field": {
                    "dropdown": {
                        "value": _wait_id(value),
                        "items": [
                            {
                                "id": _wait_id(seconds),
                                "label": {"en": _wait_label(seconds, default)},
                            }
                            for seconds in choices
                        ],
                    }
                },
            }
        )
        fields.append(
            {
                "id": f"{key}_help",
                "label": {"en": ""},
                "field": {"label": {"value": {"en": help_text}}},
            }
        )
    return fields


def _kept_address(stored: BroadlinkConfig | None) -> str | None:
    """Return the saved Remote address an Update may keep without a PIN (BL-9).

    That is when the Broadlink being updated has a saved Remote address and API key.
    """
    if stored is None or not stored.remote_api_key:
        return None
    address = stored.remote_address
    if not isinstance(address, str) or not address.strip():
        return None
    return address.strip()


class BroadlinkSetupFlow(BaseSetupFlow[BroadlinkConfig]):
    """
    Setup flow for Broadlink integration.

    Handles Broadlink device configuration through Broadlink protocol discovery or manual entry.
    """

    # BL-9: True once a request for a new Remote API key failed in a way that may have
    # deleted the saved one. Until a key request succeeds, no Remote address can keep
    # the saved key without a PIN (see handle_additional_configuration_response), in
    # this setup session or a later one: the framework keeps one flow object for as
    # long as the driver runs, and a cancelled setup saves nothing, so the saved key
    # may still be the one the Remote no longer has.
    _key_may_be_gone: bool = False
    # BL-11: the Broadlink whose settings page ("Configure selected device") is open,
    # until its answer is handled or the setup session ends.
    _configure_id: str | None = None

    async def handle_driver_setup(self, msg: SetupDriver) -> SetupAction:
        """Start each setup session afresh, then let the framework handle the message.

        BL-9: ucapi-framework 1.9.8 never clears the pending configuration of a setup
        that ends unfinished (Cancel, or a time-out once no page renews the session),
        whether the Remote sends ``AbortDriverSetup`` or, after a lost connection, just
        starts the next setup. While it is set, the framework sends every later answer
        to the "Remote API access" page's handler, so the next Configure went back to
        that unfinished page, whichever action was chosen. The Broadlink chosen for an
        Update belongs to one session too: the framework clears it on an abort but not
        when a new setup starts, and an Add after an Update that ended without an abort
        would then replace (delete) that Broadlink when it saves. The key guard is not
        reset here: it lasts until a key request succeeds.

        BL-11: the framework has no hook for an extra action, so "Configure selected
        device" and the answer to its page are handled here, before the framework sees
        them (it would end the setup with "Invalid configuration action").
        """
        if isinstance(msg, (DriverSetupRequest, AbortDriverSetup)):
            self._pending_device_config = None
            self._configure_id = None
        if isinstance(msg, DriverSetupRequest):
            self._selected_config_id = None
        if isinstance(msg, UserDataResponse):
            if self._configure_id is not None:
                return await self._save_configure(msg)
            if (
                self._pending_device_config is None
                and self._setup_step == SetupSteps.CONFIGURATION_MODE
                and "action" in msg.input_values
                and msg.input_values["action"] == CONFIGURE_ACTION["id"]
            ):
                return await self._start_configure(msg.input_values.get("choice"))
        return await super().handle_driver_setup(msg)

    async def _build_configuration_mode_screen(self) -> RequestUserInput:
        """Add "Configure selected device" to the framework's first page (BL-11).

        The action goes right after "Add a new device" and is selected when a
        Broadlink is configured, as in UC's own integrations. The framework builds the
        page in a private method with no hook for actions: if its page is not the
        expected shape, it is sent unchanged, so a framework change loses only this
        shortcut.
        """
        page = await super()._build_configuration_mode_screen()
        if not any(True for _ in self.config.all()):
            return page
        for setting in page.settings:
            if setting.get("id") != "action":
                continue
            dropdown = setting.get("field", {}).get("dropdown")
            items = dropdown.get("items") if isinstance(dropdown, dict) else None
            if (
                not isinstance(items, list)
                or not items
                or not all(isinstance(item, dict) for item in items)
            ):
                break
            if all(item.get("id") != CONFIGURE_ACTION["id"] for item in items):
                items.insert(1, dict(CONFIGURE_ACTION))
            dropdown["value"] = CONFIGURE_ACTION["id"]
            return page
        _LOG.warning(
            "The setup's first page has no action list: "
            '"Configure selected device" is not offered'
        )
        return page

    async def _start_configure(self, choice: Any) -> SetupAction:
        """Show the settings page of the chosen Broadlink (BL-11)."""
        try:
            stored = self.config.get(choice) if isinstance(choice, str) else None
            if stored is None:
                _LOG.warning("Could not configure device: %s", choice)
                return SetupError(error_type=IntegrationSetupError.OTHER)
            await asyncio.sleep(CONFIGURE_PAUSE_SEC)
            self._configure_id = choice
            return RequestUserInput(
                {"en": f"Configure {stored.name}"}, _wait_fields(stored)
            )
        except Exception as err:  # pylint: disable=broad-except
            self._configure_id = None
            _LOG.error("Cannot show the device settings: %s", err)
            return SetupError(error_type=IntegrationSetupError.OTHER)

    async def _save_configure(self, msg: UserDataResponse) -> SetupAction:
        """Save the settings page's answer in place and end the setup (BL-11).

        Only the page's fields are set, on the saved configuration and on the running
        device's own configuration when that is another object: after the Remote
        unsubscribes the Broadlink's entities and subscribes them again, the framework
        re-creates the device from a copy (``get_device_config`` falls back to
        ``config.get``). Without that, the new value would not reach the running
        device, and its next own update (a changed IP address writes every field of its
        configuration) would put the old value back in the file. No device is
        re-created: the device reads the setting each time it needs it.
        """
        device_id = self._configure_id
        try:
            stored = next(
                (
                    item
                    for item in self.config.all()
                    if self.get_device_id(item) == device_id
                ),
                None,
            )
            if stored is None:
                _LOG.warning("Could not configure device: %s", device_id)
                return SetupError(error_type=IntegrationSetupError.OTHER)
            running = self.driver.get_device_config(device_id)
            values = {}
            for key, (maximum, _title, _help) in WAIT_SETTINGS.items():
                fallback = _wait_seconds(
                    getattr(stored, key, getattr(BroadlinkConfig, key)),
                    getattr(BroadlinkConfig, key),
                    maximum,
                )
                values[key] = _wait_seconds(
                    msg.input_values.get(key), fallback, maximum
                )
            targets = [stored]
            if running is not None and running is not stored:
                targets.append(running)
            old = [{key: getattr(target, key) for key in values} for target in targets]
            for target in targets:
                for key, value in values.items():
                    setattr(target, key, value)
            if not self.config.store():
                # Put the old values back: the user is told the save failed, so the
                # device must not use the new value, and the next write from anywhere
                # (an Update, the device's own IP-change update) must not save it.
                for target, before in zip(targets, old):
                    for key, value in before.items():
                        setattr(target, key, value)
                return SetupError(error_type=IntegrationSetupError.OTHER)
            _LOG.info(
                "Saved for %s: %s",
                stored.name,
                ", ".join(
                    f"{WAIT_SETTINGS[key][1][:1].lower()}{WAIT_SETTINGS[key][1][1:]} "
                    f"{value:g} s"
                    for key, value in values.items()
                ),
            )
            await asyncio.sleep(CONFIGURE_PAUSE_SEC)
            return SetupComplete()
        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Cannot save the device settings: %s", err)
            return SetupError(error_type=IntegrationSetupError.OTHER)
        finally:
            self._configure_id = None

    def get_manual_entry_form(self) -> RequestUserInput:
        """
        Return the manual entry form for device setup.

        :return: RequestUserInput with form fields for manual configuration
        """
        return RequestUserInput(
            {"en": "Broadlink Setup"},
            [
                {
                    "id": "info",
                    "label": {
                        "en": "Setup Information",
                    },
                    "field": {
                        "label": {
                            "value": {
                                "en": (
                                    "Please supply the following settings for your Broadlink device."
                                ),
                            }
                        }
                    },
                },
                {
                    "field": {"text": {"value": ""}},
                    "id": "address",
                    "label": {
                        "en": "IP Address",
                    },
                },
            ],
        )

    async def get_additional_configuration_screen(
        self,
        device_config: BroadlinkConfig,
        previous_input: dict[str, Any],
        error_message: str | None = None,
        error_title: str = "Authentication failed",
    ) -> RequestUserInput:
        """Discover a Remote, then request its PIN for temporary authentication.

        BL-9: the page also has the wait setting, and on Update the PIN may be left
        empty to keep the saved API key.
        """
        try:
            remotes = await discover_remotes()
        except Exception as err:  # pylint: disable=broad-except
            _LOG.warning(
                "Remote discovery failed; allowing manual address entry: %s", err
            )
            remotes = []
        kept_address = _kept_address(self._stored_config(device_config.identifier))
        # BL-9: the id "password" makes ucapi hide the PIN in its message log (it logs
        # every message at DEBUG and redacts only known keys); it was "remote_pin".
        pin_field = {
            "id": PIN_FIELD,
            "label": {
                "en": (
                    "Remote PIN (leave empty to keep the current access)"
                    if kept_address and not self._key_may_be_gone
                    else "Remote PIN"
                )
            },
            "field": {"password": {"value": ""}},
        }
        error_field = (
            [
                {
                    "id": "authentication_error",
                    "label": {"en": error_title},
                    "field": {"label": {"value": {"en": error_message}}},
                }
            ]
            if error_message
            else []
        )

        if remotes:
            remote_items = [
                {
                    "id": remote.api_url,
                    "label": {
                        "en": f"{remote.name.split('-', maxsplit=1)[0]} ({remote.host})"
                    },
                }
                for remote in remotes
            ]
            # BL-9: offer the saved Remote address even when discovery reports the Remote
            # under another one, so that an Update can keep it without a PIN; select the
            # saved Remote, or the one chosen before an error.
            if kept_address and kept_address not in [i["id"] for i in remote_items]:
                remote_items.insert(
                    0,
                    {"id": kept_address, "label": {"en": f"Saved ({kept_address})"}},
                )
            selected = device_config.remote_address
            if selected not in [item["id"] for item in remote_items]:
                selected = remote_items[0]["id"]
            return RequestUserInput(
                {"en": "Remote API access"},
                error_field
                + [
                    {
                        "id": "remote_address",
                        "label": {"en": "Remote"},
                        "field": {
                            "dropdown": {
                                "value": selected,
                                "items": remote_items,
                            }
                        },
                    },
                    pin_field,
                ]
                + _wait_fields(device_config),
            )

        _LOG.info("No Unfolded Circle Remotes discovered; requesting manual address")
        return RequestUserInput(
            {"en": "Remote API access"},
            error_field
            + [
                {
                    "id": "remote_address",
                    "label": {"en": "Remote API Address"},
                    "field": {
                        "text": {
                            "value": device_config.remote_address or "",
                        }
                    },
                },
                pin_field,
            ]
            + _wait_fields(device_config),
        )

    async def handle_additional_configuration_response(
        self, msg: Any
    ) -> SetupError | RequestUserInput | None:
        """Create and retain a Remote API key; never persist the supplied PIN.

        BL-9: also store the wait setting. On Update, an empty PIN keeps the saved API
        key and Remote address, unless another Remote address was chosen or a key
        request has failed (other than for a wrong PIN) and none has succeeded since.
        """
        if self._pending_device_config is None:
            return SetupError(error_type=IntegrationSetupError.OTHER)

        # BL-9: the framework has already copied the page's values, as text, onto the
        # config fields with the same names. Store checked numbers instead; a missing or
        # invalid value keeps the saved one (on Add: the default).
        stored = self._stored_config(self._pending_device_config.identifier)
        for key, (maximum, _title, _help) in WAIT_SETTINGS.items():
            fallback = getattr(BroadlinkConfig, key)
            if stored is not None:
                fallback = _wait_seconds(
                    getattr(stored, key, fallback), fallback, maximum
                )
            setattr(
                self._pending_device_config,
                key,
                _wait_seconds(msg.input_values.get(key), fallback, maximum),
            )

        remote_address = str(msg.input_values.get("remote_address", "")).strip()
        pin = str(msg.input_values.get(PIN_FIELD, "")).strip()
        if not remote_address or not pin:
            # BL-9: on Update, an empty PIN keeps the access saved for the Broadlink when
            # the Remote address is the saved one, unless a failed key request may have
            # deleted that access (in this setup session or an earlier one).
            kept_address = _kept_address(stored)
            if (
                remote_address
                and remote_address == kept_address
                and not self._key_may_be_gone
            ):
                self._pending_device_config.remote_api_key = stored.remote_api_key
                self._pending_device_config.remote_address = kept_address
                _LOG.info(
                    "No Remote PIN entered: keeping the saved Remote API key for %s",
                    kept_address,
                )
                return None
            _LOG.warning("Remote API address or PIN was not supplied")
            if not remote_address:
                title, error = "Address required", "Select or enter the Remote address."
            elif self._key_may_be_gone and kept_address:
                title, error = "PIN required", (
                    "The last attempt may have removed the saved access from the "
                    "Remote. Enter the Remote PIN."
                )
            elif kept_address:
                title, error = "PIN required", (
                    f"The saved Remote address is {kept_address}. A different address "
                    "needs the Remote PIN: enter it, or keep the saved address to keep "
                    "the current access."
                )
            else:
                title, error = "PIN required", "Enter the Remote PIN."
            return await self.get_additional_configuration_screen(
                self._pending_device_config, msg.input_values, error, title
            )

        remote = Remote(remote_address, pin=pin)
        try:
            self._pending_device_config.remote_api_key = await remote.auth.generate_key(
                "broadlink_integration"
            )
            self._pending_device_config.remote_address = remote_address
            # BL-9: the framework saves this configuration, with the new key, as soon
            # as this method returns None, so the saved key works again.
            self._key_may_be_gone = False
        except Exception as err:  # pylint: disable=broad-except
            _LOG.warning("Unable to create Remote API key: %s", err)
            # BL-9: unfurled replaces a key by deleting the old one first. Only a wrong
            # PIN fails before that, so after any other failure the saved key may be
            # gone. That holds whichever address was used: the page can list the same
            # Remote twice (the saved address and the one discovery reports), so until a
            # key request succeeds no address keeps the key without a PIN.
            if not isinstance(err, AuthenticationError):
                self._key_may_be_gone = True
            return await self.get_additional_configuration_screen(
                self._pending_device_config,
                msg.input_values,
                (
                    "Unable to authenticate with the Remote: "
                    f"{err}. Check the selected Remote and PIN, then try again."
                ),
            )
        finally:
            await remote.close()

        return None

    def _stored_config(self, identifier: str | None) -> BroadlinkConfig | None:
        """Return the saved configuration of the Broadlink being updated, or None (BL-9).

        Only on Update: the framework keeps the device chosen for it in
        ``selected_config_id`` until the update is saved. On Add and on a first setup
        there is nothing saved to keep.
        """
        if self._add_mode or self.selected_config_id is None or not identifier:
            return None
        return self.config.get(identifier)

    async def query_device(
        self, input_values: dict[str, Any]
    ) -> BroadlinkConfig | SetupError | RequestUserInput:
        address = input_values["address"]

        if address is None or address == "":
            return self.get_manual_entry_form()

        _LOG.debug("Connecting to Broadlink device at %s", address)

        device = None
        try:
            # Try xdiscover first (exits early when device found)
            _LOG.debug("Attempting xdiscover at %s", address)
            try:
                for discovered in broadlink.xdiscover(
                    discover_ip_address=address, timeout=5
                ):
                    _LOG.debug("Device discovered: %s", discovered)
                    device = discovered
                    break  # Found a device at the target IP, exit early
            except NetworkTimeoutError as timeout_err:
                _LOG.debug("Discovery timed out: %s", timeout_err)
            except Exception as disc_err:
                _LOG.debug("Discovery failed: %s", disc_err)

            if not device:
                # Fallback to hello method
                _LOG.debug("Discovery returned no device, trying hello method")
                try:
                    device = broadlink.hello(ip_address=address, timeout=5)
                    if device:
                        _LOG.debug("Device connected via hello: %s", device)
                except NetworkTimeoutError as timeout_err:
                    _LOG.warning("Hello timed out for %s: %s", address, timeout_err)
                except Exception as hello_err:
                    _LOG.warning("Hello failed for %s: %s", address, hello_err)

            if not device:
                _LOG.error(
                    "No devices found at IP address %s (both discovery and hello failed)",
                    address,
                )
                return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

            device.auth()
            _LOG.info(
                "Broadlink device authenticated: %s", device
            )  # Get device MAC address as identifier
            identifier = device.mac.hex() if hasattr(device, "mac") else None
            if not identifier:
                _LOG.error("Device missing MAC address")
                return SetupError(error_type=IntegrationSetupError.OTHER)

            # Get device name or use type as fallback
            device_name = (
                device.name
                if hasattr(device, "name") and device.name
                else f"Broadlink {device.type}"
            )

            # if we are adding a new device: make sure it's not already configured
            if self._add_mode and self.config.contains(identifier):
                _LOG.info(
                    "Skipping found device %s: already configured",
                    device_name,
                )
                return SetupError(IntegrationSetupError.OTHER)

            # Create config object
            config = BroadlinkConfig(
                identifier=identifier,
                name=device_name,
                address=address,
                data={},
            )

            # BL-9: Update keeps what is saved for this Broadlink: its learned codes, the
            # Remote address and the wait setting. The framework saves the config
            # returned here in place of the saved one, so with data={} every Update
            # wiped the learned codes. The saved API key is not copied: only the
            # "Remote API access" page keeps it, when its PIN is left empty, so until
            # then the pending config, which the framework logs on an error, has none.
            stored = self._stored_config(identifier)
            if stored is not None:
                config.data = stored.data if isinstance(stored.data, dict) else {}
                config.remote_address = stored.remote_address
                config.wake_wait_sec = stored.wake_wait_sec

            # Clear device reference before returning to avoid any cleanup issues
            device = None

            _LOG.debug("Returning config for device %s", device_name)
            return config

        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Setup Error: %s", err, exc_info=True)
            return SetupError(error_type=IntegrationSetupError.NOT_FOUND)
