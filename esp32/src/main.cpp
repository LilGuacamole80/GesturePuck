#include <Arduino.h>
#include <Wire.h>
#include "DFRobot_MatrixLidar.h"
#include <APDS9930.h>
#undef WAIT
#include <FastLED.h>

// ---------------------------------------------------------------------------
// Pin / address configuration
// ---------------------------------------------------------------------------
#define SDA_PIN 21
#define SCL_PIN 22

#define TOF1_ADDR 0x32
#define TOF2_ADDR 0x33

#define LED_PIN   18
#define NUM_LEDS  12

// APDS-9930 hand-detection threshold (keep in sync with lidar_gesture_studio.py)
#define HAND_THRESHOLD 500

// ---------------------------------------------------------------------------
// Serial protocol
// ---------------------------------------------------------------------------
// Each ToF frame is sent as a binary packet, distinguished by its magic:
//   ToF #1:  "MLD1" + uint32 seq + uint32 millis + uint32 read_us + 64×uint16 + uint16 checksum
//   ToF #2:  "MLD2" + uint32 seq + uint32 millis + uint32 read_us + 64×uint16 + uint16 checksum
//
// APDS proximity is sent as a single ASCII line (infrequent, ~4 Hz):
//   #PROX,<proximity_value>,<0|1>\n
//   field 2 = 1 if hand present, 0 otherwise
//
// The Python visualizer reads both streams from the same serial port.
const uint32_t SERIAL_BAUD = 921600;

// Frame rate target.  Reading two sensors takes ~2× as long, so 25 FPS is
// a safe starting point; raise to 40 (25 ms) if your sensor reads are fast.
const uint32_t FRAME_PERIOD_MS = 40;   // ~25 FPS per sensor pair

// APDS proximity is polled every N frame cycles to keep the serial stream clean.
const uint32_t PROX_EVERY_N_FRAMES = 8;

// ---------------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------------
DFRobot_MatrixLidar_I2C tof1(TOF1_ADDR);
DFRobot_MatrixLidar_I2C tof2(TOF2_ADDR);
APDS9930 apds;
CRGB leds[NUM_LEDS];

uint16_t frame1[64];
uint16_t frame2[64];

bool tof1OK = false;
bool tof2OK = false;
bool apdsOK = false;

uint32_t seq        = 0;
uint32_t frameCount = 0;
uint32_t nextFrameMs = 0;

bool     handPresent   = false;
uint16_t lastProximity = 0;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
void setRingColor(CRGB color) {
  for (int i = 0; i < NUM_LEDS; i++) leds[i] = color;
  FastLED.show();
}

void putU16LE(uint8_t* buf, size_t &pos, uint16_t v) {
  buf[pos++] = v & 0xFF;
  buf[pos++] = (v >> 8) & 0xFF;
}

void putU32LE(uint8_t* buf, size_t &pos, uint32_t v) {
  buf[pos++] = v & 0xFF;
  buf[pos++] = (v >> 8) & 0xFF;
  buf[pos++] = (v >> 16) & 0xFF;
  buf[pos++] = (v >> 24) & 0xFF;
}

// Send one binary ToF packet.
// magic[4] distinguishes the two sensors ("MLD1" vs "MLD2").
void writeBinaryFrame(const uint8_t magic[4], uint16_t* frameData,
                      uint32_t frameSeq, uint32_t frameMs, uint32_t readUs) {
  // Packet layout: 4 magic + 4 seq + 4 millis + 4 read_us + 128 pixels + 2 checksum = 146 bytes
  uint8_t packet[4 + 4 + 4 + 4 + 64 * 2 + 2];
  size_t pos = 0;

  for (uint8_t i = 0; i < 4; i++) packet[pos++] = magic[i];
  putU32LE(packet, pos, frameSeq);
  putU32LE(packet, pos, frameMs);
  putU32LE(packet, pos, readUs);
  for (uint8_t i = 0; i < 64; i++) putU16LE(packet, pos, frameData[i]);

  uint16_t checksum = 0;
  for (size_t i = 0; i < pos; i++) checksum += packet[i];
  putU16LE(packet, pos, checksum);

  Serial.write(packet, pos);
}

