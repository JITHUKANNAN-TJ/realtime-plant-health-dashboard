/* Small dependency-free canvas chart toolkit. */
(function () {
  "use strict";

  const redraws = new Map();

  function prep(canvas, cssHeight) {
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || (canvas.parentElement && canvas.parentElement.clientWidth) || 300;
    const height = cssHeight || canvas.clientHeight || 200;
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
  }

  function register(canvas, fn) {
    redraws.set(canvas, fn);
    fn();
  }

  window.addEventListener("resize", function () {
    redraws.forEach(function (fn) { fn(); });
  });

  function sparkline(canvas, values, color) {
    register(canvas, function () {
      const { ctx, width, height } = prep(canvas, 42);
      if (!values || values.length < 2) {
        ctx.strokeStyle = "#233a2d";
        ctx.beginPath();
        ctx.moveTo(0, height - 1);
        ctx.lineTo(width, height - 1);
        ctx.stroke();
        return;
      }
      let min = Math.min.apply(null, values);
      let max = Math.max.apply(null, values);
      if (max - min < 0.001) { max += 1; min -= 1; }
      const pad = 3;
      const xFor = (i) => pad + (i / (values.length - 1)) * (width - pad * 2);
      const yFor = (v) => height - pad - ((v - min) / (max - min)) * (height - pad * 2);

      const gradient = ctx.createLinearGradient(0, 0, 0, height);
      gradient.addColorStop(0, hexToRgba(color, 0.28));
      gradient.addColorStop(1, hexToRgba(color, 0));

      ctx.beginPath();
      values.forEach(function (v, i) {
        const x = xFor(i), y = yFor(v);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.lineTo(xFor(values.length - 1), height);
      ctx.lineTo(xFor(0), height);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();

      ctx.beginPath();
      values.forEach(function (v, i) {
        const x = xFor(i), y = yFor(v);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.8;
      ctx.lineJoin = "round";
      ctx.stroke();
    });
  }

  function ring(canvas, score) {
    register(canvas, function () {
      const { ctx, width, height } = prep(canvas, 150);
      const cx = width / 2, cy = height / 2;
      const radius = Math.min(width, height) / 2 - 12;
      const start = -Math.PI / 2;
      const value = Math.max(0, Math.min(100, score || 0));
      const color = value >= 80 ? "#63d297" : value >= 60 ? "#f0b429" : "#ef6461";

      ctx.lineWidth = 12;
      ctx.lineCap = "round";
      ctx.strokeStyle = "#1b3126";
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.stroke();

      ctx.strokeStyle = color;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, start, start + Math.PI * 2 * (value / 100));
      ctx.stroke();

      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function lineChart(canvas, series, options) {
    const state = { series: series, options: options || {}, hoverTs: null };
    canvas.__chartState = state;

    if (!canvas.__chartBound) {
      canvas.__chartBound = true;
      canvas.addEventListener("mousemove", function (event) {
        const rect = canvas.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const { minTs, maxTs } = timeRange(canvas.__chartState);
        if (minTs == null) return;
        const pad = 44;
        const usable = rect.width - pad - 44;
        const ratio = Math.max(0, Math.min(1, (x - pad) / usable));
        canvas.__chartState.hoverTs = minTs + ratio * (maxTs - minTs);
        drawLineChart(canvas);
      });
      canvas.addEventListener("mouseleave", function () {
        canvas.__chartState.hoverTs = null;
        drawLineChart(canvas);
      });
    }

    register(canvas, function () { drawLineChart(canvas); });
  }

  function timeRange(state) {
    let minTs = null, maxTs = null;
    (state.series || []).forEach(function (s) {
      if (s.visible === false) return;
      (s.points || []).forEach(function (p) {
        const ts = p[0];
        if (minTs === null || ts < minTs) minTs = ts;
        if (maxTs === null || ts > maxTs) maxTs = ts;
      });
    });
    return { minTs, maxTs };
  }

  function drawLineChart(canvas) {
    const state = canvas.__chartState;
    if (!state) return;
    const opts = state.options || {};
    const cssHeight = opts.height || 280;
    const { ctx, width, height } = prep(canvas, cssHeight);
    const pad = { l: 44, r: 44, t: 14, b: 28 };
    const plotW = width - pad.l - pad.r;
    const plotH = height - pad.t - pad.b;

    ctx.fillStyle = "#0d1813";
    ctx.fillRect(pad.l, pad.t, plotW, plotH);

    const visible = (state.series || []).filter(function (s) { return s.visible !== false && s.points && s.points.length; });
    const range = timeRange(state);
    if (!visible.length || range.minTs === null) {
      ctx.fillStyle = "#7d9b8b";
      ctx.font = "13px Segoe UI, sans-serif";
      ctx.fillText("No data yet", pad.l + 12, pad.t + 24);
      return;
    }

    let minTs = range.minTs, maxTs = range.maxTs;
    if (maxTs - minTs < 60000) maxTs = minTs + 60000;

    const leftSeries = visible.filter(function (s) { return s.unit !== "C"; });
    const rightSeries = visible.filter(function (s) { return s.unit === "C"; });

    let rightMin = 0, rightMax = 40;
    if (rightSeries.length) {
      const vals = [];
      rightSeries.forEach(function (s) { s.points.forEach(function (p) { if (p[1] != null) vals.push(p[1]); }); });
      if (vals.length) {
        rightMin = Math.min.apply(null, vals) - 2;
        rightMax = Math.max.apply(null, vals) + 2;
      }
    }

    const xFor = (ts) => pad.l + ((ts - minTs) / (maxTs - minTs)) * plotW;
    const yLeft = (v) => pad.t + plotH - (Math.max(0, Math.min(100, v)) / 100) * plotH;
    const yRight = (v) => pad.t + plotH - ((v - rightMin) / (rightMax - rightMin)) * plotH;

    // grid
    ctx.strokeStyle = "#1b2f25";
    ctx.fillStyle = "#7d9b8b";
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (plotH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(width - pad.r, y);
      ctx.stroke();
      const leftVal = Math.round(100 - (100 / 4) * i);
      ctx.textAlign = "right";
      ctx.fillText(leftVal + "%", pad.l - 6, y + 4);
      if (rightSeries.length) {
        const rightVal = rightMax - ((rightMax - rightMin) / 4) * i;
        ctx.textAlign = "left";
        ctx.fillText(rightVal.toFixed(0) + "\u00b0", width - pad.r + 6, y + 4);
      }
    }

    // x labels
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i++) {
      const ts = minTs + ((maxTs - minTs) / 4) * i;
      const x = xFor(ts);
      ctx.fillStyle = "#7d9b8b";
      ctx.fillText(formatTime(ts, maxTs - minTs), x, height - 8);
    }

    // series
    visible.forEach(function (s) {
      const yFor = s.unit === "C" ? yRight : yLeft;
      ctx.beginPath();
      let started = false;
      s.points.forEach(function (p) {
        if (p[1] == null) { started = false; return; }
        const x = xFor(p[0]), y = yFor(p[1]);
        if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
      });
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.stroke();

      if (s.points.length) {
        const last = s.points[s.points.length - 1];
        if (last[1] != null) {
          ctx.fillStyle = s.color;
          ctx.beginPath();
          ctx.arc(xFor(last[0]), yFor(last[1]), 3, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    });

    // hover crosshair + tooltip
    if (state.hoverTs != null) {
      const x = xFor(state.hoverTs);
      ctx.strokeStyle = "rgba(220,239,227,.35)";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(x, pad.t);
      ctx.lineTo(x, pad.t + plotH);
      ctx.stroke();
      ctx.setLineDash([]);

      const lines = [];
      visible.forEach(function (s) {
        const point = nearestPoint(s.points, state.hoverTs);
        if (point) {
          lines.push({
            text: s.label + ": " + point[1].toFixed(1) + (s.unit === "C" ? " C" : "%"),
            color: s.color
          });
        }
      });
      if (lines.length) {
        const boxW = 170;
        const boxH = 10 + lines.length * 16;
        let bx = x + 12;
        if (bx + boxW > width - 4) bx = x - boxW - 12;
        const by = pad.t + 8;
        ctx.fillStyle = "rgba(8,18,13,.94)";
        ctx.strokeStyle = "#2f5641";
        ctx.lineWidth = 1;
        roundRect(ctx, bx, by, boxW, boxH, 8);
        ctx.fill();
        ctx.stroke();
        ctx.textAlign = "left";
        lines.forEach(function (line, i) {
          ctx.fillStyle = line.color;
          ctx.fillText(line.text, bx + 12, by + 18 + i * 16);
        });
      }
    }
  }

  function nearestPoint(points, ts) {
    if (!points || !points.length) return null;
    let lo = 0, hi = points.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (points[mid][0] < ts) lo = mid + 1; else hi = mid;
    }
    let best = points[lo];
    if (lo > 0) {
      const prev = points[lo - 1];
      if (Math.abs(prev[0] - ts) < Math.abs(best[0] - ts)) best = prev;
    }
    return best;
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function formatTime(ts, spanSeconds) {
    const d = new Date(ts * 1000);
    if (spanSeconds > 2 * 86400) {
      return (d.getMonth() + 1) + "/" + d.getDate();
    }
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function hexToRgba(hex, alpha) {
    const value = hex.replace("#", "");
    const r = parseInt(value.substring(0, 2), 16);
    const g = parseInt(value.substring(2, 4), 16);
    const b = parseInt(value.substring(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  window.Charts = {
    sparkline: sparkline,
    ring: ring,
    lineChart: lineChart
  };
})();
