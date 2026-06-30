from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from hass_reader.battery_simulation import (
    BaselineScenario,
    BatteryScenario,
    SimulationResult,
    SimulationStepRecord,
)


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


def persist_simulation_result(
    *,
    db_path: str | Path | None = None,
    ingestion_db_path: str | Path,
    query_start_utc: datetime,
    query_end_utc: datetime,
    result: SimulationResult,
    persist_steps: bool = False,
) -> int:
    resolved_path = initialize_simulation_db(db_path)
    with sqlite3.connect(resolved_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        run_id = _insert_simulation_run(
            connection,
            ingestion_db_path=ingestion_db_path,
            query_start_utc=query_start_utc,
            query_end_utc=query_end_utc,
            status="running",
            error_message=None,
        )
        scenario_ids_by_name: dict[str, int] = {}
        for scenario in result.scenarios:
            scenario_id = _insert_simulation_scenario(connection, run_id=run_id, scenario=scenario)
            scenario_ids_by_name[scenario.name] = scenario_id
            _insert_simulation_summary(connection, scenario_id=scenario_id, scenario=scenario)
        if persist_steps:
            _insert_simulation_steps(connection, scenario_ids_by_name, result.steps)
        _complete_simulation_run(connection, run_id=run_id, status="completed", error_message=None)
        return run_id


def persist_failed_simulation_run(
    *,
    db_path: str | Path | None = None,
    ingestion_db_path: str | Path,
    query_start_utc: datetime,
    query_end_utc: datetime,
    error_message: str,
) -> int:
    resolved_path = initialize_simulation_db(db_path)
    with sqlite3.connect(resolved_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        run_id = _insert_simulation_run(
            connection,
            ingestion_db_path=ingestion_db_path,
            query_start_utc=query_start_utc,
            query_end_utc=query_end_utc,
            status="failed",
            error_message=error_message,
        )
        _complete_simulation_run(
            connection,
            run_id=run_id,
            status="failed",
            error_message=error_message,
        )
        return run_id


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


def _insert_simulation_run(
    connection: sqlite3.Connection,
    *,
    ingestion_db_path: str | Path,
    query_start_utc: datetime,
    query_end_utc: datetime,
    status: str,
    error_message: str | None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO simulation_run (
            started_at_utc,
            ingestion_db_path,
            query_start_utc,
            query_end_utc,
            status,
            error_message
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(),
            str(ingestion_db_path),
            _format_timestamp(query_start_utc),
            _format_timestamp(query_end_utc),
            status,
            error_message,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a simulation_run id")
    return cursor.lastrowid


def _complete_simulation_run(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    status: str,
    error_message: str | None,
) -> None:
    connection.execute(
        """
        UPDATE simulation_run
        SET completed_at_utc = ?, status = ?, error_message = ?
        WHERE id = ?
        """,
        (datetime.now(UTC).isoformat(), status, error_message, run_id),
    )


def _insert_simulation_scenario(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    scenario: BaselineScenario | BatteryScenario,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO simulation_scenario (
            simulation_run_id,
            name,
            capacity_kwh,
            efficiency,
            is_baseline
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            run_id,
            scenario.name,
            scenario.capacity_kwh,
            scenario.efficiency,
            int(scenario.is_baseline),
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a simulation_scenario id")
    return cursor.lastrowid


def _insert_simulation_summary(
    connection: sqlite3.Connection,
    *,
    scenario_id: int,
    scenario: BaselineScenario | BatteryScenario,
) -> None:
    summary = scenario.summary
    connection.execute(
        """
        INSERT INTO simulation_summary (
            scenario_id,
            imported_kwh,
            exported_kwh,
            charged_kwh,
            discharged_kwh,
            losses_kwh,
            ending_soc_kwh
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scenario_id,
            summary.imported_kwh,
            summary.exported_kwh,
            summary.charged_kwh,
            summary.discharged_kwh,
            summary.losses_kwh,
            summary.ending_soc_kwh,
        ),
    )


def _insert_simulation_steps(
    connection: sqlite3.Connection,
    scenario_ids_by_name: dict[str, int],
    steps: list[SimulationStepRecord],
) -> None:
    connection.executemany(
        """
        INSERT INTO simulation_step (
            scenario_id,
            timestamp_utc,
            import_kwh,
            export_kwh,
            charged_kwh,
            discharged_kwh,
            losses_kwh,
            soc_kwh
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                scenario_ids_by_name[step.scenario_name],
                _format_timestamp(step.timestamp_utc),
                step.import_kwh,
                step.export_kwh,
                step.charged_kwh,
                step.discharged_kwh,
                step.losses_kwh,
                step.soc_kwh,
            )
            for step in steps
        ],
    )


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("simulation run timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
