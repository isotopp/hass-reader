from datetime import UTC, datetime

import pytest

from hass_reader.cache import initialize_cache
from hass_reader.influx import InfluxSample
from hass_reader.ingestion import (
    align_primary_power,
    RawMetricIngestor,
    UnitNormalizationError,
    ingest_raw_metric,
    normalize_metric,
)


class RecordingIngestor(RawMetricIngestor):
    def __init__(self, samples: list[InfluxSample] | None = None) -> None:
        self.samples = samples or []
        self.calls = 0

    def query_samples(self, measurement, entity_id, start_utc, end_utc):
        self.calls += 1
        return self.samples


class FailingThenPassingIngestor(RawMetricIngestor):
    def __init__(self) -> None:
        self.calls = 0

    def query_samples(self, measurement, entity_id, start_utc, end_utc):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary network failure")
        return [
            InfluxSample(
                timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
                value=1.0,
                unit="kW",
                friendly_name="Power Consumed",
                source="hass",
                state_class="measurement",
            )
        ]


def test_ingest_raw_metric_fetches_and_caches_samples(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    initialize_cache(db_path)
    ingestor = RecordingIngestor(
        [
            InfluxSample(
                timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
                value=1.25,
                unit="kW",
                friendly_name="Power Consumed",
                source="hass",
                state_class="measurement",
            )
        ]
    )

    result = ingest_raw_metric(
        db_path=db_path,
        role="grid_import_power",
        ingestor=ingestor,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
    )

    assert result.status == "completed"
    assert result.fetched is True
    assert result.sample_count == 1
    assert ingestor.calls == 1


def test_ingest_raw_metric_uses_completed_fetch_window_cache(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    initialize_cache(db_path)
    ingestor = RecordingIngestor(
        [
            InfluxSample(
                timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
                value=1.25,
                unit="kW",
                friendly_name="Power Consumed",
                source="hass",
                state_class="measurement",
            )
        ]
    )

    first = ingest_raw_metric(
        db_path=db_path,
        role="grid_import_power",
        ingestor=ingestor,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
    )
    second = ingest_raw_metric(
        db_path=db_path,
        role="grid_import_power",
        ingestor=ingestor,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
    )

    assert first.fetched is True
    assert second.fetched is False
    assert second.sample_count == 1
    assert ingestor.calls == 1


def test_failed_fetch_window_does_not_block_retry(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    initialize_cache(db_path)
    ingestor = FailingThenPassingIngestor()

    with pytest.raises(RuntimeError, match="temporary network failure"):
        ingest_raw_metric(
            db_path=db_path,
            role="grid_import_power",
            ingestor=ingestor,
            start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
            end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        )

    retry = ingest_raw_metric(
        db_path=db_path,
        role="grid_import_power",
        ingestor=ingestor,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
    )

    assert retry.status == "completed"
    assert retry.fetched is True
    assert retry.sample_count == 1
    assert ingestor.calls == 2


def test_normalize_metric_converts_watts_to_kw(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    initialize_cache(db_path)
    ingestor = RecordingIngestor(
        [
            InfluxSample(
                timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
                value=1250.0,
                unit="W",
                friendly_name="Solar",
                source="hass",
                state_class="measurement",
            )
        ]
    )
    ingest_raw_metric(
        db_path=db_path,
        role="solar_production_power",
        ingestor=ingestor,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
    )

    result = normalize_metric(db_path=db_path, role="solar_production_power")

    assert result.normalized_count == 1
    assert result.unit == "kW"
    assert result.values == [1.25]


def test_normalize_metric_passes_kwh_through(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    initialize_cache(db_path)
    ingestor = RecordingIngestor(
        [
            InfluxSample(
                timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
                value=2.5,
                unit="kWh",
                friendly_name="Energy Consumed Today",
                source="hass",
                state_class="total_increasing",
            )
        ]
    )
    ingest_raw_metric(
        db_path=db_path,
        role="p1_grid_import_today",
        ingestor=ingestor,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
    )

    result = normalize_metric(db_path=db_path, role="p1_grid_import_today")

    assert result.normalized_count == 1
    assert result.unit == "kWh"
    assert result.values == [2.5]


def test_normalize_metric_rejects_unknown_units_with_context(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    initialize_cache(db_path)
    ingestor = RecordingIngestor(
        [
            InfluxSample(
                timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
                value=42.0,
                unit="banana",
                friendly_name="Power Consumed",
                source="hass",
                state_class="measurement",
            )
        ]
    )
    ingest_raw_metric(
        db_path=db_path,
        role="grid_import_power",
        ingestor=ingestor,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
    )

    with pytest.raises(UnitNormalizationError) as error:
        normalize_metric(db_path=db_path, role="grid_import_power")

    message = str(error.value)
    assert "sensor__power" in message
    assert "power_consumed" in message
    assert "banana" in message
    assert "2025-07-01T00:00:00Z" in message


def test_align_primary_power_uses_exact_matching_samples(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    timestamp = datetime(2025, 7, 1, 0, 0, tzinfo=UTC)
    _ingest_and_normalize(db_path, "grid_import_power", [(timestamp, 1.0, "kW")])
    _ingest_and_normalize(db_path, "grid_export_power", [(timestamp, 0.25, "kW")])
    _ingest_and_normalize(db_path, "solar_production_power", [(timestamp, 1500.0, "W")])

    result = align_primary_power(db_path=db_path, max_gap_seconds=300)

    assert result.aligned_count == 1
    assert result.rows == [
        {
            "timestamp_utc": "2025-07-01T00:00:00Z",
            "grid_import_kw": 1.0,
            "grid_export_kw": 0.25,
            "solar_production_kw": 1.5,
            "max_source_age_seconds": 0,
            "gap_flags": "",
        }
    ]


def test_align_primary_power_fill_forwards_within_gap(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    first = datetime(2025, 7, 1, 0, 0, tzinfo=UTC)
    second = datetime(2025, 7, 1, 0, 1, tzinfo=UTC)
    _ingest_and_normalize(db_path, "grid_import_power", [(first, 1.0, "kW")])
    _ingest_and_normalize(db_path, "grid_export_power", [(first, 0.25, "kW"), (second, 0.5, "kW")])
    _ingest_and_normalize(db_path, "solar_production_power", [(first, 1000.0, "W")])

    result = align_primary_power(db_path=db_path, max_gap_seconds=300)

    assert result.aligned_count == 2
    assert result.rows[1]["timestamp_utc"] == "2025-07-01T00:01:00Z"
    assert result.rows[1]["grid_import_kw"] == 1.0
    assert result.rows[1]["grid_export_kw"] == 0.5
    assert result.rows[1]["solar_production_kw"] == 1.0
    assert result.rows[1]["max_source_age_seconds"] == 60
    assert result.rows[1]["gap_flags"] == ""


def test_align_primary_power_flags_values_beyond_gap(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    first = datetime(2025, 7, 1, 0, 0, tzinfo=UTC)
    second = datetime(2025, 7, 1, 0, 10, tzinfo=UTC)
    _ingest_and_normalize(db_path, "grid_import_power", [(first, 1.0, "kW")])
    _ingest_and_normalize(db_path, "grid_export_power", [(second, 0.5, "kW")])
    _ingest_and_normalize(db_path, "solar_production_power", [(first, 1000.0, "W")])

    result = align_primary_power(db_path=db_path, max_gap_seconds=300)

    assert result.rows[1]["timestamp_utc"] == "2025-07-01T00:10:00Z"
    assert result.rows[1]["grid_import_kw"] == 0.0
    assert result.rows[1]["grid_export_kw"] == 0.5
    assert result.rows[1]["solar_production_kw"] is None
    assert result.rows[1]["max_source_age_seconds"] == 600
    assert result.rows[1]["gap_flags"] == "import_gap,solar_gap"


def _ingest_and_normalize(
    db_path,
    role: str,
    samples: list[tuple[datetime, float, str]],
) -> None:
    initialize_cache(db_path)
    ingestor = RecordingIngestor(
        [
            InfluxSample(
                timestamp_utc=timestamp,
                value=value,
                unit=unit,
                friendly_name=role,
                source="hass",
                state_class="measurement",
            )
            for timestamp, value, unit in samples
        ]
    )
    ingest_raw_metric(
        db_path=db_path,
        role=role,
        ingestor=ingestor,
        start_utc=datetime(2025, 6, 30, 22, 0, tzinfo=UTC),
        end_utc=datetime(2026, 6, 30, 22, 0, tzinfo=UTC),
        force_refresh=True,
    )
    normalize_metric(db_path=db_path, role=role)
