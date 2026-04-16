#!/usr/bin/env python3
"""
NIRDuino fNIRS Desktop Client — GUI version
DearPyGui + bleak/asyncio using Option A:
  asyncio drives the main loop; DearPyGui renders frames manually
  via dpg.render_dearpygui_frame() so bleak callbacks fire naturally.

Requires:
    pip install dearpygui bleak
"""
import asyncio
import json
import logging
import sys
import time
from collections import deque
from pathlib import Path
from typing import List, Optional, Tuple

import dearpygui.dearpygui as dpg


def _asset_path(relative: str) -> str:
    """Resolve an asset path for both normal execution and PyInstaller bundle."""
    if getattr(sys, '_MEIPASS', None):
        return str(Path(sys._MEIPASS) / relative)
    return str(Path(__file__).parent / relative)

from NIRDuinoClient import (
    FNIRSClient,
    DBWriter,
    DEVICE_NAME,
    LED_CHAR_UUID,
    NUM_PHYSICAL_SOURCES,
    NUM_PHYSICAL_DETECTORS,
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging — dual output: stdout + rolling GUI buffer
# ─────────────────────────────────────────────────────────────────────────────
LOG_BUFFER_SIZE = 300


class _GUILogHandler(logging.Handler):
    """Stores formatted log lines for display in the GUI log panel."""

    def __init__(self):
        super().__init__()
        self.lines: deque[str] = deque(maxlen=LOG_BUFFER_SIZE)

    def emit(self, record: logging.LogRecord):
        self.lines.append(self.format(record))


_gui_log_handler = _GUILogHandler()
_gui_log_handler.setFormatter(
    logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                      datefmt="%H:%M:%S")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logging.getLogger().addHandler(_gui_log_handler)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
PLOT_WINDOW   = 300     # frames kept in the rolling live plot
TARGET_FPS    = 60
FRAME_PERIOD  = 1.0 / TARGET_FPS

# Keepalive every ~5 s (frames × period)
KEEPALIVE_FRAMES = int(5.0 / FRAME_PERIOD)

# Stall detection: no new data for ~30 s
STALL_FRAMES = int(30.0 / FRAME_PERIOD)

ADC_MAX  = 8_388_608.0
ADC_VREF = 5.0

# LED DAC reference voltage (MCP4728 with VDD = 5 V)
LED_VMAX = 5.0

# LED voltage defaults — firmware maps 0-255 → 0-4095 DAC → 0-5 V
LED_DEFAULT_RP_740 = 5.00   # 255/255 * 5V
LED_DEFAULT_RP_850 = 5.00
LED_DEFAULT_LP_740 = 1.47   # 75/255 * 5V ≈ 1.47V
LED_DEFAULT_LP_850 = 1.25   # 64/255 * 5V ≈ 1.25V


def _voltage_to_intensity(v: float) -> int:
    """Convert a voltage (0‥5 V) to a firmware intensity byte (0‥255)."""
    return max(0, min(255, round(v * 255.0 / LED_VMAX)))


def _intensity_to_voltage(i: int) -> float:
    """Convert a firmware intensity byte (0‥255) to voltage (0‥5 V)."""
    return i * LED_VMAX / 255.0


def _adc_to_v(raw: int) -> float:
    return raw * ADC_VREF / ADC_MAX


# ─────────────────────────────────────────────────────────────────────────────
# Persistent config  (~/.cogni/config.json)
# ─────────────────────────────────────────────────────────────────────────────
_CONFIG_PATH = Path.home() / ".cogni" / "config.json"
DEFAULT_CSV_OUTPUT_DIR = str(Path.home() / ".cogni" / "data")


def _load_config() -> dict:
    try:
        if _CONFIG_PATH.exists():
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("Could not read config: %s", exc)
    return {}


def _save_config(cfg: dict) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as exc:
        logging.warning("Could not save config: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# GUIApp
# ─────────────────────────────────────────────────────────────────────────────
class GUIApp:
    """
    Owns the FNIRSClient and all DearPyGui state.

    Button callbacks are synchronous (DearPyGui requirement) but schedule
    async coroutines via asyncio.create_task(), which run between frames.
    """

    def __init__(self):
        self.client: Optional[FNIRSClient] = None
        self.device = None          # BLEDevice returned by scan
        self.scanning   = False
        self.connecting = False

        # Selected source / detector pair (0-indexed)
        self._sel_source: int   = 0   # 0‥7  → S1‥S8
        self._sel_detector: int = 0   # 0‥15 → D1‥D16

        # Rolling live-plot buffers — 4 series per S/D pair
        self._px: deque[float]       = deque(maxlen=PLOT_WINDOW)
        self._p740_rp: deque[float]  = deque(maxlen=PLOT_WINDOW)
        self._p850_rp: deque[float]  = deque(maxlen=PLOT_WINDOW)
        self._p740_lp: deque[float]  = deque(maxlen=PLOT_WINDOW)
        self._p850_lp: deque[float]  = deque(maxlen=PLOT_WINDOW)
        self._plot_t0 = 0.0

        # Log display
        self._log_last_len = 0

        # Frame counters for keepalive / stall detection
        self._keepalive_tick = 0
        self._stall_tick     = 0
        self._last_frame_no  = 0

        # Pending async actions queued by button callbacks.
        # Callbacks are sync and cannot call create_task() directly on macOS
        # (DearPyGui fires them outside the asyncio context).  We push
        # coroutine-factory callables here and drain them inside run().
        self._pending: deque = deque()

        # Stimulus shading: list of [t_start, t_end_or_None] in plot-time (s)
        self._stimulus_intervals: list = []

        # TimescaleDB writer — shared across streaming sessions
        self.db_writer = DBWriter()

        # CSV output directory (persisted in ~/.cogni/config.json)
        self.csv_output_dir: str = DEFAULT_CSV_OUTPUT_DIR

        # Map radio-button label → FNIRSClient output_mode string
        self._output_mode_map = {
            "Solo CSV":            "csv",
            "Solo base de datos":  "db",
            "CSV + Base de datos": "both",
        }

    # ── Frame callback (called from asyncio, safe to update deques) ───────────
    def _on_frame(self, frame_no: int, data: List[List[int]]):
        s = self._sel_source    # 0‥7
        d = self._sel_detector  # 0‥15
        t = time.time() - self._plot_t0
        self._px.append(t)
        self._p740_rp.append(_adc_to_v(data[s][d]))        # 740 nm RP
        self._p850_rp.append(_adc_to_v(data[s + 8][d]))    # 850 nm RP
        self._p740_lp.append(_adc_to_v(data[s + 16][d]))   # 740 nm LP
        self._p850_lp.append(_adc_to_v(data[s + 24][d]))   # 850 nm LP

    # ── GUI helpers ───────────────────────────────────────────────────────────
    def _set_status(self, text: str, color=(220, 220, 220)):
        dpg.set_value("status_text", text)
        dpg.configure_item("status_text", color=color)

    def _refresh_buttons(self):
        connected  = self.client is not None and self.client.connected
        streaming  = self.client is not None and self.client.streaming
        has_device = self.device is not None

        dpg.configure_item("btn_scan",        enabled=not connected and not self.scanning)
        dpg.configure_item("btn_connect",     enabled=has_device and not connected and not self.connecting)
        dpg.configure_item("btn_disconnect",  enabled=connected)
        dpg.configure_item("btn_start",       enabled=connected and not streaming)
        dpg.configure_item("btn_stop",        enabled=connected and streaming)
        dpg.configure_item("btn_stimulus",    enabled=connected and streaming)

    def _update_stats(self):
        if self.client is None:
            return
        dpg.set_value("lbl_frames", f"Tramas: {self.client.frame_count}")

        mode = self.client.output_mode
        csv_n = self.client.csv_writer.rows_written if mode in ("csv", "both") else None
        db_n  = self.db_writer.rows_written         if mode in ("db",  "both") else None
        dpg.set_value(
            "lbl_rows",
            f"CSV: {csv_n if csv_n is not None else '—'}  "
            f"BD: {db_n  if db_n  is not None else '—'}",
        )

        bat = self.client.battery_level
        dpg.set_value("lbl_battery", f"Batería: {bat}%" if bat is not None else "Batería: --")

    def _update_plot(self):
        # ── Stimulus shading (rebuilt every frame, even before data arrives) ──
        _nan = float("nan")
        _BIG = 1e6
        sx: list = []
        sy1: list = []
        sy2: list = []
        t_now = self._px[-1] if self._px else 0.0
        for i, (t_start, t_end) in enumerate(self._stimulus_intervals):
            # If stimulus is still active, extend band to the current plot time
            t_e = t_end if t_end is not None else t_now
            if i > 0:
                sx.append(_nan);  sy1.append(_nan);  sy2.append(_nan)
            sx  += [t_start, t_e]
            sy1 += [_BIG,    _BIG]
            sy2 += [-_BIG,  -_BIG]
        dpg.set_value("series_stim", [sx, sy1, sy2])

        if not self._px:
            return
        xs = list(self._px)
        dpg.set_value("series_740_rp", [xs, list(self._p740_rp)])
        dpg.set_value("series_850_rp", [xs, list(self._p850_rp)])
        dpg.set_value("series_740_lp", [xs, list(self._p740_lp)])
        dpg.set_value("series_850_lp", [xs, list(self._p850_lp)])
        dpg.fit_axis_data("x_axis")

        # Manual Y-axis limits with padding so LP readings near 5 V
        # are not clipped at the top of the chart.
        all_vals = (list(self._p740_rp) + list(self._p850_rp)
                    + list(self._p740_lp) + list(self._p850_lp))
        if all_vals:
            ymin = min(all_vals)
            ymax = max(all_vals)
            margin = (ymax - ymin) * 0.10 if ymax > ymin else 0.05
            dpg.set_axis_limits("y_axis", ymin - margin, ymax + margin)

    def _update_log(self):
        lines = _gui_log_handler.lines
        if len(lines) == self._log_last_len:
            return
        self._log_last_len = len(lines)
        dpg.set_value("log_text", "\n".join(list(lines)[-40:]))

    # ── Async actions ─────────────────────────────────────────────────────────
    async def _do_scan(self):
        self.scanning = True
        self._set_status("Buscando dispositivo...", (255, 200, 0))
        self._refresh_buttons()

        tmp = FNIRSClient()
        device = await tmp.scan_for_device(timeout=10.0)

        if device:
            self.device = device
            dpg.set_value("device_label", f"{device.name}   ({device.address})")
            self._set_status("Dispositivo encontrado — presiona Conectar", (80, 220, 80))
        else:
            self._set_status(f'"{DEVICE_NAME}" no encontrado — ¿está en modo anuncio BLE?', (255, 80, 80))

        self.scanning = False
        self._refresh_buttons()

    async def _do_connect(self):
        if self.device is None:
            return
        self.connecting = True
        self._set_status("Conectando...", (255, 200, 0))
        self._refresh_buttons()

        self.client = FNIRSClient(output_dir=self.csv_output_dir)
        self.client.on_frame_received = self._on_frame

        ok = await self.client.connect(self.device)
        if ok:
            self._set_status(f"Conectado  ·  {self.device.name}", (80, 220, 80))
        else:
            self._set_status("Error de conexión", (255, 80, 80))
            self.client = None

        self.connecting = False
        self._refresh_buttons()

    async def _do_disconnect(self):
        if self.client:
            await self.client.disconnect()
            self.client = None
        self._set_status("Desconectado", (180, 180, 180))
        self._refresh_buttons()

    async def _do_start_streaming(self):
        if self.client is None:
            return
        # Reset plot buffers for new session
        self._plot_t0 = time.time()
        self._px.clear()
        self._p740_rp.clear()
        self._p850_rp.clear()
        self._p740_lp.clear()
        self._p850_lp.clear()
        self._stimulus_intervals.clear()
        self._last_frame_no = 0
        self._stall_tick    = 0
        self._keepalive_tick = 0

        # Determine output mode from the radio button
        output_mode = self._output_mode_map.get(
            dpg.get_value("output_mode"), "csv"
        )

        # Validate DB connection when it's required
        if output_mode in ("db", "both") and not self.db_writer.is_connected:
            self._set_status(
                "Base de datos no conectada — abrir Configuración",
                (255, 80, 80),
            )
            self._refresh_buttons()
            return

        # Attach output configuration to the client
        self.client.output_mode = output_mode
        self.client.db_writer   = self.db_writer if output_mode in ("db", "both") else None

        rp_740, rp_850, lp_740, lp_850 = self._read_led_config()
        subject_name = dpg.get_value("input_subject")
        problem      = dpg.get_value("input_problem")
        ok = await self.client.start_streaming(
            record=True,
            rp_740=rp_740, rp_850=rp_850,
            lp_740=lp_740, lp_850=lp_850,
            subject_name=subject_name,
            problem=problem,
        )
        if ok:
            self._set_status("Transmitiendo...", (0, 210, 255))
        else:
            self._set_status("Error al iniciar la transmisión", (255, 80, 80))
        self._refresh_buttons()

    async def _do_stop_streaming(self):
        if self.client:
            await self.client.stop_streaming()
        self._set_status("Conectado  ·  transmisión detenida", (80, 220, 80))
        self._refresh_buttons()

    def _toggle_stimulus(self):
        if self.client:
            new_state = not self.client.stimulus_active
            self.client.set_stimulus(new_state)
            # Sync annotation text to client
            self.client.stimulus_annotation = (
                dpg.get_value("input_stimulus_annotation").strip()
                if new_state else ""
            )
            t_now = time.time() - self._plot_t0
            if new_state:
                # Open a new interval
                self._stimulus_intervals.append([t_now, None])
            else:
                # Close the current interval
                if self._stimulus_intervals and self._stimulus_intervals[-1][1] is None:
                    self._stimulus_intervals[-1][1] = t_now
            dpg.configure_item(
                "btn_stimulus",
                label="Estímulo: ON " if new_state else "Estímulo: OFF",
            )

    # ── Plot selection helpers ────────────────────────────────────────────────
    def _clear_plot_buffers(self):
        """Reset all rolling plot deques and update the header label."""
        self._px.clear()
        self._p740_rp.clear()
        self._p850_rp.clear()
        self._p740_lp.clear()
        self._p850_lp.clear()
        dpg.configure_item(
            "plot_header",
            label=f"Datos en vivo — S{self._sel_source + 1} "
                  f"D{self._sel_detector + 1} (voltaje)",
        )

    def _cb_source_changed(self, sender, value, *_):
        self._sel_source = int(value[1:]) - 1   # "S1" → 0 … "S8" → 7
        self._clear_plot_buffers()

    def _cb_detector_changed(self, sender, value, *_):
        self._sel_detector = int(value[1:]) - 1  # "D1" → 0 … "D16" → 15
        self._clear_plot_buffers()

    # ── LED configuration helpers ──────────────────────────────────────────────
    def _read_led_config(self) -> Tuple[List[int], List[int], List[int], List[int]]:
        """Read the 8×4 LED voltage fields and convert to intensity bytes (0-255)."""
        rp_740, rp_850, lp_740, lp_850 = [], [], [], []
        for s in range(1, NUM_PHYSICAL_SOURCES + 1):
            rp_740.append(_voltage_to_intensity(dpg.get_value(f"led_s{s}_740rp")))
            rp_850.append(_voltage_to_intensity(dpg.get_value(f"led_s{s}_850rp")))
            lp_740.append(_voltage_to_intensity(dpg.get_value(f"led_s{s}_740lp")))
            lp_850.append(_voltage_to_intensity(dpg.get_value(f"led_s{s}_850lp")))
        return rp_740, rp_850, lp_740, lp_850

    def _cb_reset_defaults(self, *_):
        """Reset all LED input fields to default voltage values."""
        for s in range(1, NUM_PHYSICAL_SOURCES + 1):
            dpg.set_value(f"led_s{s}_740rp", LED_DEFAULT_RP_740)
            dpg.set_value(f"led_s{s}_850rp", LED_DEFAULT_RP_850)
            dpg.set_value(f"led_s{s}_740lp", LED_DEFAULT_LP_740)
            dpg.set_value(f"led_s{s}_850lp", LED_DEFAULT_LP_850)

    def _cb_update_leds(self, *_):
        self._pending.append(self._do_update_leds)

    async def _do_update_leds(self):
        """Send updated LED intensities to the device without stopping the stream."""
        if not self.client or not self.client.client or not self.client.client.is_connected:
            logging.warning("No se pueden actualizar los LEDs — sin conexión")
            return
        rp_740, rp_850, lp_740, lp_850 = self._read_led_config()

        # Update stored config so subsequent CSV rows reflect the change
        self.client.led_config = {
            "rp_740": rp_740,
            "rp_850": rp_850,
            "lp_740": lp_740,
            "lp_850": lp_850,
        }

        packet = self.client._build_config_packet(rp_740, rp_850, lp_740, lp_850)
        await self.client.client.write_gatt_char(LED_CHAR_UUID, packet)
        logging.info("Intensidades de LED actualizadas")

    # ── Configuration window callbacks ────────────────────────────────────────
    def _cb_open_config_window(self, *_):
        dpg.configure_item("win_config", show=True)

    def _cb_pick_csv_dir(self, *_):
        # Point the dialog at the currently configured directory so the user
        # starts browsing from a sensible location.
        try:
            dpg.configure_item(
                "file_dialog_csv_dir", default_path=self.csv_output_dir
            )
        except Exception:
            pass
        dpg.show_item("file_dialog_csv_dir")

    def _cb_csv_dir_selected(self, sender, app_data):
        path = app_data.get("file_path_name") or app_data.get("current_path")
        if not path:
            return
        self.csv_output_dir = path
        dpg.set_value("csv_dir_display", path)
        self._save_csv_output_dir()
        # If the client already exists, retarget its CSV writer so the next
        # session writes to the new directory without requiring a reconnect.
        if self.client and self.client.csv_writer:
            self.client.csv_writer.output_dir = Path(path)
            self.client.csv_writer.output_dir.mkdir(parents=True, exist_ok=True)
        logging.info("Directorio de salida CSV: %s", path)

    def _load_csv_output_dir(self):
        """Load the CSV output directory from config (or use the default)."""
        cfg = _load_config()
        saved = cfg.get("csv_output_dir")
        if saved:
            self.csv_output_dir = saved
        dpg.set_value("csv_dir_display", self.csv_output_dir)

    def _save_csv_output_dir(self):
        """Persist the current CSV output directory to ~/.cogni/config.json."""
        cfg = _load_config()
        cfg["csv_output_dir"] = self.csv_output_dir
        _save_config(cfg)

    def _cb_test_db(self, *_):
        self._pending.append(self._do_test_db)

    def _cb_connect_db(self, *_):
        self._pending.append(self._do_connect_db)

    def _cb_disconnect_db(self, *_):
        self._pending.append(self._do_disconnect_db)

    async def _do_test_db(self):
        host     = dpg.get_value("db_host")
        port     = dpg.get_value("db_port")
        dbname   = dpg.get_value("db_name")
        user     = dpg.get_value("db_user")
        password = dpg.get_value("db_pass")
        self._set_db_status("Probando...", (255, 200, 0))
        loop = asyncio.get_running_loop()
        ok, msg = await loop.run_in_executor(
            None, self.db_writer.test_connection, host, port, dbname, user, password
        )
        if ok:
            self._set_db_status(f"✓ {msg}", (80, 220, 80))
        else:
            self._set_db_status(f"✗ {msg}", (255, 80, 80))

    async def _do_connect_db(self):
        host     = dpg.get_value("db_host")
        port     = dpg.get_value("db_port")
        dbname   = dpg.get_value("db_name")
        user     = dpg.get_value("db_user")
        password = dpg.get_value("db_pass")
        self._set_db_status("Conectando...", (255, 200, 0))
        loop = asyncio.get_running_loop()
        ok, msg = await loop.run_in_executor(
            None, self.db_writer.connect, host, port, dbname, user, password
        )
        if ok:
            self._set_db_status(
                f"● Conectado  ·  {dbname}@{host}:{port}", (80, 220, 80)
            )
            dpg.configure_item("btn_db_connect",    enabled=False)
            dpg.configure_item("btn_db_disconnect", enabled=True)
            self._save_db_config()   # persist for next restart
        else:
            self._set_db_status(f"✗ {msg}", (255, 80, 80))

    async def _do_disconnect_db(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.db_writer.disconnect)
        self._set_db_status("● Sin conexión", (180, 180, 180))
        dpg.configure_item("btn_db_connect",    enabled=True)
        dpg.configure_item("btn_db_disconnect", enabled=False)

    def _set_db_status(self, text: str, color=(180, 180, 180)):
        dpg.set_value("db_conn_status", text)
        dpg.configure_item("db_conn_status", color=color)

    def _load_db_config(self) -> bool:
        """Pre-fill the connections window from ~/.cogni/config.json.

        Returns True if enough credentials exist to attempt auto-connect.
        """
        db = _load_config().get("db", {})
        if db.get("host"):     dpg.set_value("db_host", db["host"])
        if db.get("port"):     dpg.set_value("db_port", db["port"])
        if db.get("dbname"):   dpg.set_value("db_name", db["dbname"])
        if db.get("user"):     dpg.set_value("db_user", db["user"])
        if db.get("password"): dpg.set_value("db_pass", db["password"])
        return bool(db.get("host") and db.get("user"))

    def _save_db_config(self):
        """Persist the current connections-window values to ~/.cogni/config.json."""
        cfg = _load_config()
        cfg["db"] = {
            "host":     dpg.get_value("db_host"),
            "port":     dpg.get_value("db_port"),
            "dbname":   dpg.get_value("db_name"),
            "user":     dpg.get_value("db_user"),
            "password": dpg.get_value("db_pass"),
        }
        _save_config(cfg)

    # ── Button callbacks (sync — push to pending queue, drained by run()) ───────
    def _cb_scan(self, *_):        self._pending.append(self._do_scan)
    def _cb_connect(self, *_):     self._pending.append(self._do_connect)
    def _cb_disconnect(self, *_):  self._pending.append(self._do_disconnect)
    def _cb_start(self, *_):       self._pending.append(self._do_start_streaming)
    def _cb_stop(self, *_):        self._pending.append(self._do_stop_streaming)
    def _cb_stimulus(self, *_):    self._toggle_stimulus()

    # ── GUI layout ────────────────────────────────────────────────────────────
    def _build_gui(self):
        dpg.create_context()

        with dpg.font_registry():
            default_font = dpg.add_font(_asset_path("fonts/JetBrainsMonoNL-Regular.ttf"), 16)
            
        dpg.create_viewport(
            title="NIRDuino fNIRS Cliente",
            width=960,
            height=740,
            min_width=640,
            min_height=480,
        )
        dpg.setup_dearpygui()

        # ── Stimulus band theme — semi-transparent grey fill, no border line ─
        with dpg.theme(tag="theme_stim_band"):
            with dpg.theme_component(dpg.mvShadeSeries):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Fill, (180, 180, 180, 60),
                    category=dpg.mvThemeCat_Plots,
                )
                dpg.add_theme_color(
                    dpg.mvPlotCol_Line, (0, 0, 0, 0),
                    category=dpg.mvThemeCat_Plots,
                )

        with dpg.window(tag="main_window", no_close=True,
                        no_move=True, no_resize=True, no_title_bar=True,
                        menubar=True):

            # ── Menu bar ─────────────────────────────────────────────────────
            with dpg.menu_bar():
                dpg.add_menu_item(
                    label="Configuración",
                    callback=self._cb_open_config_window,
                )

            dpg.add_text("NIRDuino fNIRS Cliente", color=(0, 210, 255))
            dpg.add_separator()
            dpg.add_spacer(height=4)

            # ── Conexión ─────────────────────────────────────────────────────
            with dpg.collapsing_header(label="Conexión", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Estado: ")
                    dpg.add_text("Desconectado", tag="status_text",
                                 color=(180, 180, 180))
                dpg.add_text("Ningún dispositivo encontrado", tag="device_label",
                             color=(160, 160, 160))
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_button(label=" Buscar ",       tag="btn_scan",
                                   callback=self._cb_scan)
                    dpg.add_button(label=" Conectar ",     tag="btn_connect",
                                   callback=self._cb_connect)
                    dpg.add_button(label=" Desconectar ",  tag="btn_disconnect",
                                   callback=self._cb_disconnect)

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # ── Información de sesión ─────────────────────────────────────────
            with dpg.collapsing_header(label="Sesión", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Nombre del sujeto:")
                    dpg.add_input_text(tag="input_subject", width=200,
                                       hint="ej. Juan Pérez")
                with dpg.group(horizontal=True):
                    dpg.add_text("Problema:")
                    dpg.add_spacer(width=32)
                    dpg.add_input_text(tag="input_problem", width=200,
                                       hint="ej. Problema 1")
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_text("Salida:")
                    dpg.add_spacer(width=10)
                    dpg.add_radio_button(
                        tag="output_mode",
                        items=["Solo CSV", "Solo base de datos", "CSV + Base de datos"],
                        default_value="Solo CSV",
                        horizontal=True,
                    )

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # ── Transmisión ──────────────────────────────────────────────────
            with dpg.collapsing_header(label="Transmisión", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_button(label=" Iniciar transmisión ", tag="btn_start",
                                   callback=self._cb_start)
                    dpg.add_button(label=" Detener transmisión ", tag="btn_stop",
                                   callback=self._cb_stop)
                    dpg.add_button(label="Estímulo: OFF",         tag="btn_stimulus",
                                   callback=self._cb_stimulus)
                    dpg.add_spacer(width=16)
                    dpg.add_text("Anotación:")
                    dpg.add_input_text(tag="input_stimulus_annotation", width=200,
                                       hint="ej. Reposo, Tarea aritmética")
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_text("Tramas: 0",    tag="lbl_frames")
                    dpg.add_spacer(width=24)
                    dpg.add_text("CSV: --  BD: --", tag="lbl_rows")
                    dpg.add_spacer(width=24)
                    dpg.add_text("Batería: --",   tag="lbl_battery")

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # ── Configuración de LEDs (voltajes) ─────────────────────────────
            with dpg.collapsing_header(label="Configuración de LEDs",
                                       default_open=False):
                # Encabezados de columnas
                with dpg.group(horizontal=True):
                    dpg.add_text("Fuente", color=(160, 160, 160))
                    dpg.add_spacer(width=6)
                    dpg.add_text("740 RP (V)", color=(160, 160, 160))
                    dpg.add_spacer(width=24)
                    dpg.add_text("850 RP (V)", color=(160, 160, 160))
                    dpg.add_spacer(width=24)
                    dpg.add_text("740 LP (V)", color=(160, 160, 160))
                    dpg.add_spacer(width=24)
                    dpg.add_text("850 LP (V)", color=(160, 160, 160))

                # 8 source rows — voltage inputs (0.00 – 5.00 V)
                for s in range(1, NUM_PHYSICAL_SOURCES + 1):
                    with dpg.group(horizontal=True):
                        dpg.add_text(f"  S{s}")
                        dpg.add_spacer(width=12)
                        dpg.add_input_float(
                            tag=f"led_s{s}_740rp", width=90,
                            default_value=LED_DEFAULT_RP_740, step=0.1,
                            min_value=0.0, max_value=LED_VMAX,
                            min_clamped=True, max_clamped=True,
                            format="%.2f",
                        )
                        dpg.add_input_float(
                            tag=f"led_s{s}_850rp", width=90,
                            default_value=LED_DEFAULT_RP_850, step=0.1,
                            min_value=0.0, max_value=LED_VMAX,
                            min_clamped=True, max_clamped=True,
                            format="%.2f",
                        )
                        dpg.add_input_float(
                            tag=f"led_s{s}_740lp", width=90,
                            default_value=LED_DEFAULT_LP_740, step=0.1,
                            min_value=0.0, max_value=LED_VMAX,
                            min_clamped=True, max_clamped=True,
                            format="%.2f",
                        )
                        dpg.add_input_float(
                            tag=f"led_s{s}_850lp", width=90,
                            default_value=LED_DEFAULT_LP_850, step=0.1,
                            min_value=0.0, max_value=LED_VMAX,
                            min_clamped=True, max_clamped=True,
                            format="%.2f",
                        )

                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_button(label=" Restablecer valores ",
                                   callback=self._cb_reset_defaults)
                    dpg.add_button(label=" Actualizar LEDs ",
                                   tag="btn_update_leds",
                                   callback=self._cb_update_leds)

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # ── Gráfico en vivo ───────────────────────────────────────────────
            with dpg.collapsing_header(label="Datos en vivo - S1 D1 (voltaje)",
                                       default_open=True,
                                       tag="plot_header"):
                with dpg.group(horizontal=True):
                    dpg.add_text("Fuente:")
                    dpg.add_combo(
                        items=[f"S{i}" for i in range(1, NUM_PHYSICAL_SOURCES + 1)],
                        default_value="S1",
                        width=80,
                        tag="combo_source",
                        callback=self._cb_source_changed,
                    )
                    dpg.add_spacer(width=16)
                    dpg.add_text("Detector:")
                    dpg.add_combo(
                        items=[f"D{i}" for i in range(1, NUM_PHYSICAL_DETECTORS + 1)],
                        default_value="D1",
                        width=80,
                        tag="combo_detector",
                        callback=self._cb_detector_changed,
                    )
                dpg.add_spacer(height=4)
                with dpg.plot(label="", height=400, width=-1,
                              anti_aliased=True):
                    dpg.add_plot_legend()
                    dpg.add_plot_axis(dpg.mvXAxis, label="Tiempo (s)",
                                      tag="x_axis")
                    with dpg.plot_axis(dpg.mvYAxis, label="V",
                                       tag="y_axis"):
                        # Sombreado de estímulo — primero para que quede detrás de las líneas
                        dpg.add_shade_series([], [], y2=[], label="Estímulo",
                                             tag="series_stim")
                        dpg.bind_item_theme("series_stim", "theme_stim_band")
                        dpg.add_line_series([], [], label="740 nm RP",
                                            tag="series_740_rp")
                        dpg.add_line_series([], [], label="850 nm RP",
                                            tag="series_850_rp")
                        dpg.add_line_series([], [], label="740 nm LP",
                                            tag="series_740_lp")
                        dpg.add_line_series([], [], label="850 nm LP",
                                            tag="series_850_lp")

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # ── Log ──────────────────────────────────────────────────────────
            with dpg.collapsing_header(label="Log", default_open=True):
                dpg.add_input_text(
                    tag="log_text",
                    multiline=True,
                    readonly=True,
                    width=-1,
                    height=150,
                    default_value="",
                )
            dpg.bind_font(default_font)
        #dpg.show_font_manager()
        dpg.maximize_viewport()

        # ── File dialog for CSV output directory ──────────────────────────────
        with dpg.file_dialog(
            directory_selector=True,
            show=False,
            modal=True,
            tag="file_dialog_csv_dir",
            callback=self._cb_csv_dir_selected,
            width=700,
            height=400,
            default_path=self.csv_output_dir,
        ):
            pass

        # ── Unified configuration window (top-level, toggled by menu) ─────────
        with dpg.window(
            label="Configuración",
            tag="win_config",
            show=False,
            width=560,
            height=480,
            no_collapse=True,
            pos=[120, 80],
        ):
            # ── Section: Base de datos ────────────────────────────────────────
            dpg.add_text("Base de datos", color=(0, 210, 255))
            dpg.add_separator()
            dpg.add_spacer(height=6)

            with dpg.group(horizontal=True):
                dpg.add_text("Host:      ")
                dpg.add_input_text(tag="db_host", default_value="localhost", width=230)
                dpg.add_spacer(width=12)
                dpg.add_text("Puerto:")
                dpg.add_input_text(tag="db_port", default_value="5432", width=60)

            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text("Base de datos:")
                dpg.add_input_text(tag="db_name", default_value="fnirs_db", width=200)

            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text("Usuario:   ")
                dpg.add_input_text(tag="db_user", default_value="postgres", width=200)

            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text("Contraseña:")
                dpg.add_input_text(tag="db_pass", password=True, width=200)

            dpg.add_spacer(height=8)
            dpg.add_text("● Sin conexión", tag="db_conn_status",
                         color=(180, 180, 180))
            dpg.add_spacer(height=6)

            with dpg.group(horizontal=True):
                dpg.add_button(label=" Probar ",
                               callback=self._cb_test_db)
                dpg.add_button(label=" Conectar ",    tag="btn_db_connect",
                               callback=self._cb_connect_db)
                dpg.add_button(label=" Desconectar ", tag="btn_db_disconnect",
                               callback=self._cb_disconnect_db, enabled=False)

            dpg.add_spacer(height=18)

            # ── Section: Directorio output CSV ────────────────────────────────
            dpg.add_text("Directorio output CSV", color=(0, 210, 255))
            dpg.add_separator()
            dpg.add_spacer(height=6)

            dpg.add_text("Ruta actual:", color=(160, 160, 160))
            dpg.add_text(self.csv_output_dir, tag="csv_dir_display",
                         color=(220, 220, 220), wrap=520)
            dpg.add_spacer(height=6)
            dpg.add_button(label=" Seleccionar directorio... ",
                           callback=self._cb_pick_csv_dir)

            dpg.add_spacer(height=18)
            dpg.add_separator()
            dpg.add_spacer(height=6)

            # ── Close button ──────────────────────────────────────────────────
            dpg.add_button(
                label=" Cerrar ",
                callback=lambda: dpg.configure_item("win_config", show=False),
            )

        dpg.set_primary_window("main_window", True)
        dpg.show_viewport()
        self._refresh_buttons()

        # Pre-fill fields from persisted config (must run after all widgets exist).
        self._load_csv_output_dir()
        # If DB credentials are present, queue an auto-connect on the first loop tick.
        if self._load_db_config():
            self._pending.append(self._do_connect_db)

    # ── Main loop ─────────────────────────────────────────────────────────────
    async def run(self):
        self._build_gui()

        while dpg.is_dearpygui_running():
            # 1. Drain pending button actions — we are inside the running
            #    event loop here, so create_task() is safe.
            while self._pending:
                coro_fn = self._pending.popleft()
                asyncio.create_task(coro_fn())

            # 2. Render one GUI frame
            dpg.render_dearpygui_frame()

            # 3. Refresh widgets
            self._update_stats()
            self._update_plot()
            self._update_log()

            # 4. Keepalive + stall detection (only while streaming)
            if self.client and self.client.streaming:
                self._keepalive_tick += 1
                if self._keepalive_tick >= KEEPALIVE_FRAMES:
                    self._keepalive_tick = 0
                    try:
                        if self.client.client and self.client.client.is_connected:
                            await self.client.client.read_gatt_char(LED_CHAR_UUID)
                            logging.debug("Keepalive OK")
                    except Exception as exc:
                        logging.warning("Keepalive failed: %s", exc)

                cur = self.client.frame_count
                if cur == self._last_frame_no:
                    self._stall_tick += 1
                    if self._stall_tick >= STALL_FRAMES:
                        logging.warning("Transmisión detenida por 30 s — desconectando")
                        asyncio.create_task(self._do_disconnect())
                        self._stall_tick = 0
                else:
                    self._stall_tick    = 0
                    self._last_frame_no = cur
            else:
                self._keepalive_tick = 0
                self._stall_tick     = 0
                self._last_frame_no  = 0

            # 5. Yield to asyncio so bleak callbacks can fire
            await asyncio.sleep(FRAME_PERIOD)

        # ── Cleanup on window close ──────────────────────────────────────────
        logging.info("Ventana cerrada — limpiando")
        if self.client:
            await self.client.disconnect()
        dpg.destroy_context()


# ─────────────────────────────────────────────────────────────────────────────
async def main():
    app = GUIApp()
    await app.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
