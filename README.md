# Realtime Plant Health Dashboard

A zero-purchase, local-first plant monitoring system built on an ESP32. It reads air
temperature/humidity, light level and soil moisture, shows them on an OLED, sounds a local
buzzer on critical conditions, and streams everything to a Python dashboard on your PC.

![tests](https://github.com/JITHUKANNAN-TJ/realtime-plant-health-dashboard/actions/workflows/tests.yml/badge.svg)

## Features

- Live temperature, humidity, light and soil-moisture cards with quality badges
- Explainable plant health score (0-100) with the top problem highlighted
- Watering forecast from the soil-drying trend, adjusted for weather forecasts
- Anomaly detection and stale/outlier/fault data flags
- Smart alerts: hysteresis, cooldown, escalation, snooze, mute
- Alerts in the browser, on Telegram and by email (weekly digest)
- Event journal (watering, fertilizing, repotting) alongside the charts
- 24h / 7d / 30d history from raw readings and hourly/daily rollups
- Local OLED pages and buzzer that keep working without WiFi or the PC
- Installable PWA dashboard (works best on `http://localhost`)
- Demo mode so the UI can run without hardware
- Standard-library Python backend - no `pip install`, no build step

## Hardware

| Component | Purpose |
|---|---|
| ESP32 DevKit | Controller (a second board can be kept as a spare) |
| DHT22 | Air temperature + humidity |
| LDR | Relative light level |
| Capacitive/resistive soil sensor | Soil moisture |
| 0.96" SSD1306 OLED (I2C) | Local display |
| NEO-6M GPS | Optional; no fix indoors |
| 5 V passive buzzer | Local alert |
| Breadboard + jumpers | Prototyping |

No resistors or transistors are required: the firmware uses the ESP32's internal
pull-ups for the bare DHT22/LDR and drives the buzzer directly with PWM. See
[docs/wiring.md](docs/wiring.md) for the full pin map and caveats.

## Architecture

```
ESP32 (sensors + OLED + buzzer)
  |  HTTP POST /api/readings every 3 s (offline ring buffer)
  v
Python stdlib server :8000  --  SQLite (WAL)
  |-- analytics: health score, quality, anomaly, watering forecast
  |-- alert engine + browser / Telegram / email
  |-- Open-Meteo weather (keyless)
  `-- SSE /stream  -->  Browser dashboard (PC / phone)
```

## Quick start

### 1. Backend

```powershell
cd backend
Copy-Item config.example.json config.json
py server.py
```

Open <http://localhost:8000>. To try it without hardware:

```powershell
py server.py --demo
```

### 2. Firmware

1. Install Arduino IDE 2.x and the ESP32 board package.
2. Open `firmware/plant_monitor/plant_monitor.ino`.
3. Set `WIFI_SSID`, `WIFI_PASS`, `SERVER_URL` in `config.h`.
4. Select your board and COM port, then upload.

The ESP32 posts to `SERVER_URL/api/readings`. Find your PC's IPv4 with `ipconfig`
and use e.g. `http://172.16.22.156:8000`. A 2.4 GHz network is required.

## Configuration

`backend/config.json` (gitignored) controls thresholds, device name, weather location,
Telegram and email. Never commit it - it holds secrets. Start from
`backend/config.example.json`.

| Secret | Where |
|---|---|
| Telegram bot token / chat id | `config.json` -> `telegram` |
| Gmail app password | `config.json` -> `email` |
| Optional API key | `config.json` -> `server.api_key` |

## Repository layout

```
firmware/plant_monitor/   Arduino sketch
backend/                  stdlib HTTP server, storage, analytics, alerts
dashboard/                vanilla HTML/CSS/JS PWA
replay/                   sample CSV for offline experiments
docs/                     wiring, calibration, API, vision roadmap
data/                     runtime database (gitignored)
```

## Roadmap

- [x] Firmware: sensors, OLED, buzzer, WiFi, offline buffer
- [x] Backend: ingest, SQLite, SSE, demo mode
- [x] Dashboard: live cards, charts, health score
- [x] Alerts: engine, Telegram, email
- [x] Journal, rollups, PWA, calibration wizard, API docs
- [ ] Vision / YOLO leaf-disease detection (deferred, see [docs/vision.md](docs/vision.md))

## License

MIT - see [LICENSE](LICENSE).

## Disclaimer

This project is a hobby monitor, not laboratory equipment. Sensor readings are
approximate and disease/health suggestions are advisory only.
