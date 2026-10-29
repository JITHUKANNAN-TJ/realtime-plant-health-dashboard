#!/usr/bin/env python3
"""Realtime plant health dashboard backend.

Standard library only: ThreadingHTTPServer + SQLite + Server-Sent Events.
Run with:  py server.py            (uses config.json, creates it if missing)
           py server.py --demo     (synthetic data, no hardware)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import queue
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from config import Config
from storage import Storage
from analytics import anomaly, health_score, quality, watering_forecast
from alerts import AlertEngine
from integrations import WeatherService, send_email, send_telegram, weekly_report_html
from demo import DemoGenerator

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DASHBOARD_DIR = os.path.join(ROOT_DIR, "dashboard")
DATA_DIR = os.path.join(ROOT_DIR, "data")

RANGE_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
METRIC_LABELS = {
    "temp_c": "Temperature (C)",
    "humidity_pct": "Humidity (%)",
    "soil_pct": "Soil moisture (%)",
    "light_pct": "Light (%)",
}


class Hub:
    """Server-Sent Events fan-out."""

    def __init__(self):
        self._clients = []
        self._lock = threading.Lock()

    def add(self):
        channel = queue.Queue(maxsize=200)
        with self._lock:
            self._clients.append(channel)
        return channel

    def remove(self, channel):
        with self._lock:
            if channel in self._clients:
                self._clients.remove(channel)

    def broadcast(self, event, data):
        message = (event, data)
        with self._lock:
            clients = list(self._clients)
        for channel in clients:
            try:
                channel.put_nowait(message)
            except queue.Full:
                pass


class App:
    def __init__(self, config_path=None, demo=False):
        self.demo = demo
        self.config = Config(config_path)
        self.device = self.config.get("device", "id", default="plant-01")
        db_name = "demo.db" if demo else "plant.db"
        self.storage = Storage(os.path.join(DATA_DIR, db_name))
        self.hub = Hub()
        thresholds = self.config.get("thresholds", default={})
        alert_settings = self.config.get("alerts", default={})
        self.engine = AlertEngine(
            thresholds, alert_settings, storage=self.storage, device=self.device
        )
        self.weather = WeatherService(self.config, self.storage)
        self.buzzer_enabled = bool(
            self.storage.get_config_value("buzzer_enabled", True)
        )
        self.alert_lock = threading.Lock()
        self.demo_gen = None
        self._start_background()

    # -- background jobs --------------------------------------------------
    def _start_background(self):
        threading.Thread(target=self._rollup_loop, daemon=True).start()
        threading.Thread(target=self._weather_loop, daemon=True).start()
        threading.Thread(target=self._email_loop, daemon=True).start()
        if self.demo:
            self.demo_gen = DemoGenerator(self.process_readings, self.device)
            self.demo_gen.start()

    def _rollup_loop(self):
        while True:
            try:
                self.storage.aggregate()
                self.storage.prune()
            except Exception:
                pass
            time.sleep(300)

    def _weather_loop(self):
        while True:
            try:
                self.weather.get(force=True)
            except Exception:
                pass
            refresh = max(5, int(self.config.get("weather", "refresh_min", default=30)))
            time.sleep(refresh * 60)

    def _email_loop(self):
        while True:
            try:
                self._maybe_send_weekly()
            except Exception:
                pass
            time.sleep(60)

    def _maybe_send_weekly(self):
        if not self.config.get("email", "enabled", default=False):
            return
        now = time.localtime()
        weekday = int(self.config.get("email", "weekday", default=0))
        hour = int(self.config.get("email", "hour", default=9))
        stamp = time.strftime("%Y-%m-%d", now)
        if now.tm_wday != weekday or now.tm_hour != hour:
            return
        if self.storage.get_config_value("last_weekly_email") == stamp:
            return
        html = self.weekly_report()
        ok, message = send_email(
            self.config,
            "Weekly plant report - %s" % self.config.get("device", "name", default="plant"),
            html,
        )
        if ok:
            self.storage.set_config_value("last_weekly_email", stamp)
        print("[email] weekly report:", message)

    # -- helpers ----------------------------------------------------------
    def _health(self, reading):
        data = dict(reading)
        hour = time.localtime().tm_hour
        if hour < 6 or hour >= 20:
            data.pop("light_pct", None)
        return health_score(data, self.config.get("thresholds", default={}))

    def _baseline(self):
        now = int(time.time())
        rows = self.storage.recent(self.device, now - 24 * 3600, 2000)
        baseline = {"temp": [], "humidity": [], "soil": [], "light": []}
        mapping = {
            "temp": "temp_c",
            "humidity": "humidity_pct",
            "soil": "soil_pct",
            "light": "light_pct",
        }
        for row in rows:
            for key, field in mapping.items():
                value = row.get(field)
                if value is not None:
                    baseline[key].append(value)
        return baseline

    def _forecast(self):
        now = int(time.time())
        rows = self.storage.recent(self.device, now - 12 * 3600, 600)
        return watering_forecast(
            rows,
            self.config.get("thresholds", default={}),
            outdoor=bool(self.config.get("device", "outdoor", default=False)),
            weather=self.weather.get(),
            now=now,
        )

    def _enrich(self, row):
        now = int(time.time())
        data = dict(row)
        data["health"] = self._health(row)
        data["forecast"] = self._forecast()
        data["anomalies"] = anomaly(row, self._baseline())
        data["active_alerts"] = self.engine.active()
        data["online"] = (now - int(row.get("ts") or 0)) < 30
        data["buzzer"] = self.buzzer_enabled
        return data

    def latest(self, device=None):
        row = self.storage.latest(device or self.device)
        if not row:
            return {"online": False, "device": device or self.device}
        return self._enrich(row)

    # -- ingest -----------------------------------------------------------
    def process_readings(self, raw):
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            return {"ok": False, "error": "expected object or array"}

        now = int(time.time())
        interval = int(self.config.get("device", "post_interval_s", default=3))
        device = self.device
        recent = self.storage.recent(device, now - 120, 30)
        rows = []

        for item in raw:
            if not isinstance(item, dict):
                continue
            device = str(item.get("device") or device)[:64]
            try:
                ts = int(item.get("ts") or now)
            except (TypeError, ValueError):
                ts = now
            if ts < 1600000000 or ts > now + 86400:
                ts = now

            row = {
                "ts": ts,
                "device": device,
                "temp_c": _number(item.get("temp_c")),
                "humidity_pct": _number(item.get("humidity_pct")),
                "light_mv": _integer(item.get("light_mv")),
                "light_pct": _number(item.get("light_pct")),
                "soil_mv": _integer(item.get("soil_mv")),
                "soil_pct": _number(item.get("soil_pct")),
                "gps_fix": 1 if (item.get("gps") or {}).get("fix") else 0,
                "lat": _number((item.get("gps") or {}).get("lat")),
                "lon": _number((item.get("gps") or {}).get("lon")),
                "rssi": _integer(item.get("rssi")),
                "uptime_s": _integer(item.get("uptime_s")),
                "seq": _integer(item.get("seq")),
                "created_at": now,
            }
            row["quality"] = quality(row, recent, interval, now)
            recent.append(row)
            rows.append(row)

        if not rows:
            return {"ok": False, "error": "no valid readings"}

        self.storage.insert_readings(rows)
        last = rows[-1]

        with self.alert_lock:
            result = self.engine.evaluate(last, now)

        enriched = self._enrich(last)
        self.hub.broadcast("reading", enriched)
        if result["alerts"]:
            self.hub.broadcast("alert", {"alerts": result["alerts"]})
            self._dispatch_alerts(result["alerts"])

        return {
            "ok": True,
            "stored": len(rows),
            "buzzer": self.buzzer_enabled,
            "critical": result["critical"],
            "alerts": result["alerts"],
            "health": enriched["health"]["score"],
        }

    def _dispatch_alerts(self, alerts):
        lines = []
        for alert in alerts:
            lines.append("[%s] %s" % (alert["severity"], alert["message"]))
        if not lines:
            return
        text = "Plant monitor - %s\n%s" % (
            self.config.get("device", "name", default=self.device),
            "\n".join(lines),
        )
        threading.Thread(
            target=send_telegram, args=(self.config, text), daemon=True
        ).start()
        if any(a["severity"] == "CRIT" for a in alerts) and \
                self.config.get("email", "enabled", default=False):
            threading.Thread(
                target=send_email,
                args=(self.config, "Plant alert", "<br>".join(lines)),
                daemon=True,
            ).start()

    # -- reporting --------------------------------------------------------
    def history(self, device, range_key):
        seconds = RANGE_SECONDS.get(range_key, 86400)
        return self.storage.history(device or self.device, seconds)

    def events(self, device, range_key=None):
        since = None
        if range_key in RANGE_SECONDS:
            since = int(time.time()) - RANGE_SECONDS[range_key]
        return self.storage.list_events(device or self.device, since, 500)

    def add_event(self, body):
        device = str(body.get("device") or self.device)
        event_type = str(body.get("type") or "note")[:32]
        note = str(body.get("note") or "")[:500]
        amount = _number(body.get("amount"))
        return self.storage.add_event(device, event_type, note, amount, body.get("meta"))

    def weekly_stats(self):
        now = int(time.time())
        rows = self.storage.recent(self.device, now - 7 * 86400, 20000)
        stats = []
        for field, label in METRIC_LABELS.items():
            values = [r[field] for r in rows if r.get(field) is not None]
            if not values:
                continue
            stats.append({
                "label": label,
                "avg": round(sum(values) / len(values), 1),
                "min": round(min(values), 1),
                "max": round(max(values), 1),
            })
        return stats

    def weekly_report(self):
        stats = self.weekly_stats()
        events = self.storage.list_events(self.device, int(time.time()) - 7 * 86400, 100)
        alerts = ["%s: %s" % (rule, state) for rule, state in self.engine.active().items()]
        return weekly_report_html(
            self.config.get("device", "name", default=self.device),
            stats, events, self._forecast(), alerts,
        )

    def export_csv(self):
        now = int(time.time())
        rows = self.storage.recent(self.device, now - 7 * 86400, 100000)
        output = io.StringIO()
        fields = ["ts", "temp_c", "humidity_pct", "light_mv", "light_pct",
                  "soil_mv", "soil_pct", "rssi", "quality"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return output.getvalue()

    def calibration_for_firmware(self):
        cal = self.config.get("calibration", default={})
        light = cal.get("light", {})
        soil = cal.get("soil", {})
        return {
            "light": {
                "dark_mv": light.get("min_mv", 2500),
                "bright_mv": light.get("max_mv", 150),
            },
            "soil": {
                "dry_mv": soil.get("dry_mv", 2600),
                "wet_mv": soil.get("wet_mv", 1200),
            },
        }

    def update_calibration(self, body):
        cal = self.config.get("calibration", default={})
        if "light" in body and isinstance(body["light"], dict):
            light = body["light"]
            if "dark_mv" in light:
                cal.setdefault("light", {})["min_mv"] = _integer(light["dark_mv"])
            if "bright_mv" in light:
                cal.setdefault("light", {})["max_mv"] = _integer(light["bright_mv"])
        if "soil" in body and isinstance(body["soil"], dict):
            soil = body["soil"]
            if "dry_mv" in soil:
                cal.setdefault("soil", {})["dry_mv"] = _integer(soil["dry_mv"])
            if "wet_mv" in soil:
                cal.setdefault("soil", {})["wet_mv"] = _integer(soil["wet_mv"])
        if "sensor" in body:
            sensor = body["sensor"]
            low = _integer(body.get("min_mv"))
            high = _integer(body.get("max_mv"))
            if sensor == "light":
                cal.setdefault("light", {})["min_mv"] = low
                cal.setdefault("light", {})["max_mv"] = high
            elif sensor == "soil":
                cal.setdefault("soil", {})["dry_mv"] = low
                cal.setdefault("soil", {})["wet_mv"] = high
        self.config.set("calibration", cal)
        return self.calibration_for_firmware()

    def public_config(self):
        data = self.config.data
        return {
            "device": data.get("device", {}),
            "thresholds": data.get("thresholds", {}),
            "alerts": data.get("alerts", {}),
            "weather": {
                "enabled": data.get("weather", {}).get("enabled", True),
                "latitude": data.get("weather", {}).get("latitude"),
                "longitude": data.get("weather", {}).get("longitude"),
            },
            "telegram": {"enabled": data.get("telegram", {}).get("enabled", False)},
            "email": {"enabled": data.get("email", {}).get("enabled", False)},
            "calibration": data.get("calibration", {}),
        }

    def update_public_config(self, body):
        for key in ("thresholds", "alerts"):
            if key in body and isinstance(body[key], dict):
                self.config.set(key, body[key])
        if "device" in body and isinstance(body["device"], dict):
            current = self.config.get("device", default={}) or {}
            for field in ("name", "outdoor", "post_interval_s"):
                if field in body["device"]:
                    current[field] = body["device"][field]
            self.config.set("device", current)
        if "weather" in body and isinstance(body["weather"], dict):
            current = self.config.get("weather", default={}) or {}
            for field in ("enabled", "latitude", "longitude"):
                if field in body["weather"]:
                    current[field] = body["weather"][field]
            self.config.set("weather", current)
        return self.public_config()

    def set_buzzer(self, enabled):
        self.buzzer_enabled = bool(enabled)
        self.storage.set_config_value("buzzer_enabled", self.buzzer_enabled)
        self.hub.broadcast("buzzer", {"enabled": self.buzzer_enabled})
        return self.buzzer_enabled


def _number(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value):
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


class Handler(BaseHTTPRequestHandler):
    app: App = None
    server_version = "PlantMonitor/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- utilities --------------------------------------------------------
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _authorized(self, path):
        key = self.app.config.get("server", "api_key", default="")
        if not key:
            return True
        protected = ("/api/readings", "/api/calibrate", "/api/buzzer",
                     "/api/alerts/snooze", "/api/events", "/api/config")
        if not any(path.startswith(p) for p in protected):
            return True
        return self.headers.get("X-Api-Key", "") == key

    def _serve_static(self, path):
        rel = unquote(path).lstrip("/") or "index.html"
        base = os.path.normpath(DASHBOARD_DIR)
        full = os.path.normpath(os.path.join(base, rel))
        if not full.startswith(base) or not os.path.isfile(full):
            self._send_text("Not found", 404)
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".webmanifest": "application/manifest+json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        ext = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(ext, "application/octet-stream"))
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # -- HTTP verbs -------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/stream":
                self._stream()
                return
            if path in ("/", "/index.html"):
                self._serve_static("index.html")
                return
            if path == "/api/latest":
                self._send_json(self.app.latest(_first(query, "device")))
                return
            if path == "/api/history":
                device = _first(query, "device")
                range_key = _first(query, "range") or "24h"
                self._send_json({"range": range_key,
                                 "points": self.app.history(device, range_key)})
                return
            if path == "/api/events":
                self._send_json({"events": self.app.events(
                    _first(query, "device"), _first(query, "range"))})
                return
            if path == "/api/config":
                self._send_json(self.app.public_config())
                return
            if path == "/api/calibration":
                self._send_json(self.app.calibration_for_firmware())
                return
            if path == "/api/forecast":
                self._send_json(self.app.weather.get() or {})
                return
            if path == "/api/devices":
                self._send_json({"devices": self.app.storage.devices()})
                return
            if path == "/api/report/weekly":
                self._send_text(self.app.weekly_report(), 200, "text/html; charset=utf-8")
                return
            if path == "/api/export.csv":
                self._send_text(self.app.export_csv(), 200, "text/csv; charset=utf-8")
                return
            self._serve_static(path)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._authorized(path):
            self._send_json({"ok": False, "error": "unauthorized"}, 401)
            return
        try:
            body = self._read_body()
            if path == "/api/readings":
                self._send_json(self.app.process_readings(body))
                return
            if path == "/api/events":
                event_id = self.app.add_event(body if isinstance(body, dict) else {})
                self._send_json({"ok": True, "id": event_id})
                return
            if path == "/api/alerts/snooze":
                minutes = _number(body.get("minutes")) if isinstance(body, dict) else None
                if minutes is None:
                    minutes = self.app.config.get("alerts", "snooze_default_min", default=60)
                until = self.app.engine.snooze(float(minutes))
                self._send_json({"ok": True, "until": until})
                return
            if path == "/api/buzzer":
                enabled = bool(body.get("enabled", True)) if isinstance(body, dict) else True
                self._send_json({"ok": True, "enabled": self.app.set_buzzer(enabled)})
                return
            if path == "/api/config":
                self._send_json(self.app.update_public_config(body if isinstance(body, dict) else {}))
                return
            if path == "/api/calibrate":
                self._send_json({"ok": True, "calibration": self.app.update_calibration(
                    body if isinstance(body, dict) else {})})
                return
            if path == "/api/telegram/test":
                ok, message = send_telegram(self.app.config, "Plant monitor test message")
                self._send_json({"ok": ok, "message": message})
                return
            if path == "/api/email/test":
                ok, message = send_email(
                    self.app.config, "Plant monitor test", "<p>Test message.</p>")
                self._send_json({"ok": ok, "message": message})
                return
            self._send_json({"ok": False, "error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    # -- SSE --------------------------------------------------------------
    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        channel = self.app.hub.add()
        try:
            latest = json.dumps(self.app.latest(), default=str)
            self.wfile.write(("event: reading\ndata: %s\n\n" % latest).encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    event, data = channel.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(data, default=str)
                self.wfile.write(("event: %s\ndata: %s\n\n" % (event, payload)).encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.hub.remove(channel)


def _first(query, key):
    values = query.get(key)
    return values[0] if values else None


def main():
    parser = argparse.ArgumentParser(description="Plant health dashboard backend")
    parser.add_argument("--config", default=None, help="path to config.json")
    parser.add_argument("--demo", action="store_true", help="run with synthetic data")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    app = App(args.config, demo=args.demo)
    Handler.app = app

    host = app.config.get("server", "host", default="0.0.0.0")
    port = int(app.config.get("server", "port", default=8000))
    server = ThreadingHTTPServer((host, port), Handler)

    url = "http://localhost:%d" % port
    print("Plant monitor backend running at %s" % url)
    print("Device: %s%s" % (app.device, "  (demo mode)" if args.demo else ""))
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if app.demo_gen:
            app.demo_gen.stop()
        server.server_close()


if __name__ == "__main__":
    main()
