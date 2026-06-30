from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Iterable


@dataclass
class ScenarioSummary:
    imported_kwh: float = 0.0
    exported_kwh: float = 0.0
    charged_kwh: float = 0.0
    discharged_kwh: float = 0.0
    losses_kwh: float = 0.0
    ending_soc_kwh: float = 0.0


@dataclass(frozen=True)
class ScenarioStep:
    import_kwh: float
    export_kwh: float
    charged_kwh: float
    discharged_kwh: float
    losses_kwh: float
    soc_kwh: float


@dataclass(frozen=True)
class AlignedPowerRow:
    timestamp_utc: datetime
    grid_import_kw: float
    grid_export_kw: float
    solar_production_kw: float | None
    gap_flags: str


@dataclass(frozen=True)
class SimulationResult:
    scenarios: list[BaselineScenario | BatteryScenario]


class BaselineScenario:
    name = "baseline"
    capacity_kwh = 0.0
    efficiency = 1.0
    is_baseline = True

    def __init__(self) -> None:
        self.summary = ScenarioSummary()

    def apply_step(self, *, grid_import_kwh: float, grid_export_kwh: float) -> ScenarioStep:
        step = ScenarioStep(
            import_kwh=grid_import_kwh,
            export_kwh=grid_export_kwh,
            charged_kwh=0.0,
            discharged_kwh=0.0,
            losses_kwh=0.0,
            soc_kwh=0.0,
        )
        _accumulate(self.summary, step)
        return step


class BatteryScenario:
    is_baseline = False

    def __init__(self, *, capacity_kwh: float, efficiency: float, name: str | None = None) -> None:
        if capacity_kwh <= 0:
            raise ValueError("battery capacity must be positive")
        if not 0 < efficiency <= 1:
            raise ValueError("battery efficiency must be greater than 0 and at most 1")
        self.capacity_kwh = capacity_kwh
        self.efficiency = efficiency
        self.name = name or f"{capacity_kwh:g}kWh"
        self.soc_kwh = 0.0
        self.summary = ScenarioSummary()

    def apply_step(self, *, grid_import_kwh: float, grid_export_kwh: float) -> ScenarioStep:
        charged_kwh = min(grid_export_kwh, self.capacity_kwh - self.soc_kwh)
        self.soc_kwh += charged_kwh
        remaining_export_kwh = grid_export_kwh - charged_kwh

        discharge_needed_kwh = grid_import_kwh / self.efficiency
        discharged_kwh = min(self.soc_kwh, discharge_needed_kwh)
        self.soc_kwh -= discharged_kwh
        offset_import_kwh = discharged_kwh * self.efficiency
        losses_kwh = discharged_kwh - offset_import_kwh
        remaining_import_kwh = grid_import_kwh - offset_import_kwh

        step = ScenarioStep(
            import_kwh=_clamp_zero(remaining_import_kwh),
            export_kwh=_clamp_zero(remaining_export_kwh),
            charged_kwh=charged_kwh,
            discharged_kwh=discharged_kwh,
            losses_kwh=losses_kwh,
            soc_kwh=self.soc_kwh,
        )
        _accumulate(self.summary, step)
        return step


def _accumulate(summary: ScenarioSummary, step: ScenarioStep) -> None:
    summary.imported_kwh += step.import_kwh
    summary.exported_kwh += step.export_kwh
    summary.charged_kwh += step.charged_kwh
    summary.discharged_kwh += step.discharged_kwh
    summary.losses_kwh += step.losses_kwh
    summary.ending_soc_kwh = step.soc_kwh


def _clamp_zero(value: float) -> float:
    if abs(value) < 1e-12:
        return 0.0
    return max(value, 0.0)


def simulate_scenarios(
    rows: Iterable[AlignedPowerRow],
    *,
    battery_capacities_kwh: list[float],
    efficiency: float,
) -> SimulationResult:
    scenarios: list[BaselineScenario | BatteryScenario] = [BaselineScenario()]
    scenarios.extend(
        BatteryScenario(capacity_kwh=capacity, efficiency=efficiency)
        for capacity in battery_capacities_kwh
    )

    previous: AlignedPowerRow | None = None
    for current in rows:
        if previous is not None:
            duration_hours = (current.timestamp_utc - previous.timestamp_utc).total_seconds() / 3600
            grid_import_kwh = previous.grid_import_kw * duration_hours
            grid_export_kwh = previous.grid_export_kw * duration_hours
            for scenario in scenarios:
                scenario.apply_step(
                    grid_import_kwh=grid_import_kwh,
                    grid_export_kwh=grid_export_kwh,
                )
        previous = current

    return SimulationResult(scenarios=scenarios)
