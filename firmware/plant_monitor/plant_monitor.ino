/*
 * Realtime Plant Health Monitor - ESP32 firmware
 *
 * Reads DHT22 (air temp/humidity), an LDR (relative light) and a soil moisture
 * sensor, shows them on a 0.96" SSD1306 OLED, sounds a passive buzzer on
 * critical conditions and posts JSON to the Python backend over WiFi.
 *
 * No external resistors are required: the DHT22 and LDR use the ESP32's
 * internal pull-ups, and the soil pin can optionally use one too.
 *
 * Libraries (Arduino IDE -> Library Manager):
 *   - DHT sensor library for ESPx (DHTesp)  by beegee_tokyo
 *   - U8g2                                 by oliver
 *   - ArduinoJson                          by bblanchon
 *   - TinyGPSPlus                          by mikalhart (only if GPS_ENABLE)
 *
 * Copy config.example.h to config.h and fill in your WiFi/server details.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <DHTesp.h>
#include <Preferences.h>
#include <time.h>

#include "config.h"

#if GPS_ENABLE
#include <TinyGPSPlus.h>
HardwareSerial gpsSerial(2);
TinyGPSPlus gps;
#endif

// ---------------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------------
DHTesp dht;
U8G2_SSD1306_128X64_NONAME_F_HW_I2C display(U8G2_R0, U8X8_PIN_NONE);
Preferences prefs;

float tempC = NAN;
float humidity = NAN;
int lightMv = 0;
int soilMv = 0;
float lightPct = 0;
float soilPct = 0;

int ldrDarkMv = LDR_DARK_MV;
int ldrBrightMv = LDR_BRIGHT_MV;
int soilDryMv = SOIL_DRY_MV;
int soilWetMv = SOIL_WET_MV;

unsigned long seq = 0;
bool wifiOk = false;
bool buzzerEnabled = true;
bool serverCritical = false;

unsigned long lastDht = 0;
unsigned long lastAnalog = 0;
unsigned long lastPost = 0;
unsigned long lastOled = 0;
unsigned long lastReconnect = 0;
unsigned long lastNtp = 0;
unsigned long lastAlarm = 0;

int oledPage = 0;

// Offline ring buffer of JSON payload strings
String ringBuffer[BUFFER_SIZE];
int ringCount = 0;

// Non-blocking beep state machine
enum BeepState { BEEP_IDLE, BEEP_ON, BEEP_OFF };
BeepState beepState = BEEP_IDLE;
int beepCount = 0;
unsigned long beepTs = 0;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
float round1(float v) { return roundf(v * 10.0f) / 10.0f; }

int readAvgMillivolts(int pin, int samples) {
  long sum = 0;
  for (int i = 0; i < samples; i++) {
    sum += analogReadMilliVolts(pin);
  }
  return (int)(sum / samples);
}

int clampPercent(float v) {
  if (isnan(v)) return 0;
  if (v < 0) return 0;
  if (v > 100) return 100;
  return (int)roundf(v);
}

// Higher millivolts means darker when using an internal pull-up.
float mapLightPercent(int mv) {
  int span = ldrDarkMv - ldrBrightMv;
  if (span == 0) return 0;
  return (float)(ldrDarkMv - mv) * 100.0f / (float)span;
}

float mapSoilPercent(int mv) {
  int span = soilDryMv - soilWetMv;
  if (span == 0) return 0;
  return (float)(soilDryMv - mv) * 100.0f / (float)span;
}

unsigned long epochNow() {
  time_t t = time(nullptr);
  return (t > 1600000000) ? (unsigned long)t : 0UL;
}

float bandPenalty(float value, float low, float high, float weight, float span) {
  if (isnan(value)) return 0;
  if (value < low) return weight * min(1.0f, (low - value) / span);
  if (value > high) return weight * min(1.0f, (value - high) / span);
  return 0;
}

int localHealthScore() {
  float penalty = 0;
  penalty += bandPenalty(tempC, 18, 28, 25, 10);
  penalty += bandPenalty(humidity, 40, 70, 25, 30);
  penalty += bandPenalty(soilPct, 30, 70, 35, 40);
  penalty += bandPenalty(lightPct, 20, 80, 15, 50);
  int score = (int)(100 - penalty);
  return score < 0 ? 0 : score;
}

// ---------------------------------------------------------------------------
// Calibration (NVS + server)
// ---------------------------------------------------------------------------
void loadCalibration() {
  prefs.begin("plantmon", false);
  ldrDarkMv = prefs.getInt("l_dark", LDR_DARK_MV);
  ldrBrightMv = prefs.getInt("l_bright", LDR_BRIGHT_MV);
  soilDryMv = prefs.getInt("s_dry", SOIL_DRY_MV);
  soilWetMv = prefs.getInt("s_wet", SOIL_WET_MV);
  prefs.end();
}

void saveCalibration() {
  prefs.begin("plantmon", false);
  prefs.putInt("l_dark", ldrDarkMv);
  prefs.putInt("l_bright", ldrBrightMv);
  prefs.putInt("s_dry", soilDryMv);
  prefs.putInt("s_wet", soilWetMv);
  prefs.end();
}

void fetchCalibration() {
  HTTPClient http;
  String url = String(SERVER_URL) + "/api/calibration?device=" + DEVICE_ID;
  http.setConnectTimeout(4000);
  http.setTimeout(4000);
  if (!http.begin(url)) return;
  if (strlen(API_KEY) > 0) http.addHeader("X-Api-Key", API_KEY);
  int code = http.GET();
  if (code == 200) {
    DynamicJsonDocument doc(512);
    DeserializationError err = deserializeJson(doc, http.getString());
    if (!err) {
      if (doc["light"].containsKey("dark_mv")) ldrDarkMv = doc["light"]["dark_mv"];
      if (doc["light"].containsKey("bright_mv")) ldrBrightMv = doc["light"]["bright_mv"];
      if (doc["soil"].containsKey("dry_mv")) soilDryMv = doc["soil"]["dry_mv"];
      if (doc["soil"].containsKey("wet_mv")) soilWetMv = doc["soil"]["wet_mv"];
      saveCalibration();
    }
  }
  http.end();
}

// ---------------------------------------------------------------------------
// Buzzer (passive, LEDC PWM)
// ---------------------------------------------------------------------------
void buzzerSetup() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(PIN_BUZZER, 2731, 8);
#else
  ledcSetup(0, 2731, 8);
  ledcAttachPin(PIN_BUZZER, 0);
#endif
}

void buzzerTone(bool on) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  if (on) ledcWriteTone(PIN_BUZZER, 2731);
  else ledcWriteTone(PIN_BUZZER, 0);
#else
  if (on) ledcWriteTone(0, 2731);
  else ledcWriteTone(0, 0);
#endif
}

void startBeeps(int count) {
  if (!buzzerEnabled || count <= 0) return;
  beepCount = count;
  beepState = BEEP_ON;
  beepTs = millis();
  buzzerTone(true);
}

void updateBeeps() {
  if (beepState == BEEP_IDLE) return;
  unsigned long now = millis();
  if (beepState == BEEP_ON && now - beepTs >= 200) {
    buzzerTone(false);
    beepState = BEEP_OFF;
    beepTs = now;
  } else if (beepState == BEEP_OFF && now - beepTs >= 150) {
    beepCount--;
    if (beepCount > 0) {
      buzzerTone(true);
      beepState = BEEP_ON;
    } else {
      beepState = BEEP_IDLE;
    }
    beepTs = now;
  }
}

bool criticalCondition() {
  if (!isnan(tempC) && (tempC > 32.0f || tempC < 12.0f)) return true;
  if (!isnan(humidity) && humidity < 25.0f) return true;
  if (soilPct < 20.0f) return true;
  return false;
}

// ---------------------------------------------------------------------------
// WiFi
// ---------------------------------------------------------------------------
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void maintainWiFi(unsigned long now) {
  wifiOk = (WiFi.status() == WL_CONNECTED);
  if (!wifiOk && now - lastReconnect >= RECONNECT_INTERVAL_MS) {
    lastReconnect = now;
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }
}

// ---------------------------------------------------------------------------
// Sensors
// ---------------------------------------------------------------------------
void readDht() {
  TempAndHumidity th = dht.getTempAndHumidity();
  if (dht.getStatus() == DHTesp::ERROR_NONE &&
      !isnan(th.temperature) && !isnan(th.humidity)) {
    tempC = th.temperature;
    humidity = th.humidity;
  }
}

void readAnalog() {
  lightMv = readAvgMillivolts(PIN_LDR, 8);
  soilMv = readAvgMillivolts(PIN_SOIL, 8);
  lightPct = mapLightPercent(lightMv);
  soilPct = mapSoilPercent(soilMv);
  lightPct = constrain(lightPct, 0.0f, 100.0f);
  soilPct = constrain(soilPct, 0.0f, 100.0f);
}

// ---------------------------------------------------------------------------
// Payload + uplink
// ---------------------------------------------------------------------------
String buildPayload() {
  DynamicJsonDocument doc(768);
  doc["device"] = DEVICE_ID;
  doc["seq"] = ++seq;
  doc["ts"] = epochNow();
  doc["fw"] = FW_VERSION;

  if (isnan(tempC)) doc["temp_c"] = nullptr;
  else doc["temp_c"] = round1(tempC);
  if (isnan(humidity)) doc["humidity_pct"] = nullptr;
  else doc["humidity_pct"] = round1(humidity);

  doc["light_mv"] = lightMv;
  doc["light_pct"] = round1(lightPct);
  doc["soil_mv"] = soilMv;
  doc["soil_pct"] = round1(soilPct);

  JsonObject gpsObj = doc.createNestedObject("gps");
#if GPS_ENABLE
  bool fix = gps.location.isValid() && gps.location.age() < 5000;
  gpsObj["fix"] = fix;
  if (fix) {
    gpsObj["lat"] = gps.location.lat();
    gpsObj["lon"] = gps.location.lng();
  } else {
    gpsObj["lat"] = nullptr;
    gpsObj["lon"] = nullptr;
  }
  gpsObj["sats"] = gps.satellites.isValid() ? gps.satellites.value() : 0;
#else
  gpsObj["fix"] = false;
  gpsObj["lat"] = nullptr;
  gpsObj["lon"] = nullptr;
  gpsObj["sats"] = 0;
#endif

  doc["rssi"] = WiFi.RSSI();
  doc["uptime_s"] = (unsigned long)(millis() / 1000UL);

  String out;
  serializeJson(doc, out);
  return out;
}

void ringPush(const String &payload) {
  if (ringCount < BUFFER_SIZE) {
    ringBuffer[ringCount++] = payload;
  } else {
    for (int i = 1; i < BUFFER_SIZE; i++) ringBuffer[i - 1] = ringBuffer[i];
    ringBuffer[BUFFER_SIZE - 1] = payload;
  }
}

void handleResponse(const String &body) {
  DynamicJsonDocument doc(1024);
  if (deserializeJson(doc, body)) return;
  if (doc.containsKey("buzzer")) buzzerEnabled = doc["buzzer"].as<bool>();
  if (doc.containsKey("critical")) serverCritical = doc["critical"].as<bool>();
}

bool flushBuffer() {
  if (ringCount == 0 || !wifiOk) return false;

  String payload = "[";
  for (int i = 0; i < ringCount; i++) {
    if (i > 0) payload += ",";
    payload += ringBuffer[i];
  }
  payload += "]";

  HTTPClient http;
  String url = String(SERVER_URL) + "/api/readings";
  http.setConnectTimeout(4000);
  http.setTimeout(6000);
  if (!http.begin(url)) return false;
  http.addHeader("Content-Type", "application/json");
  if (strlen(API_KEY) > 0) http.addHeader("X-Api-Key", API_KEY);

  int code = http.POST(payload);
  bool ok = false;
  if (code >= 200 && code < 300) {
    handleResponse(http.getString());
    ringCount = 0;
    ok = true;
  }
  http.end();
  return ok;
}

// ---------------------------------------------------------------------------
// OLED
// ---------------------------------------------------------------------------
void drawBar(int x, int y, int w, int h, float pct) {
  display.drawFrame(x, y, w, h);
  int fill = (int)((w - 4) * constrain(pct, 0.0f, 100.0f) / 100.0f);
  if (fill > 0) display.drawBox(x + 2, y + 2, fill, h - 4);
}

void renderOled() {
  display.clearBuffer();
  display.setFont(u8g2_font_6x10_tf);
  display.drawStr(0, 10, "PLANT MONITOR");
  display.drawHLine(0, 12, 128);

  char line[32];

  switch (oledPage) {
    case 0:
      display.drawStr(0, 26, "Temperature");
      snprintf(line, sizeof(line), "%.1f C", isnan(tempC) ? 0.0f : tempC);
      display.setFont(u8g2_font_10x20_tf);
      display.drawStr(0, 48, line);
      display.setFont(u8g2_font_6x10_tf);
      snprintf(line, sizeof(line), "Humidity %.0f%%", isnan(humidity) ? 0.0f : humidity);
      display.drawStr(0, 62, line);
      break;

    case 1:
      display.drawStr(0, 26, "Light");
      snprintf(line, sizeof(line), "%d%%  (%d mV)", clampPercent(lightPct), lightMv);
      display.drawStr(0, 38, line);
      drawBar(0, 45, 128, 12, lightPct);
      break;

    case 2:
      display.drawStr(0, 26, "Soil moisture");
      snprintf(line, sizeof(line), "%d%%  (%d mV)", clampPercent(soilPct), soilMv);
      display.drawStr(0, 38, line);
      drawBar(0, 45, 128, 12, soilPct);
      break;

    case 3:
      display.drawStr(0, 26, "Health score");
      snprintf(line, sizeof(line), "%d / 100", localHealthScore());
      display.setFont(u8g2_font_10x20_tf);
      display.drawStr(0, 50, line);
      display.setFont(u8g2_font_6x10_tf);
      display.drawStr(0, 62, wifiOk ? WiFi.localIP().toString().c_str() : "WiFi: offline");
      break;

    default:
      display.drawStr(0, 26, wifiOk ? "WiFi: connected" : "WiFi: offline");
      snprintf(line, sizeof(line), "RSSI %d dBm", WiFi.RSSI());
      display.drawStr(0, 38, line);
      snprintf(line, sizeof(line), "Uptime %lu s", (unsigned long)(millis() / 1000UL));
      display.drawStr(0, 50, line);
      snprintf(line, sizeof(line), "Buffered %d  Seq %lu", ringCount, seq);
      display.drawStr(0, 62, line);
      break;
  }

  display.sendBuffer();
  oledPage = (oledPage + 1) % 5;
}

// ---------------------------------------------------------------------------
// Setup / loop
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);

  Wire.begin(21, 22);
  display.begin();

  display.clearBuffer();
  display.setFont(u8g2_font_6x10_tf);
  display.drawStr(0, 20, "Plant Monitor");
  display.drawStr(0, 34, "Booting...");
  display.sendBuffer();

  dht.setup(PIN_DHT, DHTesp::DHT22);

  pinMode(PIN_LDR, INPUT_PULLUP);
#if SOIL_USE_PULLUP
  pinMode(PIN_SOIL, INPUT_PULLUP);
#else
  pinMode(PIN_SOIL, INPUT);
#endif

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  buzzerSetup();

  loadCalibration();

#if GPS_ENABLE
  gpsSerial.begin(9600, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
#endif

  connectWiFi();
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  lastNtp = millis();

  // Give WiFi a moment, then pull calibration from the server.
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
  }
  wifiOk = (WiFi.status() == WL_CONNECTED);
  if (wifiOk) fetchCalibration();

  startBeeps(1);  // boot beep
}

void loop() {
  unsigned long now = millis();

  updateBeeps();
#if GPS_ENABLE
  while (gpsSerial.available() > 0) gps.encode(gpsSerial.read());
#endif
  maintainWiFi(now);

  if (now - lastDht >= DHT_INTERVAL_MS) {
    lastDht = now;
    readDht();
  }

  if (now - lastAnalog >= ANALOG_INTERVAL_MS) {
    lastAnalog = now;
    readAnalog();
  }

  if (now - lastOled >= OLED_INTERVAL_MS) {
    lastOled = now;
    renderOled();
  }

  if (now - lastNtp >= NTP_RESYNC_MS) {
    lastNtp = now;
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  }

  if (now - lastPost >= POST_INTERVAL_MS) {
    lastPost = now;
    readAnalog();
    ringPush(buildPayload());
    if (wifiOk) flushBuffer();
  }

  bool critical = criticalCondition() || serverCritical;
  if (critical && now - lastAlarm > 300000UL) {
    lastAlarm = now;
    startBeeps(3);
  }
}
