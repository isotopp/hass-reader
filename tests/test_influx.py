from datetime import UTC, datetime

import pytest

from hass_reader.influx import (
    EmptyInfluxResultError,
    InfluxClient,
    InfluxMetric,
    InfluxQueryError,
    default_query_range,
)


def test_default_query_range_matches_documented_target_year() -> None:
    query_range = default_query_range()

    assert query_range.start_utc == datetime(2025, 6, 30, 22, 0, tzinfo=UTC)
    assert query_range.end_utc == datetime(2026, 6, 30, 22, 0, tzinfo=UTC)
    assert query_range.local_timezone == "Europe/Amsterdam"


def test_query_samples_builds_half_open_influxql() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def transport(url: str, params: dict[str, str]) -> bytes:
        calls.append((url, params))
        return (
            b'{"results":[{"statement_id":0,"series":[{"columns":'
            b'["time","value","unit_of_measurement_str","friendly_name","source",'
            b'"state_class_str"],"values":[["2025-07-01T00:00:00Z",1000,"W",'
            b'"Solar","hass","measurement"]]}]}]}'
        )

    client = InfluxClient("http://influx.example:8086", "hass", transport=transport)
    metric = InfluxMetric(
        measurement="sensor__power",
        entity_id="zonneplan_one_optimized_omvormer_last_measured_value",
    )

    samples = client.query_samples(
        metric,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
    )

    assert calls == [
        (
            "http://influx.example:8086/query",
            {
                "db": "hass",
                "q": (
                    'SELECT "value", "unit_of_measurement_str", "friendly_name", "source", '
                    '"state_class_str" FROM "sensor__power" WHERE "entity_id" = '
                    "'zonneplan_one_optimized_omvormer_last_measured_value' AND time >= "
                    "'2025-06-30T22:00:00Z' AND time < '2026-06-30T22:00:00Z' "
                    "ORDER BY time ASC"
                ),
            },
        )
    ]
    assert len(samples) == 1
    assert samples[0].timestamp_utc == datetime(2025, 7, 1, 0, 0, tzinfo=UTC)
    assert samples[0].value == 1000
    assert samples[0].unit == "W"
    assert samples[0].friendly_name == "Solar"
    assert samples[0].source == "hass"
    assert samples[0].state_class == "measurement"


def test_query_samples_reports_empty_results_with_metric_context() -> None:
    def transport(_url: str, _params: dict[str, str]) -> bytes:
        return b'{"results":[{"statement_id":0}]}'

    client = InfluxClient("http://influx.example:8086", "hass", transport=transport)

    with pytest.raises(EmptyInfluxResultError) as error:
        client.query_samples(
            InfluxMetric(measurement="sensor__power", entity_id="power_consumed"),
            start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
            end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        )

    assert "sensor__power" in str(error.value)
    assert "power_consumed" in str(error.value)
    assert "2025-06-30T22:00:00Z" in str(error.value)


def test_query_samples_reports_influx_errors() -> None:
    def transport(_url: str, _params: dict[str, str]) -> bytes:
        return b'{"results":[{"statement_id":0,"error":"database not found"}]}'

    client = InfluxClient("http://influx.example:8086", "hass", transport=transport)

    with pytest.raises(InfluxQueryError, match="database not found"):
        client.query_samples(
            InfluxMetric(measurement="sensor__power", entity_id="power_consumed"),
            start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
            end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        )
