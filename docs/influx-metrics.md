# InfluxDB Metrics

This document is the discovery contract for follow-up epics. Metric ingestion, battery simulation,
and CLI reporting should use these mappings unless the CLI explicitly overrides them.

## Measurement Shape

Home Assistant writes typed measurements into InfluxDB.

| Measurement      | Purpose                               |
|------------------|---------------------------------------|
| `sensor__power`  | Instantaneous power values.           |
| `sensor__energy` | Daily and cumulative energy counters. |

Relevant `sensor__power` tags:

- `domain`
- `entity_id`
- `friendly_name`
- `source`

Relevant `sensor__power` fields:

| Field                     | Type   | Purpose                              |
|---------------------------|--------|--------------------------------------|
| `value`                   | float  | Numeric sensor value.                |
| `unit_of_measurement_str` | string | Unit such as `W`, `kW`, or `kWh`.    |
| `state_class_str`         | string | Home Assistant state class metadata. |
| `icon_str`                | string | Home Assistant icon metadata.        |

Metric selection should use `entity_id` as the stable key. Friendly names can change over time and
can create multiple grouped series for the same entity.

## Primary Simulation Inputs

| Role                           | Measurement     | Entity ID                                              | Unit  | Source                     | Availability            |
|--------------------------------|-----------------|--------------------------------------------------------|-------|----------------------------|-------------------------|
| P1 grid import power           | `sensor__power` | `power_consumed`                                       | `kW`  | Slimmelezer/Home Assistant | Assured for target year |
| P1 grid export power           | `sensor__power` | `power_produced`                                       | `kW`  | Slimmelezer/Home Assistant | Assured for target year |
| Zonneplan solar inverter power | `sensor__power` | `zonneplan_one_optimized_omvormer_last_measured_value` | `W`   | Zonneplan/Home Assistant   | Assured for target year |

Normalize power units at the ingestion boundary:

- `kW` passes through unchanged.
- `W` is divided by `1000`.

## Optional Diagnostics

Per-phase P1 power is available for diagnostics and reconciliation, but should not be required for
the first simulation model.

| Role                     | Measurement     | Entity ID Pattern           | Unit  |
|--------------------------|-----------------|-----------------------------|-------|
| P1 grid import per phase | `sensor__power` | `power_consumed_phase_1..3` | `kW`  |
| P1 grid export per phase | `sensor__power` | `power_produced_phase_1..3` | `kW`  |

## Zonneplan Extension Cross-Checks

The Zonneplan extension exposes P1-like grid flow metrics in watts. These are useful as validation
signals and fallbacks, but the primary simulation mapping should start with the Slimmelezer P1
entities above.

| Role                      | Measurement     | Entity ID                                      | Unit   | Interpretation                   |
|---------------------------|-----------------|------------------------------------------------|--------|----------------------------------|
| Zonneplan P1 net power    | `sensor__power` | `connect_energiemeter_electricity_average`     | `W`    | Negative values indicate export. |
| Zonneplan P1 import power | `sensor__power` | `connect_energiemeter_electricity_consumption` | `W`    | Non-negative import.             |
| Zonneplan P1 export power | `sensor__power` | `connect_energiemeter_electricity_production`  | `W`    | Non-negative export.             |

## Energy Counters for Validation

Daily and cumulative energy counters can validate aggregate simulation output.

| Role                          | Measurement      | Entity ID                                      | Unit  | Availability            |
|-------------------------------|------------------|------------------------------------------------|-------|-------------------------|
| P1 grid import today          | `sensor__energy` | `energy_consumed_today`                        | `kWh` | Assured for target year |
| P1 grid export today          | `sensor__energy` | `energy_produced_today`                        | `kWh` | Assured for target year |
| Zonneplan grid import today   | `sensor__energy` | `zonneplan_electricity_consumption_today`      | `kWh` | Assured for target year |
| Zonneplan grid returned today | `sensor__energy` | `zonneplan_electricity_returned_today`         | `kWh` | Assured for target year |
| Zonneplan solar yield today   | `sensor__energy` | `zonneplan_yield_today`                        | `kWh` | Assured for target year |
| Zonneplan solar yield total   | `sensor__energy` | `zonneplan_one_optimized_omvormer_yield_total` | `kWh` | Assured for target year |

## Target-Year Availability

Availability was checked for the half-open interval:

- `time >= '2025-06-30T22:00:00Z'`
- `time < '2026-06-30T22:00:00Z'`

This corresponds to July 1, 2025 through June 30, 2026 in Europe/Amsterdam local time.

| Entity ID                                              |   Count   | Unit   | Role                                | Use                  |
|--------------------------------------------------------|----------:|--------|-------------------------------------|----------------------|
| `power_consumed`                                       | 2,163,621 | `kW`   | P1 grid import power                | Primary input        |
| `power_produced`                                       |   854,567 | `kW`   | P1 grid export power                | Primary input        |
| `zonneplan_one_optimized_omvormer_last_measured_value` |    48,349 | `W`    | Zonneplan inverter production power | Primary input        |
| `connect_energiemeter_electricity_average`             |    98,983 | `W`    | Zonneplan P1 net power              | Cross-check          |
| `connect_energiemeter_electricity_consumption`         |    74,007 | `W`    | Zonneplan P1 import power           | Cross-check          |
| `connect_energiemeter_electricity_production`          |    30,291 | `W`    | Zonneplan P1 export power           | Cross-check          |
| `energy_consumed_today`                                | 1,101,394 | `kWh`  | P1 daily grid import                | Aggregate validation |
| `energy_produced_today`                                |   433,045 | `kWh`  | P1 daily grid export                | Aggregate validation |
| `zonneplan_electricity_consumption_today`              |    26,969 | `kWh`  | Zonneplan daily grid import         | Aggregate validation |
| `zonneplan_electricity_returned_today`                 |    11,884 | `kWh`  | Zonneplan daily grid export         | Aggregate validation |
| `zonneplan_yield_today`                                |    16,799 | `kWh`  | Zonneplan daily solar yield         | Aggregate validation |
| `zonneplan_one_optimized_omvormer_yield_total`         |    16,434 | `kWh`  | Zonneplan cumulative solar yield    | Aggregate validation |

## Follow-Up Contract

Metric ingestion should consume the three primary power inputs and normalize them to kW. It should
carry the measurement, entity ID, original unit, source, sample count, and query window forward as
metadata.

Battery simulation should receive already-normalized time-series samples and should not query
InfluxDB directly.

CLI reporting should show the selected metric mapping and the target-year coverage information so
results can be audited.
