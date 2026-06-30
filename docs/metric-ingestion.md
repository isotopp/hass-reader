# Metric Ingestion Cache

Metric ingestion reads Home Assistant metrics from InfluxDB, stores raw samples locally, normalizes
units, and writes an aligned power timeline for battery simulation.

Related contracts:

- [InfluxDB access](influx-access.md)
- [InfluxDB metrics](influx-metrics.md)

## Command

Populate or refresh the local cache:

```sh
uv run hass-reader ingest
```

Useful options:

```sh
uv run hass-reader ingest \
  --start 2025-07-01 \
  --end 2026-07-01 \
  --timezone Europe/Amsterdam \
  --db ./var/hass-reader.sqlite3 \
  --influx-url http://192.168.1.10:8086 \
  --influx-database hass
```

Use `--force-refresh` to query InfluxDB even when a completed fetch window already exists.

## SQLite Location

Default database path:

```text
./var/hass-reader.sqlite3
```

Override order:

1. CLI option `--db`.
2. Environment variable `HASS_READER_SQLITE_DB`.
3. Default path above.

The `var/` directory is intentionally ignored by git.

## Cached Data Flow

1. `metric_source` stores the seeded metric mapping from `docs/influx-metrics.md`.
2. `ingestion_run` records each ingestion attempt.
3. `fetch_window` records per-source query windows and cache status.
4. `raw_sample` stores values exactly as returned by InfluxDB.
5. `normalized_sample` stores values normalized to `kW` or `kWh`.
6. `aligned_power_sample` stores the simulation-ready grid and solar timeline.

## Tables

### `metric_source`

Stores metric definitions and whether each source is primary.

Downstream reporting should read:

- `role`
- `measurement`
- `entity_id`
- `source_kind`
- `expected_unit`
- `normalize_to_unit`
- `is_primary`

Primary roles:

- `grid_import_power`
- `grid_export_power`
- `solar_production_power`

### `ingestion_run`

Stores run-level audit metadata:

- InfluxDB URL and database
- local timezone
- UTC query bounds
- status and error message

CLI reporting should use this table to show which data window produced a result.

### `fetch_window`

Stores cache status per metric and exact UTC interval.

A completed row with the same metric source and exact UTC bounds is a cache hit. Failed rows are
kept for audit but do not block retry.

### `raw_sample`

Stores raw samples keyed by `(metric_source_id, timestamp_utc)`.

Raw sample rows preserve:

- original value
- original unit
- friendly name
- source tag
- state class
- ingestion run ID

### `normalized_sample`

Stores normalized samples keyed by `(metric_source_id, timestamp_utc)`.

Power normalization:

- `kW` passes through unchanged.
- `W` is divided by `1000`.

Energy normalization:

- `kWh` passes through unchanged.

Unknown units fail ingestion before aligned power rows are written.

### `aligned_power_sample`

This is the primary table for battery simulation.

Columns used by simulation:

- `timestamp_utc`
- `grid_import_kw`
- `grid_export_kw`
- `solar_production_kw`

Audit columns:

- `import_source_sample_utc`
- `export_source_sample_utc`
- `solar_source_sample_utc`
- `max_source_age_seconds`
- `gap_flags`
- `ingestion_run_id`

The alignment step uses the union of primary source timestamps and fill-forwards previous values
only within the configured `--max-gap-seconds` window. Rows outside that window are retained with
zero or null substituted values and a `gap_flags` marker.

## Follow-Up Contracts

Battery simulation should read from `aligned_power_sample` and should not query InfluxDB directly.

CLI reporting should read `ingestion_run`, `metric_source`, `fetch_window`, and
`aligned_power_sample` metadata to show the source mapping, sample counts, time range, and gap
flags.
