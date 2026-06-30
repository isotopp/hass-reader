from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from hass_reader.cache import initialize_cache, resolve_cache_path
from hass_reader.influx import InfluxSample, format_influx_time


DEFAULT_TIMEZONE = "Europe/Amsterdam"
DEFAULT_INFLUX_URL = "http://192.168.1.10:8086"
DEFAULT_INFLUX_DATABASE = "hass"


class RawMetricIngestor(Protocol):
    def query_samples(
        self,
        measurement: str,
        entity_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[InfluxSample]: ...


@dataclass(frozen=True)
class RawIngestionResult:
    role: str
    status: str
    fetched: bool
    sample_count: int
    first_sample_utc: datetime | None
    last_sample_utc: datetime | None


@dataclass(frozen=True)
class MetricSourceRow:
    id: int
    role: str
    measurement: str
    entity_id: str


def ingest_raw_metric(
    *,
    db_path: str | Path | None = None,
    role: str,
    ingestor: RawMetricIngestor,
    start_utc: datetime,
    end_utc: datetime,
    influx_url: str = DEFAULT_INFLUX_URL,
    influx_database: str = DEFAULT_INFLUX_DATABASE,
    local_timezone: str = DEFAULT_TIMEZONE,
    force_refresh: bool = False,
) -> RawIngestionResult:
    resolved_path = initialize_cache(resolve_cache_path(db_path))
    start_text = format_influx_time(start_utc)
    end_text = format_influx_time(end_utc)

    with sqlite3.connect(resolved_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        metric_source = _load_metric_source(connection, role)

        if not force_refresh:
            cached = _load_completed_fetch_window(
                connection,
                metric_source_id=metric_source.id,
                start_text=start_text,
                end_text=end_text,
                role=role,
            )
            if cached is not None:
                return cached

        run_id = _create_ingestion_run(
            connection,
            influx_url=influx_url,
            influx_database=influx_database,
            local_timezone=local_timezone,
            start_text=start_text,
            end_text=end_text,
        )

        try:
            samples = ingestor.query_samples(
                metric_source.measurement,
                metric_source.entity_id,
                _as_utc(start_utc),
                _as_utc(end_utc),
            )
        except Exception as error:
            _record_fetch_window(
                connection,
                metric_source_id=metric_source.id,
                ingestion_run_id=run_id,
                start_text=start_text,
                end_text=end_text,
                samples=[],
                status="failed",
                error_message=str(error),
            )
            _complete_ingestion_run(
                connection, run_id=run_id, status="failed", error_message=str(error)
            )
            raise

        _upsert_raw_samples(
            connection,
            metric_source_id=metric_source.id,
            ingestion_run_id=run_id,
            samples=samples,
        )
        result = _record_fetch_window(
            connection,
            metric_source_id=metric_source.id,
            ingestion_run_id=run_id,
            start_text=start_text,
            end_text=end_text,
            samples=samples,
            status="completed",
            error_message=None,
        )
        _complete_ingestion_run(connection, run_id=run_id, status="completed", error_message=None)
        return RawIngestionResult(
            role=role,
            status="completed",
            fetched=True,
            sample_count=result.sample_count,
            first_sample_utc=result.first_sample_utc,
            last_sample_utc=result.last_sample_utc,
        )


def _load_metric_source(connection: sqlite3.Connection, role: str) -> MetricSourceRow:
    row = connection.execute(
        """
        SELECT id, role, measurement, entity_id
        FROM metric_source
        WHERE role = ?
        """,
        (role,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown metric source role: {role}")
    return MetricSourceRow(
        id=int(row[0]),
        role=str(row[1]),
        measurement=str(row[2]),
        entity_id=str(row[3]),
    )


def _load_completed_fetch_window(
    connection: sqlite3.Connection,
    *,
    metric_source_id: int,
    start_text: str,
    end_text: str,
    role: str,
) -> RawIngestionResult | None:
    row = connection.execute(
        """
        SELECT sample_count, first_sample_utc, last_sample_utc
        FROM fetch_window
        WHERE metric_source_id = ?
          AND query_start_utc = ?
          AND query_end_utc = ?
          AND status = 'completed'
        """,
        (metric_source_id, start_text, end_text),
    ).fetchone()
    if row is None:
        return None
    return RawIngestionResult(
        role=role,
        status="completed",
        fetched=False,
        sample_count=int(row[0]),
        first_sample_utc=_parse_optional_time(row[1]),
        last_sample_utc=_parse_optional_time(row[2]),
    )


def _create_ingestion_run(
    connection: sqlite3.Connection,
    *,
    influx_url: str,
    influx_database: str,
    local_timezone: str,
    start_text: str,
    end_text: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO ingestion_run (
            started_at_utc,
            influx_url,
            influx_database,
            local_timezone,
            query_start_utc,
            query_end_utc,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, 'running')
        """,
        (
            datetime.now(UTC).isoformat(),
            influx_url,
            influx_database,
            local_timezone,
            start_text,
            end_text,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return an ingestion_run id")
    return cursor.lastrowid


def _complete_ingestion_run(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    status: str,
    error_message: str | None,
) -> None:
    connection.execute(
        """
        UPDATE ingestion_run
        SET completed_at_utc = ?, status = ?, error_message = ?
        WHERE id = ?
        """,
        (datetime.now(UTC).isoformat(), status, error_message, run_id),
    )


def _upsert_raw_samples(
    connection: sqlite3.Connection,
    *,
    metric_source_id: int,
    ingestion_run_id: int,
    samples: list[InfluxSample],
) -> None:
    connection.executemany(
        """
        INSERT INTO raw_sample (
            metric_source_id,
            timestamp_utc,
            value,
            unit,
            friendly_name,
            source,
            state_class,
            ingestion_run_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(metric_source_id, timestamp_utc) DO UPDATE SET
            value = excluded.value,
            unit = excluded.unit,
            friendly_name = excluded.friendly_name,
            source = excluded.source,
            state_class = excluded.state_class,
            ingestion_run_id = excluded.ingestion_run_id
        """,
        [
            (
                metric_source_id,
                format_influx_time(sample.timestamp_utc),
                sample.value,
                sample.unit,
                sample.friendly_name,
                sample.source,
                sample.state_class,
                ingestion_run_id,
            )
            for sample in samples
        ],
    )


def _record_fetch_window(
    connection: sqlite3.Connection,
    *,
    metric_source_id: int,
    ingestion_run_id: int,
    start_text: str,
    end_text: str,
    samples: list[InfluxSample],
    status: str,
    error_message: str | None,
) -> RawIngestionResult:
    first_sample = min((sample.timestamp_utc for sample in samples), default=None)
    last_sample = max((sample.timestamp_utc for sample in samples), default=None)
    connection.execute(
        """
        INSERT INTO fetch_window (
            metric_source_id,
            ingestion_run_id,
            query_start_utc,
            query_end_utc,
            sample_count,
            first_sample_utc,
            last_sample_utc,
            status,
            error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(metric_source_id, query_start_utc, query_end_utc) DO UPDATE SET
            ingestion_run_id = excluded.ingestion_run_id,
            sample_count = excluded.sample_count,
            first_sample_utc = excluded.first_sample_utc,
            last_sample_utc = excluded.last_sample_utc,
            status = excluded.status,
            error_message = excluded.error_message
        """,
        (
            metric_source_id,
            ingestion_run_id,
            start_text,
            end_text,
            len(samples),
            _format_optional_time(first_sample),
            _format_optional_time(last_sample),
            status,
            error_message,
        ),
    )
    return RawIngestionResult(
        role="",
        status=status,
        fetched=True,
        sample_count=len(samples),
        first_sample_utc=first_sample,
        last_sample_utc=last_sample,
    )


def _format_optional_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return format_influx_time(value)


def _parse_optional_time(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Ingestion timestamps must be timezone-aware")
    return value.astimezone(UTC)
