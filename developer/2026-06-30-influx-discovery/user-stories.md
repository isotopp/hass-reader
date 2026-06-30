# User Stories: Influx Discovery

## Epic

As a developer, I want the project to discover and validate the Home Assistant InfluxDB schema so
that later simulation work uses real metric names instead of assumptions.

## Stories

### Story 1: Connect to InfluxDB 1.8

As an operator, I want to configure the InfluxDB URL and database name so that the tool can query
the local Home Assistant database.

Acceptance criteria:

- Defaults are `http://192.168.1.10:8086` and `hass`.
- The URL and database can be overridden by command-line options.
- Connection errors are reported with the URL and database, without a stack trace for normal CLI use.

### Story 2: List Relevant Measurements

As a developer, I want a discovery command to list relevant Home Assistant measurements so that I
can confirm whether power and energy data are available.

Acceptance criteria:

- The command queries `SHOW MEASUREMENTS`.
- The output highlights `sensor__power` and `sensor__energy` when present.
- The command exits non-zero if neither measurement exists.

### Story 3: Find Candidate Energy Entities

As an operator, I want discovery to show P1 and Zonneplan candidate entities so that I can choose
the correct metrics for simulation.

Acceptance criteria:

- Discovery searches `entity_id`, `friendly_name`, and `source` tags.
- Discovery includes entities matching P1, Slimmelezer, Zonneplan, and `connect_energiemeter`.
- Output includes measurement, entity ID, friendly name, unit, recent sample count, and latest value.

### Story 4: Validate Required Defaults

As a developer, I want a validation command for the default metric mapping so that automated tests
and manual setup catch missing or renamed entities early.

Acceptance criteria:

- Validation checks `power_consumed`, `power_produced`, and
  `zonneplan_one_optimized_omvormer_last_measured_value`.
- Validation checks that each default has samples in a configurable lookback window.
- Validation reports stale, missing, and unit-mismatched series separately.

### Story 5: Validate Annual Analysis Coverage

As a homeowner, I want discovery to validate the July 2025 through June 2026 analysis period so
that the battery simulation is based on the intended year of data.

Acceptance criteria:

- The default coverage window is `2025-07-01` inclusive to `2026-07-01` exclusive in
  Europe/Amsterdam time.
- The Influx query uses UTC bounds `2025-06-30T22:00:00Z` inclusive to
  `2026-06-30T22:00:00Z` exclusive.
- Coverage output includes sample counts for P1 import, P1 export, and Zonneplan inverter
  production.
