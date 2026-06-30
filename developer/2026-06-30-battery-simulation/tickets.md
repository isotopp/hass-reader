# Tickets: Battery Simulation

## Ticket ID Format

Use `SIM-NNN` for tickets in this epic.

## Simulation Design

Battery simulation reads the metric ingestion cache and writes to a separate simulation SQLite
database.

Default input database:

```text
./var/hass-reader.sqlite3
```

Default simulation database:

```text
./var/hass-reader-simulation.sqlite3
```

The simulation must process `aligned_power_sample` once per run. It should maintain a baseline
scenario and one battery object per configured capacity in memory, then write all scenario results
for the run to the simulation database.

Efficiency semantics:

- `--efficiency` is discharge-side efficiency.
- Charging stores exported energy without loss until the battery reaches capacity.
- Discharging reduces battery state of charge by the requested discharge energy.
- Only `discharged_kwh * efficiency` offsets grid import.
- Losses are `discharged_kwh * (1 - efficiency)`.

## Proposed Simulation Database Schema

```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE simulation_run (
    id INTEGER PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    ingestion_db_path TEXT NOT NULL,
    query_start_utc TEXT NOT NULL,
    query_end_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE simulation_scenario (
    id INTEGER PRIMARY KEY,
    simulation_run_id INTEGER NOT NULL REFERENCES simulation_run(id),
    name TEXT NOT NULL,
    capacity_kwh REAL NOT NULL,
    efficiency REAL NOT NULL,
    is_baseline INTEGER NOT NULL,
    UNIQUE (simulation_run_id, name)
);

CREATE TABLE simulation_summary (
    scenario_id INTEGER PRIMARY KEY REFERENCES simulation_scenario(id),
    imported_kwh REAL NOT NULL,
    exported_kwh REAL NOT NULL,
    charged_kwh REAL NOT NULL,
    discharged_kwh REAL NOT NULL,
    losses_kwh REAL NOT NULL,
    ending_soc_kwh REAL NOT NULL
);

CREATE TABLE simulation_step (
    scenario_id INTEGER NOT NULL REFERENCES simulation_scenario(id),
    timestamp_utc TEXT NOT NULL,
    import_kwh REAL NOT NULL,
    export_kwh REAL NOT NULL,
    charged_kwh REAL NOT NULL,
    discharged_kwh REAL NOT NULL,
    losses_kwh REAL NOT NULL,
    soc_kwh REAL NOT NULL,
    PRIMARY KEY (scenario_id, timestamp_utc)
);
```

`simulation_step` can be made optional later if database size becomes a problem. `simulation_summary`
is required.

## SIM-001: Create Simulation SQLite Schema

Status: Done

Source story: Story 5, Persist Simulation Results Separately

Goal: Create a separate SQLite persistence layer for simulation runs and results.

Tasks:

- Add a simulation database module.
- Default the simulation database path to `./var/hass-reader-simulation.sqlite3`.
- Allow the path to be overridden by CLI option and environment variable.
- Create idempotent schema initialization using `schema_version`.
- Keep the simulation database separate from the ingestion cache database.

Acceptance criteria:

- Initializing the simulation database twice is safe.
- The schema contains run, scenario, summary, and optional step tables.
- Tests use temporary SQLite files.
- Deleting the simulation database does not affect the ingestion cache.

Execution result:

- Implemented in `src/hass_reader/simulation_db.py`.
- Covered by `tests/test_simulation_db.py`.
- Committed as `9f8dfbc Add simulation database schema`.

## SIM-002: Parse Simulation CLI Options

Status: Done

Source stories: Story 1, Simulate the Target Year; Story 2, Parse Battery Capacities; Story 3,
Parse Battery Efficiency; Story 5, Persist Simulation Results Separately

Goal: Add command-line parsing for simulation configuration without running the simulation yet.

Tasks:

- Add a `simulate` command.
- Accept `--batteries`, `--efficiency`, `--ingestion-db`, `--simulation-db`, `--start`, `--end`,
  and `--timezone`.
- Parse `--batteries 5,10,15` into positive decimal capacities.
- Validate `0 < efficiency <= 1`.
- Default the analysis range to July 2025 through June 2026.

Acceptance criteria:

- Invalid battery CSV input fails before opening either SQLite database.
- Invalid efficiency fails before opening either SQLite database.
- Defaults match the existing ingestion time range.
- CLI failures do not print Python stack traces.

Execution result:

- Implemented in `src/hass_reader/cli.py`.
- Covered by `tests/test_cli.py`.
- Committed as `97be502 Add simulation CLI parsing`.

## SIM-003: Implement Battery Scenario State

Status: Done

Source stories: Story 3, Parse Battery Efficiency; Story 4, Run All Battery Scenarios in One Pass

Goal: Implement battery and baseline state objects that can consume timestep energy flows.

Tasks:

- Implement a baseline scenario that records import/export without storage.
- Implement a battery scenario with capacity, current SoC, efficiency, charged energy, discharged
  energy, and losses.
- Apply discharge-side efficiency exactly as documented.
- Clamp state of charge between `0` and capacity.

Acceptance criteria:

- Exported energy charges the battery until full.
- Import is offset by discharge only while SoC is available.
- Losses are recorded only on discharge.
- Unit tests cover full charge, empty discharge, partial discharge, and efficiency loss.

Execution result:

