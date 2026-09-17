"""Config flow for Emerald EMS."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    EmeraldApiClient,
    EmeraldApiClientAuthenticationError,
    EmeraldApiClientCommunicationError,
    EmeraldApiClientError,
)
from .const import (
    CONF_CUSTOMER_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_PROPERTY_ID,
    CONF_PROPERTY_NAME,
    DOMAIN,
    LOGGER,
)
from .models import EmeraldDevice, EmeraldProperty

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.EMAIL,
                autocomplete="username",
            )
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
    }
)


def _device_label(site: EmeraldProperty, device: EmeraldDevice) -> str:
    """Readable label for a device in the picker."""
    name = device.name or device.category or "Device"
    parts = [part for part in (site.name, name) if part]
    label = " - ".join(parts) or device.id
    if device.serial_number:
        label = f"{label} ({device.serial_number})"
    return label


class EmeraldFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle the Emerald EMS config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._username: str | None = None
        self._password: str | None = None
        self._customer_id: str | None = None
        self._properties: list[EmeraldProperty] = []

    async def _async_validate(
        self, username: str, password: str
    ) -> tuple[list[EmeraldProperty], str | None]:
        """Sign in and return the account's properties."""
        client = EmeraldApiClient(
            username=username,
            password=password,
            session=async_get_clientsession(self.hass),
        )
        properties = await client.async_validate_credentials()
        return properties, client.customer_id

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            try:
                properties, customer_id = await self._async_validate(username, password)
            except EmeraldApiClientAuthenticationError as exception:
                LOGGER.warning("Emerald EMS authentication failed: %s", exception)
                errors["base"] = "invalid_auth"
            except EmeraldApiClientCommunicationError as exception:
                LOGGER.warning("Emerald EMS is unreachable: %s", exception)
                errors["base"] = "cannot_connect"
            except EmeraldApiClientError:
                LOGGER.exception("Unexpected error talking to Emerald EMS")
                errors["base"] = "unknown"
            else:
                self._username = username
                self._password = password
                self._customer_id = customer_id
                self._properties = properties
                return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which metering device this entry should follow."""
        choices = [
            (site, device) for site in self._properties for device in site.devices
        ]

        if not choices:
            return self.async_abort(reason="no_devices")

        if user_input is None and len(choices) == 1:
            site, device = choices[0]
            return await self._async_create_entry(site, device)

        if user_input is not None:
            selected = user_input[CONF_DEVICE_ID]
            match = next(
                ((site, device) for site, device in choices if device.id == selected),
                None,
            )
            if match is not None:
                return await self._async_create_entry(*match)
            return self.async_abort(reason="no_devices")

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            options=[
                                selector.SelectOptionDict(
                                    value=device.id,
                                    label=_device_label(site, device),
                                )
                                for site, device in choices
                            ],
                        )
                    )
                }
            ),
        )

    async def _async_create_entry(
        self, site: EmeraldProperty, device: EmeraldDevice
    ) -> ConfigFlowResult:
        """Create the config entry for a chosen device."""
        await self.async_set_unique_id(device.id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=_device_label(site, device),
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_CUSTOMER_ID: self._customer_id,
                CONF_PROPERTY_ID: site.id,
                CONF_PROPERTY_NAME: site.name,
                CONF_DEVICE_ID: device.id,
                CONF_DEVICE_SERIAL: device.serial_number,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the stored credentials stopped working."""
        self._username = entry_data.get(CONF_USERNAME)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a fresh password for an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        username = self._username or entry.data[CONF_USERNAME]

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            try:
                await self._async_validate(username, password)
            except EmeraldApiClientAuthenticationError:
                errors["base"] = "invalid_auth"
            except EmeraldApiClientCommunicationError:
                errors["base"] = "cannot_connect"
            except EmeraldApiClientError:
                LOGGER.exception("Unexpected error talking to Emerald EMS")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"username": username},
            errors=errors,
        )
