# Wiring

All parts are powered from the ESP32's USB connection. No resistors, transistors or
diodes are required - the firmware uses the ESP32's internal pull-ups and drives the
buzzer directly with PWM.

## Pin map

| Signal | ESP32 GPIO | Notes |
|---|---|---|
| DHT22 DATA | 4 | `INPUT_PULLUP` (internal ~45 kOhm) |
| DHT22 VCC / GND | 3.3 V / GND | |
| LDR (one leg) | 32 | `INPUT_PULLUP`; other leg to GND |
| Soil analog out | 33 | ADC1; set `SOIL_USE_PULLUP` only for bare prongs |
| Soil VCC / GND | 3.3 V / GND | module input |
| OLED SDA | 21 | I2C |
| OLED SCL | 22 | I2C |
| OLED VCC / GND | 3.3 V / GND | address `0x3C` |
| Buzzer + | 25 | passive buzzer, PWM ~2731 Hz |
| Buzzer - | GND | |
| GPS TX -> ESP RX2 | 16 | optional, 9600 baud |
| GPS RX -> ESP TX2 | 17 | optional |
| GPS VCC / GND | 3.3 V / GND | leave unpowered indoors |

> Use **ADC1** pins only (GPIO 32-39). ADC2 does not work while WiFi is active.
> GPIO 34-39 are input-only and have no internal pull-ups, which is why the LDR and
> soil sensor use GPIO 32 and 33.

## Diagram

```
                    ESP32 DevKit (USB 5 V)
 3.3V ──┬────────────────────[3V3]   [VIN] (unused)
        │
      DHT22 VCC ── 3.3V
      DHT22 GND ── GND
        │
  DHT22 DATA ─────────────[GPIO4]     (internal pull-up)
  LDR ────────────────────[GPIO32]    (internal pull-up) ── other leg ── GND
  Soil AO ────────────────[GPIO33]
  Soil VCC ── 3.3V
  Soil GND ── GND
  OLED SDA ───────────────[GPIO21]
  OLED SCL ───────────────[GPIO22]
  OLED VCC ── 3.3V
  OLED GND ── GND
  Buzzer (+) ─────────────[GPIO25]
  Buzzer (-) ── GND
  GPS TX ─────────────────[GPIO16]
  GPS RX ─────────────────[GPIO17]
  GPS VCC ── 3.3V (optional)
  GPS GND ── GND
```

## No-purchase workarounds and caveats

| Missing part | Workaround | Caveat |
|---|---|---|
| 10 k DHT22 pull-up | internal `INPUT_PULLUP` on GPIO4 | occasional CRC failure; firmware retries and keeps the last good value |
| 10 k LDR resistor | internal `INPUT_PULLUP` on GPIO32 | relative light only; internal resistance varies per board and with temperature |
| 10 k soil resistor | module AO direct; bare prongs use GPIO33 pull-up | bare prongs corrode quickly; calibrate |
| NPN driver + resistor | buzzer driven directly by GPIO25 | a 5 V buzzer on 3.3 V may be faint or silent |

Keep the DHT22 and LDR wires short (< 10 cm) for reliable readings.

## Soil sensor check

- **Module (recommended):** a small PCB with `VCC`, `GND` and `AO`. Wire `AO` to GPIO33
  and leave `SOIL_USE_PULLUP false`.
- **Bare two-prong:** no board. Wire one prong to GPIO33 and the other to GND, and set
  `SOIL_USE_PULLUP true`.

## Power notes

- GPS draws current spikes; leave it unpowered indoors (`GPS_ENABLE false`).
- If the board resets when the buzzer sounds, shorten the beeps or power the ESP32 from
  a stronger USB port.

## Calibration

See [calibration.md](calibration.md). The firmware fetches calibration from the backend
at boot (`GET /api/calibration`) and caches it in NVS.
