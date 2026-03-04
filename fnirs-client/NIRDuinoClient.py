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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Union

from bleak import BleakClient, BleakScanner, BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic
import platform

# Optional TimescaleDB support (pip install psycopg2-binary)
try:
    import psycopg2
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

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

# ===== ADC / DAC conversion constants =====
ADC_MAX  = 8_388_608.0   # 2^23 (24-bit ADC half-range)
ADC_VREF = 5.0           # Effective reference voltage (2 × 2.5 V)
LED_VMAX = 5.0           # MCP4728 VDD reference


def _adc_to_voltage(raw: int) -> float:
    """Convert a 24-bit signed ADC count to voltage (0–5 V)."""
    return raw * ADC_VREF / ADC_MAX


def _intensity_to_voltage(intensity: int) -> float:
    """Convert a firmware intensity byte (0–255) to LED drive voltage (0–5 V)."""
    return intensity * LED_VMAX / 255.0


# ─────────────────────────────────────────────────────────────────────────────
# TimescaleDB writer
# ─────────────────────────────────────────────────────────────────────────────

def _build_frames_insert_sql() -> str:
    """Generate the parameterised INSERT SQL for the frames hypertable."""
    cols = ["ts", "session_id", "time_elapsed"]
    for s in range(1, NUM_PHYSICAL_SOURCES + 1):
        cols += [f"led_s{s}_740nm_rp", f"led_s{s}_850nm_rp",
                 f"led_s{s}_740nm_lp", f"led_s{s}_850nm_lp"]
    for s in range(1, NUM_PHYSICAL_SOURCES + 1):
        for d in range(1, NUM_PHYSICAL_DETECTORS + 1):
            cols += [f"s{s}_d{d}_740nm_rp", f"s{s}_d{d}_850nm_rp",
                     f"s{s}_d{d}_740nm_lp", f"s{s}_d{d}_850nm_lp"]
    for d in range(1, NUM_PHYSICAL_DETECTORS + 1):
        cols.append(f"dark_d{d}")
    cols.append("stimulus")
    ph = ", ".join(["%s"] * len(cols))
    return f"INSERT INTO frames ({', '.join(cols)}) VALUES ({ph})"


_FRAMES_INSERT_SQL = _build_frames_insert_sql()


