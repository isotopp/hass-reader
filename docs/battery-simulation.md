# Battery Simulation

Battery simulation compares the no-battery baseline with all configured battery capacities in one
pass over cached aligned power data.

Related contracts:

- [Metric ingestion cache](metric-ingestion.md)
- [InfluxDB metrics](influx-metrics.md)

## Command

```sh
uv run hass-reader simulate \
  --batteries 5,10,15 \
  --efficiency 0.9
```

Useful options:

```sh
uv run hass-reader simulate \
  --start 2025-07-01 \
  --end 2026-07-01 \
  --timezone Europe/Amsterdam \
  --ingestion-db ./var/hass-reader.sqlite3 \
  --simulation-db ./var/hass-reader-simulation.sqlite3 \
  --batteries 5,10,15 \
  --efficiency 0.9 \
  --store-steps
```

## SQLite Databases

Simulation reads from the ingestion cache and writes to a separate simulation database.

| Purpose | Default Path |
| --- | --- |
| Ingestion cache input | `./var/hass-reader.sqlite3` |
| Simulation output | `./var/hass-reader-simulation.sqlite3` |

The split is intentional. Delete the simulation database to reset simulation output without
removing downloaded InfluxDB data.

## Input Table

Simulation reads ordered rows from `aligned_power_sample` in the ingestion cache:

- `timestamp_utc`
- `grid_import_kw`
- `grid_export_kw`
- `solar_production_kw`
- `gap_flags`

The simulator does not query InfluxDB directly.

## One-Pass Execution

Each simulation run creates:

- one no-battery baseline scenario,
- one battery scenario per configured capacity.

The simulator reads each aligned input row once. For each interval, it calculates elapsed hours
from adjacent timestamps, converts kW to kWh, and applies the same interval energy to every
scenario before reading the next row.

Rows with gap flags are skipped by default so large ingestion gaps do not silently distort yearly
totals.

## Efficiency Semantics

`--efficiency` is discharge-side efficiency.

- Charging stores exported energy without loss until the battery reaches capacity.
- Discharging reduces state of charge by the discharged energy.
- Only `discharged_kwh * efficiency` offsets grid import.
- Losses are `discharged_kwh * (1 - efficiency)`.

## Output Tables

### `simulation_run`

Run-level metadata:

- source ingestion database path,
- UTC query bounds,
- run status,
- error message for failed runs.

### `simulation_scenario`

One row per scenario:

- `baseline`,
- one row for each configured battery capacity.

### `simulation_summary`

Required aggregate output per scenario:

- imported kWh,
- exported kWh,
- charged kWh,
- discharged kWh,
- losses kWh,
- ending state of charge kWh.

### `simulation_step`

Optional detailed output controlled by `--store-steps`.

When enabled, each scenario gets one row per simulated interval with import, export, charge,
discharge, loss, and state of charge values. Summary output is written whether or not step storage
is enabled.

## Reporting Contract

Reporting should read simulation results from the simulation database. It should join
`simulation_run`, `simulation_scenario`, and `simulation_summary` for compact comparison output.
It should use `simulation_step` only for detailed audit or chart views when step rows were stored.
