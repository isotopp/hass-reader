import sqlite3
from datetime import UTC, datetime

from hass_reader.battery_simulation import AlignedPowerRow, simulate_scenarios
from hass_reader.cache import initialize_cache
from hass_reader.simulation_db import (
    initialize_simulation_db,
    persist_failed_simulation_run,
    persist_simulation_result,
    resolve_simulation_db_path,
)


def test_initialize_simulation_db_creates_schema(tmp_path) -> None:
    db_path = tmp_path / "simulation.sqlite3"

    initialize_simulation_db(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {
        "schema_version",
        "simulation_run",
        "simulation_scenario",
        "simulation_summary",
        "simulation_step",
    }.issubset(tables)


def test_initialize_simulation_db_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "simulation.sqlite3"

    initialize_simulation_db(db_path)
    initialize_simulation_db(db_path)

    with sqlite3.connect(db_path) as connection:
        schema_versions = connection.execute("SELECT count(*) FROM schema_version").fetchone()[0]

    assert schema_versions == 1


def test_resolve_simulation_db_path_uses_environment_override(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "override.sqlite3"
    monkeypatch.setenv("HASS_READER_SIMULATION_DB", str(db_path))

    assert resolve_simulation_db_path() == db_path


def test_simulation_database_is_separate_from_ingestion_cache(tmp_path) -> None:
    ingestion_db = tmp_path / "ingestion.sqlite3"
    simulation_db = tmp_path / "simulation.sqlite3"

    initialize_cache(ingestion_db)
    initialize_simulation_db(simulation_db)

    assert ingestion_db.exists()
    assert simulation_db.exists()
    assert ingestion_db != simulation_db


def test_persist_simulation_result_writes_run_scenarios_and_summaries(tmp_path) -> None:
    ingestion_db = tmp_path / "ingestion.sqlite3"
    simulation_db = tmp_path / "simulation.sqlite3"
    initialize_cache(ingestion_db)
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 1, 0, 0, tzinfo=UTC), 0.0, 2.0, 2.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 1, 0, tzinfo=UTC), 2.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 2, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=[1.0],
        efficiency=0.8,
    )

    run_id = persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=ingestion_db,
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        result=result,
    )

    with sqlite3.connect(simulation_db) as connection:
        run = connection.execute(
            """
            SELECT ingestion_db_path, query_start_utc, query_end_utc, status, error_message
            FROM simulation_run
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        scenarios = connection.execute(
            """
            SELECT name, capacity_kwh, efficiency, is_baseline
            FROM simulation_scenario
            WHERE simulation_run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
        summaries = connection.execute(
            """
            SELECT
                simulation_scenario.name,
                simulation_summary.imported_kwh,
                simulation_summary.exported_kwh,
                simulation_summary.charged_kwh,
                simulation_summary.discharged_kwh,
                simulation_summary.losses_kwh,
                simulation_summary.ending_soc_kwh
            FROM simulation_summary
            JOIN simulation_scenario ON simulation_scenario.id = simulation_summary.scenario_id
            WHERE simulation_scenario.simulation_run_id = ?
            ORDER BY simulation_scenario.id
            """,
            (run_id,),
        ).fetchall()

    assert run == (
        str(ingestion_db),
        "2025-06-30T22:00:00Z",
        "2026-06-30T22:00:00Z",
        "completed",
        None,
    )
    assert scenarios == [
        ("baseline", 0.0, 1.0, 1),
        ("1kWh", 1.0, 0.8, 0),
    ]
    assert summaries == [
        ("baseline", 2.0, 2.0, 0.0, 0.0, 0.0, 0.0),
        ("1kWh", 1.2, 1.0, 1.0, 1.0, 0.19999999999999996, 0.0),
    ]

    with sqlite3.connect(ingestion_db) as connection:
        simulation_tables = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'simulation_run'"
        ).fetchone()[0]
    assert simulation_tables == 0


def test_persist_failed_simulation_run_records_error(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"

    run_id = persist_failed_simulation_run(
        db_path=simulation_db,
        ingestion_db_path=tmp_path / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        error_message="aligned data missing",
    )

    with sqlite3.connect(simulation_db) as connection:
        row = connection.execute(
            "SELECT status, error_message FROM simulation_run WHERE id = ?",
            (run_id,),
        ).fetchone()

    assert row == ("failed", "aligned data missing")


def test_persist_simulation_result_writes_steps_when_enabled(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 1, 0, 0, tzinfo=UTC), 0.0, 1.0, 1.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 1, 0, tzinfo=UTC), 1.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 2, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=[1.0],
        efficiency=0.8,
    )

    run_id = persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=tmp_path / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        result=result,
        persist_steps=True,
    )

    with sqlite3.connect(simulation_db) as connection:
        steps = connection.execute(
            """
            SELECT
                simulation_scenario.name,
                simulation_step.timestamp_utc,
                simulation_step.import_kwh,
                simulation_step.export_kwh,
                simulation_step.charged_kwh,
                simulation_step.discharged_kwh,
                simulation_step.losses_kwh,
                simulation_step.soc_kwh
            FROM simulation_step
            JOIN simulation_scenario ON simulation_scenario.id = simulation_step.scenario_id
            WHERE simulation_scenario.simulation_run_id = ?
            ORDER BY simulation_step.timestamp_utc, simulation_scenario.id
            """,
            (run_id,),
        ).fetchall()

    rounded_steps = [
        (
            name,
            timestamp,
            round(import_kwh, 6),
            round(export_kwh, 6),
            round(charged_kwh, 6),
            round(discharged_kwh, 6),
            round(losses_kwh, 6),
            round(soc_kwh, 6),
        )
        for (
            name,
            timestamp,
            import_kwh,
            export_kwh,
            charged_kwh,
            discharged_kwh,
            losses_kwh,
            soc_kwh,
        ) in steps
    ]
    assert rounded_steps == [
        ("baseline", "2025-07-01T00:00:00Z", 0.0, 1.0, 0.0, 0.0, 0.0, 0.0),
        ("1kWh", "2025-07-01T00:00:00Z", 0.0, 0.0, 1.0, 0.0, 0.0, 1.0),
        ("baseline", "2025-07-01T01:00:00Z", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        ("1kWh", "2025-07-01T01:00:00Z", 0.2, 0.0, 0.0, 1.0, 0.2, 0.0),
    ]


def test_persist_simulation_result_skips_steps_by_default(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 1, 0, 0, tzinfo=UTC), 0.0, 1.0, 1.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 1, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=[1.0],
        efficiency=0.8,
    )

    persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=tmp_path / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        result=result,
    )

    with sqlite3.connect(simulation_db) as connection:
        step_count = connection.execute("SELECT count(*) FROM simulation_step").fetchone()[0]
        summary_count = connection.execute("SELECT count(*) FROM simulation_summary").fetchone()[0]

    assert step_count == 0
    assert summary_count == 2
