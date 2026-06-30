import sqlite3
from datetime import UTC, datetime

import pytest

from hass_reader.battery_simulation import AlignedPowerRow, simulate_scenarios
from hass_reader.simulation_db import persist_simulation_result
from hass_reader.soc_reporting import (
    SocReportingError,
    check_soc_step_availability,
    generate_soc_rollups,
    load_soc_rollups,
)


def test_check_soc_step_availability_rejects_runs_without_steps(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run(simulation_db, persist_steps=False)

    with pytest.raises(SocReportingError, match="simulate --store-steps"):
        check_soc_step_availability(simulation_db, run_id=run_id, selectors=["1kWh"])


def test_check_soc_step_availability_accepts_runs_with_steps(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run(simulation_db, persist_steps=True)

    availability = check_soc_step_availability(
        simulation_db,
        run_id=run_id,
        selectors=["1kWh"],
    )

    assert availability.run_id == run_id
    assert [(scenario.name, scenario.capacity_kwh) for scenario in availability.scenarios] == [
        ("1kWh", 1.0)
    ]


def test_check_soc_step_availability_uses_latest_completed_run_by_default(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    _persist_run(simulation_db, persist_steps=True, battery_capacities_kwh=[1.0])
    latest_run_id = _persist_run(simulation_db, persist_steps=True, battery_capacities_kwh=[2.0])

    availability = check_soc_step_availability(simulation_db, selectors=[])

    assert availability.run_id == latest_run_id
    assert [(scenario.name, scenario.capacity_kwh) for scenario in availability.scenarios] == [
        ("2kWh", 2.0)
    ]


def test_generate_hourly_soc_rollups_materializes_stats(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run_with_subhour_steps(simulation_db)

    result = generate_soc_rollups(simulation_db, run_id=run_id, bucket_kind="hour")

    assert result.run_id == run_id
    assert result.bucket_kind == "hour"
    assert result.rollups_written == 2
    assert _read_rollups(simulation_db) == [
        ("1kWh", "hour", "2025-07-01T00:00:00Z", "2025-07-01T01:00:00Z", 0.1875, 0.34375, 0.5, 2),
        (
            "1kWh",
            "hour",
            "2025-07-01T01:00:00Z",
            "2025-07-01T02:00:00Z",
            0.1875,
            0.1875,
            0.1875,
            1,
        ),
    ]


def test_generate_soc_rollups_is_idempotent(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run_with_subhour_steps(simulation_db)

    generate_soc_rollups(simulation_db, run_id=run_id, bucket_kind="hour")
    generate_soc_rollups(simulation_db, run_id=run_id, bucket_kind="hour")

    assert len(_read_rollups(simulation_db)) == 2


def test_load_soc_rollups_filters_selected_batteries_and_half_open_interval(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run_with_subhour_steps(simulation_db)
    generate_soc_rollups(simulation_db, run_id=run_id, bucket_kind="hour")

    report = load_soc_rollups(
        simulation_db,
        run_id=run_id,
        selectors=["1kWh"],
        bucket_kind="hour",
        start_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
        end_utc=datetime(2025, 7, 1, 1, 0, tzinfo=UTC),
    )

    assert report.run_id == run_id
    assert [scenario.name for scenario in report.scenarios] == ["1kWh"]
    assert [
        (
            row.scenario_name,
            row.bucket_start_utc,
            row.bucket_end_utc,
            row.min_soc_kwh,
            row.avg_soc_kwh,
            row.max_soc_kwh,
            row.sample_count,
        )
        for row in report.rows
    ] == [
        (
            "1kWh",
            "2025-07-01T00:00:00Z",
            "2025-07-01T01:00:00Z",
            0.1875,
            0.34375,
            0.5,
            2,
        )
    ]


def test_load_soc_rollups_does_not_require_raw_steps_after_generation(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run_with_subhour_steps(simulation_db)
    generate_soc_rollups(simulation_db, run_id=run_id, bucket_kind="hour")
    with sqlite3.connect(simulation_db) as connection:
        connection.execute("DELETE FROM simulation_step")

    report = load_soc_rollups(simulation_db, run_id=run_id, bucket_kind="hour")

    assert len(report.rows) == 2


def test_load_soc_rollups_reports_missing_rollups(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run_with_subhour_steps(simulation_db)

    with pytest.raises(SocReportingError, match="soc-rollup"):
        load_soc_rollups(simulation_db, run_id=run_id, bucket_kind="hour")


def _persist_run(
    simulation_db,
    *,
    persist_steps: bool,
    battery_capacities_kwh: list[float] | None = None,
) -> int:
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 1, 0, 0, tzinfo=UTC), 0.0, 1.0, 1.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 1, 0, tzinfo=UTC), 1.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 2, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=battery_capacities_kwh or [1.0],
        efficiency=0.8,
    )
    return persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=simulation_db.parent / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        result=result,
        persist_steps=persist_steps,
    )


def _persist_run_with_subhour_steps(simulation_db) -> int:
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 1, 0, 0, tzinfo=UTC), 0.0, 1.0, 1.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 0, 30, tzinfo=UTC), 0.5, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 1, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 2, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=[1.0],
        efficiency=0.8,
    )
    return persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=simulation_db.parent / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        result=result,
        persist_steps=True,
    )


def _read_rollups(simulation_db) -> list[tuple]:
    with sqlite3.connect(simulation_db) as connection:
        return connection.execute(
            """
            SELECT
                simulation_scenario.name,
                simulation_soc_rollup.bucket_kind,
                simulation_soc_rollup.bucket_start_utc,
                simulation_soc_rollup.bucket_end_utc,
                simulation_soc_rollup.min_soc_kwh,
                simulation_soc_rollup.avg_soc_kwh,
                simulation_soc_rollup.max_soc_kwh,
                simulation_soc_rollup.sample_count
            FROM simulation_soc_rollup
            JOIN simulation_scenario
                ON simulation_scenario.id = simulation_soc_rollup.simulation_scenario_id
            ORDER BY simulation_soc_rollup.bucket_start_utc
            """
        ).fetchall()
