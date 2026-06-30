# User Stories: State-of-Charge Reporting

## Epic

As a command-line user evaluating battery sizes, I want state-of-charge reports over selected time
ranges so that I can see when batteries stay full, stay empty, or provide useful independence during
expensive heating periods.

## Context

The existing simulation summary is useful for annual import/export totals, but it hides operational
behavior:

- Extra capacity can have small annual import savings while still adding several hours of autonomy.
- Summer behavior should show batteries filling and remaining high overnight.
- Winter behavior should show batteries staying low or failing to refill.
- `--store-steps` can produce tens of millions of rows, so plotting should use rollups rather than
  scanning raw steps repeatedly.

The initial visual goal is a terminal-friendly report, inspired by Home Assistant energy visuals,
that can compare selected battery capacities across a selected interval.

## Stories

### Story 1: Select Run, Battery Sizes, and Interval

As a user, I want to select a simulation run, battery capacities, and date range so that I can focus
on a winter week, summer week, or shoulder-season interval.

Acceptance criteria:

- A CLI command can select the latest completed run by default.
- A `--run-id` option selects a specific simulation run.
- A battery selection option accepts a CSV of scenario names or capacities.
- `--start`, `--end`, and `--timezone` limit the reported interval.
- Validation errors are written to stderr without tracebacks.

### Story 2: Build Efficient SoC Rollups

As a user, I want state-of-charge reports to be fast even when step storage is large so that I can
explore multiple periods interactively.

Acceptance criteria:

- Reports do not repeatedly scan tens of millions of raw step rows when a rollup is available.
- Rollups can be generated for hourly and daily buckets.
- Rollups include min, average, max, and sample count for `soc_kwh`.
- Rollups are tied to a simulation run and scenario.
- Missing rollups are generated on demand or reported with a clear instruction.

### Story 3: Terminal SoC Chart

As a user, I want a readable terminal chart of battery state of charge so that I can see seasonal
patterns without opening a browser or spreadsheet.

Acceptance criteria:

- A CLI report renders selected battery capacities as terminal charts.
- The chart shows `soc_kwh` against time for the selected interval.
- The report indicates each battery's capacity and efficiency.
- The output remains readable in a normal terminal width.
- The chart makes it obvious when a battery stays full or empty.

### Story 4: Daily Min/Max/Average Summary

As a user, I want daily min/average/max state-of-charge summaries so that I can compare how often
different battery sizes empty or fill.

Acceptance criteria:

- Daily summaries include min, average, and max SoC per selected scenario.
- The report flags days where a battery reached empty.
- The report flags days where a battery reached full.
- The summary can be exported as CSV.
- Values are rounded consistently and labeled in kWh.

### Story 5: Step Storage Guidance

As a user, I want the CLI to tell me when SoC reporting needs step data so that I do not accidentally
run reports against summary-only simulations.

Acceptance criteria:

- Running a SoC report against a run without step data fails with a focused message.
- The message explains that `simulate --store-steps` or a future rollup mode is required.
- The report command does not silently produce empty charts.
- The report command exits non-zero for missing step data.

### Story 6: Export for External Plotting

As a user, I want CSV exports of SoC rollups so that I can plot the same data in spreadsheets or
other tools.

Acceptance criteria:

- A CSV export writes one row per bucket and selected scenario.
- CSV columns use stable snake_case names.
- Existing files are overwritten only with an explicit option.
- Exported rows include run ID, scenario name, capacity, bucket start/end, min SoC, average SoC,
  max SoC, and sample count.
