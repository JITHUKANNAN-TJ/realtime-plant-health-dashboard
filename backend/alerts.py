"""Threshold alert engine with hysteresis, cooldown, escalation and snooze."""

from __future__ import annotations

import time

from analytics import _as_float

# Low-light alerts are disabled because darkness is normal at night.
LOW_ALERTS_DISABLED = {"light"}

METRIC_FIELDS = {
    "temp": ("temp_c", "Temperature"),
    "humidity": ("humidity_pct", "Humidity"),
    "soil": ("soil_pct", "Soil moisture"),
    "light": ("light_pct", "Light"),
}


class AlertEngine:
    def __init__(self, thresholds, settings=None, storage=None, device="plant-01"):
        self.thresholds = thresholds or {}
        self.settings = settings or {}
        self.storage = storage
        self.device = device
        self.states = {}
        self.global_snooze_until = 0
        self._load()

    def _load(self):
        if self.storage is None:
            return
        try:
            self.states = self.storage.load_alert_states(self.device)
        except Exception:
            self.states = {}

    def _state(self, rule):
        return self.states.get(rule) or {
            "state": "OK",
            "since_ts": 0,
            "last_notified_ts": 0,
            "snooze_until": 0,
        }

    def _save(self, rule, state):
        self.states[rule] = state
        if self.storage is not None:
            try:
                self.storage.save_alert_state(
                    self.device, rule,
                    state["state"], state["since_ts"],
                    state["last_notified_ts"], state["snooze_until"],
                )
            except Exception:
                pass

    def snooze(self, minutes):
        until = int(time.time()) + int(minutes * 60)
        self.global_snooze_until = until
        for rule, state in list(self.states.items()):
            state["snooze_until"] = until
            self._save(rule, state)
        return until

    def _desired(self, value, threshold, previous):
        warn_low, warn_high = threshold.get("warn", [None, None])
        if value is None or warn_low is None:
            return "OK"
        crit_margin = float(threshold.get("crit_margin", 0.0))
        recovery = float(threshold.get("warn_margin", 0.0))

        if value <= warn_low - crit_margin or value >= warn_high + crit_margin:
            return "CRIT"

        if previous in ("WARN", "CRIT"):
            inside = (value > warn_low + recovery) and (value < warn_high - recovery)
            if inside:
                return "OK"

        if value < warn_low or value > warn_high:
            return "WARN"
        return "OK"

    def active(self):
        return {
            rule: state["state"]
            for rule, state in self.states.items()
            if state.get("state") in ("WARN", "CRIT")
        }

    def evaluate(self, reading, now=None):
        now = now or int(time.time())
        cooldown = int(self.settings.get("cooldown_s", 600))
        escalate_after = int(self.settings.get("escalate_after_s", 900))
        notifications = []
        critical = False

        for rule, (field, label) in METRIC_FIELDS.items():
            threshold = self.thresholds.get(rule, {})
            value = _as_float(reading.get(field))
            prev = self._state(rule)
            previous = prev["state"]

            desired = self._desired(value, threshold, previous)
            if rule in LOW_ALERTS_DISABLED and value is not None:
                warn_low, warn_high = threshold.get("warn", [None, None])
                crit_margin = float(threshold.get("crit_margin", 0.0))
                low_bad = warn_low is not None and value <= warn_low - crit_margin
                if low_bad:
                    desired = "OK"

            if desired == "WARN" and previous == "WARN" and \
                    now - prev["since_ts"] >= escalate_after:
                desired = "CRIT"

            if desired != previous:
                since = now
            else:
                since = prev["since_ts"] or now

            state = {
                "state": desired,
                "since_ts": since,
                "last_notified_ts": prev["last_notified_ts"],
                "snooze_until": prev["snooze_until"],
            }

            if desired in ("WARN", "CRIT"):
                if desired == "CRIT":
                    critical = True
                transition = desired != previous
                cooldown_ok = now - prev["last_notified_ts"] >= cooldown
                snoozed = prev["snooze_until"] > now or self.global_snooze_until > now
                should_notify = (transition or (desired == "CRIT" and cooldown_ok)) and not snoozed
                if should_notify:
                    direction = "low" if (value is not None and threshold.get("warn") and
                                          value < threshold["warn"][0]) else "high"
                    notifications.append({
                        "rule": rule,
                        "metric": rule,
                        "label": label,
                        "severity": desired,
                        "value": round(value, 1) if value is not None else None,
                        "message": "%s %s (%.1f)" % (
                            label,
                            "too low" if direction == "low" else "too high",
                            value if value is not None else 0.0,
                        ),
                        "ts": now,
                    })
                    state["last_notified_ts"] = now

            self._save(rule, state)

        return {
            "alerts": notifications,
            "critical": critical,
            "active": self.active(),
        }
