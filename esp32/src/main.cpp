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

#define NUS_SERVICE_UUID  "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_CHAR_RX_UUID  "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_CHAR_TX_UUID  "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

static BLECharacteristic *pTxCharacteristic = nullptr;
static bool bleConnected = false;

static void handleHostCommand(String command);
void printStatusThrottled(const char *message);

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer *pServer) override {
        bleConnected = true;
        Serial.println("#BLE,connected");
    }
    void onDisconnect(BLEServer *pServer) override {
        bleConnected = false;
        Serial.println("#BLE,disconnected");
        pServer->getAdvertising()->start();
    }
};

class RxCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) override {
        String value = pCharacteristic->getValue().c_str();
        value.trim();
        if (value.length() > 0) {
            Serial.print("#BLE_RX,");
            Serial.println(value);
            handleHostCommand(value);
        }
    }
};

static void bleSendLine(const char *line) {
    if (!bleConnected || pTxCharacteristic == nullptr) return;
    pTxCharacteristic->setValue((uint8_t *)line, strlen(line));
    pTxCharacteristic->notify();
}

// ── I2C pins ──────────────────────────────────────────────────────────────────
#define SDA_PIN 21
#define SCL_PIN 22

// TCA9548A/PCA9548A I2C mux. Leave A0/A1/A2 tied to GND for address 0x70.
#define MUX_ADDR 0x70
#define MUX_RESET_PIN 19   // Connect mux RST here, or tie RST to 3V3 and leave this unused.

// DFRobot VL53L7CX / Matrix LiDAR addresses seen on mux channels.
// Your serial scan showed:
//   mux ch0 -> 0x30
//   mux ch1 -> 0x32
//   mux ch2 -> 0x33
#define NUM_TOF 3
const uint8_t TOF_CHANNELS[NUM_TOF] = {0, 1, 2};
const uint8_t TOF_ADDRS[NUM_TOF]    = {0x30, 0x32, 0x33};

// LED ring
#define LED_PIN 18
#define NUM_LEDS 12

// APDS-9930 hand detection threshold. Raise/lower if your APDS module reads differently.
#define HAND_THRESHOLD 200

const uint32_t SERIAL_BAUD      = 115200;
const uint32_t FRAME_PERIOD_MS  = 50;
const uint32_t STATUS_PERIOD_MS = 1000;

// Gesture capture window. ToF is the primary trigger; APDS remains a secondary
// hint. ToF frames are buffered before trigger so fast gesture onsets are kept.
const uint32_t POST_HAND_CAPTURE_MS = 650;
const uint32_t MIN_CAPTURE_MS       = 180;
const uint32_t MAX_CAPTURE_MS       = 1800;
const uint8_t  LED_BRIGHTNESS   = 8;

const uint16_t TOF_TRIGGER_MAX_MM       = 1650;
const uint16_t TOF_MOTION_DELTA_MM      = 85;
const uint8_t  TOF_TRIGGER_MIN_CELLS    = 2;
const uint8_t  TOF_MOTION_MIN_CELLS     = 2;
const uint8_t  PRETRIGGER_FRAME_COUNT   = 8;

const uint8_t  LED_UP_NUMBER    = 1;
const bool     LED_CLOCKWISE_IS_RIGHT = true;

String serialCommandBuffer;

DFRobot_MatrixLidar_I2C tof1(TOF_ADDRS[0]);
DFRobot_MatrixLidar_I2C tof2(TOF_ADDRS[1]);
DFRobot_MatrixLidar_I2C tof3(TOF_ADDRS[2]);
DFRobot_MatrixLidar_I2C *tofs[NUM_TOF] = {&tof1, &tof2, &tof3};

APDS9930 apds;
CRGB leds[NUM_LEDS];

uint16_t frames[NUM_TOF][64];
bool tofOK[NUM_TOF] = {false, false, false};
bool currentTofOK[NUM_TOF] = {false, false, false};
bool apdsOK = false;

