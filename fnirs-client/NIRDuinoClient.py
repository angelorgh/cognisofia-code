#!/usr/bin/env python3
"""
NIRDuino fNIRS Desktop Client using bleak

This client connects to the NIRDuino device (or emulator) and receives
fNIRS data via BLE notifications. Data is saved to CSV files.
"""
import asyncio
import csv
import logging
import os
import struct
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from bleak import BleakClient, BleakScanner, BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic
import platform

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# ===== BLE UUIDs (must match the device/emulator) =====
DEVICE_NAME = "BBOL NIRDuino"
FNIRS_SERVICE_UUID = "938548e6-c655-11ea-87d0-0242ac130003"
DATA_CHAR_UUID1 = "77539407-6493-4b89-985f-baaf4c0f8d86"
DATA_CHAR_UUID2 = "513b630c-e5fd-45b5-a678-bb2835d6c1d2"
LED_CHAR_UUID = "19b10001-e8f2-537e-4f6c-d104768a1213"

# ===== Packet dimensions =====
NUM_SOURCES = 33
NUM_DETECTORS = 17
PACKET_INTS = NUM_SOURCES * NUM_DETECTORS  # 561
PACKET_BYTES = PACKET_INTS * 4  # 2244 bytes

# Chunk sizes: 480, 480, 480, 480, 344
CHUNK_SIZES = [480, 480, 480, 480, 344]

# Number of physical LED sources
NUM_PHYSICAL_SOURCES = 8
NUM_PHYSICAL_DETECTORS = 16


