"""Base entity for Emerald EMS."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER, NAME
from .coordinator import EmeraldDataUpdateCoordinator


class EmeraldEntity(CoordinatorEntity[EmeraldDataUpdateCoordinator]):
    """Entity tied to one Emerald metering device."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EmeraldDataUpdateCoordinator,
        description: EntityDescription,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description

        device_key = coordinator.statistics_unique_id
        self._attr_unique_id = f"{device_key}_{description.key}"

        device = coordinator.device
        site = coordinator.site
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            manufacturer=MANUFACTURER,
            name=(device.name if device else None) or NAME,
            model=(device.category if device else None),
            serial_number=(device.serial_number if device else None),
            sw_version=(device.firmware_version if device else None),
            suggested_area=(site.name if site else None),
        )
