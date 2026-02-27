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
import logging
import time
from collections import deque
from typing import List, Optional

import dearpygui.dearpygui as dpg

from NIRDuinoClient import (
    FNIRSClient,
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


def _adc_to_v(raw: int) -> float:
    return raw * ADC_VREF / ADC_MAX


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
        dpg.set_value("lbl_frames",  f"Frames: {self.client.frame_count}")
        dpg.set_value("lbl_rows",    f"CSV rows: {self.client.csv_writer.rows_written}")
        bat = self.client.battery_level
        dpg.set_value("lbl_battery", f"Battery: {bat}%" if bat is not None else "Battery: --")

    def _update_plot(self):
        if not self._px:
            return
        xs = list(self._px)
        dpg.set_value("series_740_rp", [xs, list(self._p740_rp)])
        dpg.set_value("series_850_rp", [xs, list(self._p850_rp)])
        dpg.set_value("series_740_lp", [xs, list(self._p740_lp)])
        dpg.set_value("series_850_lp", [xs, list(self._p850_lp)])
        dpg.fit_axis_data("x_axis")
        dpg.fit_axis_data("y_axis")

    def _update_log(self):
        lines = _gui_log_handler.lines
        if len(lines) == self._log_last_len:
            return
        self._log_last_len = len(lines)
        dpg.set_value("log_text", "\n".join(list(lines)[-40:]))

    # ── Async actions ─────────────────────────────────────────────────────────
    async def _do_scan(self):
        self.scanning = True
        self._set_status("Scanning for device…", (255, 200, 0))
        self._refresh_buttons()

        tmp = FNIRSClient()
        device = await tmp.scan_for_device(timeout=10.0)

        if device:
            self.device = device
            dpg.set_value("device_label", f"{device.name}   ({device.address})")
            self._set_status("Device found — press Connect", (80, 220, 80))
        else:
            self._set_status(f'"{DEVICE_NAME}" not found — is it advertising?', (255, 80, 80))

        self.scanning = False
        self._refresh_buttons()

    async def _do_connect(self):
        if self.device is None:
            return
        self.connecting = True
        self._set_status("Connecting…", (255, 200, 0))
        self._refresh_buttons()

        self.client = FNIRSClient(output_dir="data")
        self.client.on_frame_received = self._on_frame

        ok = await self.client.connect(self.device)
        if ok:
            self._set_status(f"Connected  ·  {self.device.name}", (80, 220, 80))
        else:
            self._set_status("Connection failed", (255, 80, 80))
            self.client = None

        self.connecting = False
        self._refresh_buttons()

    async def _do_disconnect(self):
        if self.client:
            await self.client.disconnect()
            self.client = None
        self._set_status("Disconnected", (180, 180, 180))
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
        self._last_frame_no = 0
        self._stall_tick    = 0
        self._keepalive_tick = 0

        ok = await self.client.start_streaming(record=True)
        if ok:
            self._set_status("Streaming…", (0, 210, 255))
        else:
            self._set_status("Failed to start streaming", (255, 80, 80))
        self._refresh_buttons()

    async def _do_stop_streaming(self):
        if self.client:
            await self.client.stop_streaming()
        self._set_status("Connected  ·  streaming stopped", (80, 220, 80))
        self._refresh_buttons()

    def _toggle_stimulus(self):
        if self.client:
            new_state = not self.client.stimulus_active
            self.client.set_stimulus(new_state)
            dpg.configure_item(
                "btn_stimulus",
                label="Stimulus: ON " if new_state else "Stimulus: OFF",
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
            label=f"Live Data — S{self._sel_source + 1} "
                  f"D{self._sel_detector + 1} (voltage)",
        )

    def _cb_source_changed(self, sender, value, *_):
        self._sel_source = int(value[1:]) - 1   # "S1" → 0 … "S8" → 7
        self._clear_plot_buffers()

    def _cb_detector_changed(self, sender, value, *_):
        self._sel_detector = int(value[1:]) - 1  # "D1" → 0 … "D16" → 15
        self._clear_plot_buffers()

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
        dpg.create_viewport(
            title="NIRDuino fNIRS Client",
            width=960,
            height=740,
            min_width=640,
            min_height=480,
        )
        dpg.setup_dearpygui()

        with dpg.window(tag="main_window", no_close=True,
                        no_move=True, no_resize=True, no_title_bar=True):

            dpg.add_text("NIRDuino fNIRS Client", color=(0, 210, 255))
            dpg.add_separator()
            dpg.add_spacer(height=4)

            # ── Connection ───────────────────────────────────────────────────
            with dpg.collapsing_header(label="Connection", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Status: ")
                    dpg.add_text("Disconnected", tag="status_text",
                                 color=(180, 180, 180))
                dpg.add_text("No device found yet", tag="device_label",
                             color=(160, 160, 160))
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_button(label=" Scan ",       tag="btn_scan",
                                   callback=self._cb_scan)
                    dpg.add_button(label=" Connect ",    tag="btn_connect",
                                   callback=self._cb_connect)
                    dpg.add_button(label=" Disconnect ", tag="btn_disconnect",
                                   callback=self._cb_disconnect)

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # ── Streaming ────────────────────────────────────────────────────
            with dpg.collapsing_header(label="Streaming", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_button(label=" Start Streaming ", tag="btn_start",
                                   callback=self._cb_start)
                    dpg.add_button(label=" Stop Streaming ",  tag="btn_stop",
                                   callback=self._cb_stop)
                    dpg.add_button(label="Stimulus: OFF",     tag="btn_stimulus",
                                   callback=self._cb_stimulus)
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_text("Frames: 0",   tag="lbl_frames")
                    dpg.add_spacer(width=24)
                    dpg.add_text("CSV rows: 0", tag="lbl_rows")
                    dpg.add_spacer(width=24)
                    dpg.add_text("Battery: --", tag="lbl_battery")

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # ── Live plot ────────────────────────────────────────────────────
            with dpg.collapsing_header(label="Live Data — S1 D1 (voltage)",
                                       default_open=True,
                                       tag="plot_header"):
                with dpg.group(horizontal=True):
                    dpg.add_text("Source:")
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
                with dpg.plot(label="", height=230, width=-1,
                              anti_aliased=True):
                    dpg.add_plot_legend()
                    dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)",
                                      tag="x_axis")
                    with dpg.plot_axis(dpg.mvYAxis, label="V",
                                       tag="y_axis"):
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

        dpg.set_primary_window("main_window", True)
        dpg.show_viewport()
        self._refresh_buttons()

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
                        logging.warning("Stream stalled for 30 s — disconnecting")
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
        logging.info("Window closed — cleaning up")
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
