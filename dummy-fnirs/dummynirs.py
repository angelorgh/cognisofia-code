#!/usr/bin/env python3
"""
NIRDuino fNIRS Device Emulator for Raspberry Pi

This emulator mimics the BLE behavior of the NIRDuino fNIRS device,
allowing development and testing of client applications without the
actual hardware.

Uses the 'bless' library for BLE peripheral functionality (compatible with bleak).

Bugs fixed from original version:
1. LED_CHAR_UUID now lowercase to match Android app's .toLowerCase() comparison
2. LED config parsing fixed - Android sends 33 bytes (index 0 = start flag, 1-32 = LED values)
3. Added battery level notifications (4-byte packets)
4. Added proper timing data (durationDataRound) in data frames
5. Fixed chunk distribution across both characteristics to match real device
6. Added realistic fNIRS data generation with proper voltage ranges
7. Replaced bluezero with bless for proper notification support
"""
import asyncio
import logging
import math
import random
import struct
import sys
import time
from typing import Any, Dict, Optional

# Configure logging early so we can see import errors
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logging.info("Starting NIRDuino emulator script...")

try:
    from bless import (
        BlessServer,
        BlessGATTCharacteristic,
        GATTCharacteristicProperties,
        GATTAttributePermissions,
    )
    logging.info("Successfully imported bless library")
except ImportError as e:
    logging.error("Failed to import bless: %s", e)
    logging.error("Install with: pip install bless")
    sys.exit(1)

# Try to import dbus_next for direct D-Bus access
try:
    from dbus_next.constants import PropertyAccess
    from dbus_next.service import ServiceInterface, method, dbus_property, signal
    HAS_DBUS_NEXT = True
    logging.info("dbus_next available for direct D-Bus access")
except ImportError:
    HAS_DBUS_NEXT = False
    logging.warning("dbus_next not available, using standard bless API only")


# ===== BLE UUIDs (from NIRDuino firmware) =====
# All UUIDs must be lowercase to match Android app's .toLowerCase() comparison
FNIRS_SERVICE_UUID = "938548e6-c655-11ea-87d0-0242ac130003"

DATA_CHAR_UUID1 = "77539407-6493-4b89-985f-baaf4c0f8d86"
DATA_CHAR_UUID2 = "513b630c-e5fd-45b5-a678-bb2835d6c1d2"
LED_CHAR_UUID = "19b10001-e8f2-537e-4f6c-d104768a1213"  # lowercase!

# ===== fNIRS packet dimensions (from firmware & Android code) =====
NUM_SOURCES = 33
NUM_DETECTORS = 17
INT_BYTES = 4

PACKET_INTS = NUM_SOURCES * NUM_DETECTORS  # 561
PACKET_BYTES = PACKET_INTS * INT_BYTES  # 2244 bytes

# Sizes of each BLE notification payload (bytes)
# Android expects: 480, 480, 480, 480, 344
CHUNK_SIZES = [480, 480, 480, 480, 344]

# Where each chunk's body starts in the 2244-byte raw array
CHUNK_OFFSETS = [0, 476, 952, 1428, 1904]


