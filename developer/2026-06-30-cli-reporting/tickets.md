# Tickets: CLI Reporting

## Ticket ID Format

Use `REP-NNN` for tickets in this epic.

## TDD Approach

Use vertical TDD slices through public command-line behavior. For each ticket:

1. Add one behavior-focused test that calls the CLI through `hass_reader.cli.main`.
2. Confirm the test fails for the expected reason.
3. Implement the smallest useful reporting behavior.
4. Run the focused test, then the full suite.
5. Refactor only while green.

Prefer tests that create temporary ingestion and simulation SQLite databases and assert observable
stdout, stderr, exit codes, or exported file content. Avoid tests coupled to private helper
functions.

## Proposed CLI Shape

Reporting should work both immediately after a simulation and against an existing simulation
database.

Suggested interfaces:

```sh
uv run hass-reader simulate --batteries 5,10 --efficiency 0.9
uv run hass-reader report
uv run hass-reader report --run-id 12
uv run hass-reader report --output results.csv --overwrite
```

The `simulate` command should continue to write simulation runs to
`./var/hass-reader-simulation.sqlite3`. It may print the same compact summary as `report` after a
successful run, but the core reporting behavior should be available without rerunning simulation.

## Report Data Contract

Reports should use:

- `simulation_run` for run ID, status, source ingestion database, and UTC query range.
- `simulation_scenario` for baseline, battery capacities, and efficiency.
- `simulation_summary` for imported, exported, charged, discharged, losses, and ending SoC.
- `metric_source` from the ingestion database for grid import, grid export, and solar production
  mapping.
- `ingestion_run` from the ingestion database for Influx URL, database name, local timezone, and
  queried time range when available.

Derived reporting values:

- `avoided_import_kwh = baseline.imported_kwh - scenario.imported_kwh`
- `avoided_export_kwh = baseline.exported_kwh - scenario.exported_kwh`

The baseline row should be included in human-readable output. CSV output should write one row per
battery capacity and exclude the baseline as a standalone row unless a later ticket explicitly adds
an option for it.

## REP-001: Read Completed Simulation Runs for Reporting

Status: Done

Source stories: Story 1, Human Summary; Story 2, CSV Output

Goal: Add a reporting read model that loads a completed simulation run and its scenario summaries
from the simulation SQLite database.

Tasks:

- Add a reporting module that opens the simulation database.
- Load the latest completed run by default.
- Allow selecting a completed run by `--run-id`.
- Load baseline and battery scenario summaries for that run.
- Calculate avoided import and avoided export relative to the baseline.
- Return clear domain errors for a missing database, missing run, failed run, or missing baseline.

Acceptance criteria:

- A test can create a temporary simulation database and report the latest completed run.
- A selected `--run-id` reports that run rather than the latest run.
- Failed or incomplete runs are not silently reported as successful results.
- Missing baseline data fails with a focused error written to stderr through the CLI.

Execution result:

- Implemented in `src/hass_reader/reporting.py`.
- Covered by `tests/test_reporting.py`.
- Committed with the `REP-001` implementation.

## REP-002: Add Compact Human Summary Output

Status: Done

Source story: Story 1, Human Summary

Goal: Provide a compact terminal summary that lets a user compare battery capacities quickly.

Tasks:

- Add a `report` command that prints a human-readable summary for the latest completed run.
- Include baseline import and export in kWh.
- Include one line or table row per battery capacity.
- Include simulated import, simulated export, avoided import, and avoided export.
- Round values consistently and label all energy values with `kWh`.
- After `simulate` completes successfully, print the same summary for the newly created run.

Acceptance criteria:

- `uv run hass-reader report` exits `0` and prints baseline import/export.
- Report output includes each configured battery capacity.
- Report output includes simulated import/export and avoided import/export with units.
- The summary is stable enough for a user to compare 5 kWh, 10 kWh, 30 kWh, and 35 kWh runs.
- Tests assert public CLI output, not table-rendering internals.

Execution result:

- Implemented `report` command and compact summary formatting in `src/hass_reader/cli.py` and
  `src/hass_reader/reporting.py`.
