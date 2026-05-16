"""
engine/bluetooth_spp.py
BLE UART connection for GesturePuck.

Provides:
  list_devices() -> list[(address, name)]
  connect(address, on_event) -> BLEConnection

The ESP32 firmware advertises a Nordic UART Service (NUS):
  Service  6e400001-b5a3-f393-e0a9-e50e24dcca9e
  TX char  6e400003-b5a3-f393-e0a9-e50e24dcca9e  (notify, ESP32 → host)
  RX char  6e400002-b5a3-f393-e0a9-e50e24dcca9e  (write,  host  → ESP32)
"""

import asyncio
import threading

try:
    from bleak import BleakClient, BleakScanner
    _BLEAK_AVAILABLE = True
except ImportError:
    _BLEAK_AVAILABLE = False

# Nordic UART Service UUIDs
SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_TX      = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # ESP32 → host (notify)
CHAR_RX      = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # host → ESP32 (write)

DEVICE_NAME  = "GesturePuck"
SCAN_TIMEOUT = 5.0   # seconds


# ── Async helpers ──────────────────────────────────────────────────────────────

async def _scan() -> list[tuple[str, str]]:
    """Discover nearby BLE devices. Returns [(address, name), ...]."""
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
    return [
        (d.address, d.name or d.address)
        for d in devices
        if d.name   # skip unnamed/anonymous devices
    ]


# ── Public API ─────────────────────────────────────────────────────────────────

def list_devices() -> list[tuple[str, str]]:
    """
    Synchronous BLE scan (blocks for ~5 s).
    Returns a list of (address, name) tuples.
    GesturePuck devices are sorted to the top.
    Returns [] if bleak is not installed or on any error.
    """
    if not _BLEAK_AVAILABLE:
        return []
    try:
        loop = asyncio.new_event_loop()
        devices = loop.run_until_complete(_scan())
        loop.close()
        pucks  = [(a, n) for a, n in devices if DEVICE_NAME in n]
        others = [(a, n) for a, n in devices if DEVICE_NAME not in n]
        return pucks + others
    except Exception as exc:
        print(f"[bluetooth_spp] scan error: {exc}")
        return []


