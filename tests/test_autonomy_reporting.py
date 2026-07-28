from datetime import UTC, datetime

import pytest

from hass_reader.autonomy_reporting import AutonomyReportingError, check_autonomy_step_availability
from hass_reader.autonomy_reporting import load_autonomy_rollups
from hass_reader.battery_simulation import AlignedPowerRow, simulate_scenarios
from hass_reader.simulation_db import persist_simulation_result


def test_check_autonomy_step_availability_rejects_runs_without_steps(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run(simulation_db, persist_steps=False)

    with pytest.raises(AutonomyReportingError) as error:
        check_autonomy_step_availability(simulation_db, run_id=run_id, selectors=["1kWh"])

    message = str(error.value)
    assert "simulate --store-steps" in message
    assert "refetch" not in message.lower()
    assert "Influx" not in message


def test_check_autonomy_step_availability_accepts_runs_with_steps(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_run(simulation_db, persist_steps=True)

    availability = check_autonomy_step_availability(
        simulation_db,
        run_id=run_id,
        selectors=["1kWh"],
    )

    assert availability.run_id == run_id
    assert [(scenario.name, scenario.capacity_kwh) for scenario in availability.scenarios] == [
        ("1kWh", 1.0)
    ]


def test_check_autonomy_step_availability_uses_latest_completed_run_by_default(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    _persist_run(simulation_db, persist_steps=True, battery_capacities_kwh=[1.0])
    latest_run_id = _persist_run(simulation_db, persist_steps=True, battery_capacities_kwh=[2.0])

    availability = check_autonomy_step_availability(simulation_db, selectors=[])

    assert availability.run_id == latest_run_id
    assert [(scenario.name, scenario.capacity_kwh) for scenario in availability.scenarios] == [
        ("2kWh", 2.0)
    ]


def test_load_daily_autonomy_rollups_reports_time_weighted_zero_import(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_threshold_run(simulation_db)

    report = load_autonomy_rollups(
        simulation_db,
        bucket_kind="day",
        run_id=run_id,
        selectors=["1kWh"],
        timezone_name="UTC",
        threshold_watts=30.0,
    )

    assert report.run_id == run_id
    assert report.bucket_kind == "day"
    assert [
        (
            row.scenario_name,
            row.bucket_start_utc,
            row.bucket_end_utc,
            row.autonomy_percent,
            row.autonomous_hours,
            row.valid_hours,
            row.non_autonomous_hours,
            row.imported_kwh,
        )
        for row in report.rows
    ] == [
        (
            "1kWh",
            "2025-07-01T00:00:00Z",
            "2025-07-02T00:00:00Z",
            pytest.approx(66.6666666667),
            pytest.approx(2.0),
            pytest.approx(3.0),
            pytest.approx(1.0),
            pytest.approx(1.02),
        )
    ]


def test_load_daily_autonomy_rollups_splits_intervals_at_local_midnight(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_midnight_run(simulation_db)

    report = load_autonomy_rollups(
        simulation_db,
        bucket_kind="day",
        run_id=run_id,
        selectors=["1kWh"],
        timezone_name="UTC",
        threshold_watts=30.0,
    )

    assert [
        (
            row.bucket_start_utc,
            row.bucket_end_utc,
            row.autonomy_percent,
            row.autonomous_hours,
            row.valid_hours,
            row.imported_kwh,
        )
        for row in report.rows
    ] == [
        (
            "2025-07-01T00:00:00Z",
            "2025-07-02T00:00:00Z",
            pytest.approx(100.0),
            pytest.approx(0.5),
            pytest.approx(0.5),
            pytest.approx(0.0),
        ),
        (
            "2025-07-02T00:00:00Z",
            "2025-07-03T00:00:00Z",
            pytest.approx(50.0),
            pytest.approx(0.5),
            pytest.approx(1.0),
            pytest.approx(0.5),
        ),
    ]


def test_load_monthly_autonomy_rollups_splits_intervals_at_month_boundary(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_month_boundary_run(simulation_db)

    report = load_autonomy_rollups(
        simulation_db,
        bucket_kind="month",
        run_id=run_id,
        selectors=["1kWh"],
        timezone_name="UTC",
        threshold_watts=30.0,
    )

    assert report.bucket_kind == "month"
    assert [
        (
            row.bucket_start_utc,
            row.bucket_end_utc,
            row.autonomy_percent,
            row.autonomous_hours,
            row.valid_hours,
            row.imported_kwh,
        )
        for row in report.rows
    ] == [
        (
            "2025-07-01T00:00:00Z",
            "2025-08-01T00:00:00Z",
            pytest.approx(100.0),
            pytest.approx(0.5),
            pytest.approx(0.5),
            pytest.approx(0.0),
        ),
        (
            "2025-08-01T00:00:00Z",
            "2025-09-01T00:00:00Z",
            pytest.approx(50.0),
            pytest.approx(0.5),
            pytest.approx(1.0),
            pytest.approx(0.5),
        ),
    ]


def test_load_monthly_autonomy_rollups_filters_half_open_interval(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    run_id = _persist_month_boundary_run(simulation_db)

    report = load_autonomy_rollups(
        simulation_db,
        bucket_kind="month",
        run_id=run_id,
        selectors=["1kWh"],
        start_utc=datetime(2025, 8, 1, 0, 0, tzinfo=UTC),
        end_utc=datetime(2025, 8, 1, 0, 30, tzinfo=UTC),
        timezone_name="UTC",
        threshold_watts=30.0,
    )

    assert [
        (
            row.bucket_start_utc,
            row.bucket_end_utc,
            row.autonomy_percent,
            row.autonomous_hours,
            row.valid_hours,
            row.imported_kwh,
        )
        for row in report.rows
    ] == [
        (
            "2025-08-01T00:00:00Z",
            "2025-09-01T00:00:00Z",
            pytest.approx(100.0),
            pytest.approx(0.5),
            pytest.approx(0.5),
            pytest.approx(0.0),
        )
    ]


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


def _persist_threshold_run(simulation_db) -> int:
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 1, 0, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 1, 0, tzinfo=UTC), 1.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 2, 0, tzinfo=UTC), 0.02, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 1, 3, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=[1.0],
        efficiency=1.0,
    )
    return persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=simulation_db.parent / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
        query_end_utc=datetime(2025, 7, 1, 3, 0, tzinfo=UTC),
        result=result,
        persist_steps=True,
    )


def _persist_midnight_run(simulation_db) -> int:
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 1, 23, 30, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 2, 0, 30, tzinfo=UTC), 1.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 7, 2, 1, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=[1.0],
        efficiency=1.0,
    )
    return persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=simulation_db.parent / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 7, 1, 23, 30, tzinfo=UTC),
        query_end_utc=datetime(2025, 7, 2, 1, 0, tzinfo=UTC),
        result=result,
        persist_steps=True,
    )


def _persist_month_boundary_run(simulation_db) -> int:
    result = simulate_scenarios(
        [
            AlignedPowerRow(datetime(2025, 7, 31, 23, 30, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 8, 1, 0, 30, tzinfo=UTC), 1.0, 0.0, 0.0, ""),
            AlignedPowerRow(datetime(2025, 8, 1, 1, 0, tzinfo=UTC), 0.0, 0.0, 0.0, ""),
        ],
        battery_capacities_kwh=[1.0],
        efficiency=1.0,
    )
    return persist_simulation_result(
        db_path=simulation_db,
        ingestion_db_path=simulation_db.parent / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 7, 31, 23, 30, tzinfo=UTC),
        query_end_utc=datetime(2025, 8, 1, 1, 0, tzinfo=UTC),
        result=result,
        persist_steps=True,
    )
