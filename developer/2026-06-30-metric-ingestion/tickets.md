# Tickets: Metric Ingestion

## Ticket ID Format

Use `ING-NNN` for tickets in this epic.

## Persistence Design

Use a local SQLite database to cache InfluxDB reads and normalized samples so repeated simulation
runs do not download the same target-year data again.

Default database path:

```text
./var/hass-reader.sqlite3
```

The path should be overridable by CLI option and environment variable.

### Tables

```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE metric_source (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    measurement TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    expected_unit TEXT NOT NULL,
    normalize_to_unit TEXT NOT NULL,
    is_primary INTEGER NOT NULL,
    UNIQUE (role, measurement, entity_id)
);

CREATE TABLE ingestion_run (
    id INTEGER PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    influx_url TEXT NOT NULL,
    influx_database TEXT NOT NULL,
    local_timezone TEXT NOT NULL,
    query_start_utc TEXT NOT NULL,
    query_end_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE fetch_window (
    id INTEGER PRIMARY KEY,
    metric_source_id INTEGER NOT NULL REFERENCES metric_source(id),
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_run(id),
    query_start_utc TEXT NOT NULL,
    query_end_utc TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    first_sample_utc TEXT,
    last_sample_utc TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    UNIQUE (metric_source_id, query_start_utc, query_end_utc)
);

CREATE TABLE raw_sample (
    metric_source_id INTEGER NOT NULL REFERENCES metric_source(id),
    timestamp_utc TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    friendly_name TEXT,
    source TEXT,
    state_class TEXT,
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_run(id),
    PRIMARY KEY (metric_source_id, timestamp_utc)
);

CREATE TABLE normalized_sample (
    metric_source_id INTEGER NOT NULL REFERENCES metric_source(id),
    timestamp_utc TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_run(id),
    PRIMARY KEY (metric_source_id, timestamp_utc)
);

CREATE TABLE aligned_power_sample (
    timestamp_utc TEXT PRIMARY KEY,
    grid_import_kw REAL NOT NULL,
    grid_export_kw REAL NOT NULL,
    solar_production_kw REAL,
    import_source_sample_utc TEXT,
    export_source_sample_utc TEXT,
    solar_source_sample_utc TEXT,
    max_source_age_seconds INTEGER NOT NULL,
    gap_flags TEXT NOT NULL DEFAULT '',
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_run(id)
);
```

Indexes:

```sql
CREATE INDEX idx_raw_sample_metric_time ON raw_sample(metric_source_id, timestamp_utc);
CREATE INDEX idx_normalized_sample_metric_time
    ON normalized_sample(metric_source_id, timestamp_utc);
CREATE INDEX idx_aligned_power_sample_time ON aligned_power_sample(timestamp_utc);
```

### Seeded Metric Sources

Seed these primary metric sources from `docs/influx-metrics.md`:

| Role                     | Measurement     | Entity ID                                              | Expected Unit  | Normalized Unit  |
|--------------------------|-----------------|--------------------------------------------------------|----------------|------------------|
| `grid_import_power`      | `sensor__power` | `power_consumed`                                       | `kW`           | `kW`             |
| `grid_export_power`      | `sensor__power` | `power_produced`                                       | `kW`           | `kW`             |
| `solar_production_power` | `sensor__power` | `zonneplan_one_optimized_omvormer_last_measured_value` | `W`            | `kW`             |

Seed these optional cross-check or validation sources as non-primary rows:

- `connect_energiemeter_electricity_average`
- `connect_energiemeter_electricity_consumption`
- `connect_energiemeter_electricity_production`
- `energy_consumed_today`
- `energy_produced_today`
- `zonneplan_electricity_consumption_today`
- `zonneplan_electricity_returned_today`
- `zonneplan_yield_today`
- `zonneplan_one_optimized_omvormer_yield_total`

## ING-001: Create SQLite Cache Schema

Status: Ready

Source stories: Story 1, Query Time-Series Samples; Story 4, Preserve Source Metadata

Goal: Provide a local persistence layer that can store metric definitions, Influx fetch metadata,
raw samples, normalized samples, and aligned power samples.

Tasks:

- Add a SQLite module that opens `./var/hass-reader.sqlite3` by default.
- Allow the database path to be overridden by CLI option and environment variable.
- Create the schema from the persistence design above.
- Add idempotent migrations guarded by `schema_version`.
- Seed metric sources from `docs/influx-metrics.md`.

Acceptance criteria:

- Running initialization twice does not duplicate schema or seed rows.
- The schema records metric source metadata needed by reporting.
- `var/` is ignored by git.
- Tests create SQLite databases in temporary directories.

## ING-002: Build Influx Query Client

Status: Ready

Source story: Story 1, Query Time-Series Samples

Goal: Query InfluxDB 1.8 for configured metric sources and time ranges.

Tasks:

- Implement an InfluxDB 1.8 HTTP query client for `/query`.
- Build InfluxQL using measurement, `entity_id`, start time, and exclusive end time.
- Request `value`, `unit_of_measurement_str`, `friendly_name`, `source`, and `state_class_str`.
- Return samples in ascending timestamp order.
- Surface connection, HTTP, JSON, and InfluxDB error payloads as domain errors.

Acceptance criteria:

- Query construction uses half-open intervals: `time >= start` and `time < end`.
- The default range is Europe/Amsterdam `2025-07-01` inclusive to `2026-07-01` exclusive,
  converted to UTC.
