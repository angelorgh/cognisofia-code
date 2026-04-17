# CogniSofIA — NIRDuino fNIRS

Este repositorio almacena el código del proyecto de investigación **CogniSofIA** para la adquisición y visualización de datos de espectroscopia funcional de infrarrojo cercano (fNIRS) con el dispositivo NIRDuino. Incluye firmware para el hardware, cliente de escritorio con interfaz gráfica, base de datos TimescaleDB, notebooks de procesamiento de señal, y emulador BLE para desarrollo y pruebas.

---

## Estructura del repositorio

```
cogni-code/
├── .github/
│   └── workflows/
│       └── release.yml          # CI/CD: build multiplataforma + publicación de release
├── firmware/                    # Firmware Arduino para el dispositivo NIRDuino
├── fnirs-client/                # Cliente de escritorio Python
│   ├── NIRDuinoClient.py        # Biblioteca principal BLE + grabación de datos
│   ├── cogni-gui.py             # Interfaz gráfica (DearPyGui)
│   ├── cogni-gui.spec           # Spec de PyInstaller para compilar ejecutable
│   ├── icon.ico / icon.icns     # Iconos de la aplicación (Windows / macOS)
│   ├── fonts/                   # JetBrains Mono NL (fuente de la UI)
│   ├── requirements.txt         # Dependencias Python
│   ├── db/
│   │   └── schema.sql           # Esquema TimescaleDB
│   └── data-processing/         # Notebooks Jupyter de análisis de señal
│       ├── processing.ipynb     # Procesamiento de un par fuente-detector
│       └── processing-all.ipynb # Procesamiento de todos los pares en grid
└── dummy-fnirs/                 # Emulador BLE del dispositivo (Raspberry Pi)
```

---

## Instalación

### Opción A — Ejecutable compilado (recomendado)

Descarga el ejecutable para tu plataforma desde la página de [Releases](../../releases):

| Plataforma | Archivo |
|---|---|
| macOS | `NIRDuino-vX.Y.Z-macOS.zip` → extraer y abrir `NIRDuino.app` |
| Windows | `NIRDuino-vX.Y.Z-Windows.zip` → extraer y ejecutar `NIRDuino.exe` |
| Linux | `NIRDuino-vX.Y.Z-Linux.tar.gz` → extraer y ejecutar `NIRDuino` |

> **macOS:** Si el sistema bloquea la app por ser de desarrollador no verificado, ir a
> *Configuración del sistema → Privacidad y seguridad → Abrir de todas formas*.

### Opción B — Desde código fuente

```bash
cd fnirs-client
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python cogni-gui.py
```

**Requisitos:** Python 3.11+, Bluetooth LE activo en el sistema.

---

## Uso de la aplicación

### 1. Primer arranque — Configuración

Al abrir la aplicación por primera vez, configura los parámetros haciendo clic en **Configuración** en la barra de menú superior.

La ventana tiene dos secciones:

#### Base de datos (opcional)
Rellena los campos de conexión a TimescaleDB si deseas guardar las sesiones en base de datos:

| Campo | Descripción |
|---|---|
| Host | Dirección del servidor PostgreSQL / TimescaleDB |
| Puerto | Por defecto `5432` |
| Base de datos | Nombre de la base de datos (ej. `fnirs_db`) |
| Usuario | Usuario de PostgreSQL |
| Contraseña | Contraseña del usuario |

Usa **Probar** para verificar la conexión antes de guardar. Al hacer clic en **Conectar**, las credenciales se persisten en `~/.cogni/config.json` y se restauran automáticamente en el siguiente arranque.

> Para crear el esquema de la base de datos ejecutar `db/schema.sql` una sola vez:
> ```bash
> psql -U postgres -d fnirs_db -f fnirs-client/db/schema.sql
> ```

#### Directorio output CSV
Haz clic en **Seleccionar directorio...** para elegir la carpeta donde se guardarán los archivos CSV de cada sesión. El directorio por defecto es `~/.cogni/data/`. La selección se persiste en `~/.cogni/config.json`.

---

### 2. Conectar al dispositivo

1. Enciende el dispositivo NIRDuino y asegúrate de que está en modo de anuncio BLE.
2. En la sección **Conexión**, haz clic en **Buscar** — la app escanea durante 10 segundos.
3. Una vez encontrado el dispositivo, el nombre y dirección MAC aparecen en la pantalla.
4. Haz clic en **Conectar**.

> Si no se encuentra el dispositivo, verificar que el Bluetooth del equipo está activo y que el NIRDuino está encendido y visible.

---

### 3. Configurar la sesión

En la sección **Sesión**:

| Campo | Descripción |
|---|---|
| Nombre del sujeto | Identificador del participante (ej. `Juan Pérez`) |
| Problema | Identificador de la tarea o condición (ej. `Tarea aritmética 1`) |
| Salida | Modo de grabación: **Solo CSV**, **Solo base de datos**, o **CSV + Base de datos** |

El nombre del archivo CSV generado sigue el formato:
```
session_{sujeto}_{problema}_{YYYYMMDD_HHMMSS}.csv
```

---

### 4. Configurar LEDs (opcional)

Despliega la sección **Configuración de LEDs** para ajustar el voltaje de cada una de las 8 fuentes físicas en las 4 combinaciones (740 nm / 850 nm × Regular Power / Low Power).