uint16_t pretriggerFrames[PRETRIGGER_FRAME_COUNT][NUM_TOF][64];
bool pretriggerOK[PRETRIGGER_FRAME_COUNT][NUM_TOF];
uint32_t pretriggerSeq[PRETRIGGER_FRAME_COUNT];
uint32_t pretriggerMs[PRETRIGGER_FRAME_COUNT];
uint8_t pretriggerHead = 0;
uint8_t pretriggerCount = 0;

uint32_t lastPrintMs  = 0;
uint32_t lastStatusMs = 0;
uint32_t frameSeq     = 0;

bool captureActive = false;
bool captureSawHand = false;
uint32_t captureId = 0;
uint32_t captureStartMs = 0;
uint32_t lastHandMs = 0;
uint32_t lastActivityMs = 0;

uint16_t prevFrames[NUM_TOF][64];
bool havePrevFrame[NUM_TOF] = {false, false, false};

// ── Helpers ───────────────────────────────────────────────────────────────────

bool muxSelect(uint8_t channel) {
    if (channel > 7) return false;
    Wire.beginTransmission(MUX_ADDR);
    Wire.write(1 << channel);
    return Wire.endTransmission() == 0;
}

void muxDisableAll() {
    Wire.beginTransmission(MUX_ADDR);
    Wire.write(0x00);
    Wire.endTransmission();
}

void resetMux() {
    pinMode(MUX_RESET_PIN, OUTPUT);
    digitalWrite(MUX_RESET_PIN, LOW);
    delay(5);
    digitalWrite(MUX_RESET_PIN, HIGH);
    delay(5);
    muxDisableAll();
}

void setRingColor(CRGB color) {
    for (int i = 0; i < NUM_LEDS; i++) leds[i] = color;
    FastLED.show();
}

void clearRing() {
    setRingColor(CRGB::Black);
}

uint8_t ledUpIndex() {
    uint8_t n = LED_UP_NUMBER;
    if (n < 1) n = 1;
    if (n > NUM_LEDS) n = NUM_LEDS;
    return n - 1;
}

uint8_t wrapLedIndex(int idx) {
    while (idx < 0) idx += NUM_LEDS;
    while (idx >= NUM_LEDS) idx -= NUM_LEDS;
    return (uint8_t)idx;
}

uint8_t compassLedIndex(const String &direction) {
    int up = ledUpIndex();
    int step = LED_CLOCKWISE_IS_RIGHT ? 1 : -1;

    if (direction == "swipe_up")    return wrapLedIndex(up);
    if (direction == "swipe_right") return wrapLedIndex(up + step * 3);
    if (direction == "swipe_down")  return wrapLedIndex(up + step * 6);
    if (direction == "swipe_left")  return wrapLedIndex(up - step * 3);
    return wrapLedIndex(up);
}

void showSwipeAnimation(const String &gesture) {
    int center = compassLedIndex(gesture);
    clearRing();
    leds[center] = CRGB::Green;
    FastLED.show();
    delay(55);

    for (int radius = 1; radius <= NUM_LEDS / 2; radius++) {
        leds[wrapLedIndex(center - radius)] = CRGB::Green;
        leds[wrapLedIndex(center + radius)] = CRGB::Green;
        FastLED.show();
        delay(55);
    }
}

void handleHostCommand(String command) {
    command.trim();
    if (command.length() == 0) return;

    if (command.startsWith("LED_GESTURE,")) {
        String gesture = command.substring(String("LED_GESTURE,").length());
        gesture.trim();

        if (gesture == "swipe_left" || gesture == "swipe_right" ||
            gesture == "swipe_up"   || gesture == "swipe_down") {
            showSwipeAnimation(gesture);
        }
        return;
    }
}

void readSerialCommands() {
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            handleHostCommand(serialCommandBuffer);
            serialCommandBuffer = "";
        } else if (serialCommandBuffer.length() < 96) {
            serialCommandBuffer += c;
        } else {
            serialCommandBuffer = "";
        }
    }
}

