"""Diagnostics for Emerald EMS."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CUSTOMER_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_PROPERTY_ID,
)
from .coordinator import EmeraldConfigEntry

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_CUSTOMER_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_PROPERTY_ID,
    "email",
    "id",
    # The device name embeds the serial number, and the property name is the
    # site's street address.
    "name",
    "property_name",
    "mac_address",
    "nmi",
    "serial_number",
    "postal_code",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EmeraldConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    energy: dict[str, Any] = {}
    if data is not None:
        energy = {
            "daily_trend": data.energy.daily_trend,
            "monthly_trend": data.energy.monthly_trend,
            "average_daily_spend": data.energy.average_daily_spend,
            "synced_at": (
                data.energy.synced_at.isoformat() if data.energy.synced_at else None
            ),
            "days": [
                {
                    "date": day.day.isoformat(),
                    "is_complete": day.is_complete,
                    "total_kwh": day.total_kwh,
                    "total_cost": day.total_cost,
                    "hourly_buckets": len(day.hourly),
                    "ten_minute_buckets": len(day.ten_minute),
                }
                for day in data.energy.days
            ],
        }

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "device": async_redact_data(
            asdict(coordinator.device) if coordinator.device else {}, TO_REDACT
        ),
        "last_update_success": coordinator.last_update_success,
        "energy": energy,
    }
