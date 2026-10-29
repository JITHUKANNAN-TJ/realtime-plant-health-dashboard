/* Plant dashboard front-end: vanilla JS, no build step. */
(function () {
  "use strict";

  const METRICS = [
    { key: "temp_c", field: "temp", cfg: "temp", label: "Temperature", unit: "\u00b0C", color: "#ef6461" },
    { key: "humidity_pct", field: "humidity", cfg: "humidity", label: "Humidity", unit: "%", color: "#6fb3ff" },
    { key: "light_pct", field: "light", cfg: "light", label: "Light", unit: "%", color: "#f0b429" },
    { key: "soil_pct", field: "soil", cfg: "soil", label: "Soil moisture", unit: "%", color: "#63d297" }
  ];

  const state = {
    latest: null,
    config: null,
    events: [],
    alerts: [],
    history: [],
    range: "24h",
    weather: null,
    visible: { temp: true, humidity: true, light: false, soil: true },
    muted: false,
    connected: false
  };

  const el = {};
  const cardRefs = {};

  function $(id) { return document.getElementById(id); }
  function fmt(value, digits) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    return Number(value).toFixed(digits === undefined ? 1 : digits);
  }
  function clamp(value, lo, hi) { return Math.max(lo, Math.min(hi, value)); }
  function inputNumber(id, fallback) {
    const raw = $(id).value.trim();
    const value = Number(raw);
    return raw === "" || !Number.isFinite(value) ? fallback : value;
  }
  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function timeAgo(ts) {
    const diff = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (diff < 5) return "just now";
    if (diff < 60) return diff + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }
  function clockTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(path + " -> HTTP " + response.status);
    return response.json();
  }

  function toast(message, isError) {
    const node = el.toast;
    node.textContent = message;
    node.classList.toggle("error", !!isError);
    node.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () { node.classList.remove("show"); }, 3200);
  }

  function postJson(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  // -- metrics cards ----------------------------------------------------
  function buildCards() {
    const wrap = $("cards");
    wrap.innerHTML = "";
    METRICS.forEach(function (metric) {
      const card = document.createElement("div");
      card.className = "card metric";
      card.innerHTML =
        '<div class="label"><span>' + escapeHtml(metric.label) + '</span>' +
        '<span class="badge UNKNOWN">--</span></div>' +
        '<div class="value">--<span class="unit"> ' + metric.unit + '</span></div>' +
        '<canvas height="42"></canvas>' +
        '<div class="small">no data</div>';
      wrap.appendChild(card);
      cardRefs[metric.field] = {
        card: card,
        badge: card.querySelector(".badge"),
        value: card.querySelector(".value"),
        canvas: card.querySelector("canvas"),
        note: card.querySelector(".small")
      };
    });
  }

  function metricStatus(metric, value) {
    if (value === null || value === undefined) return "UNKNOWN";
    if (metric.field === "light") {
      const hour = new Date().getHours();
      if (hour < 6 || hour >= 20) return "NIGHT";
    }
    const thresholds = state.config && state.config.thresholds && state.config.thresholds[metric.cfg];
    if (!thresholds || !thresholds.ideal || !thresholds.warn) return "UNKNOWN";
    if (value < thresholds.warn[0] || value > thresholds.warn[1]) return "CRIT";
    if (value < thresholds.ideal[0] || value > thresholds.ideal[1]) return "WARN";
    return "OK";
  }

  function renderCards() {
    METRICS.forEach(function (metric) {
      const refs = cardRefs[metric.field];
      if (!refs) return;
      const value = state.latest ? state.latest[metric.key] : null;
      const digits = metric.field === "temp" ? 1 : 0;
      refs.value.innerHTML = fmt(value, digits) +
        '<span class="unit"> ' + metric.unit + "</span>";
      const status = metricStatus(metric, value);
      refs.badge.textContent = status;
      refs.badge.className = "badge " + status;

      const values = state.history
        .map(function (point) { return point[metric.field]; })
        .filter(function (v) { return v !== null && v !== undefined; })
        .slice(-80);
      Charts.sparkline(refs.canvas, values, metric.color);

      if (values.length) {
        const min = Math.min.apply(null, values);
        const max = Math.max.apply(null, values);
        refs.note.textContent = "min " + fmt(min, digits) + " \u00b7 max " + fmt(max, digits) + " \u00b7 " + values.length + " pts";
      } else {
        refs.note.textContent = "no data";
      }
    });
  }

  // -- hero -------------------------------------------------------------
  function renderHero() {
    const latest = state.latest;
    const health = latest && latest.health ? latest.health : { score: 0, penalties: {}, top_issue: null };
    $("health-score").textContent = latest && latest.health ? Math.round(health.score) : "--";
    Charts.ring($("health-ring"), latest && latest.health ? health.score : 0);

    const issue = latest ? health.top_issue : null;
    $("top-issue").textContent = !latest ? "Waiting for data..." :
      issue ? issue : "All conditions within range.";

    const penalties = latest && health.penalties ? health.penalties : {};
    const parts = [];
    Object.keys(penalties).forEach(function (key) {
      if (penalties[key] > 0) {
        parts.push('<span class="penalty">' + escapeHtml(key) + " -" + fmt(penalties[key], 0) + "</span>");
      }
    });
    $("penalties").innerHTML = parts.join("");

    const forecast = latest && latest.forecast ? latest.forecast : null;
    $("forecast-msg").textContent = forecast && forecast.message ? forecast.message : "Collecting data...";

    const rain = state.weather ? state.weather.rain_24h_mm : null;
    const weatherChip = $("weather-chip");
    if (rain === null || rain === undefined) {
      weatherChip.textContent = "weather unavailable";
      weatherChip.className = "chip";
    } else {
      weatherChip.textContent = "rain 24h: " + fmt(rain, 1) + " mm";
      weatherChip.className = "chip " + (rain >= 2 ? "good" : "");
    }

    const rssi = latest ? latest.rssi : null;
    const signalChip = $("signal-chip");
    signalChip.textContent = rssi === null || rssi === undefined ? "WiFi --" : "WiFi " + rssi + " dBm";
    signalChip.className = "chip " + (rssi === null || rssi === undefined ? "" : rssi > -65 ? "good" : rssi > -80 ? "warn" : "bad");

    const quality = latest ? latest.quality : null;
    const qualityChip = $("quality-chip");
    qualityChip.textContent = "quality " + (quality || "--");
    qualityChip.className = "chip " + (quality === "OK" ? "good" : quality ? "warn" : "");

    if (latest) {
      $("plant-name").textContent = (state.config && state.config.device && state.config.device.name) || "Plant";
      $("device-id").textContent = latest.device || "plant-01";
      $("last-update").textContent = "updated " + timeAgo(latest.ts);
    }

    const anomalyText = latest && latest.anomalies && latest.anomalies.length
      ? latest.anomalies.join(", ") : "none";
    $("anomaly-text").textContent = anomalyText;
  }

  function renderAlerts() {
    const list = $("alert-list");
    list.innerHTML = "";
    if (!state.alerts.length) {
      list.innerHTML = '<li class="muted">No active alerts.</li>';
    } else {
      state.alerts.forEach(function (alert) {
        const li = document.createElement("li");
        li.className = "sev-" + (alert.severity || "WARN");
        li.innerHTML = '<span class="tag">' + escapeHtml(alert.severity || "") + "</span>" +
          escapeHtml(alert.message || alert.rule || "Alert") +
          ' <span class="muted small">' + escapeHtml(timeAgo(alert.ts || Math.floor(Date.now() / 1000))) + "</span>";
        list.appendChild(li);
      });
    }
    const muteButton = $("btn-mute");
    muteButton.textContent = state.muted ? "Unmute buzzer" : "Mute buzzer";
    muteButton.classList.toggle("danger", state.muted);
  }

  // -- history chart ----------------------------------------------------
  function chartSeries() {
    return METRICS.map(function (metric) {
      return {
        label: metric.label,
        unit: metric.unit === "\u00b0C" ? "C" : "%",
        color: metric.color,
        visible: !!state.visible[metric.field],
        points: state.history
          .filter(function (p) { return p[metric.field] !== null && p[metric.field] !== undefined; })
          .map(function (p) { return [p.ts, p[metric.field]]; })
      };
    });
  }

  function renderChart() {
    Charts.lineChart($("history-chart"), chartSeries(), { height: 280 });
  }

  function buildChartToggles() {
    const wrap = $("chart-toggles");
    wrap.innerHTML = "";
    METRICS.forEach(function (metric) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = metric.label;
      button.dataset.field = metric.field;
      button.className = state.visible[metric.field] ? "on" : "";
      button.addEventListener("click", function () {
        state.visible[metric.field] = !state.visible[metric.field];
        button.classList.toggle("on", state.visible[metric.field]);
        renderChart();
      });
      wrap.appendChild(button);
    });
  }

  async function loadHistory(range) {
    state.range = range || state.range;
    document.querySelectorAll("#range-tabs button").forEach(function (button) {
      button.classList.toggle("active", button.dataset.range === state.range);
    });
    try {
      const data = await api("/api/history?range=" + encodeURIComponent(state.range));
      state.history = data.points || [];
      renderCards();
      renderChart();
    } catch (err) {
      toast("Could not load history", true);
    }
  }

  // -- journal ----------------------------------------------------------
  function renderEvents() {
    const list = $("event-list");
    list.innerHTML = "";
    if (!state.events.length) {
      list.innerHTML = '<li class="muted">Nothing logged yet.</li>';
      return;
    }
    state.events.slice(0, 12).forEach(function (event) {
      const li = document.createElement("li");
      const label = document.createElement("strong");
      label.textContent = (event.type || "note") + (event.amount ? " " + event.amount : "");
      li.appendChild(label);
      if (event.note) {
        const note = document.createElement("span");
        note.textContent = " - " + event.note;
        li.appendChild(note);
      }
      const when = document.createElement("span");
      when.className = "small muted";
      when.textContent = "  " + clockTime(event.ts);
      li.appendChild(when);
      list.appendChild(li);
    });
  }

  async function loadEvents() {
    try {
      const data = await api("/api/events");
      state.events = data.events || [];
      renderEvents();
    } catch (err) { /* ignore */ }
  }

  // -- settings ---------------------------------------------------------
  function fillSettings() {
    const cfg = state.config || {};
    const device = cfg.device || {};
    const cal = cfg.calibration || {};
    const light = cal.light || {};
    const soil = cal.soil || {};
    $("set-name").value = device.name || "";
    $("set-outdoor").checked = !!device.outdoor;
    $("set-refill").value = (cfg.thresholds && cfg.thresholds.soil && cfg.thresholds.soil.refill) || 35;
    $("cal-dark").value = light.min_mv !== undefined ? light.min_mv : 2500;
    $("cal-bright").value = light.max_mv !== undefined ? light.max_mv : 150;
    $("cal-dry").value = soil.dry_mv !== undefined ? soil.dry_mv : 2600;
    $("cal-wet").value = soil.wet_mv !== undefined ? soil.wet_mv : 1200;
  }

  async function saveSettings(event) {
    event.preventDefault();
    const status = $("settings-status");
    status.textContent = "Saving...";
    try {
      const thresholds = JSON.parse(JSON.stringify((state.config && state.config.thresholds) || {}));
      const calibration = (state.config && state.config.calibration) || {};
      const lightCal = calibration.light || {};
      const soilCal = calibration.soil || {};
      thresholds.soil = thresholds.soil || {};
      thresholds.soil.refill = inputNumber("set-refill", thresholds.soil.refill || 35);
      state.config = await postJson("/api/config", {
        device: {
          name: $("set-name").value.trim() || "Plant",
          outdoor: $("set-outdoor").checked
        },
        thresholds: thresholds
      });
      await postJson("/api/calibrate", {
        light: {
          dark_mv: inputNumber("cal-dark", lightCal.min_mv),
          bright_mv: inputNumber("cal-bright", lightCal.max_mv)
        },
        soil: {
          dry_mv: inputNumber("cal-dry", soilCal.dry_mv),
          wet_mv: inputNumber("cal-wet", soilCal.wet_mv)
        }
      });
      status.textContent = "Saved.";
      toast("Settings saved");
      renderHero();
      renderCards();
    } catch (err) {
      status.textContent = "Save failed: " + err.message;
      toast("Could not save settings", true);
    }
  }

  async function testIntegration(kind) {
    const status = $("settings-status");
    status.textContent = "Sending test...";
    try {
      const result = await postJson("/api/" + kind + "/test", {});
      status.textContent = (result.ok ? "OK: " : "Failed: ") + (result.message || "");
    } catch (err) {
      status.textContent = "Test failed: " + err.message;
    }
  }

  // -- notifications ----------------------------------------------------
  function notificationsAvailable() {
    return "Notification" in window;
  }

  function refreshNotifyButton() {
    const button = $("btn-notify");
    if (!notificationsAvailable()) {
      button.disabled = true;
      button.textContent = "Notifications unavailable";
      return;
    }
    if (Notification.permission === "granted") {
      button.textContent = "Notifications on";
      button.classList.add("active");
    } else if (Notification.permission === "denied") {
      button.textContent = "Notifications blocked";
      button.disabled = true;
    }
  }

  function notify(alert) {
    if (!notificationsAvailable() || Notification.permission !== "granted") return;
    try {
      new Notification("Plant alert: " + (alert.severity || "WARN"), {
        body: alert.message || alert.rule || "",
        tag: "plant-" + (alert.rule || "alert")
      });
    } catch (err) { /* ignore */ }
  }

  // -- live data --------------------------------------------------------
  function applyLatest(latest, append) {
    if (!latest || !latest.ts) return;
    state.latest = latest;
    state.alerts = latest.active_alerts ? Object.values(latest.active_alerts) : state.alerts;
    state.muted = latest.buzzer === false;

    if (append) {
      const last = state.history.length ? state.history[state.history.length - 1] : null;
      if (last && latest.ts <= last.ts) {
        // replace duplicate timestamp
        state.history[state.history.length - 1] = historyPoint(latest);
      } else {
        state.history.push(historyPoint(latest));
        if (state.history.length > 5000) state.history.shift();
      }
    }

    renderHero();
    renderCards();
    renderAlerts();
    renderChart();
  }

  function historyPoint(latest) {
    return {
      ts: latest.ts,
      temp: latest.temp_c,
      humidity: latest.humidity_pct,
      light: latest.light_pct,
      soil: latest.soil_pct,
      quality: latest.quality
    };
  }

  function setConnection(online, text) {
    state.connected = online;
    $("conn-dot").className = "dot " + (online ? "online" : "offline");
    $("conn-text").textContent = text || (online ? "live" : "offline");
  }

  function connect() {
    if (!("EventSource" in window)) {
      setConnection(false, "no SSE support");
      return;
    }
    const source = new EventSource("/stream");
    source.addEventListener("open", function () { setConnection(true, "live"); });
    source.addEventListener("reading", function (event) {
      let latest;
      try { latest = JSON.parse(event.data); } catch (err) { return; }
      setConnection(true, "live");
      applyLatest(latest, true);
    });
    source.addEventListener("alert", function (event) {
      try {
        const payload = JSON.parse(event.data);
        (payload.alerts || []).forEach(function (alert) {
          if (alert.severity === "CRIT") notify(alert);
        });
      } catch (err) { /* ignore */ }
    });
    source.addEventListener("buzzer", function (event) {
      try {
        const payload = JSON.parse(event.data);
        state.muted = payload.enabled === false;
        renderAlerts();
      } catch (err) { /* ignore */ }
    });
    source.addEventListener("error", function () {
      setConnection(false, "reconnecting");
    });
  }

  // -- boot -------------------------------------------------------------
  async function init() {
    el.toast = $("toast");
    buildCards();
    buildChartToggles();

    refreshNotifyButton();

    document.querySelectorAll("#range-tabs button").forEach(function (button) {
      button.addEventListener("click", function () { loadHistory(button.dataset.range); });
    });

    $("btn-snooze").addEventListener("click", async function () {
      try {
        const result = await postJson("/api/alerts/snooze", { minutes: 60 });
        toast("Alerts snoozed until " + new Date(result.until * 1000).toLocaleTimeString());
      } catch (err) { toast("Could not snooze", true); }
    });

    $("btn-mute").addEventListener("click", async function () {
      try {
        const result = await postJson("/api/buzzer", { enabled: state.muted });
        state.muted = result.enabled === false;
        renderAlerts();
        toast(state.muted ? "Buzzer muted" : "Buzzer unmuted");
      } catch (err) { toast("Could not change buzzer", true); }
    });

    $("btn-notify").addEventListener("click", function () {
      if (!notificationsAvailable()) return;
      Notification.requestPermission().then(function () {
        refreshNotifyButton();
        if (Notification.permission === "granted") toast("Notifications enabled");
      });
    });

    $("btn-settings").addEventListener("click", function () {
      fillSettings();
      $("settings-dialog").showModal();
    });

    $("settings-form").addEventListener("submit", saveSettings);
    $("btn-close-settings").addEventListener("click", function () {
      $("settings-dialog").close();
    });

    $("btn-telegram-test").addEventListener("click", function () { testIntegration("telegram"); });
    $("btn-email-test").addEventListener("click", function () { testIntegration("email"); });

    $("event-form").addEventListener("submit", async function (event) {
      event.preventDefault();
      const form = event.target;
      const body = {
        type: form.type.value,
        note: form.note.value.trim(),
        amount: form.amount.value ? Number(form.amount.value) : null
      };
      try {
        await postJson("/api/events", body);
        form.note.value = "";
        form.amount.value = "";
        await loadEvents();
        toast("Logged");
      } catch (err) { toast("Could not log event", true); }
    });

    const results = await Promise.allSettled([
      api("/api/latest"),
      api("/api/config"),
      api("/api/history?range=24h"),
      api("/api/events"),
      api("/api/forecast")
    ]);

    if (results[1].status === "fulfilled") state.config = results[1].value;
    if (results[2].status === "fulfilled") state.history = (results[2].value.points || []);
    if (results[3].status === "fulfilled") state.events = results[3].value.events || [];
    if (results[4].status === "fulfilled") state.weather = results[4].value || null;

    if (results[0].status === "fulfilled" && results[0].value && results[0].value.ts) {
      applyLatest(results[0].value, false);
    } else {
      renderHero();
      renderCards();
      renderAlerts();
      renderChart();
    }

    renderEvents();
    connect();

    setInterval(function () {
      if (state.latest) $("last-update").textContent = "updated " + timeAgo(state.latest.ts);
    }, 1000);

    if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* ignore */ });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
