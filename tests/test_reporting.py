from datetime import UTC, datetime

import pytest

from hass_reader.battery_simulation import AlignedPowerRow, simulate_scenarios
from hass_reader.reporting import ReportingError, load_simulation_report
from hass_reader.simulation_db import persist_failed_simulation_run, persist_simulation_result


def test_load_simulation_report_uses_latest_completed_run_and_derives_avoided_energy(
    tmp_path,
) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    ingestion_db = tmp_path / "ingestion.sqlite3"
    _persist_completed_run(simulation_db, ingestion_db, battery_capacities_kwh=[1.0])
    latest_run_id = _persist_completed_run(
        simulation_db, ingestion_db, battery_capacities_kwh=[2.0]
    )

    report = load_simulation_report(simulation_db)

    assert report.run_id == latest_run_id
    assert report.query_start_utc == "2025-06-30T22:00:00Z"
    assert report.query_end_utc == "2026-06-30T22:00:00Z"
    assert report.baseline.imported_kwh == 2.0
    assert report.baseline.exported_kwh == 2.0
    assert [(scenario.name, scenario.capacity_kwh) for scenario in report.battery_scenarios] == [
        ("2kWh", 2.0)
    ]
    assert report.battery_scenarios[0].imported_kwh == pytest.approx(0.4)
    assert report.battery_scenarios[0].exported_kwh == 0.0
    assert report.battery_scenarios[0].avoided_import_kwh == pytest.approx(1.6)
    assert report.battery_scenarios[0].avoided_export_kwh == 2.0


def test_load_simulation_report_can_select_completed_run_by_id(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    ingestion_db = tmp_path / "ingestion.sqlite3"
    first_run_id = _persist_completed_run(simulation_db, ingestion_db, battery_capacities_kwh=[1.0])
    _persist_completed_run(simulation_db, ingestion_db, battery_capacities_kwh=[2.0])

    report = load_simulation_report(simulation_db, run_id=first_run_id)

    assert report.run_id == first_run_id
    assert [(scenario.name, scenario.capacity_kwh) for scenario in report.battery_scenarios] == [
        ("1kWh", 1.0)
    ]


def test_load_simulation_report_rejects_failed_runs(tmp_path) -> None:
    simulation_db = tmp_path / "simulation.sqlite3"
    failed_run_id = persist_failed_simulation_run(
        db_path=simulation_db,
        ingestion_db_path=tmp_path / "ingestion.sqlite3",
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        error_message="missing aligned rows",
    )

    with pytest.raises(ReportingError, match=f"simulation run {failed_run_id} is failed"):
        load_simulation_report(simulation_db, run_id=failed_run_id)


def _persist_completed_run(
    simulation_db, ingestion_db, *, battery_capacities_kwh: list[float]
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
        ingestion_db_path=ingestion_db,
        query_start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        query_end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        result=result,
    )
