#!/bin/bash
# Setup script for optimizing BlueZ BLE connection parameters on Raspberry Pi
# Run this before starting the emulator

echo "=== Bluetooth Setup for fNIRS Emulator ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo ./setup_bluetooth.sh)"
    exit 1
fi

# Stop bluetooth service temporarily
echo "Stopping bluetooth service..."
systemctl stop bluetooth

# Wait a moment
sleep 1

# Start bluetooth service with experimental features
echo "Starting bluetooth with experimental features..."
# Modify the bluetooth service to enable experimental features
if ! grep -q "ExperimentalFeatures" /etc/bluetooth/main.conf 2>/dev/null; then
    echo "[General]" >> /etc/bluetooth/main.conf
    echo "ExperimentalFeatures = true" >> /etc/bluetooth/main.conf
    echo "Added ExperimentalFeatures to main.conf"
fi

# Restart bluetooth
systemctl start bluetooth
sleep 2

# Power on and make discoverable
echo "Configuring bluetooth adapter..."
bluetoothctl power on
bluetoothctl discoverable on
bluetoothctl pairable on

# Set advertising parameters for better connection stability
# These parameters help with connection supervision timeout issues
echo "Setting advertising parameters..."

# Get the adapter path
ADAPTER=$(bluetoothctl list | grep -oP 'Controller \K[0-9A-F:]+')
if [ -n "$ADAPTER" ]; then
    echo "Using adapter: $ADAPTER"

    # Use hcitool to set advertising parameters if available
    if command -v hcitool &> /dev/null; then
        # Set advertising interval (min 0x0020 = 20ms, max 0x0040 = 40ms)
        # Lower values = more responsive but uses more power
        hcitool -i hci0 cmd 0x08 0x0006 20 00 40 00 00 00 00 00 00 00 00 00 00 00 00 2>/dev/null || true
        echo "Set advertising interval"
    fi
fi

echo ""
echo "=== Bluetooth Status ==="
bluetoothctl show

echo ""
echo "=== Setup Complete ==="
echo "You can now start the emulator with: python dummynirs.py"
echo ""
echo "If you still experience connection drops, try:"
echo "1. Reduce distance between devices"
echo "2. Ensure no WiFi interference (BLE uses 2.4GHz)"
echo "3. Check for other Bluetooth devices that might interfere"
