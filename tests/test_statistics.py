"""Tests for the Emerald EMS long term statistics import."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.emerald_ems.api import (
    FLASHES_DATA_PATH,
    PROPERTY_LIST_PATH,
    SIGN_IN_PATH,
)
from custom_components.emerald_ems.models import EmeraldInterval
from custom_components.emerald_ems.statistics import build_series, statistic_id

from .conftest import (
    FROZEN_NOW,
    TEST_DEVICE_ID,
    api_url,
    load_json_fixture,
)
from .test_init import setup_integration

ENERGY_STAT_ID = statistic_id(TEST_DEVICE_ID, "energy")
COST_STAT_ID = statistic_id(TEST_DEVICE_ID, "cost")


def interval(hour: int, kwh: float | None, cost: float | None = 0.0) -> EmeraldInterval:
    """Build an hourly interval on 2024-03-09."""
    return EmeraldInterval(
        start=datetime(2024, 3, 9, hour, tzinfo=UTC),
        duration=timedelta(hours=1),
        kwh=kwh,
        cost=cost,
    )


async def read_statistics(hass: HomeAssistant, stat_id: str) -> list[dict]:
    """Read every stored hourly statistic for an id."""
    await async_wait_recording_done(hass)
    result = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2024, 1, 1, tzinfo=UTC),
        None,
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return result.get(stat_id, [])


def test_build_series_accumulates_a_running_sum() -> None:
    """Each row carries the cumulative sum, which is what the recorder wants."""
    series = build_series(
        [interval(0, 0.5), interval(1, 0.4), interval(2, 0.6)],
        after=None,
        running_sum=0.0,
        value="kwh",
    )

    assert [row["state"] for row in series] == [0.5, 0.4, 0.6]
    assert [row["sum"] for row in series] == pytest.approx([0.5, 0.9, 1.5])


def test_build_series_skips_already_stored_buckets() -> None:
    """Buckets at or before the last stored hour are not written again."""
    series = build_series(
        [interval(0, 0.5), interval(1, 0.4), interval(2, 0.6)],
        after=datetime(2024, 3, 9, 1, tzinfo=UTC),
        running_sum=0.9,
        value="kwh",
    )

    assert len(series) == 1
    assert series[0]["start"] == datetime(2024, 3, 9, 2, tzinfo=UTC)
    assert series[0]["sum"] == pytest.approx(1.5)


def test_build_series_skips_buckets_without_a_value() -> None:
    """A bucket with no reading contributes nothing rather than counting as zero."""
    series = build_series(
        [interval(0, 0.5), interval(1, None), interval(2, 0.6)],
        after=None,
        running_sum=0.0,
        value="kwh",
    )

    assert len(series) == 2
    assert [row["sum"] for row in series] == pytest.approx([0.5, 1.1])


async def test_energy_statistics_are_imported(
    utc_hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Completed hourly buckets land in the recorder as external statistics."""
    freezer.move_to(FROZEN_NOW)
    await setup_integration(utc_hass, config_entry)

    rows = await read_statistics(utc_hass, ENERGY_STAT_ID)

    # Three complete hours on the 9th, two on the 10th. The 02:00 bucket on the
    # 10th is still filling and must not be imported yet.
    assert len(rows) == 5
    assert [row["state"] for row in rows] == pytest.approx([0.5, 0.4, 0.6, 0.3, 0.2])
    assert [row["sum"] for row in rows] == pytest.approx([0.5, 0.9, 1.5, 1.8, 2.0])
    assert rows[0]["start"] == datetime(2024, 3, 9, tzinfo=UTC).timestamp()


async def test_cost_statistics_are_imported(
    utc_hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Cost is imported alongside energy so the dashboard can show spend."""
    freezer.move_to(FROZEN_NOW)
    await setup_integration(utc_hass, config_entry)

    rows = await read_statistics(utc_hass, COST_STAT_ID)

    assert len(rows) == 5
    assert [row["sum"] for row in rows] == pytest.approx([0.1, 0.18, 0.3, 0.36, 0.4])


async def test_repeated_polls_do_not_double_count(
    utc_hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Re-reading the same window must not inflate the running sum.

    The cloud returns the same completed buckets on every poll, so the import
    has to be idempotent.
    """
    freezer.move_to(FROZEN_NOW)
    await setup_integration(utc_hass, config_entry)
    await read_statistics(utc_hass, ENERGY_STAT_ID)

    await config_entry.runtime_data.async_refresh()
    rows = await read_statistics(utc_hass, ENERGY_STAT_ID)

    assert len(rows) == 5
    assert rows[-1]["sum"] == pytest.approx(2.0)


async def test_completed_bucket_is_imported_on_a_later_poll(
    utc_hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An hour skipped while incomplete is picked up once the cloud finishes it."""
    freezer.move_to(FROZEN_NOW)
    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.get(
        api_url(PROPERTY_LIST_PATH), json=load_json_fixture("property_list")
    )
    aioclient_mock.get(
        api_url(FLASHES_DATA_PATH), json=load_json_fixture("flashes_data")
    )

    await setup_integration(utc_hass, config_entry)
    assert len(await read_statistics(utc_hass, ENERGY_STAT_ID)) == 5

    # The device finishes the 02:00 hour and reports a revised, larger value.
    completed = load_json_fixture("flashes_data")
    today = completed["info"]["daily_consumptions"][1]
    today["hourly_consumptions"][2]["is_complete"] = True
    today["hourly_consumptions"][2]["kwh"] = 0.45

    aioclient_mock.clear_requests()
    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.get(
        api_url(PROPERTY_LIST_PATH), json=load_json_fixture("property_list")
    )
    aioclient_mock.get(api_url(FLASHES_DATA_PATH), json=completed)

    await config_entry.runtime_data.async_refresh()
    rows = await read_statistics(utc_hass, ENERGY_STAT_ID)

    assert len(rows) == 6
    assert rows[-1]["state"] == pytest.approx(0.45)
    assert rows[-1]["sum"] == pytest.approx(2.45)


async def test_half_hour_timezone_is_accepted(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Sites in a half hour offset zone still import.

    Adelaide is UTC+9:30, so a local hourly bucket is not on a UTC hour
    boundary. The recorder validates the wall clock minute, so this is
    accepted, but it is worth pinning down: it is the one timezone shape that
    could reject the whole import.
    """
    await hass.config.async_set_time_zone("Australia/Adelaide")
    hass.config.currency = "AUD"
    freezer.move_to(FROZEN_NOW)

    await setup_integration(hass, config_entry)

    rows = await read_statistics(hass, ENERGY_STAT_ID)
    assert len(rows) >= 3
    assert all(row["sum"] is not None for row in rows)
