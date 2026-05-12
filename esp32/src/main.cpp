#include <Arduino.h>
#include <Wire.h>
#include "DFRobot_MatrixLidar.h"

// SEN0628 / DFRobot Matrix LiDAR
// Address 0x30 means A1 = 0 and A0 = 0, with the I2C switch enabled.
DFRobot_MatrixLidar_I2C tof(0x30);

uint16_t frame[64];

// Binary frame format:
//   "MLD1" magic, uint32 seq, uint32 millis, uint32 read_us, 64 little-endian uint16 distances, uint16 checksum
// The checksum is the 16-bit sum of all bytes before the checksum field.
const uint8_t FRAME_MAGIC[4] = {'M', 'L', 'D', '1'};
const uint32_t SERIAL_BAUD = 921600;

// 50 FPS target. If readings become unstable, raise this to 33 or 50 ms.
const uint32_t FRAME_PERIOD_MS = 20;
uint32_t nextFrameMs = 0;
uint32_t seq = 0;

void putU16LE(uint8_t* buf, size_t &pos, uint16_t value) {
  buf[pos++] = value & 0xff;
  buf[pos++] = (value >> 8) & 0xff;
}

void putU32LE(uint8_t* buf, size_t &pos, uint32_t value) {
  buf[pos++] = value & 0xff;
  buf[pos++] = (value >> 8) & 0xff;
  buf[pos++] = (value >> 16) & 0xff;
  buf[pos++] = (value >> 24) & 0xff;
}

void writeBinaryFrame(uint32_t frameSeq, uint32_t frameMs, uint32_t readUs) {
  uint8_t packet[4 + 4 + 4 + 4 + 64 * 2 + 2];
  size_t pos = 0;

  for (uint8_t b : FRAME_MAGIC) {
    packet[pos++] = b;
  }
  putU32LE(packet, pos, frameSeq);
  putU32LE(packet, pos, frameMs);
  putU32LE(packet, pos, readUs);
  for (uint8_t i = 0; i < 64; i++) {
    putU16LE(packet, pos, frame[i]);
  }

  uint16_t checksum = 0;
  for (size_t i = 0; i < pos; i++) {
    checksum += packet[i];
  }
  putU16LE(packet, pos, checksum);

  Serial.write(packet, pos);
}

void setup() {
  // This is the ESP32-to-computer USB serial rate, not the LiDAR's I2C rate.
  Serial.begin(SERIAL_BAUD);
  delay(1500);

  Wire.begin();
  // Fast-mode I2C. If your wiring is long/noisy and begin fails, change to 100000 or remove this line.
  Wire.setClock(400000);

  Serial.println("# Matrix LiDAR binary stream starting");
  Serial.println("# Packet format: MLD1,seq,millis,read_us,64xuint16,checksum16");
  Serial.println("# Address: 0x30");

  while (tof.begin() != 0) {
    Serial.println("# LiDAR begin error. Check wiring, 3V/GND, I2C switch, and address 0x30.");
    delay(1000);
  }
  Serial.println("# LiDAR connected");

  while (tof.setRangingMode(eMatrix_8X8) != 0) {
    Serial.println("# Failed to set 8x8 mode");
    delay(1000);
  }
  Serial.println("# 8x8 mode active");
}

void loop() {
  uint32_t now = millis();
  if ((int32_t)(now - nextFrameMs) < 0) {
    return;
  }
  nextFrameMs = now + FRAME_PERIOD_MS;

  uint32_t readStartUs = micros();
  uint8_t err = tof.getAllData(frame);
  uint32_t readUs = micros() - readStartUs;
  uint32_t frameSeq = seq++;
  if (err != 0) {
    Serial.println("# getAllData error");
    return;
  }

  writeBinaryFrame(frameSeq, now, readUs);
}