import sqlite3

from hass_reader.cache import initialize_cache, resolve_cache_path


def test_initialize_cache_creates_schema_and_seed_sources(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"

    initialize_cache(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        primary_sources = connection.execute(
            """
            SELECT role, measurement, entity_id, expected_unit, normalize_to_unit
            FROM metric_source
            WHERE is_primary = 1
            ORDER BY role
            """
        ).fetchall()
        source_count = connection.execute("SELECT count(*) FROM metric_source").fetchone()[0]

    assert {
        "schema_version",
        "metric_source",
        "ingestion_run",
        "fetch_window",
        "raw_sample",
        "normalized_sample",
        "aligned_power_sample",
    }.issubset(tables)
    assert primary_sources == [
        (
            "grid_export_power",
            "sensor__power",
            "power_produced",
            "kW",
            "kW",
        ),
        (
            "grid_import_power",
            "sensor__power",
            "power_consumed",
            "kW",
            "kW",
        ),
        (
            "solar_production_power",
            "sensor__power",
            "zonneplan_one_optimized_omvormer_last_measured_value",
            "W",
            "kW",
        ),
    ]
    assert source_count == 12


def test_initialize_cache_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"

    initialize_cache(db_path)
    initialize_cache(db_path)

    with sqlite3.connect(db_path) as connection:
        schema_versions = connection.execute("SELECT count(*) FROM schema_version").fetchone()[0]
        source_count = connection.execute("SELECT count(*) FROM metric_source").fetchone()[0]

    assert schema_versions == 1
    assert source_count == 12


def test_resolve_cache_path_uses_environment_override(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "override.sqlite3"
    monkeypatch.setenv("HASS_READER_SQLITE_DB", str(db_path))

    assert resolve_cache_path() == db_path
