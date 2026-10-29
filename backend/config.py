"""Configuration loading for the plant monitor backend.

Only the Python standard library is used. Settings live in ``config.json`` next to
this file; missing keys fall back to ``DEFAULTS``.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading

DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 8000, "api_key": ""},
    "device": {
        "id": "plant-01",
        "name": "Living Room Plant",
        "outdoor": False,
        "post_interval_s": 3,
    },
    "thresholds": {
        "temp": {"ideal": [18, 28], "warn": [15, 32], "warn_margin": 1.0, "crit_margin": 3.0},
        "humidity": {"ideal": [40, 70], "warn": [30, 80], "warn_margin": 3.0, "crit_margin": 10.0},
        "soil": {"ideal": [30, 70], "warn": [20, 85], "warn_margin": 3.0, "crit_margin": 8.0, "refill": 35},
        "light": {"ideal": [20, 80], "warn": [10, 92], "warn_margin": 3.0, "crit_margin": 8.0},
    },
    "alerts": {"cooldown_s": 600, "escalate_after_s": 900, "snooze_default_min": 60},
    "weather": {"enabled": True, "latitude": 13.08, "longitude": 80.27, "refresh_min": 30},
    "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "username": "",
        "app_password": "",
        "to": "",
        "weekday": 0,
        "hour": 9,
    },
    "calibration": {
        "light": {"min_mv": 2500, "max_mv": 150, "invert": True},
        "soil": {"dry_mv": 2600, "wet_mv": 1200},
    },
    "vision": {"enabled": False},
}


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


class Config:
    def __init__(self, path=None):
        here = os.path.dirname(os.path.abspath(__file__))
        self.path = path or os.path.join(here, "config.json")
        self._lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                self.data = _merge(self.data, loaded)
            except (OSError, ValueError):
                pass
        self.save()

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, keys, value):
        if isinstance(keys, str):
            keys = [keys]
        with self._lock:
            node = self.data
            for key in keys[:-1]:
                node = node.setdefault(key, {})
            node[keys[-1]] = value
            self.save()

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=False)
                fh.write("\n")
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
