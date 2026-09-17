"""Parsed representations of the Emerald EMS cloud API payloads.

The API returns loosely typed JSON with a number of fields that are absent or
null depending on the account. Everything here is defensive: a missing or
malformed field yields ``None`` rather than raising, so that one odd bucket
cannot take down a whole poll.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

# Each ten minute bucket covers a sixth of an hour, so kWh in the bucket
# multiplied by six is the average power in kW over that bucket.
TEN_MINUTE_BUCKETS_PER_HOUR = 6


def _as_float(value: Any) -> float | None:
    """Coerce an API value to float, returning None when it is not numeric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Coerce an API value to int, returning None when it is not numeric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    """Coerce an API value to a non-empty string, else None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value: Any) -> date | None:
    """Parse a ``YYYY-MM-DD`` date string."""
    text = _as_str(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_clock(value: Any) -> time | None:
    """Parse an ``HH:MM`` clock string."""
    text = _as_str(value)
    if text is None:
        return None
    try:
        return time.fromisoformat(text)
    except ValueError:
        return None


def _local_datetime(day: date, clock: time) -> datetime:
    """Combine a date and a wall clock time in Home Assistant's local zone."""
    return datetime.combine(day, clock, tzinfo=dt_util.DEFAULT_TIME_ZONE)


@dataclass(frozen=True, slots=True)
class EmeraldTariff:
    """Tariff attached to a property."""

    supply_charge: float | None = None
    unit_charge: float | None = None
    is_flat_rate: bool = False
    gst_included: bool = False
    discount_percentage: float | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> EmeraldTariff:
        """Build a tariff from a ``tariff_structure`` entry."""
        return cls(
            supply_charge=_as_float(payload.get("supply_charge")),
            unit_charge=_as_float(payload.get("unit_charge")),
            is_flat_rate=bool(payload.get("is_flat_rate")),
            gst_included=bool(payload.get("gst_include")),
            discount_percentage=_as_float(payload.get("discount_percentage")),
        )


@dataclass(frozen=True, slots=True)
class EmeraldDevice:
    """A metering device belonging to a property."""

    id: str
    serial_number: str | None = None
    name: str | None = None
    category: str | None = None
    firmware_version: str | None = None
    mac_address: str | None = None
    nmi: str | None = None
    status: str | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> EmeraldDevice | None:
        """Build a device, or None when it carries no usable id."""
        device_id = _as_str(payload.get("id"))
        if device_id is None:
            return None
        return cls(
            id=device_id,
            serial_number=_as_str(payload.get("serial_number")),
            name=_as_str(payload.get("device_name")),
            category=_as_str(payload.get("device_category")),
            firmware_version=_as_str(payload.get("firmware_version")),
            mac_address=_as_str(payload.get("device_mac_address")),
            nmi=_as_str(payload.get("NMI")),
            status=_as_str(payload.get("device_status")),
        )

    @property
    def is_active(self) -> bool:
        """Whether the cloud considers this device active."""
        return (self.status or "").casefold() == "active"


@dataclass(frozen=True, slots=True)
class EmeraldProperty:
    """A property (site) belonging to the customer."""

    id: str
    name: str | None = None
    state: str | None = None
    postal_code: str | None = None
    devices: tuple[EmeraldDevice, ...] = ()
    tariff: EmeraldTariff | None = None
    is_shared: bool = False

    @classmethod
    def from_api(
        cls, payload: dict[str, Any], *, is_shared: bool = False
    ) -> EmeraldProperty | None:
        """Build a property, or None when it carries no usable id."""
        property_id = _as_str(payload.get("id"))
        if property_id is None:
            return None

        devices = tuple(
            device
            for raw in payload.get("devices") or []
            if isinstance(raw, dict)
            and (device := EmeraldDevice.from_api(raw)) is not None
        )

        tariffs = payload.get("tariff_structure") or []
        tariff = (
            EmeraldTariff.from_api(tariffs[0])
            if tariffs and isinstance(tariffs[0], dict)
            else None
        )

        return cls(
            id=property_id,
            name=_as_str(payload.get("property_name")),
            state=_as_str(payload.get("state")),
            postal_code=_as_str(payload.get("postal_code")),
            devices=devices,
            tariff=tariff,
            is_shared=is_shared,
        )


@dataclass(frozen=True, slots=True)
class EmeraldInterval:
    """A single consumption bucket, hourly or ten minute."""

    start: datetime
    duration: timedelta
    kwh: float | None = None
    cost: float | None = None
    flashes: int | None = None
    is_complete: bool = True

    @property
    def end(self) -> datetime:
        """Exclusive end of the bucket."""
        return self.start + self.duration

    @property
    def average_power_kw(self) -> float | None:
        """Average power across the bucket, in kW."""
        if self.kwh is None:
            return None
        hours = self.duration.total_seconds() / 3600
        if hours <= 0:
            return None
        return self.kwh / hours


