"""Tests for setting up and tearing down the Emerald EMS integration."""

from __future__ import annotations

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.emerald_ems.api import (
    FLASHES_DATA_PATH,
    PROPERTY_LIST_PATH,
    SIGN_IN_PATH,
)
from custom_components.emerald_ems.const import DOMAIN

from .conftest import FROZEN_NOW, api_url, load_json_fixture


async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Add and set up a config entry."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    utc_hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The entry loads, creates entities and unloads cleanly."""
    freezer.move_to(FROZEN_NOW)
    await setup_integration(utc_hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    coordinator = config_entry.runtime_data
    assert coordinator.device is not None
    assert coordinator.device.serial_number == "2108123123"
    assert coordinator.site is not None
    assert coordinator.site.name == "123 EXAMPLE STREET"

    assert await utc_hass.config_entries.async_unload(config_entry.entry_id)
    await utc_hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_device_registry_entry(
    utc_hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    device_registry,
) -> None:
    """The metering device is registered with its cloud metadata."""
    freezer.move_to(FROZEN_NOW)
    await setup_integration(utc_hass, config_entry)

    device = device_registry.async_get_device(identifiers={(DOMAIN, "device-uuid-1")})
    assert device is not None
    assert device.name == "EIAdv 2108123123"
    assert device.serial_number == "2108123123"
    assert device.sw_version == "2.1.1"
    assert device.model == "Electricity Advisor"


async def test_auth_failure_triggers_reauth(
    utc_hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Rejected credentials put the entry into reauth rather than retrying."""
    aioclient_mock.post(
        api_url(SIGN_IN_PATH), json={"code": 401, "message": "Invalid credentials."}
    )

    await setup_integration(utc_hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = [
        flow
        for flow in utc_hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert len(flows) == 1


async def test_connection_failure_is_retried(
    utc_hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """An unreachable API leaves the entry ready to retry."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), exc=aiohttp.ClientError("boom"))

    await setup_integration(utc_hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_missing_device_still_loads(
    utc_hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A device removed from the account does not block setup.

    The energy endpoint is still queried, so existing history stays visible
    instead of the entry failing outright.
    """
    freezer.move_to(FROZEN_NOW)
    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.get(
        api_url(PROPERTY_LIST_PATH),
        json={"code": 200, "info": {"property": [], "shared_property": []}},
    )
    aioclient_mock.get(
        api_url(FLASHES_DATA_PATH), json=load_json_fixture("flashes_data")
    )

    await setup_integration(utc_hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.device is None


async def test_first_poll_requests_backfill_window(
    utc_hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The first poll reaches back far enough to seed statistics."""
    freezer.move_to(FROZEN_NOW)
    await setup_integration(utc_hass, config_entry)

    _method, url, _payload, _headers = mock_api.mock_calls[-1]
    assert url.query["end_date"] == "2024-03-10"
    assert url.query["start_date"] == "2024-02-10"