class BLEConnection:
    """
    Manages a persistent BLE UART connection to a GesturePuck device.

    Incoming text lines (newline-delimited) are decoded and forwarded to
    on_event(line: str) from the background asyncio loop.

    Usage:
        conn = BLEConnection("AA:BB:CC:DD:EE:FF", my_callback)
        # … later …
        conn.close()
    """

    def __init__(self, address: str, on_event, on_status=None):
        if not _BLEAK_AVAILABLE:
            raise RuntimeError(
                "bleak is not installed. Run: pip install bleak"
            )
        self.address  = address
        self.on_event = on_event
        self.on_status = on_status
        self._client  = None
        self._loop    = asyncio.new_event_loop()
        self._stop_event = asyncio.Event()
        self._rx_packets = 0
        self._rx_lines = 0
        self._rx_buffer = ""
        self._thread  = threading.Thread(target=self._run, daemon=True, name="BLEConnection")
        self._thread.start()

    def _emit_status(self, text: str):
        if self.on_status is not None:
            try:
                self.on_status(text)
            except Exception as exc:
                print(f"[bluetooth_spp] on_status error: {exc}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self):
        """Entry point for the background thread — runs the asyncio event loop."""
        self._loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self):
        """Connect, subscribe to notifications, and keep the connection alive."""
        try:
            self._emit_status("BLE CONNECTING…")
            async with BleakClient(
                self.address,
                disconnected_callback=self._on_disconnect,
            ) as client:
                self._client = client
                self._emit_status("BLE CONNECTED")
                await client.start_notify(CHAR_TX, self._handle_notify)
                self._emit_status("BLE WAITING FOR FRAMES")
                # Block until close() sets the stop event
                await self._stop_event.wait()
                await client.stop_notify(CHAR_TX)
        except Exception as exc:
            print(f"[bluetooth_spp] connection error: {exc}")
            self._emit_status(f"BLE ERROR {type(exc).__name__}: {exc}")
        finally:
            self._client = None

    def _on_disconnect(self, client):
        print(f"[bluetooth_spp] disconnected from {self.address}")
        self._emit_status("BLE DISCONNECTED")

    def _emit_line(self, line: str):
        """Forward one complete decoded BLE text record to the app."""
        line = line.strip()
        if not line:
            return
        self._rx_lines += 1
        if self._rx_lines == 1:
            self._emit_status("BLE RECEIVING DATA")
        try:
            self.on_event(line)
        except Exception as exc:
            print(f"[bluetooth_spp] on_event error: {exc}")

    @staticmethod
    def _looks_like_complete_frame(line: str) -> bool:
        """Return True when a FRAME/FRAME1/FRAME2 CSV has seq, ms, and 64 values."""
        if not line.startswith(("FRAME,", "FRAME1,", "FRAME2,")):
            return False
        parts = line.split(",")
        # FRAME + seq + ms + 64 distance cells = 67 fields.
        # Some firmware may append an extra empty field, so require at least 67.
        return len(parts) >= 67

    @staticmethod
    def _record_starts(buf: str) -> list[int]:
        """Find possible starts of known BLE text records inside a raw stream."""
        tokens = ("FRAME1,", "FRAME2,", "FRAME,", "#PROX,", "GESTURE,")
        starts = []
        for token in tokens:
            start = 0
            while True:
                idx = buf.find(token, start)
                if idx < 0:
                    break
                starts.append(idx)
                start = idx + 1
        return sorted(set(starts))

    def _drain_rx_buffer(self):
        """Extract records from the BLE byte stream.

        BLE UART does not guarantee that one notify callback equals one text line.
        In practice, long FRAME CSV records may arrive split across several
        notifications, and some firmware builds may stream records without
        newlines. This parser accepts both newline-delimited records and
        back-to-back records beginning with FRAME/#PROX/GESTURE.
        """
        # Normalize line endings first, but do not require them.
        self._rx_buffer = self._rx_buffer.replace("\r", "\n")

        while self._rx_buffer:
            buf = self._rx_buffer

            # Drop junk before the first recognizable record token.
            starts = self._record_starts(buf)
            if not starts:
                # Keep a small suffix in case a token was split across packets.
                if len(buf) > 32:
                    self._rx_buffer = buf[-32:]
                return
            if starts[0] > 0:
                self._rx_buffer = buf[starts[0]:]
                buf = self._rx_buffer

            # First prefer newline records when available.
            newline = buf.find("\n")
            if newline >= 0:
                line = buf[:newline].strip()
                self._rx_buffer = buf[newline + 1:]
                if line:
                    self._emit_line(line)
                continue

            # No newline yet. If another record token starts later, the text
            # before that token is one record. This handles streams like
            # FRAME,...FRAME,... with no newline separator.
            starts = self._record_starts(buf)
            if len(starts) >= 2:
                line = buf[:starts[1]].strip()
                self._rx_buffer = buf[starts[1]:]
                if line:
                    self._emit_line(line)
                continue

            # Only one unterminated record is in the buffer. For FRAME records,
            # we can emit once seq+ms+64 cells are present, even without newline.
            if self._looks_like_complete_frame(buf):
                parts = buf.split(",")
                line = ",".join(parts[:67])
                self._rx_buffer = ",".join(parts[67:]).strip()
                self._emit_line(line)
                continue

            # #PROX and GESTURE are short, but without newline or a following
            # record token we cannot know for sure they are complete. Wait for
            # more bytes.
            return

    def _handle_notify(self, sender, data: bytearray):
        """Called by bleak for every BLE notification packet."""
        self._rx_packets += 1
        text = data.decode("utf-8", errors="ignore")
        if not text:
            return
        self._rx_buffer += text

        # v4: do NOT repeatedly reset the buffer. If the ESP32 streams without
        # newlines, resetting every ~4096 chars prevents frames from ever
        # reaching the classifier. Instead, drain what we can and retain only a
        # bounded suffix if no recognizable record start exists.
        self._drain_rx_buffer()
        if len(self._rx_buffer) > 8192:
            starts = self._record_starts(self._rx_buffer)
            if starts:
                self._rx_buffer = self._rx_buffer[starts[-1]:]
                self._emit_status("BLE BUFFER TRIMMED")
            else:
                self._rx_buffer = self._rx_buffer[-32:]
                self._emit_status("BLE BUFFER TRIMMED")

    # ── Public ────────────────────────────────────────────────────────────────

    def send(self, text: str):
        """
        Send a text command to the ESP32 (optional — not required for gesture use).
        No-op if not connected.
        """
        if self._client is None or not self._loop.is_running():
            return
        data = (text + "\n").encode("utf-8")
        asyncio.run_coroutine_threadsafe(
            self._client.write_gatt_char(CHAR_RX, data, response=False),
            self._loop,
        )

    def close(self):
        """Disconnect gracefully."""
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        # Give the background thread a moment to clean up
        self._thread.join(timeout=2.0)


def connect(address: str, on_event, on_status=None) -> "BLEConnection":
    """
    Open a BLE UART connection to the device at *address*.
    *on_event* is called with each decoded text line from the ESP32.
    *on_status* is optional and is called with connection/status updates.
    Returns a BLEConnection that can be closed with .close().
    """
    return BLEConnection(address, on_event, on_status=on_status)



