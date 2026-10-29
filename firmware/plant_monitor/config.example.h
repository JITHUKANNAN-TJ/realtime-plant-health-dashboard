#pragma once

// ---------------------------------------------------------------------------
// WiFi - must be a 2.4 GHz network (ESP32 cannot join 5 GHz)
// ---------------------------------------------------------------------------
#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASS "YOUR_WIFI_PASSWORD"

// ---------------------------------------------------------------------------
// Backend on your PC. Use the PC's IPv4 on the same network, port 8000.
// Phone hotspot example: http://192.168.43.100:8000
// ---------------------------------------------------------------------------
#define SERVER_URL "http://192.168.43.100:8000"
#define DEVICE_ID "plant-01"

// Optional shared secret. Must match server.api_key in backend/config.json.
#define API_KEY ""

// ---------------------------------------------------------------------------
// Pins (ADC1 only, because WiFi/ESP-NOW disables ADC2)
// ---------------------------------------------------------------------------
#define PIN_DHT 4
#define PIN_LDR 32
#define PIN_SOIL 33
#define PIN_BUZZER 25
#define PIN_GPS_RX 16
#define PIN_GPS_TX 17

// GPS is useless indoors; enable only when the plant is outdoors.
#define GPS_ENABLE false

// Set true only if the soil probe is bare (no module board / no analog driver).
#define SOIL_USE_PULLUP false

#define FW_VERSION "1.0.0"

// ---------------------------------------------------------------------------
// Timing
// ---------------------------------------------------------------------------
#define DHT_INTERVAL_MS 2500
#define ANALOG_INTERVAL_MS 1000
#define POST_INTERVAL_MS 3000
#define OLED_INTERVAL_MS 3000
#define RECONNECT_INTERVAL_MS 10000
#define NTP_RESYNC_MS 3600000UL
#define BUFFER_SIZE 60

// ---------------------------------------------------------------------------
// Local fallbacks used before/without calibration from the server
// ---------------------------------------------------------------------------
#define LDR_DARK_MV 2500
#define LDR_BRIGHT_MV 150
#define SOIL_DRY_MV 2600
#define SOIL_WET_MV 1200
