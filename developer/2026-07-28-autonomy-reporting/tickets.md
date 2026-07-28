# Tickets: Autonomy Reporting

## Ticket ID Format

Use `AUT-NNN` for tickets in this epic.

## TDD Approach

Use vertical TDD slices through public behavior:

1. Add one behavior-focused test through `hass_reader.cli.main` or a public autonomy reporting API.
2. Confirm the test fails for the expected reason.
3. Implement the smallest useful behavior.
4. Run the focused test, then the full suite.
5. Refactor only while green.

Prefer small temporary SQLite simulation databases with hand-built step data. Avoid tests that
require a live InfluxDB source or refetching historical data.

## Proposed CLI Shape

Autonomy should be a dedicated command, separate from the compact annual `report` summary.

Suggested commands:

```sh
uv run hass-reader autonomy --bucket day
uv run hass-reader autonomy --bucket month
uv run hass-reader autonomy --run-id 12 --bucket day --batteries 10,30,35
uv run hass-reader autonomy --bucket month --threshold-watts 30
uv run hass-reader autonomy --bucket day --output autonomy.csv --overwrite
```

The implementation must work from existing simulation SQLite data and must not require refetching
the original historical interval from InfluxDB.

## Autonomy Data Contract

Autonomy reports should use:

- `simulation_run` for run ID, status, query bounds, and the final interval cap.
- `simulation_scenario` for battery scenario names, capacities, efficiency, and baseline identity.
- `simulation_step` for per-interval simulated import and imported kWh.

Derived reporting values:

- `valid_seconds`: the total simulated interval duration assigned to a bucket.
- `autonomous_seconds`: interval duration assigned to a bucket where simulated import power is less
  than or equal to the configured threshold.
- `autonomy_percent = autonomous_seconds / valid_seconds * 100`.
- `non_autonomous_seconds = valid_seconds - autonomous_seconds`.
- `imported_kwh`: sum of simulated imported energy assigned to the bucket.

Interval end timestamps can initially be inferred with `LEAD(timestamp_utc)` per scenario and capped
by `simulation_run.query_end_utc`. Intervals that cross day or month boundaries should be split
proportionally across affected buckets.

## AUT-001: Validate Autonomy Command Inputs

Status: Done

Source stories: Story 1, Report Daily Autonomy; Story 2, Report Monthly Autonomy; Story 6, Share
Reporting Conventions with SoC Reports

Goal: Add CLI parsing and validation for the dedicated autonomy command without calculating
autonomy yet.

Tasks:

- Add an `autonomy` command.
- Accept `--simulation-db`, `--run-id`, `--batteries`, `--start`, `--end`, `--timezone`,
  `--bucket`, and `--threshold-watts`.
- Validate `--bucket` as `day` or `month`.
- Validate `--threshold-watts` as a non-negative number.
- Validate `--start < --end`.
- Validate battery CSV selectors before opening the simulation database.
- Return clear stderr errors without tracebacks.

Acceptance criteria:

- Invalid bucket input exits non-zero before database access.
- Invalid threshold input exits non-zero before database access.
- Invalid battery CSV input exits non-zero before database access.
- Invalid date ranges exit non-zero before database access.
- Valid inputs reach an explicit "not implemented yet" or empty read-model placeholder during this
  ticket only.

Execution result:

- Added the `autonomy` command with `--simulation-db`, `--run-id`, `--batteries`, `--start`,
  `--end`, `--timezone`, `--bucket`, and `--threshold-watts`.
- Added validation for bucket kind, run ID, battery selectors, threshold, and date ranges before
  database access.
- Valid inputs reach an explicit placeholder until the read model is implemented.
- Covered by `tests/test_cli.py`.
- Verified with `uv run pytest tests/test_cli.py -k autonomy` and `uv run pytest`.

## AUT-002: Detect Step Availability Without Refetching Source Data

Status: Proposed

Source story: Story 4, Require Interval Step Data

Goal: Tell users when autonomy cannot be calculated from the existing simulation database.

Tasks:

- Add a public read-model function that resolves the selected run and scenarios.
- Resolve the latest completed run by default.
- Resolve selected battery capacities or scenario names.
- Detect whether selected scenarios have `simulation_step` rows.
- Fail clearly when steps are missing.

Acceptance criteria:

- A run with no step rows produces a focused error.
- The error says `simulate --store-steps` is required for autonomy reporting.
- The error does not suggest refetching the original historical Influx interval.
- A run with step data passes the availability check.
- Tests use temporary SQLite databases only.

## AUT-003: Calculate Daily Autonomy from Step Intervals

Status: Proposed

Source stories: Story 1, Report Daily Autonomy; Story 3, Define Autonomy as Time-Weighted Zero
Import

Goal: Produce daily autonomy rows from stored simulation steps.