- Empty result sets produce a focused error naming measurement, entity ID, and time range.
- Unit tests cover query construction without requiring a live InfluxDB.

## ING-003: Avoid Duplicate Downloads with Fetch Windows

Status: Ready

Source story: Story 1, Query Time-Series Samples

Goal: Use SQLite fetch metadata to skip InfluxDB reads that have already been cached.

Tasks:

- Before querying InfluxDB, check `fetch_window` for a completed window with the same metric source
  and exact UTC bounds.
- If the exact window exists, load cached `raw_sample` rows instead of querying InfluxDB.
- If no exact window exists, query InfluxDB and upsert raw samples.
- Record fetch status, sample count, first sample timestamp, and last sample timestamp.

Acceptance criteria:

- A second ingestion of the same source and time range performs no Influx query.
- Raw samples are upserted by `(metric_source_id, timestamp_utc)`.
- Failed fetch windows are recorded with an error message and do not block future retries.
- Tests prove cache-hit and cache-miss behavior through the public ingestion interface.

## ING-004: Normalize Units

Status: Ready

Source story: Story 2, Normalize Units

Goal: Normalize raw power samples to kW and energy samples to kWh at the ingestion boundary.

Tasks:

- Convert `W` to `kW` by dividing by `1000`.
- Pass `kW` through unchanged.
- Pass `kWh` through unchanged for energy validation sources.
- Reject unknown units with metric context.
- Store normalized values in `normalized_sample`.

Acceptance criteria:

- `1000 W` is stored as `1.0 kW`.
- `1.25 kW` is stored as `1.25 kW`.
- Unknown units fail ingestion before aligned samples are written.
- Errors include measurement, entity ID, original unit, and timestamp.

## ING-005: Align Primary Power Series

Status: Ready

Source story: Story 3, Merge Import and Export Series

Goal: Produce a single aligned power timeline for simulation from normalized primary power series.

Tasks:

- Use the union of primary grid import, grid export, and solar production timestamps as the initial
  timeline.
- Fill missing values from the previous known sample only within a configurable maximum gap.
- Represent grid import and grid export as non-negative kW.
- Store aligned rows in `aligned_power_sample`.
- Record source timestamps and gap flags for auditability.

Acceptance criteria:

- Each aligned row has `grid_import_kw`, `grid_export_kw`, and optional `solar_production_kw`.
- Rows with fill-forward source ages beyond the maximum gap are flagged or excluded according to
  configuration.
- The maximum source age is recorded in seconds.
- Tests cover exact matches, fill-forward within the gap, and gaps beyond the threshold.

## ING-006: Preserve Ingestion Metadata for Reporting

Status: Ready

Source story: Story 4, Preserve Source Metadata

Goal: Make ingestion runs auditable by downstream reporting without requiring direct Influx access.

Tasks:

- Record each ingestion run in `ingestion_run`.
- Record URL, database, timezone, UTC bounds, status, and errors.
- Record per-source sample counts in `fetch_window`.
- Provide a public read model that returns metric mapping and sample counts for a run.

Acceptance criteria:

- Reports can list measurement names, entity IDs, units, source kind, and sample counts from SQLite.
- Reports can distinguish primary Slimmelezer P1 inputs from Zonneplan cross-checks.
- Reports can show the local date range and UTC query range.
- Tests verify the metadata read model from a temporary SQLite database.

## ING-007: Add Metric Ingestion CLI Command

Status: Ready

Source stories: Story 1, Query Time-Series Samples; Story 4, Preserve Source Metadata

Goal: Expose ingestion through the command line so users can populate or refresh the local cache.

Tasks:

- Add an `ingest` command.
- Accept `--start`, `--end`, `--timezone`, `--db`, `--influx-url`, `--influx-database`, and
  `--force-refresh` options.
- Default to the target-year range documented in `docs/influx-access.md`.
- Print a compact summary of fetched versus cached sources.
- Exit non-zero on connection, query, normalization, or alignment failures.

Acceptance criteria:

- `uv run hass-reader ingest` uses the documented defaults.
- `--force-refresh` re-queries InfluxDB even when a completed fetch window exists.
- The command reports cached/fetched sample counts per source.
- Normal CLI failures do not print Python stack traces.

## ING-008: Document the Metric Ingestion Cache

Status: Ready

Source stories: Story 1 through Story 4

Goal: Document the SQLite cache contract for downstream epics.

Tasks:

- Create `docs/metric-ingestion.md`.
- Document the default SQLite path and override options.
- Document the tables that downstream epics may read.
- Document that battery simulation consumes `aligned_power_sample`.
- Document cache invalidation behavior and `--force-refresh`.

Acceptance criteria:

- Battery simulation has a clear pointer to the aligned data it should consume.
- CLI reporting has a clear pointer to the run metadata and metric mapping it should display.
- The document links back to `docs/influx-access.md` and `docs/influx-metrics.md`.

## Suggested Execution Order

1. `ING-001`: create the cache schema first so all later behavior has a durable target.
2. `ING-002`: implement the Influx client independently of caching.
3. `ING-003`: connect querying to cache-hit/cache-miss behavior.
4. `ING-004`: normalize units before downstream alignment.
5. `ING-005`: align the primary power series for simulation.
6. `ING-006`: expose metadata needed by reporting.
7. `ING-007`: add the user-facing ingestion command.
8. `ING-008`: document the final cache contract for follow-up epics.
