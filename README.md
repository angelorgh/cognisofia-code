# CogniSofIA — NIRDuino fNIRS

Este repositorio tiene el propósito de almacenar el código referente al proyecto de investigación CogniSofIA para la adquisicion y visualizacion de datos de espectroscopia funcional de infrarrojo cercano (fNIRS) utilizando el dispositivo NIRDuino. Incluye firmware para el hardware, cliente de escritorio con interfaz grafica, y un emulador BLE para desarrollo y pruebas.

## Estructura del repositorio

```
cogni-code/
├── firmware/          # Firmware Arduino para el dispositivo NIRDuino
├── fnirs-client/      # Cliente de escritorio (CLI + GUI)
└── dummy-fnirs/       # Emulador BLE del dispositivo (Raspberry Pi)
```

## firmware/

Firmware en Arduino (C++) para el microcontrolador **Nano ESP32** del dispositivo NIRDuino.

- `NIRDuino_Firmware_Rev20241005.ino` — Revision original (Octubre 2024)
- `NIRDuino_Firmware_Rev20260226/` — Revision mas reciente con correccion de descriptores BLE2902

El firmware controla:
- Comunicacion SPI con dos ADCs **ADS1256** (24 bits) para lectura de 16 detectores
- Control de 32 fuentes de luz (8 fisicas x 2 longitudes de onda x 2 potencias) via DAC **MCP4728** y multiplexor **CD74HC4067**
- Servidor BLE GATT que transmite datos en 5 paquetes de notificacion por trama
- Monitoreo de bateria con **MAX17043** (fuel gauge)
- Almacenamiento de intensidades LED en EEPROM

## fnirs-client/

Cliente de escritorio en Python para conectarse al NIRDuino via Bluetooth Low Energy (BLE).

| Archivo | Descripcion |
|---|---|
| `NIRDuinoClient.py` | Biblioteca principal: escaneo BLE, conexion, reensamblaje de tramas (5 chunks → 33x17 int32), grabacion CSV, reconexion automatica y keepalive |
| `cogni-gui.py` | Interfaz grafica con DearPyGui: graficas en tiempo real, seleccion de par fuente/detector, 4 series (740nm RP/LP, 850nm RP/LP), panel de log, control de estimulo |
| `requirements.txt` | Dependencias: bleak, dearpygui, pyobjc (macOS) |
| `data/` | Carpeta de salida para sesiones grabadas en CSV (ignorada por git) |

### Ejecucion

```bash
cd fnirs-client
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Interfaz grafica
python cogni-gui.py

# Solo linea de comandos
python NIRDuinoClient.py
```

## dummy-fnirs/

Emulador BLE del dispositivo NIRDuino para desarrollo y pruebas sin hardware fisico. Diseñado para ejecutarse en una **Raspberry Pi 4**.

| Archivo | Descripcion |
|---|---|
| `dummynirs.py` | Emulador completo usando la biblioteca `bless`. Genera datos fisiologicos simulados (ondas cardiacas, respiratorias, Mayer) con respuesta hemodinamica (HRF) ante estimulos |
| `setup_bluetooth.sh` | Configura el adaptador Bluetooth de la Raspberry Pi (discoverable, pairable, intervalos de advertising) |
| `bluetooth_agent_setup.sh` | Script + servicio systemd para aceptar conexiones BLE automaticamente sin confirmacion manual (agente NoInputNoOutput) |

### Ejecucion

```bash
# En la Raspberry Pi
sudo bash setup_bluetooth.sh
python3 dummynirs.py
```

## Especificaciones tecnicas

| Parametro | Valor |
|---|---|
| Longitudes de onda | 740 nm y 850 nm |
| Modos de potencia | Regular (RP) y Baja (LP) |
| Fuentes fisicas | 8 (x2 longitudes de onda x2 potencias = 32 fuentes logicas) |
| Detectores | 16 + corriente oscura |
| Trama de datos | 33 x 17 = 561 valores int32 (2244 bytes) |
| Paquetes BLE por trama | 5 (480, 480, 480, 480, 344 bytes) |
| Frecuencia de muestreo | ~10 Hz |
| Protocolo | Bluetooth Low Energy (BLE) GATT |