bool setupToF(DFRobot_MatrixLidar_I2C &tof, uint8_t addr) {
  if (tof.begin() != 0) {
    Serial.print("# ToF 0x");
    Serial.print(addr, HEX);
    Serial.println(" begin error.");
    return false;
  }
  if (tof.setRangingMode(eMatrix_8X8) != 0) {
    Serial.print("# ToF 0x");
    Serial.print(addr, HEX);
    Serial.println(" failed to set 8x8 mode.");
    return false;
  }
  Serial.print("# ToF 0x");
  Serial.print(addr, HEX);
  Serial.println(" ready.");
  return true;
}

bool setupAPDS() {
  if (!apds.init()) {
    Serial.println("# APDS-9930 init failed.");
    return false;
  }
  if (!apds.enableProximitySensor(false)) {
    Serial.println("# APDS-9930 proximity enable failed.");
    return false;
  }
  Serial.println("# APDS-9930 ready.");
  return true;
}

// ---------------------------------------------------------------------------
// setup
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1500);

  // LED ring — show red while initialising
  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setBrightness(40);
  setRingColor(CRGB::Red);

  // I2C
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);   // 100 kHz; bump to 400000 if sensors are stable
  Wire.setTimeOut(50);

  Serial.println("# GesturePuck dual-sensor firmware");
  Serial.println("# Protocol: MLD1/MLD2 binary packets + #PROX text lines");

  apdsOK = setupAPDS();
  tof1OK = setupToF(tof1, TOF1_ADDR);
  tof2OK = setupToF(tof2, TOF2_ADDR);

  Serial.print("# APDS:  "); Serial.println(apdsOK ? "OK" : "FAILED");
  Serial.print("# ToF1:  "); Serial.println(tof1OK ? "OK" : "FAILED");
  Serial.print("# ToF2:  "); Serial.println(tof2OK ? "OK" : "FAILED");

  // Blue = ready but no hand yet
  setRingColor(CRGB::Blue);
}

// ---------------------------------------------------------------------------
// loop
// ---------------------------------------------------------------------------

// Magic bytes for each sensor
static const uint8_t MAGIC1[4] = {'M', 'L', 'D', '1'};
static const uint8_t MAGIC2[4] = {'M', 'L', 'D', '2'};

void loop() {
  uint32_t now = millis();
  if ((int32_t)(now - nextFrameMs) < 0) return;
  nextFrameMs = now + FRAME_PERIOD_MS;

  // ------------------------------------------------------------------
  // 1. Poll APDS-9930 every PROX_EVERY_N_FRAMES cycles
  // ------------------------------------------------------------------
  if (apdsOK && (frameCount % PROX_EVERY_N_FRAMES == 0)) {
    uint16_t proximity = 0;
    if (apds.readProximity(proximity)) {
      lastProximity = proximity;
      handPresent   = (proximity > HAND_THRESHOLD);
    }
    // Send compact ASCII proximity line — won't be mistaken for a binary packet
    // because it starts with '#', not "MLD".
    Serial.print("#PROX,");
    Serial.print(lastProximity);
    Serial.print(",");
    Serial.println(handPresent ? 1 : 0);
  }

  // ------------------------------------------------------------------
  // 2. Update LED ring to reflect hand presence
  // ------------------------------------------------------------------
  setRingColor(handPresent ? CRGB::Green : CRGB::Red);

  // ------------------------------------------------------------------
  // 3. Read and stream ToF sensors (only when hand is present)
  // ------------------------------------------------------------------
  uint32_t frameSeq = seq++;
  frameCount++;

  if (!handPresent) return;   // skip expensive I2C reads when no hand

  if (tof1OK) {
    uint32_t t0 = micros();
    if (tof1.getAllData(frame1) == 0) {
      writeBinaryFrame(MAGIC1, frame1, frameSeq, now, micros() - t0);
    } else {
      Serial.println("# ToF1 read error");
    }
  }

  if (tof2OK) {
    uint32_t t0 = micros();
    if (tof2.getAllData(frame2) == 0) {
      writeBinaryFrame(MAGIC2, frame2, frameSeq, now, micros() - t0);
    } else {
      Serial.println("# ToF2 read error");
    }
  }
}