- Successful real `simulate` runs print the same compact summary for the newly created run.
- Covered by `tests/test_cli.py`.
- Committed with the `REP-002` implementation.

## REP-003: Export Simulation Summary as CSV

Status: Done

Source story: Story 2, CSV Output

Goal: Write machine-readable simulation summaries for spreadsheet analysis.

Tasks:

- Add `--output PATH` to the `report` command.
- Add `--overwrite` to permit replacing an existing output file.
- Write one CSV row per battery scenario.
- Use stable snake_case column names.
- Include run metadata and scenario summary values in each row.
- Include derived avoided import and avoided export columns.

Suggested CSV columns:

```text
run_id,query_start_utc,query_end_utc,scenario_name,capacity_kwh,efficiency,
imported_kwh,exported_kwh,charged_kwh,discharged_kwh,losses_kwh,ending_soc_kwh,
avoided_import_kwh,avoided_export_kwh
```

Acceptance criteria:

- `uv run hass-reader report --output results.csv --overwrite` writes a CSV file.
- Existing output files are not overwritten unless `--overwrite` is present.
- CSV headers are stable snake_case names.
- CSV contains one row per configured battery capacity.
- File-write failures exit non-zero and print errors to stderr.

Execution result:

- Added `--output` and `--overwrite` to `hass-reader report`.
- Implemented CSV export in `src/hass_reader/reporting.py`.
- Existing files are protected unless `--overwrite` is provided.
- Covered by `tests/test_cli.py`.
- Committed with the `REP-003` implementation.

## REP-004: Include Metric Mapping and Source Metadata

Status: Done

Source story: Story 3, Show Metric Mapping

Goal: Show enough source metadata for a user to audit which Home Assistant entities and Influx
database produced the report.

Tasks:

- Read the ingestion database path from `simulation_run.ingestion_db_path`.
- Load primary `metric_source` rows for grid import, grid export, and solar production.
- Load the relevant or latest `ingestion_run` metadata for Influx URL, database name, local
  timezone, and query bounds.
- Add metric mapping details to human-readable reports.
- Add metric mapping and source metadata columns to CSV output.
- Identify non-default mappings by comparing loaded metric sources to seeded defaults.

Acceptance criteria:

- Reports include grid import, grid export, and solar production entity IDs.
- Reports include Influx URL, Influx database name, and queried time range.
- Reports identify mappings that differ from the defaults documented in `AGENTS.md`.
- Missing ingestion metadata does not crash summary reporting, but it is clearly marked as
  unavailable.

Execution result:

- Reports now load source metadata from the ingestion database recorded by `simulation_run`.
- Human output includes Influx source details and metric mapping status.
- CSV output includes source and metric mapping columns.
- Missing or unreadable ingestion metadata is reported as unavailable without failing summaries.
- Covered by `tests/test_cli.py`.
- Committed with the `REP-004` implementation.

## REP-005: Harden Reporting Exit Codes and Error Streams

Status: Not Started

Source story: Story 4, Exit Codes

Goal: Make reporting commands reliable for shell scripts and automation.

Tasks:

- Ensure successful `report` and `simulate` runs exit with code `0`.
- Ensure validation failures exit non-zero before opening databases.
- Ensure missing database, missing run, failed run, CSV overwrite refusal, and parsing failures exit
  non-zero.
- Write error messages to stderr without Python tracebacks.
- Add integration-style CLI tests for the most important failure modes.

Acceptance criteria:

- Successful reporting exits `0`.
- User-input validation errors use exit code `2` where practical.
- Runtime failures use exit code `1`.
- All error messages are written to stderr.
- Tests verify no traceback appears for expected user-facing failures.

## Suggested Execution Order

1. `REP-001`: Establish the reporting read model and error behavior.
2. `REP-002`: Add the human summary and wire it into `report` and successful `simulate`.
3. `REP-003`: Add CSV export with overwrite protection.
4. `REP-004`: Enrich reports with metric mapping and source metadata.
5. `REP-005`: Finish by tightening exit-code coverage across the reporting surface.
