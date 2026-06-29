# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

CogniSofIA — NIRDuino fNIRS research system. Acquires, streams, stores, and analyses near-infrared spectroscopy (fNIRS) data from the NIRDuino BLE device. Main components: Python desktop client (`fnirs-client/`), Arduino firmware (`firmware/`), BLE emulator for development (`dummy-fnirs/`).

## Research purpose

The NIRDuino is used to measure **cognitive load in software developers** during programming tasks (algorithm design, business rule implementation). The experimental paradigm presents software problems/business rules described in **SPL (Structured Prompt Language)** to developer subjects while recording prefrontal cortex hemodynamic responses (HbO₂/Hb) via fNIRS.

### End goal

The collected and analysed fNIRS data will be used to train a **deep learning model** that, given a problem or business rule description in SPL, estimates:
1. The cognitive load a developer will experience when solving it.
2. The expected **man-hours** required to implement it.

### Experimental design

- **Subjects:** Software developers.
- **Paradigm:** Block design — alternating rest and task (stimulus ON/OFF). The "Problema" field in session files identifies the specific SPL task presented.
- **Probe placement:** Prefrontal cortex (frontal lobe), targeting working memory and executive function areas.
- **Signal pipeline:** fNIRS amplitude → optical density → Beer-Lambert (HbO₂/Hb) → short-channel regression (remove superficial noise) → bandpass 0.01–0.5 Hz → Epochs → Evoked response.

## Running the client

```bash
cd fnirs-client
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python cogni-gui.py
```

**Requirements:** Python 3.11+, Bluetooth LE active.

## Running the dummy BLE server (development, Raspberry Pi)

```bash
# On Raspberry Pi
sudo bash dummy-fnirs/setup_bluetooth.sh
python3 dummy-fnirs/dummynirs.py
```

The dummy server generates synthetic fNIRS signals (cardiac, respiratory, Mayer waves + HRF) and advertises the same BLE GATT profile as the real device.

## Building distributables

```bash
cd fnirs-client
pyinstaller cogni-gui.spec
# Output: dist/NIRDuino.app (macOS), dist/NIRDuino.exe (Windows), dist/NIRDuino (Linux)
```

## Creating a release

```bash
git tag -a v1.x.x --cleanup=verbatim -m "## Novedades
- change 1
- change 2"
git push origin v1.x.x
```

GitHub Actions (`.github/workflows/release.yml`) builds for all three platforms in parallel and publishes the release. The annotated tag message becomes the changelog body. Use `--cleanup=verbatim` to prevent git from stripping `#` lines.

## Database setup (optional)

```bash
psql -U postgres -d fnirs_db -f fnirs-client/db/schema.sql
```

Requires PostgreSQL 14+ with the TimescaleDB 2.x extension. The `frames` table is a hypertable (partitioned by `ts`, 1-hour chunks) with 564 columns. Auto-compression is configured — never alter the compression policy without also reviewing the `compress_segmentby` and `compress_orderby` settings.

## Architecture

### Data flow

```
NIRDuino device (BLE)
  └─ 5 BLE notification chunks per frame (480+480+480+480+344 bytes)
       └─ FNIRSClient._notification_handler()  — reassembles chunks
            └─ FNIRSClient._process_frame()    — unpacks 33×17 int32 array
                 ├─ CSVWriter.write_frame()    — writes voltage-converted rows
                 └─ DBWriter.write_frame()     — buffers; flushes in thread-pool
```

### Frame data layout (`frame_data[source][detector]`)

| Source rows | Content |
|---|---|
| 0–7 | 740 nm Regular Power (RP) — 8 emitters × 16 detectors |
| 8–15 | 850 nm Regular Power (RP) |
| 16–23 | 740 nm Low Power (LP) |
| 24–31 | 850 nm Low Power (LP) |
| 32 | Dark current (no illumination) — 16 detectors |

