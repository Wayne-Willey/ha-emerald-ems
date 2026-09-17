"""Shared fixtures for the Emerald EMS tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.emerald_ems.api import (
    FLASHES_DATA_PATH,
    PROPERTY_LIST_PATH,
    SIGN_IN_PATH,
    TOKEN_REFRESH_PATH,
)
from custom_components.emerald_ems.const import (
    API_BASE_URL,
    CONF_CUSTOMER_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_PROPERTY_ID,
    CONF_PROPERTY_NAME,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Matches the timestamps baked into flashes_data.json: the second day is still
# in progress, with a trailing incomplete bucket.
FROZEN_NOW = "2024-03-10T02:35:00+00:00"

TEST_USERNAME = "user@example.com"
TEST_PASSWORD = "hunter2"
TEST_DEVICE_ID = "device-uuid-1"


def load_json_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture from this package's fixtures directory."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def api_url(path: str) -> str:
    """Absolute URL for an API path."""
    return f"{API_BASE_URL}{path}"


@pytest.fixture(autouse=True)
def auto_fixtures(recorder_mock, enable_custom_integrations):
    """Load the custom integration, with a recorder, in every test.

    ``recorder_mock`` is requested first on purpose: it has to be built before
    anything pulls in ``hass``, and the integration declares the recorder as a
    dependency because it writes long term statistics.
    """
    return


@pytest.fixture
async def utc_hass(hass: HomeAssistant) -> HomeAssistant:
    """Home Assistant pinned to UTC so local buckets line up with the fixtures."""
    await hass.config.async_set_time_zone("UTC")
    hass.config.currency = "AUD"
    return hass


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Register the full happy path against the Emerald API."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.post(
        api_url(TOKEN_REFRESH_PATH), json=load_json_fixture("token_refresh")
    )
    aioclient_mock.get(
        api_url(PROPERTY_LIST_PATH), json=load_json_fixture("property_list")
    )
    aioclient_mock.get(
        api_url(FLASHES_DATA_PATH), json=load_json_fixture("flashes_data")
    )
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a configured Emerald EMS entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="123 EXAMPLE STREET - EIAdv 2108123123 (2108123123)",
        unique_id=TEST_DEVICE_ID,
        data={
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_CUSTOMER_ID: "customer-uuid-1",
            CONF_PROPERTY_ID: "property-uuid-1",
            CONF_PROPERTY_NAME: "123 EXAMPLE STREET",
            CONF_DEVICE_ID: TEST_DEVICE_ID,
            CONF_DEVICE_SERIAL: "2108123123",
        },
    )
