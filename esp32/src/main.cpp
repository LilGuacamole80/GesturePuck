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


DFRobot_MatrixLidar_I2C tof1(TOF1_ADDR);
DFRobot_MatrixLidar_I2C tof2(TOF2_ADDR);


APDS9930 apds;
CRGB leds[NUM_LEDS];


uint16_t frame1[64];
uint16_t frame2[64];


bool tof1OK = false;
bool tof2OK = false;
bool apdsOK = false;


const uint32_t FRAME_PERIOD_MS = 250;
uint32_t lastPrintMs = 0;


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
  Serial.begin(115200);
  delay(1500);


  // LED setup
  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setBrightness(40);
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


  Serial.println("\n\n======================================");


  bool handPresent = false;
  uint16_t proximity = 0;


  // Read APDS first
  if (apdsOK) {
    if (apds.readProximity(proximity)) {
      Serial.print("APDS-9930 proximity: ");
      Serial.println(proximity);


      if (proximity > HAND_THRESHOLD) {
        handPresent = true;
        Serial.println("Hand present: YES");
        setRingColor(CRGB::Green);
      } else {
        Serial.println("Hand present: NO");
        setRingColor(CRGB::Red);
      }
    } else {
      Serial.println("APDS-9930 read failed.");
      setRingColor(CRGB::Blue);
    }
  } else {
    Serial.println("APDS-9930 skipped.");
    setRingColor(CRGB::Blue);
  }


  delay(50);


  // Only read ToF if hand is present
  if (handPresent) {
    if (tof1OK) {
      uint8_t err1 = tof1.getAllData(frame1);


      if (err1 == 0) {
        Serial.println("\nToF #1 frame:");
        print8x8(frame1);
      } else {
        Serial.println("ToF #1 read error.");
      }
    }


    if (tof2OK) {
      uint8_t err2 = tof2.getAllData(frame2);


      if (err2 == 0) {
        Serial.println("\nToF #2 frame:");
        print8x8(frame2);
      } else {
        Serial.println("ToF #2 read error.");
      }
    }
  } else {
    Serial.println("Skipping ToF read because no hand detected.");
  }
}
