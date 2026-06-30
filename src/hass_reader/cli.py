from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hass_reader.battery_simulation import AlignedPowerRow, simulate_scenarios
from hass_reader.cache import SEED_METRIC_SOURCES, resolve_cache_path
from hass_reader.influx import (
    DEFAULT_INFLUX_DATABASE,
    DEFAULT_INFLUX_URL,
    DEFAULT_LOCAL_TIMEZONE,
    InfluxClient,
    InfluxMetric,
    InfluxSample,
    default_query_range,
    format_influx_time,
)
from hass_reader.ingestion import align_primary_power, ingest_raw_metric, normalize_metric
from hass_reader.reporting import format_human_summary, load_simulation_report, write_csv_report
from hass_reader.simulation_db import (
    DEFAULT_SIMULATION_DB_PATH,
    persist_failed_simulation_run,
    persist_simulation_result,
    resolve_simulation_db_path,
)
from hass_reader.soc_reporting import check_soc_step_availability


DEFAULT_MAX_GAP_SECONDS = 900


@dataclass(frozen=True)
class IngestCommandResult:
    fetched: int
    cached: int
    normalized: int
    aligned: int


@dataclass(frozen=True)
class SimulationCommandResult:
    run_id: int
    scenario_count: int


class InfluxRawMetricIngestor:
    def __init__(self, client: InfluxClient) -> None:
        self.client = client

    def query_samples(
        self,
        measurement: str,
        entity_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[InfluxSample]:
        return self.client.query_samples(
            InfluxMetric(measurement=measurement, entity_id=entity_id),
            start_utc=start_utc,
            end_utc=end_utc,
        )


IngestRunner = Callable[[argparse.Namespace], IngestCommandResult]
SimulationRunner = Callable[[argparse.Namespace], SimulationCommandResult]


def main(
    argv: Sequence[str] | None = None,
    *,
    ingest_runner: IngestRunner | None = None,
    simulation_runner: SimulationRunner | None = None,
) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return _exit_code(error.code)
    if args.command == "ingest":
        runner = ingest_runner or run_ingest
        try:
            result = runner(args)
        except Exception as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(
            "ingest complete: "
            f"fetched={result.fetched} cached={result.cached} "
            f"normalized={result.normalized} aligned={result.aligned}"
        )
        return 0
    if args.command == "simulate":
        validation_error = _validate_simulation_args(args)
        if validation_error is not None:
            print(f"error: {validation_error}", file=sys.stderr)
            return 2
        runner = simulation_runner or run_simulation
        try:
            result = runner(args)
        except Exception as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"simulation complete: run_id={result.run_id} scenarios={result.scenario_count}")
        if simulation_runner is None:
            report = load_simulation_report(args.simulation_db, run_id=result.run_id)
            print(format_human_summary(report))
        return 0
    if args.command == "report":
        validation_error = _validate_report_args(args)
        if validation_error is not None:
            print(f"error: {validation_error}", file=sys.stderr)
            return 2
        try:
            report = load_simulation_report(args.simulation_db, run_id=args.run_id)
            if args.output is not None:
                write_csv_report(report, args.output, overwrite=args.overwrite)
        except Exception as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(format_human_summary(report))
        return 0
    if args.command == "soc":
        validation_error = _validate_soc_args(args)
        if validation_error is not None:
            print(f"error: {validation_error}", file=sys.stderr)
            return 2
        try:
            availability = check_soc_step_availability(
                args.simulation_db,
                run_id=args.run_id,
                selectors=args.battery_selectors,
            )
        except Exception as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        scenario_names = ",".join(scenario.name for scenario in availability.scenarios)
        print(f"soc step data available: run_id={availability.run_id} scenarios={scenario_names}")
        return 0
    parser.print_help()
    return 0


def run_ingest(args: argparse.Namespace) -> IngestCommandResult:
    start_utc = _parse_local_boundary(args.start, args.timezone)
    end_utc = _parse_local_boundary(args.end, args.timezone)
    client = InfluxClient(args.influx_url, args.influx_database)
    ingestor = InfluxRawMetricIngestor(client)
    fetched = 0
    cached = 0
    normalized = 0
    window = f"{format_influx_time(start_utc)}..{format_influx_time(end_utc)}"

    for source in SEED_METRIC_SOURCES:
        raw_result = ingest_raw_metric(
            db_path=args.db,
            role=source.role,
            ingestor=ingestor,
            start_utc=start_utc,
            end_utc=end_utc,
            influx_url=args.influx_url,
            influx_database=args.influx_database,
            local_timezone=args.timezone,
            force_refresh=args.force_refresh,
        )
        if raw_result.fetched:
            fetched += 1
        else:
            cached += 1
        fetch_result = "fetched" if raw_result.fetched else "cached"
        print(
            "phase=fetch "
            f"window={window} source={source.role} result={fetch_result} "
            f"samples={raw_result.sample_count}"
        )
        normalization_result = normalize_metric(db_path=args.db, role=source.role)
        normalized += normalization_result.normalized_count
        print(
            "phase=normalize "
            f"window={window} source={source.role} "
            f"samples={normalization_result.normalized_count}"
        )

    print(f"phase=align window={window} progress=0% aligned=0")
    aligned = align_primary_power(
        db_path=args.db,
        max_gap_seconds=args.max_gap_seconds,
        include_rows=False,
        progress=lambda done, total: _print_alignment_progress(window, done, total),
    ).aligned_count
    return IngestCommandResult(
        fetched=fetched,
        cached=cached,
        normalized=normalized,
        aligned=aligned,
    )


