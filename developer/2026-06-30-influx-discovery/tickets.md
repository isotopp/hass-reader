# Tickets: Influx Discovery

## Ticket ID Format

Use `DISC-NNN` for tickets in this epic.

## DISC-001: Document InfluxDB Access

Status: Done

Source story: Story 1, Connect to InfluxDB 1.8

Goal: Create durable documentation for how this project reaches the Home Assistant InfluxDB 1.8
database.

Tasks:

- Document the default InfluxDB URL, database name, API style, and query endpoint.
- Document the project convention for local Europe/Amsterdam dates and UTC query bounds.
- Include copy-paste discovery commands using `curl -G -sS`.
- Explain how connection failures should be reported by later CLI implementation work.

Deliverables:

- `docs/influx-access.md`
- Link from `README.md` to the access documentation.

Execution result:

- Completed in `docs/influx-access.md`.
- Linked from `README.md`.

Acceptance criteria:

- The documentation names `http://192.168.1.10:8086` and database `hass`.
- The documentation uses the InfluxDB 1.8 `/query` endpoint.
- The documentation includes the default analysis interval:
  `2025-07-01T00:00:00+02:00` to `2026-07-01T00:00:00+02:00`.
- The documentation includes the equivalent UTC interval:
  `2025-06-30T22:00:00Z` to `2026-06-30T22:00:00Z`.

## DISC-002: Document Relevant Measurements

Status: Done

Source story: Story 2, List Relevant Measurements

Goal: Record the Home Assistant measurements that matter for power and energy analysis.

Tasks:

- Document the result of `SHOW MEASUREMENTS`.
- Identify `sensor__power` as the source of instantaneous power.
- Identify `sensor__energy` as the source of daily and cumulative energy counters.
- Record tag keys and field keys needed by follow-up implementation work.

Deliverables:

- `docs/influx-metrics.md`

Execution result:

- Completed in `docs/influx-metrics.md`, section "Measurement Shape".

Acceptance criteria:

- The documentation states that useful power values use field `value`.
- The documentation states that units are available through `unit_of_measurement_str`.
- The documentation states that metrics are selected primarily by `entity_id`.
- The documentation includes the relevant tags: `domain`, `entity_id`, `friendly_name`, and
  `source`.

## DISC-003: Document Useful P1 and Zonneplan Metrics

Status: Done

Source story: Story 3, Find Candidate Energy Entities

Goal: Publish the list of useful metric mappings for P1 grid flow and Zonneplan solar data.

Tasks:

- Document the required default metrics for grid import, grid export, and solar production.
- Document optional P1 per-phase metrics.
- Document Zonneplan extension cross-check metrics.
- Document daily and cumulative energy counters that can validate aggregate results.

Deliverables:

- `docs/influx-metrics.md`

Execution result:

- Completed in `docs/influx-metrics.md`, sections "Primary Simulation Inputs", "Optional
  Diagnostics", "Zonneplan Extension Cross-Checks", and "Energy Counters for Validation".

Acceptance criteria:

- The documentation includes `power_consumed` as P1 grid import power.
- The documentation includes `power_produced` as P1 grid export power.
- The documentation includes `zonneplan_one_optimized_omvormer_last_measured_value` as Zonneplan
  inverter production power.
- The documentation includes `connect_energiemeter_electricity_average`,
  `connect_energiemeter_electricity_consumption`, and
  `connect_energiemeter_electricity_production` as Zonneplan extension cross-checks.

## DISC-004: Document Assured Availability for the Target Year

Status: Done

Source stories: Story 4, Validate Required Defaults; Story 5, Validate Annual Analysis Coverage

Goal: Record which metrics have data for July 2025 through June 2026 and how that availability was
verified.

Tasks:

- Query sample counts for required and cross-check metrics in the target year.
- Record measurement, entity ID, unit, role, and sample count.
- State which metrics are assured enough for follow-up epics.
- State which metrics are cross-checks rather than primary simulation inputs.

Deliverables:

- `docs/influx-metrics.md`

Execution result:

- Completed in `docs/influx-metrics.md`, section "Target-Year Availability".

Acceptance criteria:

- The documentation includes sample counts for P1 import, P1 export, and Zonneplan inverter
  production in the target year.
- The documentation includes the UTC query bounds used for the availability check.
- The documentation distinguishes primary inputs from validation or cross-check metrics.
- The documentation can be used by metric ingestion without re-running schema discovery.

## DISC-005: Add Follow-Up Epic Pointers

Status: Done

Source story: Epic handoff requirement

Goal: Make the output of this discovery epic easy for follow-up epics to consume.

Tasks:

- Add a dependency note to the metric ingestion epic pointing at `docs/influx-access.md` and
  `docs/influx-metrics.md`.
- Add a dependency note to the battery simulation epic pointing at the metric contract in
  `docs/influx-metrics.md`.
- Add a dependency note to the CLI reporting epic pointing at the metric mapping and availability
  docs.
- Link the docs from the top-level README.

Deliverables:

- Updated follow-up epic `user-stories.md` files.
- Updated `README.md`.

Execution result:

- Completed in `developer/2026-06-30-metric-ingestion/user-stories.md`.
- Completed in `developer/2026-06-30-battery-simulation/user-stories.md`.
- Completed in `developer/2026-06-30-cli-reporting/user-stories.md`.
- Linked from `README.md`.

Acceptance criteria:

- Each follow-up epic has an explicit "Discovery Inputs" section.
- Each section names the docs it depends on.
- The README links to both discovery documentation files.

## Suggested Execution Order

1. `DISC-001`: access details are the base for every other discovery artifact.
2. `DISC-002`: measurement and field shape explain where useful data can live.
3. `DISC-003`: metric mapping turns schema discovery into project defaults.
4. `DISC-004`: availability proves the defaults cover the target year.
5. `DISC-005`: follow-up pointers make ingestion, simulation, and reporting consume the same
   contract.