class NIRDuinoEmulator:
    """
    Holds state for the emulator and generates packets that match the
    NIRDuino's BLE framing.
    """

    def __init__(self):
        self.streaming = False
        self.server: Optional[BlessServer] = None
        self.subscribed_chars: set = set()  # Track subscribed characteristic UUIDs

        # 32 LED intensity slots (matching Android's ledIntensityValues array)
        self.led_values = [1] + [160] * 16 + [75, 64] * 8  # Default values

        # Battery percentage (0-100)
        self.battery_percentage = 85

        # Timing for frame duration calculation
        self.last_frame_time: Optional[float] = None
        self.frame_duration_ms = 100  # Default ~10 Hz

        # Frame counter for logging
        self.frame_count = 0

        # ===== Realistic fNIRS simulation state =====
        self.start_time = time.time()

        # Physiological parameters
        self.heart_rate = 1.0 + random.uniform(-0.1, 0.1)  # ~60 BPM (1 Hz)
        self.resp_rate = 0.25 + random.uniform(-0.02, 0.02)  # ~15 breaths/min
        self.mayer_freq = 0.1 + random.uniform(-0.01, 0.01)  # Mayer waves (~0.1 Hz)

        # Baseline voltages per channel
        self.baseline_740nm: Dict[tuple, float] = {}
        self.baseline_850nm: Dict[tuple, float] = {}
        for source in range(8):
            for detector in range(16):
                base = 0.4 + random.uniform(-0.1, 0.1)
                self.baseline_740nm[(source, detector)] = base + random.uniform(-0.02, 0.02)
                self.baseline_850nm[(source, detector)] = base + random.uniform(-0.02, 0.02)

        # Hemodynamic response state
        self.stimulus_active = False
        self.stimulus_start_time: Optional[float] = None
        self.hrf_amplitude = 0.0

    def voltage_to_adc(self, voltage: float, pga_gain: int = 1) -> int:
        """Convert voltage to ADC counts."""
        adc_value = int(voltage * pga_gain * 8388608.0 / 5.0)
        adc_value = max(-(1 << 23), min((1 << 23) - 1, adc_value))
        return adc_value

    def generate_physiological_noise(self, t: float) -> tuple:
        """Generate physiological noise components."""
        cardiac = 0.008 * math.sin(2 * math.pi * self.heart_rate * t)
        cardiac += 0.003 * math.sin(4 * math.pi * self.heart_rate * t)
        respiratory = 0.012 * math.sin(2 * math.pi * self.resp_rate * t)
        mayer = 0.006 * math.sin(2 * math.pi * self.mayer_freq * t)
        return cardiac + mayer, respiratory

    def generate_hemodynamic_response(self, t_since_stimulus: float) -> tuple:
        """Generate HRF values."""
        if t_since_stimulus < 0:
            return 0.0, 0.0
        tau, n, delay = 1.5, 4, 2.0
        t_adj = max(0, t_since_stimulus - delay)
        if t_adj == 0:
            hrf = 0.0
        else:
            hrf = ((t_adj / tau) ** n) * math.exp(-t_adj / tau)
            peak_time = n * tau
            peak_val = ((peak_time / tau) ** n) * math.exp(-n)
            hrf = hrf / peak_val if peak_val > 0 else 0
        delta_hbo2 = hrf * 0.025
        delta_hbr = -hrf * 0.012
        return delta_hbo2, delta_hbr

    def set_stimulus(self, active: bool):
        """Enable or disable stimulus simulation."""
        if active and not self.stimulus_active:
            self.stimulus_start_time = time.time()
        self.stimulus_active = active

    def make_dummy_frame_bytes(self) -> bytes:
        """Generate one realistic fNIRS frame as 33x17 int32 values."""
        data = [[0] * NUM_DETECTORS for _ in range(NUM_SOURCES)]

        now = time.time()
        if self.last_frame_time is not None:
            self.frame_duration_ms = int((now - self.last_frame_time) * 1000)
        self.last_frame_time = now

        t = now - self.start_time
        cardiac_resp, respiratory = self.generate_physiological_noise(t)

        delta_hbo2, delta_hbr = 0.0, 0.0
        if self.stimulus_active and self.stimulus_start_time is not None:
            t_stim = now - self.stimulus_start_time
            delta_hbo2, delta_hbr = self.generate_hemodynamic_response(t_stim)

        for physical_source in range(8):
            for detector in range(16):
                base_740 = self.baseline_740nm.get((physical_source, detector), 0.4)
                base_850 = self.baseline_850nm.get((physical_source, detector), 0.4)

                phase_offset = (physical_source * 16 + detector) * 0.01
                physio_740 = cardiac_resp * math.cos(phase_offset) + respiratory
                physio_850 = cardiac_resp * math.cos(phase_offset + 0.1) + respiratory

                hemo_740 = -delta_hbr
                hemo_850 = -delta_hbo2

                noise_740 = random.gauss(0, 0.003)
                noise_850 = random.gauss(0, 0.003)

                voltage_740 = max(0.1, min(0.8, base_740 + physio_740 + hemo_740 + noise_740))
                voltage_850 = max(0.1, min(0.8, base_850 + physio_850 + hemo_850 + noise_850))

                data[physical_source][detector] = self.voltage_to_adc(voltage_740)
                data[physical_source + 8][detector] = self.voltage_to_adc(voltage_850)

                voltage_740_lp = voltage_740 + random.gauss(0, 0.001)
                voltage_850_lp = voltage_850 + random.gauss(0, 0.001)

                data[physical_source + 16][detector] = self.voltage_to_adc(voltage_740_lp)
                data[physical_source + 24][detector] = self.voltage_to_adc(voltage_850_lp)

        for source in range(32):
            data[source][16] = self.frame_duration_ms // 33

        for detector in range(16):
            dark_base = 0.001 + (detector % 4) * 0.0002
            dark_voltage = max(0, min(0.01, dark_base + random.gauss(0, 0.0001)))
            data[32][detector] = self.voltage_to_adc(dark_voltage)

        data[32][16] = self.frame_duration_ms // 33

        ints = []
        for source in range(NUM_SOURCES):
            for detector in range(NUM_DETECTORS):
                ints.append(data[source][detector])

        return struct.pack("<" + "i" * PACKET_INTS, *ints)

    def build_chunks(self, frame_bytes: bytes) -> list:
        """Split the 2244-byte frame into 5 BLE chunks."""
        assert len(frame_bytes) == PACKET_BYTES
        chunks = []

        for data_set_counter in range(1, 6):
            idx = data_set_counter - 1
            size = CHUNK_SIZES[idx]
            offset = CHUNK_OFFSETS[idx]
            header = struct.pack("<i", data_set_counter)
            body_size = size - 4
            body = frame_bytes[offset : offset + body_size]
            chunks.append(header + body)

        return chunks

    def build_battery_packet(self) -> bytes:
        """Build a 4-byte battery level packet."""
        return struct.pack("<i", self.battery_percentage)

    def handle_led_write(self, characteristic: BlessGATTCharacteristic, value: bytes):
        """Called when the app writes to the LED characteristic."""
        data = bytes(value)
        logging.info(
            "LED write: %d bytes, hex: %s",
            len(data),
            data.hex(" ") if len(data) <= 16 else data[:16].hex(" ") + "...",
        )

        if len(data) == 1:
            cmd = data[0]
            if cmd == 0x03:
                logging.info("Received STOP streaming command (0x03)")
                self.streaming = False
            elif cmd == 0x05:
                logging.info("Received SYNC command (0x05)")
                self.last_frame_time = None
            else:
                logging.info("Unknown 1-byte LED command: 0x%02X", cmd)
            return

        logging.info("Received CONFIG packet (%d bytes) - starting streaming", len(data))
        self.streaming = True
        self.frame_count = 0
        self.last_frame_time = None

        if len(data) >= 33:
            self.led_values = list(data[:33])
            logging.info(
                "Updated LED intensities: flag=%d, S1=[%d,%d], S2=[%d,%d]...",
                self.led_values[0],
                self.led_values[1],
                self.led_values[2],
                self.led_values[3],
                self.led_values[4],
            )

    def handle_led_read(self, characteristic: BlessGATTCharacteristic) -> bytes:
        """Read callback for LED characteristic."""
        return bytes(self.led_values[:32])

    async def send_notification(self, char_uuid: str, data: bytes) -> bool:
        """Send a notification on a characteristic."""
        if self.server is None:
            logging.error("Server not set!")
            return False

        try:
            char = self.server.get_characteristic(char_uuid)
            if char is None:
                logging.error("Characteristic %s not found", char_uuid)
                return False

            # Set the value on the characteristic
            char.value = bytearray(data)

            # Call update_value to trigger notification
            result = self.server.update_value(FNIRS_SERVICE_UUID, char_uuid)

            # Also try to access the underlying BlueZ characteristic and emit signal
            if self.frame_count <= 5:
                try:
                    # Try to access internal bless structures
                    gatt_char = getattr(char, 'gatt', None)
                    if gatt_char:
                        # Check if there's an obj that we can emit on
                        obj = getattr(gatt_char, 'obj', None) or getattr(gatt_char, '_obj', None)
                        if obj:
                            # Try emitting PropertiesChanged
                            if hasattr(obj, 'emit_properties_changed'):
                                obj.emit_properties_changed({'Value': bytearray(data)})
                            # Also try setting Value directly
                            if hasattr(obj, 'Value'):
                                obj.Value = bytearray(data)
                except Exception as inner_e:
                    if self.frame_count <= 2:
                        logging.debug("Inner notification attempt: %s", inner_e)

            if self.frame_count <= 2:
                logging.debug(
                    "send_notification(%s) len=%d, result=%s",
                    char_uuid[-8:],
                    len(data),
                    result,
                )
            return True
        except Exception as e:
            logging.exception("Error sending notification on %s: %s", char_uuid, e)
            return False

    async def tick(self):
        """Periodic timer callback. If streaming, send one full 5-chunk frame."""
        if not self.streaming:
            return

        if self.server is None:
            logging.warning("tick: server not ready")
            return

        frame = self.make_dummy_frame_bytes()
        chunks = self.build_chunks(frame)

        self.frame_count += 1

        if self.frame_count % 10 == 1:
            logging.info(
                "tick: sending frame #%d, chunk sizes=%s",
                self.frame_count,
                [len(c) for c in chunks],
            )

        try:
            # Send chunks: char1 gets chunks 0, 4; char2 gets chunks 1, 2, 3
            await self.send_notification(DATA_CHAR_UUID1, chunks[0])
            for idx in (1, 2, 3):
                await self.send_notification(DATA_CHAR_UUID2, chunks[idx])
            await self.send_notification(DATA_CHAR_UUID1, chunks[4])

            # Occasionally send battery level updates
            if self.frame_count % 50 == 0:
                self.battery_percentage = max(
                    0, self.battery_percentage - random.choice([0, 0, 0, 1])
                )
                battery_packet = self.build_battery_packet()
                await self.send_notification(DATA_CHAR_UUID1, battery_packet)
                logging.info("Sent battery update: %d%%", self.battery_percentage)

        except Exception as e:
            logging.exception("Error sending notification: %s", e)


