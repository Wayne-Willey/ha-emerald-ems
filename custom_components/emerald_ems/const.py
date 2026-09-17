"""Constants for the Emerald EMS integration."""

from __future__ import annotations

from datetime import timedelta
from logging import Logger, getLogger
from typing import Final

LOGGER: Logger = getLogger(__package__)

DOMAIN: Final = "emerald_ems"
NAME: Final = "Emerald EMS"
MANUFACTURER: Final = "Emerald Planet"
ATTRIBUTION: Final = "Data provided by Emerald EMS"

# API
API_BASE_URL: Final = "https://api.emerald-ems.com.au"
DEFAULT_TIMEOUT: Final = 10

# The cloud API is undocumented and modelled on the Android app. These values are
# sent verbatim by the app on every authentication call; the server has been seen
# to reject requests that omit them, so they are kept as-is rather than invented.
APP_VERSION: Final = "1.2.1"
DEVICE_NAME: Final = "Home Assistant"
DEVICE_OS_VERSION: Final = "12"
DEVICE_TYPE: Final = "android"

# Polling. This is someone else's infrastructure and the upstream data only moves
# when the LiveLink gateway syncs, so there is nothing to gain from polling faster.
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=5)
MIN_SCAN_INTERVAL_MINUTES: Final = 5

# Number of days of history requested on every poll. The cloud backfills and
# revises recent buckets, so a short trailing window is re-read each time.
POLL_WINDOW_DAYS: Final = 2

# Days of history pulled once, on first setup, to seed long term statistics.
BACKFILL_DAYS: Final = 30

# Config entry keys
CONF_PROPERTY_ID: Final = "property_id"
CONF_PROPERTY_NAME: Final = "property_name"
CONF_DEVICE_ID: Final = "device_id"
CONF_DEVICE_SERIAL: Final = "device_serial"
CONF_CUSTOMER_ID: Final = "customer_id"

# Statistic id suffixes (external statistics, written directly to the recorder)
STATISTIC_ENERGY_SUFFIX: Final = "energy"
STATISTIC_COST_SUFFIX: Final = "cost"

DEFAULT_CURRENCY: Final = "AUD"