- Implemented in `src/hass_reader/battery_simulation.py`.
- Covered by `tests/test_battery_simulation.py`.
- Committed as `7079a35 Add battery scenario state`.

## SIM-004: Process All Scenarios in One Data Pass

Status: Done

Source story: Story 4, Run All Battery Scenarios in One Pass

Goal: Simulate the baseline and all configured batteries while reading aligned input rows once.

Tasks:

- Read ordered rows from `aligned_power_sample` in the ingestion cache.
- Convert power to energy from elapsed time between rows.
- Maintain one scenario state object for baseline and each configured battery capacity.
- Apply the same timestep energy to every scenario before reading the next source row.

Acceptance criteria:

- The ingestion cache is read once per simulation run.
- Every scenario receives the same timestep sequence.
- The no-battery baseline is always included.
- Tests prove two configured batteries are simulated in one pass over a fake reader.

Execution result:

- Implemented in `src/hass_reader/battery_simulation.py`.
- Covered by `tests/test_battery_simulation.py`.
- Committed as `485c863 Simulate battery scenarios in one pass`.

## SIM-005: Handle Irregular Samples and Gaps

Status: Done

Source story: Story 6, Handle Irregular Samples

Goal: Make timestep conversion robust against irregular aligned sample intervals.

Tasks:

- Calculate timestep duration from adjacent `timestamp_utc` values.
- Ignore or flag rows with gap markers from `aligned_power_sample` according to configuration.
- Reject negative or duplicate timestamp intervals.
- Record skipped or flagged intervals in simulation metadata.

Acceptance criteria:

- Energy is based on elapsed hours between adjacent timestamps.
- Large gaps do not silently distort yearly totals.
- Unit tests cover irregular intervals, duplicate timestamps, and gap flags.

Execution result:

- Implemented in `src/hass_reader/battery_simulation.py`.
- Covered by `tests/test_battery_simulation.py`.
- Committed as `fc6776d Handle simulation intervals and gaps`.

## SIM-006: Persist Simulation Runs and Summaries

Status: Done

Source stories: Story 5, Persist Simulation Results Separately; Story 7, Summarize Baseline and
Battery Scenarios

Goal: Write simulation run metadata, scenarios, and summaries to the simulation SQLite database.

Tasks:

- Insert one `simulation_run` row per run.
- Insert one `simulation_scenario` row for baseline and each configured battery capacity.
- Insert one `simulation_summary` row per scenario.
- Store source ingestion database path and UTC query range.
- Mark failed runs with status and error message.

Acceptance criteria:

- Each configured scenario has exactly one summary for a completed run.
- Summary rows include imported, exported, charged, discharged, losses, and ending SoC in kWh.
- Failed runs are visible in the simulation database.
- The ingestion cache database is never modified by simulation persistence.

Execution result:

- Implemented in `src/hass_reader/simulation_db.py`.
- Covered by `tests/test_simulation_db.py`.
- Committed as `95a4385 Persist simulation summaries`.

## SIM-007: Add Optional Step-Level Persistence

Status: Done

Source stories: Story 4, Run All Battery Scenarios in One Pass; Story 6, Handle Irregular Samples

Goal: Store detailed per-step results when the user wants audit or chart data.

Tasks:

- Add a CLI option to enable or disable step persistence.
- Write `simulation_step` rows only when enabled.
- Store per-scenario timestep import, export, charge, discharge, loss, and SoC.
- Keep summary output independent of whether step persistence is enabled.

Acceptance criteria:

- Summary results are identical with and without step persistence.
- Step persistence can be disabled to reduce database size.
- Tests verify step rows for at least baseline and one battery scenario.

Execution result:

- Implemented in `src/hass_reader/battery_simulation.py`, `src/hass_reader/cli.py`, and
  `src/hass_reader/simulation_db.py`.
- Covered by `tests/test_battery_simulation.py`, `tests/test_cli.py`, and
  `tests/test_simulation_db.py`.
- Committed as `4b6aae6 Persist optional simulation steps`.

## SIM-008: Document Simulation Output Contract

Status: Done

Source stories: Story 5, Persist Simulation Results Separately; Story 7, Summarize Baseline and
Battery Scenarios

Goal: Document how simulation reads cached ingestion data and writes resettable results.

Tasks:

- Create `docs/battery-simulation.md`.
- Document the separate input and output SQLite databases.
- Document one-pass multi-scenario execution.
- Document discharge-side efficiency.
- Document simulation summary and optional step tables.
- Link the document from README and follow-up reporting stories.

Acceptance criteria:

- Users know they can delete the simulation database to reset simulation output.
- Reporting has a clear contract for reading simulation results.
- Efficiency semantics are documented in one place.

Execution result:

- Completed in `docs/battery-simulation.md`.
- Linked from `README.md`.
- Follow-up reporting pointer added to
  `developer/2026-06-30-cli-reporting/user-stories.md`.

## Suggested Execution Order

1. `SIM-001`: create the simulation database schema.
2. `SIM-002`: expose simulation configuration through the CLI.
3. `SIM-003`: implement scenario state and discharge-side loss semantics.
4. `SIM-004`: process all scenarios in one pass over cached aligned input.
5. `SIM-005`: handle irregular intervals and gap flags.
6. `SIM-006`: persist run metadata and summaries.
7. `SIM-007`: optionally persist step-level audit data.
8. `SIM-008`: document the final simulation contract.
