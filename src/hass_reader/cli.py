from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hass_reader.cache import SEED_METRIC_SOURCES
from hass_reader.influx import (
    DEFAULT_INFLUX_DATABASE,
    DEFAULT_INFLUX_URL,
    DEFAULT_LOCAL_TIMEZONE,
    InfluxClient,
    InfluxMetric,
    InfluxSample,
    default_query_range,
)
from hass_reader.ingestion import align_primary_power, ingest_raw_metric, normalize_metric
from hass_reader.simulation_db import DEFAULT_SIMULATION_DB_PATH


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
        normalized += normalize_metric(db_path=args.db, role=source.role).normalized_count

    aligned = align_primary_power(
        db_path=args.db,
        max_gap_seconds=args.max_gap_seconds,
    ).aligned_count
    return IngestCommandResult(
        fetched=fetched,
        cached=cached,
        normalized=normalized,
        aligned=aligned,
    )


def run_simulation(_args: argparse.Namespace) -> SimulationCommandResult:
    raise NotImplementedError("simulation execution is not implemented yet")


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


def _validate_simulation_args(args: argparse.Namespace) -> str | None:
    capacities = _parse_battery_capacities(args.batteries)
    if not capacities:
        return "invalid --batteries: provide one or more positive decimal capacities"
    if not 0 < args.efficiency <= 1:
        return "invalid --efficiency: expected a decimal where 0 < efficiency <= 1"
    args.battery_capacities_kwh = capacities
    return None


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
