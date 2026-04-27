#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// Nordic UART Service (NUS) — standard BLE serial protocol
#define SERVICE_UUID        "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_TX   "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // ESP32 → app

BLEServer*         pServer   = nullptr;
BLECharacteristic* pTX       = nullptr;
bool               connected = false;

const int BUTTON_PINS[]     = {13};
const char* BUTTON_EVENTS[] = {"BTN1"};
const int BUTTON_COUNT      = 1;

const unsigned long DEBOUNCE_MS          = 40;

bool stableStates[BUTTON_COUNT];
bool lastReadings[BUTTON_COUNT];
unsigned long lastChangeTimes[BUTTON_COUNT];

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer* s) override {
        connected = true;
        Serial.println("BLE client connected");
    }
    void onDisconnect(BLEServer* s) override {
        connected = false;
        Serial.println("BLE client disconnected — restarting advertising");
        BLEDevice::startAdvertising();   // auto-reconnect
    }
};

void sendEvent(const char* event) {
    Serial.println(event);
    if (connected) {
        pTX->setValue((uint8_t*)event, strlen(event));
        pTX->notify();
    }
}

// ── setup ─────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);

    for (int i = 0; i < BUTTON_COUNT; i++) {
        pinMode(BUTTON_PINS[i], INPUT_PULLUP);
        stableStates[i]    = HIGH;
        lastReadings[i]    = HIGH;
        lastChangeTimes[i] = 0;
    }

    BLEDevice::init("GesturePuck");
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());

    BLEService* pService = pServer->createService(SERVICE_UUID);

    pTX = pService->createCharacteristic(
        CHARACTERISTIC_TX,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    pTX->addDescriptor(new BLE2902());

    pService->start();

    BLEAdvertising* pAdv = BLEDevice::getAdvertising();
    pAdv->addServiceUUID(SERVICE_UUID);
    pAdv->setScanResponse(true);
    BLEDevice::startAdvertising();

    Serial.println("GesturePuck ready — BLE advertising");
}

// ── loop ──────────────────────────────────────────────────────────────────────

void loop() {
    unsigned long now = millis();

    for (int i = 0; i < BUTTON_COUNT; i++) {
        bool reading = digitalRead(BUTTON_PINS[i]);

        if (reading != lastReadings[i]) {
            lastReadings[i]    = reading;
            lastChangeTimes[i] = now;
        }

        if ((now - lastChangeTimes[i]) > DEBOUNCE_MS) {
            if (reading != stableStates[i]) {
                stableStates[i] = reading;
                char event[32];
                snprintf(event, sizeof(event), "%s_%s",
                         BUTTON_EVENTS[i],
                         reading == LOW ? "DOWN" : "UP");
                sendEvent(event);
            }
        }
    }
}