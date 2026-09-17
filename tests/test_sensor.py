"""Tests for the Emerald EMS sensors."""

from __future__ import annotations

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import (
    ATTR_STATE_CLASS,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.emerald_ems.api import (
    FLASHES_DATA_PATH,
    PROPERTY_LIST_PATH,
    SIGN_IN_PATH,
)
from custom_components.emerald_ems.const import DOMAIN

from .conftest import (
    FROZEN_NOW,
    TEST_DEVICE_ID,
    api_url,
    load_json_fixture,
)
from .test_init import setup_integration


def entity_id_for(hass: HomeAssistant, key: str) -> str:
    """Resolve a sensor's entity id from its unique id."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{TEST_DEVICE_ID}_{key}"
    )
    assert entity_id is not None, f"no entity registered for {key}"
    return entity_id


@pytest.fixture
async def loaded_entry(
    utc_hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> MockConfigEntry:
    """Return a fully set up entry at the frozen point in time."""
    freezer.move_to(FROZEN_NOW)
    await setup_integration(utc_hass, config_entry)
    return config_entry


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("today_energy", "0.6"),
        ("today_cost", "0.12"),
        ("average_daily_spend", "1.55"),
        ("daily_trend", "-80.0"),
        ("last_synced", "2024-03-10T02:35:00+00:00"),
    ],
)
async def test_sensor_states(
    utc_hass: HomeAssistant, loaded_entry: MockConfigEntry, key: str, expected: str
) -> None:
    """Each sensor reports the value derived from the fixture."""
    state = utc_hass.states.get(entity_id_for(utc_hass, key))
    assert state is not None
    assert state.state == expected


async def test_current_power_uses_the_last_complete_bucket(
    utc_hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Power comes from the newest finished ten minute bucket, converted to W.

    The trailing 02:30 bucket is still filling, so the 02:20 bucket of 0.06 kWh
    is used: 0.06 kWh over ten minutes is 0.36 kW.
    """
    state = utc_hass.states.get(entity_id_for(utc_hass, "current_power"))
    assert state is not None
    assert float(state.state) == pytest.approx(360.0)
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "W"
    assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.POWER
    assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT


async def test_cumulative_sensors_have_no_state_class(
    utc_hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Cumulative values must not generate their own statistics.

    Long term statistics come from the external statistics import. Giving these
    entities a state class would double count, and total_increasing would read
    the cloud's downward revisions as meter resets.
    """
    for key in ("today_energy", "today_cost", "average_daily_spend"):
        state = utc_hass.states.get(entity_id_for(utc_hass, key))
        assert state is not None
        assert ATTR_STATE_CLASS not in state.attributes


async def test_monetary_sensors_use_the_configured_currency(
    utc_hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Cost sensors report in the Home Assistant currency."""
    for key in ("today_cost", "average_daily_spend"):
        state = utc_hass.states.get(entity_id_for(utc_hass, key))
        assert state is not None
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "AUD"
        assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.MONETARY


async def test_missing_day_reports_unknown(
    utc_hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A day the cloud has not reported yet is unknown, not zero.

    Reporting zero would look like real consumption on the dashboard.
    """
    freezer.move_to(FROZEN_NOW)
    payload = load_json_fixture("flashes_data")
    payload["info"]["daily_consumptions"] = [
        day
        for day in payload["info"]["daily_consumptions"]
        if day["date_string"] != "2024-03-10"
    ]

    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.get(
        api_url(PROPERTY_LIST_PATH), json=load_json_fixture("property_list")
    )
    aioclient_mock.get(api_url(FLASHES_DATA_PATH), json=payload)

    await setup_integration(utc_hass, config_entry)

    state = utc_hass.states.get(entity_id_for(utc_hass, "today_energy"))
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_entities_are_attached_to_the_device(
    utc_hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Every sensor belongs to the one metering device."""
    registry = er.async_get(utc_hass)
    entries = er.async_entries_for_config_entry(registry, loaded_entry.entry_id)

    assert len(entries) == 6
    assert len({entry.device_id for entry in entries}) == 1
