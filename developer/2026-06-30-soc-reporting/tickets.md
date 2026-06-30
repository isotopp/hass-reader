# Tickets: State-of-Charge Reporting

## Ticket ID Format

Use `SOC-NNN` for tickets in this epic.

## TDD Approach

Use vertical TDD slices through public behavior:

1. Add one behavior-focused test through `hass_reader.cli.main` or a public reporting API.
2. Confirm the test fails for the expected reason.
3. Implement the smallest useful behavior.
4. Run the focused test, then the full suite.
5. Commit the ticket before starting the next one.

Prefer temporary SQLite databases with small step datasets. Avoid tests that require a real
multi-million-row simulation database.

## Proposed CLI Shape

Suggested commands:

```sh
uv run hass-reader soc --run-id 2 --batteries 10,30,35 --start 2026-01-01 --end 2026-02-01
uv run hass-reader soc --run-id 2 --batteries 10,30,35 --bucket day
uv run hass-reader soc --run-id 2 --batteries 10,30,35 --bucket day --output soc.csv --overwrite
uv run hass-reader soc-rollup --run-id 2 --bucket hour
uv run hass-reader soc-rollup --run-id 2 --bucket day
```

The exact command names can change during implementation, but the implementation should preserve
these capabilities.

## Proposed Rollup Schema

Add rollup tables to the simulation SQLite database rather than repeatedly querying raw
`simulation_step` rows.

```sql
CREATE TABLE simulation_soc_rollup (
    simulation_run_id INTEGER NOT NULL REFERENCES simulation_run(id),
    simulation_scenario_id INTEGER NOT NULL REFERENCES simulation_scenario(id),
    bucket_kind TEXT NOT NULL,
    bucket_start_utc TEXT NOT NULL,
    bucket_end_utc TEXT NOT NULL,
    min_soc_kwh REAL NOT NULL,
    avg_soc_kwh REAL NOT NULL,
    max_soc_kwh REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (
        simulation_scenario_id,
        bucket_kind,
        bucket_start_utc
    )
);
```

Indexes:

```sql
CREATE INDEX idx_simulation_soc_rollup_run_bucket
    ON simulation_soc_rollup(simulation_run_id, bucket_kind, bucket_start_utc);
```

## SOC-001: Validate SoC Reporting Inputs

Status: Done

Source stories: Story 1, Select Run, Battery Sizes, and Interval; Story 5, Step Storage Guidance

Goal: Add CLI parsing and validation for state-of-charge reporting without rendering charts yet.

Tasks:

- Add a `soc` command.
- Accept `--simulation-db`, `--run-id`, `--batteries`, `--start`, `--end`, `--timezone`, and
  `--bucket`.
- Validate bucket values such as `hour` and `day`.
- Validate that `--start < --end`.
- Validate battery CSV input before opening the simulation database.
- Return clear stderr errors without tracebacks.

Acceptance criteria:

- Invalid bucket input exits non-zero before database access.
- Invalid battery CSV input exits non-zero before database access.
- Invalid date ranges exit non-zero before database access.
- Valid inputs reach an explicit "not implemented yet" or empty read-model placeholder during this
  ticket only.

Execution result:

- Added the `soc` command with `--simulation-db`, `--run-id`, `--batteries`, `--start`, `--end`,
  `--timezone`, and `--bucket`.
- Added validation for bucket kind, run ID, battery selectors, and date ranges before database
  access.
- Valid inputs reach an explicit placeholder until the read model is implemented.
- Covered by `tests/test_cli.py`.
- Committed with the `SOC-001` implementation.

## SOC-002: Detect Step Availability for a Simulation Run

Status: Done

Source story: Story 5, Step Storage Guidance

Goal: Tell users when a simulation run cannot support SoC reporting because it lacks step data.

Tasks:

- Add a public read-model function that checks whether selected run/scenarios have step rows.
- Resolve latest completed run by default, matching `hass-reader report`.
- Resolve selected battery capacities to scenario rows.
- Fail clearly if a selected scenario has no step rows.

Acceptance criteria:

- A run with no `simulation_step` rows produces a focused error.
- The error says `simulate --store-steps` is required for raw SoC reporting.
- A run with step data passes the availability check.
- Tests create small temporary simulation databases.

Execution result:

- Added `src/hass_reader/soc_reporting.py` with run/scenario resolution and step availability
  checks.
- `hass-reader soc` now reports available step data for selected scenarios.
- Summary-only runs fail with guidance to rerun simulation using `--store-steps`.
- Covered by `tests/test_soc_reporting.py` and `tests/test_cli.py`.
- Committed with the `SOC-002` implementation.

## SOC-003: Build Hourly and Daily SoC Rollups

