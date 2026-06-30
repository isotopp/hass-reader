from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hass_reader.cache import SEED_METRIC_SOURCES


CSV_FIELDNAMES = [
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
PRIMARY_MAPPING_ROLES = (
    "grid_import_power",
    "grid_export_power",
    "solar_production_power",
)


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
    source_metadata: SourceMetadata


@dataclass(frozen=True)
class MetricMapping:
    role: str
    entity_id: str
    is_default: bool


@dataclass(frozen=True)
class SourceMetadata:
    influx_url: str | None
    influx_database: str | None
    ingestion_query_start_utc: str | None
    ingestion_query_end_utc: str | None
    mappings: dict[str, MetricMapping]

    @property
    def non_default_mapping_roles(self) -> list[str]:
        return [
            role
            for role in PRIMARY_MAPPING_ROLES
            if role in self.mappings and not self.mappings[role].is_default
        ]


def format_human_summary(report: SimulationReport) -> str:
    lines = [
        f"Simulation run {report.run_id}",
        "Range:",
        f"  simulation: {report.query_start_utc}..{report.query_end_utc}",
        "Source:",
        *_format_source_metadata(report.source_metadata),
        "Metric mapping:",
        *_format_metric_mappings(report.source_metadata),
        "",
        "Scenario  Cap kWh  Import kWh  Export kWh  Avoid import  Avoid export",
        _format_scenario_row(report.baseline),
    ]
    lines.extend(_format_scenario_row(scenario) for scenario in report.battery_scenarios)
    return "\n".join(lines)


def write_csv_report(
    report: SimulationReport,
    output_path: str | Path,
    *,
    overwrite: bool,
    numbersapp: bool = False,
) -> None:
    resolved_path = Path(output_path)
    if resolved_path.exists() and not overwrite:
        raise ReportingError(f"output file already exists: {resolved_path}")
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for scenario in report.battery_scenarios:
            writer.writerow(_csv_row(report, scenario, numbersapp=numbersapp))


def load_simulation_report(db_path: str | Path, *, run_id: int | None = None) -> SimulationReport:
    resolved_path = Path(db_path)
    if not resolved_path.exists():
        raise ReportingError(f"simulation database not found: {resolved_path}")

    with sqlite3.connect(resolved_path) as connection:
        run = _load_run(connection, run_id=run_id)
        raw_scenarios = _load_scenarios(connection, run_id=run.id)
    source_metadata = _load_source_metadata(Path(run.ingestion_db_path))

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
        source_metadata=source_metadata,
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


def _format_scenario_row(scenario: ReportScenario) -> str:
    name = "Baseline" if scenario.is_baseline else scenario.name
    return (
        f"{name:<8} "
        f"{scenario.capacity_kwh:>7.2f} "
        f"{scenario.imported_kwh:>11.2f} "
        f"{scenario.exported_kwh:>11.2f} "
        f"{scenario.avoided_import_kwh:>13.2f} "
        f"{scenario.avoided_export_kwh:>13.2f}"
    )


def _csv_row(
    report: SimulationReport,
    scenario: ReportScenario,
    *,
    numbersapp: bool,
) -> dict[str, str]:
    source_metadata = report.source_metadata
    return {
        "run_id": str(report.run_id),
        "query_start_utc": _format_csv_timestamp(
            report.query_start_utc,
            numbersapp=numbersapp,
        ),
        "query_end_utc": _format_csv_timestamp(
            report.query_end_utc,
            numbersapp=numbersapp,
        ),
        "scenario_name": scenario.name,
        "capacity_kwh": _format_decimal(scenario.capacity_kwh),
        "efficiency": _format_decimal(scenario.efficiency),
        "imported_kwh": _format_decimal(scenario.imported_kwh),
        "exported_kwh": _format_decimal(scenario.exported_kwh),
        "charged_kwh": _format_decimal(scenario.charged_kwh),
        "discharged_kwh": _format_decimal(scenario.discharged_kwh),
        "losses_kwh": _format_decimal(scenario.losses_kwh),
        "ending_soc_kwh": _format_decimal(scenario.ending_soc_kwh),
        "avoided_import_kwh": _format_decimal(scenario.avoided_import_kwh),
        "avoided_export_kwh": _format_decimal(scenario.avoided_export_kwh),
        "influx_url": source_metadata.influx_url or "",
        "influx_database": source_metadata.influx_database or "",
        "ingestion_query_start_utc": _format_optional_csv_timestamp(
            source_metadata.ingestion_query_start_utc,
            numbersapp=numbersapp,
        ),
        "ingestion_query_end_utc": _format_optional_csv_timestamp(
            source_metadata.ingestion_query_end_utc,
            numbersapp=numbersapp,
        ),
        "grid_import_entity_id": _mapping_entity_id(source_metadata, "grid_import_power"),
        "grid_export_entity_id": _mapping_entity_id(source_metadata, "grid_export_power"),
        "solar_production_entity_id": _mapping_entity_id(
            source_metadata,
            "solar_production_power",
        ),
        "non_default_mappings": ",".join(source_metadata.non_default_mapping_roles),
    }


def _format_decimal(value: float) -> str:
    return f"{value:.12g}"


def _format_optional_csv_timestamp(value: str | None, *, numbersapp: bool) -> str:
    if value is None:
        return ""
    return _format_csv_timestamp(value, numbersapp=numbersapp)


def _format_csv_timestamp(value: str, *, numbersapp: bool) -> str:
    if not numbersapp:
        return value
    return _parse_utc_timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_source_metadata(ingestion_db_path: Path) -> SourceMetadata:
    default_sources = {source.role: source for source in SEED_METRIC_SOURCES}
    if not ingestion_db_path.exists():
        return SourceMetadata(
            influx_url=None,
            influx_database=None,
            ingestion_query_start_utc=None,
            ingestion_query_end_utc=None,
            mappings={},
        )

    try:
        with sqlite3.connect(ingestion_db_path) as connection:
            ingestion_run = connection.execute(
                """
                SELECT influx_url, influx_database, query_start_utc, query_end_utc
                FROM ingestion_run
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            rows = connection.execute(
                """
                SELECT role, entity_id
                FROM metric_source
                WHERE role IN (?, ?, ?)
                """,
                PRIMARY_MAPPING_ROLES,
            ).fetchall()
    except sqlite3.DatabaseError:
        return SourceMetadata(
            influx_url=None,
            influx_database=None,
            ingestion_query_start_utc=None,
            ingestion_query_end_utc=None,
            mappings={},
        )

    mappings = {}
    for row in rows:
        role = str(row[0])
        entity_id = str(row[1])
        mappings[role] = MetricMapping(
            role=role,
            entity_id=entity_id,
            is_default=role in default_sources and default_sources[role].entity_id == entity_id,
        )

    return SourceMetadata(
        influx_url=None if ingestion_run is None else str(ingestion_run[0]),
        influx_database=None if ingestion_run is None else str(ingestion_run[1]),
        ingestion_query_start_utc=None if ingestion_run is None else str(ingestion_run[2]),
        ingestion_query_end_utc=None if ingestion_run is None else str(ingestion_run[3]),
        mappings=mappings,
    )


def _format_source_metadata(source_metadata: SourceMetadata) -> list[str]:
    if source_metadata.influx_url is None or source_metadata.influx_database is None:
        return ["  unavailable"]
    return [
        f"  url: {source_metadata.influx_url}",
        f"  database: {source_metadata.influx_database}",
        (
            "  ingestion range: "
            f"{source_metadata.ingestion_query_start_utc}.."
            f"{source_metadata.ingestion_query_end_utc}"
        ),
    ]


def _format_metric_mappings(source_metadata: SourceMetadata) -> list[str]:
    lines = []
    for role in PRIMARY_MAPPING_ROLES:
        mapping = source_metadata.mappings.get(role)
        label_role = role.removesuffix("_power").replace("_", " ")
        if mapping is None:
            lines.append(f"  {label_role}: unavailable")
            continue
        label = "default" if mapping.is_default else "non-default"
        lines.append(f"  {label_role}: {mapping.entity_id} ({label})")
    return lines


def _mapping_entity_id(source_metadata: SourceMetadata, role: str) -> str:
    mapping = source_metadata.mappings.get(role)
    if mapping is None:
        return ""
    return mapping.entity_id
