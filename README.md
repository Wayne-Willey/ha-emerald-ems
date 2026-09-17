# Emerald EMS for Home Assistant

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]
[![Tests][tests-shield]][tests]
[![Validate][validate-shield]][validate]

_Home Assistant integration for the [Emerald Electricity Advisor][emerald-ems]._

> [!NOTE]
> The Emerald cloud API is undocumented and reverse engineered, so it can change
> without warning. If something looks wrong, please
> [open an issue](https://github.com/Wayne-Willey/ha-emerald-ems/issues).

## How it works

Emerald's Electricity Advisor counts pulses from your meter and uploads them to
Emerald's cloud whenever the LiveLink gateway (or the phone app) syncs. This is
a **cloud polling** integration: it reads what the cloud has, every five
minutes. It is not real time, and the most recent readings are usually a little
behind. The finest granularity Emerald exposes is a ten minute bucket.

### Energy dashboard

Rather than exposing a `total_increasing` sensor, the integration writes
completed hourly buckets to Home Assistant's long term statistics directly, at
the hour they actually belong to. This matters because the cloud revises recent
buckets: a `total_increasing` sensor would read a downward revision as a meter
reset and permanently inflate your energy history.

Two statistics are created per device:

| Statistic id | Shown as | Contents |
|---|---|---|
| `emerald_ems:<device id>_energy` | `<device name> energy` | Hourly energy, in kWh |
| `emerald_ems:<device id>_cost` | `<device name> cost` | Hourly cost, in your Home Assistant currency |

To wire them up, go to **Settings > Dashboards > Energy > Grid consumption**,
add the energy statistic, then attach the cost statistic to it using the "use
an entity tracking total costs" option. Search the picker by your device name,
for example `EIAdv 2108123123 energy`.

On first setup the integration imports the last 30 days, so history from before
you installed it appears straight away.

### Sensors

These are the live view of the account. They deliberately carry no state class,
because the statistics above already cover long term history. Giving them one
would double count against those statistics.

| Sensor | Source |
|---|---|
| Energy today | Today's running total, in kWh |
| Cost today | Today's running cost |
| Current power | Last completed ten minute bucket, converted to W |
| Average daily spend | Reported by Emerald |
| Daily trend | Percentage change reported by Emerald |
| Last synced | When the device last uploaded to the cloud |

"Last synced" is the device's upload time, not the time of the last poll. A
stale value means the gateway has not synced, not that the integration failed.

## Installation

### HACS

1. In HACS, open the three dot menu and choose **Custom repositories**.
2. Add `https://github.com/Wayne-Willey/ha-emerald-ems` as an **Integration**.
3. Install **Emerald EMS**, then restart Home Assistant.
4. Go to **Settings > Devices & Services > Add Integration** and search for
   **Emerald EMS**.

### Manual

1. Copy `custom_components/emerald_ems/` into your Home Assistant
   `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings > Devices & Services**.

## Configuration

Sign in with the email address and password you use in the Emerald app. If the
account has more than one metering device you will be asked which to add; add
the integration again to follow another. Credentials are stored in your Home
Assistant config entry and are only ever sent to Emerald.

When Emerald stops accepting the stored password, Home Assistant raises a
re-authentication prompt rather than silently failing.

## Troubleshooting

**Readings look stale.** Check the "Last synced" sensor. If it is hours old,
the LiveLink gateway has not uploaded, and no amount of polling will help.
Opening the Emerald phone app forces a sync.

**The current hour is missing from the energy dashboard.** Expected. An hour is
only written once Emerald marks it complete; it appears on a later poll.

**Home Assistant is asking me to re-authenticate.** The stored password stopped
working. Emerald tokens last around a day and are renewed automatically, so
this normally means the password itself changed.

**Something else.** Download diagnostics from the device page and attach them
to an issue. Credentials, account identifiers, serial numbers and your address
are redacted before the file is written, so it is safe to share.

## Known limitations

- **Polling, not streaming.** Data arrives when the gateway syncs. Polling
  faster will not make readings appear sooner, and this is an undocumented API
  on someone else's infrastructure.
- **Trailing buckets are skipped.** An hour is only written to statistics once
  Emerald marks it complete, so the current hour is always missing from the
  energy dashboard. It appears on a later poll.
- **Late gap fills are not backfilled.** Statistics are appended after the most
  recent stored hour. If Emerald fills a gap older than that, the integration
  will not pick it up.
- **Requires Home Assistant 2026.2 or newer.**

## Contributing

See the [contribution guidelines](CONTRIBUTING.md).

```bash
scripts/setup    # install Poetry and the dev and test dependencies
scripts/lint     # ruff check --fix, then ruff format
scripts/test     # the full pytest suite
scripts/develop  # a throwaway Home Assistant with the integration loaded
```

The test suite is fully mocked and needs no Emerald credentials.

***

[emerald-ems]: http://emerald-ems.com.au/
[license-shield]: https://img.shields.io/github/license/Wayne-Willey/ha-emerald-ems.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/Wayne-Willey/ha-emerald-ems.svg?style=for-the-badge
[releases]: https://github.com/Wayne-Willey/ha-emerald-ems/releases
[tests-shield]: https://img.shields.io/github/actions/workflow/status/Wayne-Willey/ha-emerald-ems/test.yml?branch=main&style=for-the-badge&label=tests
[tests]: https://github.com/Wayne-Willey/ha-emerald-ems/actions/workflows/test.yml
[validate-shield]: https://img.shields.io/github/actions/workflow/status/Wayne-Willey/ha-emerald-ems/validate.yml?branch=main&style=for-the-badge&label=hassfest%20%2B%20hacs
[validate]: https://github.com/Wayne-Willey/ha-emerald-ems/actions/workflows/validate.yml
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
