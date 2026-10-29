"""Pure analysis helpers: health score, data quality, anomalies, watering forecast.

Everything here is standard library only and side-effect free so it can be unit
tested without a database or network.
"""

from __future__ import annotations

import math
import time

# (threshold key, reading key, weight, severity span, display name)
METRICS = (
    ("temp", "temp_c", 25.0, 10.0, "Temperature"),
    ("humidity", "humidity_pct", 25.0, 30.0, "Humidity"),
    ("soil", "soil_pct", 35.0, 40.0, "Soil moisture"),
    ("light", "light_pct", 15.0, 50.0, "Light"),
)

VALID_RANGES = {
    "temp_c": (-40.0, 80.0),
    "humidity_pct": (0.0, 100.0),
    "soil_pct": (0.0, 100.0),
    "light_pct": (0.0, 100.0),
}


def _as_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def health_score(reading, thresholds):
    """Return an explainable 0-100 score with per-metric penalties."""
    penalties = {}
    issues = []
    total = 0.0
    for key, field, weight, span, label in METRICS:
        value = _as_float(reading.get(field))
        band = thresholds.get(key, {}).get("ideal", [None, None])
        low, high = band[0], band[1]
        if value is None or low is None or high is None:
            penalties[key] = 0.0
            continue
        penalty = 0.0
        direction = None
        if value < low:
            penalty = weight * min(1.0, (low - value) / span)
            direction = "low"
        elif value > high:
            penalty = weight * min(1.0, (value - high) / span)
            direction = "high"
        penalties[key] = round(penalty, 2)
        total += penalty
        if penalty > 0:
            issues.append({
                "metric": key,
                "label": label,
                "direction": direction,
                "value": round(value, 1),
                "penalty": round(penalty, 2),
                "message": "%s too %s (%.1f)" % (label, direction, value),
            })

    score = max(0.0, 100.0 - total)
    top_issue = max(issues, key=lambda item: item["penalty"]) if issues else None
    return {
        "score": round(score, 1),
        "penalties": penalties,
        "top_issue": top_issue,
    }


def _stddev(values):
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def quality(reading, recent=None, post_interval_s=3, now=None):
    """Classify a reading as OK / OUT_OF_RANGE / FLATLINE / NOISY / STALE."""
    now = now or int(time.time())

    for field, (low, high) in VALID_RANGES.items():
        value = _as_float(reading.get(field))
        if value is not None and not (low <= value <= high):
            return "OUT_OF_RANGE"

    recent = recent or []
    if len(recent) >= 5:
        for field in ("soil_mv", "light_mv"):
            values = [r.get(field) for r in recent[-5:]]
            values = [v for v in values if v is not None]
            if len(values) >= 5 and len(set(values)) == 1:
                return "FLATLINE"
        temps = [ _as_float(r.get("temp_c")) for r in recent[-10:] ]
        temps = [v for v in temps if v is not None]
        if len(temps) >= 8 and _stddev(temps) > 8.0:
            return "NOISY"

    ts = _as_float(reading.get("ts"))
    if ts and ts > 1600000000 and now - ts > max(30, post_interval_s * 3):
        return "STALE"

    return "OK"


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def anomaly(reading, baseline):
    """Robust z-score check against same-hour baselines.

    ``baseline`` maps a metric key (temp/humidity/soil/light) to a list of values.
    Requires at least 12 samples per metric.
    """
    flags = []
    for key, field, _weight, _span, label in METRICS:
        value = _as_float(reading.get(field))
        values = [v for v in (_as_float(x) for x in baseline.get(key, [])) if v is not None]
        if value is None or len(values) < 12:
            continue
        med = _median(values)
        deviations = [abs(v - med) for v in values]
        mad = _median(deviations)
        if mad <= 0.0001:
            continue
        z = 0.6745 * (value - med) / mad
        if abs(z) > 3.0:
            flags.append({
                "metric": key,
                "label": label,
                "value": round(value, 1),
                "baseline": round(med, 1),
                "z": round(z, 2),
                "message": "%s is unusual for this time of day (%.1f vs %.1f)" % (
                    label, value, med),
            })
    return flags


def linear_fit(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 0.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r2


def watering_forecast(readings, thresholds, outdoor=False, weather=None, now=None):
    """Estimate how long until the soil reaches the refill threshold."""
    now = now or int(time.time())
    points = []
    for row in readings:
        ts = _as_float(row.get("ts"))
        soil = _as_float(row.get("soil_pct"))
        if ts is None or soil is None:
            continue
        if now - ts <= 12 * 3600:
            points.append((ts, soil))

    points.sort(key=lambda item: item[0])
    refill = float(thresholds.get("soil", {}).get("refill", 35))
    if not points:
        return {"status": "unknown", "message": "Not enough soil data yet."}

    current = points[-1][1]
    if current <= refill:
        return {
            "status": "due",
            "current": round(current, 1),
            "refill": refill,
            "message": "Soil is at or below the refill threshold - water now.",
        }

    if len(points) < 8 or (points[-1][0] - points[0][0]) < 3600:
        return {
            "status": "unknown",
            "current": round(current, 1),
            "refill": refill,
            "message": "Collecting data - need at least an hour of readings.",
        }

    base = points[0][0]
    xs = [(ts - base) / 3600.0 for ts, _ in points]
    ys = [v for _, v in points]
    fit = linear_fit(xs, ys)
    if fit is None:
        return {"status": "unknown", "current": round(current, 1), "refill": refill}

    slope, _intercept, r2 = fit
    confidence = "high" if r2 >= 0.8 else "medium" if r2 >= 0.5 else "low"

    if slope > -0.05:
        return {
            "status": "stable",
            "current": round(current, 1),
            "refill": refill,
            "slope": round(slope, 3),
            "confidence": confidence,
            "message": "Soil moisture is stable - no watering needed yet.",
        }

    hours = (current - refill) / (-slope)
    hours = min(hours, 14 * 24)
    note = ""
    rain_mm = 0.0
    if weather:
        rain_mm = float(weather.get("rain_24h_mm") or 0.0)
    if outdoor and rain_mm >= 2.0:
        hours += 24
        note = " Rain expected - watering delayed by a day."

    return {
        "status": "watering_soon",
        "current": round(current, 1),
        "refill": refill,
        "hours": round(hours, 1),
        "eta": int(now + hours * 3600),
        "slope": round(slope, 3),
        "confidence": confidence,
        "rain_24h_mm": round(rain_mm, 1),
        "message": "Water in about %.1f day(s).%s" % (hours / 24.0, note),
    }