class DBWriter:
    """
    Writes fNIRS session and frame data to a TimescaleDB (PostgreSQL) database.

    Frames are buffered in memory and committed in batches of BATCH_SIZE rows.
    The actual psycopg2 flush is always executed in a thread-pool worker so
    that the asyncio event loop (and therefore the GUI + BLE callbacks) is
    never blocked by a network round-trip to the database.

    Thread-safety: all psycopg2 operations are serialised through _lock.
    _pending is only ever appended/swapped from the event-loop thread
    (CPython GIL makes the reference swap in _take_batch() atomic).
    """

    BATCH_SIZE = 50  # rows buffered before a flush (~3 s at 15 Hz)

    def __init__(self):
        self.conn = None
        self._lock = threading.Lock()   # serialises all psycopg2 access
        self._pending: list = []
        self._session_id = None
        self.rows_written: int = 0

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self, host: str, port: str, dbname: str,
                user: str, password: str) -> tuple:
        """Open a persistent connection for streaming use."""
        if not _PSYCOPG2_AVAILABLE:
            return False, "psycopg2 not installed — run: pip install psycopg2-binary"
        try:
            with self._lock:
                if self.conn and not self.conn.closed:
                    self.conn.close()
                self.conn = psycopg2.connect(
                    host=host, port=int(port), dbname=dbname,
                    user=user, password=password, connect_timeout=5,
                )
                self.conn.autocommit = False
            logging.info("DB connected: %s@%s:%s/%s", user, host, port, dbname)
            return True, "Connected"
        except Exception as exc:
            self.conn = None
            return False, str(exc)

    def test_connection(self, host: str, port: str, dbname: str,
                        user: str, password: str) -> tuple:
        """Test credentials without storing a permanent connection."""
        if not _PSYCOPG2_AVAILABLE:
            return False, "psycopg2 not installed — run: pip install psycopg2-binary"
        try:
            conn = psycopg2.connect(
                host=host, port=int(port), dbname=dbname,
                user=user, password=password, connect_timeout=5,
            )
            conn.close()
            return True, "Connection successful"
        except Exception as exc:
            return False, str(exc)

    def disconnect(self):
        """Close the stored connection."""
        with self._lock:
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
        logging.info("DB disconnected")

    @property
    def is_connected(self) -> bool:
        return self.conn is not None and self.conn.closed == 0

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def start_session(self, subject_name: str, problem: str) -> str:
        """Insert a row into sessions and return its UUID as a string."""
        if not self.is_connected:
            raise RuntimeError("Not connected to database")
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO sessions (subject_name, problem) "
                "VALUES (%s, %s) RETURNING session_id",
                (subject_name or "unknown", problem or "unknown"),
            )
            self._session_id = cur.fetchone()[0]
            self.conn.commit()
            cur.close()
        self._pending.clear()
        self.rows_written = 0
        logging.info("DB session started: %s", self._session_id)
        return str(self._session_id)

    def stop_session(self):
        """Flush remaining rows and finalise the session.

        Blocking — must be called from a thread, not the event-loop thread.
        FNIRSClient._stop_recording() spawns a daemon thread for this.
        """
        self._flush_batch(self._take_batch())
        with self._lock:
            if self.is_connected and self._session_id:
                try:
                    cur = self.conn.cursor()
                    cur.execute("SELECT stop_session(%s)", (self._session_id,))
                    self.conn.commit()
                    cur.close()
                    logging.info(
                        "DB session closed: %s  (%d rows)",
                        self._session_id, self.rows_written,
                    )
                except Exception as exc:
                    logging.error("DB stop_session error: %s", exc)
        self._session_id = None

    # ── Frame writes ──────────────────────────────────────────────────────────

    def write_frame(self, ts: datetime, time_elapsed: float,
                    frame_data: List[List[int]], stimulus: int,
                    led_config: Optional[dict]) -> bool:
        """Buffer one frame row.

        Returns True when the batch has reached BATCH_SIZE and is ready
        to be flushed.  The caller is responsible for calling _take_batch()
        and scheduling _flush_batch() in a thread-pool executor.
        """
        if not self.is_connected or self._session_id is None:
            return False
        row = self._build_row(
            ts, self._session_id, time_elapsed, frame_data, stimulus, led_config
        )
        self._pending.append(row)
        return len(self._pending) >= self.BATCH_SIZE

    def _take_batch(self) -> list:
        """Atomically extract all buffered rows and reset the buffer.

        The tuple-unpack ``batch, self._pending = self._pending, []`` is
        atomic under CPython's GIL, so it is safe to call from the
        event-loop thread while new rows are being appended concurrently.
        """
        batch, self._pending = self._pending, []
        return batch

    def _flush_batch(self, batch: list) -> None:
        """Write *batch* to the DB in a single transaction.

        Blocking — must be called from a thread-pool worker, never from
        the asyncio event loop, to avoid freezing the GUI.
        """
        if not batch:
            return
        with self._lock:
            if not self.is_connected:
                logging.warning(
                    "DB flush: not connected — %d rows dropped", len(batch)
                )
                return
            try:
                cur = self.conn.cursor()
                cur.executemany(_FRAMES_INSERT_SQL, batch)
                self.conn.commit()
                cur.close()
                self.rows_written += len(batch)
                logging.debug("DB flush: %d rows committed", len(batch))
            except Exception as exc:
                logging.error("DB flush error: %s", exc)
                try:
                    self.conn.rollback()
                except Exception:
                    pass

    @staticmethod
    def _build_row(ts: datetime, session_id, time_elapsed: float,
                   frame_data: List[List[int]], stimulus: int,
                   led_config: Optional[dict]) -> tuple:
        cfg = led_config or {}
        rp7 = cfg.get("rp_740", [0] * NUM_PHYSICAL_SOURCES)
        rp8 = cfg.get("rp_850", [0] * NUM_PHYSICAL_SOURCES)
        lp7 = cfg.get("lp_740", [0] * NUM_PHYSICAL_SOURCES)
        lp8 = cfg.get("lp_850", [0] * NUM_PHYSICAL_SOURCES)

        vals: list = [ts, session_id, time_elapsed]

        # LED drive voltages
        for s in range(NUM_PHYSICAL_SOURCES):
            vals += [_intensity_to_voltage(rp7[s]), _intensity_to_voltage(rp8[s]),
                     _intensity_to_voltage(lp7[s]), _intensity_to_voltage(lp8[s])]

        # Detector readings
        for src in range(NUM_PHYSICAL_SOURCES):
            for det in range(NUM_PHYSICAL_DETECTORS):
                vals += [
                    _adc_to_voltage(frame_data[src][det]),
                    _adc_to_voltage(frame_data[src + 8][det]),
                    _adc_to_voltage(frame_data[src + 16][det]),
                    _adc_to_voltage(frame_data[src + 24][det]),
                ]

        # Dark current
        for det in range(NUM_PHYSICAL_DETECTORS):
            vals.append(_adc_to_voltage(frame_data[32][det]))

        vals.append(stimulus)
        return tuple(vals)


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
        """Generate CSV header with voltage values and LED configuration."""
        header = ["Timestamp", "Time(s)"]

        # LED configuration columns (voltage applied to each source/wavelength/power)
        for source in range(1, NUM_PHYSICAL_SOURCES + 1):
            header.append(f"LED_S{source}_740nm_RP(V)")
            header.append(f"LED_S{source}_850nm_RP(V)")
            header.append(f"LED_S{source}_740nm_LP(V)")
            header.append(f"LED_S{source}_850nm_LP(V)")

        # Detector readings in voltage — S{s}_D{d}_{wavelength}_{power}(V)
        for source in range(1, NUM_PHYSICAL_SOURCES + 1):
            for detector in range(1, NUM_PHYSICAL_DETECTORS + 1):
                header.append(f"S{source}_D{detector}_740nm_RP(V)")
                header.append(f"S{source}_D{detector}_850nm_RP(V)")
                header.append(f"S{source}_D{detector}_740nm_LP(V)")
                header.append(f"S{source}_D{detector}_850nm_LP(V)")

        # Dark current measurements (voltage)
        for detector in range(1, NUM_PHYSICAL_DETECTORS + 1):
            header.append(f"Dark_D{detector}(V)")

        # Stimulus marker
        header.append("Stimulus")

        return header

    def start_session(self, subject_name: str = "", problem: str = "") -> str:
        """Start a new recording session and create CSV file.

        Args:
            subject_name: Name of the subject being measured.
            problem: Problem name/number the subject is working on.
        """
        # Sanitise inputs: strip, replace spaces with underscores, fallback
        subj = subject_name.strip().replace(" ", "_") or "unknown"
        prob = problem.strip().replace(" ", "_") or "unknown"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"session_{subj}_{prob}_{timestamp}.csv"
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

    def write_frame(
        self,
        frame_data: List[List[int]],
        stimulus: int = 0,
        led_config: Optional[dict] = None,
    ):
        """
        Write a frame of data to the CSV file.

        Values are converted from raw 24-bit ADC counts to voltage (V).
        LED configuration voltages are included per row so mid-session
        changes are captured.

        Args:
            frame_data: 33x17 array of int32 ADC values
                - Sources 0-7: 740nm Regular Power
                - Sources 8-15: 850nm Regular Power
                - Sources 16-23: 740nm Low Power
                - Sources 24-31: 850nm Low Power
                - Source 32: Dark current
            stimulus: Stimulus marker value (0=off, 10=on)
            led_config: dict with keys 'rp_740', 'rp_850', 'lp_740', 'lp_850',
                        each a list of 8 intensity bytes (0-255).
                        If None, zeros are written.
        """
        if not self.csv_writer or not self.session_start_time:
            return

        # Calculate timestamp
        current_time = time.time()
        elapsed = current_time - self.session_start_time
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # Build row
        row = [timestamp, f"{elapsed:.3f}"]

        # ── LED configuration voltages (8 sources × 4 modes = 32 cols) ────
        if led_config:
            rp7 = led_config.get("rp_740", [0] * 8)
            rp8 = led_config.get("rp_850", [0] * 8)
            lp7 = led_config.get("lp_740", [0] * 8)
            lp8 = led_config.get("lp_850", [0] * 8)
        else:
            rp7 = rp8 = lp7 = lp8 = [0] * 8

        for s in range(NUM_PHYSICAL_SOURCES):
            row.append(f"{_intensity_to_voltage(rp7[s]):.4f}")
            row.append(f"{_intensity_to_voltage(rp8[s]):.4f}")
            row.append(f"{_intensity_to_voltage(lp7[s]):.4f}")
            row.append(f"{_intensity_to_voltage(lp8[s]):.4f}")

        # ── Detector readings converted to voltage ────────────────────────
        for source in range(NUM_PHYSICAL_SOURCES):
            for detector in range(NUM_PHYSICAL_DETECTORS):
                val_740_rp = _adc_to_voltage(frame_data[source][detector])
                val_850_rp = _adc_to_voltage(frame_data[source + 8][detector])
                val_740_lp = _adc_to_voltage(frame_data[source + 16][detector])
                val_850_lp = _adc_to_voltage(frame_data[source + 24][detector])

                row.extend([
                    f"{val_740_rp:.6f}",
                    f"{val_850_rp:.6f}",
                    f"{val_740_lp:.6f}",
                    f"{val_850_lp:.6f}",
                ])

        # Dark current in voltage (source 32, detectors 0-15)
        for detector in range(NUM_PHYSICAL_DETECTORS):
            row.append(f"{_adc_to_voltage(frame_data[32][detector]):.6f}")

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

        # Current LED configuration (intensity bytes 0-255, per source)
        self.led_config: Optional[dict] = None

        # Output mode: "csv" | "db" | "both"  (set by GUI before start_streaming)
        self.output_mode: str = "csv"
        self.db_writer: Optional[DBWriter] = None
        self._session_start_time: Optional[float] = None

    @staticmethod
    def _expand(val: Union[int, List[int]], n: int = 8) -> List[int]:
        """Return a list of *n* ints.  Accepts a single int (broadcast) or a list."""
        if isinstance(val, int):
            return [val] * n
        if len(val) != n:
            raise ValueError(f"Expected {n} values, got {len(val)}")
        return list(val)

    def _build_config_packet(
        self,
        rp_740: Union[int, List[int]] = 255,
        rp_850: Union[int, List[int]] = 255,
        lp_740: Union[int, List[int]] = 75,
        lp_850: Union[int, List[int]] = 64,
    ) -> bytes:
        """
        Build the 33-byte configuration packet for the NIRDuino.

        Each parameter accepts either a single int (applied to all 8 sources)
        or a list of 8 ints for per-source control.

        Packet byte layout:
            Byte  0     : command flag (0x01 = START)
            Bytes 1-16  : Regular Power — 8 pairs of (740nm, 850nm)
            Bytes 17-32 : Low Power     — 8 pairs of (740nm, 850nm)
        """
        rp7 = self._expand(rp_740)
        rp8 = self._expand(rp_850)
        lp7 = self._expand(lp_740)
        lp8 = self._expand(lp_850)

        packet = bytearray(33)
        packet[0] = 0x01  # Start flag

        for i in range(8):
            packet[1 + i * 2] = rp7[i]   # 740nm RP
            packet[2 + i * 2] = rp8[i]   # 850nm RP

        for i in range(8):
            packet[17 + i * 2] = lp7[i]  # 740nm LP
            packet[18 + i * 2] = lp8[i]  # 850nm LP

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

        # Write to active output(s)
        if self.recording:
            stimulus = 10 if self.stimulus_active else 0
            elapsed = (time.time() - self._session_start_time
                       if self._session_start_time else 0.0)
            if self.output_mode in ("csv", "both"):
                self.csv_writer.write_frame(data, stimulus, self.led_config)
            if self.output_mode in ("db", "both") and self.db_writer:
                batch_ready = self.db_writer.write_frame(
                    datetime.now(), elapsed, data, stimulus, self.led_config
                )
                if batch_ready:
                    # Atomically take the full batch and flush it in a
                    # thread-pool worker so the event loop is never blocked
                    # by the psycopg2 network round-trip.
                    batch = self.db_writer._take_batch()
                    asyncio.get_event_loop().run_in_executor(
                        None, self.db_writer._flush_batch, batch
                    )

        # Call user callback if set
        if self.on_frame_received:
            self.on_frame_received(self.frame_count, data)

    def _stop_recording(self):
        """Stop whichever output(s) are active and reset the recording flag."""
        if self.output_mode in ("csv", "both"):
            self.csv_writer.stop_session()
        if self.output_mode in ("db", "both") and self.db_writer:
            # stop_session() blocks on the final flush + SQL call; run it in
            # a daemon thread so neither the event loop nor a sync callback
            # (e.g. _disconnection_handler) is stalled.
            threading.Thread(
                target=self.db_writer.stop_session,
                daemon=True,
                name="db-stop-session",
            ).start()
        self.recording = False
        self._session_start_time = None

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

        # Stop active output(s)
        if self.recording:
            self._stop_recording()

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
        rp_740: Union[int, List[int]] = 255,
        rp_850: Union[int, List[int]] = 255,
        lp_740: Union[int, List[int]] = 75,
        lp_850: Union[int, List[int]] = 64,
        subject_name: str = "",
        problem: str = "",
    ) -> bool:
        """
        Enable notifications and start data streaming.

        Args:
            record: Whether to record data to CSV
            rp_740: Regular power 740nm intensity — int (all sources) or list of 8
            rp_850: Regular power 850nm intensity — int (all sources) or list of 8
            lp_740: Low power 740nm intensity — int (all sources) or list of 8
            lp_850: Low power 850nm intensity — int (all sources) or list of 8
            subject_name: Name of the subject being measured
            problem: Problem name/number the subject is working on
        """
        if not self.client or not self.connected:
            logging.error("Not connected!")
            return False

        try:
            # Start recording according to output_mode
            if record:
                self._session_start_time = time.time()
                if self.output_mode in ("csv", "both"):
                    self.csv_writer.start_session(subject_name, problem)
                if self.output_mode in ("db", "both") and self.db_writer:
                    self.db_writer.start_session(subject_name, problem)
                self.recording = True

            # Enable notifications on both data characteristics
            logging.info("Enabling notifications on data characteristics...")

            await self.client.start_notify(DATA_CHAR_UUID1, self._notification_handler)
            await self.client.start_notify(DATA_CHAR_UUID2, self._notification_handler)

            logging.info("Notifications enabled")

            # Store current LED configuration for CSV recording
            self.led_config = {
                "rp_740": self._expand(rp_740),
                "rp_850": self._expand(rp_850),
                "lp_740": self._expand(lp_740),
                "lp_850": self._expand(lp_850),
            }

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
                self._stop_recording()
            return False

    async def stop_streaming(self) -> bool:
        """Stop data streaming."""
        if not self.client:
            self.streaming = False
            return False

        try:
            # Check if still connected before sending commands
            if not self.client.is_connected:
                logging.info("Already disconnected, skipping stop commands")
                self.streaming = False
                if self.recording:
                    self._stop_recording()
                return True

            # Send stop command — retry up to 3 times because BLE link
            # may be congested with incoming data notifications.
            stop_cmd = bytes([0x03])
            sent = False
            for attempt in range(3):
                try:
                    await self.client.write_gatt_char(LED_CHAR_UUID, stop_cmd)
                    sent = True
                    logging.info("Stop command sent (attempt %d)", attempt + 1)
                    break
                except Exception as exc:
                    logging.warning(
                        "Stop command write failed (attempt %d): %s",
                        attempt + 1, exc,
                    )
                    await asyncio.sleep(0.1)

            if not sent:
                logging.error("Failed to send stop command after 3 attempts")

            # Brief pause to let firmware process the stop before we
            # unsubscribe (reduces BLE congestion from notifications).
            await asyncio.sleep(0.15)

            # Disable notifications
            try:
                await self.client.stop_notify(DATA_CHAR_UUID1)
            except Exception as exc:
                logging.warning("stop_notify(DATA1): %s", exc)
            try:
                await self.client.stop_notify(DATA_CHAR_UUID2)
            except Exception as exc:
                logging.warning("stop_notify(DATA2): %s", exc)

            self.streaming = False
            logging.info("Streaming stopped")

            # Stop active output(s)
            if self.recording:
                self._stop_recording()

            return True
        except Exception as e:
            logging.warning("Stop streaming error: %s", e)
            self.streaming = False
            if self.recording:
                self._stop_recording()
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
