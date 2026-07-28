# User Stories: Autonomy Reporting

## Epic

As a command-line user evaluating battery sizes, I want daily and monthly autonomy percentages so
that I can understand how often my home can run from solar production or battery storage without
importing power from the grid.

## Context

The existing simulation summary compares annual import/export totals across battery capacities. That
is useful for energy accounting, but it does not answer a time-based independence question:

- A battery can reduce imported kWh while still requiring grid import during many intervals.
- A battery can add useful overnight or seasonal autonomy even when annual import savings are small.
- Daily and monthly views make seasonal behavior visible without requiring a spreadsheet.
- Autonomy depends on interval-level simulated import after battery dispatch, so summary-only
  simulation runs cannot answer this accurately.

For this epic, autonomy should mean the share of valid simulated time where post-simulation grid
import is effectively zero. Direct solar self-use is already represented in the P1 import/export
inputs, and battery discharge is represented by the scenario's simulated import. Therefore an
interval is autonomous when the selected scenario's `simulation_step.import_kwh` is below a small
zero threshold.

The implementation must preserve and reuse existing ingested and simulated data. The upstream
InfluxDB source has rolled over and no longer provides the same historical interval that was
available on 2026-06-30, so autonomy reporting must not require refetching that interval from the
source database.

## Stories

### Story 1: Report Daily Autonomy

As a user, I want a daily autonomy report so that I can see which days were mostly independent from
the grid.

Acceptance criteria:

- `hass-reader autonomy --bucket day` reports one row per local day and battery scenario.
- The command selects the latest completed simulation run by default.
- A `--run-id` option selects a specific simulation run.
- Rows include scenario name, capacity, day, autonomy percentage, autonomous hours, valid hours, and
  imported kWh.
- Daily buckets use the configured reporting timezone.
- Values are rounded consistently and labeled with units.

### Story 2: Report Monthly Autonomy

As a user, I want a monthly autonomy report so that I can compare summer, winter, and shoulder-season
behavior.

Acceptance criteria:

- `hass-reader autonomy --bucket month` reports one row per local month and battery scenario.
- Monthly buckets are calendar months in the configured reporting timezone.
- Rows include scenario name, capacity, month, autonomy percentage, autonomous hours, valid hours,
  and imported kWh.
- Partial first and last months respect the simulation run's half-open query interval.
- Output remains readable in a normal terminal width.

### Story 3: Define Autonomy as Time-Weighted Zero Import

As a user, I want autonomy to represent time without grid import so that the percentage matches the
plain-language idea of being independent from the grid.

Acceptance criteria:

- Autonomy is calculated as `autonomous_duration / valid_simulated_duration * 100`.
- An interval is autonomous when simulated import is less than or equal to a configurable threshold.
- The default threshold should be well below the house's observed idle load of around 300 W, for
  example 30 W.
- Export during an interval does not make that interval non-autonomous.
- The calculation does not use `1 - imported_kwh / baseline_imported_kwh` as the autonomy
  definition.
- The implementation can report imported kWh alongside autonomy percentage as a separate context
  value.

### Story 4: Require Interval Step Data

As a user, I want clear guidance when autonomy cannot be calculated so that I know when to rerun the
simulation with detailed output.

Acceptance criteria:

- Daily and monthly autonomy reports require `simulation_step` data.
- Running autonomy reporting against a summary-only run fails with a focused message.
- The message explains that `simulate --store-steps` is required.
- The command does not silently produce incomplete or empty autonomy percentages.
- The command exits non-zero for missing step data.
- If step data is missing for an existing historical run, the guidance must not assume the original
  source interval can still be refetched from InfluxDB.

### Story 5: Export Autonomy Buckets

As a user, I want CSV exports of daily and monthly autonomy so that I can plot or compare results
outside the terminal.

Acceptance criteria:

- `--output PATH` writes one row per bucket and selected battery scenario.
- CSV columns use stable snake_case names.
- Existing files are overwritten only with an explicit option.
- Exported rows include run ID, scenario name, capacity, efficiency, bucket kind, bucket start/end,
  autonomy percentage, autonomous hours, valid hours, non-autonomous hours, and imported kWh.
- `--numbersapp` formats timestamps consistently with existing CSV reports.

### Story 6: Share Reporting Conventions with SoC Reports

As a maintainer, I want autonomy reporting to reuse the existing reporting patterns so that the CLI
does not grow duplicate concepts.

Acceptance criteria:

- Autonomy reporting uses the same run resolution behavior as `report` and `soc`.
- Battery scenario selection follows the same selector behavior as SoC reporting.
- Date range parsing, timezone handling, and half-open intervals match existing report commands.
- Missing database, invalid run ID, invalid bucket option, and invalid time range errors are reported
  without tracebacks.

## Open Questions

- Should the no-battery baseline be included as a comparison row in terminal and CSV output, or
  should autonomy output include only configured battery scenarios?

## Notes and Suggestions

- This should probably use a new `autonomy_reporting.py` read model rather than expanding the annual
  summary model in `reporting.py`.
- The implementation can initially infer interval end timestamps with SQL `LEAD(timestamp_utc)` per
  scenario and cap the final interval at `simulation_run.query_end_utc`.
- Intervals crossing day or month boundaries should be split and distributed proportionally across
  the affected buckets.
- For long-term accuracy and simpler bucket splitting, consider adding `end_utc` or
  `duration_seconds` to `SimulationStepRecord` and `simulation_step`.
- On-demand rollup generation can follow the existing `soc` command pattern. A persisted
  `simulation_autonomy_rollup` table can be added later if raw step scans become too slow.
- The terminal report should include imported kWh next to autonomy percentage, because the two values
  answer different questions and are easy to misread in isolation.