class CSVWriter:
    """Handles writing fNIRS data to CSV files."""

    def __init__(self, output_dir: str = "data"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.csv_file = None
        self.csv_writer = None
        self.file_handle = None
        self.session_start_time: Optional[float] = None
        self.rows_written = 0

    def _generate_header(self) -> List[str]:
        """Generate CSV header matching Android app format."""
        header = ["Timestamp", "Time(s)"]

        # For each physical source (1-8) and detector (1-16):
        # S{s}_D{d}_740nm_RP, S{s}_D{d}_850nm_RP, S{s}_D{d}_740nm_LP, S{s}_D{d}_850nm_LP
        for source in range(1, NUM_PHYSICAL_SOURCES + 1):
            for detector in range(1, NUM_PHYSICAL_DETECTORS + 1):
                header.append(f"S{source}_D{detector}_740nm_RP")
                header.append(f"S{source}_D{detector}_850nm_RP")
                header.append(f"S{source}_D{detector}_740nm_LP")
                header.append(f"S{source}_D{detector}_850nm_LP")

        # Dark current measurements
        for detector in range(1, NUM_PHYSICAL_DETECTORS + 1):
            header.append(f"Dark_D{detector}")

        # Stimulus marker
        header.append("Stimulus")

        return header

    def start_session(self) -> str:
        """Start a new recording session and create CSV file."""
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"fnirs_session_{timestamp}.csv"
        filepath = self.output_dir / filename

        # Open file and create CSV writer
        self.file_handle = open(filepath, "w", newline="")
        self.csv_writer = csv.writer(self.file_handle)

        # Write header
        header = self._generate_header()
        self.csv_writer.writerow(header)

        self.session_start_time = time.time()
        self.rows_written = 0

        logging.info("Started CSV recording: %s", filepath)
        return str(filepath)

    def write_frame(self, frame_data: List[List[int]], stimulus: int = 0):
        """
        Write a frame of data to the CSV file.

        Args:
            frame_data: 33x17 array of int32 ADC values
                - Sources 0-7: 740nm Regular Power
                - Sources 8-15: 850nm Regular Power
                - Sources 16-23: 740nm Low Power
                - Sources 24-31: 850nm Low Power
                - Source 32: Dark current
            stimulus: Stimulus marker value (0=off, 10=on)
        """
        if not self.csv_writer or not self.session_start_time:
            return

        # Calculate timestamp
        current_time = time.time()
        elapsed = current_time - self.session_start_time
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # Build row
        row = [timestamp, f"{elapsed:.3f}"]

        # Extract data for each source-detector pair
        # Data layout in frame_data[source][detector]:
        # Sources 0-7: 740nm RP, Sources 8-15: 850nm RP
        # Sources 16-23: 740nm LP, Sources 24-31: 850nm LP
        for source in range(NUM_PHYSICAL_SOURCES):
            for detector in range(NUM_PHYSICAL_DETECTORS):
                # 740nm Regular Power (sources 0-7)
                val_740_rp = frame_data[source][detector]
                # 850nm Regular Power (sources 8-15)
                val_850_rp = frame_data[source + 8][detector]
                # 740nm Low Power (sources 16-23)
                val_740_lp = frame_data[source + 16][detector]
                # 850nm Low Power (sources 24-31)
                val_850_lp = frame_data[source + 24][detector]

                row.extend([val_740_rp, val_850_rp, val_740_lp, val_850_lp])

        # Dark current (source 32, detectors 0-15)
        for detector in range(NUM_PHYSICAL_DETECTORS):
            row.append(frame_data[32][detector])

        # Stimulus marker
        row.append(stimulus)

        # Write row
        self.csv_writer.writerow(row)
        self.rows_written += 1

        # Flush periodically to ensure data is saved
        if self.rows_written % 100 == 0:
            self.file_handle.flush()

    def stop_session(self):
        """Stop recording and close the CSV file."""
        if self.file_handle:
            self.file_handle.flush()
            self.file_handle.close()
            self.file_handle = None
            self.csv_writer = None
            logging.info("CSV recording stopped. %d rows written.", self.rows_written)

        self.session_start_time = None
        self.rows_written = 0


class FNIRSClient:
    """Client for receiving fNIRS data from NIRDuino device."""

    def __init__(self, output_dir: str = "data"):
        self.client: Optional[BleakClient] = None
        self.connected = False
        self.streaming = False

        # Buffer to reassemble chunks into complete frames
        self.chunk_buffer = {}  # dataSetCounter -> chunk bytes
        self.frame_count = 0

        # CSV writer for data recording
        self.csv_writer = CSVWriter(output_dir)
        self.recording = False

        # Stimulus state (can be toggled by user)
        self.stimulus_active = False

        # Battery level
        self.battery_level: Optional[int] = None

        # Callback for when a complete frame is received
        self.on_frame_received: Optional[Callable[[int, List[List[int]]], None]] = None

        # Callback for battery updates
        self.on_battery_update: Optional[Callable[[int], None]] = None

    def _build_config_packet(
        self,
        rp_740: int = 255,
        rp_850: int = 255,
        lp_740: int = 75,
        lp_850: int = 64,
    ) -> bytes:
        """
        Build the configuration packet to start streaming.

        Args:
            rp_740: Regular power intensity for 740nm LEDs (0-255)
            rp_850: Regular power intensity for 850nm LEDs (0-255)
            lp_740: Low power intensity for 740nm LEDs (0-255)
            lp_850: Low power intensity for 850nm LEDs (0-255)

        Returns:
            33-byte configuration packet
        """
        packet = bytearray(33)
        packet[0] = 0x01  # Start flag

        # Regular power values for 8 sources (740nm, 850nm pairs)
        for i in range(8):
            packet[1 + i * 2] = rp_740
            packet[2 + i * 2] = rp_850

        # Low power values for 8 sources (740nm, 850nm pairs)
        for i in range(8):
            packet[17 + i * 2] = lp_740
            packet[18 + i * 2] = lp_850

        return bytes(packet)

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ):
        """Handle incoming notifications from data characteristics."""
        # Check packet size to determine type
        if len(data) == 4:
            # Battery packet
            battery = struct.unpack("<i", data)[0]
            self.battery_level = battery
            logging.info("Battery level: %d%%", battery)
            if self.on_battery_update:
                self.on_battery_update(battery)
            return

        if len(data) not in CHUNK_SIZES:
            logging.warning("Unexpected packet size: %d bytes", len(data))
            return

        # Extract dataSetCounter from header
        data_set_counter = struct.unpack("<i", data[:4])[0]
        chunk_body = data[4:]

        # Store chunk in buffer
        self.chunk_buffer[data_set_counter] = chunk_body

        # Check if we have all 5 chunks
        if len(self.chunk_buffer) == 5 and all(
            i in self.chunk_buffer for i in range(1, 6)
        ):
            # Reassemble frame
            frame_bytes = b"".join(self.chunk_buffer[i] for i in range(1, 6))

            self.frame_count += 1
            self.chunk_buffer.clear()

            # Parse frame data
            if len(frame_bytes) == PACKET_BYTES:
                self._process_frame(frame_bytes)
            else:
                logging.warning(
                    "Frame size mismatch: expected %d, got %d",
                    PACKET_BYTES,
                    len(frame_bytes),
                )

    def _process_frame(self, frame_bytes: bytes):
        """Process a complete fNIRS data frame."""
        # Unpack as 33x17 int32 array
        ints = struct.unpack("<" + "i" * PACKET_INTS, frame_bytes)

        # Convert to 2D array [source][detector]
        data = []
        for source in range(NUM_SOURCES):
            row = []
            for detector in range(NUM_DETECTORS):
                idx = source * NUM_DETECTORS + detector
                row.append(ints[idx])
            data.append(row)

        # Log every 10th frame
        if self.frame_count % 10 == 1:
            logging.info(
                "Frame #%d: S0D0_740nm=%d, S0D0_850nm=%d",
                self.frame_count,
                data[0][0],
                data[8][0],
            )

        # Write to CSV if recording
        if self.recording:
            stimulus = 10 if self.stimulus_active else 0
            self.csv_writer.write_frame(data, stimulus)

        # Call user callback if set
        if self.on_frame_received:
            self.on_frame_received(self.frame_count, data)

    def set_stimulus(self, active: bool):
        """Set the stimulus marker state."""
        self.stimulus_active = active
        logging.info("Stimulus marker: %s", "ON" if active else "OFF")

    async def scan_for_device(self, timeout: float = 10.0) -> Optional[BLEDevice]:
        """Scan for the NIRDuino device and return its address."""
        logging.info("Scanning for %s...", DEVICE_NAME)

        devices = await BleakScanner.discover(timeout=timeout)

        for device in devices:
            if device.name and DEVICE_NAME in device.name:
                logging.info("Found device: %s (%s)", device.name, device.address)
                return device

        logging.warning("Device not found")
        return None

    def _disconnection_handler(self, client: BleakClient):
        """Called when the device disconnects."""
        logging.warning("Device disconnected unexpectedly!")
        self.connected = False
        self.streaming = False

        # Stop CSV recording if active
        if self.recording:
            self.csv_writer.stop_session()
            self.recording = False

    async def connect(self, device: BLEDevice) -> bool:
        """Connect to the device at the given address."""
        logging.info("Connecting to %s...", device.address)

        # Configure connection parameters for better stability
        # On macOS, we need to use CoreBluetooth-specific options
        self.client = BleakClient(
                device,
                disconnected_callback=self._disconnection_handler,
                timeout=15.0,  # Longer connection timeout
            )

        try:
            await self.client.connect()
            self.connected = True
            logging.info("Connected!")

            # Log connection info
            if hasattr(self.client, 'mtu_size'):
                logging.info("MTU size: %d", self.client.mtu_size)

            return True
        except Exception as e:
            logging.error("Connection failed: %s", e)
            self.connected = False
            return False

    async def start_streaming(
        self,
        record: bool = True,
        rp_740: int = 255,
        rp_850: int = 255,
        lp_740: int = 75,
        lp_850: int = 64,
    ) -> bool:
        """
        Enable notifications and start data streaming.

        Args:
            record: Whether to record data to CSV
            rp_740: Regular power intensity for 740nm LEDs (0-255)
            rp_850: Regular power intensity for 850nm LEDs (0-255)
            lp_740: Low power intensity for 740nm LEDs (0-255)
            lp_850: Low power intensity for 850nm LEDs (0-255)
        """
        if not self.client or not self.connected:
            logging.error("Not connected!")
            return False

        try:
            # Start CSV recording if requested
            if record:
                self.csv_writer.start_session()
                self.recording = True

            # Enable notifications on both data characteristics
            logging.info("Enabling notifications on data characteristics...")

            await self.client.start_notify(DATA_CHAR_UUID1, self._notification_handler)
            await self.client.start_notify(DATA_CHAR_UUID2, self._notification_handler)

            logging.info("Notifications enabled")

            # Send config packet to start streaming
            config = self._build_config_packet(rp_740, rp_850, lp_740, lp_850)
            logging.info("Sending config packet (%d bytes)...", len(config))

            await self.client.write_gatt_char(LED_CHAR_UUID, config)

            self.streaming = True
            self.frame_count = 0
            logging.info("Streaming started!")

            return True
        except Exception as e:
            logging.error("Failed to start streaming: %s", e)
            if self.recording:
                self.csv_writer.stop_session()
                self.recording = False
            return False

    async def stop_streaming(self) -> bool:
        """Stop data streaming."""
        if not self.client:
            self.streaming = False
            return False

        try:
            # Check if still connected before sending commands
            if not self.client.is_connected:
                logging.debug("Already disconnected, skipping stop commands")
                self.streaming = False
                if self.recording:
                    self.csv_writer.stop_session()
                    self.recording = False
                return True

            # Send stop command
            stop_cmd = bytes([0x03])
            await self.client.write_gatt_char(LED_CHAR_UUID, stop_cmd)

            # Disable notifications
            await self.client.stop_notify(DATA_CHAR_UUID1)
            await self.client.stop_notify(DATA_CHAR_UUID2)

            self.streaming = False
            logging.info("Streaming stopped")

            # Stop CSV recording
            if self.recording:
                self.csv_writer.stop_session()
                self.recording = False

            return True
        except Exception as e:
            logging.debug("Stop streaming cleanup: %s", e)
            self.streaming = False
            if self.recording:
                self.csv_writer.stop_session()
                self.recording = False
            return False

    async def disconnect(self):
        """Disconnect from the device."""
        if self.client:
            if self.streaming:
                await self.stop_streaming()
            if self.client.is_connected:
                await self.client.disconnect()
            self.connected = False
            logging.info("Disconnected")


