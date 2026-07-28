from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

AUTONOMY_ROLLUP_CSV_FIELDNAMES = [
    "run_id",
    "scenario_name",
    "capacity_kwh",
    "efficiency",
    "bucket_kind",
    "bucket_start_utc",
    "bucket_end_utc",
    "autonomy_percent",
    "autonomous_hours",
    "valid_hours",
    "non_autonomous_hours",
    "imported_kwh",
    "threshold_watts",
]


class AutonomyReportingError(ValueError):
    pass


@dataclass(frozen=True)
class AutonomyScenario:
    id: int
    name: str
    capacity_kwh: float
    efficiency: float
    is_baseline: bool


@dataclass(frozen=True)
class AutonomyStepAvailability:
    run_id: int
    scenarios: list[AutonomyScenario]


@dataclass(frozen=True)
class AutonomyRollupRow:
    scenario_name: str
    capacity_kwh: float
    efficiency: float
    bucket_kind: str
    bucket_start_utc: str
    bucket_end_utc: str
    autonomy_percent: float
    autonomous_hours: float
    valid_hours: float
    non_autonomous_hours: float
    imported_kwh: float


@dataclass(frozen=True)
class AutonomyRollupReport:
    run_id: int
    bucket_kind: str
    threshold_watts: float
    scenarios: list[AutonomyScenario]
    rows: list[AutonomyRollupRow]


def format_autonomy_rollup_summary(report: AutonomyRollupReport) -> str:
    lines = [
        (
            f"Autonomy report run {report.run_id} bucket={report.bucket_kind} "
            f"threshold={report.threshold_watts:g} W"
        ),
        "Bucket      Scenario  Cap kWh  Autonomy  Auto h  Valid h  Import kWh",
    ]
    lines.extend(_format_autonomy_rollup_row(row) for row in report.rows)
    return "\n".join(lines)


def write_autonomy_rollup_csv(
    report: AutonomyRollupReport,
    output_path: str | Path,
    *,
    overwrite: bool,
    numbersapp: bool = False,
) -> None:
    resolved_path = Path(output_path)
    if resolved_path.exists() and not overwrite:
        raise AutonomyReportingError(f"output file already exists: {resolved_path}")
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=AUTONOMY_ROLLUP_CSV_FIELDNAMES)
        writer.writeheader()
        for row in report.rows:
            writer.writerow(_autonomy_rollup_csv_row(report, row, numbersapp=numbersapp))


@dataclass
class _AutonomyBucket:
    scenario: AutonomyScenario
    start_utc: datetime
    end_utc: datetime
    valid_seconds: float = 0.0
    autonomous_seconds: float = 0.0
    imported_kwh: float = 0.0

    @property
    def autonomy_percent(self) -> float:
        if self.valid_seconds == 0:
            return 0.0
        return self.autonomous_seconds / self.valid_seconds * 100

    @property
    def valid_hours(self) -> float:
        return self.valid_seconds / 3600

    @property
    def autonomous_hours(self) -> float:
        return self.autonomous_seconds / 3600

    @property
    def non_autonomous_hours(self) -> float:
        return (self.valid_seconds - self.autonomous_seconds) / 3600

    def add(self, *, seconds: float, autonomous: bool, imported_kwh: float) -> None:
        self.valid_seconds += seconds
        if autonomous:
            self.autonomous_seconds += seconds
        self.imported_kwh += imported_kwh


@dataclass(frozen=True)
class _StepInterval:
    start_utc: datetime
    end_utc: datetime
    import_kwh: float


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


