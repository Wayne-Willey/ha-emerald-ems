"""Sensor platform for Emerald EMS.

These entities are the live view of the account. Long term statistics are
written separately by ``statistics.py`` straight to the recorder, so the
cumulative sensors here deliberately carry no ``state_class``: giving them one
would double count against the external statistics, and ``total_increasing``
in particular would misread the cloud's downward revisions as meter resets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DEFAULT_CURRENCY
from .coordinator import EmeraldConfigEntry, EmeraldData, EmeraldDataUpdateCoordinator
from .entity import EmeraldEntity

WATTS_PER_KILOWATT = 1000


@dataclass(frozen=True, kw_only=True)
class EmeraldSensorEntityDescription(SensorEntityDescription):
    """Describes an Emerald sensor."""

    value_fn: Callable[[EmeraldData], StateType | datetime]


def _today_energy(data: EmeraldData) -> StateType:
    """Energy consumed so far today, in kWh."""
    today = data.today
    return today.total_kwh if today else None


def _today_cost(data: EmeraldData) -> StateType:
    """Cost accrued so far today."""
    today = data.today
    return today.total_cost if today else None


def _current_power(data: EmeraldData) -> StateType:
    """Average power over the most recent finished ten minute bucket, in W."""
    interval = data.energy.latest_complete_ten_minute
    if interval is None or (power_kw := interval.average_power_kw) is None:
        return None
    return round(power_kw * WATTS_PER_KILOWATT, 1)


def _last_synced(data: EmeraldData) -> datetime | None:
    """When the device last uploaded to the cloud."""
    return data.energy.synced_at


SENSOR_DESCRIPTIONS: tuple[EmeraldSensorEntityDescription, ...] = (
    EmeraldSensorEntityDescription(
        key="today_energy",
        translation_key="today_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_today_energy,
    ),
    EmeraldSensorEntityDescription(
        key="today_cost",
        translation_key="today_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=_today_cost,
    ),
    EmeraldSensorEntityDescription(
        key="current_power",
        translation_key="current_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=_current_power,
    ),
    EmeraldSensorEntityDescription(
        key="average_daily_spend",
        translation_key="average_daily_spend",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda data: data.energy.average_daily_spend,
    ),
    EmeraldSensorEntityDescription(
        key="daily_trend",
        translation_key="daily_trend",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:trending-up",
        suggested_display_precision=0,
        value_fn=lambda data: data.energy.daily_trend,
    ),
    EmeraldSensorEntityDescription(
        key="last_synced",
        translation_key="last_synced",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_synced,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmeraldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Emerald EMS sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        EmeraldSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class EmeraldSensor(EmeraldEntity, SensorEntity):
    """A single Emerald EMS sensor."""

    entity_description: EmeraldSensorEntityDescription

    def __init__(
        self,
        coordinator: EmeraldDataUpdateCoordinator,
        description: EmeraldSensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description)
        if description.device_class is SensorDeviceClass.MONETARY:
            # Monetary sensors must report the instance's configured currency.
            self._attr_native_unit_of_measurement = (
                coordinator.hass.config.currency or DEFAULT_CURRENCY
            )

    @property
    def native_value(self) -> StateType | datetime:
        """Return the current value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Whether the sensor currently has a value to report."""
        return super().available and self.coordinator.data is not None
