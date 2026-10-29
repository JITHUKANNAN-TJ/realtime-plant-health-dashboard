# Calibration

The LDR and soil sensor are analog and vary from board to board. Calibrate them once and
again every few months (the ESP32 internal pull-up drifts with temperature).

## Light (LDR)

With the LDR on GPIO32 using the internal pull-up, **higher millivolts means darker**.

1. Cover the LDR completely - wait ~5 s - record the millivolt value as `dark_mv`.
2. Expose it to bright light - record `bright_mv`.
3. Values must be `dark_mv > bright_mv`; the dashboard wizard validates this.

Default fallback in firmware: `dark_mv = 2500`, `bright_mv = 150`.

## Soil moisture

1. Hold the probe in dry air (do not touch the pads) - record `dry_mv` after ~30 s.
2. Put the probe in a cup of water up to the line - record `wet_mv`.
3. Values must be `dry_mv > wet_mv`.

Default fallback in firmware: `dry_mv = 2600`, `wet_mv = 1200`.

## Applying calibration

- **Browser wizard:** Settings -> Calibration. It posts to `/api/calibrate` and stores the
  values in `backend/config.json`.
- **Firmware:** on boot the ESP32 calls `GET /api/calibration?device=<id>` and caches the
  result in NVS (`Preferences`). Reboot after calibrating.
- **Manual fallback:** edit the defaults in `firmware/plant_monitor/config.h` and reflash.

## Sanity checks

- Light: 0 % when covered, ~100 % in direct light.
- Soil: 0 % in dry air, ~100 % in water.
- If the numbers barely move, the sensor is likely wired to the wrong pin or the module is
  not powered.
