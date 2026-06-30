# User Stories: Metric Ingestion

## Epic

As a simulator user, I want Home Assistant power data loaded into a normalized internal model so
that calculations are independent of Influx field names and units.

## Discovery Inputs

This epic depends on the Influx discovery contract:

- `docs/influx-access.md` for URL, database, query endpoint, and time range conventions.
- `docs/influx-metrics.md` for measurement names, entity IDs, units, and assured availability.

Metric ingestion should not rediscover the schema by default. It should use the documented primary
inputs unless the CLI provides explicit overrides.

## Stories

### Story 1: Query Time-Series Samples

As a user, I want to request a time range so that simulations can run for a day, week, month, or
custom period.

Acceptance criteria:

- The CLI accepts start and end timestamps.
- The default range is `2025-07-01` inclusive to `2026-07-01` exclusive in Europe/Amsterdam time.
- Queries return time-ordered samples.
- Empty result sets produce a clear error.

### Story 2: Normalize Units

As a developer, I want all power samples normalized to kW and all energy samples normalized to kWh
so that simulation math is consistent.

Acceptance criteria:

- `W` power values are divided by 1000.
- `kW` power values are passed through unchanged.
- Unknown units are rejected with entity context.

### Story 3: Merge Import and Export Series

As a simulator user, I want P1 import and export samples aligned on a common timeline so that each
timestep has a single view of grid flow.

Acceptance criteria:

- Import and export are represented as non-negative kW values.
- Missing values can be filled using the previous known value within a bounded gap.
- Gaps beyond the configured threshold are reported.

### Story 4: Preserve Source Metadata

As an operator, I want output to include the metric mapping that was used so that results can be
audited later.

Acceptance criteria:

- Reports include measurement names, entity IDs, units, and lookback range.
- Reports distinguish P1 Slimmelezer metrics from Zonneplan extension cross-check metrics.
- Reports include the sample counts used for each source series.
