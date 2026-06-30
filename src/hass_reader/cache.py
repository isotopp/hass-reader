from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_CACHE_PATH = Path("var/hass-reader.sqlite3")
ENV_CACHE_PATH = "HASS_READER_SQLITE_DB"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SeedMetricSource:
    role: str
    measurement: str
    entity_id: str
    source_kind: str
    expected_unit: str
    normalize_to_unit: str
    is_primary: bool


SEED_METRIC_SOURCES = (
    SeedMetricSource(
        role="grid_import_power",
        measurement="sensor__power",
        entity_id="power_consumed",
        source_kind="slimmelezer_p1",
        expected_unit="kW",
        normalize_to_unit="kW",
        is_primary=True,
    ),
    SeedMetricSource(
        role="grid_export_power",
        measurement="sensor__power",
        entity_id="power_produced",
        source_kind="slimmelezer_p1",
        expected_unit="kW",
        normalize_to_unit="kW",
        is_primary=True,
    ),
    SeedMetricSource(
        role="solar_production_power",
        measurement="sensor__power",
        entity_id="zonneplan_one_optimized_omvormer_last_measured_value",
        source_kind="zonneplan_inverter",
        expected_unit="W",
        normalize_to_unit="kW",
        is_primary=True,
    ),
    SeedMetricSource(
        role="zonneplan_p1_net_power",
        measurement="sensor__power",
        entity_id="connect_energiemeter_electricity_average",
        source_kind="zonneplan_cross_check",
        expected_unit="W",
        normalize_to_unit="kW",
        is_primary=False,
    ),
    SeedMetricSource(
        role="zonneplan_p1_import_power",
        measurement="sensor__power",
        entity_id="connect_energiemeter_electricity_consumption",
        source_kind="zonneplan_cross_check",
        expected_unit="W",
        normalize_to_unit="kW",
        is_primary=False,
    ),
    SeedMetricSource(
        role="zonneplan_p1_export_power",
        measurement="sensor__power",
        entity_id="connect_energiemeter_electricity_production",
        source_kind="zonneplan_cross_check",
        expected_unit="W",
        normalize_to_unit="kW",
        is_primary=False,
    ),
    SeedMetricSource(
        role="p1_grid_import_today",
        measurement="sensor__energy",
        entity_id="energy_consumed_today",
        source_kind="aggregate_validation",
        expected_unit="kWh",
        normalize_to_unit="kWh",
        is_primary=False,
    ),
    SeedMetricSource(
        role="p1_grid_export_today",
        measurement="sensor__energy",
        entity_id="energy_produced_today",
        source_kind="aggregate_validation",
        expected_unit="kWh",
        normalize_to_unit="kWh",
        is_primary=False,
    ),
    SeedMetricSource(
        role="zonneplan_grid_import_today",
        measurement="sensor__energy",
        entity_id="zonneplan_electricity_consumption_today",
        source_kind="aggregate_validation",
        expected_unit="kWh",
        normalize_to_unit="kWh",
        is_primary=False,
    ),
    SeedMetricSource(
        role="zonneplan_grid_returned_today",
        measurement="sensor__energy",
        entity_id="zonneplan_electricity_returned_today",
        source_kind="aggregate_validation",
        expected_unit="kWh",
        normalize_to_unit="kWh",
        is_primary=False,
    ),
    SeedMetricSource(
        role="zonneplan_solar_yield_today",
        measurement="sensor__energy",
        entity_id="zonneplan_yield_today",
        source_kind="aggregate_validation",
        expected_unit="kWh",
        normalize_to_unit="kWh",
        is_primary=False,
    ),
    SeedMetricSource(
        role="zonneplan_solar_yield_total",
        measurement="sensor__energy",
        entity_id="zonneplan_one_optimized_omvormer_yield_total",
        source_kind="aggregate_validation",
        expected_unit="kWh",
        normalize_to_unit="kWh",
        is_primary=False,
    ),
)


def resolve_cache_path(db_path: str | Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env_path = os.environ.get(ENV_CACHE_PATH)
    if env_path:
        return Path(env_path)
    return DEFAULT_CACHE_PATH


def initialize_cache(db_path: str | Path | None = None) -> Path:
    resolved_path = resolve_cache_path(db_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(resolved_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        _record_schema_version(connection)
        _seed_metric_sources(connection)

    return resolved_path


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metric_source (
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

        CREATE TABLE IF NOT EXISTS ingestion_run (
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

        CREATE TABLE IF NOT EXISTS fetch_window (
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

        CREATE TABLE IF NOT EXISTS raw_sample (
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

        CREATE TABLE IF NOT EXISTS normalized_sample (
            metric_source_id INTEGER NOT NULL REFERENCES metric_source(id),
            timestamp_utc TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_run(id),
            PRIMARY KEY (metric_source_id, timestamp_utc)
        );

        CREATE TABLE IF NOT EXISTS aligned_power_sample (
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

        CREATE INDEX IF NOT EXISTS idx_raw_sample_metric_time
            ON raw_sample(metric_source_id, timestamp_utc);
        CREATE INDEX IF NOT EXISTS idx_normalized_sample_metric_time
            ON normalized_sample(metric_source_id, timestamp_utc);
        CREATE INDEX IF NOT EXISTS idx_aligned_power_sample_time
            ON aligned_power_sample(timestamp_utc);
        """
    )


def _record_schema_version(connection: sqlite3.Connection) -> None:
    applied_at = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_version (version, applied_at_utc)
        VALUES (?, ?)
        """,
        (SCHEMA_VERSION, applied_at),
    )


def _seed_metric_sources(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT OR IGNORE INTO metric_source (
            role,
            measurement,
            entity_id,
            source_kind,
            expected_unit,
            normalize_to_unit,
            is_primary
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                source.role,
                source.measurement,
                source.entity_id,
                source.source_kind,
                source.expected_unit,
                source.normalize_to_unit,
                int(source.is_primary),
            )
            for source in SEED_METRIC_SOURCES
        ],
    )
