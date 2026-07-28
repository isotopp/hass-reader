# Autonomy Reporting

Autonomy reporting answers a time-based question:

How often can the home run from direct solar production or battery storage without importing power
from the grid?

It reads existing simulation results from the simulation SQLite database. It does not query
InfluxDB and does not refetch historical source data.

Related contracts:

- [Metric ingestion cache](metric-ingestion.md)
- [Battery simulation](battery-simulation.md)

## Requirements

Autonomy reports require simulation step data.

Create or reuse a simulation run made with `--store-steps`:

```sh
uv run hass-reader simulate \
  --batteries 5,10,30 \
  --efficiency 0.9 \
  --store-steps
```

Summary-only simulation runs can produce `hass-reader report`, but they cannot produce autonomy
reports because monthly and daily autonomy depend on interval-level `simulation_step` rows.

If `uv run hass-reader soc` works for the same run, that run has the step data that autonomy needs.

## Commands

Monthly autonomy for the latest completed run:

```sh
uv run hass-reader autonomy --bucket month
```

Daily autonomy:

```sh
uv run hass-reader autonomy --bucket day
```

Select a specific run:

```sh
uv run hass-reader autonomy --run-id 1 --bucket month
```

Select battery scenarios while keeping the baseline comparison row:

```sh
uv run hass-reader autonomy --bucket month --batteries 5,10,30
```

Change the autonomy threshold:

```sh
uv run hass-reader autonomy --bucket month --threshold-watts 50
```

Limit the reported interval:

```sh
uv run hass-reader autonomy \
  --bucket day \
  --start 2026-03-01 \
  --end 2026-04-01 \
  --timezone Europe/Amsterdam
```

Write CSV output:

```sh
uv run hass-reader autonomy \
  --bucket month \
  --output autonomy.csv \
  --overwrite
```

Write CSV output for Apple Numbers:

```sh
uv run hass-reader autonomy \
  --bucket month \
  --output autonomy.csv \
  --numbersapp \
  --overwrite
```

## Autonomy Definition

Autonomy is a time-weighted percentage:

```text
autonomy_percent = autonomous_duration / valid_simulated_duration * 100
```

An interval is autonomous when its simulated average grid import power is less than or equal to the
configured threshold.

The default threshold is:

```text
30 W
```

That default is intentionally well below the observed roughly `300 W` night idle load. It treats
small measurement noise as zero import without counting normal household demand as autonomous.

Autonomy is not calculated as energy reduction. This is not the definition:

```text
1 - simulated_import_kwh / baseline_import_kwh
```

Energy reduction and autonomy answer different questions. A battery can reduce a lot of imported
energy while still importing briefly every day, or it can add many autonomous hours without removing
all imported kWh.

## Data Source

Autonomy reads:

- `simulation_run` for run status and query bounds.
- `simulation_scenario` for baseline and battery scenario metadata.
- `simulation_step` for simulated import per interval.

Each stored step has a start timestamp. The report infers each interval end from the next step
timestamp for the same scenario. The final interval is capped at `simulation_run.query_end_utc`.

Intervals that cross a day or month boundary are split proportionally across the affected local-time
buckets.

## Buckets

Use `--bucket day` for local calendar days.

Use `--bucket month` for local calendar months.

Bucket boundaries use `--timezone`, defaulting to Europe/Amsterdam. Output stores bucket boundaries
internally as UTC timestamps, but terminal labels are compact:

- day labels: `YYYY-MM-DD`
- month labels: `YYYY-MM`

The date range is half-open:

```text
start <= time < end
```

## Report Rows

Example terminal output:

```text
Autonomy report run 1 bucket=month threshold=30 W
Bucket      Scenario  Cap kWh  Autonomy  Auto h  Valid h  Import kWh
2026-03    baseline    0.00     42.55%  306.37   720.00      333.81
2026-03    5kWh        5.00     73.67%  530.39   720.00      212.06
2026-03    10kWh      10.00     83.62%  602.04   720.00      154.83
2026-03    30kWh      30.00     91.52%  658.91   720.00       71.61
```

Columns:

| Column | Meaning |
| --- | --- |
| `Bucket` | Local day or month label. |
| `Scenario` | `baseline` or the battery scenario name. |
| `Cap kWh` | Battery capacity. Baseline is `0.00`. |
| `Autonomy` | Percentage of valid simulated time with import at or below threshold. |
| `Auto h` | Autonomous hours in the bucket. |
| `Valid h` | Total simulated hours represented in the bucket. |
| `Import kWh` | Simulated imported energy in the bucket. |

The no-battery baseline is included by default as a comparison row. When `--batteries` is used,
the selected battery rows are filtered, but the baseline row remains for context.

## CSV Output

CSV output writes one row per bucket and scenario.

Columns:

```text
run_id,scenario_name,capacity_kwh,efficiency,bucket_kind,bucket_start_utc,
bucket_end_utc,autonomy_percent,autonomous_hours,valid_hours,
non_autonomous_hours,imported_kwh,threshold_watts
```

By default, timestamps are ISO UTC strings:

```text
2026-03-01T00:00:00Z
```

With `--numbersapp`, timestamps are formatted for Apple Numbers:

```text
2026-03-01 00:00:00
```

Existing CSV files are protected unless `--overwrite` is provided.

## Interpreting Results

If larger batteries have the same autonomy as smaller batteries, capacity is probably not the
bottleneck for that period. There may not be enough surplus solar production to charge the larger
battery.

If imported kWh falls but autonomy changes little, the battery is reducing energy import but not
eliminating import intervals.

If autonomy rises while imported kWh remains non-zero, the remaining grid imports are concentrated
in fewer intervals.
