from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


class AutonomyReportingError(ValueError):
    pass


@dataclass(frozen=True)
class AutonomyScenario:
    id: int
    name: str
    capacity_kwh: float
    efficiency: float


@dataclass(frozen=True)
class AutonomyStepAvailability:
    run_id: int
    scenarios: list[AutonomyScenario]


def check_autonomy_step_availability(
    db_path: str | Path,
    *,
    run_id: int | None = None,
    selectors: list[str] | None = None,
) -> AutonomyStepAvailability:
    resolved_path = Path(db_path)
    if not resolved_path.exists():
        raise AutonomyReportingError(f"simulation database not found: {resolved_path}")

    with sqlite3.connect(resolved_path) as connection:
        selected_run_id = _resolve_run_id(connection, run_id=run_id)
        scenarios = _resolve_scenarios(
            connection,
            run_id=selected_run_id,
            selectors=selectors or [],
        )
        missing_step_scenarios = [
            scenario.name for scenario in scenarios if _count_steps(connection, scenario.id) == 0
        ]

    if missing_step_scenarios:
        names = ", ".join(missing_step_scenarios)
        raise AutonomyReportingError(
            "Autonomy reporting requires existing simulation step data; rerun "
            f"`uv run hass-reader simulate --store-steps` for: {names}"
        )
    return AutonomyStepAvailability(run_id=selected_run_id, scenarios=scenarios)


def _resolve_run_id(connection: sqlite3.Connection, *, run_id: int | None) -> int:
    if run_id is None:
        row = connection.execute(
            """
            SELECT id
            FROM simulation_run
            WHERE status = 'completed'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise AutonomyReportingError("no completed simulation runs found")
        return int(row[0])

    row = connection.execute(
        """
        SELECT status
        FROM simulation_run
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise AutonomyReportingError(f"simulation run {run_id} not found")
    if str(row[0]) != "completed":
        raise AutonomyReportingError(f"simulation run {run_id} is {row[0]}")
    return run_id


def _resolve_scenarios(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    selectors: list[str],
) -> list[AutonomyScenario]:
    rows = connection.execute(
        """
        SELECT id, name, capacity_kwh, efficiency
        FROM simulation_scenario
        WHERE simulation_run_id = ? AND is_baseline = 0
        ORDER BY capacity_kwh
        """,
        (run_id,),
    ).fetchall()
    scenarios = [
        AutonomyScenario(
            id=int(row[0]),
            name=str(row[1]),
            capacity_kwh=float(row[2]),
            efficiency=float(row[3]),
        )
        for row in rows
    ]
    if not selectors:
        return scenarios

    selected = [
        scenario
        for scenario in scenarios
        if scenario.name in selectors or f"{scenario.capacity_kwh:g}kWh" in selectors
    ]
    if not selected:
        raise AutonomyReportingError(f"no selected battery scenarios found for run {run_id}")
    return selected


def _count_steps(connection: sqlite3.Connection, scenario_id: int) -> int:
    row = connection.execute(
        """
        SELECT count(*)
        FROM simulation_step
        WHERE scenario_id = ?
        """,
        (scenario_id,),
    ).fetchone()
    if row is None:
        return 0
    return int(row[0])
