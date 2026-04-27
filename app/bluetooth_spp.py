"""
BLE UART connection using bleak (cross-platform, no pairing needed).
"""

import asyncio
import threading
from bleak import BleakClient, BleakScanner

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_TX      = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME  = "GesturePuck"


async def scan() -> list[tuple[str, str]]:
    """Returns list of (address, name) for discovered BLE devices."""
    devices = await BleakScanner.discover(timeout=5.0)
    return [(d.address, d.name or "Unknown") for d in devices if d.name]


def list_ports() -> list[str]:
    """
    Synchronous scan — returns addresses of nearby GesturePuck devices.
    Falls back to all named BLE devices if none match.
    """
    try:
        loop = asyncio.new_event_loop()
        devices = loop.run_until_complete(scan())
        loop.close()
        pucks = [addr for addr, name in devices if DEVICE_NAME in name]
        return pucks if pucks else [addr for addr, name in devices]
    except Exception:
        return []


class BLEConnection:
    def __init__(self, address: str, on_event):
        """
        address  — BLE MAC / UUID
        on_event — callback(str) called on each received line
        """
        self.address  = address
        self.on_event = on_event
        self._buf     = ""
        self._client  = None
        self._loop    = asyncio.new_event_loop()
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self._loop.run_until_complete(self._connect())

    async def _connect(self):
        async with BleakClient(self.address) as client:
            self._client = client
            await client.start_notify(CHAR_TX, self._handle_notify)
            # Keep alive until disconnect
            while client.is_connected:
                await asyncio.sleep(0.1)

    def _handle_notify(self, sender, data: bytearray):
        text = data.decode("utf-8", errors="replace").strip().rstrip("\r\n")
        if text:
            self.on_event(text)

    def close(self):
        if self._client and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._client.disconnect(), self._loop)


def connect(address: str, on_event) -> BLEConnection:
    return BLEConnection(address, on_event)