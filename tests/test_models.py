"""Tests for parsing the Emerald API payloads."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from homeassistant.core import HomeAssistant

from custom_components.emerald_ems.models import (
    EmeraldDay,
    EmeraldDevice,
    EmeraldEnergyData,
    EmeraldProperty,
)

from .conftest import load_json_fixture


@pytest.fixture
def energy(utc_hass: HomeAssistant) -> EmeraldEnergyData:
    """Return parsed energy data from the fixture, in UTC."""
    return EmeraldEnergyData.from_api(load_json_fixture("flashes_data")["info"])


def test_synced_timestamp_is_milliseconds(energy: EmeraldEnergyData) -> None:
    """synced_timestamp is epoch milliseconds, not seconds."""
    assert energy.synced_at == datetime(2024, 3, 10, 2, 35, tzinfo=UTC)


def test_days_are_sorted(energy: EmeraldEnergyData) -> None:
    """Days come back oldest first regardless of payload order."""
    assert [day.day for day in energy.days] == [
        date(2024, 3, 9),
        date(2024, 3, 10),
    ]


def test_day_totals(energy: EmeraldEnergyData) -> None:
    """Daily totals are parsed."""
    today = energy.day_for(date(2024, 3, 10))
    assert today is not None
    assert today.total_kwh == 0.6
    assert today.total_cost == 0.12
    assert today.is_complete is False


def test_hourly_completeness_is_taken_from_the_payload(
    energy: EmeraldEnergyData,
) -> None:
    """Hourly buckets carry their own is_complete flag."""
    today = energy.day_for(date(2024, 3, 10))
    assert today is not None
    assert [interval.is_complete for interval in today.hourly] == [True, True, False]


def test_incomplete_hours_are_excluded_from_statistics(
    energy: EmeraldEnergyData,
) -> None:
    """Only finished hours are eligible for long term statistics."""
    complete = energy.hourly_intervals(complete_only=True)
    assert len(complete) == 5
    assert all(interval.is_complete for interval in complete)

    everything = energy.hourly_intervals(complete_only=False)
    assert len(everything) == 6


def test_ten_minute_completeness_is_inferred(energy: EmeraldEnergyData) -> None:
    """The trailing ten minute bucket of an unfinished day is still filling.

    The API omits is_complete on ten minute buckets, so the last bucket of a
    day that is not yet complete is treated as partial.
    """
    today = energy.day_for(date(2024, 3, 10))
    assert today is not None
    assert [interval.is_complete for interval in today.ten_minute] == [
        True,
        True,
        False,
    ]

    finished = energy.day_for(date(2024, 3, 9))
    assert finished is not None
    assert all(interval.is_complete for interval in finished.ten_minute)


def test_latest_complete_ten_minute(energy: EmeraldEnergyData) -> None:
    """The newest finished ten minute bucket is used for current power."""
    interval = energy.latest_complete_ten_minute
    assert interval is not None
    assert interval.start == datetime(2024, 3, 10, 2, 20, tzinfo=UTC)
    assert interval.kwh == 0.06


def test_average_power_conversion(energy: EmeraldEnergyData) -> None:
    """A ten minute bucket converts to average power over that bucket."""
    interval = energy.latest_complete_ten_minute
    assert interval is not None
    # 0.06 kWh across ten minutes is 0.36 kW.
    assert interval.average_power_kw == pytest.approx(0.36)


def test_hourly_average_power(energy: EmeraldEnergyData) -> None:
    """An hourly bucket's kWh equals its average kW."""
    interval = energy.hourly_intervals()[0]
    assert interval.average_power_kw == pytest.approx(interval.kwh)


def test_property_parsing() -> None:
    """Properties, tariffs and devices are parsed."""
    info = load_json_fixture("property_list")["info"]
    site = EmeraldProperty.from_api(info["property"][0])
    assert site is not None
    assert site.postal_code == "3000"
    assert site.is_shared is False
    assert site.tariff is not None
    assert site.tariff.gst_included is True
    assert site.devices[0].nmi == "62824023840"
    assert site.devices[0].firmware_version == "2.1.1"


def test_property_without_id_is_dropped() -> None:
    """A property with no id cannot be addressed and is skipped."""
    assert EmeraldProperty.from_api({"property_name": "No id"}) is None


def test_device_without_id_is_dropped() -> None:
    """A device with no id cannot be polled and is skipped."""
    assert EmeraldDevice.from_api({"serial_number": "123"}) is None


def test_day_without_date_is_dropped() -> None:
    """A day with an unusable date is skipped rather than raising."""
    assert EmeraldDay.from_api({"date_string": "not-a-date"}) is None
    assert EmeraldDay.from_api({}) is None


def test_malformed_values_become_none(utc_hass: HomeAssistant) -> None:
    """Garbage in a numeric field yields None rather than an exception."""
    day = EmeraldDay.from_api(
        {
            "date_string": "2024-03-09",
            "is_complete": True,
            "total_kwh_of_day": "not a number",
            "total_cost_of_day": None,
            "hourly_consumptions": [
                {"hour_string": "00:00", "kwh": None, "cost": "x"},
                {"hour_string": "bad", "kwh": 1.0},
                "not a dict",
            ],
        }
    )
    assert day is not None
    assert day.total_kwh is None
    assert day.total_cost is None
    assert len(day.hourly) == 1
    assert day.hourly[0].kwh is None
    assert day.hourly[0].average_power_kw is None


def test_empty_payload_is_safe() -> None:
    """An empty info object parses to empty data."""
    data = EmeraldEnergyData.from_api({})
    assert data.days == ()
    assert data.synced_at is None
    assert data.latest_day is None
    assert data.latest_complete_ten_minute is None


async def test_local_timezone_is_respected(hass: HomeAssistant) -> None:
    """Bucket times are wall clock times at the property, not UTC."""
    await hass.config.async_set_time_zone("Australia/Melbourne")

    day = EmeraldDay.from_api(
        {
            "date_string": "2024-03-09",
            "is_complete": True,
            "hourly_consumptions": [
                {"hour_string": "00:00", "kwh": 1.0, "is_complete": True}
            ],
        }
    )

    assert day is not None
    start = day.hourly[0].start
    # Local midnight in Melbourne, which is the previous afternoon in UTC.
    assert start.hour == 0
    assert start.utcoffset() is not None
    assert start.astimezone(UTC).day == 8