void scanMainI2C() {
    Serial.println("\nScanning main I2C bus...");
    for (uint8_t addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);
        uint8_t error = Wire.endTransmission();
        if (error == 0) {
            Serial.print("Found I2C device at 0x");
            if (addr < 16) Serial.print("0");
            Serial.println(addr, HEX);
        }
    }
    Serial.println("Main I2C scan done.\n");
}

void scanMuxChannels() {
    for (uint8_t ch = 0; ch < NUM_TOF; ch++) {
        Serial.print("Scanning mux channel ");
        Serial.print(TOF_CHANNELS[ch]);
        Serial.println("...");
        if (!muxSelect(TOF_CHANNELS[ch])) {
            Serial.println("#ERR,MUX,select_failed");
            continue;
        }
        for (uint8_t addr = 1; addr < 127; addr++) {
            if (addr == MUX_ADDR) continue;
            Wire.beginTransmission(addr);
            uint8_t error = Wire.endTransmission();
            if (error == 0) {
                Serial.print("  Found channel device at 0x");
                if (addr < 16) Serial.print("0");
                Serial.println(addr, HEX);
            }
        }
    }
    muxDisableAll();
}

static void printLine(const char *line) {
    Serial.println(line);
    bleSendLine(line);
}

void printProximity(uint16_t proximity, bool handPresent) {
    char buf[32];
    snprintf(buf, sizeof(buf), "#PROX,%u,%d", proximity, handPresent ? 1 : 0);
    printLine(buf);
}

void printFrameCSV(const char *prefix, uint32_t seq, uint32_t deviceMs, uint16_t frame[64]) {
    char buf[512];
    int pos = snprintf(buf, sizeof(buf), "%s,%lu,%lu", prefix, (unsigned long)seq, (unsigned long)deviceMs);
    for (int i = 0; i < 64 && pos < (int)sizeof(buf) - 8; i++) {
        pos += snprintf(buf + pos, sizeof(buf) - pos, ",%u", frame[i]);
    }
    printLine(buf);
}

void appendPretriggerFrame(uint32_t seq, uint32_t nowMs) {
    uint8_t slot = pretriggerHead;
    pretriggerSeq[slot] = seq;
    pretriggerMs[slot] = nowMs;
    for (uint8_t sensor = 0; sensor < NUM_TOF; sensor++) {
        pretriggerOK[slot][sensor] = currentTofOK[sensor];
        if (currentTofOK[sensor]) {
            memcpy(pretriggerFrames[slot][sensor], frames[sensor], sizeof(frames[sensor]));
        }
    }
    pretriggerHead = (pretriggerHead + 1) % PRETRIGGER_FRAME_COUNT;
    if (pretriggerCount < PRETRIGGER_FRAME_COUNT) pretriggerCount++;
}

void printStoredFrame(uint8_t slot) {
    for (uint8_t sensor = 0; sensor < NUM_TOF; sensor++) {
        if (!pretriggerOK[slot][sensor]) continue;
        char prefix[8];
        snprintf(prefix, sizeof(prefix), "FRAME%u", sensor + 1);
        printFrameCSV(prefix, pretriggerSeq[slot], pretriggerMs[slot], pretriggerFrames[slot][sensor]);
    }
}

void flushPretriggerFrames() {
    if (pretriggerCount == 0) return;
    uint8_t start = (pretriggerHead + PRETRIGGER_FRAME_COUNT - pretriggerCount) % PRETRIGGER_FRAME_COUNT;
    for (uint8_t i = 0; i < pretriggerCount; i++) {
        uint8_t slot = (start + i) % PRETRIGGER_FRAME_COUNT;
        printStoredFrame(slot);
    }
}

void printCurrentFrames(uint32_t seq, uint32_t nowMs) {
    for (uint8_t sensor = 0; sensor < NUM_TOF; sensor++) {
        if (!currentTofOK[sensor]) continue;
        char prefix[8];
        snprintf(prefix, sizeof(prefix), "FRAME%u", sensor + 1);
        printFrameCSV(prefix, seq, nowMs, frames[sensor]);
    }
}

