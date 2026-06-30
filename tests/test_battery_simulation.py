import pytest

from datetime import UTC, datetime

from hass_reader.battery_simulation import (
    AlignedPowerRow,
    BaselineScenario,
    BatteryScenario,
    SimulationInputError,
    simulate_scenarios,
)


def test_baseline_records_import_and_export_without_storage() -> None:
    scenario = BaselineScenario()

    step = scenario.apply_step(grid_import_kwh=2.0, grid_export_kwh=1.5)

    assert step.import_kwh == 2.0
    assert step.export_kwh == 1.5
    assert step.charged_kwh == 0.0
    assert step.discharged_kwh == 0.0
    assert step.losses_kwh == 0.0
    assert step.soc_kwh == 0.0
    assert scenario.summary.imported_kwh == 2.0
    assert scenario.summary.exported_kwh == 1.5


def test_battery_charges_from_export_until_capacity() -> None:
    scenario = BatteryScenario(capacity_kwh=5.0, efficiency=0.9)

    step = scenario.apply_step(grid_import_kwh=0.0, grid_export_kwh=7.0)

    assert step.import_kwh == 0.0
    assert step.export_kwh == 2.0
    assert step.charged_kwh == 5.0
    assert step.soc_kwh == 5.0
    assert scenario.summary.charged_kwh == 5.0
    assert scenario.summary.exported_kwh == 2.0


def test_battery_discharge_applies_loss_on_discharge() -> None:
    scenario = BatteryScenario(capacity_kwh=5.0, efficiency=0.8)
    scenario.apply_step(grid_import_kwh=0.0, grid_export_kwh=5.0)

    step = scenario.apply_step(grid_import_kwh=2.0, grid_export_kwh=0.0)

    assert step.import_kwh == 0.0
    assert step.export_kwh == 0.0
    assert step.discharged_kwh == 2.5
    assert step.losses_kwh == 0.5
    assert step.soc_kwh == 2.5
    assert scenario.summary.discharged_kwh == 2.5
    assert scenario.summary.losses_kwh == 0.5


def test_battery_partial_discharge_when_soc_is_insufficient() -> None:
    scenario = BatteryScenario(capacity_kwh=5.0, efficiency=0.8)
    scenario.apply_step(grid_import_kwh=0.0, grid_export_kwh=1.0)

    step = scenario.apply_step(grid_import_kwh=2.0, grid_export_kwh=0.0)

    assert step.import_kwh == 1.2
    assert step.discharged_kwh == 1.0
    assert step.losses_kwh == pytest.approx(0.2)
    assert step.soc_kwh == 0.0
    assert scenario.summary.imported_kwh == 1.2


def test_battery_does_not_discharge_when_empty() -> None:
    scenario = BatteryScenario(capacity_kwh=5.0, efficiency=0.8)

    step = scenario.apply_step(grid_import_kwh=2.0, grid_export_kwh=0.0)

    assert step.import_kwh == 2.0
    assert step.discharged_kwh == 0.0
    assert step.losses_kwh == 0.0
    assert step.soc_kwh == 0.0


def test_simulate_scenarios_includes_baseline_and_all_batteries() -> None:
    rows = [
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
            grid_import_kw=0.0,
            grid_export_kw=2.0,
            solar_production_kw=2.0,
            gap_flags="",
        ),
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 1, 0, tzinfo=UTC),
            grid_import_kw=2.0,
            grid_export_kw=0.0,
            solar_production_kw=0.0,
            gap_flags="",
        ),
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 2, 0, tzinfo=UTC),
            grid_import_kw=0.0,
            grid_export_kw=0.0,
            solar_production_kw=0.0,
            gap_flags="",
        ),
    ]

    result = simulate_scenarios(rows, battery_capacities_kwh=[1.0, 5.0], efficiency=0.8)

    assert [scenario.name for scenario in result.scenarios] == ["baseline", "1kWh", "5kWh"]
    assert result.scenarios[0].summary.imported_kwh == 2.0
    assert result.scenarios[0].summary.exported_kwh == 2.0
    assert result.scenarios[1].summary.imported_kwh == 1.2
    assert result.scenarios[1].summary.exported_kwh == 1.0
    assert result.scenarios[2].summary.imported_kwh == pytest.approx(0.4)
    assert result.scenarios[2].summary.exported_kwh == 0.0


def test_simulate_scenarios_reads_source_rows_once() -> None:
    read_count = 0

    def rows():
        nonlocal read_count
        for hour in range(3):
            read_count += 1
            yield AlignedPowerRow(
                timestamp_utc=datetime(2025, 7, 1, hour, 0, tzinfo=UTC),
                grid_import_kw=1.0,
                grid_export_kw=0.0,
                solar_production_kw=0.0,
                gap_flags="",
            )

    result = simulate_scenarios(rows(), battery_capacities_kwh=[1.0, 2.0], efficiency=0.9)

    assert read_count == 3
    assert len(result.scenarios) == 3


def test_simulate_scenarios_uses_irregular_intervals() -> None:
    rows = [
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
            grid_import_kw=2.0,
            grid_export_kw=0.0,
            solar_production_kw=0.0,
            gap_flags="",
        ),
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 0, 30, tzinfo=UTC),
            grid_import_kw=4.0,
            grid_export_kw=0.0,
            solar_production_kw=0.0,
            gap_flags="",
        ),
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 2, 0, tzinfo=UTC),
            grid_import_kw=0.0,
            grid_export_kw=0.0,
            solar_production_kw=0.0,
            gap_flags="",
        ),
    ]

    result = simulate_scenarios(rows, battery_capacities_kwh=[], efficiency=0.9)

    assert result.scenarios[0].summary.imported_kwh == 7.0


def test_simulate_scenarios_rejects_duplicate_timestamps() -> None:
    timestamp = datetime(2025, 7, 1, 0, 0, tzinfo=UTC)
    rows = [
        AlignedPowerRow(timestamp, 1.0, 0.0, 0.0, ""),
        AlignedPowerRow(timestamp, 1.0, 0.0, 0.0, ""),
    ]

    with pytest.raises(SimulationInputError, match="non-increasing timestamps"):
        simulate_scenarios(rows, battery_capacities_kwh=[], efficiency=0.9)


def test_simulate_scenarios_skips_intervals_starting_from_gap_rows() -> None:
    rows = [
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
            grid_import_kw=10.0,
            grid_export_kw=0.0,
            solar_production_kw=0.0,
            gap_flags="import_gap",
        ),
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 1, 0, tzinfo=UTC),
            grid_import_kw=1.0,
            grid_export_kw=0.0,
            solar_production_kw=0.0,
            gap_flags="",
        ),
        AlignedPowerRow(
            timestamp_utc=datetime(2025, 7, 1, 2, 0, tzinfo=UTC),
            grid_import_kw=0.0,
            grid_export_kw=0.0,
            solar_production_kw=0.0,
            gap_flags="",
        ),
    ]

    result = simulate_scenarios(rows, battery_capacities_kwh=[], efficiency=0.9)

    assert result.scenarios[0].summary.imported_kwh == 1.0
    assert result.skipped_intervals == [
        {
            "start_utc": "2025-07-01T00:00:00Z",
            "end_utc": "2025-07-01T01:00:00Z",
            "reason": "import_gap",
        }
    ]
