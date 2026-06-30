# User Stories: CLI Reporting

## Epic

As a command-line user, I want clear simulation output and machine-readable exports so that I can
inspect results manually and reuse them in spreadsheets.

## Stories

### Story 1: Human Summary

As a user, I want a compact terminal summary so that I can quickly compare battery sizes.

Acceptance criteria:

- Output includes baseline import and export.
- Output includes simulated import, simulated export, avoided import, and avoided export.
- Values are rounded consistently and labeled with units.

### Story 2: CSV Output

As a user, I want optional CSV output so that I can analyze results elsewhere.

Acceptance criteria:

- `--output results.csv` writes one row per battery capacity.
- CSV columns have stable snake_case names.
- Existing files are overwritten only with an explicit option.

### Story 3: Show Metric Mapping

As an operator, I want reports to show the selected entities so that I can verify the run used the
expected P1 and Zonneplan data.

Acceptance criteria:

- Reports include grid import, grid export, and solar production entity IDs.
- Reports include the database URL, database name, and queried time range.
- Reports identify any configured non-default mappings.

### Story 4: Exit Codes

As an automation user, I want reliable exit codes so that scripts can detect failed runs.

Acceptance criteria:

- Successful discovery and simulation exit with code `0`.
- Validation, connection, query, and parsing failures exit non-zero.
- Error messages are written to stderr.
