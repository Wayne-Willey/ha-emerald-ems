"""Tests for Emerald EMS diagnostics."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.emerald_ems.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import FROZEN_NOW, TEST_PASSWORD, TEST_USERNAME
from .test_init import setup_integration


async def test_diagnostics_redact_identifiers(
    utc_hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Credentials and account identifiers never appear in diagnostics."""
    freezer.move_to(FROZEN_NOW)
    await setup_integration(utc_hass, config_entry)

    result = await async_get_config_entry_diagnostics(utc_hass, config_entry)

    dumped = str(result)
    assert TEST_PASSWORD not in dumped
    assert TEST_USERNAME not in dumped
    assert "2108123123" not in dumped
    assert "device-uuid-1" not in dumped

    assert result["last_update_success"] is True
    assert len(result["energy"]["days"]) == 2
    assert result["energy"]["days"][0]["total_kwh"] == 1.5
