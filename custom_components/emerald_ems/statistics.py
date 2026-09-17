"""Long term statistics for Emerald EMS.

The cloud is a laggy, backfilling source: buckets arrive late, and the trailing
bucket of a day is revised until the device reports it complete. Feeding that
into a ``total_increasing`` entity would make every downward revision look like
a meter reset. Instead the finished hourly buckets are written straight to the
recorder as external statistics, timestamped with the hour they actually
belong to. That also lets history from before the integration was installed be
imported on first setup.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    DEFAULT_CURRENCY,
    DOMAIN,
    LOGGER,
    STATISTIC_COST_SUFFIX,
    STATISTIC_ENERGY_SUFFIX,
)
from .models import EmeraldEnergyData, EmeraldInterval


def statistic_id(entry_unique_id: str, suffix: str) -> str:
    """Build an external statistic id for a device."""
    slug = entry_unique_id.replace("-", "_").lower()
    return f"{DOMAIN}:{slug}_{suffix}"


def _metadata(*, name: str, stat_id: str, unit: str | None) -> StatisticMetaData:
    """Build statistic metadata for an external statistic.

    ``unit_class`` drives unit conversion. Energy has a converter; a currency
    does not, so it is declared with no unit class, the same way core
    integrations record cost statistics.
    """
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class=(
            EnergyConverter.UNIT_CLASS if unit == UnitOfEnergy.KILO_WATT_HOUR else None
        ),
        unit_of_measurement=unit,
    )


def _as_datetime(value: Any) -> datetime | None:
    """Normalise a recorder ``start`` value to an aware datetime."""
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(value)
    return None


async def _async_last_statistic(
    hass: HomeAssistant, stat_id: str
) -> tuple[datetime | None, float]:
    """Return the start and running sum of the newest stored statistic."""
    rows = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    if not (series := rows.get(stat_id)):
        return None, 0.0

    row = series[0]
    start = _as_datetime(row.get("start"))
    try:
        total = float(row.get("sum") or 0.0)
    except (TypeError, ValueError):
        total = 0.0
    return start, total


def build_series(
    intervals: list[EmeraldInterval],
    *,
    after: datetime | None,
    running_sum: float,
    value: str,
) -> list[StatisticData]:
    """Accumulate buckets into recorder rows, oldest first.

    Buckets at or before ``after`` are already stored and are skipped, which
    keeps the running sum monotonic without re-reading the whole history.

    Rows keep the bucket's local start time rather than a UTC one. The recorder
    validates that a statistic starts on the hour, and it checks the wall clock,
    so converting here would reject every site in a half hour offset zone such
    as Australia/Adelaide. Home Assistant does the conversion itself.
    """
    series: list[StatisticData] = []
    for interval in intervals:
        if after is not None and dt_util.as_utc(interval.start) <= after:
            continue

        amount = getattr(interval, value)
        if amount is None:
            continue

        running_sum += amount
        series.append(
            StatisticData(start=interval.start, state=amount, sum=running_sum)
        )

    return series


async def async_import_statistics(
    hass: HomeAssistant,
    *,
    unique_id: str,
    device_name: str,
    data: EmeraldEnergyData,
) -> None:
    """Write any newly completed hourly buckets to the recorder."""
    intervals = data.hourly_intervals(complete_only=True)
    if not intervals:
        return

    currency = hass.config.currency or DEFAULT_CURRENCY

    for suffix, value, unit, label in (
        (STATISTIC_ENERGY_SUFFIX, "kwh", UnitOfEnergy.KILO_WATT_HOUR, "energy"),
        (STATISTIC_COST_SUFFIX, "cost", currency, "cost"),
    ):
        stat_id = statistic_id(unique_id, suffix)
        after, running_sum = await _async_last_statistic(hass, stat_id)
        series = build_series(
            intervals, after=after, running_sum=running_sum, value=value
        )
        if not series:
            continue

        async_add_external_statistics(
            hass,
            _metadata(name=f"{device_name} {label}", stat_id=stat_id, unit=unit),
            series,
        )
        LOGGER.debug(
            "Imported %s %s statistics up to %s",
            len(series),
            label,
            series[-1]["start"],
        )