async def main():
    """Main entry point - continuous streaming with automatic reconnection."""
    output_dir = "data"
    max_reconnect_attempts = 5
    reconnect_delay = 3.0  # seconds between reconnection attempts

    # Store device address for reconnection
    device_address = None

    reconnect_count = 0
    total_frames = 0

    while reconnect_count <= max_reconnect_attempts:
        client = FNIRSClient(output_dir=output_dir)

        try:
            # Scan for device (only on first connection or if address is unknown)
            if device_address is None:
                device_address = await client.scan_for_device()
                if not device_address:
                    logging.error("Could not find device. Make sure it's advertising.")
                    return

            # Connect
            if reconnect_count > 0:
                logging.info("Reconnection attempt %d/%d...", reconnect_count, max_reconnect_attempts)

            if not await client.connect(device_address):
                reconnect_count += 1
                await asyncio.sleep(reconnect_delay)
                continue

            # Start streaming with CSV recording
            if not await client.start_streaming(record=True):
                reconnect_count += 1
                await asyncio.sleep(reconnect_delay)
                continue

            logging.info("=" * 50)
            logging.info("Streaming data. Press Ctrl+C to stop.")
            logging.info("=" * 50)

            # Reset reconnect counter on successful stream start
            reconnect_count = 0

            # Run until interrupted or disconnected
            last_frame_count = 0
            stall_count = 0
            keepalive_counter = 0

            while True:
                await asyncio.sleep(1)

                # Check if still connected
                if not client.connected or (client.client and not client.client.is_connected):
                    logging.warning("Connection lost! Will attempt reconnection...")
                    total_frames += client.frame_count
                    reconnect_count += 1
                    break

                # Keepalive: periodically read a characteristic to maintain connection
                # More aggressive keepalive - every 5 seconds
                keepalive_counter += 1
                if keepalive_counter >= 5:  # Every 5 seconds
                    keepalive_counter = 0
                    try:
                        if client.client and client.client.is_connected:
                            # Read LED characteristic as keepalive
                            await client.client.read_gatt_char(LED_CHAR_UUID)
                            logging.debug("Keepalive read successful")
                    except Exception as e:
                        logging.warning("Keepalive read failed: %s", e)
                        # Keepalive failure might indicate impending disconnection
                        # but don't break yet, let the connection check handle it

                # Check for stalled data (no new frames in last second)
                if client.frame_count == last_frame_count and client.streaming:
                    stall_count += 1
                    if stall_count >= 5:
                        logging.warning("No data received for %d seconds!", stall_count)
                    if stall_count >= 30:
                        logging.error("Data stream stalled for 30 seconds. Will attempt reconnection...")
                        total_frames += client.frame_count
                        reconnect_count += 1
                        break
                else:
                    stall_count = 0
                last_frame_count = client.frame_count

                # Print status every 100 frames
                if client.frame_count > 0 and client.frame_count % 100 == 0:
                    logging.info(
                        "Status: %d frames received (%d total session), %d rows written to CSV",
                        client.frame_count,
                        total_frames + client.frame_count,
                        client.csv_writer.rows_written,
                    )

        except KeyboardInterrupt:
            logging.info("\nInterrupted by user")
            await client.disconnect()
            total_frames += client.frame_count
            break
        except asyncio.CancelledError:
            logging.info("\nCancelled")
            await client.disconnect()
            total_frames += client.frame_count
            break
        except Exception as e:
            logging.error("Unexpected error: %s", e)
            reconnect_count += 1
        finally:
            # Clean up current client
            if client.connected:
                await client.disconnect()

        # Wait before reconnecting
        if reconnect_count > 0 and reconnect_count <= max_reconnect_attempts:
            logging.info("Waiting %.1f seconds before reconnection...", reconnect_delay)
            await asyncio.sleep(reconnect_delay)

    if reconnect_count > max_reconnect_attempts:
        logging.error("Max reconnection attempts reached. Exiting.")

    logging.info("Session complete. Total frames received: %d", total_frames)
    logging.info("Data saved to: %s", output_dir)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