- Los valores se expresan en voltios (0.00 – 5.00 V).
- Haz clic en **Actualizar LEDs** para enviar la nueva configuración al dispositivo sin detener la transmisión.
- **Restablecer valores** vuelve a los defaults de fábrica.

---

### 5. Iniciar transmisión

1. Haz clic en **Iniciar transmisión** en la sección **Transmisión**.
2. La gráfica en tiempo real mostrará las 4 series del par fuente/detector seleccionado (740 nm RP/LP, 850 nm RP/LP).
3. Usa los selectores **Fuente** y **Detector** para cambiar el par visualizado mientras se graba.

Los contadores de la sección Transmisión muestran en tiempo real:
- **Tramas** recibidas en la sesión actual
- **CSV / BD** filas escritas
- **Batería** del dispositivo (%)

---

### 6. Marcar estímulos

Durante la transmisión, el botón **Estímulo: OFF** activa/desactiva la anotación de estímulo:

- Al activar, el gráfico muestra una **banda gris semitransparente** sobre el período de estímulo.
- El campo **Anotación** permite describir el tipo de estímulo (ej. `Reposo`, `Tarea aritmética`).
- Cada trama grabada incluye la columna `stimulus` (0/1) y `stimulus_annotation` (texto).

---

### 7. Detener y guardar

Haz clic en **Detener transmisión**. El archivo CSV queda cerrado en el directorio configurado.
La conexión BLE se mantiene activa hasta que hagas clic en **Desconectar**.

---

### 8. Procesamiento de datos

Los notebooks en `fnirs-client/data-processing/` implementan el pipeline de análisis:

| Notebook | Descripción |
|---|---|
| `processing.ipynb` | Procesamiento de un solo par fuente-detector |
| `processing-all.ipynb` | Procesamiento de todos los pares, gráficas en grid 5 columnas |

El pipeline implementa:
1. Filtro de mediana (eliminación de artefactos spike)
2. Conversión a densidad óptica (OD)
3. Ley de Beer-Lambert Modificada (MBLL) → concentraciones HbO₂ y Hb
4. Filtro paso-banda (elimina deriva lenta y ruido de alta frecuencia)

Los notebooks leen las credenciales de BD desde `~/.cogni/config.json` (misma configuración que la GUI).

---

## firmware/

Firmware en Arduino (C++) para el microcontrolador **Nano ESP32** del dispositivo NIRDuino.

| Archivo | Descripción |
|---|---|
| `NIRDuino_Firmware_Rev20241005.ino` | Revisión original (Octubre 2024) |
| `NIRDuino_Firmware_Rev20260226/` | Revisión más reciente con corrección de descriptores BLE2902 |

El firmware controla:
- Comunicación SPI con dos ADCs **ADS1256** (24 bits) para lectura de 16 detectores
- Control de 32 fuentes de luz (8 físicas × 2 longitudes de onda × 2 potencias) vía DAC **MCP4728** y multiplexor **CD74HC4067**
- Servidor BLE GATT que transmite datos en 5 paquetes de notificación por trama
- Monitoreo de batería con **MAX17043** (fuel gauge)
- Almacenamiento de intensidades LED en EEPROM

---

## dummy-fnirs/

Emulador BLE del dispositivo NIRDuino para desarrollo y pruebas sin hardware físico. Diseñado para ejecutarse en una **Raspberry Pi 4**.

| Archivo | Descripción |
|---|---|
| `dummynirs.py` | Emulador completo usando `bless`. Genera datos fisiológicos simulados (ondas cardíacas, respiratorias, Mayer) con respuesta hemodinámica (HRF) ante estímulos |
| `setup_bluetooth.sh` | Configura el adaptador Bluetooth de la Raspberry Pi |
| `bluetooth_agent_setup.sh` | Script + servicio systemd para aceptar conexiones BLE automáticamente |

```bash
# En la Raspberry Pi
sudo bash setup_bluetooth.sh
python3 dummynirs.py
```

---

## Especificaciones técnicas

| Parámetro | Valor |
|---|---|
| Longitudes de onda | 740 nm y 850 nm |
| Modos de potencia | Regular (RP) y Baja (LP) |
| Fuentes físicas | 8 (×2 longitudes de onda ×2 potencias = 32 fuentes lógicas) |
| Detectores | 16 + corriente oscura |
| Trama de datos | 33 × 17 = 561 valores int32 (2 244 bytes) |
| Paquetes BLE por trama | 5 (480, 480, 480, 480, 344 bytes) |
| Frecuencia de muestreo | ~10 Hz |
| Protocolo | Bluetooth Low Energy (BLE) GATT |
| Base de datos | TimescaleDB (PostgreSQL + extensión de series temporales) |
| Formato CSV | `session_{sujeto}_{problema}_{timestamp}.csv` |
| Configuración persistida | `~/.cogni/config.json` |

---

## Build y Release

Los ejecutables se compilan automáticamente con GitHub Actions al crear un tag `v*` en `main`.
El workflow (`release.yml`) corre en paralelo en macOS, Windows y Linux usando PyInstaller.

```bash
# Crear un nuevo release
git tag -a v1.0.0 --cleanup=verbatim -m "## Novedades
- descripción de cambios"
git push origin v1.0.0
```

Los artefactos quedan publicados automáticamente en la página de [Releases](../../releases).
