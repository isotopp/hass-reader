from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

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
class NormalizationResult:
    role: str
    normalized_count: int
    unit: str
    values: list[float]


@dataclass(frozen=True)
class AlignmentResult:
    aligned_count: int
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class MetricSourceRow:
    id: int
    role: str
    measurement: str
    entity_id: str
    normalize_to_unit: str


class UnitNormalizationError(ValueError):
    pass


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
        SELECT id, role, measurement, entity_id, normalize_to_unit
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
        normalize_to_unit=str(row[4]),
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


def normalize_metric(*, db_path: str | Path | None = None, role: str) -> NormalizationResult:
    resolved_path = initialize_cache(resolve_cache_path(db_path))
    with sqlite3.connect(resolved_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        metric_source = _load_metric_source(connection, role)
        rows = connection.execute(
            """
            SELECT timestamp_utc, value, unit, ingestion_run_id
            FROM raw_sample
            WHERE metric_source_id = ?
            ORDER BY timestamp_utc
            """,
            (metric_source.id,),
        ).fetchall()

        normalized_rows: list[tuple[int, str, float, str, int]] = []
        values: list[float] = []
        for row in rows:
            timestamp = str(row[0])
            raw_value = float(row[1])
            raw_unit = str(row[2])
            ingestion_run_id = int(row[3])
            normalized_value = _normalize_value(
                raw_value,
                raw_unit=raw_unit,
                target_unit=metric_source.normalize_to_unit,
                metric_source=metric_source,
                timestamp=timestamp,
            )
            normalized_rows.append(
                (
                    metric_source.id,
                    timestamp,
                    normalized_value,
                    metric_source.normalize_to_unit,
                    ingestion_run_id,
                )
            )
            values.append(normalized_value)

        connection.executemany(
            """
            INSERT INTO normalized_sample (
                metric_source_id,
                timestamp_utc,
                value,
                unit,
                ingestion_run_id
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(metric_source_id, timestamp_utc) DO UPDATE SET
                value = excluded.value,
                unit = excluded.unit,
                ingestion_run_id = excluded.ingestion_run_id
            """,
            normalized_rows,
        )
        return NormalizationResult(
            role=role,
            normalized_count=len(normalized_rows),
            unit=metric_source.normalize_to_unit,
            values=values,
        )


def align_primary_power(
    *,
    db_path: str | Path | None = None,
    max_gap_seconds: int,
) -> AlignmentResult:
    resolved_path = initialize_cache(resolve_cache_path(db_path))
    with sqlite3.connect(resolved_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        import_source = _load_metric_source(connection, "grid_import_power")
        export_source = _load_metric_source(connection, "grid_export_power")
        solar_source = _load_metric_source(connection, "solar_production_power")

        import_samples = _load_normalized_samples(connection, import_source.id)
        export_samples = _load_normalized_samples(connection, export_source.id)
        solar_samples = _load_normalized_samples(connection, solar_source.id)
        timeline = sorted(
            {
                sample.timestamp_utc
                for samples in (import_samples, export_samples, solar_samples)
                for sample in samples
            }
        )
        if not timeline:
            return AlignmentResult(aligned_count=0, rows=[])

        ingestion_run_id = max(
            sample.ingestion_run_id
            for samples in (import_samples, export_samples, solar_samples)
            for sample in samples
        )
        aligned_rows = [
            _align_timestamp(
                timestamp,
                import_samples=import_samples,
                export_samples=export_samples,
                solar_samples=solar_samples,
                max_gap_seconds=max_gap_seconds,
            )
            for timestamp in timeline
        ]
        connection.executemany(
            """
            INSERT INTO aligned_power_sample (
                timestamp_utc,
                grid_import_kw,
                grid_export_kw,
                solar_production_kw,
                import_source_sample_utc,
                export_source_sample_utc,
                solar_source_sample_utc,
                max_source_age_seconds,
                gap_flags,
                ingestion_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(timestamp_utc) DO UPDATE SET
                grid_import_kw = excluded.grid_import_kw,
                grid_export_kw = excluded.grid_export_kw,
                solar_production_kw = excluded.solar_production_kw,
                import_source_sample_utc = excluded.import_source_sample_utc,
                export_source_sample_utc = excluded.export_source_sample_utc,
                solar_source_sample_utc = excluded.solar_source_sample_utc,
                max_source_age_seconds = excluded.max_source_age_seconds,
                gap_flags = excluded.gap_flags,
                ingestion_run_id = excluded.ingestion_run_id
            """,
            [
                (
                    row["timestamp_utc"],
                    row["grid_import_kw"],
                    row["grid_export_kw"],
                    row["solar_production_kw"],
                    row["import_source_sample_utc"],
                    row["export_source_sample_utc"],
                    row["solar_source_sample_utc"],
                    row["max_source_age_seconds"],
                    row["gap_flags"],
                    ingestion_run_id,
                )
                for row in aligned_rows
            ],
        )
        public_rows = [
            {
                "timestamp_utc": row["timestamp_utc"],
                "grid_import_kw": row["grid_import_kw"],
                "grid_export_kw": row["grid_export_kw"],
                "solar_production_kw": row["solar_production_kw"],
                "max_source_age_seconds": row["max_source_age_seconds"],
                "gap_flags": row["gap_flags"],
            }
            for row in aligned_rows
        ]
        return AlignmentResult(aligned_count=len(public_rows), rows=public_rows)


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


def _normalize_value(
    value: float,
    *,
    raw_unit: str,
    target_unit: str,
    metric_source: MetricSourceRow,
    timestamp: str,
) -> float:
    if raw_unit == target_unit:
        return value
    if raw_unit == "W" and target_unit == "kW":
        return value / 1000
    raise UnitNormalizationError(
        "Cannot normalize unit "
        f"{raw_unit!r} to {target_unit!r} for "
        f"{metric_source.measurement}/{metric_source.entity_id} at {timestamp}"
    )


@dataclass(frozen=True)
class NormalizedSampleRow:
    timestamp_utc: datetime
    value: float
    ingestion_run_id: int


def _load_normalized_samples(
    connection: sqlite3.Connection,
    metric_source_id: int,
) -> list[NormalizedSampleRow]:
    rows = connection.execute(
        """
        SELECT timestamp_utc, value, ingestion_run_id
        FROM normalized_sample
        WHERE metric_source_id = ?
        ORDER BY timestamp_utc
        """,
        (metric_source_id,),
    ).fetchall()
    return [
        NormalizedSampleRow(
            timestamp_utc=_parse_optional_time(row[0]) or datetime.min.replace(tzinfo=UTC),
            value=float(row[1]),
            ingestion_run_id=int(row[2]),
        )
        for row in rows
    ]


def _align_timestamp(
    timestamp: datetime,
    *,
    import_samples: list[NormalizedSampleRow],
    export_samples: list[NormalizedSampleRow],
    solar_samples: list[NormalizedSampleRow],
    max_gap_seconds: int,
) -> dict[str, Any]:
    import_value = _latest_value_at(import_samples, timestamp)
    export_value = _latest_value_at(export_samples, timestamp)
    solar_value = _latest_value_at(solar_samples, timestamp)
    flags: list[str] = []

    grid_import_kw, import_age, import_timestamp = _required_power_value(
        import_value,
        timestamp=timestamp,
        max_gap_seconds=max_gap_seconds,
        flag="import",
        flags=flags,
    )
    grid_export_kw, export_age, export_timestamp = _required_power_value(
        export_value,
        timestamp=timestamp,
        max_gap_seconds=max_gap_seconds,
        flag="export",
        flags=flags,
    )
    solar_production_kw, solar_age, solar_timestamp = _optional_power_value(
        solar_value,
        timestamp=timestamp,
        max_gap_seconds=max_gap_seconds,
        flag="solar",
        flags=flags,
    )
    ages = [age for age in (import_age, export_age, solar_age) if age is not None]
    return {
        "timestamp_utc": format_influx_time(timestamp),
        "grid_import_kw": max(grid_import_kw, 0.0),
        "grid_export_kw": max(grid_export_kw, 0.0),
        "solar_production_kw": None
        if solar_production_kw is None
        else max(solar_production_kw, 0.0),
        "import_source_sample_utc": _format_optional_time(import_timestamp),
        "export_source_sample_utc": _format_optional_time(export_timestamp),
        "solar_source_sample_utc": _format_optional_time(solar_timestamp),
        "max_source_age_seconds": max(ages, default=0),
        "gap_flags": ",".join(flags),
    }


def _latest_value_at(
    samples: list[NormalizedSampleRow],
    timestamp: datetime,
) -> NormalizedSampleRow | None:
    latest: NormalizedSampleRow | None = None
    for sample in samples:
        if sample.timestamp_utc > timestamp:
            break
        latest = sample
    return latest


def _required_power_value(
    sample: NormalizedSampleRow | None,
    *,
    timestamp: datetime,
    max_gap_seconds: int,
    flag: str,
    flags: list[str],
) -> tuple[float, int | None, datetime | None]:
    if sample is None:
        flags.append(f"{flag}_missing")
        return 0.0, None, None
    age_seconds = int((timestamp - sample.timestamp_utc).total_seconds())
    if age_seconds > max_gap_seconds:
        flags.append(f"{flag}_gap")
        return 0.0, age_seconds, sample.timestamp_utc
    return sample.value, age_seconds, sample.timestamp_utc


def _optional_power_value(
    sample: NormalizedSampleRow | None,
    *,
    timestamp: datetime,
    max_gap_seconds: int,
    flag: str,
    flags: list[str],
) -> tuple[float | None, int | None, datetime | None]:
    if sample is None:
        flags.append(f"{flag}_missing")
        return None, None, None
    age_seconds = int((timestamp - sample.timestamp_utc).total_seconds())
    if age_seconds > max_gap_seconds:
        flags.append(f"{flag}_gap")
        return None, age_seconds, sample.timestamp_utc
    return sample.value, age_seconds, sample.timestamp_utc
