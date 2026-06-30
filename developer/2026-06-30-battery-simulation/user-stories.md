# User Stories: Battery Simulation

## Epic

As a homeowner, I want to simulate multiple battery capacities against measured grid and solar
data so that I can compare the practical impact before buying storage.

## Discovery Inputs

This epic depends on `docs/influx-metrics.md` for the metric contract produced by Influx discovery.

Battery simulation should consume normalized samples from the metric ingestion epic. It should not
query InfluxDB directly and should not duplicate metric discovery logic.

## Stories

### Story 1: Simulate the Target Year

As a homeowner, I want the default simulation to cover July 2025 through June 2026 so that the
results represent the intended annual period.

Acceptance criteria:

- Default simulation start is `2025-07-01T00:00:00` Europe/Amsterdam.
- Default simulation end is `2026-07-01T00:00:00` Europe/Amsterdam and is exclusive.
- The report labels the period as `2025-07-01` through `2026-06-30`.

### Story 2: Parse Battery Capacities

As a CLI user, I want to pass a CSV list of battery capacities so that one run can compare several
storage sizes.

Acceptance criteria:

- `--batteries 5,10,15` is parsed as `[5.0, 10.0, 15.0]` kWh.
- Capacity values must be positive decimals.
- Invalid CSV input produces a focused validation error.

### Story 3: Parse Battery Efficiency

As a CLI user, I want to pass battery efficiency as a decimal fraction so that the simulation can
model storage losses.

Acceptance criteria:

- `--efficiency` accepts values where `0 < efficiency <= 1`.
- The default efficiency is documented.
- Invalid efficiency values fail before querying InfluxDB.

### Story 4: Simulate One Battery

As a user, I want the simulator to calculate import and export after adding one battery so that I
can compare against the no-battery baseline.

Acceptance criteria:

- Exported energy charges the battery until full.
- Imported energy is offset by battery discharge until empty.
- The simulation tracks charged energy, discharged energy, losses, and ending state of charge.

### Story 5: Compare Multiple Batteries

As a user, I want the simulator to run the same period for every requested capacity so that the
results are directly comparable.

Acceptance criteria:

- Each capacity uses the same input samples.
- Output includes one row per battery capacity.
- The baseline no-battery import/export is included in the report.

### Story 6: Handle Irregular Samples

As a developer, I want simulation logic to handle irregular Home Assistant sample intervals so
that results remain correct with real Influx data.

Acceptance criteria:

- Energy is calculated from elapsed time between samples.
- Very large gaps are excluded or reported according to configuration.
- Unit tests cover irregular intervals.
