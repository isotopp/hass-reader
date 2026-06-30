from datetime import UTC, datetime

import pytest

from hass_reader.cache import initialize_cache
from hass_reader.influx import InfluxSample
from hass_reader.ingestion import RawMetricIngestor, ingest_raw_metric


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
