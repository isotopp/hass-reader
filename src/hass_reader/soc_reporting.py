from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hass_reader.simulation_db import initialize_simulation_db


class SocReportingError(ValueError):
    pass


@dataclass(frozen=True)
class SocScenario:
    id: int
    name: str
    capacity_kwh: float
    efficiency: float


@dataclass(frozen=True)
class SocStepAvailability:
    run_id: int
    scenarios: list[SocScenario]


@dataclass(frozen=True)
class SocRollupGenerationResult:
    run_id: int
    bucket_kind: str
    rollups_written: int


@dataclass(frozen=True)
class SocRollupRow:
    scenario_name: str
    capacity_kwh: float
    efficiency: float
    bucket_kind: str
    bucket_start_utc: str
    bucket_end_utc: str
    min_soc_kwh: float
    avg_soc_kwh: float
    max_soc_kwh: float
    sample_count: int


@dataclass(frozen=True)
class SocRollupReport:
    run_id: int
    bucket_kind: str
    scenarios: list[SocScenario]
    rows: list[SocRollupRow]


def format_soc_rollup_summary(report: SocRollupReport) -> str:
    lines = [
        f"SoC report run {report.run_id} bucket={report.bucket_kind}",
        "Date        Scenario  Min kWh  Avg kWh  Max kWh  Samples  State",
    ]
    lines.extend(_format_soc_rollup_row(row) for row in report.rows)
    lines.extend(["", "SoC chart (average)"])
    lines.extend(_format_soc_chart(report))
    return "\n".join(lines)


def check_soc_step_availability(
    db_path: str | Path,
    *,
    run_id: int | None = None,
    selectors: list[str] | None = None,
) -> SocStepAvailability:
    resolved_path = Path(db_path)
    if not resolved_path.exists():
        raise SocReportingError(f"simulation database not found: {resolved_path}")

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
        raise SocReportingError(
            "SoC reporting requires simulation step data; rerun "
            f"`uv run hass-reader simulate --store-steps` for: {names}"
        )
    return SocStepAvailability(run_id=selected_run_id, scenarios=scenarios)


def generate_soc_rollups(
    db_path: str | Path,
    *,
    bucket_kind: str,
    run_id: int | None = None,
    selectors: list[str] | None = None,
    timezone_name: str = "UTC",
) -> SocRollupGenerationResult:
    if bucket_kind not in {"hour", "day"}:
        raise SocReportingError("invalid bucket_kind: expected one of hour, day")
    initialize_simulation_db(db_path)
    availability = check_soc_step_availability(db_path, run_id=run_id, selectors=selectors)
    timezone = ZoneInfo(timezone_name)

    rollups_written = 0
    with sqlite3.connect(db_path) as connection:
        for scenario in availability.scenarios:
            buckets = _load_soc_buckets(
                connection,
                scenario_id=scenario.id,
                bucket_kind=bucket_kind,
                timezone=timezone,
            )
            _replace_rollups(
                connection,
                run_id=availability.run_id,
                scenario_id=scenario.id,
                bucket_kind=bucket_kind,
                buckets=buckets,
            )
            rollups_written += len(buckets)
    return SocRollupGenerationResult(
        run_id=availability.run_id,
        bucket_kind=bucket_kind,
        rollups_written=rollups_written,
    )