void readAllToFSensors() {
    for (uint8_t i = 0; i < NUM_TOF; i++) {
        currentTofOK[i] = false;
        char errMsg[32];
        if (!tofOK[i]) {
            snprintf(errMsg, sizeof(errMsg), "#ERR,TOF%u,init_failed", i + 1);
            printStatusThrottled(errMsg);
            continue;
        }
        if (!muxSelect(TOF_CHANNELS[i])) {
            snprintf(errMsg, sizeof(errMsg), "#ERR,TOF%u,mux_select_failed", i + 1);
            printStatusThrottled(errMsg);
            continue;
        }
        uint8_t err = tofs[i]->getAllData(frames[i]);
        if (err == 0) {
            currentTofOK[i] = true;
        } else {
            snprintf(errMsg, sizeof(errMsg), "#ERR,TOF%u,read_failed", i + 1);
            printStatusThrottled(errMsg);
        }
    }
    muxDisableAll();
}

bool detectToFActivity() {
    uint8_t activeCells = 0;
    uint8_t movingCells = 0;
    bool anyCurrentFrame = false;
    bool anyPreviousFrame = false;

    for (uint8_t sensor = 0; sensor < NUM_TOF; sensor++) {
        if (!currentTofOK[sensor]) continue;
        anyCurrentFrame = true;
        anyPreviousFrame = anyPreviousFrame || havePrevFrame[sensor];

        for (int i = 0; i < 64; i++) {
            uint16_t curr = frames[sensor][i];
            bool currActive = curr > 0 && curr <= TOF_TRIGGER_MAX_MM;
            if (currActive) activeCells++;

            if (havePrevFrame[sensor]) {
                uint16_t prev = prevFrames[sensor][i];
                bool prevActive = prev > 0 && prev <= TOF_TRIGGER_MAX_MM;
                uint16_t delta = curr > prev ? curr - prev : prev - curr;
                if ((currActive || prevActive) && delta >= TOF_MOTION_DELTA_MM) movingCells++;
            }
        }

        memcpy(prevFrames[sensor], frames[sensor], sizeof(prevFrames[sensor]));
        havePrevFrame[sensor] = true;
    }

    if (!anyCurrentFrame) return false;

    bool activeEnough = activeCells >= TOF_TRIGGER_MIN_CELLS;
    bool movingEnough = movingCells >= TOF_MOTION_MIN_CELLS;
    return activeEnough && (!anyPreviousFrame || movingEnough || captureActive);
}

void printCaptureState(const char *state, uint32_t id, uint32_t nowMs, const char *reason) {
    char buf[80];
    snprintf(buf, sizeof(buf), "#CAPTURE,%s,%lu,%lu,%s", state, (unsigned long)id, (unsigned long)nowMs, reason);
    printLine(buf);
}

bool updateCaptureWindow(bool handPresent, bool tofActivity, uint32_t nowMs) {
    bool activity = handPresent || tofActivity;
    if (activity) {
        lastActivityMs = nowMs;
        if (handPresent) {
            lastHandMs = nowMs;
        }
        if (!captureActive) {
            captureActive = true;
            captureSawHand = handPresent;
            captureStartMs = nowMs;
            captureId++;
            printCaptureState("START", captureId, nowMs, handPresent ? "apds_on" : "tof_motion");
            return true;
        } else {
            captureSawHand = captureSawHand || handPresent;
        }
        return false;
    }

    if (!captureActive) return false;

    uint32_t sinceStart = nowMs - captureStartMs;
    uint32_t sinceActivity = nowMs - lastActivityMs;

    // Keep streaming after ToF/APDS activity releases. This is important for fast
    // swipes: the last ToF frames often carry the exit direction even after
    // proximity/motion already fell below threshold.
    bool minWindowDone = sinceStart >= MIN_CAPTURE_MS;
    bool tailDone = sinceActivity >= POST_HAND_CAPTURE_MS;
    bool tooLong = sinceStart >= MAX_CAPTURE_MS;

    if ((minWindowDone && tailDone) || tooLong) {
        const char *reason = tooLong ? "max_window" : "post_activity_tail_done";
        printCaptureState("END", captureId, nowMs, reason);
        captureActive = false;
        captureSawHand = false;
    }
    return false;
}