@dataclass(frozen=True, slots=True)
class EmeraldDay:
    """One day of consumption for a device."""

    day: date
    is_complete: bool = False
    total_kwh: float | None = None
    total_cost: float | None = None
    total_flashes: int | None = None
    hourly: tuple[EmeraldInterval, ...] = ()
    ten_minute: tuple[EmeraldInterval, ...] = ()

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> EmeraldDay | None:
        """Build a day, or None when the date is missing or malformed."""
        day = _parse_date(payload.get("date_string"))
        if day is None:
            return None

        day_is_complete = bool(payload.get("is_complete"))

        hourly = cls._parse_intervals(
            payload.get("hourly_consumptions") or [],
            day=day,
            key="hour_string",
            duration=timedelta(hours=1),
            day_is_complete=day_is_complete,
        )
        ten_minute = cls._parse_intervals(
            payload.get("ten_minute_consumptions") or [],
            day=day,
            key="time_string",
            duration=timedelta(minutes=10),
            day_is_complete=day_is_complete,
        )

        return cls(
            day=day,
            is_complete=day_is_complete,
            total_kwh=_as_float(payload.get("total_kwh_of_day")),
            total_cost=_as_float(payload.get("total_cost_of_day")),
            total_flashes=_as_int(payload.get("total_consumption_of_day")),
            hourly=hourly,
            ten_minute=ten_minute,
        )

    @staticmethod
    def _parse_intervals(
        raw_intervals: list[Any],
        *,
        day: date,
        key: str,
        duration: timedelta,
        day_is_complete: bool,
    ) -> tuple[EmeraldInterval, ...]:
        """Parse a list of buckets, inferring completeness where absent.

        Ten minute buckets are returned without an ``is_complete`` flag. For a
        day that is still in progress the trailing bucket is the one the device
        is still filling, so it is treated as incomplete.
        """
        parsed: list[EmeraldInterval] = []
        for index, raw in enumerate(raw_intervals):
            if not isinstance(raw, dict):
                continue
            clock = _parse_clock(raw.get(key))
            if clock is None:
                continue

            is_last = index == len(raw_intervals) - 1
            if "is_complete" in raw:
                is_complete = bool(raw["is_complete"])
            else:
                is_complete = day_is_complete or not is_last

            parsed.append(
                EmeraldInterval(
                    start=_local_datetime(day, clock),
                    duration=duration,
                    kwh=_as_float(raw.get("kwh")),
                    cost=_as_float(raw.get("cost")),
                    flashes=_as_int(raw.get("number_of_flashes")),
                    is_complete=is_complete,
                )
            )

        parsed.sort(key=lambda interval: interval.start)
        return tuple(parsed)


@dataclass(frozen=True, slots=True)
class EmeraldEnergyData:
    """A ``flashes-data`` response for one device."""

    device_id: str | None = None
    daily_trend: float | None = None
    monthly_trend: float | None = None
    average_daily_spend: float | None = None
    synced_at: datetime | None = None
    days: tuple[EmeraldDay, ...] = field(default_factory=tuple)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> EmeraldEnergyData:
        """Build energy data from the ``info`` object of a flashes response."""
        days = tuple(
            day
            for raw in payload.get("daily_consumptions") or []
            if isinstance(raw, dict) and (day := EmeraldDay.from_api(raw)) is not None
        )

        synced_at: datetime | None = None
        if (synced_ms := _as_int(payload.get("synced_timestamp"))) is not None:
            # The API reports milliseconds since the epoch, and it is the time
            # the device last uploaded, not the time of this request.
            synced_at = dt_util.utc_from_timestamp(synced_ms / 1000)

        return cls(
            device_id=_as_str(payload.get("id")),
            daily_trend=_as_float(payload.get("daily_trend")),
            monthly_trend=_as_float(payload.get("monthly_trend")),
            average_daily_spend=_as_float(payload.get("average_daily_spend")),
            synced_at=synced_at,
            days=tuple(sorted(days, key=lambda entry: entry.day)),
        )

    def day_for(self, day: date) -> EmeraldDay | None:
        """Return the entry for a given day, if present."""
        return next((entry for entry in self.days if entry.day == day), None)

    @property
    def latest_day(self) -> EmeraldDay | None:
        """Most recent day present in the response."""
        return self.days[-1] if self.days else None

    @property
    def latest_complete_ten_minute(self) -> EmeraldInterval | None:
        """Most recent ten minute bucket the cloud considers finished."""
        for day in reversed(self.days):
            for interval in reversed(day.ten_minute):
                if interval.is_complete:
                    return interval
        return None

    def hourly_intervals(self, *, complete_only: bool = True) -> list[EmeraldInterval]:
        """All hourly buckets across all days, oldest first."""
        intervals = [
            interval
            for day in self.days
            for interval in day.hourly
            if not complete_only or interval.is_complete
        ]
        intervals.sort(key=lambda interval: interval.start)
        return intervals
