#include <Arduino.h>
#include <Wire.h>
#include "DFRobot_MatrixLidar.h"
#include <APDS9930.h>
#undef WAIT
#include <FastLED.h>

// ── BLE UART (Nordic UART Service) ────────────────────────────────────────────
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

#define BLE_DEVICE_NAME   "GesturePuck"

// Nordic UART Service UUIDs — same as what the Python client expects
#define NUS_SERVICE_UUID  "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_CHAR_RX_UUID  "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  // host → ESP32
#define NUS_CHAR_TX_UUID  "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  // ESP32 → host (notify)

static BLECharacteristic *pTxCharacteristic = nullptr;
static bool bleConnected = false;

// BLE server callbacks — track connection state
class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer *pServer) override {
        bleConnected = true;
        Serial.println("#BLE,connected");
    }
    void onDisconnect(BLEServer *pServer) override {
        bleConnected = false;
        Serial.println("#BLE,disconnected");
        // Restart advertising so a new client can connect
        pServer->getAdvertising()->start();
    }
};

// RX characteristic callbacks — handle incoming commands from host (optional)
class RxCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) override {
        String value = pCharacteristic->getValue().c_str();
        value.trim();
        if (value.length() > 0) {
            // Echo received command to Serial for debugging
            Serial.print("#BLE_RX,");
            Serial.println(value);
            // Future: handle commands like "RECALIBRATE", "LED_OFF", etc.
        }
    }
};

/**
 * Send a line over BLE UART (notify) if a client is connected.
 * The line is sent as-is; the Python client splits on newlines.
 */
static void bleSendLine(const char *line) {
    if (!bleConnected || pTxCharacteristic == nullptr) return;
    pTxCharacteristic->setValue((uint8_t *)line, strlen(line));
    pTxCharacteristic->notify();
}

// ── I2C pins ──────────────────────────────────────────────────────────────────
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

// Serial stream settings
const uint32_t SERIAL_BAUD      = 115200;
const uint32_t FRAME_PERIOD_MS  = 50;       // 20 Hz target
const uint32_t STATUS_PERIOD_MS = 1000;
const uint8_t  LED_BRIGHTNESS   = 8;
const bool     STREAM_TOF2      = false;

// ── Sensor objects ─────────────────────────────────────────────────────────────
DFRobot_MatrixLidar_I2C tof1(TOF1_ADDR);
DFRobot_MatrixLidar_I2C tof2(TOF2_ADDR);

APDS9930 apds;
CRGB leds[NUM_LEDS];

uint16_t frame1[64];
uint16_t frame2[64];

bool tof1OK  = false;
bool tof2OK  = false;
bool apdsOK  = false;

uint32_t lastPrintMs  = 0;
uint32_t lastStatusMs = 0;
uint32_t frameSeq     = 0;

// ── Helpers ───────────────────────────────────────────────────────────────────

void setRingColor(CRGB color) {
    for (int i = 0; i < NUM_LEDS; i++) leds[i] = color;
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

/**
 * Print a line to both Serial and BLE (if connected).
 * Using a shared buffer avoids building the string twice.
 */
static void printLine(const char *line) {
    Serial.println(line);
    bleSendLine(line);
}

// ── Frame output helpers ───────────────────────────────────────────────────────

void printProximity(uint16_t proximity, bool handPresent) {
    char buf[32];
    snprintf(buf, sizeof(buf), "#PROX,%u,%d", proximity, handPresent ? 1 : 0);
    printLine(buf);
}

void printFrameCSV(const char *prefix, uint32_t seq, uint32_t deviceMs, uint16_t frame[64]) {
    // Build the full CSV line into a stack buffer.
    // Worst case: "FRAME2," + 10 digits + "," + 10 digits + 64*(5+1) chars ≈ 430 bytes
    char buf[512];
    int  pos = snprintf(buf, sizeof(buf), "%s,%lu,%lu", prefix, (unsigned long)seq, (unsigned long)deviceMs);
    for (int i = 0; i < 64 && pos < (int)sizeof(buf) - 8; i++) {
        pos += snprintf(buf + pos, sizeof(buf) - pos, ",%u", frame[i]);
    }
    printLine(buf);
}

void printStatusThrottled(const char *message) {
    uint32_t now = millis();
    if (now - lastStatusMs < STATUS_PERIOD_MS) return;
    lastStatusMs = now;
    printLine(message);
}

// ── Sensor setup ──────────────────────────────────────────────────────────────

bool setupToF(DFRobot_MatrixLidar_I2C &tof, const char *name) {
    Serial.println(name);
    if (tof.begin() != 0) {
        Serial.print(name); Serial.println(" begin error.");
        return false;
    }
    if (tof.setRangingMode(eMatrix_8X8) != 0) {
        Serial.print(name); Serial.println(" failed to set 8x8 mode.");
        return false;
    }
    Serial.print(name); Serial.println(" connected.");
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

// ── BLE setup ─────────────────────────────────────────────────────────────────

void setupBLE() {
    BLEDevice::init(BLE_DEVICE_NAME);

    BLEServer *pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());

    BLEService *pService = pServer->createService(NUS_SERVICE_UUID);

    // TX characteristic: ESP32 → host, supports notify
    pTxCharacteristic = pService->createCharacteristic(
        NUS_CHAR_TX_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    pTxCharacteristic->addDescriptor(new BLE2902());

    // RX characteristic: host → ESP32, supports write
    BLECharacteristic *pRxCharacteristic = pService->createCharacteristic(
        NUS_CHAR_RX_UUID,
        BLECharacteristic::PROPERTY_WRITE
    );
    pRxCharacteristic->setCallbacks(new RxCallbacks());

    pService->start();

    BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(NUS_SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    // Helps iOS/macOS find the device faster
    pAdvertising->setMinPreferred(0x06);
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    Serial.println("BLE UART advertising as \"" BLE_DEVICE_NAME "\"");
}

// ── Arduino setup / loop ──────────────────────────────────────────────────────

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1500);

    FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(LED_BRIGHTNESS);
    setRingColor(CRGB::Red);

    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(100000);
    Wire.setTimeOut(50);

    Serial.println("Starting APDS-9930 + ToF + LED ring + BLE...");

    scanI2C();

    apdsOK = setupAPDS9930();
    tof1OK = setupToF(tof1, "ToF #1");
    tof2OK = setupToF(tof2, "ToF #2");

    setupBLE();

    Serial.println("\nSetup complete.");
    Serial.print("APDS: ");  Serial.println(apdsOK ? "OK" : "FAILED");
    Serial.print("ToF #1: "); Serial.println(tof1OK ? "OK" : "FAILED");
    Serial.print("ToF #2: "); Serial.println(tof2OK ? "OK" : "FAILED");
    Serial.println();
}

void loop() {
    if (millis() - lastPrintMs < FRAME_PERIOD_MS) return;
    lastPrintMs = millis();

    bool     handPresent = false;
    uint16_t proximity   = 0;

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