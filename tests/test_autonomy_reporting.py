from datetime import UTC, datetime

import pytest

from hass_reader.autonomy_reporting import AutonomyReportingError, check_autonomy_step_availability
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
