from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


class ReportingError(ValueError):
    pass


@dataclass(frozen=True)
class ReportScenario:
    name: str
    capacity_kwh: float
    efficiency: float
    is_baseline: bool
    imported_kwh: float
    exported_kwh: float
    charged_kwh: float
    discharged_kwh: float
    losses_kwh: float
    ending_soc_kwh: float
    avoided_import_kwh: float
    avoided_export_kwh: float


@dataclass(frozen=True)
class SimulationReport:
    run_id: int
    ingestion_db_path: str
    query_start_utc: str
    query_end_utc: str
    baseline: ReportScenario
    battery_scenarios: list[ReportScenario]


def load_simulation_report(db_path: str | Path, *, run_id: int | None = None) -> SimulationReport:
    resolved_path = Path(db_path)
    if not resolved_path.exists():
        raise ReportingError(f"simulation database not found: {resolved_path}")

    with sqlite3.connect(resolved_path) as connection:
        run = _load_run(connection, run_id=run_id)
        raw_scenarios = _load_scenarios(connection, run_id=run.id)

    baseline = next((scenario for scenario in raw_scenarios if scenario.is_baseline), None)
    if baseline is None:
        raise ReportingError(f"simulation run {run.id} has no baseline scenario")

    battery_scenarios = [
        _with_avoided_energy(scenario, baseline)
        for scenario in raw_scenarios
        if not scenario.is_baseline
    ]
    return SimulationReport(
        run_id=run.id,
        ingestion_db_path=run.ingestion_db_path,
        query_start_utc=run.query_start_utc,
        query_end_utc=run.query_end_utc,
        baseline=_with_avoided_energy(baseline, baseline),
        battery_scenarios=battery_scenarios,
    )


@dataclass(frozen=True)
class _RunRecord:
    id: int
    ingestion_db_path: str
    query_start_utc: str
    query_end_utc: str
    status: str


def _load_run(connection: sqlite3.Connection, *, run_id: int | None) -> _RunRecord:
    if run_id is None:
        row = connection.execute(
            """
            SELECT id, ingestion_db_path, query_start_utc, query_end_utc, status
            FROM simulation_run
            WHERE status = 'completed'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise ReportingError("no completed simulation runs found")
    else:
        row = connection.execute(
            """
            SELECT id, ingestion_db_path, query_start_utc, query_end_utc, status
            FROM simulation_run
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ReportingError(f"simulation run {run_id} not found")

    run = _RunRecord(
        id=int(row[0]),
        ingestion_db_path=str(row[1]),
        query_start_utc=str(row[2]),
        query_end_utc=str(row[3]),
        status=str(row[4]),
    )
    if run.status != "completed":
        raise ReportingError(f"simulation run {run.id} is {run.status}")
    return run


def _load_scenarios(connection: sqlite3.Connection, *, run_id: int) -> list[ReportScenario]:
    rows = connection.execute(
        """
        SELECT
            simulation_scenario.name,
            simulation_scenario.capacity_kwh,
            simulation_scenario.efficiency,
            simulation_scenario.is_baseline,
            simulation_summary.imported_kwh,
            simulation_summary.exported_kwh,
            simulation_summary.charged_kwh,
            simulation_summary.discharged_kwh,
            simulation_summary.losses_kwh,
            simulation_summary.ending_soc_kwh
        FROM simulation_scenario
        JOIN simulation_summary ON simulation_summary.scenario_id = simulation_scenario.id
        WHERE simulation_scenario.simulation_run_id = ?
        ORDER BY simulation_scenario.is_baseline DESC, simulation_scenario.capacity_kwh
        """,
        (run_id,),
    ).fetchall()
    return [
        ReportScenario(
            name=str(row[0]),
            capacity_kwh=float(row[1]),
            efficiency=float(row[2]),
            is_baseline=bool(row[3]),
            imported_kwh=float(row[4]),
            exported_kwh=float(row[5]),
            charged_kwh=float(row[6]),
            discharged_kwh=float(row[7]),
            losses_kwh=float(row[8]),
            ending_soc_kwh=float(row[9]),
            avoided_import_kwh=0.0,
            avoided_export_kwh=0.0,
        )
        for row in rows
    ]


def _with_avoided_energy(
    scenario: ReportScenario,
    baseline: ReportScenario,
) -> ReportScenario:
    return ReportScenario(
        name=scenario.name,
        capacity_kwh=scenario.capacity_kwh,
        efficiency=scenario.efficiency,
        is_baseline=scenario.is_baseline,
        imported_kwh=scenario.imported_kwh,
        exported_kwh=scenario.exported_kwh,
        charged_kwh=scenario.charged_kwh,
        discharged_kwh=scenario.discharged_kwh,
        losses_kwh=scenario.losses_kwh,
        ending_soc_kwh=scenario.ending_soc_kwh,
        avoided_import_kwh=baseline.imported_kwh - scenario.imported_kwh,
        avoided_export_kwh=baseline.exported_kwh - scenario.exported_kwh,
    )
