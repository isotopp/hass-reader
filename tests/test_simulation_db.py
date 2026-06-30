import sqlite3

from hass_reader.cache import initialize_cache
from hass_reader.simulation_db import initialize_simulation_db, resolve_simulation_db_path


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
