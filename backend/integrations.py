"""Free integrations: Open-Meteo weather, Telegram messages, Gmail reports.

All outbound calls are best-effort; failures are reported but never crash the
server. Standard library only.
"""

from __future__ import annotations

import json
import smtplib
import time
import urllib.parse
import urllib.request
from email.mime.text import MIMEText


class WeatherService:
    API = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, config, storage=None):
        self.config = config
        self.storage = storage
        self.cache = None
        self.cache_ts = 0

    def _enabled(self):
        return bool(self.config.get("weather", "enabled", default=True))

    def _fresh(self):
        refresh = max(5, int(self.config.get("weather", "refresh_min", default=30))) * 60
        return self.cache is not None and (time.time() - self.cache_ts) < refresh

    def get(self, force=False):
        if not self._enabled():
            return None
        if not force and self._fresh():
            return self.cache
        try:
            self.cache = self._fetch()
            self.cache_ts = time.time()
        except Exception:
            pass
        return self.cache

    def _fetch(self):
        latitude = self.config.get("weather", "latitude", default=0.0)
        longitude = self.config.get("weather", "longitude", default=0.0)
        params = urllib.parse.urlencode({
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation",
            "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
            "forecast_days": 3,
        })
        request = urllib.request.Request(
            self.API + "?" + params, headers={"User-Agent": "plant-monitor/1.0"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        hourly = payload.get("hourly", {})
        rain = hourly.get("precipitation") or []
        rain_24h = sum(float(x or 0) for x in rain[:24])
        temps = hourly.get("temperature_2m") or []
        hums = hourly.get("relative_humidity_2m") or []
        daily = payload.get("daily", {})
        return {
            "updated": int(time.time()),
            "rain_24h_mm": round(rain_24h, 1),
            "temp_now": temps[0] if temps else None,
            "humidity_now": hums[0] if hums else None,
            "daily": {
                "time": daily.get("time", []),
                "precipitation_sum": daily.get("precipitation_sum", []),
                "temp_max": daily.get("temperature_2m_max", []),
                "temp_min": daily.get("temperature_2m_min", []),
            },
        }


def send_telegram(config, text, timeout=10):
    if not config.get("telegram", "enabled", default=False):
        return False, "telegram disabled"
    token = config.get("telegram", "bot_token", default="")
    chat_id = config.get("telegram", "chat_id", default="")
    if not token or not chat_id:
        return False, "telegram not configured"
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        request = urllib.request.Request(url, data=body)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            ok = response.status == 200
        return ok, "sent" if ok else "http error"
    except Exception as exc:  # network / config problems must not crash
        return False, str(exc)


def send_email(config, subject, html, timeout=20):
    if not config.get("email", "enabled", default=False):
        return False, "email disabled"
    host = config.get("email", "smtp_host", default="smtp.gmail.com")
    port = int(config.get("email", "smtp_port", default=465))
    username = config.get("email", "username", default="")
    password = config.get("email", "app_password", default="")
    recipient = config.get("email", "to", default="") or username
    if not username or not password or not recipient:
        return False, "email not configured"

    message = MIMEText(html, "html", "utf-8")
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    try:
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
            server.login(username, password)
            server.sendmail(username, [recipient], message.as_string())
        return True, "sent"
    except Exception as exc:
        return False, str(exc)


def weekly_report_html(device, stats, events, forecast, alerts):
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            item.get("label", ""),
            item.get("avg", "-"),
            item.get("min", "-"),
            item.get("max", "-"),
        )
        for item in stats
    )
    event_rows = "".join(
        "<li>%s - %s%s</li>" % (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(e["ts"])),
            e.get("type", "event"),
            (": " + e["note"]) if e.get("note") else "",
        )
        for e in events[:20]
    ) or "<li>No events logged.</li>"
    alert_rows = "".join(
        "<li>%s</li>" % a for a in alerts
    ) or "<li>No active alerts.</li>"
    forecast_text = forecast.get("message", "No forecast available.") if forecast else "No forecast available."

    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Weekly plant report</title>
<style>
body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0d1512;color:#d8efe2;padding:24px}}
h1,h2{{color:#7ee2a8}}table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{border:1px solid #244034;padding:8px;text-align:left}}
th{{background:#14251d}}ul{{line-height:1.6}}
.small{{color:#7d9b8b;font-size:12px}}
</style></head><body>
<h1>Weekly plant report - {device}</h1>
<p>{forecast}</p>
<h2>Sensor averages (7 days)</h2>
<table><tr><th>Metric</th><th>Average</th><th>Min</th><th>Max</th></tr>{rows}</table>
<h2>Active alerts</h2><ul>{alert_rows}</ul>
<h2>Recent events</h2><ul>{event_rows}</ul>
<p class="small">Generated {generated} by realtime-plant-health-dashboard.</p>
</body></html>""".format(
        device=device,
        forecast=forecast_text,
        rows=rows or "<tr><td colspan='4'>No data yet.</td></tr>",
        alert_rows=alert_rows,
        event_rows=event_rows,
        generated=time.strftime("%Y-%m-%d %H:%M"),
    )
