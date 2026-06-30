# hass-reader

Read Home Assistant energy data from InfluxDB 1.8 and simulate battery storage against real P1
meter and Zonneplan solar data.

## Goal

The project will answer questions such as:

- How much power was consumed from the grid?
- How much power was produced by the Zonneplan solar array?
- How much power was fed back to the grid?
- How would different battery capacities change import, export, and self-consumption?

The intended command-line interface accepts a CSV list of battery sizes in kWh and a round-trip
efficiency fraction:

```sh
uv run hass-reader simulate \
  --start 2025-07-01 \
  --end 2026-07-01 \
  --batteries 5,10,15 \
  --efficiency 0.9
```

## Development

This is a uv-based Python project.

```sh
uv sync --dev
uv run ruff format
uv run ruff check --fix
uv run ty check
uv run pytest
```

The completion gate is:

```sh
uv run ruff format && uv run ruff check --fix && uv run ty check && uv run pytest
```

## InfluxDB

The local Home Assistant InfluxDB target is:

- URL: `http://192.168.1.10:8086`
- Database: `hass`
- InfluxDB: `1.8`

InfluxDB 1.8 exposes data through the `/query` endpoint:

```sh
curl -G -sS 'http://192.168.1.10:8086/query' \
  --data-urlencode 'db=hass' \
  --data-urlencode 'q=SHOW MEASUREMENTS'
```

Home Assistant stores the relevant data in typed measurements:

- `sensor__power`: instantaneous power.
- `sensor__energy`: daily or cumulative energy counters.

Detailed discovery documentation:

- [InfluxDB access](docs/influx-access.md)
- [InfluxDB metrics](docs/influx-metrics.md)
- [Metric ingestion cache](docs/metric-ingestion.md)
- [Battery simulation](docs/battery-simulation.md)

## Discovered Metrics

Yes, the required metrics are present.

| Purpose | Measurement | Entity ID | Unit | Notes |
| --- | --- | --- | --- | --- |
| Grid power consumed from P1 | `sensor__power` | `power_consumed` | `kW` | Dutch P1 meter via Slimmelezer. Current import from grid. |
| Grid power fed back from P1 | `sensor__power` | `power_produced` | `kW` | Dutch P1 meter via Slimmelezer. Current export to grid. |
| Per-phase grid import | `sensor__power` | `power_consumed_phase_1..3` | `kW` | Optional diagnostic detail. |
| Per-phase grid export | `sensor__power` | `power_produced_phase_1..3` | `kW` | Optional diagnostic detail. |
| Solar inverter production | `sensor__power` | `zonneplan_one_optimized_omvormer_last_measured_value` | `W` | Zonneplan inverter power. Normalize to kW. |
| Zonneplan grid import today | `sensor__energy` | `zonneplan_electricity_consumption_today` | `kWh` | Daily imported energy. |
| Zonneplan grid returned today | `sensor__energy` | `zonneplan_electricity_returned_today` | `kWh` | Daily exported energy. |
| Zonneplan solar yield today | `sensor__energy` | `zonneplan_yield_today` | `kWh` | Daily solar yield. |
| Zonneplan solar yield total | `sensor__energy` | `zonneplan_one_optimized_omvormer_yield_total` | `kWh` | Cumulative inverter yield. |
| Zonneplan P1 average | `sensor__power` | `connect_energiemeter_electricity_average` | `W` | Cross-check from the Zonneplan extension. Negative values indicate export. |
| Zonneplan P1 import | `sensor__power` | `connect_energiemeter_electricity_consumption` | `W` | Cross-check import power. |
| Zonneplan P1 export | `sensor__power` | `connect_energiemeter_electricity_production` | `W` | Cross-check export power. |

Recent 24-hour checks found current data for the P1 Slimmelezer entities, Zonneplan inverter
production, Zonneplan daily counters, and `connect_energiemeter_*` Zonneplan extension entities.

## Analysis Window

The target analysis period is July 2025 through the end of June 2026.

Use a half-open interval:

- Local start: `2025-07-01T00:00:00+02:00`
- Local end: `2026-07-01T00:00:00+02:00`
- UTC query start: `2025-06-30T22:00:00Z`
- UTC query end: `2026-06-30T22:00:00Z`

The end timestamp is exclusive, so this covers every sample from July 1, 2025 through June 30,
2026 in Europe/Amsterdam local time.

Coverage checks over this period found usable data for:

| Entity ID | Count | Unit | Role |
| --- | ---: | --- | --- |
| `power_consumed` | 2,163,621 | `kW` | P1 grid import power |
| `power_produced` | 854,567 | `kW` | P1 grid export power |
| `zonneplan_one_optimized_omvormer_last_measured_value` | 48,349 | `W` | Zonneplan inverter production power |
| `connect_energiemeter_electricity_average` | 98,983 | `W` | Zonneplan P1 net power cross-check |
| `connect_energiemeter_electricity_consumption` | 74,007 | `W` | Zonneplan P1 import cross-check |
| `connect_energiemeter_electricity_production` | 30,291 | `W` | Zonneplan P1 export cross-check |
| `zonneplan_electricity_consumption_today` | 26,969 | `kWh` | Zonneplan daily grid import |
| `zonneplan_electricity_returned_today` | 11,884 | `kWh` | Zonneplan daily grid export |
| `zonneplan_yield_today` | 16,799 | `kWh` | Zonneplan daily solar yield |
| `zonneplan_one_optimized_omvormer_yield_total` | 16,434 | `kWh` | Zonneplan cumulative solar yield |

## Useful Discovery Queries

List measurements:

```sql
SHOW MEASUREMENTS
```

List power entities:

```sql
SHOW TAG VALUES FROM "sensor__power" WITH KEY = "entity_id"
```

List energy entities:

```sql
SHOW TAG VALUES FROM "sensor__energy" WITH KEY = "entity_id"
```

Check recent matching power series:

```sql
SELECT count("value"), last("value"), last("unit_of_measurement_str")
FROM "sensor__power"
WHERE "entity_id" =~ /(?i)(p1|zonneplan|power_consumed|power_produced|slimmelezer|connect_energiemeter)/
  AND time > now() - 24h
GROUP BY "entity_id", "friendly_name", "source"
```

## Simulation Model

The simulator should work from time-ordered samples of:

- grid import power in kW,
- grid export power in kW,
- solar production power in kW,
- elapsed time between samples.

For each timestep, convert power to energy, then apply this priority:

1. Existing grid import remains demand that could be offset by battery discharge.
2. Existing grid export is surplus that could charge the battery.
3. Charging and discharging are limited by available capacity and stored energy.
4. Efficiency is applied consistently as round-trip loss.

The first implementation can report per battery capacity:

- baseline imported kWh,
- baseline exported kWh,
- simulated imported kWh,
- simulated exported kWh,
- charged kWh,
- discharged kWh,
- losses kWh,
- ending state of charge.
