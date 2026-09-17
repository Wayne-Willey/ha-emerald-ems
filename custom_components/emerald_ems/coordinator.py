"""Data update coordinator for Emerald EMS."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    EmeraldApiClient,
    EmeraldApiClientAuthenticationError,
    EmeraldApiClientError,
)
from .const import (
    BACKFILL_DAYS,
    CONF_DEVICE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    POLL_WINDOW_DAYS,
)
from .models import EmeraldDevice, EmeraldEnergyData, EmeraldProperty
from .statistics import async_import_statistics

type EmeraldConfigEntry = ConfigEntry[EmeraldDataUpdateCoordinator]


@dataclass(slots=True)
class EmeraldData:
    """Everything one refresh produces."""

    energy: EmeraldEnergyData
    device: EmeraldDevice | None = None
    site: EmeraldProperty | None = None

    @property
    def today(self):
        """Today's consumption entry, if the cloud has reported it."""
        return self.energy.day_for(dt_util.now().date())


class EmeraldDataUpdateCoordinator(DataUpdateCoordinator[EmeraldData]):
    """Poll the Emerald cloud and feed both entities and long term statistics."""

    config_entry: EmeraldConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EmeraldConfigEntry,
        client: EmeraldApiClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.device_id: str = config_entry.data[CONF_DEVICE_ID]
        self.device: EmeraldDevice | None = None
        self.site: EmeraldProperty | None = None
        self._backfilled = False

    @property
    def device_label(self) -> str:
        """Human readable name for the metering device."""
        if self.device is not None:
            return self.device.name or self.device.serial_number or self.device_id
        return self.device_id

    @property
    def statistics_unique_id(self) -> str:
        """Stable id used to build this device's statistic ids."""
        return self.config_entry.unique_id or self.device_id

    async def _async_setup(self) -> None:
        """Resolve device metadata once, before the first refresh."""
        try:
            await self._async_load_device()
        except EmeraldApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(exception) from exception
        except EmeraldApiClientError as exception:
            raise ConfigEntryNotReady(exception) from exception

    async def _async_load_device(self) -> None:
        """Look up the configured device in the property list."""
        for site in await self.client.async_get_properties():
            for device in site.devices:
                if device.id == self.device_id:
                    self.device = device
                    self.site = site
                    return

        LOGGER.warning(
            "Device %s is no longer present on this Emerald account",
            self.device_id,
        )

    def _window(self) -> tuple[date, date]:
        """Date range to request on this poll.

        A short trailing window is re-read every time because the cloud revises
        recent buckets. The first poll after setup reaches further back to seed
        long term statistics with history that predates the integration.
        """
        today = dt_util.now().date()
        span = BACKFILL_DAYS if not self._backfilled else POLL_WINDOW_DAYS
        return today - timedelta(days=span - 1), today

    async def _async_update_data(self) -> EmeraldData:
        """Fetch consumption and hand finished buckets to the recorder."""
        start_date, end_date = self._window()

        try:
            energy = await self.client.async_get_energy_data(
                self.device_id, start_date, end_date
            )
        except EmeraldApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(exception) from exception
        except EmeraldApiClientError as exception:
            raise UpdateFailed(exception) from exception

        self._backfilled = True

        # Statistics are a side effect of the poll: a failure to write them
        # should not mark the whole update as failed and blank the entities.
        try:
            await async_import_statistics(
                self.hass,
                unique_id=self.statistics_unique_id,
                device_name=self.device_label,
                data=energy,
            )
        except Exception:  # noqa: BLE001 - recorder failures must not break polling
            LOGGER.exception("Failed to import Emerald EMS long term statistics")

        return EmeraldData(energy=energy, device=self.device, site=self.site)