Detectors D1–D8 are physically short-separation detectors (~8–14 mm, adjacent to each emitter, used for short-channel regression). D9–D16 are long-separation detectors (~25 mm, hemodynamic signal). ADC values are 24-bit signed integers; convert with `raw * 5.0 / 8_388_608`.

### Database column naming

`s{1..8}_d{1..16}_{740nm|850nm}_{rp|lp}` — e.g. `s1_d9_740nm_rp`.
LED drive voltages: `led_s{1..8}_{740nm|850nm}_{rp|lp}`.
Stimulus: `stimulus` (0/1) + `stimulus_annotation` (text).

### Threading model

`FNIRSClient` runs inside an asyncio event loop hosted in a background thread (started by `cogni-gui.py`). DearPyGui runs its render loop on the main thread. All GUI → BLE calls go through `asyncio.run_coroutine_threadsafe()`. `DBWriter._flush_batch()` is always dispatched to a thread-pool executor — never called directly from the event loop.

### Persistent configuration

`~/.cogni/config.json` stores DB credentials (`db.host`, `db.port`, `db.dbname`, `db.user`, `db.password`) and `csv_output_dir`. The GUI reads/writes this file via `_load_config()` / `_save_config()` helpers in `cogni-gui.py`.

### Signal processing notebooks (`fnirs-client/data-processing/`)

| Notebook | Purpose |
|---|---|
| `processing.ipynb` | Single source-detector pair, manual pipeline |
| `processing-all.ipynb` | All pairs in a 5-column grid, manual pipeline |
| `processing-mne.ipynb` | Full MNE-Python pipeline with short-channel regression |

`processing-mne.ipynb` pipeline: `fnirs_cw_amplitude` → `optical_density()` → `beer_lambert_law()` → `short_channel_regression()` (using LP channels) → bandpass filter (0.01–0.5 Hz) → `Epochs` → `Evoked`.

MNE channel `loc` array: `loc[0:3]` = midpoint, `loc[3:6]` = source position, `loc[6:9]` = detector position, `loc[9]` = wavelength (nm). Must be set inside `with info._unlock():`.

#### Probe geometry (positions in mm, origin = midpoint between RP7 and E7)

Sources (emitters): E1–E8 → MNE keys `s1`–`s8`.  
Long detectors: RP1–RP8 → DB columns `d9`–`d16`; physical probe labels D3,D4,D6,D7,D8,D10,D13,D16 map in numeric order: `d9`=D3, `d10`=D4, `d11`=D6, `d12`=D7, `d13`=D8, `d14`=D10, `d15`=D13, `d16`=D16.  
Short detectors: LP1–LP8 → DB columns `d1`–`d8`; LP{n} is adjacent to E{n}, column `s{n}_d{n}_*nm_lp`; physical labels: `d1`=D9, `d2`=D12, `d3`=D15, `d4`=D11, `d5`=D14, `d6`=D1, `d7`=D5, `d8`=D2.

### BLE UUIDs (must match firmware and dummy server)

```python
FNIRS_SERVICE_UUID = "938548e6-c655-11ea-87d0-0242ac130003"
DATA_CHAR_UUID1    = "77539407-6493-4b89-985f-baaf4c0f8d86"
DATA_CHAR_UUID2    = "513b630c-e5fd-45b5-a678-bb2835d6c1d2"
LED_CHAR_UUID      = "19b10001-e8f2-537e-4f6c-d104768a1213"
DEVICE_NAME        = "BBOL NIRDuino"
```

### LED config packet (33 bytes, written to `LED_CHAR_UUID`)

```
Byte 0     : command (0x01 = START, 0x03 = STOP)
Bytes 1-16 : Regular Power — 8 pairs of (740nm, 850nm) intensity (0-255)
Bytes 17-32: Low Power     — 8 pairs of (740nm, 850nm) intensity (0-255)
```

Defaults: RP 740=255, RP 850=255, LP 740=75, LP 850=64.
