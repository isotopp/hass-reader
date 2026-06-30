# User Stories: Battery Simulation

## Epic

As a homeowner, I want to simulate multiple battery capacities against measured grid and solar
data so that I can compare the practical impact before buying storage.

## Discovery Inputs

This epic depends on `docs/influx-metrics.md` for the metric contract produced by Influx discovery.

Battery simulation should consume normalized samples from the metric ingestion epic. It should not
query InfluxDB directly and should not duplicate metric discovery logic.

Battery simulation should read simulation-ready inputs from `aligned_power_sample`, documented in
`docs/metric-ingestion.md`.

Simulation output should be written to a separate SQLite database from the ingestion cache. The
default simulation database should be easy to delete when the user wants to reset simulation
results without removing downloaded Influx data.

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
- Efficiency is interpreted as discharge-side efficiency.
- Charging stores exported energy without loss until capacity is reached.
- Discharging removes energy from the battery and offsets only `discharged_kwh * efficiency` of
  grid import.
- The default efficiency is documented.
- Invalid efficiency values fail before querying InfluxDB.

### Story 4: Run All Battery Scenarios in One Pass

As a user, I want the simulator to process the cached year of aligned power data once for the
baseline and all configured battery sizes so that comparison runs are efficient and consistent.

Acceptance criteria:

- The simulator reads each `aligned_power_sample` row once per run.
- The simulator maintains one in-memory scenario state for no-battery and one for each configured
  battery size.
- Each battery scenario has current state of charge, capacity, efficiency, charged energy,
  discharged energy, and losses.
- Every scenario uses the same source rows and timestep calculations.

### Story 5: Persist Simulation Results Separately

As a user, I want simulation results written to a separate SQLite database so that I can reset
simulations without deleting cached Influx data.

Acceptance criteria:

- The simulator reads from the ingestion cache SQLite database.
- The simulator writes to a different simulation SQLite database.
- The default simulation database path is documented.
- Deleting the simulation database resets simulation output without affecting the ingestion cache.
- A simulation run records the source ingestion database path and queried time range.

### Story 6: Handle Irregular Samples

As a developer, I want simulation logic to handle irregular Home Assistant sample intervals so
that results remain correct with real Influx data.

Acceptance criteria:

- Energy is calculated from elapsed time between samples.
- Very large gaps are excluded or reported according to configuration.
- Unit tests cover irregular intervals.

### Story 7: Summarize Baseline and Battery Scenarios

As a user, I want one summary row per scenario so that I can compare no-battery behavior against all
configured battery capacities.

Acceptance criteria:

- The no-battery baseline is always included.
- Each configured battery capacity has exactly one summary row per simulation run.
- Summary rows include imported kWh, exported kWh, charged kWh, discharged kWh, losses kWh, and
  ending state of charge.
- Summary rows include the capacity and efficiency used by the scenario.
