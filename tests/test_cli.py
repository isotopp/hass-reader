import sqlite3
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from hass_reader.cli import IngestCommandResult, SimulationCommandResult, main
from hass_reader.cache import initialize_cache


def test_ingest_command_uses_documented_defaults(tmp_path, capsys) -> None:
    calls = []

    def runner(args) -> IngestCommandResult:
        calls.append(args)
        return IngestCommandResult(fetched=1, cached=2, normalized=3, aligned=4)

    exit_code = main(["ingest", "--db", str(tmp_path / "cache.sqlite3")], ingest_runner=runner)

    assert exit_code == 0
    assert calls[0].start == "2025-07-01"
    assert calls[0].end == "2026-07-01"
    assert calls[0].timezone == "Europe/Amsterdam"
    assert calls[0].influx_url == "http://192.168.1.10:8086"
    assert calls[0].influx_database == "hass"
    assert calls[0].force_refresh is False
    assert "fetched=1 cached=2 normalized=3 aligned=4" in capsys.readouterr().out


def test_ingest_command_reports_failures_without_traceback(tmp_path, capsys) -> None:
    def runner(_args) -> IngestCommandResult:
        raise RuntimeError("connection failed")

    exit_code = main(["ingest", "--db", str(tmp_path / "cache.sqlite3")], ingest_runner=runner)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "connection failed" in captured.err
    assert "Traceback" not in captured.err


def test_console_scripts_are_wired_to_cli_main() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["hass-reader"] == "hass_reader:main"


def test_simulate_command_parses_defaults_and_batteries(tmp_path, capsys) -> None:
    calls = []

    def runner(args) -> SimulationCommandResult:
        calls.append(args)
        return SimulationCommandResult(run_id=7, scenario_count=4)

    exit_code = main(
        [
            "simulate",
            "--batteries",
            "5,10,15",
            "--ingestion-db",
            str(tmp_path / "ingestion.sqlite3"),
            "--simulation-db",
            str(tmp_path / "simulation.sqlite3"),
        ],
        simulation_runner=runner,
    )

    assert exit_code == 0
    assert calls[0].start == "2025-07-01"
    assert calls[0].end == "2026-07-01"
    assert calls[0].timezone == "Europe/Amsterdam"
    assert calls[0].battery_capacities_kwh == [5.0, 10.0, 15.0]
    assert calls[0].efficiency == 0.9
    assert "simulation complete: run_id=7 scenarios=4" in capsys.readouterr().out


def test_simulate_command_rejects_invalid_batteries(capsys) -> None:
    exit_code = main(["simulate", "--batteries", "5,0,nope"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "invalid --batteries" in captured.err
    assert "Traceback" not in captured.err


def test_simulate_command_rejects_invalid_efficiency(capsys) -> None:
    exit_code = main(["simulate", "--batteries", "5", "--efficiency", "1.5"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "invalid --efficiency" in captured.err
    assert "Traceback" not in captured.err


def test_simulate_command_parses_store_steps(tmp_path) -> None:
    calls = []

    def runner(args) -> SimulationCommandResult:
        calls.append(args)
        return SimulationCommandResult(run_id=9, scenario_count=2)

    exit_code = main(
        [
            "simulate",
            "--batteries",
            "5",
            "--store-steps",
            "--ingestion-db",
            str(tmp_path / "ingestion.sqlite3"),
            "--simulation-db",
            str(tmp_path / "simulation.sqlite3"),
        ],
        simulation_runner=runner,
    )

    assert exit_code == 0
    assert calls[0].store_steps is True


def test_simulate_command_reads_ingestion_cache_and_writes_simulation_db(tmp_path, capsys) -> None:
    ingestion_db = tmp_path / "ingestion.sqlite3"
    simulation_db = tmp_path / "simulation.sqlite3"
    _write_aligned_power_samples(ingestion_db)

    exit_code = main(
        [
            "simulate",
            "--batteries",
            "1",
            "--efficiency",
            "0.8",
            "--ingestion-db",
            str(ingestion_db),
            "--simulation-db",
            str(simulation_db),
            "--timezone",
            "UTC",
            "--start",
            "2025-07-01",
            "--end",
            "2025-07-02",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "phase=simulate" in output
    assert "progress=0% samples=0/3" in output
    assert "progress=100% samples=3/3" in output
    assert "simulation complete: run_id=1 scenarios=2" in output
    with sqlite3.connect(simulation_db) as connection:
        rows = connection.execute(
            """
            SELECT
                simulation_scenario.name,
                simulation_summary.imported_kwh,
                simulation_summary.exported_kwh,
                simulation_summary.charged_kwh,
                simulation_summary.discharged_kwh,
                simulation_summary.losses_kwh
            FROM simulation_summary
            JOIN simulation_scenario ON simulation_scenario.id = simulation_summary.scenario_id
            ORDER BY simulation_scenario.id
            """
        ).fetchall()

    assert [
        (name, round(imported, 6), round(exported, 6), charged, discharged, round(losses, 6))
        for name, imported, exported, charged, discharged, losses in rows
    ] == [
        ("baseline", 1.0, 1.0, 0.0, 0.0, 0.0),
        ("1kWh", 0.2, 0.0, 1.0, 1.0, 0.2),
    ]


def _write_aligned_power_samples(db_path: Path) -> None:
    initialize_cache(db_path)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO ingestion_run (
                started_at_utc,
                influx_url,
                influx_database,
                local_timezone,
                query_start_utc,
                query_end_utc,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                "http://192.168.1.10:8086",
                "hass",
                "UTC",
                "2025-07-01T00:00:00Z",
                "2025-07-02T00:00:00Z",
                "completed",
            ),
        )
        run_id = cursor.lastrowid
        connection.executemany(
            """
            INSERT INTO aligned_power_sample (
                timestamp_utc,
                grid_import_kw,
                grid_export_kw,
                solar_production_kw,
                import_source_sample_utc,
                export_source_sample_utc,
                solar_source_sample_utc,
                max_source_age_seconds,
                gap_flags,
                ingestion_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "2025-07-01T00:00:00Z",
                    0.0,
                    1.0,
                    1.0,
                    "2025-07-01T00:00:00Z",
                    "2025-07-01T00:00:00Z",
                    "2025-07-01T00:00:00Z",
                    0,
                    "",
                    run_id,
                ),
                (
                    "2025-07-01T01:00:00Z",
                    1.0,
                    0.0,
                    0.0,
                    "2025-07-01T01:00:00Z",
                    "2025-07-01T01:00:00Z",
                    "2025-07-01T01:00:00Z",
                    0,
                    "",
                    run_id,
                ),
                (
                    "2025-07-01T02:00:00Z",
                    0.0,
                    0.0,
                    0.0,
                    "2025-07-01T02:00:00Z",
                    "2025-07-01T02:00:00Z",
                    "2025-07-01T02:00:00Z",
                    0,
                    "",
                    run_id,
                ),
            ],
        )