def load_autonomy_rollups(
    db_path: str | Path,
    *,
    bucket_kind: str,
    run_id: int | None = None,
    selectors: list[str] | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    timezone_name: str = "UTC",
    threshold_watts: float = 30.0,
) -> AutonomyRollupReport:
    if bucket_kind not in {"day", "month"}:
        raise AutonomyReportingError("invalid bucket_kind: expected one of day, month")
    if threshold_watts < 0:
        raise AutonomyReportingError("invalid threshold_watts: expected a non-negative number")

    availability = check_autonomy_step_availability(
        db_path,
        run_id=run_id,
        selectors=selectors,
    )
    timezone = ZoneInfo(timezone_name)

    with sqlite3.connect(db_path) as connection:
        run_end_utc = _load_run_end_utc(connection, run_id=availability.run_id)
        buckets: dict[tuple[str, int], _AutonomyBucket] = {}
        for scenario in availability.scenarios:
            intervals = _load_step_intervals(
                connection,
                scenario_id=scenario.id,
                run_end_utc=run_end_utc,
            )
            for interval in intervals:
                _add_interval_to_daily_buckets(
                    buckets,
                    bucket_kind=bucket_kind,
                    scenario=scenario,
                    interval=interval,
                    timezone=timezone,
                    threshold_watts=threshold_watts,
                    report_start_utc=start_utc,
                    report_end_utc=end_utc,
                )

    return AutonomyRollupReport(
        run_id=availability.run_id,
        bucket_kind=bucket_kind,
        threshold_watts=threshold_watts,
        scenarios=availability.scenarios,
        rows=[
            _bucket_to_row(bucket_kind=bucket_kind, bucket=buckets[key]) for key in sorted(buckets)
        ],
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


def _load_run_end_utc(connection: sqlite3.Connection, *, run_id: int) -> datetime:
    row = connection.execute(
        """
        SELECT query_end_utc
        FROM simulation_run
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise AutonomyReportingError(f"simulation run {run_id} not found")
    return _parse_utc_timestamp(str(row[0]))


def _resolve_scenarios(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    selectors: list[str],
) -> list[AutonomyScenario]:
    rows = connection.execute(
        """
        SELECT id, name, capacity_kwh, efficiency, is_baseline
        FROM simulation_scenario
        WHERE simulation_run_id = ?
        ORDER BY is_baseline DESC, capacity_kwh
        """,
        (run_id,),
    ).fetchall()
    scenarios = [
        AutonomyScenario(
            id=int(row[0]),
            name=str(row[1]),
            capacity_kwh=float(row[2]),
            efficiency=float(row[3]),
            is_baseline=bool(row[4]),
        )
        for row in rows
    ]
    if not selectors:
        return scenarios

    baseline_scenarios = [scenario for scenario in scenarios if scenario.is_baseline]
    selected = [
        scenario
        for scenario in scenarios
        if not scenario.is_baseline
        and (scenario.name in selectors or f"{scenario.capacity_kwh:g}kWh" in selectors)
    ]
    if not selected:
        raise AutonomyReportingError(f"no selected battery scenarios found for run {run_id}")
    return [*baseline_scenarios, *selected]


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


def _load_step_intervals(
    connection: sqlite3.Connection,
    *,
    scenario_id: int,
    run_end_utc: datetime,
) -> list[_StepInterval]:
    rows = connection.execute(
        """
        SELECT timestamp_utc, import_kwh
        FROM simulation_step
        WHERE scenario_id = ?
        ORDER BY timestamp_utc
        """,
        (scenario_id,),
    ).fetchall()
    intervals = []
    for index, row in enumerate(rows):
        start_utc = _parse_utc_timestamp(str(row[0]))
        end_utc = (
            _parse_utc_timestamp(str(rows[index + 1][0])) if index + 1 < len(rows) else run_end_utc
        )
        if end_utc > start_utc:
            intervals.append(
                _StepInterval(
                    start_utc=start_utc,
                    end_utc=end_utc,
                    import_kwh=float(row[1]),
                )
            )
    return intervals


def _add_interval_to_daily_buckets(
    buckets: dict[tuple[str, int], _AutonomyBucket],
    *,
    bucket_kind: str,
    scenario: AutonomyScenario,
    interval: _StepInterval,
    timezone: ZoneInfo,
    threshold_watts: float,
    report_start_utc: datetime | None,
    report_end_utc: datetime | None,
) -> None:
    start_utc = max(
        interval.start_utc,
        report_start_utc.astimezone(UTC) if report_start_utc is not None else interval.start_utc,
    )
    end_utc = min(
        interval.end_utc,
        report_end_utc.astimezone(UTC) if report_end_utc is not None else interval.end_utc,
    )
    if end_utc <= start_utc:
        return

    interval_seconds = (interval.end_utc - interval.start_utc).total_seconds()
    interval_hours = interval_seconds / 3600
    import_power_watts = (
        interval.import_kwh / interval_hours * 1000 if interval_hours > 0 else float("inf")
    )
    autonomous = import_power_watts <= threshold_watts

    current_start = start_utc
    while current_start < end_utc:
        bucket_start, bucket_end = _bucket_bounds(
            current_start,
            bucket_kind=bucket_kind,
            timezone=timezone,
        )
        segment_end = min(end_utc, bucket_end)
        segment_seconds = (segment_end - current_start).total_seconds()
        segment_import_kwh = interval.import_kwh * (segment_seconds / interval_seconds)
        bucket_key = (_format_timestamp(bucket_start), scenario.id)
        if bucket_key not in buckets:
            buckets[bucket_key] = _AutonomyBucket(
                scenario=scenario,
                start_utc=bucket_start,
                end_utc=bucket_end,
            )
        buckets[bucket_key].add(
            seconds=segment_seconds,
            autonomous=autonomous,
            imported_kwh=segment_import_kwh,
        )
        current_start = segment_end


def _bucket_bounds(
    timestamp_utc: datetime,
    *,
    bucket_kind: str,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    local_timestamp = timestamp_utc.astimezone(timezone)
    if bucket_kind == "day":
        local_start = local_timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
        return local_start.astimezone(UTC), local_end.astimezone(UTC)

    local_start = local_timestamp.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    if local_start.month == 12:
        local_end = local_start.replace(year=local_start.year + 1, month=1)
    else:
        local_end = local_start.replace(month=local_start.month + 1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def _bucket_to_row(*, bucket_kind: str, bucket: _AutonomyBucket) -> AutonomyRollupRow:
    return AutonomyRollupRow(
        scenario_name=bucket.scenario.name,
        capacity_kwh=bucket.scenario.capacity_kwh,
        efficiency=bucket.scenario.efficiency,
        bucket_kind=bucket_kind,
        bucket_start_utc=_format_timestamp(bucket.start_utc),
        bucket_end_utc=_format_timestamp(bucket.end_utc),
        autonomy_percent=bucket.autonomy_percent,
        autonomous_hours=bucket.autonomous_hours,
        valid_hours=bucket.valid_hours,
        non_autonomous_hours=bucket.non_autonomous_hours,
        imported_kwh=bucket.imported_kwh,
    )


def _format_autonomy_rollup_row(row: AutonomyRollupRow) -> str:
    bucket_label = (
        row.bucket_start_utc[:7] if row.bucket_kind == "month" else row.bucket_start_utc[:10]
    )
    return (
        f"{bucket_label:<10} "
        f"{row.scenario_name:<8} "
        f"{row.capacity_kwh:>7.2f} "
        f"{row.autonomy_percent:>9.2f}% "
        f"{row.autonomous_hours:>7.2f} "
        f"{row.valid_hours:>8.2f} "
        f"{row.imported_kwh:>11.2f}"
    )


def _autonomy_rollup_csv_row(
    report: AutonomyRollupReport,
    row: AutonomyRollupRow,
    *,
    numbersapp: bool,
) -> dict[str, str]:
    return {
        "run_id": str(report.run_id),
        "scenario_name": row.scenario_name,
        "capacity_kwh": _format_decimal(row.capacity_kwh),
        "efficiency": _format_decimal(row.efficiency),
        "bucket_kind": row.bucket_kind,
        "bucket_start_utc": _format_csv_timestamp(
            row.bucket_start_utc,
            numbersapp=numbersapp,
        ),
        "bucket_end_utc": _format_csv_timestamp(
            row.bucket_end_utc,
            numbersapp=numbersapp,
        ),
        "autonomy_percent": _format_decimal(row.autonomy_percent),
        "autonomous_hours": _format_decimal(row.autonomous_hours),
        "valid_hours": _format_decimal(row.valid_hours),
        "non_autonomous_hours": _format_decimal(row.non_autonomous_hours),
        "imported_kwh": _format_decimal(row.imported_kwh),
        "threshold_watts": _format_decimal(report.threshold_watts),
    }


def _format_decimal(value: float) -> str:
    return f"{value:.12g}"


def _format_csv_timestamp(value: str, *, numbersapp: bool) -> str:
    if not numbersapp:
        return value
    return _parse_utc_timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _parse_utc_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