def load_soc_rollups(
    db_path: str | Path,
    *,
    bucket_kind: str,
    run_id: int | None = None,
    selectors: list[str] | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> SocRollupReport:
    if bucket_kind not in {"hour", "day"}:
        raise SocReportingError("invalid bucket_kind: expected one of hour, day")
    resolved_path = Path(db_path)
    if not resolved_path.exists():
        raise SocReportingError(f"simulation database not found: {resolved_path}")

    with sqlite3.connect(resolved_path) as connection:
        selected_run_id = _resolve_run_id(connection, run_id=run_id)
        scenarios = _resolve_scenarios(
            connection,
            run_id=selected_run_id,
            selectors=selectors or [],
        )
        rows = _load_rollup_rows(
            connection,
            run_id=selected_run_id,
            scenarios=scenarios,
            bucket_kind=bucket_kind,
            start_utc=start_utc,
            end_utc=end_utc,
        )

    if not rows:
        raise SocReportingError(
            "no SoC rollups found; run `uv run hass-reader soc-rollup` for this run first"
        )
    return SocRollupReport(
        run_id=selected_run_id,
        bucket_kind=bucket_kind,
        scenarios=scenarios,
        rows=rows,
    )


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
            raise SocReportingError("no completed simulation runs found")
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
        raise SocReportingError(f"simulation run {run_id} not found")
    if str(row[0]) != "completed":
        raise SocReportingError(f"simulation run {run_id} is {row[0]}")
    return run_id


def _resolve_scenarios(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    selectors: list[str],
) -> list[SocScenario]:
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
        SocScenario(
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
        raise SocReportingError(f"no selected battery scenarios found for run {run_id}")
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


@dataclass
class _SocBucket:
    start_utc: datetime
    end_utc: datetime
    min_soc_kwh: float
    max_soc_kwh: float
    total_soc_kwh: float
    sample_count: int

    @property
    def avg_soc_kwh(self) -> float:
        return self.total_soc_kwh / self.sample_count

    def add(self, soc_kwh: float) -> None:
        self.min_soc_kwh = min(self.min_soc_kwh, soc_kwh)
        self.max_soc_kwh = max(self.max_soc_kwh, soc_kwh)
        self.total_soc_kwh += soc_kwh
        self.sample_count += 1


def _load_soc_buckets(
    connection: sqlite3.Connection,
    *,
    scenario_id: int,
    bucket_kind: str,
    timezone: ZoneInfo,
) -> list[_SocBucket]:
    rows = connection.execute(
        """
        SELECT timestamp_utc, soc_kwh
        FROM simulation_step
        WHERE scenario_id = ?
        ORDER BY timestamp_utc
        """,
        (scenario_id,),
    )
    buckets: dict[str, _SocBucket] = {}
    for timestamp_text, soc_kwh in rows:
        bucket_start, bucket_end = _bucket_bounds(
            _parse_utc_timestamp(str(timestamp_text)),
            bucket_kind=bucket_kind,
            timezone=timezone,
        )
        bucket_key = _format_timestamp(bucket_start)
        if bucket_key not in buckets:
            buckets[bucket_key] = _SocBucket(
                start_utc=bucket_start,
                end_utc=bucket_end,
                min_soc_kwh=float(soc_kwh),
                max_soc_kwh=float(soc_kwh),
                total_soc_kwh=float(soc_kwh),
                sample_count=1,
            )
        else:
            buckets[bucket_key].add(float(soc_kwh))
    return [buckets[key] for key in sorted(buckets)]


def _replace_rollups(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    scenario_id: int,
    bucket_kind: str,
    buckets: list[_SocBucket],
) -> None:
    connection.executemany(
        """
        INSERT OR REPLACE INTO simulation_soc_rollup (
            simulation_run_id,
            simulation_scenario_id,
            bucket_kind,
            bucket_start_utc,
            bucket_end_utc,
            min_soc_kwh,
            avg_soc_kwh,
            max_soc_kwh,
            sample_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                scenario_id,
                bucket_kind,
                _format_timestamp(bucket.start_utc),
                _format_timestamp(bucket.end_utc),
                bucket.min_soc_kwh,
                bucket.avg_soc_kwh,
                bucket.max_soc_kwh,
                bucket.sample_count,
            )
            for bucket in buckets
        ],
    )


def _load_rollup_rows(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    scenarios: list[SocScenario],
    bucket_kind: str,
    start_utc: datetime | None,
    end_utc: datetime | None,
) -> list[SocRollupRow]:
    scenario_ids = {scenario.id for scenario in scenarios}
    scenario_by_id = {scenario.id: scenario for scenario in scenarios}
    filters = ["simulation_run_id = ?", "bucket_kind = ?"]
    parameters: list[object] = [run_id, bucket_kind]
    if start_utc is not None:
        filters.append("bucket_start_utc >= ?")
        parameters.append(_format_timestamp(start_utc))
    if end_utc is not None:
        filters.append("bucket_start_utc < ?")
        parameters.append(_format_timestamp(end_utc))
    rows = connection.execute(
        f"""
        SELECT
            simulation_scenario_id,
            bucket_kind,
            bucket_start_utc,
            bucket_end_utc,
            min_soc_kwh,
            avg_soc_kwh,
            max_soc_kwh,
            sample_count
        FROM simulation_soc_rollup
        WHERE {" AND ".join(filters)}
        ORDER BY bucket_start_utc, simulation_scenario_id
        """,
        parameters,
    ).fetchall()
    result = []
    for row in rows:
        scenario_id = int(row[0])
        if scenario_id not in scenario_ids:
            continue
        scenario = scenario_by_id[scenario_id]
        result.append(
            SocRollupRow(
                scenario_name=scenario.name,
                capacity_kwh=scenario.capacity_kwh,
                efficiency=scenario.efficiency,
                bucket_kind=str(row[1]),
                bucket_start_utc=str(row[2]),
                bucket_end_utc=str(row[3]),
                min_soc_kwh=float(row[4]),
                avg_soc_kwh=float(row[5]),
                max_soc_kwh=float(row[6]),
                sample_count=int(row[7]),
            )
        )
    return result


def _bucket_bounds(
    timestamp_utc: datetime,
    *,
    bucket_kind: str,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    if bucket_kind == "hour":
        start = timestamp_utc.replace(minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=1)

    local_timestamp = timestamp_utc.astimezone(timezone)
    local_start = local_timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def _parse_utc_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _format_soc_rollup_row(row: SocRollupRow) -> str:
    flags = []
    if row.min_soc_kwh <= 1e-9:
        flags.append("empty")
    if row.max_soc_kwh >= row.capacity_kwh - 1e-9:
        flags.append("full")
    state = ",".join(flags) if flags else "-"
    return (
        f"{row.bucket_start_utc[:10]}  "
        f"{row.scenario_name:<8} "
        f"{row.min_soc_kwh:>7.2f} "
        f"{row.avg_soc_kwh:>8.2f} "
        f"{row.max_soc_kwh:>8.2f} "
        f"{row.sample_count:>8}  "
        f"{state}"
    )


def _format_soc_chart(report: SocRollupReport) -> list[str]:
    rows_by_scenario: dict[str, list[SocRollupRow]] = {}
    for row in report.rows:
        rows_by_scenario.setdefault(row.scenario_name, []).append(row)

    chart_lines = []
    for scenario in report.scenarios:
        rows = rows_by_scenario.get(scenario.name, [])
        if not rows:
            continue
        bars = " ".join(_soc_bar(row.avg_soc_kwh, row.capacity_kwh) for row in rows)
        first = rows[0].bucket_start_utc[:10]
        last = rows[-1].bucket_start_utc[:10]
        chart_lines.append(f"{scenario.name:<8} | {bars}  {first}..{last}")
    return chart_lines


def _soc_bar(value: float, capacity: float) -> str:
    if capacity <= 0:
        return ".........."
    filled = round(max(0.0, min(1.0, value / capacity)) * 10)
    return "#" * filled + "." * (10 - filled)