void printStatusThrottled(const char *message) {
    uint32_t now = millis();
    if (now - lastStatusMs < STATUS_PERIOD_MS) return;
    lastStatusMs = now;
    printLine(message);
}

bool setupToF(uint8_t index) {
    char name[16];
    snprintf(name, sizeof(name), "ToF #%u", index + 1);
    Serial.print(name);
    Serial.print(" on mux channel ");
    Serial.print(TOF_CHANNELS[index]);
    Serial.print(" at address 0x");
    Serial.println(TOF_ADDRS[index], HEX);

    if (!muxSelect(TOF_CHANNELS[index])) {
        Serial.print(name); Serial.println(" mux select error.");
        return false;
    }
    if (tofs[index]->begin() != 0) {
        Serial.print(name); Serial.println(" begin error.");
        return false;
    }
    if (tofs[index]->setRangingMode(eMatrix_8X8) != 0) {
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

void setupBLE() {
    BLEDevice::init(BLE_DEVICE_NAME);

    BLEServer *pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());

    BLEService *pService = pServer->createService(NUS_SERVICE_UUID);

    pTxCharacteristic = pService->createCharacteristic(
        NUS_CHAR_TX_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    pTxCharacteristic->addDescriptor(new BLE2902());

    BLECharacteristic *pRxCharacteristic = pService->createCharacteristic(
        NUS_CHAR_RX_UUID,
        BLECharacteristic::PROPERTY_WRITE
    );
    pRxCharacteristic->setCallbacks(new RxCallbacks());

    pService->start();

    BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(NUS_SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    pAdvertising->setMinPreferred(0x06);
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    Serial.println("BLE UART advertising as \"" BLE_DEVICE_NAME "\"");
}

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1500);

    FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(LED_BRIGHTNESS);
    setRingColor(CRGB::Red);

    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(100000);
    Wire.setTimeOut(50);
    resetMux();

    Serial.println("Starting APDS-9930 + 3x ToF through TCA9548A + LED ring + BLE...");

    scanMainI2C();
    scanMuxChannels();

    apdsOK = setupAPDS9930();
    for (uint8_t i = 0; i < NUM_TOF; i++) {
        tofOK[i] = setupToF(i);
    }
    muxDisableAll();

    setupBLE();

    Serial.println("\nSetup complete.");
    Serial.print("APDS: "); Serial.println(apdsOK ? "OK" : "FAILED");
    for (uint8_t i = 0; i < NUM_TOF; i++) {
        Serial.print("ToF #"); Serial.print(i + 1); Serial.print(": ");
        Serial.println(tofOK[i] ? "OK" : "FAILED");
    }
    Serial.println();
}

void loop() {
    readSerialCommands();
    if (millis() - lastPrintMs < FRAME_PERIOD_MS) return;
    lastPrintMs = millis();

    bool handPresent = false;
    uint16_t proximity = 0;
    bool sensorError = false;

    if (apdsOK) {
        if (apds.readProximity(proximity)) {
            handPresent = proximity > HAND_THRESHOLD;
        } else {
            printStatusThrottled("#ERR,APDS,read_failed");
            sensorError = true;
        }
    } else {
        printStatusThrottled("#ERR,APDS,init_failed");
        sensorError = true;
    }

    // ToF is read every frame so it can trigger capture and fill the pre-trigger
    // history. Serial/BLE output is still emitted only during active capture.
    readAllToFSensors();
    bool tofActivity = detectToFActivity();
    appendPretriggerFrame(frameSeq, lastPrintMs);

    if (sensorError) {
        setRingColor(CRGB::Blue);
    } else {
        setRingColor((handPresent || tofActivity || captureActive) ? CRGB::Green : CRGB::Red);
    }

    printProximity(proximity, handPresent);
    bool captureStarted = updateCaptureWindow(handPresent, tofActivity, lastPrintMs);

    if (captureStarted) {
        flushPretriggerFrames();
    } else if (captureActive) {
        printCurrentFrames(frameSeq, lastPrintMs);
    }

    frameSeq++;
}
