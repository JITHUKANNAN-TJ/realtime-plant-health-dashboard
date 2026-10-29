# HTTP API

The backend is a single standard-library Python process. The dashboard is served from
the same origin, so ordinary browser requests need no CORS headers.

Base URL: `http://<pc-ip>:8000` (default). JSON in, JSON out, except where noted.

If `server.api_key` is set in `config.json`, write endpoints require an
`X-Api-Key: <key>` header. Read endpoints stay public on the LAN.

## Device ingest

### `POST /api/readings`

Accepts one reading object or an array (the ESP32 sends its offline buffer as an array).

```json
{
  "device": "plant-01",
  "ts": 1793260000,
  "seq": 42,
  "temp_c": 24.6,
  "humidity_pct": 55.8,
  "light_mv": 729,
  "light_pct": 73.8,
  "soil_mv": 1737,
  "soil_pct": 61.6,
  "rssi": -55,
  "uptime_s": 3600,
  "gps": { "fix": false, "lat": null, "lon": null }
}
```

`ts` is Unix seconds. Values outside the last day are clamped to server time.
`light_pct` / `soil_pct` are optional; the firmware computes them from calibration.

Response:

```json
{
  "ok": true,
  "stored": 1,
  "buzzer": true,
  "critical": false,
  "alerts": [],
  "health": 96.4
}
```

### `GET /api/calibration`

Calibration the firmware polls at boot:

```json
{
  "light": { "dark_mv": 2500, "bright_mv": 150 },
  "soil": { "dry_mv": 2600, "wet_mv": 1200 }
}
```

## Live data

### `GET /api/latest[?device=plant-01]`

Latest reading enriched with `health`, `forecast`, `anomalies`, `active_alerts`,
`online` and `buzzer`.

### `GET /api/history?range=24h|7d|30d[&device=...]`

```json
{
  "range": "24h",
  "points": [
    { "ts": 1793260000, "temp": 24.6, "humidity": 55.8, "light": 73.8, "soil": 61.6, "quality": "OK" }
  ]
}
```

Ranges up to 7 days come from raw readings. `30d` uses hourly rollups; older ranges
would use daily rollups.

### `GET /stream` (Server-Sent Events)

Long-lived stream. Event names:

| Event | Payload |
|---|---|
| `reading` | same object as `/api/latest` |
| `alert` | `{ "alerts": [ ... ] }` for newly triggered alerts |
| `buzzer` | `{ "enabled": true|false }` |

A `: ping` comment is sent every 15 s to keep proxies awake.

## Alerts and control

| Method | Path | Body | Notes |
|---|---|---|---|
| `POST` | `/api/alerts/snooze` | `{ "minutes": 60 }` | Suppresses alert dispatch until the timestamp in the reply |
| `POST` | `/api/buzzer` | `{ "enabled": false }` | Persisted; the ESP32 picks the flag up from its next response |
| `POST` | `/api/telegram/test` | `{}` | Sends a test message if Telegram is configured |
| `POST` | `/api/email/test` | `{}` | Sends a test email if email is configured |

## Configuration and calibration

### `GET /api/config`

Public (secret-free) view of `config.json`: device, thresholds, alert timings, weather
location, integration enabled flags and calibration.

### `POST /api/config`

Partial update. Only these branches are writable: `device.name`, `device.outdoor`,
`device.post_interval_s`, `weather.enabled/latitude/longitude`, and whole
`thresholds` / `alerts` objects.

### `POST /api/calibrate`

```json
{
  "light": { "dark_mv": 2500, "bright_mv": 150 },
  "soil": { "dry_mv": 2600, "wet_mv": 1200 }
}
```

The legacy form `{ "sensor": "soil", "min_mv": 2600, "max_mv": 1200 }` is also accepted.

## Journal

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/events[?range=7d]` | - |
| `POST` | `/api/events` | `{ "type": "watering", "note": "0.5 L", "amount": 0.5 }` |

Event types used by the UI: `watering`, `fertilize`, `repot`, `prune`, `move`, `note`.

## Reports and exports

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/report/weekly` | Standalone HTML report |
| `GET` | `/api/export.csv` | Raw readings from the last 7 days |
| `GET` | `/api/forecast` | Cached Open-Meteo summary |
| `GET` | `/api/devices` | Known device ids, last timestamp and row count |

## Demo mode

`py server.py --demo` runs the same API with a synthetic generator writing to
`data/demo.db`, so the dashboard can be demonstrated or developed without hardware.
