from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo


DEFAULT_INFLUX_URL = "http://192.168.1.10:8086"
DEFAULT_INFLUX_DATABASE = "hass"
DEFAULT_LOCAL_TIMEZONE = "Europe/Amsterdam"

Transport = Callable[[str, dict[str, str]], bytes]


class InfluxError(Exception):
    """Base error for Influx query failures."""


class InfluxQueryError(InfluxError):
    """Raised when InfluxDB returns an error payload or malformed response."""


class EmptyInfluxResultError(InfluxError):
    """Raised when a query succeeds but returns no samples."""


@dataclass(frozen=True)
class QueryRange:
    start_utc: datetime
    end_utc: datetime
    local_timezone: str


@dataclass(frozen=True)
class InfluxMetric:
    measurement: str
    entity_id: str


@dataclass(frozen=True)
class InfluxSample:
    timestamp_utc: datetime
    value: float
    unit: str
    friendly_name: str | None
    source: str | None
    state_class: str | None


class InfluxClient:
    def __init__(
        self,
        url: str = DEFAULT_INFLUX_URL,
        database: str = DEFAULT_INFLUX_DATABASE,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.database = database
        self._transport = transport or _urllib_transport

    def query_samples(
        self,
        metric: InfluxMetric,
        *,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[InfluxSample]:
        start = _as_utc(start_utc)
        end = _as_utc(end_utc)
        query = build_sample_query(metric, start_utc=start, end_utc=end)
        payload = self._transport(f"{self.url}/query", {"db": self.database, "q": query})
        samples = _parse_samples(payload)
        if not samples:
            raise EmptyInfluxResultError(
                "No samples returned for "
                f"{metric.measurement}/{metric.entity_id} between "
                f"{format_influx_time(start)} and {format_influx_time(end)}"
            )
        return samples


def default_query_range(local_timezone: str = DEFAULT_LOCAL_TIMEZONE) -> QueryRange:
    timezone = ZoneInfo(local_timezone)
    local_start = datetime(2025, 7, 1, 0, 0, tzinfo=timezone)
    local_end = datetime(2026, 7, 1, 0, 0, tzinfo=timezone)
    return QueryRange(
        start_utc=local_start.astimezone(UTC),
        end_utc=local_end.astimezone(UTC),
        local_timezone=local_timezone,
    )


def build_sample_query(metric: InfluxMetric, *, start_utc: datetime, end_utc: datetime) -> str:
    return (
        'SELECT "value", "unit_of_measurement_str", "friendly_name", "source", '
        f'"state_class_str" FROM "{_escape_identifier(metric.measurement)}" '
        f"WHERE \"entity_id\" = '{_escape_string(metric.entity_id)}' "
        f"AND time >= '{format_influx_time(start_utc)}' "
        f"AND time < '{format_influx_time(end_utc)}' "
        "ORDER BY time ASC"
    )


def format_influx_time(value: datetime) -> str:
    utc_value = _as_utc(value)
    return utc_value.isoformat().replace("+00:00", "Z")


def _urllib_transport(url: str, params: dict[str, str]) -> bytes:
    query_url = f"{url}?{urlencode(params)}"
    with urlopen(query_url, timeout=30) as response:
        return response.read()


def _parse_samples(payload: bytes) -> list[InfluxSample]:
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as error:
        raise InfluxQueryError(f"Invalid InfluxDB JSON response: {error}") from error

    results = _expect_list(document.get("results"), "results")
    samples: list[InfluxSample] = []
    for result in results:
        if not isinstance(result, dict):
            raise InfluxQueryError("Invalid InfluxDB result entry")
        if "error" in result:
            raise InfluxQueryError(str(result["error"]))
        for series in result.get("series", []):
            samples.extend(_parse_series(series))
    return samples


def _parse_series(series: Any) -> list[InfluxSample]:
    if not isinstance(series, dict):
        raise InfluxQueryError("Invalid InfluxDB series entry")
    columns = _expect_list(series.get("columns"), "columns")
    values = _expect_list(series.get("values"), "values")
    parsed: list[InfluxSample] = []
    for row in values:
        if not isinstance(row, list):
            raise InfluxQueryError("Invalid InfluxDB value row")
        row_by_column = dict(zip(columns, row, strict=True))
        parsed.append(
            InfluxSample(
                timestamp_utc=_parse_timestamp(str(row_by_column["time"])),
                value=_required_float(row_by_column["value"]),
                unit=str(row_by_column["unit_of_measurement_str"]),
                friendly_name=_optional_string(row_by_column.get("friendly_name")),
                source=_optional_string(row_by_column.get("source")),
                state_class=_optional_string(row_by_column.get("state_class_str")),
            )
        )
    return parsed


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _required_float(value: object) -> float:
    if isinstance(value, str | int | float):
        return float(value)
    raise InfluxQueryError(f"Invalid numeric sample value: {value!r}")


def _expect_list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise InfluxQueryError(f"Invalid InfluxDB response: {name} is missing or not a list")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Influx query timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _escape_identifier(value: str) -> str:
    return value.replace('"', '\\"')


def _escape_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
