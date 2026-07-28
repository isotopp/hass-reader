# AGENT.md

## Project

`hass-reader` is a uv-based Python project for reading Home Assistant data from InfluxDB
1.8 and simulating battery storage against measured grid import/export and solar production.

The local target database is:

- URL: `http://192.168.1.10:8086`
- Database: `hass`
- InfluxDB version: 1.8 query API

The default analysis period is July 2025 through the end of June 2026:

- Local half-open interval: `2025-07-01T00:00:00+02:00` to
  `2026-07-01T00:00:00+02:00`.
- Influx UTC half-open interval: `2025-06-30T22:00:00Z` to
  `2026-06-30T22:00:00Z`.

The upstream InfluxDB source can roll over and may no longer provide this historical interval.
Preserve and reuse existing ingestion and simulation SQLite data for historical reporting. Do not
make `report`, `soc`, or `autonomy` depend on refetching the original interval from InfluxDB.

## Development Commands

Run all commands from the repository root.

```sh
uv sync --dev
uv run ruff format
uv run ruff check --fix
uv run ty check
uv run pytest
```

Before considering work complete, run:

```sh
uv run ruff format && uv run ruff check --fix && uv run ty check && uv run pytest
```

## TDD Workflow

Use vertical TDD slices:

1. Write one behavior-focused test through a public interface.
2. Run it and confirm it fails for the expected reason.
3. Implement the smallest useful change to pass.
4. Run the test suite.
5. Refactor only while green.

Prefer integration-style tests over tests coupled to private functions. Tests should describe
observable behavior, such as parsing battery sizes, normalizing power units, querying an Influx
series, or producing a battery simulation summary.

## Influx Metrics

Use typed Home Assistant measurements:

- `sensor__power` for instantaneous power values.
- `sensor__energy` for cumulative or daily energy values.

Relevant discovered entities:

- Grid import power: `sensor__power`, `entity_id='power_consumed'`, unit `kW`.
- Grid export power: `sensor__power`, `entity_id='power_produced'`, unit `kW`.
- Zonneplan inverter production power: `sensor__power`,
  `entity_id='zonneplan_one_optimized_omvormer_last_measured_value'`, unit `W`.
- Zonneplan grid consumption today: `sensor__energy`,
  `entity_id='zonneplan_electricity_consumption_today'`, unit `kWh`.
- Zonneplan grid returned today: `sensor__energy`,
  `entity_id='zonneplan_electricity_returned_today'`, unit `kWh`.
- Zonneplan solar yield today: `sensor__energy`, `entity_id='zonneplan_yield_today'`, unit `kWh`.
- Zonneplan solar yield total: `sensor__energy`,
  `entity_id='zonneplan_one_optimized_omvormer_yield_total'`, unit `kWh`.

The Zonneplan extension also exposes `connect_energiemeter_*` power entities. They are useful
cross-checks for P1 import/export in watts:

- `connect_energiemeter_electricity_average`
- `connect_energiemeter_electricity_consumption`
- `connect_energiemeter_electricity_production`

## Implementation Notes

- Normalize all power to kW and all energy to kWh at the boundary.
- Treat P1 import and export as separate non-negative series.
- Treat solar inverter production as generation before household load.
- Simulate battery capacity values from a CSV command-line option, for example `--batteries 5,10,15`.
- Validate `--efficiency` as a decimal fraction where `0 < efficiency <= 1`.
- Prefer half-open time intervals: `start <= time < end`.
- Use Europe/Amsterdam as the reporting timezone unless the CLI overrides it.
- Keep Influx query construction isolated so it can be tested without a live database.
- Use `simulate --store-steps` for reports that need interval-level behavior, including `soc` and
  `autonomy`.
- `hass-reader autonomy` reports time-weighted autonomy by `--bucket day|month` from existing
  `simulation_step` data.
- Autonomy is `autonomous_duration / valid_simulated_duration * 100`, where an interval is
  autonomous when simulated average import power is less than or equal to `--threshold-watts`.
- The default autonomy threshold is `30 W`, intentionally below the observed roughly `300 W` night
  idle load.
- Include the no-battery baseline as an autonomy comparison row by default; battery selectors limit
  battery rows while keeping the baseline row for context.
- Split autonomy intervals that cross day or month boundaries proportionally across the affected
  local-time buckets.
