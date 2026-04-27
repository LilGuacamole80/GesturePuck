"""
BLE UART connection
"""

import asyncio
import threading
from bleak import BleakClient, BleakScanner

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME  = "GesturePuck"


async def _scan() -> list[tuple[str, str]]:
    """Returns list of (address, name)."""
    devices = await BleakScanner.discover(timeout=5.0)
    return [(d.address, d.name or d.address) for d in devices if d.name]


def list_devices() -> list[tuple[str, str]]:
    """
    Synchronous scan. Returns (address, name) pairs.
    GesturePuck devices sorted to top.
    """
    try:
        loop = asyncio.new_event_loop()
        devices = loop.run_until_complete(_scan())
        loop.close()
        pucks  = [(a, n) for a, n in devices if DEVICE_NAME in n]
        others = [(a, n) for a, n in devices if DEVICE_NAME not in n]
        return pucks + others
    except Exception:
        return []


class BLEConnection:
    def __init__(self, address: str, on_event):
        self.address  = address
        self.on_event = on_event
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