# InfluxDB Access

This project reads Home Assistant history from an InfluxDB 1.8 database.

## Default Connection

| Setting        | Value                       |
|----------------|-----------------------------|
| URL            | `http://192.168.1.10:8086`  |
| Database       | `hass`                      |
| API            | InfluxDB 1.8 HTTP query API |
| Query endpoint | `/query`                    |

Use `curl -G -sS` for manual read-only discovery queries:

```sh
curl -G -sS 'http://192.168.1.10:8086/query' \
  --data-urlencode 'db=hass' \
  --data-urlencode 'q=SHOW MEASUREMENTS'
```

## Time Range Convention

The target analysis period is July 2025 through the end of June 2026 in Europe/Amsterdam local
time.

Use half-open intervals: `start <= time < end`.

| Boundary                | Value                       |
|-------------------------|-----------------------------|
| Local start             | `2025-07-01T00:00:00+02:00` |
| Local exclusive end     | `2026-07-01T00:00:00+02:00` |
| UTC query start         | `2025-06-30T22:00:00Z`      |
| UTC query exclusive end | `2026-06-30T22:00:00Z`      |

The UTC form is the form to use in InfluxQL queries.

## Discovery Queries

List measurements:

```sql
SHOW MEASUREMENTS
```

List tag keys for instantaneous power:

```sql
SHOW TAG KEYS FROM "sensor__power"
```

List field keys for instantaneous power:

```sql
SHOW FIELD KEYS FROM "sensor__power"
```

List power entities:

```sql
SHOW TAG VALUES FROM "sensor__power" WITH KEY = "entity_id"
```

List energy entities:

```sql
SHOW TAG VALUES FROM "sensor__energy" WITH KEY = "entity_id"
```

Check annual coverage for selected power entities:

```sql
SELECT count("value"), first("value"), last("value"), last("unit_of_measurement_str")
FROM "sensor__power"
WHERE "entity_id" =~ /^(power_consumed|power_produced|zonneplan_one_optimized_omvormer_last_measured_value|connect_energiemeter_electricity_average|connect_energiemeter_electricity_consumption|connect_energiemeter_electricity_production)$/
  AND time >= '2025-06-30T22:00:00Z'
  AND time < '2026-06-30T22:00:00Z'
GROUP BY "entity_id", "friendly_name", "source"
```

Check annual coverage for selected energy entities:

```sql
SELECT count("value"), first("value"), last("value"), last("unit_of_measurement_str")
FROM "sensor__energy"
WHERE "entity_id" =~ /^(energy_consumed_today|energy_produced_today|zonneplan_electricity_consumption_today|zonneplan_electricity_returned_today|zonneplan_yield_today|zonneplan_one_optimized_omvormer_yield_total)$/
  AND time >= '2025-06-30T22:00:00Z'
  AND time < '2026-06-30T22:00:00Z'
GROUP BY "entity_id", "friendly_name", "source"
```

## CLI Error Handling Requirement

Future CLI commands should report connection and query failures with:

- the InfluxDB URL,
- the database name,
- the operation being attempted,
- the underlying HTTP or InfluxDB error.

Normal command-line failures should not print a Python stack trace.
