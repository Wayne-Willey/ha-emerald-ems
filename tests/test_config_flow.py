"""Tests for the Emerald EMS config flow."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.emerald_ems.api import PROPERTY_LIST_PATH, SIGN_IN_PATH
from custom_components.emerald_ems.const import (
    CONF_CUSTOMER_ID,
    CONF_DEVICE_ID,
    CONF_PROPERTY_ID,
    DOMAIN,
)

from .conftest import (
    TEST_DEVICE_ID,
    TEST_PASSWORD,
    TEST_USERNAME,
    api_url,
    load_json_fixture,
)

USER_INPUT = {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}


async def start_flow(hass: HomeAssistant) -> dict:
    """Open the user step of the config flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_single_device_creates_entry_without_prompting(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """One device on the account means no device picker."""
    result = await start_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.emerald_ems.async_setup_entry", return_value=True
    ) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "123 EXAMPLE STREET - EIAdv 2108123123 (2108123123)"
    assert result["data"][CONF_DEVICE_ID] == TEST_DEVICE_ID
    assert result["data"][CONF_PROPERTY_ID] == "property-uuid-1"
    assert result["data"][CONF_CUSTOMER_ID] == "customer-uuid-1"
    assert result["result"].unique_id == TEST_DEVICE_ID
    assert len(setup.mock_calls) == 1


async def test_multiple_devices_prompt_for_a_choice(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """More than one device means the user picks which to add."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.get(
        api_url(PROPERTY_LIST_PATH), json=load_json_fixture("property_list_multi")
    )

    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "device"

    with patch("custom_components.emerald_ems.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE_ID: "device-uuid-2"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == "device-uuid-2"
    assert result["data"][CONF_PROPERTY_ID] == "property-uuid-2"
    assert result["title"] == "456 OTHER ROAD - EIAdv 2108999999 (2108999999)"


async def test_invalid_auth_is_recoverable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Bad credentials show an error and allow another attempt."""
    aioclient_mock.post(
        api_url(SIGN_IN_PATH), json={"code": 401, "message": "Invalid credentials."}
    )

    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.get(
        api_url(PROPERTY_LIST_PATH), json=load_json_fixture("property_list")
    )

    with patch("custom_components.emerald_ems.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A transport failure is reported as a connection problem, not bad auth."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), exc=aiohttp.ClientError("boom"))

    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_unknown_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unexpected API code is surfaced as an unknown error."""
    aioclient_mock.post(
        api_url(SIGN_IN_PATH), json={"code": 500, "message": "Internal error."}
    )

    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["errors"] == {"base": "unknown"}


async def test_account_without_devices_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An account with no metering hardware has nothing to add."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.get(
        api_url(PROPERTY_LIST_PATH),
        json={"code": 200, "info": {"property": [], "shared_property": []}},
    )

    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices"


async def test_duplicate_device_aborts(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The same device cannot be added twice."""
    config_entry.add_to_hass(hass)

    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_password(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Reauth replaces the stored password on the existing entry."""
    config_entry.add_to_hass(hass)

    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    # Home Assistant adds a "name" placeholder of its own to reauth flows.
    assert result["description_placeholders"]["username"] == TEST_USERNAME

    with patch("custom_components.emerald_ems.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.data[CONF_USERNAME] == TEST_USERNAME
    assert config_entry.data[CONF_DEVICE_ID] == TEST_DEVICE_ID


async def test_reauth_rejects_a_bad_password(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A still-wrong password keeps the reauth form open."""
    config_entry.add_to_hass(hass)
    aioclient_mock.post(
        api_url(SIGN_IN_PATH), json={"code": 401, "message": "Invalid credentials."}
    )

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "still-wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.data[CONF_PASSWORD] == TEST_PASSWORD
