#include <Arduino.h>
#include <Wire.h>
#include "DFRobot_MatrixLidar.h"
#include <APDS9930.h>
#undef WAIT
#include <FastLED.h>


// I2C pins
#define SDA_PIN 21
#define SCL_PIN 22


// ToF addresses
#define TOF1_ADDR 0x32
#define TOF2_ADDR 0x33


// LED ring
#define LED_PIN 18
#define NUM_LEDS 12


// Hand detection threshold
#define HAND_THRESHOLD 500


// Compact serial stream for the desktop visualizer.
// Keep this at 115200 so PlatformIO Serial Monitor and the app use the same baud.
const uint32_t SERIAL_BAUD = 115200;
const uint32_t FRAME_PERIOD_MS = 50;      // 20 Hz target; actual rate is limited by sensor read time
const uint32_t STATUS_PERIOD_MS = 1000;   // throttle non-frame diagnostic messages
const uint8_t LED_BRIGHTNESS = 8;         // keep USB power draw low during bring-up
const bool STREAM_TOF2 = false;           // one-sensor bring-up path; enable later if power is solid


DFRobot_MatrixLidar_I2C tof1(TOF1_ADDR);
DFRobot_MatrixLidar_I2C tof2(TOF2_ADDR);


APDS9930 apds;
CRGB leds[NUM_LEDS];


uint16_t frame1[64];
uint16_t frame2[64];


bool tof1OK = false;
bool tof2OK = false;
bool apdsOK = false;


uint32_t lastPrintMs = 0;
uint32_t lastStatusMs = 0;
uint32_t frameSeq = 0;


void setRingColor(CRGB color) {
  for (int i = 0; i < NUM_LEDS; i++) {
    leds[i] = color;
  }
  FastLED.show();
}


void scanI2C() {
  Serial.println("\nScanning I2C bus...");


  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t error = Wire.endTransmission();


    if (error == 0) {
      Serial.print("Found I2C device at 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);
    }
  }


  Serial.println("I2C scan done.\n");
}


void print8x8(uint16_t frame[64]) {
  for (int row = 0; row < 8; row++) {
    for (int col = 0; col < 8; col++) {
      Serial.print(frame[row * 8 + col]);
      Serial.print("\t");
    }
    Serial.println();
  }
}


void printProximity(uint16_t proximity, bool handPresent) {
  Serial.print("#PROX,");
  Serial.print(proximity);
  Serial.print(",");
  Serial.println(handPresent ? 1 : 0);
}


void printFrameCSV(const char *prefix, uint32_t seq, uint32_t deviceMs, uint16_t frame[64]) {
  Serial.print(prefix);
  Serial.print(",");
  Serial.print(seq);
  Serial.print(",");
  Serial.print(deviceMs);
  for (int i = 0; i < 64; i++) {
    Serial.print(",");
    Serial.print(frame[i]);
  }
  Serial.println();
}


void printStatusThrottled(const char *message) {
  uint32_t now = millis();
  if (now - lastStatusMs < STATUS_PERIOD_MS) return;
  lastStatusMs = now;
  Serial.println(message);
}


bool setupToF(DFRobot_MatrixLidar_I2C &tof, const char *name) {
  Serial.println(name);


  if (tof.begin() != 0) {
    Serial.print(name);
    Serial.println(" begin error.");
    return false;
  }


  if (tof.setRangingMode(eMatrix_8X8) != 0) {
    Serial.print(name);
    Serial.println(" failed to set 8x8 mode.");
    return false;
  }


  Serial.print(name);
  Serial.println(" connected.");
  return true;
}


bool setupAPDS9930() {
  Serial.println("Starting APDS-9930...");


  if (!apds.init()) {
    Serial.println("APDS-9930 init FAILED.");
    return false;
  }


  Serial.println("APDS-9930 init OK.");


  if (!apds.enableProximitySensor(false)) {
    Serial.println("APDS-9930 proximity enable FAILED.");
    return false;
  }


  Serial.println("APDS-9930 proximity enabled.");
  return true;
}


void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1500);


  // LED setup
  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  setRingColor(CRGB::Red);


  // I2C setup
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  Wire.setTimeOut(50);


  Serial.println("Starting APDS-9930 + ToF + LED ring test...");


  scanI2C();


  // Setup APDS first
  apdsOK = setupAPDS9930();


  // Setup ToF sensors
  tof1OK = setupToF(tof1, "ToF #1");
  tof2OK = setupToF(tof2, "ToF #2");


  Serial.println("\nSetup complete.");
  Serial.print("APDS: "); Serial.println(apdsOK ? "OK" : "FAILED");
  Serial.print("ToF #1: "); Serial.println(tof1OK ? "OK" : "FAILED");
  Serial.print("ToF #2: "); Serial.println(tof2OK ? "OK" : "FAILED");
  Serial.println();
}


void loop() {
  if (millis() - lastPrintMs < FRAME_PERIOD_MS) return;
  lastPrintMs = millis();


  bool handPresent = false;
  uint16_t proximity = 0;


  // Read APDS first
  if (apdsOK) {
    if (apds.readProximity(proximity)) {
      if (proximity > HAND_THRESHOLD) {
        handPresent = true;
        setRingColor(CRGB::Green);
      } else {
        setRingColor(CRGB::Red);
      }
    } else {
      printStatusThrottled("#ERR,APDS,read_failed");
      setRingColor(CRGB::Blue);
    }
  } else {
    printStatusThrottled("#ERR,APDS,init_failed");
    setRingColor(CRGB::Blue);
  }


  printProximity(proximity, handPresent);


  // Only read ToF if hand is present
  if (handPresent) {
    if (tof1OK) {
      uint8_t err1 = tof1.getAllData(frame1);


      if (err1 == 0) {
        printFrameCSV("FRAME", frameSeq, millis(), frame1);
      } else {
        printStatusThrottled("#ERR,TOF1,read_failed");
      }
    } else {
      printStatusThrottled("#ERR,TOF1,init_failed");
    }


    if (STREAM_TOF2 && tof2OK) {
      uint8_t err2 = tof2.getAllData(frame2);


      if (err2 == 0) {
        printFrameCSV("FRAME2", frameSeq, millis(), frame2);
      } else {
        printStatusThrottled("#ERR,TOF2,read_failed");
      }
    }
  }

  frameSeq++;
}