def _print_alignment_progress(window: str, done: int, total: int) -> None:
    percent = 100 if total == 0 else min(100, done * 100 // total)
    print(f"phase=align window={window} progress={percent}% aligned={done}/{total}")


def run_simulation(args: argparse.Namespace) -> SimulationCommandResult:
    start_utc = _parse_local_boundary(args.start, args.timezone)
    end_utc = _parse_local_boundary(args.end, args.timezone)
    ingestion_db_path = resolve_cache_path(args.ingestion_db)
    simulation_db_path = resolve_simulation_db_path(args.simulation_db)

    try:
        row_count = _count_aligned_power_rows(ingestion_db_path, start_utc, end_utc)
        if row_count < 2:
            raise ValueError(
                "not enough aligned power samples found for simulation window; "
                "run `uv run hass-reader ingest` first"
            )
        window = f"{format_influx_time(start_utc)}..{format_influx_time(end_utc)}"
        print(f"phase=simulate window={window} progress=0% samples=0/{row_count}")
        result = simulate_scenarios(
            _iter_aligned_power_rows(
                ingestion_db_path,
                start_utc,
                end_utc,
                total=row_count,
                progress=lambda done, total: _print_simulation_progress(window, done, total),
            ),
            battery_capacities_kwh=args.battery_capacities_kwh,
            efficiency=args.efficiency,
        )
        run_id = persist_simulation_result(
            db_path=simulation_db_path,
            ingestion_db_path=ingestion_db_path,
            query_start_utc=start_utc,
            query_end_utc=end_utc,
            result=result,
            persist_steps=args.store_steps,
        )
    except Exception as error:
        try:
            persist_failed_simulation_run(
                db_path=simulation_db_path,
                ingestion_db_path=ingestion_db_path,
                query_start_utc=start_utc,
                query_end_utc=end_utc,
                error_message=str(error),
            )
        except Exception:
            pass
        raise

    return SimulationCommandResult(run_id=run_id, scenario_count=len(result.scenarios))


def _count_aligned_power_rows(
    db_path: Path,
    start_utc: datetime,
    end_utc: datetime,
) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"ingestion cache not found: {db_path}")
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT count(*)
            FROM aligned_power_sample
            WHERE timestamp_utc >= ? AND timestamp_utc < ?
            """,
            (_format_utc_timestamp(start_utc), _format_utc_timestamp(end_utc)),
        ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def _iter_aligned_power_rows(
    db_path: Path,
    start_utc: datetime,
    end_utc: datetime,
    *,
    total: int,
    progress: Callable[[int, int], None] | None = None,
) -> Iterator[AlignedPowerRow]:
    with sqlite3.connect(db_path) as connection:
        progress_step = max(1, total // 100)
        cursor = connection.execute(
            """
            SELECT
                timestamp_utc,
                grid_import_kw,
                grid_export_kw,
                solar_production_kw,
                gap_flags
            FROM aligned_power_sample
            WHERE timestamp_utc >= ? AND timestamp_utc < ?
            ORDER BY timestamp_utc
            """,
            (_format_utc_timestamp(start_utc), _format_utc_timestamp(end_utc)),
        )
        for done, row in enumerate(cursor, start=1):
            yield AlignedPowerRow(
                timestamp_utc=_parse_utc_timestamp(str(row[0])),
                grid_import_kw=float(row[1]),
                grid_export_kw=float(row[2]),
                solar_production_kw=None if row[3] is None else float(row[3]),
                gap_flags=str(row[4]),
            )
            if progress is not None and (done % progress_step == 0 or done == total):
                progress(done, total)


def _print_simulation_progress(window: str, done: int, total: int) -> None:
    if total == 0 or done >= total:
        percent = 100
    else:
        percent = max(1, done * 100 // total)
    print(f"phase=simulate window={window} progress={percent}% samples={done}/{total}")


def _build_parser() -> argparse.ArgumentParser:
    default_range = default_query_range()
    parser = argparse.ArgumentParser(prog="hass-reader")
    subparsers = parser.add_subparsers(dest="command")
    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--start", default=_format_local_date(default_range.start_utc))
    ingest.add_argument("--end", default=_format_local_date(default_range.end_utc))
    ingest.add_argument("--timezone", default=DEFAULT_LOCAL_TIMEZONE)
    ingest.add_argument("--db", type=Path, default=None)
    ingest.add_argument("--influx-url", default=DEFAULT_INFLUX_URL)
    ingest.add_argument("--influx-database", default=DEFAULT_INFLUX_DATABASE)
    ingest.add_argument("--max-gap-seconds", type=int, default=DEFAULT_MAX_GAP_SECONDS)
    ingest.add_argument("--force-refresh", action="store_true")
    simulate = subparsers.add_parser("simulate")
    simulate.add_argument("--batteries", required=True)
    simulate.add_argument("--efficiency", type=float, default=0.9)
    simulate.add_argument("--ingestion-db", type=Path, default=None)
    simulate.add_argument("--simulation-db", type=Path, default=DEFAULT_SIMULATION_DB_PATH)
    simulate.add_argument("--start", default=_format_local_date(default_range.start_utc))
    simulate.add_argument("--end", default=_format_local_date(default_range.end_utc))
    simulate.add_argument("--timezone", default=DEFAULT_LOCAL_TIMEZONE)
    simulate.add_argument("--store-steps", action="store_true")
    report = subparsers.add_parser("report")
    report.add_argument("--simulation-db", type=Path, default=DEFAULT_SIMULATION_DB_PATH)
    report.add_argument("--run-id", type=int, default=None)
    report.add_argument("--output", type=Path, default=None)
    report.add_argument("--overwrite", action="store_true")
    soc = subparsers.add_parser("soc")
    soc.add_argument("--simulation-db", type=Path, default=DEFAULT_SIMULATION_DB_PATH)
    soc.add_argument("--run-id", type=int, default=None)
    soc.add_argument("--batteries", default="")
    soc.add_argument("--start", default=_format_local_date(default_range.start_utc))
    soc.add_argument("--end", default=_format_local_date(default_range.end_utc))
    soc.add_argument("--timezone", default=DEFAULT_LOCAL_TIMEZONE)
    soc.add_argument("--bucket", default="day")
    return parser


def _format_local_date(value: datetime) -> str:
    return value.astimezone(ZoneInfo(DEFAULT_LOCAL_TIMEZONE)).date().isoformat()


def _parse_local_boundary(value: str, timezone_name: str) -> datetime:
    timezone = ZoneInfo(timezone_name)
    if "T" in value:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
        return parsed.astimezone(UTC)
    parsed_date = date.fromisoformat(value)
    return datetime(
        parsed_date.year,
        parsed_date.month,
        parsed_date.day,
        tzinfo=timezone,
    ).astimezone(UTC)


def _parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_simulation_args(args: argparse.Namespace) -> str | None:
    capacities = _parse_battery_capacities(args.batteries)
    if not capacities:
        return "invalid --batteries: provide one or more positive decimal capacities"
    if not 0 < args.efficiency <= 1:
        return "invalid --efficiency: expected a decimal where 0 < efficiency <= 1"
    args.battery_capacities_kwh = capacities
    return None


def _validate_report_args(args: argparse.Namespace) -> str | None:
    if args.run_id is not None and args.run_id <= 0:
        return "invalid --run-id: expected a positive integer"
    return None


def _validate_soc_args(args: argparse.Namespace) -> str | None:
    if args.run_id is not None and args.run_id <= 0:
        return "invalid --run-id: expected a positive integer"
    if args.bucket not in {"hour", "day"}:
        return "invalid --bucket: expected one of hour, day"
    selectors = _parse_soc_battery_selectors(args.batteries)
    if args.batteries and not selectors:
        return "invalid --batteries: provide comma-separated scenario names or positive capacities"
    try:
        start_utc = _parse_local_boundary(args.start, args.timezone)
        end_utc = _parse_local_boundary(args.end, args.timezone)
    except ValueError as error:
        return f"invalid time range: {error}"
    if start_utc >= end_utc:
        return "invalid time range: expected --start to be before --end"
    args.battery_selectors = selectors
    args.start_utc = start_utc
    args.end_utc = end_utc
    return None


def _parse_soc_battery_selectors(value: str) -> list[str]:
    if not value:
        return []
    selectors = []
    for part in value.split(","):
        selector = part.strip()
        if not selector:
            return []
        try:
            capacity = float(selector.removesuffix("kWh"))
        except ValueError:
            selectors.append(selector)
            continue
        if capacity <= 0:
            return []
        selectors.append(f"{capacity:g}kWh")
    return selectors


def _parse_battery_capacities(value: str) -> list[float]:
    capacities: list[float] = []
    for part in value.split(","):
        stripped = part.strip()
        if not stripped:
            return []
        try:
            capacity = float(stripped)
        except ValueError:
            return []
        if capacity <= 0:
            return []
        capacities.append(capacity)
    return capacities


def _exit_code(value: object) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        return 0
    return 1
