import csv
import sqlite3
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from hass_reader.battery_simulation import AlignedPowerRow, simulate_scenarios
from hass_reader.cli import IngestCommandResult, SimulationCommandResult, main
from hass_reader.cache import initialize_cache
from hass_reader.simulation_db import persist_simulation_result


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
    assert "Baseline: import=1.00 kWh export=1.00 kWh" in output
    assert "1kWh: import=0.20 kWh export=0.00 kWh" in output
    assert "avoided_import=0.80 kWh avoided_export=1.00 kWh" in output
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


def test_report_command_prints_compact_summary_for_latest_run(tmp_path, capsys) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    _persist_reportable_run(simulation_db, battery_capacities_kwh=[1.0, 2.0])

    exit_code = main(["report", "--simulation-db", str(simulation_db)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Simulation run 1" in output
    assert "Baseline: import=2.00 kWh export=2.00 kWh" in output
    assert "1kWh: import=1.20 kWh export=1.00 kWh" in output
    assert "avoided_import=0.80 kWh avoided_export=1.00 kWh" in output
    assert "2kWh: import=0.40 kWh export=0.00 kWh" in output
    assert "avoided_import=1.60 kWh avoided_export=2.00 kWh" in output


def test_report_command_can_select_run_id(tmp_path, capsys) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    first_run_id = _persist_reportable_run(simulation_db, battery_capacities_kwh=[1.0])
    _persist_reportable_run(simulation_db, battery_capacities_kwh=[2.0])

    exit_code = main(
        ["report", "--simulation-db", str(simulation_db), "--run-id", str(first_run_id)]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert f"Simulation run {first_run_id}" in output
    assert "1kWh:" in output
    assert "2kWh:" not in output


def test_report_command_writes_csv_output(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    output_path = tmp_path / "results.csv"
    _persist_reportable_run(simulation_db, battery_capacities_kwh=[1.0, 2.0])

    exit_code = main(
        [
            "report",
            "--simulation-db",
            str(simulation_db),
            "--output",
            str(output_path),
            "--overwrite",
        ]
    )

    assert exit_code == 0
    with output_path.open(newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert list(rows[0]) == [
        "run_id",
        "query_start_utc",
        "query_end_utc",
        "scenario_name",
        "capacity_kwh",
        "efficiency",
        "imported_kwh",
        "exported_kwh",
        "charged_kwh",
        "discharged_kwh",
        "losses_kwh",
        "ending_soc_kwh",
        "avoided_import_kwh",
        "avoided_export_kwh",
        "influx_url",
        "influx_database",
        "ingestion_query_start_utc",
        "ingestion_query_end_utc",
        "grid_import_entity_id",
        "grid_export_entity_id",
        "solar_production_entity_id",
        "non_default_mappings",
    ]
    assert [row["scenario_name"] for row in rows] == ["1kWh", "2kWh"]
    assert rows[0]["capacity_kwh"] == "1"
    assert rows[0]["imported_kwh"] == "1.2"
    assert rows[0]["avoided_import_kwh"] == "0.8"


def test_report_command_refuses_to_overwrite_csv_without_option(tmp_path, capsys) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    output_path = tmp_path / "results.csv"
    output_path.write_text("keep me\n")
    _persist_reportable_run(simulation_db, battery_capacities_kwh=[1.0])

    exit_code = main(
        ["report", "--simulation-db", str(simulation_db), "--output", str(output_path)]
    )

    assert exit_code == 1
    assert output_path.read_text() == "keep me\n"
    captured = capsys.readouterr()
    assert "already exists" in captured.err
    assert "Traceback" not in captured.err


def test_report_command_includes_metric_mapping_and_source_metadata(tmp_path, capsys) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    ingestion_db = tmp_path / "ingestion.sqlite3"
    _write_ingestion_metadata(ingestion_db)
    _persist_reportable_run(
        simulation_db,
        battery_capacities_kwh=[1.0],
        ingestion_db=ingestion_db,
    )

    exit_code = main(["report", "--simulation-db", str(simulation_db)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Source: url=http://192.168.1.10:8086 database=hass" in output
    assert "grid_import_power=power_consumed (default)" in output
    assert "grid_export_power=power_produced (default)" in output
    assert (
        "solar_production_power=zonneplan_one_optimized_omvormer_last_measured_value (default)"
        in output
    )


def test_report_csv_includes_metric_mapping_and_source_metadata(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    ingestion_db = tmp_path / "ingestion.sqlite3"
    output_path = tmp_path / "results.csv"
    _write_ingestion_metadata(ingestion_db)
    _persist_reportable_run(
        simulation_db,
        battery_capacities_kwh=[1.0],
        ingestion_db=ingestion_db,
    )

    exit_code = main(
        [
            "report",
            "--simulation-db",
            str(simulation_db),
            "--output",
            str(output_path),
            "--overwrite",
        ]
    )

    assert exit_code == 0
    with output_path.open(newline="") as csv_file:
        row = next(csv.DictReader(csv_file))

    assert row["influx_url"] == "http://192.168.1.10:8086"
    assert row["influx_database"] == "hass"
    assert row["grid_import_entity_id"] == "power_consumed"
    assert row["grid_export_entity_id"] == "power_produced"
    assert row["solar_production_entity_id"] == (
        "zonneplan_one_optimized_omvormer_last_measured_value"
    )
    assert row["non_default_mappings"] == ""


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


def _write_ingestion_metadata(db_path: Path) -> None:
    initialize_cache(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
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
                "Europe/Amsterdam",
                "2025-06-30T22:00:00Z",
                "2026-06-30T22:00:00Z",
                "completed",
            ),
        )


def _persist_reportable_run(
    simulation_db: Path,
    *,
    battery_capacities_kwh: list[float],
    ingestion_db: Path | None = None,
) -> int:
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 1, 0, 0, tzinfo=UTC), 0.0, 2.0, 2.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 1, 0, tzinfo=UTC), 2.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 2, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=battery_capacities_kwh,
        efficiency=0.8,
    )
    return persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=ingestion_db or simulation_db.parent / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        result=result,
    )