Tasks:

- Load ordered step rows for selected scenarios.
- Infer interval end timestamps with the next step timestamp and cap the final interval at
  `simulation_run.query_end_utc`.
- Convert `--threshold-watts` to an interval energy threshold.
- Group intervals into local-day buckets using the reporting timezone.
- Split intervals that cross day boundaries proportionally.
- Calculate autonomy percent, autonomous hours, valid hours, non-autonomous hours, and imported kWh.

Acceptance criteria:

- A test fixture with known autonomous and non-autonomous intervals reports the expected daily
  percentage.
- A low non-zero import below the watt threshold counts as autonomous.
- Export during an interval does not make it non-autonomous.
- Intervals crossing a local midnight are split across both days.
- The calculation is time-weighted, not energy-reduction weighted.

## AUT-004: Calculate Monthly Autonomy from Step Intervals

Status: Proposed

Source story: Story 2, Report Monthly Autonomy

Goal: Add calendar-month autonomy buckets using the same interval calculation path.

Tasks:

- Support `bucket_kind="month"` in the autonomy read model.
- Group buckets by local calendar month in the reporting timezone.
- Split intervals that cross month boundaries proportionally.
- Respect selected half-open date ranges.
- Preserve partial first and last months.

Acceptance criteria:

- `--bucket month` returns one row per selected scenario and local calendar month.
- A fixture crossing a month boundary distributes valid and autonomous duration correctly.
- Filtering with `--start` and `--end` is half-open.
- Partial months report only the selected interval's valid hours.

## AUT-005: Decide and Implement Baseline Inclusion

Status: Proposed

Source stories: Story 3, Define Autonomy as Time-Weighted Zero Import; Open Questions

Goal: Decide whether autonomy output should include the no-battery baseline and make terminal and
CSV behavior consistent.

Tasks:

- Decide whether baseline rows are included by default, excluded, or controlled by an option.
- Update the user story to remove the remaining open question.
- Add behavior-focused tests for the chosen behavior through the public autonomy read model.
- Implement the chosen behavior before terminal and CSV formatting are finalized.

Acceptance criteria:

- The no-battery baseline behavior is documented in `user-stories.md`.
- The autonomy read model follows the chosen baseline inclusion rule.
- Tests cover the chosen behavior.

## AUT-006: Render Terminal Autonomy Reports

Status: Proposed

Source stories: Story 1, Report Daily Autonomy; Story 2, Report Monthly Autonomy

Goal: Print readable daily and monthly autonomy summaries from the CLI.

Tasks:

- Add a formatter for autonomy reports.
- Include run ID and bucket kind in the output.
- Include bucket label, scenario name, capacity, autonomy percentage, autonomous hours, valid hours,
  and imported kWh.
- Keep lines readable in a normal terminal width.
- Wire successful `hass-reader autonomy` execution to the read model and formatter.

Acceptance criteria:

- `hass-reader autonomy --bucket day` exits `0` and prints daily autonomy rows.
- `hass-reader autonomy --bucket month` exits `0` and prints monthly autonomy rows.
- Output includes scenario names and battery capacities.
- Output labels percentages, hours, and kWh clearly.
- Tests assert stable textual markers rather than brittle full table art.

## AUT-007: Export Autonomy Buckets as CSV

Status: Proposed

Source story: Story 5, Export Autonomy Buckets

Goal: Export autonomy bucket rows for spreadsheet analysis.

Tasks:

- Add `--output PATH`, `--overwrite`, and `--numbersapp` handling to the autonomy command.
- Write one CSV row per bucket and selected scenario.
- Use stable snake_case column names.
- Protect existing files unless `--overwrite` is present.
- Format timestamps consistently with existing CSV reports when `--numbersapp` is used.

Suggested CSV columns:

```text
run_id,scenario_name,capacity_kwh,efficiency,bucket_kind,bucket_start_utc,
bucket_end_utc,autonomy_percent,autonomous_hours,valid_hours,
non_autonomous_hours,imported_kwh,threshold_watts
```

Acceptance criteria:

- `hass-reader autonomy --bucket day --output autonomy.csv --overwrite` writes a CSV file.
- Existing output files are not overwritten unless `--overwrite` is present.
- CSV headers are stable snake_case names.
- CSV contains one row per selected scenario and bucket.
- File-write failures exit non-zero and print errors to stderr.

## Suggested Execution Order

1. `AUT-001`: Establish CLI parsing and validation.
2. `AUT-002`: Establish run/scenario resolution and step availability without refetching.
3. `AUT-003`: Implement daily autonomy calculations.
4. `AUT-004`: Add monthly autonomy calculations.
5. `AUT-005`: Resolve and implement baseline inclusion.
6. `AUT-006`: Render terminal output.
7. `AUT-007`: Add CSV export.
