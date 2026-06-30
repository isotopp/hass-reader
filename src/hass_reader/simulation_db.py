from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_SIMULATION_DB_PATH = Path("var/hass-reader-simulation.sqlite3")
ENV_SIMULATION_DB_PATH = "HASS_READER_SIMULATION_DB"
SCHEMA_VERSION = 1


def resolve_simulation_db_path(db_path: str | Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env_path = os.environ.get(ENV_SIMULATION_DB_PATH)
    if env_path:
        return Path(env_path)
    return DEFAULT_SIMULATION_DB_PATH


def initialize_simulation_db(db_path: str | Path | None = None) -> Path:
    resolved_path = resolve_simulation_db_path(db_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(resolved_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        _record_schema_version(connection)

    return resolved_path


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS simulation_run (
            id INTEGER PRIMARY KEY,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            ingestion_db_path TEXT NOT NULL,
            query_start_utc TEXT NOT NULL,
            query_end_utc TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS simulation_scenario (
            id INTEGER PRIMARY KEY,
            simulation_run_id INTEGER NOT NULL REFERENCES simulation_run(id),
            name TEXT NOT NULL,
            capacity_kwh REAL NOT NULL,
            efficiency REAL NOT NULL,
            is_baseline INTEGER NOT NULL,
            UNIQUE (simulation_run_id, name)
        );

        CREATE TABLE IF NOT EXISTS simulation_summary (
            scenario_id INTEGER PRIMARY KEY REFERENCES simulation_scenario(id),
            imported_kwh REAL NOT NULL,
            exported_kwh REAL NOT NULL,
            charged_kwh REAL NOT NULL,
            discharged_kwh REAL NOT NULL,
            losses_kwh REAL NOT NULL,
            ending_soc_kwh REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS simulation_step (
            scenario_id INTEGER NOT NULL REFERENCES simulation_scenario(id),
            timestamp_utc TEXT NOT NULL,
            import_kwh REAL NOT NULL,
            export_kwh REAL NOT NULL,
            charged_kwh REAL NOT NULL,
            discharged_kwh REAL NOT NULL,
            losses_kwh REAL NOT NULL,
            soc_kwh REAL NOT NULL,
            PRIMARY KEY (scenario_id, timestamp_utc)
        );
        """
    )


def _record_schema_version(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_version (version, applied_at_utc)
        VALUES (?, ?)
        """,
        (SCHEMA_VERSION, datetime.now(UTC).isoformat()),
    )