Status: Done

Source story: Story 2, Build Efficient SoC Rollups

Goal: Materialize efficient state-of-charge rollups in the simulation SQLite database.

Tasks:

- Add `simulation_soc_rollup` schema migration or idempotent table creation.
- Implement rollup generation from `simulation_step`.
- Support `hour` and `day` bucket kinds.
- Store min, average, max, and sample count for each selected scenario and bucket.
- Make rollup generation idempotent for a run/scenario/bucket.

Acceptance criteria:

- Generating the same rollup twice does not duplicate rows.
- Hourly buckets group timestamps by UTC hour.
- Daily buckets group timestamps by reporting timezone day, then store UTC bucket bounds.
- Tests verify min, average, max, and sample count from small fixture data.

Execution result:

- Added `simulation_soc_rollup` schema creation to the simulation SQLite database.
- Added `generate_soc_rollups` for `hour` and `day` bucket kinds.
- Rollup generation stores min, average, max, and sample count and is idempotent.
- Covered by `tests/test_soc_reporting.py`.
- Committed with the `SOC-003` implementation.

## SOC-004: Read SoC Rollups for Selected Intervals

Status: Not Started

Source stories: Story 1, Select Run, Battery Sizes, and Interval; Story 2, Build Efficient SoC
Rollups

Goal: Load rollup rows for selected scenarios and intervals without scanning raw steps.

Tasks:

- Add a public read-model for SoC rollups.
- Select latest completed run or explicit `--run-id`.
- Filter by selected batteries.
- Filter by half-open interval: `start <= bucket_start < end`.
- Return scenario capacity, efficiency, bucket bounds, and SoC statistics.

Acceptance criteria:

- Tests prove selected battery filters work.
- Tests prove interval filtering is half-open.
- Missing rollup rows fail with a clear instruction to run rollup generation.
- The raw `simulation_step` table is not queried by the read path when rollups exist.

## SOC-005: Render Terminal SoC Charts

Status: Not Started

Source story: Story 3, Terminal SoC Chart

Goal: Render readable terminal state-of-charge charts for selected batteries and intervals.

Tasks:

- Choose a terminal rendering dependency, preferably `plotext` for charts or `rich` for custom
  readable bars.
- Add a renderer that plots selected scenarios over time.
- Show capacity and efficiency in a legend or adjacent table.
- Keep output readable at normal terminal widths.
- Provide graceful fallback text if the terminal is too narrow.

Acceptance criteria:

- `hass-reader soc ...` prints a chart for selected scenarios.
- Output distinguishes each battery capacity.
- Empty/full behavior is visually obvious for small test fixtures.
- Tests assert stable textual markers rather than brittle full chart art.

## SOC-006: Render Daily Min/Max/Average Summary

Status: Not Started

Source story: Story 4, Daily Min/Max/Average Summary

Goal: Provide a compact daily summary that identifies when selected batteries empty or fill.

Tasks:

- Add a daily summary view using day rollups.
- Include min, average, max, and sample count per day and scenario.
- Mark days that reached `0 kWh`.
- Mark days that reached scenario capacity.
- Keep formatting readable in the terminal.

Acceptance criteria:

- Daily summaries include min/avg/max SoC in kWh.
- Empty and full days are explicitly marked.
- Multiple selected battery capacities can be compared.
- Tests use small rollup fixtures with known empty/full days.

## SOC-007: Export SoC Rollups as CSV

Status: Not Started

Source story: Story 6, Export for External Plotting

Goal: Export state-of-charge rollups for external plotting.

Tasks:

- Add `--output PATH` and `--overwrite` to the SoC report command.
- Write one CSV row per scenario and bucket.
- Use stable snake_case column names.
- Protect existing files unless `--overwrite` is present.

Suggested CSV columns:

```text
run_id,scenario_name,capacity_kwh,efficiency,bucket_kind,bucket_start_utc,
bucket_end_utc,min_soc_kwh,avg_soc_kwh,max_soc_kwh,sample_count
```

Acceptance criteria:

- CSV export writes selected scenarios and intervals only.
- Existing files are not overwritten without `--overwrite`.
- Headers are stable snake_case names.
- File errors go to stderr without tracebacks.

## Suggested Execution Order

1. `SOC-001`: CLI input surface and validation.
2. `SOC-002`: Step availability checks and clear user guidance.
3. `SOC-003`: Rollup schema and generation.
4. `SOC-004`: Rollup read model for selected intervals and batteries.
5. `SOC-006`: Daily summary first, because it is stable and cheap to test.
6. `SOC-005`: Terminal chart rendering after the data model is reliable.
7. `SOC-007`: CSV export once the read model is final.