# ---------- Bless callbacks ----------

def read_request(characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
    """Global read request handler."""
    logging.debug("Read request on %s", characteristic.uuid)
    return characteristic.value


def write_request(
    characteristic: BlessGATTCharacteristic, value: Any, emu: NIRDuinoEmulator, **kwargs
):
    """Global write request handler."""
    logging.debug("Write request on %s: %d bytes", characteristic.uuid, len(value))
    uuid_lower = str(characteristic.uuid).lower()
    if LED_CHAR_UUID in uuid_lower:
        emu.handle_led_write(characteristic, bytes(value))
    characteristic.value = value


def subscribe_callback(characteristic: BlessGATTCharacteristic, subscribed: bool):
    """Called when a client subscribes/unsubscribes to notifications."""
    logging.info(
        "Subscription %s on characteristic %s",
        "ENABLED" if subscribed else "DISABLED",
        characteristic.uuid,
    )


async def run_server():
    """Main server coroutine."""
    logging.info("Initializing BLE server...")

    emu = NIRDuinoEmulator()
    loop = asyncio.get_running_loop()

    server = BlessServer(name="BBOL NIRDuino", loop=loop)
    emu.server = server

    server.read_request_func = read_request
    server.write_request_func = lambda char, value, **kw: write_request(
        char, value, emu, **kw
    )

    def on_subscribe(char, subscribed):
        subscribe_callback(char, subscribed)
        uuid_lower = str(char.uuid).lower()
        if subscribed:
            emu.subscribed_chars.add(uuid_lower)
        else:
            emu.subscribed_chars.discard(uuid_lower)

    # Define GATT structure
    gatt: Dict = {
        FNIRS_SERVICE_UUID: {
            DATA_CHAR_UUID1: {
                "Properties": (
                    GATTCharacteristicProperties.read
                    | GATTCharacteristicProperties.write
                    | GATTCharacteristicProperties.notify
                    | GATTCharacteristicProperties.indicate
                ),
                "Permissions": (
                    GATTAttributePermissions.readable
                    | GATTAttributePermissions.writeable
                ),
                "Value": bytearray(b""),
                "OnSubscribe": on_subscribe,
            },
            DATA_CHAR_UUID2: {
                "Properties": (
                    GATTCharacteristicProperties.read
                    | GATTCharacteristicProperties.write
                    | GATTCharacteristicProperties.notify
                    | GATTCharacteristicProperties.indicate
                ),
                "Permissions": (
                    GATTAttributePermissions.readable
                    | GATTAttributePermissions.writeable
                ),
                "Value": bytearray(b""),
                "OnSubscribe": on_subscribe,
            },
            LED_CHAR_UUID: {
                "Properties": (
                    GATTCharacteristicProperties.read
                    | GATTCharacteristicProperties.write
                    | GATTCharacteristicProperties.write_without_response
                    | GATTCharacteristicProperties.notify
                    | GATTCharacteristicProperties.indicate
                ),
                "Permissions": (
                    GATTAttributePermissions.readable
                    | GATTAttributePermissions.writeable
                ),
                "Value": bytearray(b""),
                "OnSubscribe": on_subscribe,
            },
        }
    }

    await server.add_gatt(gatt)

    logging.info("Starting BLE advertising as 'BBOL NIRDuino'...")
    await server.start()

    logging.info("Server started. Waiting for connections...")

    # Log the server's internal state for debugging
    logging.info("Checking server characteristics...")
    for uuid in [DATA_CHAR_UUID1, DATA_CHAR_UUID2, LED_CHAR_UUID]:
        char = server.get_characteristic(uuid)
        if char:
            logging.info("  Characteristic %s: OK", uuid[-8:])
            # Try to log more details
            gatt = getattr(char, 'gatt', None)
            if gatt:
                logging.info("    gatt object: %s", type(gatt).__name__)
                obj = getattr(gatt, 'obj', None) or getattr(gatt, '_obj', None)
                if obj:
                    logging.info("    dbus object: %s", type(obj).__name__)
        else:
            logging.warning("  Characteristic %s: NOT FOUND", uuid[-8:])

    # Main loop: tick at ~10 Hz
    try:
        while True:
            await emu.tick()
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        logging.info("Server shutting down...")
    finally:
        await server.stop()
        logging.info("Server stopped.")


def main():
    """Entry point."""
    logging.info("main() called, starting asyncio event loop...")
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logging.exception("Unhandled exception in main: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    print("Script starting...", flush=True)
    main()
