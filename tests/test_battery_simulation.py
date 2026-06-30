import pytest

from hass_reader.battery_simulation import BaselineScenario, BatteryScenario


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
